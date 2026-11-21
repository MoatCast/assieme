//! Port of `assieme/prompts.py`, owned by session B (see OWNERS.md).
//!
//! Prompts and questions an agent shows, recognised from its screen text.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// ["enter"] (keep the default) on one of FIRST_RUN's screens, else None.
pub fn first_run_keys(screen: &str) -> Option<Vec<&'static str>> {
    todo!("prompts.first_run_keys")
}

pub fn hook_review(screen: &str) -> bool {
    todo!("prompts.hook_review")
}

/// The keys that answer "yes" to a folder-trust prompt on screen, or None.
pub fn trust_keys(screen: &str) -> Option<Vec<&'static str>> {
    todo!("prompts.trust_keys")
}
