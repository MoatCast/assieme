//! Port of `assieme/plugin.py`, owned by session D (see OWNERS.md).
//!
//! Step 18a: Assieme packaged the way each app expects, written at install time because it must name this Mac's own
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Write the marketplace, plugin and extension into `dest`.
pub fn build(dest: &Path) -> Result<Value, BrainError> {
    todo!("plugin.build")
}
