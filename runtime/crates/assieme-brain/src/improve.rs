//! Port of `assieme/improve.py`, owned by session F (see OWNERS.md).
//!
//! Improve this team: a team started outside Assieme, compared with the recommended setup.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Give each chosen finding's rules to its agents with their next turn.
pub fn apply(
    brain: &Brain,
    team_name: &str,
    ids: &[String],
    now: Option<Timestamp>,
    log: Option<&Path>,
    terminals: Option<&[Value]>,
) -> Result<Value, BrainError> {
    todo!("improve.apply")
}

/// `{team, found, findings, in_place}`. `terminals`: herdr's terminals for this team, read when None.
pub fn check(
    brain: &Brain,
    team_name: &str,
    now: Option<Timestamp>,
    log: Option<&Path>,
    terminals: Option<&[Value]>,
) -> Result<Value, BrainError> {
    todo!("improve.check")
}

/// A note still held is taken back; one already delivered is followed by a note that it no longer applies.
pub fn undo(e: &Value, rt: &Runtime) -> Result<(), BrainError> {
    todo!("improve.undo")
}
