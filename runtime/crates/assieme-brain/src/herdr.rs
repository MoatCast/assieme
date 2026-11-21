//! Port of `assieme/herdr.py`, owned by session F (see OWNERS.md).
//!
//! Import from herdr: a saved herdr workspace or a launch script, as a New team draft.
//!
//! Read-only: herdr's files are only read, and nothing is started. What doesn't map is said in the draft's `notes`,
//! one plain sentence per kind with the agents it is about, instead of being dropped. A team running in herdr is no
//! longer read (it needed `hc`, which is trimmed from the port).
use crate::prelude::*;
use regex::Regex;
use serde_json::json;
use std::cell::Cell;

/// Whether `w` is one of the space-separated words of `list`.
fn has(list: &str, w: &str) -> bool {
    list.split(' ').any(|x| x == w)
}

/// Agent kinds herdr knows (`herdr agent start --kind`): one of these that isn't Claude Code or Codex is left out with a
/// note; any other program (a shell, a dev server) isn't an agent and is skipped.
const HERDR_KINDS: &str = "pi claude codex gemini cursor devin agy cline omp mastracode opencode copilot kimi kiro droid amp grok hermes kilo qodercli qwen letta maki muse cursor-agent aider";
const MAX_BRIEF: u64 = 256 * 1024;
/// Options that take a value; the rest are switches. Claude Code's tool and folder lists take several values.
const VALUED_CLAUDE: &str = "--model --effort --permission-mode --settings --mcp-config --append-system-prompt --append-system-prompt-file --system-prompt --system-prompt-file --fallback-model --session-id --resume -r --output-format --input-format --agents --name -n --add-dir --allowedTools --allowed-tools --disallowedTools --disallowed-tools --plugin-dir --setting-sources";
const VALUED_CODEX: &str = "-m --model -c --config -s --sandbox -a --ask-for-approval -p --profile -C --cd -i --image --add-dir -o --output-last-message --enable --disable";
const SEVERAL: &str = "--allowedTools --allowed-tools --disallowedTools --disallowed-tools --add-dir";
/// Read here, so they aren't listed as left out.
const READ: &str = "--model -m --effort --permission-mode --dangerously-skip-permissions --allow-dangerously-skip-permissions --append-system-prompt --append-system-prompt-file --system-prompt --system-prompt-file --allowedTools --allowed-tools --disallowedTools --disallowed-tools -s --sandbox -a --ask-for-approval --full-auto --yolo --dangerously-bypass-approvals-and-sandbox -c --config --settings --mcp-config";
/// "${opts[@]}": options only known when the script runs.
const ARRAY: &str = "\u{1}";

fn valued(kind: &str, a: &str) -> bool {
    has(if kind == "claude" { VALUED_CLAUDE } else { VALUED_CODEX }, a)
}

/// One word of a command line; None where it is only known when the script runs.
pub(crate) type Word = Option<String>;
type Vars = BTreeMap<String, Word>;

fn home() -> String {
    std::env::var("HOME").unwrap_or_default()
}

fn config_dir() -> PathBuf {
    let base = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| format!("{}/.config", home()));
    PathBuf::from(base).join("herdr")
}

fn short(path: &str) -> String {
    let h = home();
    if !path.is_empty() && format!("{path}/").starts_with(&format!("{h}/")) {
        format!("~{}", &path[h.len()..])
    } else {
        path.to_string()
    }
}

fn slug(s: &str) -> String {
    static NON: LazyLock<Regex> = LazyLock::new(|| Regex::new("[^a-z0-9]+").unwrap());
    NON.replace_all(&s.to_lowercase(), "-").trim_matches('-').to_string()
}

/// The standard role a name suggests, for its brief and job.
pub(crate) fn kind_of(name: &str) -> &'static str {
    for (word, kind) in
        [("plan", "planner"), ("coord", "planner"), ("lead", "planner"), ("review", "reviewer"), ("test", "tester")]
    {
        if name.contains(word) {
            return kind;
        }
    }
    "builder"
}

// --- what the port asks of the rest of the brain ---------------------------------------------------------------------

type Validate = dyn Fn(&Value) -> Result<(), String>;
type EffortProblem = dyn Fn(&str, &str) -> Option<String>;
type Checkout = dyn Fn(&str) -> (String, Option<String>);

/// New team's answers, read once per import. Tests give their own.
pub(crate) struct Env {
    /// {CLI: the model ids New team offers}. Codex's come from this Mac's own list; an empty one checks nothing.
    pub known: BTreeMap<String, BTreeSet<String>>,
    /// The standard job of each kind of role.
    pub jobs: BTreeMap<String, String>,
    pub kind_brief: Box<dyn Fn(&str) -> String>,
    /// New team's refusal of one role, in words.
    pub validate: Box<Validate>,
    /// Why a thinking effort can't be used, or None.
    pub effort_problem: Box<EffortProblem>,
    /// (the project's main checkout, the branch it is on) for a folder; the folder itself when it isn't in git.
    pub checkout: Box<Checkout>,
}

impl Env {
    pub(crate) fn real() -> Env {
        let known = crate::newteam::model_options()
            .as_object()
            .map(|o| {
                o.iter()
                    .map(|(cli, v)| {
                        let ids = v["models"]
                            .as_array()
                            .map(|m| m.iter().filter_map(|m| m["id"].as_str().map(String::from)).collect());
                        (cli.clone(), ids.unwrap_or_default())
                    })
                    .collect()
            })
            .unwrap_or_default();
        let full =
            crate::newteam::as_preset(&crate::newteam::PRESETS_DIR.join("full"), "full", None).unwrap_or(json!({}));
        let jobs = full["roles"]
            .as_array()
            .map(|r| {
                r.iter()
                    .filter_map(|r| {
                        Some((r["name"].as_str()?.split('-').next()?.to_string(), r["job"].as_str()?.to_string()))
                    })
                    .collect()
            })
            .unwrap_or_default();
        Env {
            known,
            jobs,
            kind_brief: Box::new(crate::newteam::kind_brief),
            validate: Box::new(|role| crate::newteam::validate(std::slice::from_ref(role)).map_err(|e| e.message)),
            effort_problem: Box::new(|kind, effort| crate::launch::check_model_effort(kind, None, Some(effort))),
            checkout: Box::new(checkout),
        }
    }
}

