//! Port of `assieme/adapters.py`, owned by session A (see OWNERS.md).
//!
//! Adapters: turn one tool's local logs into Sessions. Each takes (root, since, show_text) and yields Sessions.
//! Wake markers (Assieme's `asm`, herdr `hc`, Claude Code Agent Teams, task notifications) are shared: any adapter
//! whose transport carries user-turn text runs it through [`wake_sender`].
#![allow(clippy::todo)]
use crate::detect;
use crate::model::{HUMAN, parse_ts};
use crate::prelude::*;
use crate::pyrules;
use regex::Regex;

/// A row Python would have failed on (AttributeError, TypeError): the log it is in is lost, never the whole read.
#[derive(Debug)]
pub(crate) struct Bad;

pub(crate) type Read<T> = Result<T, Bad>;

/// Deny rules: (rule, file).
type Rules = Vec<(String, String)>;

static WAKE_MARKERS: LazyLock<Vec<(Regex, &'static str, bool)>> = LazyLock::new(|| {
    [
        (r#"<asm from="(?:\S+ )?([\w./-]+)""#, "asm", true), // Assieme's runtime
        (r#"<hc from="(?:\S+ )?([\w./-]+)""#, "hc", true),   // <hc from="t2">, or "<term> <name>"
        (r#"<teammate-message teammate_id="([\w.-]+)""#, "team", true), // Claude Code Agent Teams
        (r"^\s*<task-notification>", "notify", false),       // a background subagent finished
    ]
    .into_iter()
    .map(|(rx, channel, named)| (Regex::new(rx).unwrap(), channel, named))
    .collect()
});

/// Codex: sandbox or approval refusal in a tool's output.
pub(crate) static CODEX_REFUSED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)sandbox_apply: Operation not permitted|rejected by (the )?(user|policy)|approval (was )?denied")
        .unwrap()
});

static ASSIGNMENT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\w+=").unwrap());

/// User-turn text -> (sender, channel). Unmarked text is a person typing.
pub fn wake_sender(text: &str) -> (String, String) {
    for (rx, channel, named) in WAKE_MARKERS.iter() {
        if let Some(m) = rx.captures(text) {
            let sender = if *named { m[1].to_string() } else { "subagent".into() };
            return (sender, channel.to_string());
        }
    }
    (HUMAN.into(), HUMAN.into())
}

/// The program a shell command runs. Never a leading `API_KEY=...`: that can be a secret.
pub fn program(command: &str) -> String {
    pyrules::split(command).into_iter().find(|w| !ASSIGNMENT.is_match(w)).map(basename).unwrap_or_default().into()
}

/// A refused call -> a short label. Only the program name unless show_text: arguments can hold secrets.
pub fn command_label(tool: &str, command: &str, show_text: bool) -> String {
    if command.is_empty() {
        return tool.into();
    }
    let shown = if show_text {
        pyrules::split(command).into_iter().take(3).collect::<Vec<_>>().join(" ").chars().take(60).collect()
    } else {
        program(command)
    };
    format!("{tool}: {shown}")
}

/// `os.path.basename`.
pub(crate) fn basename(p: &str) -> &str {
    p.rsplit('/').next().unwrap_or(p)
}

/// A file's text, read as Python's `errors='ignore'` does: bytes that are not UTF-8 are dropped.
pub(crate) fn decode_ignore(bytes: &[u8]) -> String {
    bytes.utf8_chunks().map(|c| c.valid()).collect()
}

/// Python's text-mode lines (universal newlines: `\n`, `\r` and `\r\n` end a line).
pub(crate) fn text_lines(text: &str) -> Vec<&str> {
    let mut out = vec![];
    let (b, mut start, mut i) = (text.as_bytes(), 0, 0);
    while i < b.len() {
        if b[i] == b'\n' || b[i] == b'\r' {
            let end = if b[i] == b'\r' && b.get(i + 1) == Some(&b'\n') { i + 2 } else { i + 1 };
            out.push(&text[start..end]);
            (start, i) = (end, end);
        } else {
            i += 1;
        }
    }
    if start < b.len() {
        out.push(&text[start..]);
    }
    out
}

/// `json.loads(line)` of a line that may carry its line end: Python skips surrounding whitespace.
pub(crate) fn loads(line: &str) -> Option<Value> {
    pyrules::json_loads(line.trim_matches([' ', '\t', '\n', '\r'])).ok()
}

/// The JSON objects of a JSONL file, skipping lines that are not objects.
pub fn rows(path: &Path) -> Vec<Map<String, Value>> {
    try_rows(path).unwrap_or_default()
}

