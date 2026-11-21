//! Port of `assieme/activity.py`, owned by session B (see OWNERS.md).
//!
//! Activity: the action log as rows in plain words, and Undo.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const MAX_ROWS: usize = 500;

/// The ids of entries an undo already took back.
pub fn undone(entries: &[Value]) -> BTreeSet<String> {
    todo!("activity._undone")
}

/// Step 9's merges: the Review object holds what an Undo needs, and logs its own row.
pub fn register_merge(review: Arc<Review>) {
    todo!("activity.register_merge")
}
