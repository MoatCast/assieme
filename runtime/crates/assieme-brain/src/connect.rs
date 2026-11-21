//! Port of `assieme/connect.py`, owned by session E (see OWNERS.md).
//!
//! Settings → Your assistants (step 18a): Assieme installed into each app the way that app expects, and removed.
//!
//! - Claude Code: a plugin from a local marketplace Assieme writes (`plugin`), with its ask hook.
//! - The Claude app: an extension (.mcpb), opened so the app shows its own install window; listed there as a connector.
//! - Codex in the ChatGPT app: a plugin from the same marketplace, with per-tool approval.
//!
//! The old Connect button added plain `mcp` entries instead; installing removes them so an app never runs Assieme twice.
//! The Claude app rewrites its config file from memory while open (seen on 8 November 2026), so its old entry is only
//! removed with the app closed.
use crate::prelude::*;
use serde_json::json;
use std::process::Command;
use std::time::Duration;

const NAME: &str = "assieme";
const PLUGIN: &str = "assieme@assieme";
const TIMEOUT: Duration = Duration::from_secs(60);
const APPS: [(&str, &str, &str); 3] = [
    ("claude-code", "Claude Code", "plugin"),
    ("claude-app", "The Claude app", "connector"),
    ("codex", "Codex in the ChatGPT app", "plugin"),
];
const FINISH_IN_APP: &str = "Finish in the Claude app’s install window.";
const QUIT_APP: &str = "Quit the Claude app, then open Settings here again to remove its old Assieme entry.";
const REMOVE_IN_APP: &str = "Remove it in the Claude app: Settings → Extensions → Assieme.";

fn cli_of(app: &str) -> Option<&'static str> {
    match app {
        "claude-code" => Some("claude"),
        "codex" => Some("codex"),
        _ => None,
    }
}

fn support() -> PathBuf {
    PathBuf::from(crate::rules::home()).join("Library/Application Support/Claude")
}

fn load(path: &Path) -> Option<Map<String, Value>> {
    match crate::quiet::read_json(path, Value::Null) {
        Value::Object(o) => Some(o),
        _ => None,
    }
}

/// Whether it ran and succeeded; its stdout.
fn exec(args: &[&str]) -> Result<std::process::Output, String> {
    let mut cmd = Command::new(args[0]);
    cmd.args(&args[1..]);
    crate::setup::run(cmd, None, TIMEOUT).map_err(|e| e.to_string())?.ok_or_else(|| "it took too long".to_string())
}

fn cli(args: &[&str]) -> Result<String, String> {
    let out = exec(args)?;
    if out.status.success() {
        return Ok(String::from_utf8_lossy(&out.stdout).into());
    }
    let said =
        String::from_utf8_lossy(if out.stderr.is_empty() { &out.stdout } else { &out.stderr }).trim().to_string();
    Err(if said.is_empty() { "it failed".into() } else { said.chars().take(200).collect() })
}

/// A step that may already be done (a marketplace added before, nothing to remove).
fn quiet(args: &[&str]) -> bool {
    exec(args).is_ok_and(|o| o.status.success())
}

fn desktop_open() -> bool {
    quiet(&["pgrep", "-x", "Claude"])
}

fn installed(app: &str, exe: Option<&str>) -> bool {
    match (app, exe) {
        ("claude-code", Some(exe)) => cli(&[exe, "plugin", "list", "--json"])
            .ok()
            .and_then(|out| serde_json::from_str::<Vec<Value>>(&out).ok())
            .is_some_and(|plugins| plugins.iter().any(|p| p["id"] == PLUGIN)),
        // `codex plugin list` refuses to run while any configured marketplace is broken, so its own config is read
        ("codex", Some(_)) => std::fs::read_to_string(Path::new(&crate::rules::home()).join(".codex/config.toml"))
            .ok()
            .and_then(|t| t.parse::<toml::Table>().ok())
            .is_some_and(|t| t["plugins"][PLUGIN]["enabled"].as_bool() == Some(true)),
        ("claude-app", _) => load(&support().join("extensions-installations.json")).is_some_and(|f| {
            f.get("extensions").and_then(Value::as_object).is_some_and(|exts| {
                exts.iter().any(|(id, e)| id.starts_with("local.mcpb.") && e["manifest"]["name"] == NAME)
            })
        }),
        _ => false,
    }
}

fn desktop_config() -> PathBuf {
    support().join("claude_desktop_config.json")
}