pub(crate) fn try_rows(path: &Path) -> std::io::Result<Vec<Map<String, Value>>> {
    let text = decode_ignore(&std::fs::read(path)?);
    Ok(text_lines(&text).into_iter().filter_map(loads).filter_map(into_object).collect())
}

pub(crate) fn into_object(v: Value) -> Option<Map<String, Value>> {
    match v {
        Value::Object(o) => Some(o),
        _ => None,
    }
}

/// `x.get(key) or {}` where x must be a dict when it is truthy.
pub(crate) fn or_obj(v: Option<&Value>) -> Read<Option<&Map<String, Value>>> {
    match v {
        Some(Value::Object(o)) => Ok(Some(o)),
        Some(v) if pyrules::truthy(v) => Err(Bad),
        _ => Ok(None),
    }
}

/// `d.get(key)` of a dict that may be missing.
pub(crate) fn get<'a>(d: Option<&'a Map<String, Value>>, key: &str) -> Option<&'a Value> {
    d.and_then(|d| d.get(key))
}

pub(crate) fn get_str<'a>(d: Option<&'a Map<String, Value>>, key: &str) -> Option<&'a str> {
    get(d, key).and_then(Value::as_str)
}

/// `bool(d.get(key))`.
pub(crate) fn get_true(d: Option<&Map<String, Value>>, key: &str) -> bool {
    get(d, key).is_some_and(pyrules::truthy)
}

/// A count as Python adds it: missing is 0; None or text fails the sum.
pub(crate) fn int(v: Option<&Value>) -> Read<i64> {
    match v {
        None => Ok(0),
        Some(Value::Bool(b)) => Ok(*b as i64),
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).ok_or(Bad),
        Some(_) => Err(Bad),
    }
}

/// `int(x or 0)`: what Gemini and OpenCode counts go through.
fn int_or_zero(v: Option<&Value>) -> i64 {
    match v {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0),
        Some(Value::Bool(b)) => *b as i64,
        Some(Value::String(s)) => s.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

/// Python's `str()` of a JSON value.
pub(crate) fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
            (Some(i), _, _) => i.to_string(),
            (_, Some(u), _) => u.to_string(),
            (_, _, Some(f)) if f.fract() == 0.0 && f.abs() < 1e16 => format!("{f:.1}"),
            _ => n.to_string(),
        },
        other => py_dumps(other),
    }
}

/// `json.dumps(v)`: `", "` and `": "` between items, non-ASCII escaped.
pub(crate) fn py_dumps(v: &Value) -> String {
    use serde::Serialize;
    use std::io::{Result, Write};
    struct Py;
    impl serde_json::ser::Formatter for Py {
        fn begin_array_value<W: ?Sized + Write>(&mut self, w: &mut W, first: bool) -> Result<()> {
            w.write_all(if first { b"" } else { b", " })
        }
        fn begin_object_key<W: ?Sized + Write>(&mut self, w: &mut W, first: bool) -> Result<()> {
            w.write_all(if first { b"" } else { b", " })
        }
        fn begin_object_value<W: ?Sized + Write>(&mut self, w: &mut W) -> Result<()> {
            w.write_all(b": ")
        }
        fn write_string_fragment<W: ?Sized + Write>(&mut self, w: &mut W, s: &str) -> Result<()> {
            for c in s.chars() {
                match c {
                    ' '..='~' => write!(w, "{c}")?,
                    c => c.encode_utf16(&mut [0; 2]).iter().try_for_each(|u| write!(w, "\\u{u:04x}"))?,
                }
            }
            Ok(())
        }
    }
    let mut out = vec![];
    let _ = v.serialize(&mut serde_json::Serializer::with_formatter(&mut out, Py));
    String::from_utf8(out).unwrap_or_default()
}

/// User content -> its typed text, or None when it only carries tool results.
pub fn text_of(content: &Value) -> Option<String> {
    try_text_of(content).unwrap_or(None)
}

/// `_text_of`, failing where Python's join would (a text block whose text is not a string).
pub(crate) fn try_text_of(content: &Value) -> Read<Option<String>> {
    match content {
        Value::String(s) => Ok(Some(s.clone())),
        Value::Array(blocks) => texts(blocks, "\n", true),
        _ => Ok(None),
    }
}

