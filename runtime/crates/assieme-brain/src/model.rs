//! Port of `assieme/model.py`, owned by the foundation (see OWNERS.md).
//!
//! The data model every adapter produces: sessions made of turns, each turn opened by a wake.
use crate::pyrules;
use jiff::Timestamp;
use serde_json::Value;
use std::collections::BTreeMap;

pub const HUMAN: &str = "human";

/// Insertion-ordered counts, as Python's `collections.Counter`.
#[derive(Debug, Clone, PartialEq)]
pub struct Counter<K>(pub Vec<(K, i64)>);

impl<K> Default for Counter<K> {
    fn default() -> Self {
        Counter(vec![])
    }
}

impl<K: PartialEq> Counter<K> {
    /// `counter[key] += n`.
    pub fn add(&mut self, key: K, n: i64) {
        match self.0.iter_mut().find(|(k, _)| *k == key) {
            Some((_, v)) => *v += n,
            None => self.0.push((key, n)),
        }
    }

    pub fn get(&self, key: &K) -> i64 {
        self.0.iter().find(|(k, _)| k == key).map_or(0, |(_, v)| *v)
    }

    /// Most common first; ties keep the order they were first counted, as Python's.
    pub fn most_common(&self) -> Vec<&(K, i64)> {
        let mut out: Vec<_> = self.0.iter().collect();
        out.sort_by_key(|(_, v)| std::cmp::Reverse(*v));
        out
    }
}

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
        let text = if show_text { pyrules::split(text).join(" ").chars().take(80).collect() } else { String::new() };
        self.turns.push(Turn {
            sender: sender.into(),
            channel: channel.into(),
            text,
            ts,
            branch: branch.map(Into::into),
            ..Turn::default()
        });
    }

    pub fn call(&mut self, input_tokens: i64, output_tokens: i64, ts: Option<Timestamp>) {
        if let Some(ts) = ts {
            self.samples.push((ts, input_tokens));
        }
        self.calls += 1;
        self.tokens += input_tokens;
        self.output += output_tokens;
        self.peak = self.peak.max(input_tokens);
        if let Some(turn) = self.turns.last_mut() {
            turn.calls += 1;
            turn.tokens += input_tokens;
        }
    }
}

/// ISO-8601 string -> a time, or None. A time with no zone is UTC.
pub fn parse_ts(ts: &Value) -> Option<Timestamp> {
    pyrules::fromisoformat(&ts.as_str()?.replace('Z', "+00:00"))
}
