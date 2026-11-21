//! Port of `assieme/status.py`, owned by session A (see OWNERS.md).
//!
//! F1 live health and F2 stall alarm: what each agent is doing right now, read from its own logs.
//! States: working, stuck, waiting on subagents, waiting on you, idle with work waiting, idle, exited. Only the tail of
//! each log is read, so polling stays cheap on large sessions. Reading the person's own herdr (`hc`) is not ported.
#![allow(clippy::todo)]
use crate::adapters::{
    Bad, Read, basename, command_label, decode_ignore, get, get_str, get_true, id_of, joined_text, loads, or_obj,
    output_text, program, py_dumps, py_str, secs, tokens, wake_sender,
};
use crate::detect;
use crate::model::HUMAN;
use crate::prelude::*;
use crate::pyrules;
use regex::Regex;
use serde_json::json;

pub use crate::model::parse_ts;

/// One agent read from its log, with its health state.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Agent {
    pub name: String,
    pub source: String,
    pub path: PathBuf,
    /// "idle" when new.
    pub state: String,
    pub last: Option<Timestamp>,
    pub turn_start: Option<Timestamp>,
    pub detail: String,
    /// The calls still open, in the order they started: tool id -> (tool name, label, command). A missing id or
    /// name is "", a missing label "None", as Python's str() would show them.
    pub pending: Vec<(String, (String, String, String))>,
    pub cmds: Vec<String>,
    /// (time, target) messages this agent sent
    pub sends: Vec<(Timestamp, String)>,
    pub sent_at: Option<Timestamp>,
    /// (time, sender) messages from other agents that started a turn
    pub wakes: Vec<(Timestamp, String)>,
    pub waits_on: Vec<String>,
    pub peers: BTreeSet<String>,
    pub thread: Option<String>,
    pub parent_thread: Option<String>,
    /// Its subagents, read the same way.
    pub children: Vec<Agent>,
    pub herdr: Option<Value>,
    pub herdr_address: Option<String>,
    pub herdr_space: Option<String>,
    pub cwd: Option<String>,
    pub asked: bool,
    pub turn_by: Option<String>,
    pub ask_kind: String,
    pub ask_since: Option<Timestamp>,
    pub context: i64,
    /// (time, label, why, advice)
    pub refused: Vec<(Timestamp, String, String, String)>,
    pub context_limit: Option<i64>,
    /// (time, kind, full suite?, label, tool id)
    pub heavy: Vec<(Timestamp, String, bool, String, String)>,
    pub heavy_end: BTreeMap<String, Timestamp>,
    /// (time, fingerprint, when its turn began, label)
    pub failures: Vec<(Timestamp, String, Option<Timestamp>, String)>,
    pub samples: Vec<(Timestamp, i64)>,
}

pub const REPEATS: usize = 4; // the same command this many times in a row within one turn
pub const GRACE: f64 = 120.0; // a message this old that started no turn is "work waiting"
pub const SUBAGENT_WINDOW: f64 = 3600.0; // unfinished subagents older than this are abandoned, not running
pub const TAIL_BYTES: u64 = 512 * 1024;
pub const ORDER: [&str; 7] =
    ["stuck", "waiting on you", "idle, work waiting", "waiting on subagents", "working", "idle", "exited"];
pub const EXIT_GRACE: f64 = 120.0; // an agent seen this recently is never called exited
const ASK_TOOLS: [&str; 2] = ["AskUserQuestion", "ExitPlanMode"];
const EDITS: [&str; 4] = ["Edit", "Write", "NotebookEdit", "MultiEdit"];
const VALUE_FLAGS: [&str; 5] = ["--if", "--output", "--timeout", "--children", "--lines"];

impl Agent {
    pub fn new(name: &str, source: &str, path: &Path) -> Agent {
        Agent { name: name.into(), source: source.into(), path: path.into(), state: "idle".into(), ..Agent::default() }
    }

    /// The first call still open.
    fn tool(&self) -> Option<&(String, String, String)> {
        self.pending.first().map(|(_, t)| t)
    }

    fn pop(&mut self, id: &str) -> Option<(String, String, String)> {
        let i = self.pending.iter().position(|(k, _)| k == id)?;
        Some(self.pending.remove(i).1)
    }

    /// `pending[id] = tool`: a known id keeps its place.
    fn open(&mut self, id: String, tool: (String, String, String)) {
        match self.pending.iter_mut().find(|(k, _)| *k == id) {
            Some(slot) => slot.1 = tool,
            None => self.pending.push((id, tool)),
        }
    }

    pub fn as_dict(&self, now: Timestamp) -> Value {
        json!({"agent": self.name, "source": self.source, "state": self.state, "detail": self.detail,
               "seconds": self.last.map(|l| secs(now, l) as i64), "herdr": self.herdr})
    }
}

/// The JSON objects in the last `size` bytes of a log. The first line is dropped when the file is bigger: it is cut.
pub(crate) fn tail_rows(path: &Path, size: u64) -> std::io::Result<Vec<Map<String, Value>>> {
    use std::io::{Read as _, Seek, SeekFrom};
    let mut f = std::fs::File::open(path)?;
    let end = f.seek(SeekFrom::End(0))?;
    f.seek(SeekFrom::Start(end.saturating_sub(size)))?;
    let mut bytes = vec![];
    f.read_to_end(&mut bytes)?;
    let text = decode_ignore(&bytes);
    let mut lines = pyrules::splitlines(&text);
    if end > size && !lines.is_empty() {
        lines.remove(0);
    }
    Ok(lines.into_iter().filter_map(|l| pyrules::json_loads(l).ok()).filter_map(crate::adapters::into_object).collect())
}

/// The log's first line as JSON; `{}` when it isn't JSON.
fn first_row(path: &Path) -> std::io::Result<Value> {
    let text = decode_ignore(&std::fs::read(path)?);
    let line = crate::adapters::text_lines(&text).into_iter().next().unwrap_or("");
    Ok(loads(line).unwrap_or_else(|| json!({})))
}

