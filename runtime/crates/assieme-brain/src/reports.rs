//! Port of `assieme/reports.py`, owned by session A (see OWNERS.md).
//!
//! What agents say about themselves through the MCP server: stuck, or needing a decision.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const SHOWN: &[&str] = &["stuck", "need_decision"];

pub fn clear(team: &str, role: &str) {
    todo!("reports.clear")
}

/// The report that still holds for this terminal row, or None.
pub fn current(team: &str, role: &str, row: &Value) -> Option<Value> {
    todo!("reports.current")
}

/// A teammate's note is on its way to this agent.
pub fn noted(team: &str, role: &str) {
    todo!("reports.noted")
}

/// The turn in progress or just ended.
pub fn running_turn(row: &Value) -> Option<i64> {
    todo!("reports.running_turn")
}

pub fn save(
    team: &str,
    role: &str,
    state: &str,
    note: &str,
    now: Option<Timestamp>,
    turn: Option<i64>,
) -> Result<(), BrainError> {
    todo!("reports.save")
}
