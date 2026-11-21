//! Port of `assieme/permissions.py`, owned by session D (see OWNERS.md).
//!
//! What each agent may do: three levels and the person's own rules, written as each agent's own settings.
#[allow(unused_imports)]
use crate::prelude::*;

pub const GEMINI_POLICY: &str = ".gemini/policies/assieme.toml";
pub const RULES_FILE: &str = ".codex/rules/assieme.rules";
