//! Port of `assieme/detect.py`, owned by session A (see OWNERS.md).
//!
//! F5 context meter additions, F6 heavy-job guard, F7 retry loops and F10 paid compute: reading commands, and the
//! checks. No command, output or message text is kept: only program names, counts, times and a failure's fingerprint.
//! Every finding says what is wrong, why it matters and the one thing that fixes it.
use crate::adapters::{basename, secs};
use crate::model::{HUMAN, Job};
use crate::prelude::*;
use crate::pyrules;
use regex::Regex;
use serde_json::json;
use std::collections::HashMap;
use std::hash::Hash;

pub const GROWTH_FLAG: i64 = 150_000; // tokens per active hour, in the report
pub const GROWTH_LIVE: i64 = 100_000; // tokens per active hour, live, and only when it nears a known memory size
pub const FULL_SOON_MINUTES: f64 = 30.0;
pub const GROWTH_MIN_HOURS: f64 = 0.5;
pub const ACTIVE_GAP: f64 = 1800.0; // a longer silence is not active time
pub const COMPACTED: f64 = 0.7; // context below this share of the call before it means it was compacted
pub const IDLE_SHARE: f64 = 0.25;
pub const IDLE_MIN_TOKENS: i64 = 5_000_000;
pub const HOG_SHARE: f64 = 0.7;
pub const HOG_MIN_TOKENS: i64 = 20_000_000;
pub const OVERLAP_MIN: f64 = 60.0;
pub const FULL_REPEATS: usize = 3;
pub const REPEAT_MIN_SECONDS: f64 = 180.0;
pub const LOW_FREE_PCT: f64 = 15.0;
pub const HIGH_LOAD: f64 = 2.0;
pub const LOOP_TURNS: usize = 3;
pub const LOOP_TOTAL: usize = 3;
pub const LOOP_WINDOW: f64 = 2.0 * 3600.0;
pub const LOOP_LIVE: f64 = 30.0 * 60.0;
pub const REVIEW_ROUNDS: usize = 5;
pub const FIX_CYCLES: i64 = 5;
pub const BOX_LOW_CPU: f64 = 30.0;
pub const BOX_MIN_HOURS: f64 = 1.0;
pub const BOX_LIVE_HOURS: i64 = 3;

fn is_main(branch: Option<&str>) -> bool {
    matches!(branch, None | Some("" | "main" | "master" | "HEAD"))
}

const WRAPPERS: [&str; 10] = ["env", "time", "nice", "command", "exec", "sudo", "caffeinate", "npx", "bunx", "pnpx"];
const RUNNERS: [&str; 5] = ["uv", "poetry", "pipenv", "hatch", "pdm"];
const VALUE_FLAGS: [&str; 14] = [
    "--with",
    "--python",
    "-p",
    "--project",
    "--directory",
    "--package",
    "--extra",
    "--group",
    "--env-file",
    "--from",
    "-n",
    "-k",
    "-s",
    "-g",
];

static HEAVY: LazyLock<Vec<(&'static str, Vec<&'static str>)>> = LazyLock::new(|| {
    let test = [
        "pytest",
        "py.test",
        "unittest",
        "tox",
        "nox",
        "vitest",
        "jest",
        "mocha",
        "rspec",
        "phpunit",
        "ctest",
        "cargo test",
        "cargo nextest",
        "go test",
        "npm test",
        "npm run test",
        "pnpm test",
        "pnpm run test",
        "yarn test",
        "yarn run test",
        "bun test",
        "bun run test",
        "make test",
        "make check",
        "mvn test",
        "gradle test",
        "gradlew test",
        "swift test",
        "xcodebuild test",
        "dotnet test",
        "mix test",
    ];
    let build = [
        "cargo build",
        "cargo tauri build",
        "tauri build",
        "vite build",
        "npm run build",
        "pnpm build",
        "pnpm run build",
        "yarn build",
        "yarn run build",
        "bun run build",
        "next build",
        "tsc",
        "go build",
        "swift build",
        "docker build",
        "xcodebuild",
        "mvn package",
        "mvn install",
        "gradle build",
        "gradlew build",
        "make build",
        "make all",
    ];
    let ci = ["gh workflow run", "gh run rerun"];
    let review = ["codex review", "gh pr review", "coderabbit"];
    let mut out: Vec<_> = [("test", &test[..]), ("build", &build[..]), ("ci", &ci[..]), ("review", &review[..])]
        .into_iter()
        .flat_map(|(kind, words)| words.iter().map(move |w| (kind, w.split(' ').collect::<Vec<_>>())))
        .collect();
    out.sort_by_key(|(_, w)| std::cmp::Reverse(w.len())); // the longest match wins: `xcodebuild test` first
    out
});

const NARROW_FLAGS: [&str; 12] = [
    "-k",
    "-run",
    "--lf",
    "--last-failed",
    "--failed",
    "-t",
    "--testnamepattern",
    "--filter",
    "--package",
    "-p",
    "--test",
    "--changed",
];
const WHOLE: [&str; 10] = ["run", ".", "./...", "tests", "test", "tests/", "test/", "--", "src", "src/"];
const CHANGE_TOOLS: [&str; 9] =
    ["Edit", "Write", "NotebookEdit", "MultiEdit", "Agent", "Task", "apply_patch", "spawn_agent", "js"];
const READ_ONLY: [&str; 41] = [
    "ls", "cat", "head", "tail", "grep", "egrep", "rg", "find", "wc", "awk", "sed", "pwd", "cd", "echo", "printf",
    "date", "sleep", "which", "jq", "sort", "uniq", "cut", "tr", "stat", "file", "du", "df", "true", "test", "tree",
    "ps", "asm", "hc", "basename", "dirname", "realpath", "diff", "nl", "less", "column", "xargs",
];
const READ_ONLY_GIT: [&str; 14] = [
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "rev-parse",
    "blame",
    "remote",
    "ls-files",
    "describe",
    "fetch",
    "merge-base",
    "shortlog",
    "worktree",
];
const READ_ONLY_GH: [&str; 5] = ["view", "list", "checks", "status", "diff"];

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap()
}