/// `'hc send builder --if idle "x"'` -> `['builder']` for verb `send`. Messages are never kept, only who they went to.
pub(crate) fn hc_targets(command: &str, verb: &str) -> Vec<String> {
    static SEPARATORS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[;&|\n]+").unwrap());
    let mut out = vec![];
    for seg in SEPARATORS.split(command) {
        let words =
            detect::shlex_split(seg).unwrap_or_else(|| pyrules::split(seg).into_iter().map(String::from).collect());
        for i in 0..words.len().saturating_sub(1) {
            if matches!(basename(&words[i]), "asm" | "hc") && words[i + 1] == verb {
                let mut rest = &words[i + 2..];
                while rest.first().is_some_and(|w| w.starts_with('-')) {
                    let n = if VALUE_FLAGS.contains(&rest[0].as_str()) { 2 } else { 1 };
                    rest = &rest[n.min(rest.len())..];
                }
                if let Some(target) = rest.first() {
                    out.push(target.clone());
                }
            }
        }
    }
    out
}

fn note_heavy(agent: &mut Agent, command: &str, ts: Timestamp, call_id: &str) {
    let extra = detect::project_heavy(agent.cwd.as_deref());
    for c in detect::commands_of(Some(command)) {
        for (kind, full, label) in detect::heavy_runs(&c, &extra) {
            agent.heavy.push((ts, kind, full, label, call_id.into()));
        }
    }
}

fn note_failure(agent: &mut Agent, text: &str, ts: Timestamp, label: &str) {
    if let Some(sig) = detect::failure_sig(text) {
        agent.failures.push((ts, sig, agent.turn_start, label.into()));
    }
}

fn note_wake(agent: &mut Agent, ts: Timestamp, text: &str) {
    let (sender, _) = wake_sender(text);
    if sender != HUMAN {
        agent.wakes.push((ts, sender.clone()));
    }
    agent.turn_by = Some(sender);
}

/// A user row's typed text: the string, or its text blocks joined by spaces.
fn user_text(content: Option<&Value>) -> Read<String> {
    match content {
        Some(Value::String(s)) => Ok(s.clone()),
        Some(Value::Array(blocks)) => {
            let typed: Vec<Value> =
                blocks.iter().filter(|b| b.get("type").and_then(Value::as_str) == Some("text")).cloned().collect();
            joined_text(Some(&Value::Array(typed)))
        }
        _ => Ok(String::new()),
    }
}

/// The text a log shows for a missing value, as Python's f-strings do.
fn shown(v: Option<&str>) -> String {
    v.unwrap_or("None").into()
}

/// The deny rules that apply in a folder.
type Rules<'a> = &'a dyn Fn(&str) -> Vec<(String, String)>;

fn read_claude(agent: &mut Agent, rows: &[Map<String, Value>], show_text: bool, rules_for: Rules) -> Read<()> {
    for o in rows {
        let kind = o.get("type").and_then(Value::as_str);
        if kind == Some("system") && o.get("subtype").and_then(Value::as_str) == Some("compact_boundary") {
            agent.context = 0; // history was summarised, and no usage row may follow until its next call
            continue;
        }
        let Some(ts) = o.get("timestamp").and_then(crate::model::parse_ts) else { continue };
        let msg = or_obj(o.get("message"))?;
        let content = get(msg, "content");
        if let Some(cwd) = o.get("cwd").filter(|v| pyrules::truthy(v)) {
            agent.cwd = Some(py_str(cwd));
        }
        let blocks: Vec<&Map<String, Value>> = match content {
            Some(Value::Array(a)) => a.iter().filter_map(Value::as_object).collect(),
            _ => vec![],
        };
        let is = |b: &Map<String, Value>, t: &str| b.get("type").and_then(Value::as_str) == Some(t);
        if kind == Some("assistant") {
            agent.last = Some(ts);
            let u = or_obj(get(msg, "usage"))?;
            if u.is_some_and(|u| !u.is_empty()) {
                let used = tokens(u, "input_tokens")?
                    + tokens(u, "cache_read_input_tokens")?
                    + tokens(u, "cache_creation_input_tokens")?;
                if used != 0 {
                    agent.context = used;
                }
                agent.samples.push((ts, agent.context));
            }
            let mut texts = vec![];
            for b in blocks.iter().filter(|b| is(b, "text")) {
                let text = match b.get("text") {
                    None => "",
                    Some(Value::String(t)) => t,
                    Some(_) => return Err(Bad),
                };
                if !text.trim_matches(pyrules::isspace).is_empty() {
                    texts.push(text);
                }
            }
            if let Some(last) = texts.last() {
                // only whether it ends with a question is kept, never the text
                agent.asked = last.trim_end_matches(pyrules::isspace).ends_with('?');
                agent.ask_since = Some(ts);
            }
            if blocks.iter().any(|b| is(b, "tool_use")) {
                agent.asked = false;
            }
            for b in blocks.iter().filter(|b| is(b, "tool_use")) {
                let name = b.get("name").and_then(Value::as_str);
                let cmd = if name == Some("Bash") { get_str(or_obj(b.get("input"))?, "command") } else { None };
                let label = match (name, cmd.filter(|c| !c.is_empty())) {
                    (n, Some(c)) => command_label(&shown(n), c, show_text),
                    (n, None) => shown(n),
                };
                let id = id_of(b.get("id"));
                agent.open(id.clone(), (name.unwrap_or("").into(), label, cmd.unwrap_or("").into()));
                if let Some(cmd) = cmd.filter(|c| !c.is_empty()) {
                    agent.cmds.push(cmd.into());
                    let targets = hc_targets(cmd, "send");
                    agent.sends.extend(targets.into_iter().map(|t| (ts, t)));
                    note_heavy(agent, cmd, ts, &id);
                }
            }
            match get(msg, "stop_reason") {
                Some(Value::String(r)) if r == "end_turn" => agent.state = "idle".into(),
                Some(r) if pyrules::truthy(r) => agent.state = "working".into(),
                _ => {}
            }
        } else if kind == Some("user") && !o.get("isMeta").is_some_and(pyrules::truthy) {
            agent.last = Some(ts);
            agent.state = "working".into();
            let text = user_text(content)?;
            if text.starts_with("[Request interrupted") {
                // the person pressed Esc: the turn is over
                agent.state = "idle".into();
                agent.pending.clear();
                continue;
            }
            if !text.is_empty() {
                agent.turn_start = Some(ts);
                agent.cmds.clear();
                agent.pending.clear();
                agent.asked = false;
                note_wake(agent, ts, &text);
            }
            for b in blocks.iter().filter(|b| is(b, "tool_result")) {
                let id = id_of(b.get("tool_use_id"));
                let tool = agent.pop(&id);
                agent.heavy_end.insert(id, ts);
                let out = match b.get("content") {
                    Some(Value::String(t)) => t.clone(),
                    other => py_dumps(other.unwrap_or(&Value::Null)),
                };
                let Some((name, label, cmd)) = tool.filter(|_| get_true(Some(b), "is_error")) else { continue };
                if crate::rules::REFUSED.is_match(&out) {
                    let rules = rules_for(agent.cwd.as_deref().unwrap_or(""));
                    let (why, advice) = crate::rules::explain(&out, &name, &cmd, &rules, None);
                    agent.refused.push((ts, label, why, advice));
                } else if name == "Bash" {
                    note_failure(agent, &out, ts, &label);
                }
            }
        }
    }
    Ok(())
}

