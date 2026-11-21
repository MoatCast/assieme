//! Port of `assieme/team.py`, owned by session D (see OWNERS.md).
//!
//! Start, list and stop a team of agents from a preset, through the runtime (Assieme's own core, `asm`).
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const TEAM: &str = "assieme.team";
pub const ROLE: &str = "assieme.role";
pub const BRIEFED: &str = "assieme.briefed";
pub const KIND: &str = "assieme.kind";

#[derive(Debug, Clone, PartialEq)]
pub struct TeamError(pub String);

impl From<TeamError> for BrainError {
    fn from(e: TeamError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// One role's start.
#[derive(Debug, Clone, PartialEq)]
pub struct Started {
    pub role: String,
    pub address: Option<String>,
    /// running, needs-you, not-reporting, exited, already-running
    pub state: String,
    pub note: String,
}

/// Start a team's roles (only `only` when given); `ask` answers a yes-or-no question.
pub fn start(
    tm: &Team,
    task: Option<&str>,
    rt: &Runtime,
    ask: &dyn Fn(&str) -> bool,
    only: Option<&str>,
) -> Result<Vec<Started>, BrainError> {
    todo!("team.start")
}

/// The terminals of a team (all teams when None).
pub fn status(rt: &Runtime, name: Option<&str>) -> Result<Vec<Value>, RuntimeError> {
    todo!("team.status")
}

/// Stop a team's live terminals. Returns how many.
pub fn stop(rt: &Runtime, name: &str) -> Result<usize, RuntimeError> {
    todo!("team.stop")
}
