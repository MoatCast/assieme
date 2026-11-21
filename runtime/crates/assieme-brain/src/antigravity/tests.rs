use super::*;
use std::os::unix::fs::PermissionsExt;

/// A stand-in `agy` that prints what `plugin list --json` would.
fn fake_agy(dir: &Path, output: &str) -> PathBuf {
    let path = dir.join("agy");
    std::fs::write(&path, format!("#!/bin/sh\ncat <<'EOF'\n{output}\nEOF\n")).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    path
}

#[test]
fn the_plugin_names_this_program_and_asks_for_the_mcp_server() {
    let dir = tempfile::tempdir().unwrap();
    let plugin = build_plugin(Some(&dir.path().join("p/q"))).unwrap();
    let config: Value =
        serde_json::from_str(&std::fs::read_to_string(plugin.join("mcp_config.json")).unwrap()).unwrap();
    assert_eq!(config["mcpServers"]["assieme"], json!({"command": program(), "args": ["mcp"]}));
    let info: Value = serde_json::from_str(&std::fs::read_to_string(plugin.join("plugin.json")).unwrap()).unwrap();
    assert_eq!((info["name"].as_str(), info["version"].as_str()), (Some("assieme"), Some("1.0.0")));
}

#[test]
fn connected_needs_it_installed_switched_on_and_not_stale() {
    let dir = tempfile::tempdir().unwrap();
    let plugin = build_plugin(Some(&dir.path().join("plugin"))).unwrap();
    let listed =
        |name: &str, enabled: bool, path: &Path| json!([{"name": name, "enabled": enabled, "path": path}]).to_string();
    let agy = |out: &str| fake_agy(dir.path(), out);
    assert!(connected(&agy(&listed("assieme", true, &plugin))));
    assert!(!connected(&agy(&listed("assieme", false, &plugin))));
    assert!(!connected(&agy(&listed("other", true, &plugin))));
    assert!(!connected(&agy(&listed("assieme", true, &dir.path().join("gone")))));
    assert!(!connected(&agy("not json")));
    assert!(!connected(&agy("[1, null]")));
    assert!(!connected(&dir.path().join("no-agy")));
    // After an update moved `asm`, the copy there still names the old one.
    std::fs::write(
        plugin.join("mcp_config.json"),
        json!({"mcpServers": {"assieme": {"command": "/old/asm", "args": ["mcp"]}}}).to_string(),
    )
    .unwrap();
    assert!(!connected(&agy(&listed("assieme", true, &plugin))));
}