fn checkout(cwd: &str) -> (String, Option<String>) {
    if cwd.is_empty() || !Path::new(cwd).is_dir() {
        return (cwd.to_string(), None);
    }
    let git = |args: &[&str]| crate::worktree::git(cwd, args).ok().filter(|o| o.status.success());
    let Some(common) = git(&["rev-parse", "--path-format=absolute", "--git-common-dir"]) else {
        return (cwd.to_string(), None);
    };
    let path = String::from_utf8_lossy(&common.stdout).trim().to_string();
    let main = if py_basename(&path) == ".git" { py_dirname(&path) } else { path };
    let branch = git(&["branch", "--show-current"])
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|b| !b.is_empty());
    (crate::quiet::real(&main), branch)
}

// --- Python's `os.path` and `str` ------------------------------------------------------------------------------------

pub(crate) fn py_basename(p: &str) -> String {
    p[p.rfind('/').map_or(0, |i| i + 1)..].to_string()
}

pub(crate) fn py_dirname(p: &str) -> String {
    let head = &p[..p.rfind('/').map_or(0, |i| i + 1)];
    if !head.is_empty() && head.chars().any(|c| c != '/') {
        head.trim_end_matches('/').to_string()
    } else {
        head.to_string()
    }
}

fn py_normpath(p: &str) -> String {
    if p.is_empty() {
        return ".".into();
    }
    let slashes = if p.starts_with("//") && !p.starts_with("///") { 2 } else { usize::from(p.starts_with('/')) };
    let mut out: Vec<&str> = vec![];
    for c in p.split('/') {
        if c.is_empty() || c == "." {
            continue;
        }
        if c != ".." || (slashes == 0 && out.is_empty()) || out.last() == Some(&"..") {
            out.push(c);
        } else if !out.is_empty() {
            out.pop();
        }
    }
    let body = out.join("/");
    let joined = format!("{}{body}", "/".repeat(slashes));
    if joined.is_empty() { ".".into() } else { joined }
}

fn py_join(a: &str, b: &str) -> String {
    if b.starts_with('/') || a.is_empty() {
        b.to_string()
    } else if a.ends_with('/') {
        format!("{a}{b}")
    } else {
        format!("{a}/{b}")
    }
}

use crate::quiet::expanduser;