fn read_codex(agent: &mut Agent, rows: &[Map<String, Value>], show_text: bool) -> Read<()> {
    for o in rows {
        let payload = or_obj(o.get("payload")); // a payload that isn't a dict fails where it is first read
        let row_type = o.get("type").and_then(Value::as_str);
        if row_type == Some("compacted") {
            agent.context = 0; // history was summarised: the old context size no longer applies
            continue;
        }
        if row_type == Some("session_meta") {
            let p = payload?;
            agent.thread = get(p, "id").filter(|v| !v.is_null()).map(py_str);
            let src = get(p, "source").and_then(Value::as_object);
            let sub = get(src, "subagent").and_then(Value::as_object);
            let spawn = get(sub, "thread_spawn").and_then(Value::as_object);
            agent.parent_thread = get(spawn, "parent_thread_id").filter(|v| !v.is_null()).map(py_str);
            continue;
        }
        let Some(ts) = o.get("timestamp").and_then(crate::model::parse_ts) else { continue };
        agent.last = Some(ts);
        let p = payload?;
        let kind = get(p, "type");
        let kind_str = kind.and_then(Value::as_str);
        let role = get(p, "role").and_then(Value::as_str);
        match kind_str {
            Some("message") if role == Some("user") => {
                let text = joined_text(get(p, "content"))?;
                if !crate::adapters::injected(&text) {
                    note_wake(agent, ts, &text);
                    agent.asked = false;
                }
            }
            Some("message") if role == Some("assistant") => {
                let text = joined_text(get(p, "content"))?;
                let text = text.trim_end_matches(pyrules::isspace);
                if !text.is_empty() {
                    agent.asked = text.ends_with('?');
                    agent.ask_since = Some(ts);
                }
            }
            Some("task_started") => {
                agent.state = "working".into();
                agent.turn_start = Some(ts);
                agent.cmds.clear();
                agent.pending.clear();
            }
            Some("task_complete" | "turn_aborted") => {
                agent.state = "idle".into();
                agent.pending.clear();
            }
            Some(k) if k.contains("approval_request") || k.contains("request_user_input") => {
                agent.state = "working".into();
                agent.pending = vec![("ask".into(), (k.into(), "approval".into(), String::new()))];
                agent.ask_since = Some(ts);
            }
            Some("function_call" | "custom_tool_call") => {
                let p = p.ok_or(Bad)?;
                let cmd = crate::adapters::codex_command(p)?;
                let name = p.get("name").filter(|v| pyrules::truthy(v)).map(py_str);
                let label = command_label(name.as_deref().unwrap_or("?"), &cmd, show_text);
                let id = id_of(p.get("call_id"));
                let tool_name = p.get("name").and_then(Value::as_str).unwrap_or("").to_string();
                agent.pending = vec![(id.clone(), (tool_name, label, cmd.clone()))];
                if !cmd.is_empty() {
                    agent.cmds.push(cmd.clone());
                    let targets = hc_targets(&cmd, "send");
                    agent.sends.extend(targets.into_iter().map(|t| (ts, t)));
                    note_heavy(agent, &cmd, ts, &id);
                }
            }
            Some("function_call_output" | "custom_tool_call_output") => {
                let id = id_of(get(p, "call_id"));
                let tool = agent.pop(&id);
                agent.heavy_end.insert(id, ts);
                let out = output_text(get(p, "output"))?;
                let Some((_, label, cmd)) = tool else { continue };
                if crate::adapters::CODEX_REFUSED.is_match(&out) {
                    agent.refused.push((ts, label, "Codex sandbox or approval".into(), String::new()));
                } else if detect::codex_failed(&out) {
                    let inner = detect::commands_of(Some(&cmd));
                    let label = inner.first().map_or(label, |c| command_label("Bash", c, show_text));
                    note_failure(agent, &out, ts, &label);
                }
            }
            Some("token_count") if get(p, "info").is_some_and(Value::is_object) => {
                let info = get(p, "info").and_then(Value::as_object);
                let last = or_obj(get(info, "last_token_usage"))?;
                let input = get(last, "input_tokens").filter(|v| pyrules::truthy(v));
                if let Some(n) = input {
                    agent.context = number(n)?;
                }
                if let Some(w) = get(info, "model_context_window").filter(|v| pyrules::truthy(v)) {
                    agent.context_limit = Some(number(w)?);
                }
                if input.is_some() {
                    agent.samples.push((ts, agent.context));
                }
            }
            _ => {}
        }
    }
    Ok(())
}

fn number(v: &Value) -> Read<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).ok_or(Bad),
        Value::Bool(b) => Ok(*b as i64),
        _ => Err(Bad),
    }
}

