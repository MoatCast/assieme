//! Port of `assieme/machine.py`, owned by session E (see OWNERS.md).
//!
//! The optional F6 sampler: free memory and load, once a minute from the brain's tick, in a small capped file.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Samples since then, each with `at` as a time.
pub fn read(since: Timestamp, file: Option<&Path>) -> Vec<Value> {
    todo!("machine.read")
}

/// Record one sample if the last is at least a minute old. Returns whether it did. Never fatal.
pub fn tick(now: Timestamp, file: Option<&Path>) -> bool {
    todo!("machine.tick")
}