/// `str.splitlines`: Python ends a line at more than `\n`.
fn splitlines(text: &str) -> Vec<String> {
    let mut out = vec![];
    let mut cur = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if matches!(
            c,
            '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
        ) {
            if c == '\r' && chars.peek() == Some(&'\n') {
                chars.next();
            }
            out.push(std::mem::take(&mut cur));
        } else {
            cur.push(c);
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// Python's `shlex` (POSIX, whitespace-split): `comments` makes `#` start a comment, `punct` makes `;&|<>()` words of
/// their own. Returns the words read and whether it stopped on an error.
fn lex(text: &str, comments: bool, punct: bool) -> (Vec<String>, bool) {
    crate::detect::Shlex {
        whitespace: " \t\r\n",
        commenters: if comments { "#" } else { "" },
        punctuation: if punct { ";&|<>()" } else { "" },
    }
    .read(text)
}

// --- one agent's command line ----------------------------------------------------------------------------------------

/// An option's value: a word, a switch with none, or a value only known when the script runs.
#[derive(Debug, Clone, PartialEq)]
enum V {
    S(String),
    Yes,
    Unknown,
}

impl V {
    fn truthy(&self) -> bool {
        match self {
            V::S(s) => !s.is_empty(),
            V::Yes => true,
            V::Unknown => false,
        }
    }
}

fn truthy(v: &Option<V>) -> bool {
    v.as_ref().is_some_and(V::truthy)
}

/// {option: [values]} and the plain words (the first message, or Codex's subcommand and prompt).
fn options(kind: &str, args: &[Word]) -> (BTreeMap<String, Vec<V>>, Vec<Word>) {
    let (mut opts, mut words): (BTreeMap<String, Vec<V>>, Vec<Word>) = (BTreeMap::new(), vec![]);
    let mut i = 0;
    while i < args.len() {
        let Some(a) = args[i].as_deref().filter(|a| a.starts_with('-') && *a != "-") else {
            words.push(args[i].clone());
            i += 1;
            continue;
        };
        if a == "--" {
            words.extend(args[i + 1..].iter().cloned());
            break;
        } else if a.starts_with("--") && a.contains('=') {
            let (flag, value) = a.split_once('=').unwrap_or((a, ""));
            opts.entry(flag.to_string()).or_default().push(V::S(value.to_string()));
        } else if valued(kind, a) && kind == "claude" && has(SEVERAL, a) {
            let mut j = i + 1;
            while j < args.len() && !args[j].as_deref().unwrap_or("").starts_with('-') {
                opts.entry(a.to_string()).or_default().push(args[j].clone().map_or(V::Unknown, V::S));
                j += 1;
            }
            i = j;
            continue;
        } else if valued(kind, a) {
            let v = args.get(i + 1).cloned().flatten().map_or(V::Unknown, V::S);
            opts.entry(a.to_string()).or_default().push(v);
            i += 2;
            continue;
        } else {
            opts.entry(a.to_string()).or_default().push(V::Yes);
        }
        i += 1;
    }
    (opts, words)
}

fn unquote(v: &str) -> String {
    let c: Vec<char> = v.chars().collect();
    if c.len() > 1 && c[0] == c[c.len() - 1] && (c[0] == '"' || c[0] == '\'') {
        c[1..c.len() - 1].iter().collect()
    } else {
        v.to_string()
    }
}

/// A brief file's text. A relative path is read from the agent's folder (else the script's), as the agent would.
fn read_brief(path: &Word, say: &mut dyn FnMut(&str, &str), base: &str) -> String {
    let Some(path) = path else {
        say("brief-part", "its brief file is chosen when the script runs");
        return String::new();
    };
    let path = py_join(base, &expanduser(path));
    let read = || -> Option<String> {
        if std::fs::metadata(&path).ok()?.len() > MAX_BRIEF {
            return None;
        }
        Some(String::from_utf8(std::fs::read(&path).ok()?).ok()?.trim().to_string())
    };
    read().unwrap_or_else(|| {
        say("brief-part", &format!("{} couldn't be read", short(&path)));
        String::new()
    })
}

/// What didn't carry over: (kind, agent, detail).
pub(crate) type Note = (String, Option<String>, String);

/// A New team role from one Claude Code or Codex command line, and what didn't carry over.
/// `holds_task`: its first message is the team's task (`_task`), so it is left out of the brief.
/// `base`: the folder relative brief files are read from.
pub(crate) fn agent_role(
    env: &Env,
    name: &str,
    kind: &str,
    args: &[Word],
    env_names: &[String],
    holds_task: bool,
    base: &str,
) -> (Map<String, Value>, Vec<Note>) {
    let mut notes: Vec<Note> = vec![];
    let mut say = |k: &str, detail: &str| notes.push((k.to_string(), Some(name.to_string()), detail.to_string()));
    let args: Vec<Word> = if args.iter().any(|a| a.as_deref() == Some(ARRAY)) {
        say("extras", "options chosen when the script runs");
        args.iter().filter(|a| a.as_deref() != Some(ARRAY)).cloned().collect()
    } else {
        args.to_vec()
    };
    let (opts, mut words) = options(kind, &args);
    let first =
        |flags: &[&str]| -> Option<V> { flags.iter().find_map(|f| opts.get(*f).and_then(|v| v.first().cloned())) };
    let mut role = json!({"name": name, "agent": kind, "job": "", "checkout": "branch", "parent": null, "brief": "",
                          "model": null, "effort": null});

    let mut config: BTreeMap<String, String> = BTreeMap::new();
    let configs = opts.get("-c").into_iter().chain(opts.get("--config")).flatten();
    for c in configs {
        match c {
            V::S(c) if c.contains('=') => {
                let (key, value) = c.split_once('=').unwrap_or((c, ""));
                config.insert(key.trim().to_string(), unquote(value.trim()));
            }
            V::Unknown => say("extras", "a setting chosen when the script runs"),
            _ => {}
        }
    }
    // `a or b`: the option when it says something, else the setting of the same meaning (taken out of `config`).
    let mut or_config =
        |a: Option<V>, key: &str| -> Option<V> { if truthy(&a) { a } else { config.remove(key).map(V::S) } };
    let model = or_config(first(&["--model", "-m"]), "model");
    let effort = or_config(first(&["--effort"]), "model_reasoning_effort");
    if let Some(V::S(m)) = model.as_ref().filter(|m| m.truthy()) {
        if let Some(problem) = model_problem(kind, m, &env.known) {
            say("model", &problem);
        } else {
            role["model"] = json!(m);
        }
    }
    if let Some(V::S(e)) = effort.as_ref().filter(|e| e.truthy()) {
        if (env.effort_problem)(kind, e).is_none() {
            role["effort"] = json!(e);
        } else {
            say("model", &format!("effort {}", crate::pyrules::repr_str(e)));
        }
    }

    let mut wide: Vec<String> = vec![];
    let all = |k: &str| opts.get(k).cloned().unwrap_or_default();
    if kind == "claude" {
        let mode = first(&["--permission-mode"]);
        static SPLIT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s,]+").unwrap());
        let blocked: BTreeSet<String> = [all("--disallowedTools"), all("--disallowed-tools")]
            .concat()
            .iter()
            .filter_map(|v| if let V::S(s) = v { Some(s.as_str()).filter(|s| !s.is_empty()) } else { None })
            .flat_map(|s| SPLIT.split(s).map(String::from))
            .collect();
        let mode = match mode {
            Some(V::S(m)) => Some(m),
            _ => None,
        };
        if mode.as_deref() == Some("plan") || blocked.contains("Edit") || blocked.contains("Write") {
            role["checkout"] = json!("read-only");
        }
        if let Some(m) = mode.as_deref().filter(|m| has("auto acceptEdits bypassPermissions dontAsk", m)) {
            wide.push(format!("permission mode \"{m}\""));
        }
        if truthy(&first(&["--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"])) {
            wide.push("permission checks skipped".into());
        }
        if opts.contains_key("--allowedTools") || opts.contains_key("--allowed-tools") {
            wide.push("extra allowed tools".into());
        }
    } else {
        let sandbox = or_config(first(&["-s", "--sandbox"]), "sandbox_mode");
        match sandbox {
            Some(V::S(s)) if s == "read-only" => role["checkout"] = json!("read-only"),
            Some(V::S(s)) if s == "danger-full-access" => wide.push("full access outside its sandbox".into()),
            _ => {}
        }
        let approval = or_config(first(&["-a", "--ask-for-approval"]), "approval_policy");
        if approval == Some(V::S("never".into())) {
            wide.push("no approval asked".into());
        }
        if truthy(&first(&["--full-auto"])) {
            wide.push("\"--full-auto\"".into());
        }
        if truthy(&first(&["--yolo", "--dangerously-bypass-approvals-and-sandbox"])) {
            wide.push("approvals and sandbox turned off".into());
        }
        if config.keys().any(|k| k.starts_with("sandbox_")) {
            wide.push("its own sandbox settings".into());
        }
        if words.first().is_some_and(|w| matches!(w.as_deref(), Some("exec" | "e"))) {
            words.remove(0);
            say("exec", "");
        }
        config.retain(|k, _| !k.starts_with("sandbox_"));
    }
    if !wide.is_empty() {
        say("wide", &wide.join(", "));
    }

    let mut parts: Vec<String> = vec![];
    for f in ["--system-prompt-file", "--append-system-prompt-file"] {
        for v in all(f) {
            let path = if let V::S(p) = v { Some(p) } else { None };
            parts.push(read_brief(&path, &mut say, base));
        }
    }
    for f in ["--system-prompt", "--append-system-prompt"] {
        for v in all(f) {
            match v {
                V::S(text) => parts.push(text),
                _ => {
                    say("brief-part", "part chosen when the script runs");
                    parts.push(String::new());
                }
            }
        }
    }
    if words.iter().any(Option::is_none) {
        say("brief-part", "first message chosen when the script runs");
    }
    let message = words.iter().flatten().filter(|w| !w.is_empty()).cloned().collect::<Vec<_>>().join(" ");
    if holds_task {
        role["_task"] = json!(message);
    } else {
        parts.push(message);
    }
    role["brief"] = json!(parts.iter().filter(|p| !p.trim().is_empty()).cloned().collect::<Vec<_>>().join("\n\n"));
    let std = kind_of(name);
    role["job"] = json!(env.jobs.get(std).cloned().unwrap_or_default());
    if role["brief"] == "" {
        role["brief"] = json!((env.kind_brief)(std));
        say("standard-brief", &format!("{std} brief"));
    }

    if opts.contains_key("--settings") || opts.contains_key("--mcp-config") {
        say("extras", "its settings file and MCP servers");
    }
    if !config.is_empty() {
        say("extras", &config.keys().cloned().collect::<Vec<_>>().join(", "));
    }
    let other: Vec<&String> = opts.keys().filter(|f| !has(READ, f)).collect();
    if !other.is_empty() {
        say("extras", &other.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "));
    }
    let names: BTreeSet<&String> = env_names.iter().filter(|n| *n != "PATH").collect();
    if !names.is_empty() {
        say("extras", &names.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "));
    }
    let Value::Object(role) = role else { unreachable!() };
    (role, notes)
}

/// Why a model can't be carried over, or None. A full Claude model name is fine: New team's menu has aliases.
fn model_problem(kind: &str, model: &str, known: &BTreeMap<String, BTreeSet<String>>) -> Option<String> {
    static OK: LazyLock<Regex> = LazyLock::new(|| Regex::new(crate::launch::MODEL).unwrap());
    let ids = known.get(kind).cloned().unwrap_or_default();
    let quoted = crate::pyrules::repr_str(model);
    if !OK.is_match(model) {
        return Some(format!("model {quoted}"));
    }
    if (kind == "claude" && !ids.contains(model) && !model.starts_with("claude-"))
        || (kind == "codex" && !ids.is_empty() && !ids.contains(model))
    {
        return Some(format!("model {quoted} isn't on this Mac's list"));
    }
    None
}

// --- shell words and variables ---------------------------------------------------------------------------------------

/// The text with each outer $(...) swapped for a marker, and the texts inside them.
fn subs(text: &str) -> (String, Vec<String>) {
    let c: Vec<char> = text.chars().collect();
    let (mut out, mut inner, mut i) = (String::new(), vec![], 0);
    while let Some(j) = (i..c.len().saturating_sub(1)).find(|&j| c[j] == '$' && c[j + 1] == '(') {
        let (mut depth, mut k) = (1, j + 2);
        while k < c.len() && depth > 0 {
            depth += match c[k] {
                '(' => 1,
                ')' => -1,
                _ => 0,
            };
            k += 1;
        }
        out.extend(&c[i..j]);
        out.push_str(&format!("\0{}\0", inner.len()));
        inner.push(c[j + 2..k.saturating_sub(1).max(j + 2)].iter().collect());
        i = k;
    }
    out.extend(&c[i.min(c.len())..]);
    (out, inner)
}

static VAR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\$\{(\w+|BASH_SOURCE\[0\])(?:(:?[-+])([^}]*))?\}|\$(\w+)|\x00(\d+)\x00").unwrap());

/// The word with its variables filled in, or None when any of them is only known when the script runs.
fn expand(s: &str, env: &Vars, inner: &[String]) -> Word {
    let unknown = Cell::new(false);
    let out = VAR.replace_all(s, |m: &regex::Captures| {
        let v: Word = if let Some(n) = m.get(5) {
            command(&inner[n.as_str().parse::<usize>().unwrap_or(usize::MAX)], env)
        } else {
            let name = m.get(1).or_else(|| m.get(4)).map_or("", |x| x.as_str());
            let mut v = env.get(name).cloned().flatten();
            let (op, word) = (m.get(2).map_or("", |x| x.as_str()), m.get(3).map_or("", |x| x.as_str()));
            if op.ends_with('-') && !v.as_deref().is_some_and(|v| !v.is_empty()) {
                v = expand(word, env, inner);
            } else if op.ends_with('+') {
                v = if v.as_deref().is_some_and(|v| !v.is_empty()) {
                    expand(word, env, inner)
                } else {
                    Some(String::new())
                };
            }
            v
        };
        if v.is_none() {
            unknown.set(true);
        }
        v.unwrap_or_default()
    });
    if unknown.get() {
        None
    } else if out.starts_with('~') {
        Some(expanduser(&out))
    } else {
        Some(out.into_owned())
    }
}

/// What a few common $(...) give: command -v, which, dirname, basename, cd X && pwd. Otherwise None.
fn command(text: &str, env: &Vars) -> Word {
    let (masked, inner) = subs(text);
    let (split, bad) = lex(&masked, false, false);
    if bad {
        return None;
    }
    let w: Vec<Word> = split.iter().map(|x| expand(x, env, &inner)).collect();
    let is = |i: usize, s: &str| w.get(i).is_some_and(|x| x.as_deref() == Some(s));
    if (is(0, "command") && is(1, "-v") && w.len() == 3) || (is(0, "which") && w.len() == 2) {
        return w.last().cloned().flatten();
    }
    if w.len() == 2 && (is(0, "dirname") || is(0, "basename")) && w[1].as_deref().is_some_and(|x| !x.is_empty()) {
        let arg = w[1].as_deref().unwrap_or("").trim_end_matches('/');
        let arg = if arg.is_empty() { "/" } else { arg };
        return Some(if is(0, "dirname") { py_dirname(arg) } else { py_basename(arg) });
    }
    if w.len() >= 4 && is(0, "cd") && w[1].as_deref().is_some_and(|x| !x.is_empty()) && is(2, "&&") && is(3, "pwd") {
        return Some(py_normpath(w[1].as_deref().unwrap_or("")));
    }
    None
}

/// The shell words of one command, variables filled in (None where unknown), up to the first | ; && ) or >.
fn words(text: &str, env: &Vars) -> Vec<Word> {
    static ARR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\$\{\w+\[[@*]\]\}$").unwrap());
    static MARK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x00(\d+)\x00").unwrap());
    static ASSIGN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z_]\w*=").unwrap());
    let (masked, inner) = subs(text);
    let (tokens, _) = lex(&masked, false, true);
    let mut out = vec![];
    for t in tokens {
        if !t.is_empty() && t.chars().all(|c| ";&|<>()".contains(c)) {
            break;
        }
        if ARR.is_match(&t) {
            out.push(Some(ARRAY.to_string()));
            continue;
        }
        // shortcut: a word found in single quotes is taken as literal; a mixed-quoted word may expand wrongly.
        let restored =
            MARK.replace_all(&t, |m: &regex::Captures| format!("$({})", inner[m[1].parse::<usize>().unwrap_or(0)]));
        let mut word =
            if masked.contains(&format!("'{t}'")) { Some(restored.into_owned()) } else { expand(&t, env, &inner) };
        if word.is_none() && ASSIGN.is_match(&t) {
            // PATH="$PATH": the name still counts
            word = Some(format!("{}=", t.split('=').next().unwrap_or("")));
        }
        out.push(word);
    }
    out
}

/// (program, its arguments, the variables set in front of it) for a command line.
pub(crate) fn split(argv: &[Word]) -> (Word, Vec<Word>, Vars) {
    static ASSIGN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z_]\w*=").unwrap());
    let (mut env, mut i) = (Vars::new(), 0);
    while let Some(Some(a)) = argv.get(i) {
        if py_basename(a) == "env" {
            i += 1;
        } else if ASSIGN.is_match(a) {
            let (k, v) = a.split_once('=').unwrap_or((a, ""));
            env.insert(k.to_string(), Some(v.to_string()));
            i += 1;
        } else {
            break;
        }
    }
    let Some(Some(prog)) = argv.get(i) else { return (None, vec![], env) };
    let (prog, args) = (py_basename(prog), argv[i + 1..].to_vec());
    let c = args.iter().position(|a| a.as_deref() == Some("-c"));
    if matches!(prog.as_str(), "sh" | "bash" | "zsh") && c.is_some_and(|c| c + 1 < args.len()) {
        let script = args[c.unwrap_or(0) + 1].clone().unwrap_or_default();
        for line in splitlines(&script) {
            let ws = words(&line, &env); // the variables set in front of it fill in its words
            for (j, w) in ws.iter().enumerate() {
                if let Some(w) = w.as_deref().filter(|w| has(HERDR_KINDS, &py_basename(w))) {
                    return (Some(py_basename(w)), ws[j + 1..].to_vec(), env);
                }
            }
        }
    }
    (Some(prog), args, env)
}