/// `5 min`, or `3 h` past two hours.
pub(crate) fn ago(seconds: f64) -> String {
    let m = (seconds / 60.0).floor() as i64;
    if m < 120 { format!("{m} min") } else { format!("{} h", m.div_euclid(60)) }
}

/// Turn raw turn state into the health state the owner reads.
fn finish(agent: &mut Agent, now: Timestamp, stuck_after: f64) {
    static WAIT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(?:asm|hc)\s+wait\b").unwrap());
    if agent.state != "working" {
        return;
    }
    let age = agent.last.map_or(0.0, |l| secs(now, l));
    let tool = agent.tool().cloned();
    match &tool {
        Some((name, label, _)) if ASK_TOOLS.contains(&name.as_str()) || label == "approval" => {
            agent.state = "waiting on you".into();
            agent.detail = format!("asked {} ago", ago(age));
            agent.ask_kind = match name.as_str() {
                "AskUserQuestion" => "a question",
                "ExitPlanMode" => "a plan to approve",
                _ => "an approval",
            }
            .into();
            agent.ask_since = agent.last;
        }
        Some((_, _, cmd)) if WAIT.is_match(cmd) => agent.detail = "waiting".into(), // never "stuck" by silence
        _ if age > stuck_after => {
            agent.state = "stuck".into();
            agent.detail = match &tool {
                Some((_, label, _)) => format!("silent {}, last {label}", ago(age)),
                None => format!("no reply for {} (stuck, or the session closed)", ago(age)),
            };
        }
        _ if agent.cmds.len() >= REPEATS
            && agent.cmds[agent.cmds.len() - REPEATS..].iter().all(|c| *c == agent.cmds[agent.cmds.len() - 1]) =>
        {
            agent.state = "stuck".into();
            agent.detail = format!("same command x{REPEATS}: {}", tool.map_or("Bash".into(), |t| t.1));
        }
        Some((_, label, _)) => agent.detail = format!("running {label}"),
        None => agent.detail = "thinking".into(),
    }
}

/// How much context this agent can hold, or None if its log doesn't say. Claude Code's is 200K, or 1M once a session
/// has gone past that.
pub fn memory_limit(agent: &Agent) -> Option<i64> {
    if agent.source == "codex" {
        return agent.context_limit;
    }
    Some(if agent.context <= 200_000 { 200_000 } else { 1_000_000 })
}

fn short(command: &Value, show_text: bool) -> String {
    let command = match command {
        Value::Array(a) => a.iter().map(py_str).collect::<Vec<_>>().join(" "),
        Value::String(s) => s.clone(),
        _ => String::new(),
    };
    if show_text { pyrules::split(&command).join(" ") } else { program(&command) }
}

/// An agent's last few commands and edits, newest first, as (time, plain words). Program and file names only unless
/// show_text: arguments can hold secrets. Messages are never included.
pub fn recent_actions(path: &Path, source: &str, show_text: bool, n: usize) -> Vec<(Timestamp, String)> {
    let Ok(rows) = tail_rows(path, TAIL_BYTES) else { return vec![] };
    let mut out: Vec<(Timestamp, String)> = vec![];
    let mut by_id: BTreeMap<String, usize> = BTreeMap::new();
    for o in &rows {
        let Some(ts) = o.get("timestamp").and_then(crate::model::parse_ts) else { continue };
        if source == "codex" {
            let Ok(p) = or_obj(o.get("payload")) else { return vec![] };
            let kind = get(p, "type").and_then(Value::as_str);
            if let (Some("function_call" | "custom_tool_call"), Some(p)) = (kind, p) {
                let raw = [p.get("arguments"), p.get("input")].into_iter().flatten().find(|v| pyrules::truthy(v));
                let args = match raw {
                    Some(Value::String(s)) => pyrules::json_loads(s).unwrap_or_else(|_| json!({})),
                    Some(v) => v.clone(),
                    None => json!(""),
                };
                let cmd = args
                    .as_object()
                    .and_then(|a| [a.get("cmd"), a.get("command")].into_iter().flatten().find(|v| pyrules::truthy(v)));
                if let Some(cmd) = cmd {
                    out.push((ts, format!("Ran {}", short(cmd, show_text))));
                } else if p.get("name").and_then(Value::as_str) == Some("apply_patch") {
                    out.push((ts, "Edited files".into()));
                }
            }
            continue;
        }
        let Ok(msg) = or_obj(o.get("message")) else { return vec![] };
        let content = get(msg, "content");
        let blocks: Vec<&Map<String, Value>> = match content {
            Some(Value::Array(a)) => a.iter().filter_map(Value::as_object).collect(),
            _ => vec![],
        };
        let kind = o.get("type").and_then(Value::as_str);
        if kind == Some("assistant") {
            for b in blocks.iter().filter(|b| b.get("type").and_then(Value::as_str) == Some("tool_use")) {
                let Ok(inp) = or_obj(b.get("input")) else { return vec![] };
                let name = b.get("name").and_then(Value::as_str).unwrap_or("");
                if name == "Bash" {
                    out.push((ts, format!("Ran {}", short(get(inp, "command").unwrap_or(&Value::Null), show_text))));
                } else if EDITS.contains(&name) {
                    let file = [get(inp, "file_path"), get(inp, "notebook_path")]
                        .into_iter()
                        .flatten()
                        .find(|v| pyrules::truthy(v))
                        .map_or("a file".into(), py_str);
                    out.push((ts, format!("Edited {}", basename(&file))));
                } else {
                    continue;
                }
                by_id.insert(id_of(b.get("id")), out.len() - 1);
            }
        } else if kind == Some("user") && !o.get("isMeta").is_some_and(pyrules::truthy) {
            let Ok(text) = user_text(content) else { return vec![] };
            let (sender, _) = wake_sender(&text);
            if !text.is_empty() && sender != HUMAN {
                out.push((ts, format!("Took a note from {sender}")));
            }
            for b in &blocks {
                if b.get("type").and_then(Value::as_str) == Some("tool_result")
                    && get_true(Some(b), "is_error")
                    && let Some(&i) = b.get("tool_use_id").map(|v| id_of(Some(v))).as_ref().and_then(|id| by_id.get(id))
                {
                    out[i].1.push_str(", failed");
                }
            }
        }
    }
    out.into_iter().rev().take(n).collect()
}

