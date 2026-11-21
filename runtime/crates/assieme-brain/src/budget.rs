//! Port of `assieme/budget.py`, owned by session C (see OWNERS.md).
//!
//! Daily token limits for a team and for single agents. At a limit: pause and ask (the default), or only tell.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const TEAM: &str = "*";

/// The brain's tick: pause or tell at a limit. Returns what it logged.
pub fn check(
    team: &str,
    built: &Value,
    rt: &Runtime,
    now: Timestamp,
    announce: Option<&Notify>,
    log: Option<&Path>,
) -> Vec<Value> {
    todo!("budget.check")
}

/// Keep a message for a paused agent. False when it isn't paused.
pub fn hold(team: &str, role: &str, sender: &str, text: &str) -> bool {
    todo!("budget.hold")
}

pub fn resume(
    team: &str,
    scope: &str,
    add: i64,
    rt: &Runtime,
    now: Option<Timestamp>,
    by: &str,
    log: Option<&Path>,
) -> Value {
    todo!("budget.resume")
}

/// Save the limits (tokens a day; none or 0 removes one).
#[allow(clippy::too_many_arguments)]
pub fn set_limits(
    team: &str,
    limits: &Value,
    at_limit: &str,
    rt: &Runtime,
    used: &BTreeMap<String, i64>,
    total: i64,
    now: Option<Timestamp>,
    log: Option<&Path>,
) -> Result<Value, BrainError> {
    todo!("budget.set_limits")
}

/// Teams the tick has to look at.
pub fn teams() -> BTreeSet<String> {
    todo!("budget.teams")
}

/// What the Team tab shows. `used` is {role: tokens}.
pub fn view(team: &str, used: &BTreeMap<String, i64>, total: i64, now: Timestamp) -> Value {
    todo!("budget.view")
}
