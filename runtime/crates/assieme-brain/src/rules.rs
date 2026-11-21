//! Port of `assieme/rules.py`, owned by the foundation (see OWNERS.md).
//!
//! F4: say why a command was refused, which rule did it, and suggest a narrower rule. Suggestions only.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The deny rules that apply in a folder, as `rules::load` gives them.
pub type RulesFor = dyn Fn(&str) -> Vec<(String, String)> + Send + Sync;

/// Any Claude Code refusal wording.
pub static REFUSED: LazyLock<Regex> = LazyLock::new(|| todo!("rules.REFUSED"));

/// Refusal text -> (why, note). why names the source; note is the advice, or "".
pub fn explain(
    text: &str,
    tool: &str,
    command: &str,
    rules: &[(String, String)],
    path: Option<&str>,
) -> (String, String) {
    todo!("rules.explain")
}

/// Deny rules that apply in cwd -> [(rule, file)], project files first.
pub fn load(cwd: Option<&str>, user_settings: &Path) -> Vec<(String, String)> {
    todo!("rules.load")
}

/// Deny rules from one settings file given with --rules.
pub fn load_file(path: &Path) -> Vec<(String, String)> {
    todo!("rules.load_file")
}

/// {cwd: [(rule, file)]} for running Claude Code agents started with `--settings <file>`. Never fatal.
pub fn running_settings() -> BTreeMap<String, Vec<(String, String)>> {
    todo!("rules.running_settings")
}
