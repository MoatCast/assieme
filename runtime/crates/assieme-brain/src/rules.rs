//! Port of `assieme/rules.py`, owned by the foundation (see OWNERS.md).
//!
//! F4: say why a command was refused, which rule did it, and suggest a narrower rule. Suggestions only.
use crate::pyrules;
use regex::Regex;
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::Path;
use std::sync::LazyLock;

/// The deny rules that apply in a folder, as `rules::load` gives them.
pub type RulesFor = dyn Fn(&str) -> Vec<(String, String)> + Send + Sync;

// Claude Code refusal wordings, each mapped to where the refusal came from.
const DENY_RULE: &str = r"^Permission to use (\w+) with command .* has been denied";
const CLASSIFIER: &str = r"denied by the Claude Code auto mode classifier(?:\. Reason: \[([^\]]+)\])?";
const SAFETY: &str = r"denied by a built-in Claude Code safety check";
const SLEEP: &str = r"^<tool_use_error>Blocked: sleep";
const PATH_RULE: &str = r"blocked by a deny rule|denied by your permission settings";

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("a valid pattern")
}

static DENY_RULE_RE: LazyLock<Regex> = LazyLock::new(|| re(&format!("(?s){DENY_RULE}")));
static CLASSIFIER_RE: LazyLock<Regex> = LazyLock::new(|| re(CLASSIFIER));
static SAFETY_RE: LazyLock<Regex> = LazyLock::new(|| re(SAFETY));
static SLEEP_RE: LazyLock<Regex> = LazyLock::new(|| re(SLEEP));
static PATH_RULE_RE: LazyLock<Regex> = LazyLock::new(|| re(PATH_RULE));

/// Any Claude Code refusal wording (the patterns above, unanchored, as Python joins them).
pub static REFUSED: LazyLock<Regex> = LazyLock::new(|| {
    let parts: Vec<&str> = [DENY_RULE, CLASSIFIER, SAFETY, SLEEP, PATH_RULE].map(|p| p.trim_start_matches('^')).into();
    re(&format!("{}|^Permission (for this|to use)", parts.join("|")))
});

fn home() -> String {
    std::env::home_dir().and_then(|h| h.to_str().map(|h| h.trim_end_matches('/').to_string())).unwrap_or_default()
}

/// The deny rules a settings file holds; nothing when it can't be read.
fn deny_rules(path: &Path) -> Option<Vec<String>> {
    let text = String::from_utf8(std::fs::read(path).ok()?).ok()?;
    let settings = pyrules::json_loads(&text).ok()?;
    let permissions = match settings.as_object()?.get("permissions") {
        Some(p) if pyrules::truthy(p) => p.as_object()?.clone(),
        _ => return Some(vec![]),
    };
    // Python iterates whatever `deny` is: a list's strings, a dict's keys, a string's characters. Python fails on a
    // number there; here it reads no rules.
    Some(match permissions.get("deny") {
        Some(Value::Array(a)) => a.iter().filter_map(|r| r.as_str().map(String::from)).collect(),
        Some(Value::Object(o)) => o.keys().cloned().collect(),
        Some(Value::String(s)) => s.chars().map(String::from).collect(),
        _ => vec![],
    })
}

/// Deny rules that apply in cwd -> [(rule, file)], project files first.
pub fn load(cwd: Option<&str>, user_settings: &Path) -> Vec<(String, String)> {
    let project = cwd.filter(|c| !c.is_empty()).map(|c| {
        ["settings.local.json", "settings.json"].map(|n| Path::new(c).join(".claude").join(n).display().to_string())
    });
    let mut out = vec![];
    for path in project.into_iter().flatten().chain([user_settings.display().to_string()]) {
        for rule in deny_rules(Path::new(&path)).unwrap_or_default() {
            out.push((rule, short(&path)));
        }
    }
    out
}

fn short(path: &str) -> String {
    let home = home();
    match path.strip_prefix(&format!("{home}/")) {
        Some(rest) => format!("~/{rest}"),
        None => path.to_string(),
    }
}

/// 'a && b | c' -> ['a', 'b', 'c']: Claude Code checks each part of a compound command.
fn parts(command: &str) -> Vec<&str> {
    static SPLIT: LazyLock<Regex> = LazyLock::new(|| re(r"&&|\|\||[;|\n]"));
    SPLIT.split(command).map(|p| p.trim_matches(pyrules::isspace)).filter(|p| !p.is_empty()).collect()
}

fn bash_match(rule_body: &str, part: &str) -> bool {
    match rule_body.strip_suffix(":*") {
        Some(head) => part == head || part.starts_with(&format!("{head} ")),
        None => pyrules::fnmatchcase(part, rule_body),
    }
}

