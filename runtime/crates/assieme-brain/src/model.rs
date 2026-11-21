//! Port of `assieme/model.py`, owned by the foundation (see OWNERS.md).
//!
//! The data model every adapter produces: sessions made of turns, each turn opened by a wake.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const HUMAN: &str = "human";

/// Insertion-ordered counts, as Python's `collections.Counter`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Counter<K>(pub Vec<(K, i64)>);

/// Everything an agent did after one wake, until the next one.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Turn {
    pub sender: String,
    pub channel: String,
    pub calls: i64,
    pub tokens: i64,
    pub text: String,
    pub ts: Option<Timestamp>,
    pub changed: bool,
    pub branch: Option<String>,
}

/// F6: one call that ran heavy commands.
#[derive(Debug, Clone, PartialEq)]
pub struct Job {
    pub id: String,
    pub start: Timestamp,
    pub end: Option<Timestamp>,
    /// (kind, full suite?, label)
    pub runs: Vec<(String, bool, String)>,
    pub branch: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Session {
    pub agent: String,
    pub source: String,
    pub calls: i64,
    pub tokens: i64,
    pub output: i64,
    pub peak: i64,
    pub turns: Vec<Turn>,
    /// (label, why) -> count
    pub refusals: Counter<(String, String)>,
    /// why -> advice (F4)
    pub rule_notes: BTreeMap<String, String>,
    pub cwd: Option<String>,
    /// F5: (time, input tokens) per call
    pub samples: Vec<(Timestamp, i64)>,
    pub jobs: Vec<Job>,
    /// F7: (time, fingerprint, turn number, label)
    pub failures: Vec<(Timestamp, String, i64, String)>,
    /// F7: branch -> fix commits
    pub cycles: Counter<String>,
    /// F8: why -> seconds lost
    pub refusal_seconds: Counter<String>,
}

impl Session {
    pub fn wake(
        &mut self,
        sender: &str,
        channel: &str,
        text: &str,
        show_text: bool,
        ts: Option<Timestamp>,
        branch: Option<&str>,
    ) {
        todo!("model.Session.wake")
    }

    pub fn call(&mut self, input_tokens: i64, output_tokens: i64, ts: Option<Timestamp>) {
        todo!("model.Session.call")
    }
}

/// ISO-8601 string -> a time, or None. A time with no zone is UTC.
pub fn parse_ts(ts: &Value) -> Option<Timestamp> {
    todo!("model.parse_ts")
}