/// `sep.join(b.get('text', '') for b in blocks if isinstance(b, dict))`, only `text` blocks when `typed`; None when
/// no block counts. Fails where Python's join would: a text that isn't a string.
pub(crate) fn texts(blocks: &[Value], sep: &str, typed: bool) -> Read<Option<String>> {
    let blocks = blocks.iter().filter_map(Value::as_object);
    let texts = blocks
        .filter(|b| !typed || b.get("type").and_then(Value::as_str) == Some("text"))
        .map(|b| match b.get("text") {
            None => Ok(""),
            Some(Value::String(s)) => Ok(s.as_str()),
            Some(_) => Err(Bad),
        })
        .collect::<Read<Vec<_>>>()?;
    Ok((!texts.is_empty()).then(|| texts.join(sep)))
}

/// Codex's message text: `sep.join(b.get('text', '') for b in content or [] if isinstance(b, dict))`.
pub(crate) fn joined(content: Option<&Value>, sep: &str) -> Read<String> {
    match content {
        Some(Value::Array(a)) => Ok(texts(a, sep, false)?.unwrap_or_default()),
        Some(v @ (Value::Number(_) | Value::Bool(true))) if pyrules::truthy(v) => Err(Bad), // not iterable
        _ => Ok(String::new()), // falsy, or a text or dict: iterating it gives no dicts
    }
}

/// F10: where a metrics source writes, one row per reading.
pub fn box_path() -> PathBuf {
    let base = std::env::var("XDG_STATE_HOME").ok().filter(|s| !s.is_empty());
    let base = base.map(PathBuf::from).unwrap_or_else(|| home().join(".local/state"));
    base.join("assieme/boxes.jsonl")
}

pub(crate) fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
}

/// `{"ts", "box", "cpu", "rate", "usd_per_hour"}` rows since then; `"box": "local"` is the baseline. `ts` is
/// returned as a number of seconds since 1970, `box` as text.
pub fn box_metrics(path: &Path, since: Timestamp) -> Vec<Value> {
    let Ok(rows) = try_rows(path) else { return vec![] };
    let mut out = vec![];
    for mut r in rows {
        let Some(ts) = r.get("ts").and_then(parse_ts) else { continue };
        if ts < since || !r.get("box").is_some_and(pyrules::truthy) {
            continue;
        }
        let name = py_str(&r["box"]);
        r.insert("ts".into(), seconds(ts).into());
        r.insert("box".into(), name.into());
        out.push(Value::Object(r));
    }
    out
}

/// `(a - b).total_seconds()`.
pub(crate) fn secs(a: Timestamp, b: Timestamp) -> f64 {
    (a.as_nanosecond() - b.as_nanosecond()) as f64 / 1e9
}

/// A time as seconds since 1970, as Python's `timestamp()`.
pub(crate) fn seconds(t: Timestamp) -> f64 {
    t.as_nanosecond() as f64 / 1e9
}

/// A Codex tool's output: a string, or a list of text blocks.
pub(crate) fn output_text(out: Option<&Value>) -> Read<String> {
    match out {
        Some(Value::Array(_)) => joined(out, "\n"),
        Some(Value::String(s)) => Ok(s.clone()),
        other => Ok(py_dumps(other.unwrap_or(&Value::Null))),
    }
}

/// Project folder names -> short agent names. `-Users-me-src-team-a`, `-Users-me-src-team-b` -> `a`, `b`. Folders
/// are grouped by their first two parts below the home folder, so one unrelated project doesn't stop a team's names
/// from shortening. A project alone in its group keeps its last two parts.
pub(crate) fn strip_common_prefix(names: &[String]) -> BTreeMap<String, String> {
    static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^A-Za-z0-9]").unwrap());
    let home = format!("{}-", NOT_ALNUM.replace_all(&home().to_string_lossy(), "-"));
    let base = |n: &String| -> String {
        let cut = n.strip_prefix(home.as_str()).unwrap_or(n).trim_matches('-');
        if cut.is_empty() { n.clone() } else { cut.into() }
    };
    let mut groups: Vec<(Vec<String>, Vec<&String>)> = vec![];
    for n in names {
        let key: Vec<String> = base(n).split('-').take(2).map(String::from).collect();
        match groups.iter_mut().find(|(k, _)| *k == key) {
            Some((_, g)) => g.push(n),
            None => groups.push((key, vec![n])),
        }
    }
    let mut out = BTreeMap::new();
    for (_, group) in groups {
        if group.len() == 1 {
            let b = base(group[0]);
            let parts: Vec<&str> = b.split('-').collect();
            out.insert(group[0].clone(), parts[parts.len().saturating_sub(2)..].join("-"));
            continue;
        }
        let bases: Vec<Vec<char>> = group.iter().map(|n| base(n).chars().collect()).collect();
        let common = (0..).take_while(|&i| bases.iter().all(|b| i < b.len() && b[i] == bases[0][i])).count();
        let cut = bases[0][..common].iter().rposition(|&c| c == '-').map_or(0, |i| i + 1);
        for (n, b) in group.iter().zip(&bases) {
            let short: String = b[cut.min(b.len())..].iter().collect();
            out.insert((*n).clone(), if short.is_empty() { b.iter().collect() } else { short });
        }
    }
    out
}