/// Read recent logs -> agents with health states. `herdr` (the person's own herdr) is not read any more: it is
/// accepted and ignored. `rules_for`: the deny rules for a folder; None reads each folder's and the person's.
#[allow(clippy::too_many_arguments)]
pub fn collect(
    claude_dir: &Path,
    codex_dir: &Path,
    now: Timestamp,
    hours: f64,
    show_text: bool,
    _herdr: Option<&[Value]>,
    running: Option<&BTreeSet<String>>,
    rules_for: Option<&RulesFor>,
) -> Vec<Agent> {
    let cutoff = crate::adapters::seconds(now) - hours * 3600.0;
    let user = crate::adapters::home().join(".claude/settings.json");
    let cache: Mutex<BTreeMap<String, Vec<(String, String)>>> = Mutex::default();
    let own = |cwd: &str| -> Vec<(String, String)> {
        let mut cache = cache.lock().unwrap();
        let cwd_opt = (!cwd.is_empty()).then_some(cwd);
        cache.entry(cwd.into()).or_insert_with(|| crate::rules::load(cwd_opt, &user)).clone()
    };
    let rules_for: Rules = match rules_for {
        Some(r) => r,
        None => &own,
    };
    let mtime = crate::adapters::mtime_or_zero;
    let mut agents: Vec<Agent> = vec![];

    let mut latest: Vec<(String, PathBuf)> = vec![];
    for p in pyrules::glob(&claude_dir.join("*/*.jsonl")) {
        if mtime(&p) < cutoff {
            continue;
        }
        let d = p.parent().and_then(Path::file_name).map(|d| d.to_string_lossy().into_owned()).unwrap_or_default();
        match latest.iter_mut().find(|(k, _)| *k == d) {
            Some(slot) if mtime(&p) > mtime(&slot.1) => slot.1 = p,
            Some(_) => {}
            None => latest.push((d, p)),
        }
    }
    let mut folders: Vec<String> = latest.iter().map(|(d, _)| d.clone()).collect();
    folders.sort();
    let names = crate::adapters::strip_common_prefix(&folders);
    for (d, path) in &latest {
        let mut a = Agent::new(&names[d], "claude-code", path);
        let read = || -> Read<Vec<Agent>> {
            let mut children = vec![];
            let base = path.to_string_lossy();
            for sp in pyrules::glob(&PathBuf::from(format!("{}/subagents/*.jsonl", base.trim_end_matches(".jsonl")))) {
                if crate::adapters::seconds(now) - mtime(&sp) > SUBAGENT_WINDOW {
                    continue;
                }
                children.push(sp);
            }
            let mut out = vec![];
            for sp in children {
                let mut child = Agent::new(&format!("{}/sub", names[d]), "claude-code", &sp);
                read_claude(&mut child, &tail_rows(&sp, TAIL_BYTES).map_err(|_| Bad)?, show_text, rules_for)?;
                if child.last.is_some() {
                    out.push(child);
                }
            }
            Ok(out)
        };
        let Ok(rows) = tail_rows(path, TAIL_BYTES) else { continue };
        if read_claude(&mut a, &rows, show_text, rules_for).is_err() {
            continue; // one damaged log loses that agent, never the whole view
        }
        let Ok(children) = read() else { continue };
        a.children = children;
        if a.last.is_some() {
            agents.push(a);
        }
    }

    // Codex: every log once, then subagents under their parent thread
    let mut all: Vec<Agent> = vec![];
    let mut by_thread: Vec<(Option<String>, usize)> = vec![];
    let mut names: Vec<(String, usize)> = vec![];
    for path in crate::adapters::glob_deep(codex_dir, ".jsonl") {
        if mtime(&path) < cutoff {
            continue;
        }
        let mut a = Agent::new("", "codex", &path);
        let read = |a: &mut Agent| -> Read<Value> {
            let first = first_row(&path).map_err(|_| Bad)?;
            let Value::Object(first_map) = &first else { return Err(Bad) };
            let meta = or_obj(first_map.get("payload"))?.cloned().map(Value::Object).unwrap_or_else(|| json!({}));
            let mut rows = vec![first_map.clone()];
            rows.extend(tail_rows(&path, TAIL_BYTES).map_err(|_| Bad)?);
            read_codex(a, &rows, show_text)?;
            Ok(meta)
        };
        let Ok(meta) = read(&mut a) else { continue }; // one damaged log loses that agent, never the whole view
        if a.last.is_none() {
            continue;
        }
        let src = meta.get("source").and_then(Value::as_object);
        let sub = src.and_then(|s| s.get("subagent")).and_then(Value::as_object);
        let kind = if a.parent_thread.is_some() {
            "subagent".to_string()
        } else {
            sub.and_then(|s| s.get("other")).filter(|v| pyrules::truthy(v)).map(py_str).unwrap_or_default()
        };
        let cwd = meta.get("cwd").filter(|v| pyrules::truthy(v)).map(py_str);
        let folder = cwd.as_deref().map(basename).filter(|b| !b.is_empty()).unwrap_or("?");
        a.name = format!("codex:{folder}") + &if kind.is_empty() { String::new() } else { format!("/{kind}") };
        a.cwd = meta.get("cwd").and_then(Value::as_str).map(String::from);
        let i = all.len();
        match by_thread.iter_mut().find(|(t, _)| *t == a.thread) {
            Some(slot) => slot.1 = i,
            None => by_thread.push((a.thread.clone(), i)),
        }
        let newer = |n: &(String, usize)| a.last > all[n.1].last;
        if kind != "subagent" {
            match names.iter().position(|(n, _)| *n == a.name) {
                Some(k) if newer(&names[k]) => names[k].1 = i,
                Some(_) => {}
                None => names.push((a.name.clone(), i)),
            }
        }
        all.push(a);
    }
    let mut children: Vec<Vec<usize>> = vec![vec![]; all.len()];
    for (_, i) in &by_thread {
        let parent = all[*i].parent_thread.as_ref();
        if let Some(&(_, p)) = by_thread.iter().find(|(t, _)| parent.is_some() && t.as_ref() == parent) {
            children[p].push(*i);
        }
    }
    let mut slots: Vec<Option<Agent>> = all.into_iter().map(Some).collect();
    fn build(i: usize, slots: &mut [Option<Agent>], children: &[Vec<usize>], seen: &mut Vec<usize>) -> Option<Agent> {
        if seen.contains(&i) {
            return None; // a thread that names itself, or a loop of them: no one's child twice
        }
        seen.push(i);
        let mut a = slots[i].clone()?;
        a.children = children[i].iter().filter_map(|&c| build(c, slots, children, seen)).collect();
        Some(a)
    }
    for (_, i) in &names {
        let a = build(*i, &mut slots, &children, &mut vec![]);
        if let Some(a) = a.filter(|a| !(a.name.ends_with("/guardian") && a.state != "working")) {
            agents.push(a);
        }
    }

    let stuck_after = crate::settings::threshold("quiet_minutes") * 60.0;
    for a in agents.iter_mut() {
        finish(a, now, stuck_after);
        for c in a.children.iter_mut() {
            finish(c, now, stuck_after);
        }
    }
    for a in agents.iter_mut() {
        let busy = a.children.iter().filter(|c| c.state == "working" || c.state == "waiting on you").count();
        let lost = a.children.iter().find(|c| c.state == "stuck").map(|c| c.detail.clone());
        if a.state == "idle" && busy > 0 {
            a.state = "waiting on subagents".into();
            a.detail = format!("{busy} running");
        } else if let (true, Some(detail)) = (a.state == "idle", lost) {
            a.state = "stuck".into();
            a.detail = format!("subagent {detail}");
        }
    }

    if let Some(running) = running {
        // a folder with no Claude or Codex process: the session was closed
        for a in agents.iter_mut() {
            let quiet = a.last.is_some_and(|l| secs(now, l) > EXIT_GRACE);
            if a.cwd.as_ref().is_some_and(|c| !c.is_empty() && !running.contains(c))
                && quiet
                && matches!(a.state.as_str(), "idle" | "stuck" | "idle, work waiting")
            {
                a.state = "exited".into();
                a.detail = String::new();
            }
        }
    }
    for a in agents.iter_mut() {
        // it ended its turn with a question, in a turn a person started: the person owes an answer
        if a.state == "idle" && a.asked && a.turn_by.as_deref() == Some(HUMAN) {
            a.state = "waiting on you".into();
            a.ask_kind = "a question in its reply".into();
            let since = a.ask_since.or(a.last).unwrap_or(now);
            a.detail = format!("asked you a question {} ago", ago(secs(now, since)));
        }
    }
    let alias = aliases(&agents);
    work_waiting(&mut agents, now, &alias);
    wait_edges(&mut agents, &alias);
    let rank = |s: &str| ORDER.iter().position(|o| *o == s).unwrap_or(ORDER.len());
    agents.sort_by(|a, b| (rank(&a.state), &a.name).cmp(&(rank(&b.state), &b.name)));
    agents
}

