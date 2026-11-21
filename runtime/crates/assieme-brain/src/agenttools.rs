//! Port of `assieme/agenttools.py`, owned by session D (see OWNERS.md).
//!
//! Step 32: the brain methods for one agent's skills, plugins and MCP servers.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub fn find(kind: &str, project: Option<&str>, own_copy: bool) -> Value {
    todo!("agenttools.find")
}

/// What Agent details' picker needs: this agent's kind's items, its saved choice and its role's suggestion.
pub fn for_agent(teams: &Path, team_name: &str, role_name: &str) -> Result<Value, BrainError> {
    todo!("agenttools.for_agent")
}

/// Stop the agent and start it again with its saved choice.
pub fn restart(teams: &Path, rt: &Runtime, team_name: &str, role_name: &str) -> Result<Value, BrainError> {
    todo!("agenttools.restart")
}

/// Save one agent's choice. It applies the next time the agent starts. Logged with an Undo.
pub fn set_tools(
    teams: &Path,
    team_name: &str,
    role_name: &str,
    tools_off: &[String],
    tools_new: bool,
) -> Result<Value, BrainError> {
    todo!("agenttools.set_tools")
}

pub fn suggest(role_kind: &str, kind: &str, project: Option<&str>, own_copy: bool) -> Value {
    todo!("agenttools.suggest")
}

/// Undo of a `tools` Activity row.
pub fn undo(e: &Value) -> Result<(), BrainError> {
    todo!("agenttools.undo")
}