fn mtime(p: &Path) -> Option<f64> {
    let m = std::fs::metadata(p).ok()?.modified().ok()?;
    let d = m.duration_since(std::time::UNIX_EPOCH).ok()?;
    Some(d.as_secs_f64())
}

/// A path's file time in seconds since 1970; a path removed since it was listed reads as the oldest.
pub(crate) fn mtime_or_zero(p: &Path) -> f64 {
    mtime(p).unwrap_or(0.0)
}

fn recent(paths: Vec<PathBuf>, since: Timestamp) -> Vec<PathBuf> {
    let cutoff = seconds(since);
    paths.into_iter().filter(|p| mtime(p).is_some_and(|t| t >= cutoff)).collect()
}

/// `glob(os.path.join(root, '**', name), recursive=True)`: any depth, the top included, dot entries skipped.
pub(crate) fn glob_deep(root: &Path) -> Vec<PathBuf> {
    let options = glob::MatchOptions { require_literal_leading_dot: true, ..Default::default() };
    let pattern = root.join("**/*.jsonl");
    glob::glob_with(&pattern.to_string_lossy(), options).map(|p| p.flatten().collect()).unwrap_or_default()
}

/// `~/.claude/projects/<project>/<session>.jsonl`, plus `<session>/subagents/agent-*.jsonl`.
///
/// Deny rules explain refusals. They come from each session's cwd (`.claude/settings*.json`), `user_settings`,
/// `cwd_rules` (running agents' `--settings` files) and `extra_rules` (`--rules` files).
pub fn claude_code(
    root: &Path,
    since: Timestamp,
    show_text: bool,
    user_settings: Option<&Path>,
    cwd_rules: Option<&RulesFor>,
    extra_rules: &[(String, String)],
) -> Vec<Session> {
    let user_settings = user_settings.map(PathBuf::from).unwrap_or_else(|| home().join(".claude/settings.json"));
    let cache: Mutex<BTreeMap<Option<String>, Rules>> = Mutex::default();
    let rules_for = |cwd: Option<&str>| -> Vec<(String, String)> {
        let mut cache = cache.lock().unwrap();
        cache
            .entry(cwd.map(String::from))
            .or_insert_with(|| {
                let mut rules = crate::rules::load(cwd, &user_settings);
                if let (Some(more), Some(cwd)) = (cwd_rules, cwd) {
                    rules.extend(more(cwd));
                }
                rules.extend(extra_rules.iter().cloned());
                rules
            })
            .clone()
    };
    let mut paths = pyrules::glob(&root.join("*/*.jsonl"));
    paths.extend(pyrules::glob(&root.join("*/*/subagents/*.jsonl")));
    let paths = recent(paths, since);
    let relative = |p: &Path| -> Vec<String> {
        p.strip_prefix(root).unwrap_or(p).iter().map(|s| s.to_string_lossy().into_owned()).collect()
    };
    let projects: BTreeSet<String> = paths.iter().map(|p| relative(p)[0].clone()).collect();
    let names = strip_common_prefix(&projects.into_iter().collect::<Vec<_>>());
    let mut out = vec![];
    for path in &paths {
        let parts = relative(path);
        let mut agent = names[&parts[0]].clone();
        let mut parent = None;
        if parts.len() == 4 {
            // a subagent: name it after its parent and type
            let meta_path = PathBuf::from(format!("{}.meta.json", path.to_string_lossy().trim_end_matches(".jsonl")));
            let meta = std::fs::read(&meta_path).ok().and_then(|b| loads(&decode_ignore(&b)));
            let kind = meta.as_ref().and_then(|m| m.get("agentType")).filter(|v| pyrules::truthy(v));
            let kind = kind.map_or("subagent".into(), py_str);
            parent = Some(agent.clone());
            agent = format!("{agent}/{kind}");
        }
        let Ok(s) = claude_session(path, &agent, parent.as_deref(), since, show_text, &rules_for) else { continue };
        if s.calls > 0 {
            out.push(s);
        }
    }
    out
}