/// Every name an agent is addressed by: our name and its short forms.
fn aliases(agents: &[Agent]) -> BTreeMap<String, usize> {
    let mut alias: BTreeMap<String, usize> = agents.iter().enumerate().map(|(i, a)| (a.name.clone(), i)).collect();
    for (i, a) in agents.iter().enumerate() {
        for short in [a.name.rsplit(':').next(), a.name.rsplit('-').next()].into_iter().flatten() {
            alias.entry(short.into()).or_insert(i);
        }
    }
    alias
}

/// A message sent to an idle agent after its last turn began, that started no turn: work is waiting.
fn work_waiting(agents: &mut [Agent], now: Timestamp, alias: &BTreeMap<String, usize>) {
    for s in 0..agents.len() {
        for (ts, target) in agents[s].sends.clone() {
            let Some(&r) = alias.get(&target).filter(|&&r| r != s) else { continue };
            let (sender, receiver) = (agents[s].name.clone(), agents[r].name.clone());
            agents[s].peers.insert(receiver);
            agents[r].peers.insert(sender.clone());
            let a = &mut agents[r];
            if a.state == "idle"
                && secs(now, ts) > GRACE
                && a.turn_start.is_none_or(|t| ts > t)
                && a.last.is_none_or(|l| ts > l)
            {
                a.state = "idle, work waiting".into();
                a.detail = format!("message from {sender} {} ago started no turn", ago(secs(now, ts)));
            }
        }
    }
}