// --- the two sources -------------------------------------------------------------------------------------------------

/// One agent as read from a source: (herdr name, program, args, env names, cwd, parent). The parent is "" when the
/// agent is known to report to no one and None when that is only decided at run time.
type Member = (Word, Word, Vec<Word>, Vec<String>, Word, Word);

/// The draft from the agents found, one per terminal or pane. The first agent known to report to no one has the team's
/// task: its first message. `base`: the script's folder.
fn team(env: &Env, members: Vec<Member>, project: Option<String>, mut notes: Vec<Note>, base: &str) -> Value {
    let (mut roles, mut taken): (Vec<Map<String, Value>>, BTreeSet<String>) = (vec![], BTreeSet::new());
    let mut cwds: Vec<(String, String)> = vec![];
    let mut task: Option<String> = None;
    // (role's herdr name, its parent as read)
    let mut links: Vec<(Word, Word)> = vec![];
    for (n, (name, prog, args, env_names, cwd, parent)) in members.into_iter().enumerate() {
        let n = n + 1;
        let shown = name.clone().filter(|n| !n.is_empty());
        let Some(prog) = prog else {
            notes.push(("unknown-program".into(), Some(shown.unwrap_or(format!("agent-{n}"))), String::new()));
            continue;
        };
        if prog != "claude" && prog != "codex" {
            if has(HERDR_KINDS, &prog) {
                // anything else (a shell, a dev server) isn't an agent
                notes.push(("other-cli".into(), Some(shown.unwrap_or(prog.clone())), prog));
            }
            continue;
        }
        let mut stem = slug(name.as_deref().unwrap_or(""));
        if stem.is_empty() {
            stem = format!("agent-{n}");
            notes.push(("numbered".into(), Some(stem.clone()), String::new()));
        }
        let (own, mut k) = (stem.clone(), 2);
        while taken.contains(&stem) {
            stem = format!("{own}-{k}");
            k += 1;
        }
        taken.insert(stem.clone());
        let leads = task.is_none() && parent.as_deref() == Some("");
        let folder = cwd.clone().filter(|c| !c.is_empty()).unwrap_or_else(|| base.to_string());
        let (mut role, said) = agent_role(env, &stem, &prog, &args, &env_names, leads, &folder);
        let mut asked = Value::Object(role.clone());
        asked["parent"] = Value::Null;
        if let Err(why) = (env.validate)(&asked) {
            // one role New team would refuse is left out, not the whole import
            notes.push(("refused".into(), Some(stem), why));
            continue;
        }
        let first = role.remove("_task");
        if leads {
            task = Some(first.and_then(|t| t.as_str().map(String::from)).unwrap_or_default());
        }
        links.push((name, parent));
        roles.push(role);
        notes.extend(said);
        cwds.push((stem, cwd.unwrap_or_default()));
    }
    if task.is_none() && !roles.is_empty() {
        task = Some(String::new());
        notes.push((
            "task".into(),
            None,
            "Which agent leads is only decided when the script runs, so the team's task is left empty here. Say what the team should work on."
                .to_string(),
        ));
    }
    let by_herdr: BTreeMap<String, String> = roles
        .iter()
        .zip(&links)
        .filter_map(|(r, (h, _))| Some((h.clone().filter(|h| !h.is_empty())?, r["name"].as_str()?.to_string())))
        .collect();
    for (r, (_, parent)) in roles.iter_mut().zip(&links) {
        let own = r["name"].as_str().unwrap_or("").to_string();
        match parent {
            None => notes.push(("parent".into(), Some(own), "decided when the script runs".into())),
            Some(p) if !p.is_empty() && by_herdr.get(p).is_some_and(|b| *b != own) => r["parent"] = json!(by_herdr[p]),
            Some(p) if !p.is_empty() => notes.push(("parent".into(), Some(own), format!("{p} isn't in this team"))),
            Some(_) => {}
        }
    }
    let places: BTreeMap<&str, (String, Option<String>)> =
        cwds.iter().filter(|(_, c)| !c.is_empty()).map(|(s, c)| (s.as_str(), (env.checkout)(c))).collect();
    let mut mains: Vec<(String, usize)> = vec![]; // in the order first seen, as Python's Counter
    for (s, _) in cwds.iter().filter(|(_, c)| !c.is_empty()) {
        let main = &places[s.as_str()].0;
        if main.is_empty() {
            continue;
        }
        match mains.iter_mut().find(|(m, _)| m == main) {
            Some(m) => m.1 += 1,
            None => mains.push((main.clone(), 1)),
        }
    }
    let project = project.filter(|p| !p.is_empty()).unwrap_or_else(|| {
        mains
            .iter()
            .fold(None::<&(String, usize)>, |best, m| if best.is_none_or(|b| m.1 > b.1) { Some(m) } else { best })
            .map_or(String::new(), |m| m.0.clone())
    });
    if mains.len() > 1 {
        let others: Vec<String> = mains.iter().filter(|(m, _)| *m != project).map(|(m, _)| short(m)).collect();
        notes.push((
            "folders".into(),
            None,
            format!(
                "The agents worked in {} folders. This team uses {}; the others ({}) are left as they are.",
                mains.len(),
                short(&project),
                others.join(", ")
            ),
        ));
    }
    for r in &roles {
        let own = r["name"].as_str().unwrap_or("");
        let Some((main, Some(branch))) = places.get(own) else { continue };
        let cwd = &cwds.iter().find(|(s, _)| s == own).map(|c| c.1.clone()).unwrap_or_default();
        if r["checkout"] == "branch" && crate::quiet::real(cwd) != *main {
            notes.push(("branch".into(), Some(own.into()), branch.clone()));
        }
    }
    json!({"project": project, "task": task.unwrap_or_default(), "roles": roles, "notes": grouped(&notes)})
}