/// One tool call of a Claude Code session: (label, name, command, file path).
type ClaudeTool = (String, Option<String>, Option<String>, Option<String>);

fn claude_session(
    path: &Path,
    agent: &str,
    parent: Option<&str>,
    since: Timestamp,
    show_text: bool,
    rules_for: &dyn Fn(Option<&str>) -> Rules,
) -> Read<Session> {
    let mut s = Session { agent: agent.into(), source: "claude-code".into(), ..Session::default() };
    let rows = try_rows(path).map_err(|_| Bad)?;
    let (mut seen, mut tools) = (BTreeSet::<String>::new(), BTreeMap::<String, ClaudeTool>::new());
    let (mut cwd, mut jobs, mut refused_at) = (None::<String>, BTreeMap::<String, usize>::new(), None);
    for o in &rows {
        if let Some(c) = o.get("cwd").filter(|v| pyrules::truthy(v)) {
            cwd = Some(py_str(c));
        }
        s.cwd = cwd.clone();
        let branch = o.get("gitBranch").and_then(Value::as_str);
        let Some(ts) = o.get("timestamp").and_then(parse_ts) else { continue };
        if ts < since {
            continue;
        }
        let msg = or_obj(o.get("message"))?;
        let content = get(msg, "content");
        let kind = o.get("type").and_then(Value::as_str);
        let blocks: Vec<&Map<String, Value>> = match content {
            Some(Value::Array(a)) => a.iter().filter_map(Value::as_object).collect(),
            _ => vec![],
        };
        if kind == Some("user") && !o.get("isMeta").is_some_and(pyrules::truthy) {
            if let Some(text) = try_text_of(content.unwrap_or(&Value::Null))? {
                let (mut sender, mut channel) = wake_sender(&text);
                if let (Some(p), true) = (parent, s.turns.is_empty()) {
                    (sender, channel) = (p.into(), "subagent".into());
                }
                s.wake(&sender, &channel, &text, show_text, Some(ts), branch);
            }
            for b in &blocks {
                if b.get("type").and_then(Value::as_str) != Some("tool_result") || !get_true(Some(b), "is_error") {
                    continue;
                }
                let out = match b.get("content") {
                    Some(Value::String(t)) => t.clone(),
                    other => py_dumps(other.unwrap_or(&Value::Null)),
                };
                let id = id_of(b.get("tool_use_id"));
                let (label, name, cmd, fpath) =
                    tools.get(&id).cloned().unwrap_or_else(|| ("?".into(), None, None, None));
                if crate::rules::REFUSED.is_match(&out) {
                    let (why, note) = crate::rules::explain(
                        &out,
                        name.as_deref().unwrap_or("None"),
                        cmd.as_deref().unwrap_or(""),
                        &rules_for(cwd.as_deref()),
                        fpath.as_deref(),
                    );
                    s.refusals.add((label, why.clone()), 1);
                    refused_at = Some((why.clone(), ts));
                    if !note.is_empty() {
                        s.rule_notes.insert(why, note);
                    }
                } else if name.as_deref() == Some("Bash")
                    && let Some(sig) = detect::failure_sig(&out)
                {
                    s.failures.push((ts, sig, s.turns.len() as i64, label));
                }
            }
            for b in &blocks {
                if b.get("type").and_then(Value::as_str) == Some("tool_result")
                    && let Some(i) = jobs.remove(&id_of(b.get("tool_use_id")))
                {
                    s.jobs[i].end = Some(ts);
                }
            }
        } else if kind == Some("assistant") && get_true(msg, "usage") {
            if let Some((why, at)) = refused_at.take() {
                s.refusal_seconds.add(why, secs(ts, at) as i64);
            }
            let mid = id_of(get(msg, "id"));
            if seen.insert(mid) {
                let u = or_obj(get(msg, "usage"))?;
                let input = int(get(u, "input_tokens"))?
                    + int(get(u, "cache_read_input_tokens"))?
                    + int(get(u, "cache_creation_input_tokens"))?;
                s.call(input, int(get(u, "output_tokens"))?, Some(ts));
            }
            for b in &blocks {
                if b.get("type").and_then(Value::as_str) != Some("tool_use") {
                    continue;
                }
                let inp = or_obj(b.get("input"))?;
                let name = b.get("name").and_then(Value::as_str);
                let command = get_str(inp, "command");
                let id = id_of(b.get("id"));
                if let Some(job) = detect::note_call(&mut s, name, command, ts, &id, branch) {
                    jobs.insert(id.clone(), job);
                }
                let label = command_label(name.unwrap_or("None"), command.unwrap_or(""), show_text);
                let fpath = get_str(inp, "file_path").map(String::from);
                tools.insert(id, (label, name.map(String::from), command.map(String::from), fpath));
            }
        }
    }
    s.cwd = cwd;
    Ok(s)
}

