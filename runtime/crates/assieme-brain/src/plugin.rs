//! Port of `assieme/plugin.py`, owned by session D (see OWNERS.md).
//!
//! Step 18a: Assieme packaged the way each app expects, written at install time because it must name this Mac's own
//! Assieme (the Claude app doesn't see a terminal's PATH):
//!
//! - one local marketplace, for Claude Code (.claude-plugin/marketplace.json) and Codex (.agents/plugins/marketplace.json)
//! - one plugin folder: the MCP server, a "Use Assieme" skill, Claude Code's ask hook (in its own file, so Codex, which
//!   reads hooks/hooks.json, doesn't run it) and Codex's per-tool "prompt" approval
//! - a Claude app extension (.mcpb: a zip holding manifest.json), listed there as a connector
//!
//! Every server entry sets CLIENT_ASKS: the client's own approval is the person's yes (see `mcpserver`).
use crate::prelude::*;
use serde_json::json;
use std::io::Write;

const NAME: &str = "assieme";
const DESCRIPTION: &str =
    "See and run your Assieme teams: design new ones and act on what needs you. Every change asks you first.";
const GUIDE: &str = include_str!("../../../../assieme/guide/team-design.md");

fn write(path: &Path, data: &str) -> Result<(), BrainError> {
    let at = |e: std::io::Error| BrainError::new(format!("Couldn't write {}: {e}.", path.display()));
    std::fs::create_dir_all(path.parent().unwrap_or(Path::new(".")))
        .and_then(|()| std::fs::write(path, data))
        .map_err(at)
}

/// Write the marketplace, plugin and extension into `dest` (replacing what was there) for this `asm`.
/// Returns `{market: the marketplace folder, extension: the .mcpb path}`.
pub fn build(dest: &Path) -> Result<Value, BrainError> {
    let exe = std::env::current_exe().map_or_else(|_| "asm".into(), |p| p.to_string_lossy().into_owned());
    let server = json!({"command": exe, "args": ["mcp"], "env": {crate::actions::CLIENT_ASKS: "1"}});
    let _ = std::fs::remove_dir_all(dest);
    let p = dest.join("plugin");
    let json_at = |path: PathBuf, data: Value| write(&path, &format!("{data:#}"));
    json_at(
        dest.join(".claude-plugin/marketplace.json"),
        json!({"name": NAME, "owner": {"name": "Assieme"}, "plugins": [{"name": NAME, "source": "./plugin", "description": DESCRIPTION}]}),
    )?;
    json_at(
        dest.join(".agents/plugins/marketplace.json"),
        json!({"name": NAME, "interface": {"displayName": "Assieme"},
            "plugins": [{"name": NAME, "source": {"source": "local", "path": "./plugin"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Developer Tools"}]}),
    )?;
    json_at(
        p.join(".claude-plugin/plugin.json"),
        json!({"name": NAME, "version": "1.0.0", "description": DESCRIPTION, "hooks": "./claude-hooks.json"}),
    )?;
    json_at(p.join(".mcp.json"), json!({"mcpServers": {NAME: server}}))?;
    let hook = format!("{} askhook", shlex::try_quote(&exe).map_or_else(|_| "asm".into(), |q| q.into_owned()));
    json_at(
        p.join("claude-hooks.json"),
        json!({"hooks": {"PreToolUse": [{"matcher": "mcp__.*assieme.*", "hooks": [{"type": "command", "command": hook}]}]}}),
    )?;
    json_at(
        p.join(".codex-plugin/plugin.json"),
        json!({"name": NAME, "version": "1.0.0", "description": DESCRIPTION, "mcpServers": "./codex-mcp.json", "author": {"name": "Assieme"}}),
    )?;
    let prompt: Map<String, Value> = crate::askhook::CHANGE_TOOLS
        .iter()
        .chain(&["app_action"])
        .map(|t| (t.to_string(), json!({"approval_mode": "prompt"})))
        .collect();
    let mut codex = server.clone();
    codex["default_tools_approval_mode"] = json!("approve");
    codex["tools"] = Value::Object(prompt);
    json_at(p.join("codex-mcp.json"), json!({"mcpServers": {NAME: codex}}))?;
    write(
        &p.join("skills/use-assieme/SKILL.md"),
        &format!(
            "---\nname: use-assieme\ndescription: How to see, design and run Assieme teams of Claude Code and Codex agents on this Mac. \
         Use when the person asks about their agents, their teams, or designing a new team.\n---\n\n{}\n",
            GUIDE.trim()
        ),
    )?;
    let extension = dest.join("assieme.mcpb");
    let manifest = json!({"manifest_version": "0.2", "name": NAME, "display_name": "Assieme", "version": "1.0.0",
        "description": DESCRIPTION, "author": {"name": "Assieme"},
        "server": {"type": "binary", "entry_point": exe, "mcp_config": server}, "tools_generated": true});
    let zipped = || -> zip::result::ZipResult<()> {
        let mut z = zip::ZipWriter::new(std::fs::File::create(&extension)?);
        z.start_file("manifest.json", zip::write::SimpleFileOptions::default())?;
        z.write_all(format!("{manifest:#}").as_bytes())?;
        z.finish().map(drop)
    };
    zipped().map_err(|e| BrainError::new(format!("Couldn't write {}: {e}.", extension.display())))?;
    Ok(json!({"market": dest, "extension": extension}))
}