fn count(n: usize) -> String {
    if n == 1 { "1 agent".into() } else { format!("{n} agents") }
}

/// What each kind of note says about the agents it lists, in the order they are shown.
fn sentence(kind: &str, n: usize, details: &[String]) -> Option<String> {
    let c = count(n);
    Some(match kind {
        "refused" => format!("{c} couldn't be carried over as New team would refuse them, so they are left out."),
        "unknown-program" => format!("The program {c} ran is chosen when the script runs, so they are left out."),
        "other-cli" => {
            format!("{c} ran a CLI Assieme doesn't start yet ({}), so they are left out.", details.join(", "))
        }
        "wide" => format!(
            "{c} ran with wider permissions ({}). They start with safe permissions instead: each edits its own copy and can't push.",
            details.join(", ")
        ),
        "exec" => format!("{c} ran once (codex exec) and stopped. Here they stay running and wait for work."),
        "branch" => format!(
            "{c} worked on their own branch. Here each gets a fresh branch of the project; the old branches stay as they are."
        ),
        "model" => format!("The model or thinking effort of {c} couldn't be carried over, so they get the default."),
        "standard-brief" => format!(
            "No brief was found for {c}, so they get the standard brief for their role. Check it before you start."
        ),
        "brief-part" => format!("Part of the brief of {c} couldn't be read, so it is missing here."),
        "parent" => format!("Who {c} reported to is unknown here, so they report to no one."),
        "numbered" => format!("The names of {c} are chosen when the script runs, so they are numbered here."),
        "extras" => format!("{c} set environment variables, settings or options that aren't carried over."),
        _ => return None,
    })
}

