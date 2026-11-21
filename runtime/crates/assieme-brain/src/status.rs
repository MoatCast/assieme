//! Port of `assieme/status.py`, owned by session A (see OWNERS.md).
//!
//! F1 live health and F2 stall alarm: what each agent is doing right now, read from its own logs.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

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

/// Read recent logs -> agents with health states. `herdr`: herdr-core's terminals.
#[allow(clippy::too_many_arguments)]
pub fn collect(
    claude_dir: &Path,
    codex_dir: &Path,
    now: Timestamp,
    hours: f64,
    show_text: bool,
    herdr: Option<&[Value]>,
    running: Option<&BTreeSet<String>>,
    rules_for: Option<&RulesFor>,
) -> Vec<Agent> {
    todo!("status.collect")
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

/// How much context this agent can hold, or None if its log doesn't say.
pub fn memory_limit(agent: &Agent) -> Option<i64> {
    todo!("status.memory_limit")
}

pub fn now_utc() -> Timestamp {
    todo!("status.now_utc")
}

/// F9: decisions that are truly the owner's.
pub fn owner_queue(agents: &[Agent], now: Timestamp) -> Vec<Value> {
    todo!("status.owner_queue")
}

/// An agent's last few commands and edits, newest first, as (time, plain words).
pub fn recent_actions(path: &Path, source: &str, show_text: bool, n: usize) -> Vec<(Timestamp, String)> {
    todo!("status.recent_actions")
}

/// Working folders of running Claude Code and Codex processes, or None when that can't be told.
pub fn running_cwds() -> Option<BTreeSet<String>> {
    todo!("status.running_cwds")
}

/// F2: one description per stalled team: `{team, quiet_seconds, roots, text}`.
pub fn stalls(agents: &[Agent], now: Timestamp) -> Vec<Value> {
    todo!("status.stalls")
}

/// (headline, alerts).
pub fn summary(agents: &[Agent]) -> (String, Vec<String>) {
    todo!("status.summary")
}

pub fn to_json(agents: &[Agent], now: Timestamp) -> String {
    todo!("status.to_json")
}

pub fn to_text(agents: &[Agent], now: Timestamp) -> String {
    todo!("status.to_text")
}