/// F2: who each agent is blocked on. Also links agents that woke each other into one team.
fn wait_edges(agents: &mut [Agent], alias: &BTreeMap<String, usize>) {
    for a in 0..agents.len() {
        for (_, sender) in agents[a].wakes.clone() {
            if let Some(&s) = alias.get(&sender).filter(|&&s| s != a) {
                let (an, sn) = (agents[a].name.clone(), agents[s].name.clone());
                agents[a].peers.insert(sn);
                agents[s].peers.insert(an);
            }
        }
    }
    for a in 0..agents.len() {
        let cmd = agents[a].tool().map(|t| t.2.clone()).unwrap_or_default();
        let waited: Vec<String> =
            hc_targets(&cmd, "wait").iter().filter_map(|t| alias.get(t)).map(|&w| agents[w].name.clone()).collect();
        let me = &agents[a];
        if me.state == "waiting on you" {
            agents[a].waits_on = vec!["you".into()];
        } else if me.state == "working" && !waited.is_empty() {
            // `asm wait <agent>`: busy on paper, blocked in fact
            agents[a].detail = format!("waiting for {}", waited.join(", "));
            agents[a].waits_on = waited;
        } else if me.state == "idle" {
            // its last message this turn went to X, and X has not answered since: it may wait on X
            let sends: Vec<(Timestamp, usize)> = me
                .sends
                .iter()
                .filter_map(|(ts, t)| alias.get(t).filter(|&&x| x != a).map(|&x| (*ts, x)))
                .filter(|(ts, _)| me.turn_start.is_none_or(|start| *ts >= start))
                .collect();
            let latest = sends.iter().copied().fold(None::<(Timestamp, usize)>, |best, s| match best {
                Some(b) if s.0 <= b.0 => Some(b),
                _ => Some(s),
            });
            if let Some((ts, x)) = latest
                && !me.wakes.iter().any(|(w_ts, w)| *w_ts > ts && alias.get(w) == Some(&x))
            {
                agents[a].waits_on = vec![agents[x].name.clone()];
                agents[a].sent_at = Some(ts);
            }
        }
    }
    // X took A's message and finished with nothing open: that was a report, not a request. Repeat until stable.
    let mut changed = true;
    while changed {
        changed = false;
        for a in 0..agents.len() {
            let me = &agents[a];
            let Some(&x) = me.waits_on.first().filter(|_| me.state == "idle").and_then(|w| alias.get(w)) else {
                continue;
            };
            let other = &agents[x];
            let sent = me.sent_at;
            let answered =
                other.wakes.iter().any(|(w_ts, w)| sent.is_some_and(|s| *w_ts >= s) && alias.get(w) == Some(&a))
                    || matches!((other.turn_start, sent), (Some(t), Some(s)) if t > s);
            if other.state == "idle" && other.waits_on.is_empty() && answered {
                agents[a].waits_on.clear();
                changed = true;
            }
        }
    }
}

/// F2: teams where nobody is making progress but work is open. Returns one description per stalled team.
pub fn stalls(agents: &[Agent], now: Timestamp) -> Vec<Value> {
    let by: BTreeMap<&str, usize> = agents.iter().enumerate().map(|(i, a)| (a.name.as_str(), i)).collect();
    let (mut seen, mut out) = (BTreeSet::<&str>::new(), vec![]);
    for start in agents {
        if seen.contains(start.name.as_str()) || start.peers.is_empty() {
            continue;
        }
        let (mut team, mut todo) = (vec![], vec![start]);
        while let Some(a) = todo.pop() {
            if !seen.insert(&a.name) {
                continue;
            }
            team.push(a);
            todo.extend(
                a.peers
                    .iter()
                    .filter(|p| !seen.contains(p.as_str()))
                    .filter_map(|p| by.get(p.as_str()))
                    .map(|&i| &agents[i]),
            );
        }
        if team.len() < 2 {
            continue;
        }
        let moving = team
            .iter()
            .any(|a| matches!(a.state.as_str(), "working" | "waiting on subagents") && a.waits_on.is_empty());
        let blocked = team.iter().any(|a| {
            !a.waits_on.is_empty() || matches!(a.state.as_str(), "stuck" | "idle, work waiting" | "waiting on you")
        });
        let quiet = team.iter().map(|a| a.last.map_or(0.0, |l| secs(now, l))).fold(f64::INFINITY, f64::min);
        if moving || !blocked || quiet < GRACE {
            continue;
        }
        let mut roots: Vec<((usize, bool), Vec<&Agent>)> = vec![];
        for a in &team {
            let (r, looped) = root(a, agents, &by);
            if !std::ptr::eq(&agents[r], *a) || a.state != "idle" {
                match roots.iter_mut().find(|(k, _)| *k == (r, looped)) {
                    Some((_, v)) => v.push(a),
                    None => roots.push(((r, looped), vec![a])),
                }
            }
        }
        let others = |r: usize, deps: &[&Agent]| -> Vec<String> {
            let mut v: Vec<String> =
                deps.iter().filter(|d| !std::ptr::eq(**d, &agents[r])).map(|d| d.name.clone()).collect();
            v.sort();
            v
        };
        let mut ranked: Vec<&((usize, bool), Vec<&Agent>)> = roots.iter().collect();
        ranked.sort_by_key(|(_, deps)| std::cmp::Reverse(deps.len()));
        let parts: Vec<String> = ranked
            .iter()
            .map(|((r, looped), deps)| {
                let o = others(*r, deps);
                let who = if o.is_empty() {
                    String::new()
                } else {
                    let verb = if o.len() > 1 { "agents wait" } else { "agent waits" };
                    format!("{} {verb} on {} ({}); ", o.len(), agents[*r].name, o.join(", "))
                };
                who + &blocker(&agents[*r], *looped)
            })
            .collect();
        let mut names: Vec<&str> = team.iter().map(|a| a.name.as_str()).collect();
        names.sort();
        let roots: Vec<Value> = roots
            .iter()
            .map(|((r, looped), deps)| {
                json!({"agent": agents[*r].name, "state": agents[*r].state, "looped": looped,
                       "waiting": others(*r, deps)})
            })
            .collect();
        out.push(json!({"team": names, "quiet_seconds": quiet as i64, "roots": roots,
                        "text": format!("Team stalled for {}: {}", ago(quiet), parts.join("; "))}));
    }
    out
}

/// Follow waits-on links to the agent at the end of the chain -> (agent, true if the chain loops).
fn root(a: &Agent, agents: &[Agent], by: &BTreeMap<&str, usize>) -> (usize, bool) {
    let mut path = vec![by[a.name.as_str()]];
    while let Some(&next) = agents[*path.last().unwrap()].waits_on.first().and_then(|w| by.get(w.as_str())) {
        if path.contains(&next) {
            return (next, true);
        }
        path.push(next);
    }
    (*path.last().unwrap(), false)
}

fn blocker(r: &Agent, looped: bool) -> String {
    match r.state.as_str() {
        "waiting on you" => format!("{} waits on you ({})", r.name, r.detail),
        "stuck" => format!("{} is stuck ({})", r.name, r.detail),
        "exited" => format!("{} has exited (its session is closed)", r.name),
        "idle, work waiting" => format!("{} never started on its message ({})", r.name, r.detail),
        _ if looped => format!("{} and {} wait on each other", r.name, r.waits_on.first().map_or("", |w| w.as_str())),
        _ => format!("{} is idle with nothing to do", r.name),
    }
}

