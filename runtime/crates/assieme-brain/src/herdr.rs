//! Port of `assieme/herdr.py`, owned by session F (see OWNERS.md).
//!
//! Import from herdr: a running herdr team, a saved herdr workspace or a launch script, as a New team draft.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The New team draft for one source from `find`, or `script:<path>`.
pub fn draft(source: &str) -> Result<Value, BrainError> {
    todo!("herdr.draft")
}

/// Every herdr team on this Mac that can be imported.
pub fn find() -> Value {
    todo!("herdr.find")
}
