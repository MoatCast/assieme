//! Port of `assieme/answers.py`, owned by session B (see OWNERS.md).
//!
//! Two short suggested answers to an agent's plain-word question, from the person's own agent.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Suggested answers, asked of the person's own agent in the background and cached.
pub struct Answers {}

impl Default for Answers {
    fn default() -> Answers {
        Answers::new()
    }
}

impl Answers {
    pub fn new() -> Answers {
        todo!("answers.Answers.new")
    }

    /// The suggestion for `key`, or None while it is being fetched.
    pub fn get(&self, key: &str, question: &str) -> Option<Value> {
        todo!("answers.Answers.get")
    }
}
