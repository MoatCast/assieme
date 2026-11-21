//! Port of `assieme/live.py`, owned by session A (see OWNERS.md).
//!
//! The live view: what `assieme` opens with no arguments.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const NOTIFY_MAX_AGE: i64 = 2 * 3600;
pub const REFUSAL_WINDOW: i64 = 30 * 60;

/// Append one entry to a JSONL log.
pub fn log(path: &Path, entry: &Value) {
    todo!("live._log")
}

/// Notify once per new item in the owner's queue and per new stall. Returns the (title, body) pairs sent.
pub fn announce(queue: &[Value], stalls: &[String], now: Timestamp) -> Vec<(String, String)> {
    todo!("live.announce")
}

pub fn log_path() -> PathBuf {
    todo!("live.log_path")
}

/// A desktop notification: macOS Notification Centre, notify-send on Linux, else the terminal bell.
pub fn notify(title: &str, body: &str) {
    todo!("live.notify")
}

pub fn recent_actions(path: Option<&Path>, n: usize) -> Vec<Value> {
    todo!("live.recent_actions")
}

/// One Activity row. `extra` is what Undo needs (see activity.UNDO). Returns the entry.
pub fn record(row: Record<'_>) -> Value {
    todo!("live.record")
}

/// `live.record`'s keyword arguments: `Record { action, agent, reason, ..Record::default() }`.
#[derive(Default)]
pub struct Record<'a> {
    pub action: &'a str,
    pub agent: &'a str,
    pub reason: &'a str,
    /// Python's default is True.
    pub failed: bool,
    pub detail: &'a str,
    pub team: Option<&'a str>,
    /// "you" when empty.
    pub by: &'a str,
    pub address: Option<&'a str>,
    pub log: Option<&'a Path>,
    pub now: Option<Timestamp>,
    pub extra: Map<String, Value>,
}

/// The interactive loop. Returns the exit code.
pub fn run(claude_dir: &Path, codex_dir: &Path, hours: f64, use_herdr: bool) -> i32 {
    todo!("live.run")
}

/// Alerts -> suggestions, most urgent first.
pub fn suggest(
    agents: &[Agent],
    stalls: &[String],
    addr: &BTreeMap<String, String>,
    queue: &[Value],
    done: &[Value],
    now: Option<Timestamp>,
    send_less: &[Value],
) -> Vec<Value> {
    todo!("live.suggest")
}

/// `assieme watch`. Returns the exit code.
pub fn watch(claude_dir: &Path, codex_dir: &Path, hours: f64, use_herdr: bool) -> i32 {
    todo!("live.watch")
}

/// One round of `assieme watch`. Returns what it did, one line each.
pub fn watch_once(claude_dir: &Path, codex_dir: &Path, hours: f64, use_herdr: bool, announcing: bool) -> Vec<String> {
    todo!("live.watch_once")
}
