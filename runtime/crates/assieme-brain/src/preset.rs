//! Port of `assieme/preset.py`, owned by session D (see OWNERS.md).
//!
//! Team presets: a folder with team.toml and one Markdown brief per role (briefs use the core's template format).
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const AGENTS: &[&str] = &["claude", "codex", "gemini", "opencode", "cursor", "antigravity"];

#[derive(Debug, Clone, PartialEq)]
pub struct PresetError(pub String);

impl From<PresetError> for BrainError {
    fn from(e: PresetError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// One role of a saved team.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Role {
    pub name: String,
    pub agent: String,
    pub brief_path: String,
    pub brief: String,
    pub model: Option<String>,
    /// "branch" by default.
    pub checkout: String,
    pub parent: Option<String>,
    pub job: String,
    pub effort: Option<String>,
    pub group: Option<String>,
    pub kind: Option<String>,
    pub owns: Vec<String>,
    pub level: Option<String>,
    pub rules: Vec<Value>,
    pub container: bool,
    pub share_signin: bool,
    pub worktree: Option<String>,
    pub branch: Option<String>,
    pub resume: Option<String>,
    pub tools_off: Vec<String>,
    /// True by default.
    pub tools_new: bool,
    pub tools_known: Vec<String>,
    pub musician: Option<String>,
    pub cont: bool,
}

/// A saved team.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Team {
    pub name: String,
    pub project: String,
    pub practices: Map<String, Value>,
    pub roles: Vec<Role>,
    pub scripts: BTreeMap<String, String>,
}

/// Every key a choice may hold: group, rule and bigger-team ids.
pub fn keys() -> BTreeSet<String> {
    todo!("preset.keys")
}

pub fn load(folder: &Path, practices: bool) -> Result<Team, PresetError> {
    todo!("preset.load")
}

/// `practices.toml`, compiled into the binary, unless `path` names another.
pub fn practices(path: Option<&Path>) -> Value {
    todo!("preset.practices")
}

pub fn rule_on(chosen: &Map<String, Value>, rule_id: &str) -> bool {
    todo!("preset.rule_on")
}

/// The rules agents get: a rule is on when its group and itself are both on.
pub fn rules_on(chosen: &Map<String, Value>) -> Vec<Value> {
    todo!("preset.rules_on")
}