/// Working folders of running Claude Code and Codex processes, or None when that can't be told.
pub fn running_cwds() -> Option<BTreeSet<String>> {
    use std::process::Command;
    static AGENT: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(^|/)(claude|codex|gemini|opencode|cursor-agent)(\s|$)").unwrap());
    let ps = Command::new("ps").args(["-axo", "pid=,command="]).output().ok()?;
    let ps = String::from_utf8_lossy(&ps.stdout);
    let pids: Vec<&str> = pyrules::splitlines(&ps)
        .into_iter()
        .filter_map(|line| {
            let mut words = line.trim_matches(pyrules::isspace).splitn(2, char::is_whitespace);
            let pid = words.next().filter(|p| !p.is_empty())?;
            AGENT.is_match(words.next().unwrap_or("").trim_start()).then_some(pid)
        })
        .collect();
    if pids.is_empty() {
        return Some(BTreeSet::new());
    }
    if Path::new("/proc").is_dir() {
        // Linux
        let cwd = |pid: &str| std::fs::read_link(format!("/proc/{pid}/cwd")).ok();
        return Some(pids.iter().filter_map(|p| cwd(p)).map(|p| p.to_string_lossy().into_owned()).collect());
    }
    // macOS: one lsof call for all of them
    let lsof = Command::new("lsof").args(["-a", "-d", "cwd", "-p", &pids.join(","), "-Fn"]).output().ok()?;
    let out = String::from_utf8_lossy(&lsof.stdout);
    Some(pyrules::splitlines(&out).into_iter().filter_map(|l| l.strip_prefix('n')).map(String::from).collect())
}

/// Python's `datetime.isoformat()` of a UTC time.
fn isoformat(t: Timestamp) -> String {
    let fmt = if t.subsec_nanosecond() / 1000 != 0 { "%Y-%m-%dT%H:%M:%S%.6f+00:00" } else { "%Y-%m-%dT%H:%M:%S+00:00" };
    t.strftime(fmt).to_string()
}

/// F9: decisions that are truly the owner's, each with how long it has waited and which agents it holds up.
pub fn owner_queue(agents: &[Agent], now: Timestamp) -> Vec<Value> {
    let by: BTreeMap<&str, usize> = agents.iter().enumerate().map(|(i, a)| (a.name.as_str(), i)).collect();
    let mut blocks: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for a in agents {
        if !a.waits_on.is_empty() && a.waits_on != ["you"] {
            let (r, _) = root(a, agents, &by);
            if !std::ptr::eq(&agents[r], a) && agents[r].state == "waiting on you" {
                blocks.entry(&agents[r].name).or_default().push(&a.name);
            }
        }
    }
    let mut items: Vec<(usize, i64, Value)> = vec![];
    for a in agents.iter().filter(|a| a.state == "waiting on you") {
        let Some(since) = a.ask_since.or(a.last) else { continue };
        let mut held = blocks.get(a.name.as_str()).cloned().unwrap_or_default();
        held.sort();
        let seconds = secs(now, since) as i64;
        let kind = if a.ask_kind.is_empty() { "an answer" } else { &a.ask_kind };
        let key = format!("{}|{}|{}", a.name, a.ask_kind, isoformat(since));
        items.push((
            held.len(),
            seconds,
            json!({"agent": a.name, "kind": kind, "seconds": seconds, "blocks": held, "key": key}),
        ));
    }
    items.sort_by_key(|(n, s, _)| (std::cmp::Reverse(*n), std::cmp::Reverse(*s)));
    items.into_iter().map(|x| x.2).collect()
}

pub fn now_utc() -> Timestamp {
    Timestamp::now()
}
/// The person's herdr install's own hc, else theirs on the login PATH.
pub fn hc_path() -> Option<PathBuf> {
    todo!("status.hc_path")
}

pub fn herdr_core_up() -> bool {
    todo!("status.herdr_core_up")
}

/// Live herdr-core terminals; [] when herdr's core isn't running; None when they can't be read.
pub fn herdr_terminals() -> Option<Vec<Value>> {
    todo!("status.herdr_terminals")
}

/// (headline, alerts).
pub fn summary(agents: &[Agent]) -> (String, Vec<String>) {
    let head: Vec<String> = ORDER
        .iter()
        .map(|s| (s, agents.iter().filter(|a| a.state == *s).count()))
        .filter(|(_, n)| *n > 0)
        .map(|(s, n)| format!("{n} {s}"))
        .collect();
    let head = if head.is_empty() { "no active agents".into() } else { head.join(", ") };
    let alerts = agents
        .iter()
        .filter(|a| ORDER[..3].contains(&a.state.as_str()))
        .map(|a| format!("{}: {} ({})", a.state, a.name, a.detail))
        .collect();
    (head, alerts)
}

/// F5, F6, F7 and F10 as they stand right now: each one says what, why and the fix.
pub(crate) fn warnings(agents: &[Agent], now: Timestamp) -> Vec<Value> {
    let since = now - jiff::SignedDuration::from_hours(detect::BOX_LIVE_HOURS);
    let boxes = crate::adapters::box_metrics(&crate::adapters::box_path(), since);
    let mut out = detect::live_findings(agents, now);
    out.extend(detect::box_findings(&boxes));
    out
}

pub fn to_json(agents: &[Agent], now: Timestamp) -> String {
    let (head, alerts) = summary(agents);
    let out = json!({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ").to_string(), "summary": head, "alerts": alerts,
                     "queue": owner_queue(agents, now), "stalls": stalls(agents, now),
                     "warnings": warnings(agents, now),
                     "agents": agents.iter().map(|a| a.as_dict(now)).collect::<Vec<_>>()});
    serde_json::to_string_pretty(&out).unwrap_or_default()
}

pub fn to_text(_agents: &[Agent], _now: Timestamp) -> String {
    todo!("status.to_text")
}