/// The old Connect button's entry in the Claude app's config, removed only while the app is closed.
fn drop_old_app_entry() -> Result<Option<String>, String> {
    let Some(mut data) = load(&desktop_config()) else { return Ok(None) };
    if !data.get("mcpServers").and_then(Value::as_object).is_some_and(|s| s.contains_key(NAME)) {
        return Ok(None);
    }
    if desktop_open() {
        return Ok(Some(QUIT_APP.into()));
    }
    data["mcpServers"].as_object_mut().map(|s| s.remove(NAME));
    crate::quiet::write_json(&desktop_config(), &Value::Object(data)).map_err(|e| e.to_string())?;
    Ok(None)
}

fn status(problem: Option<(&str, String)>) -> Value {
    let rows = APPS.map(|(app, label, kind)| {
        let exe = cli_of(app).and_then(crate::setup::agent_path);
        let exe = exe.as_deref().and_then(Path::to_str);
        let available = if cli_of(app).is_some() { exe.is_some() } else { support().is_dir() };
        let installed = available && installed(app, exe);
        let mut problem = problem.as_ref().filter(|(a, _)| *a == app).map(|(_, p)| p.clone());
        if app == "claude-app" && installed && problem.is_none() {
            problem = drop_old_app_entry().unwrap_or(None);
        }
        let mut row = json!({"app": app, "label": label, "kind": kind, "available": available, "installed": installed});
        if let Some(p) = problem {
            row["problem"] = json!(p);
        }
        row
    });
    json!({"apps": rows})
}

fn check(params: &Value) -> Result<(&'static str, &'static str), BrainError> {
    let app = params["app"].as_str().unwrap_or_default();
    APPS.iter().find(|a| a.0 == app).map(|a| (a.0, a.1)).ok_or_else(|| BrainError::new(format!("No app named {app}.")))
}

fn install(app: &str) -> Result<Option<String>, String> {
    let market = crate::quiet::state_dir().join("assistants-plugin");
    let built = crate::plugin::build(&market).map_err(|e| e.message)?;
    let Some(name) = cli_of(app) else {
        if !support().is_dir() {
            return Ok(Some("The Claude app isn’t installed on this Mac.".into()));
        }
        let problem = drop_old_app_entry()?;
        cli(&["open", built["extension"].as_str().unwrap_or_default()])?; // the app's own install window
        return Ok(problem.or_else(|| (!installed(app, None)).then(|| FINISH_IN_APP.to_string())));
    };
    let Some(exe) = crate::setup::agent_path(name) else {
        return Ok(Some(format!(
            "{} isn’t installed on this Mac.",
            if app == "codex" { "Codex" } else { "Claude Code" }
        )));
    };
    let exe = exe.to_string_lossy();
    let scope: &[&str] = if app == "claude-code" { &["--scope", "user"] } else { &[] };
    if quiet(&[&exe, "mcp", "get", NAME]) {
        cli(&[&[&exe, "mcp", "remove"], scope, &[NAME]].concat())?; // the old Connect button's entry
    }
    quiet(&[&exe, "plugin", "marketplace", "add", &market.to_string_lossy()]); // already added: fine
    cli(&[&exe, "plugin", if app == "claude-code" { "install" } else { "add" }, PLUGIN])?;
    Ok(None)
}

fn remove(app: &str) -> Result<Option<String>, String> {
    let Some(name) = cli_of(app) else { return Ok(Some(REMOVE_IN_APP.into())) }; // the app owns its extensions
    if let Some(exe) = crate::setup::agent_path(name) {
        let exe = exe.to_string_lossy();
        cli(&[&exe, "plugin", if app == "claude-code" { "uninstall" } else { "remove" }, PLUGIN])?;
        quiet(&[&exe, "plugin", "marketplace", "remove", NAME]);
    }
    Ok(None)
}

/// `assistants.status`, `assistants.install`, `assistants.remove`.
pub fn handlers() -> Handlers {
    let act = |verb: &'static str, f: fn(&str) -> Result<Option<String>, String>| -> crate::server::Handler {
        Box::new(move |params, _| {
            let (app, label) = check(&params)?;
            let problem = f(app).unwrap_or_else(|e| Some(format!("{label} couldn’t {verb} it: {e}")));
            Ok(status(problem.map(|p| (app, p))))
        })
    };
    vec![
        ("assistants.status", Box::new(|_, _| Ok(status(None)))),
        ("assistants.install", act("install", install)),
        ("assistants.remove", act("remove", remove)),
    ]
}
