//! Port of `assieme/limits.py`, owned by session A (see OWNERS.md).
//!
//! Plan limits, read from each agent kind's own logs: how much of the plan's window is used, and when it comes back.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// `{kind: {used, reached, resets_at}}`; a kind with no data is left out.
pub fn read(claude_dir: &Path, codex_dir: &Path, now: Timestamp) -> Result<Value, BrainError> {
    todo!("limits.read")
}