const KIND_ORDER: [&str; 12] = [
    "refused",
    "unknown-program",
    "other-cli",
    "wide",
    "exec",
    "branch",
    "model",
    "standard-brief",
    "brief-part",
    "parent",
    "numbered",
    "extras",
];
/// Their details go in the sentence, not after each name.
const IN_SENTENCE: [&str; 2] = ["wide", "other-cli"];

/// [{text, agents}] from (kind, agent, detail) records: one sentence per kind, then the team-wide ones.
fn grouped(records: &[Note]) -> Vec<Value> {
    let mut by_kind: BTreeMap<&str, Vec<(String, Vec<String>)>> = BTreeMap::new();
    let mut out = vec![];
    for (kind, agent, detail) in records {
        match agent {
            None => out.push(json!({"text": detail, "agents": []})),
            Some(agent) => {
                let list = by_kind.entry(kind).or_default();
                let at = list.iter().position(|(a, _)| a == agent).unwrap_or_else(|| {
                    list.push((agent.clone(), vec![]));
                    list.len() - 1
                });
                if !detail.is_empty() && !list[at].1.contains(detail) {
                    list[at].1.push(detail.clone());
                }
            }
        }
    }
    for kind in KIND_ORDER {
        let Some(agents) = by_kind.get(kind) else { continue };
        let mut details: Vec<String> = vec![];
        for d in agents.iter().flat_map(|(_, ds)| ds).flat_map(|part| part.split(", ")) {
            if !details.iter().any(|x| x == d) {
                details.push(d.to_string());
            }
        }
        let labels: Vec<String> = agents
            .iter()
            .map(|(a, ds)| {
                if IN_SENTENCE.contains(&kind) || ds.is_empty() {
                    a.clone()
                } else {
                    format!("{a} ({})", ds.join("; "))
                }
            })
            .collect();
        out.push(json!({"text": sentence(kind, agents.len(), &details), "agents": labels}));
    }
    out
}