/// A tool call's id as a key: None and non-text ids read as "".
pub(crate) fn id_of(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => py_str(other),
    }
}

/// `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Spawned subagents name their parent thread.
pub fn codex(root: &Path, since: Timestamp, show_text: bool) -> Vec<Session> {
    let mut sessions: Vec<(String, Option<String>, Session)> = vec![];
    for path in recent(glob_deep(root), since) {
        let Ok((thread, parent, s)) = codex_session(&path, since, show_text) else { continue };
        let key = thread.unwrap_or_else(|| path.to_string_lossy().into_owned());
        match sessions.iter_mut().find(|(k, _, _)| *k == key) {
            Some(slot) => *slot = (key, parent, s),
            None => sessions.push((key, parent, s)),
        }
    }
    let agents: BTreeMap<String, String> = sessions.iter().map(|(k, _, s)| (k.clone(), s.agent.clone())).collect();
    let mut out = vec![];
    for (_, parent, mut s) in sessions {
        if let Some(parent) = parent.filter(|p| !p.is_empty())
            && s.turns.first().is_some_and(|t| t.channel == "spawn")
        {
            s.turns[0].sender = agents.get(&parent).cloned().unwrap_or_else(|| "codex:parent".into());
        }
        if s.calls > 0 {
            out.push(s);
        }
    }
    out
}

/// A Codex function call's command: `cmd` or `command` from its arguments, a list joined by spaces.
pub(crate) fn codex_command(p: &Map<String, Value>) -> Read<String> {
    let raw = [p.get("arguments"), p.get("input")].into_iter().flatten().find(|v| pyrules::truthy(v));
    let args = match raw {
        Some(Value::String(s)) => pyrules::json_loads(s).unwrap_or_else(|_| Value::String(s.clone())),
        Some(v) => v.clone(),
        None => Value::String(String::new()),
    };
    let cmd = match &args {
        Value::Object(o) => {
            [o.get("cmd"), o.get("command")].into_iter().flatten().find(|v| pyrules::truthy(v)).cloned()
        }
        other => Some(other.clone()),
    };
    Ok(match cmd {
        Some(Value::Array(a)) => a.iter().map(py_str).collect::<Vec<_>>().join(" "),
        Some(Value::String(s)) => s,
        _ => String::new(),
    })
}

const INJECTED: [&str; 3] = ["<environment_context>", "# AGENTS.md", "<user_instructions>"];

/// Injected context, not a wake.
pub(crate) fn injected(text: &str) -> bool {
    let text = text.trim_start_matches(pyrules::isspace);
    INJECTED.iter().any(|p| text.starts_with(p))
}

