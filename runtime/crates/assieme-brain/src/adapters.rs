//! Port of `assieme/adapters.py`, owned by session A (see OWNERS.md).
//!
//! Adapters: turn one tool's local logs into Sessions. Each takes (root, since, show_text) and yields Sessions.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The JSON objects of a JSONL file, skipping lines that are not objects.
pub fn rows(path: &Path) -> Vec<Map<String, Value>> {
    todo!("adapters._rows")
}

/// User content -> its typed text, or None when it only carries tool results.
pub fn text_of(content: &Value) -> Option<String> {
    todo!("adapters._text_of")
}

/// `{"ts", "box", "cpu", "rate", "usd_per_hour"}` rows since then.
pub fn box_metrics(path: &Path, since: Timestamp) -> Vec<Value> {
    todo!("adapters.box_metrics")
}

pub fn box_path() -> PathBuf {
    todo!("adapters.box_path")
}

pub fn claude_code(
    root: &Path,
    since: Timestamp,
    show_text: bool,
    user_settings: Option<&Path>,
    cwd_rules: Option<&RulesFor>,
    extra_rules: &[(String, String)],
) -> Vec<Session> {
    todo!("adapters.claude_code")
}

pub fn codex(root: &Path, since: Timestamp, show_text: bool) -> Vec<Session> {
    todo!("adapters.codex")
}

pub fn gemini(root: &Path, since: Timestamp, show_text: bool) -> Vec<Session> {
    todo!("adapters.gemini")
}

pub fn generic(path: &Path, since: Timestamp, show_text: bool) -> Vec<Session> {
    todo!("adapters.generic")
}

pub fn opencode(db: &Path, since: Timestamp, show_text: bool) -> Vec<Session> {
    todo!("adapters.opencode")
}

/// The program a shell command runs. Never a leading `API_KEY=...`: that can be a secret.
pub fn program(command: &str) -> String {
    todo!("adapters.program")
}