/// [(session name, its workspaces)] for herdr's default session and its named ones.
fn sessions(cfg: &Path) -> Vec<(String, Vec<Value>)> {
    let mut found = vec![("default".to_string(), cfg.join("session.json"))];
    let folder = cfg.join("sessions");
    if folder.is_dir() {
        let mut names: Vec<String> = std::fs::read_dir(&folder)
            .map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default();
        names.sort();
        found.extend(names.into_iter().map(|n| (n.clone(), folder.join(n).join("session.json"))));
    }
    found
        .into_iter()
        .filter_map(|(name, path)| {
            let data = crate::pyrules::json_loads_bytes(&std::fs::read(path).ok()?).ok()?;
            let workspaces = data.as_object()?.get("workspaces").filter(|w| crate::pyrules::truthy(w))?;
            Some((name, workspaces.as_array().cloned().unwrap_or_default()))
        })
        .collect()
}

fn agent_panes(ws: &Value) -> Vec<&Value> {
    let tabs = ws["tabs"].as_array().into_iter().flatten();
    let panes = tabs.filter_map(|t| t["panes"].as_object()).flat_map(|p| p.values());
    panes
        .filter(|p| {
            crate::pyrules::truthy(&p["managed_agent_kind"]) || crate::pyrules::truthy(&p["agent_session"]["agent"])
        })
        .collect()
}

fn from_workspace(env: &Env, ws: &Value) -> Value {
    let members: Vec<Member> = agent_panes(ws)
        .into_iter()
        .map(|p| {
            let kind = p["managed_agent_kind"]
                .as_str()
                .filter(|k| !k.is_empty())
                .or_else(|| p["agent_session"]["agent"].as_str())
                .map(String::from);
            let name = p["agent_name"].as_str().filter(|n| !n.is_empty()).map(String::from).or_else(|| kind.clone());
            (name, kind, vec![], vec![], p["cwd"].as_str().map(String::from), Some(String::new()))
        })
        .collect();
    let said = (
        "saved".to_string(),
        None,
        "herdr doesn't save models, briefs or permissions, so each agent gets the default model, the standard brief for its role and its own branch. Check them before you start."
            .to_string(),
    );
    team(env, members, None, vec![said], "")
}