fn basename(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

/// Refusal text -> (why, note). why names the source; note is the advice, or "".
pub fn explain(
    text: &str,
    tool: &str,
    command: &str,
    rules: &[(String, String)],
    path: Option<&str>,
) -> (String, String) {
    if let Some(m) = CLASSIFIER_RE.captures(text) {
        static WORD: LazyLock<Regex> = LazyLock::new(|| re(r"^[\w.-]+$"));
        let words = pyrules::split(command);
        let cmd = words.iter().take(2).filter(|w| WORD.is_match(w)).copied().collect::<Vec<_>>().join(" ");
        let why = format!("auto-mode classifier ({})", m.get(1).map_or("no reason given", |r| r.as_str()));
        let note = if cmd.is_empty() {
            "change the brief so the agent stops trying".to_string()
        } else {
            format!("if this agent is meant to do it, allow it explicitly (`Bash({cmd}:*)`) or change its brief")
        };
        return (why, note);
    }
    let pair = |a: &str, b: &str| (a.to_string(), b.to_string());
    if SAFETY_RE.is_match(text) {
        return pair("built-in safety check", "delete named paths instead of a broad removal");
    }
    if SLEEP_RE.is_match(text) {
        return pair("built-in sleep guard", "wait with `asm wait` or a Monitor loop instead of sleep");
    }
    if PATH_RULE_RE.is_match(text) {
        static QUOTED: LazyLock<Regex> = LazyLock::new(|| re("'([^']+)'"));
        static BODY: LazyLock<Regex> = LazyLock::new(|| re(r"^(?:Read|Edit|Write)\((.+)\)$"));
        let quoted = QUOTED.captures(text).map(|c| c[1].to_string());
        let p = path.filter(|p| !p.is_empty()).map(String::from).or(quoted);
        for (rule, src) in rules {
            if let (Some(body), Some(p)) = (BODY.captures(rule), &p)
                && path_match(&body[1], p)
            {
                return (format!("deny rule `{rule}` ({src})"), String::new());
            }
        }
        return pair("deny rule on a file path", "");
    }
    if DENY_RULE_RE.is_match(text) || text.starts_with("Permission to use") {
        static BASH: LazyLock<Regex> = LazyLock::new(|| re(r"^Bash\((.+)\)$"));
        for (rule, src) in rules {
            let Some(body) = BASH.captures(rule).filter(|_| tool == "Bash") else { continue };
            for part in parts(command) {
                if bash_match(&body[1], part) {
                    return (format!("deny rule `{rule}` ({src})"), text_only_note(&body[1], part));
                }
            }
        }
        return pair("deny rule (not found in readable settings)", "");
    }
    pair("permission refused", "")
}

/// A rule like Bash(*launcher*) also matches `grep launcher notes.md`: it names text, not the program run.
fn text_only_note(body: &str, part: &str) -> String {
    if !body.starts_with('*') {
        return String::new();
    }
    let spaced = body.replace(":*", " ").replace('*', " ");
    let words = pyrules::split(&spaced);
    let program = pyrules::split(part).first().map_or("", |w| basename(w));
    if words.is_empty() || program == basename(words[0]) {
        return String::new();
    }
    format!(
        "the rule matched a word in the command, not the program run ({program}); deny running it instead: `Bash({}:*)`",
        words.join(" ")
    )
}

/// Python's `os.path.expanduser` for `~` and `~/...` (another user's `~name` is left as it is).
fn expanduser(p: &str) -> String {
    match p.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => format!("{}{rest}", home()),
        _ => p.to_string(),
    }
}

fn path_match(pattern: &str, path: &str) -> bool {
    let pattern = expanduser(if pattern.starts_with("//") { &pattern[1..] } else { pattern });
    let pattern = pattern.replace("**", "*");
    pyrules::fnmatchcase(path, &pattern)
        || pyrules::fnmatchcase(path, &format!("*/{}", pattern.trim_start_matches(['.', '/'])))
}

/// Deny rules from one settings file given with --rules.
pub fn load_file(path: &Path) -> Vec<(String, String)> {
    load(None, path)
}

/// {cwd: [(rule, file)]} for running Claude Code agents started with `--settings <file>`. Never fatal.
///
/// Teams often pass each role's rules this way, and the logs don't record the file, so this is the only zero-setup
/// way to find them.
pub fn running_settings() -> BTreeMap<String, Vec<(String, String)>> {
    let ps = std::process::Command::new("ps").args(["-axo", "pid=,command="]).output();
    let Ok(ps) = ps else { return BTreeMap::new() };
    settings_of(&String::from_utf8_lossy(&ps.stdout), &proc_cwd)
}

/// `running_settings` on a `ps -axo pid=,command=` listing, with a way to find a process's folder.
pub fn settings_of(ps: &str, cwd_of: &dyn Fn(&str) -> Option<String>) -> BTreeMap<String, Vec<(String, String)>> {
    static SETTINGS: LazyLock<Regex> = LazyLock::new(|| re(r"\bclaude\b.*--settings[= ](\S+)"));
    let mut out: BTreeMap<String, Vec<(String, String)>> = BTreeMap::new();
    for line in pyrules::splitlines(ps) {
        let line = line.trim_matches(pyrules::isspace);
        let (pid, cmd) = line.split_once(' ').unwrap_or((line, ""));
        let Some(m) = SETTINGS.captures(cmd) else { continue };
        let file = &m[1];
        if file.starts_with('{') || file.starts_with("'{") {
            continue; // inline JSON settings carry no file to read
        }
        let cwd = cwd_of(pid);
        let path = match &cwd {
            Some(cwd) if !file.starts_with('/') => format!("{cwd}/{file}"),
            _ => file.to_string(),
        };
        if let Some(cwd) = cwd
            && Path::new(&path).is_file()
        {
            out.entry(cwd).or_default().extend(load_file(Path::new(&path)));
        }
    }
    out
}

fn proc_cwd(pid: &str) -> Option<String> {
    if let Ok(link) = std::fs::read_link(format!("/proc/{pid}/cwd")) {
        return Some(link.display().to_string()); // Linux
    }
    let lsof = std::process::Command::new("lsof").args(["-a", "-p", pid, "-d", "cwd", "-Fn"]).output().ok()?;
    let text = String::from_utf8_lossy(&lsof.stdout).into_owned();
    pyrules::splitlines(&text).into_iter().find_map(|l| l.strip_prefix('n').map(String::from))
}
