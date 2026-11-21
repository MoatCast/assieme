//! Port of `assieme/bringover.py`, owned by session F (see OWNERS.md).
//!
//! Bring your team over (step 30): agent work from other tools on this Mac, as a New team draft.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The New team draft: one agent per ticked workspace.
pub fn draft(id: &str, ticked: &[String]) -> Result<Value, BrainError> {
    todo!("bringover.draft")
}

/// `{projects, unreadable, problems, looked_for}`.
pub fn find(folder: Option<&str>) -> Value {
    todo!("bringover.find")
}

/// (roles to start, skipped).
pub fn not_running(roles: &[Value], tool: &str) -> (Vec<Value>, Vec<Value>) {
    todo!("bringover.not_running")
}

/// After a brought-over team is saved and starting.
pub fn started(
    team: &str,
    project: &str,
    roles: &[Value],
    cards: &[Value],
    scripts: &BTreeMap<String, String>,
    origin: &Value,
    remember: &dyn Fn(&str, &str),
) {
    todo!("bringover.started")
}

/// `{label, tool, rows, scripts}`.
pub fn workspaces(id: &str) -> Result<Value, BrainError> {
    todo!("bringover.workspaces")
}
