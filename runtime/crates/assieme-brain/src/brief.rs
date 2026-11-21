//! Port of `assieme/brief.py`, owned by session D (see OWNERS.md).
//!
//! The shared team brief: one small file per team with the goal, the person's rules and the decisions they made.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// One decision. `by` is who wrote it down.
pub fn add(
    team: &str,
    role: &str,
    question: &str,
    answer: &str,
    by: &str,
    now: Option<Timestamp>,
) -> Result<Option<Value>, BrainError> {
    todo!("brief.add")
}

/// `{goal, rules, decisions}`.
pub fn load(team: &str) -> Value {
    todo!("brief.load")
}

pub fn remove(team: &str, id: &str) -> Result<Value, BrainError> {
    todo!("brief.remove")
}

/// Set the goal, the rules or both; None leaves that part as it is.
pub fn save(team: &str, goal: Option<&str>, rules: Option<&str>) -> Result<Value, BrainError> {
    todo!("brief.save")
}

/// What agents read, or "" while the brief is empty.
pub fn text(team: &str, goal: bool) -> String {
    todo!("brief.text")
}