static COMMIT_MESSAGE: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r#"\bgit\s+commit\b[^\n]*?\s(?:-\w*m|--message)[\s=]+(?:["']?\$\(cat\s+<<-?\s*['"]?\w+['"]?\s*\n\s*|["'])?\s*([^\n]*)"#,
    )
});
static FIX_SUBJECT: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^\W*(fix|fixes|fixed|bugfix|hotfix|address|re-?review|revert)\b"));
static LOG_NOISE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^(exit code|script (completed|failed)|wall time|output:|process exited|chunk id|original token)")
});
static CODEX_FAILED: LazyLock<Regex> =
    LazyLock::new(|| re(r#"Script failed|Process exited with code [1-9]|"?exit_code"?\s*[:=]\s*[1-9]"#));
static ASSIGNMENT: LazyLock<Regex> = LazyLock::new(|| re(r"^\w+="));
static REDIRECT: LazyLock<Regex> = LazyLock::new(|| re(r"^\d*>"));

// --- shlex ------------------------------------------------------------------------------------------------------------

/// Python's `shlex.shlex(posix=True)` with `whitespace_split=True`, ported from CPython's `read_token`: the brain
/// reads commands exactly as the Python did (comments mid-word, an escape only of `"` and `\` inside double quotes).
pub(crate) struct Shlex<'a> {
    pub whitespace: &'a str,
    pub commenters: &'a str,
    pub punctuation: &'a str,
}

impl Shlex<'_> {
    /// The tokens, or None where Python raises ("No closing quotation", "No escaped character").
    pub fn tokens(&self, text: &str) -> Option<Vec<String>> {
        let chars: Vec<char> = text.chars().collect();
        let (mut pos, mut pushback, mut out) = (0usize, Vec::<char>::new(), vec![]);
        let mut state = Some(' ');
        loop {
            let mut token = String::new();
            let (mut quoted, mut escaped_state) = (false, ' ');
            loop {
                let next: Option<char> = if !self.punctuation.is_empty() && !pushback.is_empty() {
                    pushback.pop()
                } else {
                    let c = chars.get(pos).copied();
                    pos += c.is_some() as usize;
                    c
                };
                let is = |set: &str| next.is_some_and(|c| set.contains(c));
                let skip_line = |pos: &mut usize| {
                    while *pos < chars.len() {
                        *pos += 1;
                        if chars[*pos - 1] == '\n' {
                            break;
                        }
                    }
                };
                match state {
                    None => {
                        token.clear();
                        break;
                    }
                    Some(' ') => match next {
                        None => {
                            state = None;
                            break;
                        }
                        Some(_) if is(self.whitespace) => {
                            if !token.is_empty() || quoted {
                                break;
                            }
                        }
                        Some(_) if is(self.commenters) => skip_line(&mut pos),
                        Some('\\') => {
                            escaped_state = 'a';
                            state = Some('\\');
                        }
                        Some(c) if self.punctuation.contains(c) => {
                            token.push(c);
                            state = Some('c');
                        }
                        Some(c @ ('\'' | '"')) => state = Some(c),
                        Some(c) => {
                            token.push(c);
                            state = Some('a');
                        }
                    },
                    Some(q @ ('\'' | '"')) => {
                        quoted = true;
                        match next {
                            None => return None,
                            Some(c) if c == q => state = Some('a'),
                            Some('\\') if q == '"' => {
                                escaped_state = q;
                                state = Some('\\');
                            }
                            Some(c) => token.push(c),
                        }
                    }
                    Some('\\') => {
                        let c = next?;
                        if (escaped_state == '\'' || escaped_state == '"') && c != '\\' && c != escaped_state {
                            token.push('\\');
                        }
                        token.push(c);
                        state = Some(escaped_state);
                    }
                    Some(st) => {
                        // 'a' (a word) or 'c' (punctuation)
                        match next {
                            None => {
                                state = None;
                                break;
                            }
                            Some(_) if is(self.whitespace) => {
                                state = Some(' ');
                                if !token.is_empty() || quoted {
                                    break;
                                }
                            }
                            Some(_) if is(self.commenters) => {
                                skip_line(&mut pos);
                                state = Some(' ');
                                if !token.is_empty() || quoted {
                                    break;
                                }
                            }
                            Some(c) if st == 'c' => {
                                if self.punctuation.contains(c) {
                                    token.push(c);
                                } else {
                                    if !self.whitespace.contains(c) {
                                        pushback.push(c);
                                    }
                                    state = Some(' ');
                                    break;
                                }
                            }
                            Some(c @ ('\'' | '"')) => state = Some(c),
                            Some('\\') => {
                                escaped_state = 'a';
                                state = Some('\\');
                            }
                            Some(c) if !self.punctuation.contains(c) => token.push(c),
                            Some(c) => {
                                pushback.push(c);
                                state = Some(' ');
                                if !token.is_empty() || quoted {
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            if token.is_empty() && !quoted {
                return Some(out); // end of input
            }
            out.push(token);
        }
    }
}

/// `shlex.split(s)`.
pub(crate) fn shlex_split(s: &str) -> Option<Vec<String>> {
    Shlex { whitespace: " \t\r\n", commenters: "", punctuation: "" }.tokens(s)
}

// --- reading commands -----------------------------------------------------------------------------------------------

/// A shell command, or a Codex `exec` script -> the shell commands in it. A command held in a variable can't be read.
pub fn commands_of(raw: Option<&str>) -> Vec<String> {
    let raw = raw.unwrap_or("");
    if !raw.contains("tools.") {
        return if raw.is_empty() { vec![] } else { vec![raw.into()] };
    }
    script_commands(raw).into_iter().map(|body| unescape(&body)).collect()
}

/// Bodies of `cmd: "..."` (or `'...'`, or backquotes) in a script, as the Python's `SCRIPT_CMD` finds them.
fn script_commands(raw: &str) -> Vec<String> {
    let c: Vec<char> = raw.chars().collect();
    let word = |i: usize| c.get(i).is_some_and(|&ch| ch.is_alphanumeric() || ch == '_');
    let (mut out, mut i) = (vec![], 0);
    'scan: while i < c.len() {
        let at = i;
        i += 1;
        if !(c[at..].starts_with(&['c', 'm', 'd']) && (at == 0 || !word(at - 1)) && !word(at + 3)) {
            continue;
        }
        let mut j = at + 3;
        while j < c.len() && pyrules::isspace(c[j]) {
            j += 1;
        }
        if c.get(j) != Some(&':') {
            continue;
        }
        j += 1;
        while j < c.len() && pyrules::isspace(c[j]) {
            j += 1;
        }
        let Some(&q) = c.get(j).filter(|q| matches!(q, '"' | '\'' | '`')) else { continue };
        let (start, mut k) = (j + 1, j + 1);
        loop {
            match c.get(k) {
                None => continue 'scan,
                Some('\\') if k + 1 < c.len() => k += 2,
                Some('\\') => continue 'scan,
                Some(&ch) if ch == q => break,
                Some(_) => k += 1,
            }
        }
        out.push(c[start..k].iter().collect());
        i = k + 1;
    }
    out
}

/// `re.sub(r'\\(.)', ...)`: `\n` and `\t` become their characters, any other escaped character itself.
fn unescape(body: &str) -> String {
    let mut out = String::new();
    let mut it = body.chars().peekable();
    while let Some(ch) = it.next() {
        match (ch, it.peek()) {
            ('\\', Some(&n)) if n != '\n' => {
                it.next();
                out.push(match n {
                    'n' => '\n',
                    't' => '\t',
                    n => n,
                });
            }
            _ => out.push(ch),
        }
    }
    out
}

/// The Python's `HEREDOC.sub(r'\2', ...)`: a heredoc's body is dropped, the rest of its first line kept.
fn drop_heredocs(text: &str) -> String {
    let c: Vec<char> = text.chars().collect();
    let word = |ch: char| ch.is_alphanumeric() || ch == '_';
    let (mut out, mut i) = (String::new(), 0);
    while i < c.len() {
        match heredoc_at(&c, i, word) {
            Some((rest, end)) => {
                out.extend(&c[rest.0..rest.1]);
                i = end;
            }
            None => {
                out.push(c[i]);
                i += 1;
            }
        }
    }
    out
}

/// A heredoc starting at `i` -> (the rest of its first line, where the match ends).
fn heredoc_at(c: &[char], i: usize, word: impl Fn(char) -> bool) -> Option<((usize, usize), usize)> {
    if !c[i..].starts_with(&['<', '<']) {
        return None;
    }
    let mut j = i + 2;
    if c.get(j) == Some(&'-') {
        j += 1;
    }
    while j < c.len() && pyrules::isspace(c[j]) {
        j += 1;
    }
    if matches!(c.get(j), Some('\'' | '"')) {
        j += 1;
    }
    let w0 = j;
    while j < c.len() && word(c[j]) {
        j += 1;
    }
    for w1 in (w0 + 1..=j).rev() {
        let mut k = w1;
        if w1 == j && matches!(c.get(k), Some('\'' | '"')) {
            k += 1;
        }
        let rest = k;
        while k < c.len() && c[k] != '\n' {
            k += 1;
        }
        if k >= c.len() {
            continue;
        }
        let line_end = (rest, k);
        let name = &c[w0..w1];
        // `\n.*?^\s*NAME\s*$`: the first line start after it where the name stands alone
        let mut p = k + 1;
        while p <= c.len() {
            if p == k + 1 || c[p - 1] == '\n' {
                let mut q = p;
                while q < c.len() && pyrules::isspace(c[q]) {
                    q += 1;
                }
                if c[q..].starts_with(name) {
                    let after = q + name.len();
                    let mut r = after;
                    let mut last_newline = None;
                    while r < c.len() && pyrules::isspace(c[r]) {
                        if c[r] == '\n' {
                            last_newline = Some(r);
                        }
                        r += 1;
                    }
                    if r == c.len() {
                        return Some((line_end, r));
                    }
                    if let Some(n) = last_newline {
                        return Some((line_end, n));
                    }
                }
            }
            p += 1;
        }
    }
    None
}

/// `'a && b | c'` -> `[['a'], ['b'], ['c']]`, respecting quotes and dropping heredoc bodies. None if it can't be read.
pub(crate) fn segments(command: &str) -> Option<Vec<Vec<String>>> {
    static DUP: LazyLock<Regex> = LazyLock::new(|| re(r"\d*>&\d+"));
    let text = DUP.replace_all(&drop_heredocs(command), "").into_owned();
    let tokens = Shlex { whitespace: " \t\r", commenters: "#", punctuation: ";&|()\n" }.tokens(&text)?;
    let (mut out, mut now) = (vec![], vec![]);
    for tok in tokens {
        if !tok.is_empty() && tok.chars().all(|c| ";&|()\n".contains(c)) {
            out.push(std::mem::take(&mut now));
        } else {
            now.push(tok);
        }
    }
    out.push(now);
    Some(out.into_iter().filter(|s| !s.is_empty()).collect())
}

/// Drop what only starts the real program: variables, `uv run`, `npx`, `python -m`, `timeout 60`.
fn core(words: &[String]) -> &[String] {
    static DURATION: LazyLock<Regex> = LazyLock::new(|| re(r"^[\d.]+[smhd]?$"));
    static PYTHON: LazyLock<Regex> = LazyLock::new(|| re(r"^python[\d.]*$"));
    let mut w = words;
    while let Some(first) = w.first() {
        let base = basename(first);
        let second = w.get(1).map(String::as_str);
        if ASSIGNMENT.is_match(first) {
            w = &w[1..];
        } else if base == "timeout" {
            w = &w[1..];
            while w.first().is_some_and(|x| x.starts_with('-') || DURATION.is_match(x)) {
                w = &w[1..];
            }
        } else if WRAPPERS.contains(&base)
            || (RUNNERS.contains(&base) && second == Some("run"))
            || (base == "pnpm" && second == Some("exec"))
        {
            w = if RUNNERS.contains(&base) || base == "pnpm" { &w[2.min(w.len())..] } else { &w[1..] };
            while let Some(flag) = w.first().filter(|x| x.starts_with('-')) {
                let n = if VALUE_FLAGS.contains(&flag.as_str()) && !flag.contains('=') { 2 } else { 1 };
                w = &w[n.min(w.len())..];
            }
        } else if PYTHON.is_match(base) && second == Some("-m") {
            w = &w[2..];
        } else {
            break;
        }
    }
    w
}

/// `str.isdigit()`.
fn isdigit(s: &str) -> bool {
    !s.is_empty() && s.chars().all(char::is_numeric)
}

/// No path or selector means the whole suite. Anything unclear counts as narrow, so it errs towards quiet.
fn is_full(rest: &[String]) -> bool {
    static REDIRECTED: LazyLock<Regex> = LazyLock::new(|| re(r"^\d*[<>]"));
    for w in rest {
        if REDIRECTED.is_match(w) {
            break;
        }
        let flag = w.split('=').next().unwrap_or("").to_lowercase();
        if NARROW_FLAGS.contains(&w.to_lowercase().as_str()) || (w.starts_with('-') && NARROW_FLAGS.contains(&&*flag)) {
            return false;
        }
        if !w.starts_with('-') && !WHOLE.contains(&w.as_str()) && !isdigit(w) {
            return false;
        }
    }
    true
}

/// A shell command -> [(kind, full suite?, label)]. Labels name the program, never its arguments.
pub fn heavy_runs(command: &str, extra: &[String]) -> Vec<(String, bool, String)> {
    let mut out = vec![];
    for seg in segments(command).unwrap_or_default() {
        let words = core(&seg);
        if words.is_empty() {
            continue;
        }
        let text = words.join(" ");
        if extra.iter().any(|p| text == *p || text.starts_with(&format!("{p} "))) {
            out.push(("project".into(), false, basename(&words[0]).into()));
            continue;
        }
        let names: Vec<&str> = std::iter::once(basename(&words[0]))
            .chain(words[1..].iter().map(|w| w.split(':').next().unwrap_or("")))
            .collect();
        for (kind, want) in HEAVY.iter() {
            if names.len() >= want.len() && names[..want.len()] == want[..] {
                let full = *kind == "test" && is_full(&words[want.len()..]);
                out.push((kind.to_string(), full, want.join(" ")));
                break;
            }
        }
    }
    out
}

/// A project's own heavy commands: `.assieme/heavy.txt`, one command prefix per line.
pub fn project_heavy(cwd: Option<&str>) -> Vec<String> {
    let path = Path::new(cwd.unwrap_or("")).join(".assieme/heavy.txt");
    let Ok(bytes) = std::fs::read(path) else { return vec![] };
    let text = String::from_utf8_lossy(&bytes);
    crate::adapters::text_lines(&text)
        .into_iter()
        .map(|l| l.trim_matches(pyrules::isspace))
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(String::from)
        .collect()
}

/// Could this call have changed anything? Only what is known to be read-only says no.
pub fn changes_something(tool: Option<&str>, command: Option<&str>) -> bool {
    if tool.is_some_and(|t| CHANGE_TOOLS.contains(&t) || t.starts_with("mcp__")) {
        return true;
    }
    let Some(command) = command.filter(|c| !c.is_empty()) else { return false };
    let Some(segments) = segments(command) else { return true };
    for seg in segments {
        let words = core(&seg);
        let Some(first) = words.first() else { continue };
        let base = basename(first);
        if words.iter().any(|w| REDIRECT.is_match(w)) || (base == "sed" && words.iter().any(|w| w == "-i")) {
            return true;
        }
        if base == "git" && words.get(1).is_some_and(|w| READ_ONLY_GIT.contains(&w.as_str())) {
            continue;
        }
        if base == "gh" && words.get(2).is_some_and(|w| READ_ONLY_GH.contains(&w.as_str())) {
            continue;
        }
        if !READ_ONLY.contains(&base) {
            return true;
        }
    }
    false
}

/// Failed output -> a fingerprint that ignores numbers and paths, or None if it says nothing. The text isn't kept.
pub fn failure_sig(text: &str) -> Option<String> {
    use sha1::{Digest, Sha1};
    static PATH: LazyLock<Regex> = LazyLock::new(|| re(r"[\w.~-]*/[^\s:]*"));
    static DIGITS: LazyLock<Regex> = LazyLock::new(|| re(r"\d+"));
    let lines: Vec<&str> = pyrules::splitlines(text)
        .into_iter()
        .map(|x| x.trim_matches(pyrules::isspace))
        .filter(|x| !x.is_empty() && !LOG_NOISE.is_match(x))
        .collect();
    let tail = lines[lines.len().saturating_sub(2)..].join(" ").to_lowercase();
    let tail = DIGITS.replace_all(&PATH.replace_all(&tail, "/"), "n").into_owned();
    if tail.chars().count() < 20 {
        return None;
    }
    let hex: String = Sha1::digest(tail.as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
    Some(hex[..12].into())
}

pub fn codex_failed(text: &str) -> bool {
    let head: String = text.chars().take(4000).collect();
    CODEX_FAILED.is_match(&head)
}

pub fn is_fix_commit(command: &str) -> bool {
    COMMIT_MESSAGE.captures(command).is_some_and(|m| FIX_SUBJECT.is_match(&m[1]))
}

/// One tool call in a Session -> what F5, F6 and F7 need from it. Returns the index of the heavy job it started.
pub fn note_call(
    s: &mut Session,
    tool: Option<&str>,
    command: Option<&str>,
    ts: Timestamp,
    call_id: &str,
    branch: Option<&str>,
) -> Option<usize> {
    let cmds = commands_of(command);
    let raw = command.unwrap_or("");
    if let Some(turn) = s.turns.last_mut().filter(|t| !t.changed) {
        turn.changed = changes_something(tool, None)
            || cmds.iter().any(|c| changes_something(tool, Some(c)))
            || raw.contains("apply_patch")
            || (raw.contains("exec_command") && cmds.is_empty());
    }
    if let Some(b) = branch.filter(|b| !is_main(Some(b))) {
        for c in &cmds {
            if is_fix_commit(c) {
                s.cycles.add(b.into(), 1);
            }
        }
    }
    let extra = project_heavy(s.cwd.as_deref());
    let runs: Vec<_> = cmds.iter().flat_map(|c| heavy_runs(c, &extra)).collect();
    if runs.is_empty() {
        return None;
    }
    s.jobs.push(Job { id: call_id.into(), start: ts, end: None, runs, branch: branch.map(String::from) });
    Some(s.jobs.len() - 1)
}

// --- the checks -----------------------------------------------------------------------------------------------------

/// What a finding holds: what is wrong, why it matters, the fix, and its numbers.
fn finding(kind: &str, agent: Option<&str>, what: String, why: String, fix: &str) -> Map<String, Value> {
    let line = format!("{what}. {why}. {fix}.");
    let mut f = Map::new();
    for (k, v) in [
        ("kind", json!(kind)),
        ("agent", json!(agent)),
        ("what", json!(what)),
        ("why", json!(why)),
        ("fix", json!(fix)),
        ("line", json!(line)),
        ("message", Value::Null),
        ("label", Value::Null),
    ] {
        f.insert(k.into(), v);
    }
    f
}

fn with(mut f: Map<String, Value>, extra: &[(&str, Value)]) -> Value {
    for (k, v) in extra {
        f.insert((*k).into(), v.clone());
    }
    Value::Object(f)
}

/// A time on the person's clock, `HH:MM`.
fn at(t: Timestamp) -> String {
    t.to_zoned_datetime(jiff::tz::TimeZone::system()).strftime("%H:%M").to_string()
}

/// Python's `round()` to a whole number: halves go to the even neighbour.
pub(crate) fn round_even(x: f64) -> i64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 && r % 2.0 != 0.0 { (r - x.signum()) as i64 } else { r as i64 }
}

/// Python's `a // b` for whole numbers.
fn floor_div(a: i64, b: i64) -> i64 {
    a.div_euclid(b)
}

/// (time, context) calls -> runs between compactions, each as (growth per active hour, active hours).
fn runs(samples: &[(Timestamp, i64)]) -> Vec<(i64, f64)> {
    let mut order = samples.to_vec();
    order.sort();
    let (mut out, mut first, mut prev, mut active) = (vec![], None, None::<(Timestamp, i64)>, 0.0);
    for (ts, size) in order {
        match prev {
            Some(p) if (size as f64) >= p.1 as f64 * COMPACTED => {
                let gap = secs(ts, p.0);
                active += if gap <= ACTIVE_GAP { gap } else { 0.0 };
            }
            _ => {
                if let (Some(p), Some(f)) = (prev, first) {
                    out.push((f, p, active));
                }
                (first, active) = (Some((ts, size)), 0.0);
            }
        }
        prev = Some((ts, size));
    }
    if let (Some(p), Some(f)) = (prev, first) {
        out.push((f, p, active));
    }
    out.into_iter()
        .filter(|(_, _, sec)| *sec >= GROWTH_MIN_HOURS * 3600.0)
        .map(|(a, b, sec)| (round_even((b.1 - a.1) as f64 / (sec / 3600.0)), sec / 3600.0))
        .collect()
}

/// The fastest growth per active hour in any run, or 0.
pub fn growth(samples: &[(Timestamp, i64)]) -> i64 {
    runs(samples).into_iter().map(|(rate, _)| rate).max().unwrap_or(0)
}

/// Growth of the current run only, or 0.
pub fn growth_now(samples: &[(Timestamp, i64)]) -> i64 {
    let mut order = samples.to_vec();
    order.sort();
    let mut start = 0;
    for i in 1..order.len() {
        if (order[i].1 as f64) < order[i - 1].1 as f64 * COMPACTED {
            start = i;
        }
    }
    runs(&order[start.min(order.len())..]).last().map_or(0, |r| r.0)
}

/// Sorted times -> the longest stretch that fits in the window.
fn in_window(times: &[Timestamp], seconds: f64) -> Vec<Timestamp> {
    let (mut best, mut start): (&[Timestamp], usize) = (&[], 0);
    for (end, &t) in times.iter().enumerate() {
        while secs(t, times[start]) > seconds {
            start += 1;
        }
        if end - start + 1 > best.len() {
            best = &times[start..=end];
        }
    }
    best.to_vec()
}

/// A failure that keeps coming back.
pub struct Loop {
    pub sig: String,
    pub label: String,
    pub count: usize,
    pub turns: usize,
    pub times: Vec<Timestamp>,
}

/// [(time, fingerprint, turn, label)] -> one per failure that keeps coming back.
pub fn loops<T: Clone + Eq + Hash>(failures: &[(Timestamp, String, T, String)]) -> Vec<Loop> {
    let mut by_sig: Vec<(&String, BTreeMap<Timestamp, (T, &String)>)> = vec![];
    for (ts, sig, turn, label) in failures {
        let rows = match by_sig.iter().position(|(s, _)| *s == sig) {
            Some(i) => &mut by_sig[i].1,
            None => {
                by_sig.push((sig, BTreeMap::new()));
                &mut by_sig.last_mut().unwrap().1
            }
        };
        rows.insert(*ts, (turn.clone(), label));
    }
    let mut out = vec![];
    for (sig, rows) in by_sig {
        let times = in_window(&rows.keys().copied().collect::<Vec<_>>(), LOOP_WINDOW);
        let turns: HashMap<&T, ()> = times.iter().map(|t| (&rows[t].0, ())).collect();
        if turns.len() >= LOOP_TURNS || times.len() >= LOOP_TOTAL {
            let label = rows[times.last().unwrap()].1.clone();
            out.push(Loop { sig: sig.clone(), label, count: times.len(), turns: turns.len(), times });
        }
    }
    out
}

fn loop_finding(agent: &str, x: &Loop, tell: &[(&str, Value)]) -> Value {
    let history = x.times[x.times.len().saturating_sub(3)..].iter().map(|t| at(*t)).collect::<Vec<_>>().join(", ");
    let f = finding(
        "loop",
        Some(agent),
        format!("{agent} is going in circles: `{}` hit the same failure {} times ({history})", x.label, x.count),
        "Retrying has not changed the result, and each try costs time and tokens".into(),
        "Stop it and have it try a different approach",
    );
    let mut extra = tell.to_vec();
    extra.push(("count", json!(x.count)));
    with(f, &extra)
}

fn pressed(m: &Value) -> bool {
    let n = |k: &str| m.get(k).and_then(Value::as_f64).unwrap_or(0.0);
    n("free_pct") < LOW_FREE_PCT || n("load") >= HIGH_LOAD * n("cpus")
}

fn pressure_words(m: &Value) -> String {
    let n = |k: &str| m.get(k).and_then(Value::as_f64).unwrap_or(0.0);
    format!("{:.0}% free memory, load {:.0}", n("free_pct"), n("load"))
}

/// A machine sample's time: an ISO text or seconds since 1970.
fn sample_at(m: &Value) -> Option<Timestamp> {
    let at = m.get("at")?;
    crate::model::parse_ts(at).or_else(|| at.as_f64().and_then(|s| Timestamp::from_microsecond((s * 1e6) as i64).ok()))
}

/// Every heavy job once, with its agent: a resumed session's log repeats the earlier rows.
fn unique_jobs(sessions: &[Session]) -> Vec<(&str, &Job)> {
    let mut seen: Vec<(&str, &Job)> = vec![];
    for s in sessions {
        for j in &s.jobs {
            if !seen.iter().any(|(_, k)| k.id == j.id) {
                seen.push((&s.agent, j));
            }
        }
    }
    seen
}

fn end(j: &Job) -> Timestamp {
    j.end.unwrap_or(j.start)
}

/// F8: hours spent per heavy command and per refusal source, from the logs. A compound command counts once.
pub fn gate_hours(sessions: &[Session]) -> BTreeMap<String, f64> {
    let mut hours: Vec<(String, f64)> = vec![];
    let mut add = |k: String, v: f64| match hours.iter_mut().find(|(x, _)| *x == k) {
        Some(slot) => slot.1 += v,
        None => hours.push((k, v)),
    };
    for (_, j) in unique_jobs(sessions) {
        add(j.runs[0].2.clone(), secs(end(j), j.start) / 3600.0);
    }
    for s in sessions {
        for (why, seconds) in &s.refusal_seconds.0 {
            add(format!("refused: {why}"), *seconds as f64 / 3600.0);
        }
    }
    hours.into_iter().filter(|(_, v)| *v > 0.0).map(|(k, v)| (k, (v * 100.0).round() / 100.0)).collect()
}

struct Overlap {
    start: Timestamp,
    seconds: i64,
    who: String,
    pressed: Option<Value>,
}

fn overlaps(jobs: &[(&str, &Job)], machine: &[Value]) -> Vec<Overlap> {
    let mut jobs = jobs.to_vec();
    jobs.sort_by_key(|(_, j)| j.start);
    let mut out = vec![];
    for (i, (who, a)) in jobs.iter().enumerate() {
        for (other, b) in &jobs[i + 1..] {
            if b.start >= end(a) {
                break;
            }
            let (start, stop) = (b.start, end(a).min(end(b)));
            if secs(stop, start) >= OVERLAP_MIN {
                let hit = machine.iter().find(|m| sample_at(m).is_some_and(|t| start <= t && t <= stop) && pressed(m));
                out.push(Overlap {
                    start,
                    seconds: secs(stop, start) as i64,
                    who: format!("{who}: {}; {other}: {}", a.runs[0].2, b.runs[0].2),
                    pressed: hit.cloned(),
                });
            }
        }
    }
    out
}

/// Sessions -> `{findings, agents: new columns per agent, detectors: each one's count and why it is quiet}`.
pub fn analyse(sessions: &[Session], now: Option<Timestamp>, boxes: &[Value], machine: &[Value]) -> Value {
    let mut findings: Vec<Value> = vec![];
    let mut agents = Map::new();
    let mut per: Vec<(&str, Vec<&Session>)> = vec![];
    for s in sessions {
        match per.iter_mut().find(|(n, _)| *n == s.agent) {
            Some((_, ss)) => ss.push(s),
            None => per.push((&s.agent, vec![s])),
        }
    }

    // F5
    let hour_ago = now.map(|n| n - jiff::SignedDuration::from_hours(1));
    let last_hour: Vec<(&str, i64)> = match hour_ago {
        Some(h) => per
            .iter()
            .map(|(name, ss)| (*name, ss.iter().flat_map(|s| &s.samples).filter(|(t, _)| *t >= h).map(|x| x.1).sum()))
            .collect(),
        None => vec![],
    };
    let everyone: i64 = last_hour.iter().map(|x| x.1).sum();
    for (name, ss) in &per {
        let idle: Vec<_> = ss
            .iter()
            .flat_map(|s| &s.turns)
            .filter(|t| t.sender != HUMAN && t.channel != "subagent" && t.channel != "spawn" && !t.changed)
            .collect();
        let total: i64 = ss.iter().map(|s| s.tokens).sum();
        let rate = ss.iter().map(|s| growth(&s.samples)).max().unwrap_or(0);
        let idle_calls: i64 = idle.iter().map(|t| t.calls).sum();
        let idle_tokens: i64 = idle.iter().map(|t| t.tokens).sum();
        let mine = last_hour.iter().find(|x| x.0 == *name).map_or(0, |x| x.1);
        let share = if everyone > 0 { (mine as f64 / everyone as f64 * 1e4).round() / 1e4 } else { 0.0 };
        agents.insert(
            name.to_string(),
            json!({"growth_per_hour": rate, "idle_calls": idle_calls, "idle_tokens": idle_tokens,
                   "share_last_hour": if everyone > 0 { json!(share) } else { json!(0) }}),
        );
        if rate >= GROWTH_FLAG {
            let why = format!(
                "At that rate a 200K memory fills in about {:.0} min and a 1M memory in about {:.1} h, and every \
                 call re-reads it all",
                200_000.0 / rate as f64 * 60.0,
                1_000_000.0 / rate as f64
            );
            let f = finding(
                "growth",
                Some(name),
                format!("{name}: context grew {}K tokens an hour", floor_div(rate, 1000)),
                why,
                "Compact it at its next pause, or split the work",
            );
            findings.push(with(f, &[("per_hour", json!(rate))]));
        }
        if total > 0 && idle_tokens >= IDLE_MIN_TOKENS && idle_tokens as f64 >= IDLE_SHARE * total as f64 {
            let what = format!(
                "{name}: {:.1}M tokens ({:.0}% of its input) went on {idle_calls} calls in turns that changed nothing",
                idle_tokens as f64 / 1e6,
                100.0 * idle_tokens as f64 / total as f64
            );
            let f = finding(
                "idle",
                Some(name),
                what,
                "Other agents woke it, and it re-read its context without doing any work".into(),
                "Batch the notes it gets, or stop sending the routine ones",
            );
            findings.push(with(f, &[("tokens", json!(idle_tokens))]));
        }
    }
    let hog = last_hour.iter().fold(None::<(&str, i64)>, |best, &(n, v)| match best {
        Some((_, b)) if v <= b => best,
        _ => Some((n, v)),
    });
    if let Some((hog, n)) = hog
        && last_hour.iter().filter(|x| x.1 > 0).count() >= 2
        && everyone >= HOG_MIN_TOKENS
        && n as f64 >= HOG_SHARE * everyone as f64
    {
        findings.push(Value::Object(finding(
            "share",
            Some(hog),
            format!("{hog}: {:.0}% of all input in the last hour", 100.0 * n as f64 / everyone as f64),
            "One agent is spending most of what the team uses right now".into(),
            "Check it is on the work you want most, or give it a narrower task",
        )));
    }

    // F6
    let jobs = unique_jobs(sessions);
    let laps = overlaps(&jobs, machine);
    if !laps.is_empty() {
        let worst = laps.iter().fold(&laps[0], |w, o| if o.seconds > w.seconds { o } else { w });
        let hit = laps.iter().find_map(|o| o.pressed.as_ref());
        let what = format!(
            "Heavy jobs overlapped {} time{}, the longest {} min at {} ({})",
            laps.len(),
            if laps.len() > 1 { "s" } else { "" },
            worst.seconds / 60,
            at(worst.start),
            worst.who
        ) + &hit.map_or(String::new(), |p| format!(", with the machine at {}", pressure_words(p)));
        let f = finding(
            "overlap",
            None,
            what,
            "They compete for the same CPU and memory, so both run slower".into(),
            "Have agents run heavy jobs one at a time",
        );
        findings.push(with(f, &[("count", json!(laps.len())), ("longest_seconds", json!(worst.seconds))]));
    }
    let mut full: Vec<((&str, &str), Vec<&Job>)> = vec![];
    for (who, j) in &jobs {
        for (_, is_full, label) in &j.runs {
            if *is_full {
                match full.iter_mut().find(|(k, _)| *k == (*who, label.as_str())) {
                    Some((_, v)) => v.push(j),
                    None => full.push(((who, label), vec![j])),
                }
            }
        }
    }
    let (mut worst_repeat, mut worst_minutes) = (0usize, 0f64);
    for ((who, label), runs) in &full {
        let cost: BTreeMap<Timestamp, f64> = runs.iter().map(|j| (j.start, secs(end(j), j.start))).collect();
        let most = in_window(&cost.keys().copied().collect::<Vec<_>>(), 3600.0);
        let seconds: f64 = most.iter().map(|t| cost[t]).sum();
        worst_repeat = worst_repeat.max(most.len());
        worst_minutes = worst_minutes.max(seconds / 60.0);
        if most.len() >= FULL_REPEATS && seconds >= REPEAT_MIN_SECONDS {
            let f = finding(
                "repeats",
                Some(who),
                format!("{who}: full suite ({label}) x{} in one hour, from {}", most.len(), at(most[0])),
                "A full run costs the most time and machine, and a narrow run usually answers the question".into(),
                "Have it run only the tests for what it changed, and the full suite once at the end",
            );
            findings.push(with(f, &[("count", json!(most.len()))]));
        }
    }
    let under: Vec<&Value> = machine
        .iter()
        .filter(|m| sample_at(m).is_some_and(|t| jobs.iter().any(|(_, j)| j.start <= t && t <= end(j))) && pressed(m))
        .collect();
    if !under.is_empty() {
        let n = |m: &Value, k: &str| m.get(k).and_then(Value::as_f64).unwrap_or(0.0);
        let worst = under.iter().fold(under[0], |w, m| {
            if n(m, "free_pct") - n(m, "load") < n(w, "free_pct") - n(w, "load") { m } else { w }
        });
        let f = finding(
            "pressure",
            None,
            format!("The machine was under pressure while heavy jobs ran: {}", pressure_words(worst)),
            format!(
                "{} sample{} past the limit, and a Mac out of memory slows everything",
                under.len(),
                if under.len() > 1 { "s" } else { "" }
            ),
            "Run fewer heavy jobs at once",
        );
        findings.push(with(f, &[("count", json!(under.len()))]));
    }

    // F7
    let mut fail_total = 0;
    for (name, ss) in &per {
        let rows: Vec<_> = ss.iter().flat_map(|s| s.failures.iter().cloned()).collect();
        fail_total += rows.len();
        findings.extend(loops(&rows).iter().map(|x| loop_finding(name, x, &[])));
    }
    type RoundKey<'a> = (&'a str, &'a str, Option<&'a str>);
    fn push_round<'a>(rounds: &mut Vec<(RoundKey<'a>, Vec<Timestamp>)>, k: RoundKey<'a>, ts: Vec<Timestamp>) {
        match rounds.iter_mut().find(|(x, _)| *x == k) {
            Some((_, v)) => v.extend(ts),
            None => rounds.push((k, ts)),
        }
    }
    let mut round_times: Vec<(RoundKey, Vec<Timestamp>)> = vec![];
    for s in sessions {
        for t in &s.turns {
            if let Some(ts) = t.ts
                && t.sender != HUMAN
                && format!("{} {}", t.sender, s.agent).to_lowercase().contains("review")
            {
                push_round(&mut round_times, (&t.sender, &s.agent, t.branch.as_deref()), vec![ts]);
            }
        }
    }
    for (who, j) in &jobs {
        let starts = j.runs.iter().filter(|r| r.0 == "review").map(|_| j.start).collect();
        push_round(&mut round_times, ("", who, j.branch.as_deref()), starts);
    }
    // main holds many items, so only a burst of rounds there is one loop
    let rounds: Vec<(RoundKey, usize)> = round_times
        .iter()
        .map(|(k, v)| {
            let mut v = v.clone();
            v.sort();
            (*k, if is_main(k.2) { in_window(&v, LOOP_WINDOW).len() } else { v.len() })
        })
        .collect();
    for ((sender, who, branch), n) in &rounds {
        if *n > REVIEW_ROUNDS {
            let with_whom = if sender.is_empty() { String::new() } else { format!(" with {sender}") };
            let f = finding(
                "review",
                Some(who),
                format!(
                    "{who}: {n} review rounds on {}{with_whom}",
                    branch.filter(|b| !b.is_empty()).unwrap_or("main")
                ),
                "Past this many rounds, reviews are rarely finding new problems".into(),
                "Decide what is good enough, or have a person settle it",
            );
            findings.push(with(f, &[("count", json!(n))]));
        }
    }
    let mut cycles: Vec<(&str, i64)> = vec![];
    for s in sessions {
        for (b, n) in &s.cycles.0 {
            match cycles.iter_mut().find(|(x, _)| x == b) {
                Some(slot) => slot.1 += n,
                None => cycles.push((b, *n)),
            }
        }
    }
    for (branch, n) in &cycles {
        if *n > FIX_CYCLES {
            let f = finding(
                "cycles",
                None,
                format!("{branch}: {n} fix cycles"),
                "One work item keeps coming back".into(),
                "Stop and look at the approach, or split the item",
            );
            findings.push(with(f, &[("count", json!(n))]));
        }
    }

    // F10
    findings.extend(box_findings(boxes));

    let fired = |kind: &str| findings.iter().filter(|f| f["kind"] == kind).count();
    let column = |k: &str| agents.values().filter_map(|a| a[k].as_i64()).max().unwrap_or(0);
    let (fast, idle_top) = (column("growth_per_hour"), column("idle_tokens"));
    let memory_full = crate::settings::threshold("memory_full");
    let paid: BTreeSet<&str> = boxes.iter().filter_map(|b| b["box"].as_str()).filter(|b| *b != "local").collect();
    let detectors = [
        (
            "F5 growth",
            "growth",
            format!(
                "fastest {}K an hour in {} agents; flags at {}K here; live needs a known memory size, {}K an hour and {} full within {} min",
                floor_div(fast, 1000),
                agents.len(),
                GROWTH_FLAG / 1000,
                GROWTH_LIVE / 1000,
                percent(memory_full),
                FULL_SOON_MINUTES as i64
            ),
        ),
        (
            "F5 idle",
            "idle",
            format!(
                "most by one agent {:.1}M; flags at {:.0}M and {} of its input",
                idle_top as f64 / 1e6,
                IDLE_MIN_TOKENS as f64 / 1e6,
                percent(IDLE_SHARE)
            ),
        ),
        (
            "F5 share",
            "share",
            format!(
                "{:.1}M tokens in the last hour; flags one agent at {} of at least {:.0}M",
                everyone as f64 / 1e6,
                percent(HOG_SHARE),
                HOG_MIN_TOKENS as f64 / 1e6
            ),
        ),
        (
            "F6 overlap",
            "overlap",
            format!(
                "{} heavy jobs, {} overlaps of {} s or more; background jobs look instant",
                jobs.len(),
                laps.len(),
                OVERLAP_MIN as i64
            ),
        ),
        (
            "F6 repeats",
            "repeats",
            format!(
                "most full-suite runs in one hour {worst_repeat}, costliest {worst_minutes:.0} min; flags at {FULL_REPEATS} runs and {} min together",
                REPEAT_MIN_SECONDS as i64 / 60
            ),
        ),
        (
            "F6 pressure",
            "pressure",
            format!(
                "{} machine samples, {} past the limit during heavy jobs{}",
                machine.len(),
                under.len(),
                if machine.is_empty() { "; the brain has not sampled this window" } else { "" }
            ),
        ),
        (
            "F7 same failure",
            "loop",
            format!(
                "{fail_total} failed commands; flags at {LOOP_TURNS} turns or {LOOP_TOTAL} times in {} h",
                LOOP_WINDOW as i64 / 3600
            ),
        ),
        (
            "F7 review rounds",
            "review",
            format!(
                "most on one branch {}; flags past {REVIEW_ROUNDS}; on main, within {} h",
                rounds.iter().map(|r| r.1).max().unwrap_or(0),
                LOOP_WINDOW as i64 / 3600
            ),
        ),
        (
            "F7 fix cycles",
            "cycles",
            format!(
                "most on one branch {}; flags past {FIX_CYCLES}; main is not counted",
                cycles.iter().map(|c| c.1).max().unwrap_or(0)
            ),
        ),
        (
            "F10 paid compute",
            "box",
            format!(
                "{} boxes with metrics{}",
                paid.len(),
                if boxes.is_empty() { "; no metrics file, nothing reports cloud boxes yet" } else { "" }
            ),
        ),
    ];
    let detectors: Vec<Value> =
        detectors.iter().map(|(id, kind, why)| json!({"id": id, "fired": fired(kind), "reason": why})).collect();
    json!({"findings": findings, "agents": agents, "detectors": detectors})
}

/// Python's `f'{x:.0%}'`.
fn percent(x: f64) -> String {
    format!("{:.0}%", x * 100.0)
}

/// F10: boxes using far less than what is paid for, or slower than the local run.
pub fn box_findings(rows: &[Value]) -> Vec<Value> {
    let mean = |v: Vec<f64>| (!v.is_empty()).then(|| v.iter().sum::<f64>() / v.len() as f64);
    let num = |r: &Value, k: &str| r.get(k).filter(|v| !v.is_null()).and_then(pyrules::float);
    let name = |r: &Value| r["box"].as_str().unwrap_or("").to_string();
    let local = mean(rows.iter().filter(|r| name(r) == "local").filter_map(|r| num(r, "rate")).collect());
    let boxes: BTreeSet<String> = rows.iter().map(name).filter(|b| b != "local").collect();
    let mut out = vec![];
    for b in boxes {
        let mut mine: Vec<&Value> = rows.iter().filter(|r| name(r) == b).collect();
        let ts = |r: &Value| r["ts"].as_f64().unwrap_or(0.0);
        mine.sort_by(|x, y| ts(x).total_cmp(&ts(y)));
        let span = (ts(mine[mine.len() - 1]) - ts(mine[0])) / 3600.0;
        let cpu = mean(mine.iter().filter_map(|r| num(r, "cpu")).collect());
        let rate = mean(mine.iter().filter_map(|r| num(r, "rate")).collect());
        let price = mine[mine.len() - 1].get("usd_per_hour").and_then(pyrules::float).filter(|p| *p != 0.0);
        let low = cpu.is_some_and(|c| c < BOX_LOW_CPU);
        let slow = matches!((rate, local), (Some(r), Some(l)) if r < l);
        if span < BOX_MIN_HOURS || !(low || slow) {
            continue;
        }
        let mut words = vec![];
        if low {
            words.push(format!("{:.0}% CPU for {span:.0} h", cpu.unwrap_or(0.0)));
        }
        if slow {
            words.push("slower than the local run".into());
        }
        let why = match price {
            Some(p) => format!("About ${:.2} paid for this stretch", p * span),
            None => "You are paying for time it is not using".into(),
        };
        let f = finding(
            "box",
            Some(&b),
            format!("box {b}: {}", words.join("; ")),
            why,
            "Stop or shrink the box, or move the job back to this Mac",
        );
        out.push(Value::Object(f));
    }
    out
}

/// An agent's memory size only when its log proves it: Codex states it, and a Claude Code agent past 200K has 1M.
fn known_memory(a: &Agent) -> Option<i64> {
    if a.source == "codex" {
        return a.context_limit.filter(|l| *l != 0);
    }
    (a.context > 200_000).then_some(1_000_000)
}

/// What `status` and the fix cards show: only what is happening now, from each agent's log tail.
pub fn live_findings(agents: &[Agent], now: Timestamp) -> Vec<Value> {
    let mut out = vec![];
    let active: Vec<&Agent> = agents.iter().filter(|a| a.state != "exited").collect();
    let memory_full = crate::settings::threshold("memory_full"); // read once, not per agent
    for a in &active {
        for x in loops(&a.failures) {
            if secs(now, *x.times.last().unwrap()) <= LOOP_LIVE {
                let message = format!(
                    "assieme: `{}` has failed the same way {} times. Stop retrying it. Say what you have tried, then \
                     try a different approach.",
                    x.label, x.count
                );
                let tell = [("label", json!("Tell it to change approach")), ("message", json!(message))];
                out.push(loop_finding(&a.name, &x, &tell));
            }
        }
        let hour_ago = now - jiff::SignedDuration::from_hours(1);
        let mut full: Vec<(&str, Vec<f64>)> = vec![];
        for (ts, _, is_full, label, call) in &a.heavy {
            if *is_full && *ts >= hour_ago {
                let took = secs(a.heavy_end.get(call).copied().unwrap_or(now), *ts);
                match full.iter_mut().find(|(l, _)| l == label) {
                    Some((_, v)) => v.push(took),
                    None => full.push((label, vec![took])),
                }
            }
        }
        for (label, times) in full {
            if times.len() >= FULL_REPEATS && times.iter().sum::<f64>() >= REPEAT_MIN_SECONDS {
                let f = finding(
                    "repeats",
                    Some(&a.name),
                    format!("{}: full suite ({label}) x{} this hour", a.name, times.len()),
                    "A full run costs the most time and machine, and a narrow run usually answers the question".into(),
                    "Have it run only the tests for what it changed",
                );
                let message = format!(
                    "assieme: you have run the full suite {} times this hour. From now on run a narrow run for the \
                     files you changed, and the full suite once before you finish.",
                    times.len()
                );
                out.push(with(
                    f,
                    &[
                        ("label", json!("Ask it to run fewer tests")),
                        ("count", json!(times.len())),
                        ("message", json!(message)),
                    ],
                ));
            }
        }
        let (rate, known) = (growth_now(&a.samples), known_memory(a));
        let minutes = match known {
            Some(k) if rate >= GROWTH_LIVE => Some((memory_full * k as f64 - a.context as f64) / rate as f64 * 60.0),
            _ => None,
        };
        if let (Some(minutes), Some(known)) = (minutes, known)
            && minutes <= FULL_SOON_MINUTES
        {
            let why = if minutes > 0.0 {
                format!("It reaches {} of its {}K memory in about {minutes:.0} min", percent(memory_full), known / 1000)
            } else {
                format!("It is already past {} of its {}K memory", percent(memory_full), known / 1000)
            };
            let f = finding(
                "growth",
                Some(&a.name),
                format!("{}: context growing {}K tokens an hour", a.name, floor_div(rate, 1000)),
                format!("{why}, and every call re-reads it all"),
                "Compact it at its next pause",
            );
            out.push(with(f, &[("per_hour", json!(rate))]));
        }
    }
    let mut running: Vec<(Timestamp, &Agent, &str)> = active
        .iter()
        .flat_map(|a| {
            a.heavy.iter().filter(|h| a.pending.iter().any(|(id, _)| *id == h.4)).map(move |h| (h.0, *a, h.3.as_str()))
        })
        .collect();
    running.sort_by_key(|r| r.0);
    for pair in running.windows(2) {
        let ((_, first, first_label), (ts, a, label)) = (pair[0], pair[1]);
        if !std::ptr::eq(a, first) && secs(now, ts) >= OVERLAP_MIN {
            let f = finding(
                "overlap",
                Some(&a.name),
                format!("{} ({first_label}) and {} ({label}) are both running heavy jobs", first.name, a.name),
                "They compete for the same CPU and memory, so both run slower".into(),
                "Have one wait for the other",
            );
            let message = format!(
                "assieme: {} is running a heavy job too. When yours ends, wait for it to finish before you start \
                 another.",
                first.name
            );
            out.push(with(f, &[("label", json!("Ask it to wait")), ("message", json!(message))]));
        }
    }
    out
}
