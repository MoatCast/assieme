//! Port of `assieme/antigravity.py`, owned by session F (see OWNERS.md).
//!
//! Antigravity (`agy`) and Assieme's plugin for it.
//!
//! Antigravity loads MCP servers only from the person's own settings or from a plugin, so its agents get the team tools
//! through a small plugin Assieme builds here and installs only when the person says yes (Paper "v2 — Antigravity:
//! connect Assieme"). The plugin also shows in the person's own Antigravity sessions, where the tools read team status
//! and ask before changing anything. Hooks and permissions don't need it: they go per agent (see `launch`).
use crate::prelude::*;
use crate::setup::{SetupError, TIMEOUT};
use serde_json::json;
use std::process::Command;

const NAME: &str = "assieme";
const DESCRIPTION: &str =
    "Assieme's team tools for the agents it starts. In your own sessions they read team status and ask before changing anything.";

pub fn plugin_dir() -> PathBuf {
    crate::quiet::state_dir().join("agy-plugin")
}

/// The program the plugin starts: this `asm`. After an Assieme update that moved it, the copy Antigravity holds is stale.
fn program() -> String {
    std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "asm".into())
}

fn write_json(path: &Path, v: &Value) -> std::io::Result<()> {
    std::fs::write(path, serde_json::to_string_pretty(v).unwrap_or_default())
}

/// The plugin folder, pointing at this `asm` (Antigravity copies it when it installs it).
pub fn build_plugin(path: Option<&Path>) -> std::io::Result<PathBuf> {
    let path = path.map_or_else(plugin_dir, Path::to_path_buf);
    std::fs::create_dir_all(&path)?;
    write_json(&path.join("plugin.json"), &json!({"name": NAME, "description": DESCRIPTION, "version": "1.0.0"}))?;
    let config = json!({"mcpServers": {NAME: {"command": program(), "args": ["mcp"]}}});
    write_json(&path.join("mcp_config.json"), &config)?;
    Ok(path)
}

/// Installed, switched on, and starting this `asm` (after an Assieme update the copy there is stale).
pub fn connected(agy: &Path) -> bool {
    let mut cmd = Command::new(agy);
    cmd.args(["plugin", "list", "--json"]);
    let Ok(Some(out)) = crate::setup::run(cmd, None, TIMEOUT) else { return false };
    let listed = serde_json::from_slice::<Value>(&out.stdout).unwrap_or_else(|_| json!([]));
    let mine = listed.as_array().and_then(|l| l.iter().find(|p| p["name"] == NAME && p.is_object()));
    let Some(path) = mine.filter(|m| crate::pyrules::truthy(&m["enabled"])).and_then(|m| m["path"].as_str()) else { return false };
    let Ok(text) = std::fs::read_to_string(Path::new(path).join("mcp_config.json")) else { return false };
    serde_json::from_str::<Value>(&text).is_ok_and(|c| c["mcpServers"][NAME]["command"] == program())
}

/// Install the plugin in a setup terminal the person can watch: the shown command is what runs.
pub fn connect(rt: &Runtime) -> Result<Value, SetupError> {
    let Some(agy) = crate::setup::agent_path("agy") else {
        return Err(SetupError::Failed("Antigravity isn't installed yet.".into()));
    };
    let plugin = build_plugin(None).map_err(|e| SetupError::Failed(e.to_string()))?;
    let quote = |s: &Path| shlex::try_quote(&s.to_string_lossy()).map_or_else(|_| s.to_string_lossy().into_owned(), |q| q.into_owned());
    crate::setup::start(rt, "antigravity", "connect", &format!("{} plugin install {}", quote(&agy), quote(&plugin)))
}

#[cfg(test)]
mod tests;
