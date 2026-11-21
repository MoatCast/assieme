//! Port of `assieme/musicians.py`, owned by session D (see OWNERS.md).
//!
//! Step 31: the instrument each agent plays. Chosen by role when the team is made, then saved with the role, so it never
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const INSTRUMENTS: &[&str] =
    &["piano", "reviewer", "violin", "cello", "bass", "timpani", "flute", "clarinet", "trumpet"];

/// Each role's instrument.
pub fn assign(roles: &[Role]) -> Vec<String> {
    todo!("musicians.assign")
}
