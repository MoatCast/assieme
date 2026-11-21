//! Port of `assieme/launch.py`, owned by session D (see OWNERS.md).
//!
//! Each agent's command line: its CLI, model, read-only settings, the status hook and the team tools.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Agent kind -> its program.
pub const CLIS: &[(&str, &str)] = &[
    ("claude", "claude"),
    ("codex", "codex"),
    ("gemini", "gemini"),
    ("opencode", "opencode"),
    ("cursor", "cursor-agent"),
    ("antigravity", "agy"),
];

pub const CURSOR_OURS: &str = ".cursor/assieme-permissions.json";

/// A model name's pattern.
pub const MODEL: &str = r"^[A-Za-z0-9][A-Za-z0-9._-]*$";

pub const NAMES: &[(&str, &str)] = &[
    ("claude", "Claude Code"),
    ("codex", "Codex"),
    ("gemini", "Gemini CLI"),
    ("opencode", "OpenCode"),
    ("cursor", "Cursor"),
    ("antigravity", "Antigravity"),
];

/// The problem with an agent's model or effort, in words, or None.
pub fn check_model_effort(agent: &str, model: Option<&str>, effort: Option<&str>) -> Option<String> {
    todo!("launch.check_model_effort")
}

/// Take Assieme's entries back out of the settings files written into a folder.
pub fn unmerge(cwd: &str) {
    todo!("launch.unmerge")
}