fn codex_session(path: &Path, since: Timestamp, show_text: bool) -> Read<(Option<String>, Option<String>, Session)> {
    let mut s = Session { agent: "codex:?".into(), source: "codex".into(), ..Session::default() };
    let (mut thread, mut parent, mut branch) = (None, None, None::<String>);
    let (mut last_total, mut labels, mut jobs) = (None::<Value>, BTreeMap::<String, String>::new(), BTreeMap::new());
    for o in try_rows(path).map_err(|_| Bad)? {
        let payload = or_obj(o.get("payload")); // a payload that isn't a dict fails where it is first read
        if o.get("type").and_then(Value::as_str) == Some("session_meta") {
            let p = payload?;
            thread = get(p, "id").filter(|v| !v.is_null()).map(py_str);
            let src = get(p, "source").and_then(Value::as_object);
            let sub = get(src, "subagent").and_then(Value::as_object);
            let spawn = get(sub, "thread_spawn").and_then(Value::as_object);
            parent = get(spawn, "parent_thread_id").filter(|v| !v.is_null()).map(py_str);
            let kind = if spawn.is_some_and(|m| !m.is_empty()) {
                "subagent".into()
            } else {
                get(sub, "other").filter(|v| pyrules::truthy(v)).map(py_str).unwrap_or_default()
            };
            let cwd = get(p, "cwd").filter(|v| pyrules::truthy(v)).map(py_str);
            let folder = cwd.as_deref().map(basename).filter(|b| !b.is_empty()).unwrap_or("?");
            s.agent = format!("codex:{folder}") + &if kind.is_empty() { String::new() } else { format!("/{kind}") };
            s.cwd = get(p, "cwd").and_then(Value::as_str).map(String::from);
            branch = or_obj(get(p, "git"))?.and_then(|g| g.get("branch")).and_then(Value::as_str).map(String::from);
            continue;
        }
        let Some(ts) = o.get("timestamp").and_then(parse_ts) else { continue };
        if ts < since {
            continue;
        }
        let p = payload?;
        let kind = get(p, "type").and_then(Value::as_str);
        let role = get(p, "role").and_then(Value::as_str);
        match kind {
            Some("message") if role == Some("user") => {
                let text = joined(get(p, "content"), "\n")?;
                if injected(&text) {
                    continue;
                }
                let (mut sender, mut channel) = wake_sender(&text);
                if (parent.is_some() || s.agent.contains("subagent")) && s.turns.is_empty() {
                    (sender, channel) = ("codex:parent".into(), "spawn".into());
                }
                s.wake(&sender, &channel, &text, show_text, Some(ts), branch.as_deref());
            }
            Some("token_count") if get(p, "info").is_some_and(Value::is_object) => {
                let info = get(p, "info").and_then(Value::as_object);
                let total = or_obj(get(info, "total_token_usage"))?.and_then(|t| t.get("total_tokens")).cloned();
                let last = or_obj(get(info, "last_token_usage"))?;
                if total != last_total {
                    // the same count is re-emitted between calls; input already includes cached
                    last_total = total;
                    s.call(int(get(last, "input_tokens"))?, int(get(last, "output_tokens"))?, Some(ts));
                }
            }
            Some("function_call" | "custom_tool_call") => {
                let p = p.ok_or(Bad)?;
                let cmd = codex_command(p)?;
                let inner = detect::commands_of(Some(&cmd));
                let name = p.get("name").filter(|v| pyrules::truthy(v)).map_or("?".into(), py_str);
                let id = id_of(p.get("call_id"));
                labels.insert(id.clone(), command_label(&name, inner.first().unwrap_or(&cmd), show_text));
                let tool = p.get("name").and_then(Value::as_str);
                if let Some(job) = detect::note_call(&mut s, tool, Some(&cmd), ts, &id, branch.as_deref()) {
                    jobs.insert(id, job);
                }
            }
            Some("function_call_output" | "custom_tool_call_output") => {
                let out = output_text(get(p, "output"))?;
                let id = id_of(get(p, "call_id"));
                if let Some(i) = jobs.remove(&id) {
                    s.jobs[i].end = Some(ts);
                }
                let label = labels.get(&id).cloned().unwrap_or_else(|| "?".into());
                if CODEX_REFUSED.is_match(&out) {
                    s.refusals.add((label, "Codex sandbox or approval".into()), 1);
                } else if detect::codex_failed(&out)
                    && let Some(sig) = detect::failure_sig(&out)
                {
                    s.failures.push((ts, sig, s.turns.len() as i64, label));
                }
            }
            _ => {}
        }
    }
    Ok((thread, parent, s))
}

/// A Gemini message's text: a string, or a list of parts with `text`.
fn gemini_text(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(parts)) => {
            parts.iter().filter_map(|p| p.get("text").and_then(Value::as_str)).collect::<Vec<_>>().join("\n")
        }
        _ => String::new(),
    }
}

