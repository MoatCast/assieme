//! Port of `assieme/ask.py`, owned by session G (see OWNERS.md).
//!
//! Asking the person's own agent a one-off question: Claude Code first, else Codex, read-only, in a folder.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The agent could not answer, in words; `missing` when no agent is installed.
#[derive(Debug, Clone, PartialEq)]
pub struct AskError {
    pub message: String,
    pub missing: bool,
}

impl From<AskError> for BrainError {
    fn from(e: AskError) -> BrainError {
        BrainError::new(e.message)
    }
}

/// The agent's answer text (Claude's --output-format json wrapper removed).
pub fn ask_agent(
    prompt: &str,
    cwd: &str,
    timeout_s: f64,
    timeout_label: Option<&str>,
    tools: bool,
) -> Result<String, AskError> {
    todo!("ask.ask_agent")
}

/// The JSON object from the first `{` to the last `}` of a reply.
pub fn first_json(text: &str) -> Option<Map<String, Value>> {
    todo!("ask.first_json")
}

/// The agents installed on this machine, from `preset::AGENTS`.
pub fn installed() -> Vec<String> {
    todo!("ask.installed")
}