/// The agents a launch script starts with `hc run` or `herdr agent start`, helper functions followed one level.
fn from_script(env: &Env, text: &str, path: &str) -> Value {
    static CONT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\\\n").unwrap());
    static FUNC: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^(\s*)(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{\s*(#.*)?$").unwrap());
    let capped: String = text.chars().take((MAX_BRIEF * 4) as usize).collect();
    let raw = splitlines(&CONT.replace_all(&capped, " "));
    let (mut lines, mut buf): (Vec<String>, Vec<String>) = (vec![], vec![]);
    for line in raw {
        // a quoted text may run over several lines (sh -c '...')
        buf.push(line);
        if lex(&buf.join("\n"), true, false).1 && buf.len() < 50 {
            continue;
        }
        lines.push(buf.join("\n"));
        buf.clear();
    }
    lines.extend(buf);
    let path = crate::quiet::real(path);
    let mut vars: Vars =
        [("HOME", home()), ("0", path.clone()), ("BASH_SOURCE[0]", path.clone()), ("BASH_SOURCE", path.clone())]
            .into_iter()
            .map(|(k, v)| (k.to_string(), Some(v)))
            .collect();
    let at = r"(?:^\s*|[;&|()`]\s*|\b(?:then|do|else)\s+)"; // where a command starts, not a word in its arguments
    let starts = Regex::new(&format!(r"(?s){at}(hc\s+run|herdr\s+agent\s+start)\s(.*)")).unwrap();
    let (mut funcs, mut walk): (Vec<(String, Vec<String>)>, Vec<String>) = (vec![], vec![]);
    let mut i = 0;
    while i < lines.len() {
        // helper functions that start an agent are followed where they are called
        if let Some(m) = FUNC.captures(&lines[i]) {
            let indent = m[1].chars().count();
            let end = (i + 1..lines.len())
                .find(|&j| {
                    lines[j].trim() == "}" && lines[j].chars().count() - lines[j].trim_start().chars().count() <= indent
                })
                .unwrap_or(lines.len() - 1);
            let body = &lines[(i + 1).min(end)..end];
            if body.iter().any(|x| starts.is_match(x)) {
                funcs.push((m[2].to_string(), body.to_vec()));
                i = end + 1;
                continue;
            }
        }
        walk.push(lines[i].clone());
        i += 1;
    }
    let calls: Vec<(String, Regex)> = funcs
        .iter()
        .map(|(n, _)| (n.clone(), Regex::new(&format!(r"(?s){at}{}\s+(.*)$", regex::escape(n))).unwrap()))
        .collect();
    let mut members: Vec<Member> = vec![];
    fn run(
        text: &[String],
        vars: &mut Vars,
        depth: usize,
        starts: &Regex,
        funcs: &[(String, Vec<String>)],
        calls: &[(String, Regex)],
        members: &mut Vec<Member>,
    ) {
        for line in text {
            let stripped = line.trim();
            if stripped.is_empty() || stripped.starts_with('#') {
                continue;
            }
            assign(stripped, vars);
            if let Some(m) = starts.captures(line) {
                let tool = m[1].split_whitespace().next().unwrap_or("");
                members.push(script_member(tool, &words(&m[2], vars)));
                continue;
            }
            for ((_, body), (_, call)) in funcs.iter().zip(calls) {
                if let Some(c) = call.captures(stripped).filter(|_| depth < 3) {
                    let mut inner = vars.clone();
                    for (k, v) in words(&c[1], vars).into_iter().enumerate() {
                        inner.insert((k + 1).to_string(), v);
                    }
                    run(body, &mut inner, depth + 1, starts, funcs, calls, members);
                }
            }
        }
    }
    run(&walk, &mut vars, 0, &starts, &funcs, &calls, &mut members);
    let base = py_dirname(&path);
    let project = (env.checkout)(&base).0;
    let any_cwd = members.iter().any(|m| m.4.as_deref().is_some_and(|c| !c.is_empty()));
    team(env, members, if any_cwd { None } else { Some(project) }, vec![], &base)
}

fn assign(line: &str, env: &mut Vars) {
    static NAME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)^([A-Za-z_]\w*)=(.*)$").unwrap());
    static WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z_]\w*$|^-").unwrap());
    let (masked, inner) = subs(line);
    let (ws, bad) = lex(&masked, true, false);
    if bad {
        return;
    }
    let declare =
        ws.first().is_some_and(|w| matches!(w.as_str(), "export" | "local" | "readonly" | "declare" | "typeset"));
    for w in ws.iter().skip(usize::from(declare)) {
        if let Some(m) = NAME.captures(w) {
            let v = expand(&m[2], env, &inner);
            env.insert(m[1].to_string(), v);
        } else if !declare || !WORD.is_match(w) {
            break;
        }
    }
}

/// (herdr name, program, args, env, cwd, parent) for one `hc run ...` or `herdr agent start ...`.
fn script_member(tool: &str, ws: &[Word]) -> Member {
    let is = |i: usize, s: &str| ws.get(i).is_some_and(|w| w.as_deref() == Some(s));
    if tool == "herdr" {
        let (name, mut i, mut kind) = (ws.first().cloned().flatten(), 1, None);
        while i < ws.len() && !is(i, "--") {
            if is(i, "--kind") && i + 1 < ws.len() {
                kind = ws[i + 1].clone();
            }
            i += if ["--kind", "--pane", "--timeout"].iter().any(|f| is(i, f)) { 2 } else { 1 };
        }
        return (
            name,
            kind,
            ws.get(i + 1..).unwrap_or_default().to_vec(),
            vec![],
            Some(String::new()),
            Some(String::new()),
        );
    }
    let (mut name, mut cwd, mut parent, mut i) = (Some(String::new()), Some(String::new()), Some(String::new()), 0);
    while let Some(Some(w)) = ws.get(i) {
        if !(w.starts_with('-') || w.starts_with(ARRAY)) {
            break;
        }
        if w == ARRAY {
            // its options may hold --parent
            if parent.as_deref() == Some("") {
                parent = None;
            }
            i += 1;
            continue;
        }
        if w == "--" {
            i += 1;
            break;
        }
        if has("--name --cwd --on --label --parent --timeout", w) && i + 1 < ws.len() {
            let value = ws[i + 1].clone();
            match w.as_str() {
                "--name" => name = value,
                "--cwd" => cwd = value,
                "--parent" => parent = value,
                _ => {}
            }
            i += 2;
        } else {
            i += 1;
        }
    }
    let (prog, args, vars) = split(ws.get(i..).unwrap_or_default());
    (name, prog, args, vars.into_keys().collect(), cwd, parent)
}

// --- what the app calls ----------------------------------------------------------------------------------------------

/// Every herdr team on this Mac that can be imported: the workspaces herdr saved.
pub fn find() -> Value {
    find_in(&config_dir())
}

fn find_in(cfg: &Path) -> Value {
    let mut teams = vec![];
    for (session, workspaces) in sessions(cfg) {
        for ws in &workspaces {
            let panes = agent_panes(ws);
            if panes.is_empty() {
                continue;
            }
            let folder = ws["identity_cwd"].as_str().unwrap_or("");
            let label = ws["custom_name"].as_str().filter(|n| !n.is_empty()).map(String::from);
            let label = label.or_else(|| Some(py_basename(folder.trim_end_matches('/'))).filter(|b| !b.is_empty()));
            teams.push(json!({
                "id": format!("saved:{session}:{}", py_str(&ws["id"])),
                "label": label.unwrap_or("workspace".into()),
                "where": format!("Saved in herdr{}", if session == "default" { String::new() } else { format!(", session {session}") }),
                "agents": panes.len(), "running": false,
            }));
        }
    }
    json!({"teams": teams, "unreadable": null})
}

/// The New team draft for one source from `find`, or `script:<path>`.
pub fn draft(source: &str) -> Result<Value, BrainError> {
    draft_with(&Env::real(), source, &config_dir())
}

fn draft_with(env: &Env, source: &str, cfg: &Path) -> Result<Value, BrainError> {
    let (kind, rest) = source.split_once(':').unwrap_or((source, ""));
    match kind {
        "saved" => {
            let (session, ws_id) = rest.rsplit_once(':').unwrap_or(("", rest));
            for (name, workspaces) in sessions(cfg) {
                for ws in workspaces.iter().filter(|_| name == session) {
                    if py_str(&ws["id"]) == ws_id {
                        return Ok(from_workspace(env, ws));
                    }
                }
            }
            Err(BrainError::new("That herdr workspace isn't saved any more."))
        }
        "script" => {
            let text = std::fs::read(rest).ok().and_then(|b| String::from_utf8(b).ok());
            let text = text.ok_or_else(|| BrainError::new("That launch script couldn't be read."))?;
            Ok(from_script(env, &text, rest))
        }
        _ => Err(BrainError::new(format!("Unknown herdr source {}.", crate::pyrules::repr_str(source)))),
    }
}

#[cfg(test)]
mod tests;