/// `~/.gemini/tmp/<project>/chats/session-*.jsonl`: a header line, then messages. A message is written again when
/// it changes (its tokens arrive after it), so the last copy of each id counts. `projects.json` maps folders.
pub fn gemini(root: &Path, since: Timestamp, show_text: bool) -> Vec<Session> {
    let projects = std::fs::read(root.join("projects.json")).ok().and_then(|b| loads(&decode_ignore(&b)));
    let mut folders = BTreeMap::new();
    if let Some(Value::Object(ps)) = projects.as_ref().and_then(|p| p.get("projects")) {
        for (path, short) in ps {
            folders.insert(py_str(short), path.clone());
        }
    }
    let mut out = vec![];
    for path in recent(pyrules::glob(&root.join("tmp/*/chats/*.jsonl")), since) {
        let project = path.parent().and_then(Path::parent).and_then(Path::file_name);
        let cwd = project.and_then(|p| folders.get(&*p.to_string_lossy())).cloned();
        let folder = cwd.as_deref().map(basename).filter(|b| !b.is_empty()).unwrap_or("?");
        let mut s = Session { agent: format!("gemini:{folder}"), source: "gemini".into(), cwd, ..Session::default() };
        let mut messages: Vec<(String, Map<String, Value>)> = vec![];
        for o in rows(&path) {
            if let (Some(id), true) = (o.get("id"), o.contains_key("type")) {
                let id = py_dumps(id);
                match messages.iter_mut().find(|(k, _)| *k == id) {
                    Some(slot) => slot.1 = o,
                    None => messages.push((id, o)),
                }
            }
        }
        for (_, m) in &messages {
            let Some(ts) = m.get("timestamp").and_then(parse_ts) else { continue };
            if ts < since {
                continue;
            }
            match m["type"].as_str() {
                Some("user") => {
                    let text = gemini_text(m.get("content"));
                    let (sender, channel) = wake_sender(&text);
                    s.wake(&sender, &channel, &text, show_text, Some(ts), None);
                }
                Some("gemini") if m.get("tokens").is_some_and(Value::is_object) => {
                    let t = &m["tokens"];
                    let output = int_or_zero(t.get("output")) + int_or_zero(t.get("thoughts"));
                    s.call(int_or_zero(t.get("input")), output, Some(ts));
                }
                _ => {}
            }
        }
        if s.calls > 0 {
            out.push(s);
        }
    }
    out
}

/// OpenCode's SQLite database, read-only, and only its session and message tables (others hold sign-ins).
pub fn opencode(db: &Path, since: Timestamp, show_text: bool) -> Vec<Session> {
    opencode_read(db, since, show_text).unwrap_or_default()
}

fn opencode_read(db: &Path, since: Timestamp, show_text: bool) -> rusqlite::Result<Vec<Session>> {
    use rusqlite::{Connection, OpenFlags};
    let con = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    con.busy_timeout(std::time::Duration::from_secs(2))?;
    let cutoff = (seconds(since) * 1000.0) as i64;
    let sessions: Vec<(String, Option<String>, Option<String>)> = con
        .prepare("SELECT id, directory, parent_id FROM session WHERE time_updated >= ?")?
        .query_map([cutoff], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let mut out = vec![];
    for (sid, cwd, parent) in sessions {
        let folder = cwd.as_deref().map(basename).filter(|b| !b.is_empty()).unwrap_or("?");
        let agent = format!("opencode:{folder}") + if parent.is_some() { "/subagent" } else { "" };
        let mut s = Session { agent, source: "opencode".into(), cwd: cwd.clone(), ..Session::default() };
        let rows: Vec<(String, i64, String)> = con
            .prepare(
                "SELECT id, time_created, data FROM message WHERE session_id = ? AND time_created >= ? \
                 ORDER BY time_created",
            )?
            .query_map(rusqlite::params![sid, cutoff], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
            .collect::<rusqlite::Result<_>>()?;
        for (mid, at, data) in rows {
            let Ok(Value::Object(m)) = pyrules::json_loads(&data) else { continue };
            let ts = Timestamp::from_millisecond(at).unwrap_or(Timestamp::UNIX_EPOCH);
            match m.get("role").and_then(Value::as_str) {
                Some("user") => {
                    let parts: Vec<String> = con
                        .prepare("SELECT data FROM part WHERE message_id = ? ORDER BY time_created")?
                        .query_map([&mid], |r| r.get(0))?
                        .collect::<rusqlite::Result<_>>()?;
                    let text = parts
                        .iter()
                        .filter(|d| d.contains("\"text\""))
                        .map(|d| {
                            pyrules::json_loads(d).ok().and_then(|v| v.get("text").map(py_str)).unwrap_or_default()
                        })
                        .collect::<Vec<_>>()
                        .join("\n");
                    let (mut sender, mut channel) = wake_sender(&text);
                    if parent.is_some() && s.turns.is_empty() {
                        (sender, channel) = ("opencode:parent".into(), "subagent".into());
                    }
                    s.wake(&sender, &channel, &text, show_text, Some(ts), None);
                }
                Some("assistant") if m.get("tokens").is_some_and(Value::is_object) => {
                    let t = &m["tokens"];
                    let cache = t.get("cache").filter(|c| c.is_object());
                    let input = int_or_zero(t.get("input"))
                        + int_or_zero(cache.and_then(|c| c.get("read")))
                        + int_or_zero(cache.and_then(|c| c.get("write")));
                    let output = int_or_zero(t.get("output")) + int_or_zero(t.get("reasoning"));
                    s.call(input, output, Some(ts));
                }
                _ => {}
            }
        }
        if s.calls > 0 {
            out.push(s);
        }
    }
    Ok(out)
}
