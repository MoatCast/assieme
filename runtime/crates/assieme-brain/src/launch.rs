//! Port of `assieme/launch.py`, owned by session D (see OWNERS.md).
//!
//! Each agent's command line: its CLI, model, read-only settings, the status hook and the team tools.
//!
//! The brief is not on the command line: it is sent after the agent's first turn ends.
//!
//! What the permission, tool and container modules (session D2) give a command line comes through [`Policy`]: those
//! modules had no declared functions when this was written. `policy()` names the one place to wire them.
use crate::prelude::*;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::sync::mpsc;
use std::time::Duration;

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

/// Thinking effort each CLI accepts (claude --effort; codex model_reasoning_effort). Codex models list their own.
pub fn efforts(agent: &str) -> &'static [&'static str] {
    match agent {
        "claude" | "antigravity" => &["low", "medium", "high", "xhigh", "max"],
        "codex" => &["low", "medium", "high", "xhigh", "max", "ultra"],
        _ => &[], // no thinking-effort flag
    }
}

pub fn cli_of(agent: &str) -> Option<&'static str> {
    CLIS.iter().find(|(k, _)| *k == agent).map(|(_, v)| *v)
}

pub fn name_of(agent: &str) -> String {
    NAMES.iter().find(|(k, _)| *k == agent).map_or_else(|| agent.to_string(), |(_, v)| v.to_string())
}

static MODEL_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(MODEL).expect("a fixed pattern"));

/// The problem with an agent's model or effort, in words, or None.
pub fn check_model_effort(agent: &str, model: Option<&str>, effort: Option<&str>) -> Option<String> {
    if model.is_some_and(|m| !m.is_empty() && !MODEL_RE.is_match(m)) {
        return Some("Model names use letters, digits, dots and dashes.".into());
    }
    match effort {
        Some(e) if !e.is_empty() && !efforts(agent).contains(&e) => {
            Some(format!("{} has no thinking effort \"{e}\".", name_of(agent)))
        }
        _ => None,
    }
}

/// What session D2's modules give a command line, by the names they have in Python. Until they are declared, the
/// brain wires them in [`Wired`].
pub trait Policy: Send + Sync {
    /// `permissions.LEVELS`.
    fn levels(&self) -> Vec<String>;
    fn check_owns(&self, owns: &[String]) -> Option<String>;
    fn check_rules(&self, rules: &[Value]) -> Option<String>;
    /// `permissions.check_rule`: the validated program, or the problem.
    fn check_rule(&self, decision: Option<&Value>, program: Option<&Value>) -> Result<Vec<String>, String>;
    /// `permissions.table`.
    fn table(&self) -> Value;
    /// `permissions.claude`: (allow, deny).
    fn claude(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> (Vec<String>, Vec<String>);
    fn codex_args(&self, role: &Role) -> Vec<String>;
    fn write_codex_rules(&self, cwd: &str, roles: &[&Role]);
    /// The policy file's path, or None.
    fn write_gemini_policy(&self, cwd: &str, role: &Role, test: Option<Vec<String>>) -> Option<String>;
    fn opencode(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value>;
    fn cursor(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value>;
    fn antigravity(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value>;
    /// `tools.off`: the ids turned off for this role among these items.
    fn tools_off(&self, role: &Role, items: &[Value]) -> BTreeSet<String>;
    fn skill_paths(&self, kind: &str, cwd: &str) -> BTreeMap<String, String>;
    fn extension_servers(&self) -> BTreeMap<String, Vec<String>>;
    /// `container.run_argv`.
    fn container_run_argv(
        &self,
        role: &Role,
        argv: &[String],
        env: &BTreeMap<String, String>,
        cwd: &str,
        name: &str,
        bridge: Option<&str>,
    ) -> Vec<String>;
    /// `review.test_command`: the project's test command, or None.
    fn test_command(&self, cwd: &str) -> Option<Vec<String>>;
    /// `container.problem`: why a container cannot run this agent, or None.
    fn container_problem(&self, agent: &str) -> Option<String>;
}

/// The wiring to session D2's modules. Each body becomes a call to the function named in it.
pub struct Wired;

#[allow(clippy::todo, unused_variables)]
impl Policy for Wired {
    fn levels(&self) -> Vec<String> {
        todo!("permissions.LEVELS")
    }
    fn check_owns(&self, owns: &[String]) -> Option<String> {
        todo!("permissions.check_owns")
    }
    fn check_rules(&self, rules: &[Value]) -> Option<String> {
        todo!("permissions.check_rules")
    }
    fn check_rule(&self, decision: Option<&Value>, program: Option<&Value>) -> Result<Vec<String>, String> {
        todo!("permissions.check_rule")
    }
    fn table(&self) -> Value {
        todo!("permissions.table")
    }
    fn claude(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> (Vec<String>, Vec<String>) {
        todo!("permissions.claude")
    }
    fn codex_args(&self, role: &Role) -> Vec<String> {
        todo!("permissions.codex_args")
    }
    fn write_codex_rules(&self, cwd: &str, roles: &[&Role]) {
        todo!("permissions.write_codex_rules")
    }
    fn write_gemini_policy(&self, cwd: &str, role: &Role, test: Option<Vec<String>>) -> Option<String> {
        todo!("permissions.write_gemini_policy")
    }
    fn opencode(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value> {
        todo!("permissions.opencode")
    }
    fn cursor(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value> {
        todo!("permissions.cursor")
    }
    fn antigravity(&self, role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value> {
        todo!("permissions.antigravity")
    }
    fn tools_off(&self, role: &Role, items: &[Value]) -> BTreeSet<String> {
        todo!("tools.off")
    }
    fn skill_paths(&self, kind: &str, cwd: &str) -> BTreeMap<String, String> {
        todo!("tools.skill_paths")
    }
    fn extension_servers(&self) -> BTreeMap<String, Vec<String>> {
        todo!("tools.extension_servers")
    }
    fn container_run_argv(
        &self,
        role: &Role,
        argv: &[String],
        env: &BTreeMap<String, String>,
        cwd: &str,
        name: &str,
        bridge: Option<&str>,
    ) -> Vec<String> {
        todo!("container.run_argv")
    }
    fn test_command(&self, cwd: &str) -> Option<Vec<String>> {
        crate::review::test_command(Path::new(cwd))
    }
    fn container_problem(&self, agent: &str) -> Option<String> {
        todo!("container.problem")
    }
}

/// Nothing turned on and nothing refused: what this module's own tests run against.
#[cfg(test)]
pub struct Plain;

#[cfg(test)]
impl Policy for Plain {
    fn levels(&self) -> Vec<String> {
        ["safe", "read-only", "trusted"].map(String::from).to_vec()
    }
    fn check_owns(&self, _: &[String]) -> Option<String> {
        None
    }
    fn check_rules(&self, _: &[Value]) -> Option<String> {
        None
    }
    fn check_rule(&self, _: Option<&Value>, program: Option<&Value>) -> Result<Vec<String>, String> {
        Ok(serde_json::from_value(program.cloned().unwrap_or_default()).unwrap_or_default())
    }
    fn table(&self) -> Value {
        json!({})
    }
    fn claude(&self, _: &Role, _: &str, _: Option<Vec<String>>) -> (Vec<String>, Vec<String>) {
        (vec![], vec![])
    }
    fn codex_args(&self, _: &Role) -> Vec<String> {
        vec![]
    }
    fn write_codex_rules(&self, _: &str, _: &[&Role]) {}
    fn write_gemini_policy(&self, _: &str, _: &Role, _: Option<Vec<String>>) -> Option<String> {
        None
    }
    fn opencode(&self, _: &Role, _: &str, _: Option<Vec<String>>) -> Option<Value> {
        None
    }
    fn cursor(&self, _: &Role, _: &str, _: Option<Vec<String>>) -> Option<Value> {
        None
    }
    fn antigravity(&self, _: &Role, _: &str, _: Option<Vec<String>>) -> Option<Value> {
        None
    }
    fn tools_off(&self, _: &Role, _: &[Value]) -> BTreeSet<String> {
        BTreeSet::new()
    }
    fn skill_paths(&self, _: &str, _: &str) -> BTreeMap<String, String> {
        BTreeMap::new()
    }
    fn extension_servers(&self) -> BTreeMap<String, Vec<String>> {
        BTreeMap::new()
    }
    fn container_run_argv(
        &self,
        _: &Role,
        argv: &[String],
        env: &BTreeMap<String, String>,
        _: &str,
        name: &str,
        _: Option<&str>,
    ) -> Vec<String> {
        let mut out = vec!["docker".to_string(), "run".to_string(), name.to_string()];
        out.extend(env.iter().map(|(k, v)| format!("{k}={v}")));
        out.extend(argv.iter().cloned());
        out
    }
    fn test_command(&self, _: &str) -> Option<Vec<String>> {
        None
    }
    fn container_problem(&self, _: &str) -> Option<String> {
        None
    }
}

#[cfg(not(test))]
pub fn policy() -> &'static dyn Policy {
    &Wired
}

#[cfg(test)]
pub fn policy() -> &'static dyn Policy {
    &Plain
}

type Which<'a> = &'a dyn Fn(&str) -> Option<PathBuf>;

/// Programs that must be on this Mac: a container brings its own agent.
pub fn missing_clis_with(roles: &[Role], which: Which) -> Vec<String> {
    let set: BTreeSet<String> = roles
        .iter()
        .filter(|r| !r.container)
        .filter_map(|r| cli_of(&r.agent))
        .filter(|cli| which(cli).is_none())
        .map(String::from)
        .collect();
    set.into_iter().collect()
}

pub fn missing_clis(roles: &[Role]) -> Vec<String> {
    missing_clis_with(roles, &crate::setup::agent_path)
}

/// Agent event -> status. Not Claude Code's Notification: it also fires after a minute of plain idling.
const HOOKS: [(&str, &str); 4] =
    [("SessionStart", "idle"), ("UserPromptSubmit", "working"), ("Stop", "idle"), ("PermissionRequest", "blocked")];

/// The program that runs hooks and the team tools: this one, unless a container's own is named.
fn asm_exe(exe: Option<&str>) -> String {
    exe.map(String::from).unwrap_or_else(|| {
        std::env::current_exe().map_or_else(|_| "asm".into(), |p| p.to_string_lossy().into_owned())
    })
}

/// `exe` runs it: this program on the Mac, the image's own in a container.
pub fn hook_command(state: &str, exe: Option<&str>) -> String {
    format!("{} hook {state}", shlex::try_quote(&asm_exe(exe)).map_or_else(|_| "asm".into(), |q| q.into_owned()))
}

/// What marks an entry in an agent's settings as Assieme's own.
static OURS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"hook (idle|working|blocked)\b").expect("a fixed pattern"));

fn is_ours(entry: &Value) -> bool {
    OURS.is_match(&entry.to_string())
}

fn mcp(exe: Option<&str>) -> Value {
    json!({"command": asm_exe(exe), "args": ["mcp"]})
}

fn test_command(cwd: Option<&str>) -> Option<Vec<String>> {
    cwd.and_then(|c| policy().test_command(c))
}

fn limits(role: &Role, cwd: Option<&str>) -> BTreeSet<String> {
    // Step 32: the ids turned off for this agent, as `tools` reads them from what its kind has installed. Locked
    // items are never in it; a role that never chose anything gets nothing off.
    if role.tools_off.is_empty() && role.tools_new {
        return BTreeSet::new();
    }
    let items = tool_items(&role.agent, cwd, own(role));
    policy().tools_off(role, &items)
}

fn tool_items(kind: &str, cwd: Option<&str>, own_copy: bool) -> Vec<Value> {
    crate::agenttools::find(kind, cwd, own_copy)["items"].as_array().cloned().unwrap_or_default()
}

fn off_of(off: &BTreeSet<String>, kind: &str) -> Vec<String> {
    let prefix = format!("{kind}:");
    off.iter().filter_map(|i| i.strip_prefix(&prefix)).map(String::from).collect() // sorted: a BTreeSet
}

/// The agent works in its own copy, so settings files in its folder are its alone.
fn own(role: &Role) -> bool {
    role.checkout == "branch" && role.worktree.is_none()
}

fn strings<S: AsRef<str>>(v: &[S]) -> Vec<String> {
    v.iter().map(|s| s.as_ref().to_string()).collect()
}

fn claude(role: &Role, cwd: Option<&str>, exe: Option<&str>) -> Vec<String> {
    // Its own team's message and report tools: asking the person about each one would defeat them.
    let test = if role.level.is_some() { test_command(cwd) } else { None };
    let (allow, deny) = policy().claude(role, cwd.unwrap_or(""), test);
    let allow: Vec<String> = std::iter::once("mcp__assieme".to_string()).chain(allow).collect();
    let mut perms = json!({ "allow": allow });
    if !deny.is_empty() {
        perms["deny"] = json!(deny);
    }
    let hooks: Map<String, Value> = HOOKS
        .iter()
        .map(|(e, s)| (e.to_string(), json!([{"hooks": [{"type": "command", "command": hook_command(s, exe)}]}])))
        .collect();
    let mut settings = json!({"hooks": hooks, "permissions": perms});
    let off = limits(role, cwd);
    if !off_of(&off, "server").is_empty() {
        // removes the server and its tools for this session; no server settings are copied
        settings["deniedMcpServers"] = off_of(&off, "server").iter().map(|n| json!({"serverName": n})).collect();
    }
    if !off_of(&off, "plugin").is_empty() {
        settings["enabledPlugins"] = off_of(&off, "plugin").iter().map(|n| (n.clone(), json!(false))).collect::<Map<_, _>>().into();
    }
    let mut a = strings(&["claude", "--settings"]);
    a.push(settings.to_string());
    a.push("--mcp-config".into());
    a.push(json!({"mcpServers": {"assieme": mcp(exe)}}).to_string());
    if let Some(m) = &role.model {
        a.extend(["--model".into(), m.clone()]);
    }
    if let Some(e) = &role.effort {
        a.extend(["--effort".into(), e.clone()]);
    }
    if role.level.is_some() {
        // "anything else asks you first": not the person's auto mode or a skip-prompts default
        a.extend(strings(&["--permission-mode", "default"]));
    }
    if role.checkout == "read-only" || role.level.as_deref() == Some("read-only") {
        a.extend(strings(&["--disallowedTools", "Edit", "Write", "NotebookEdit"]));
    }
    if let Some(r) = resume(role) {
        // brought over (step 30): its first start picks up the conversation it had
        a.extend(["--resume".into(), r]);
    } else if role.cont {
        a.push("--continue".into()); // restarted from its details: its last conversation in this folder
    }
    if off.contains("skill:*") {
        a.push("--disable-slash-commands".into()); // Claude Code turns skills off only all together
    }
    a
}

/// The conversation to pick up: only in the folder it was had in. A copy made because that folder is gone starts
/// fresh, as the agents find their conversations by folder.
fn resume(role: &Role) -> Option<String> {
    let r = role.resume.as_ref().filter(|r| !r.is_empty())?;
    match &role.worktree {
        Some(w) if !w.is_empty() && !Path::new(w).is_dir() => None,
        _ => Some(r.clone()),
    }
}

const CODEX_EVENTS: [(&str, &str); 4] = [
    ("SessionStart", "session_start"),
    ("UserPromptSubmit", "user_prompt_submit"),
    ("Stop", "stop"),
    ("PermissionRequest", "permission_request"),
];

/// Codex's trust fingerprint for one command hook: sha256 of its normalized identity as sorted, compact JSON.
pub fn codex_hook_hash(event: &str, command: &str) -> String {
    let ident = json!({"event_name": event, "hooks": [{"type": "command", "command": command, "timeout": 600, "async": false}]});
    let digest = Sha256::digest(ident.to_string()); // serde_json writes keys sorted and compact
    format!("sha256:{}", digest.iter().map(|b| format!("{b:02x}")).collect::<String>())
}

fn toml_key(name: &str) -> String {
    if !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        name.to_string()
    } else {
        json!(name).to_string()
    }
}

fn codex(role: &Role, exe: Option<&str>, cwd: Option<&str>) -> Vec<String> {
    let mut a = strings(&["codex"]);
    match resume(role) {
        Some(r) => a.extend(["resume".into(), r]),
        None if role.cont => a.extend(strings(&["resume", "--last"])),
        None => {}
    }
    let mut c = |v: String| a.extend(["-c".to_string(), v]);
    c("check_for_update_on_startup=false".into()); // its update screen would stop the start-up
    // Its sandbox keeps .git read-only, so each commit is a request to step outside it: the person answers, in Assieme.
    c("approval_policy=\"on-request\"".into());
    c("approvals_reviewer=\"user\"".into());
    let mut trust = vec![];
    for ((e, s), (_, key)) in HOOKS.iter().zip(CODEX_EVENTS) {
        // A JSON string is a valid TOML basic string.
        c(format!("hooks.{e}=[{{hooks=[{{type=\"command\",command={}}}]}}]", json!(hook_command(s, exe))));
        let key = json!(format!("/<session-flags>/config.toml:{key}:0:0"));
        let hash = json!(codex_hook_hash(e, &hook_command(s, exe)));
        trust.push(format!("{key}={{trusted_hash={hash}}}"));
    }
    // Trust these four hooks for this run only, so a first Codex team doesn't stop at "Hooks need review". Any
    // other hook (the person's, a project's, a plugin's) still goes through Codex's own review.
    c(format!("hooks.state={{{}}}", trust.join(",")));
    // Codex hands MCP servers a short list of variables: these say which agent this is.
    let m = mcp(exe);
    c(format!("mcp_servers.assieme.command={}", m["command"]));
    c(format!("mcp_servers.assieme.args={}", m["args"]));
    c("mcp_servers.assieme.env_vars=[\"ASM_SESSION\",\"ASM_TERMINAL_ID\",\"XDG_STATE_HOME\",\"ASSIEME_BRIDGE\"]".into());
    c("mcp_servers.assieme.default_tools_approval_mode=\"approve\"".into());
    if let Some(m) = &role.model {
        a.extend(["-m".into(), m.clone()]);
    }
    if let Some(e) = &role.effort {
        a.extend(["-c".into(), format!("model_reasoning_effort={}", json!(e))]);
    }
    let off = cwd.map_or_else(BTreeSet::new, |c| limits(role, Some(c)));
    for n in off_of(&off, "server") {
        a.extend(["-c".into(), format!("mcp_servers.{}.enabled=false", toml_key(&n))]);
    }
    let paths = match cwd {
        Some(c) if !off_of(&off, "skill").is_empty() => policy().skill_paths("codex", c),
        _ => BTreeMap::new(),
    };
    let skills: Vec<String> = off
        .iter()
        .filter_map(|i| paths.get(i).map(|p| format!("{{path={},enabled=false}}", json!(p))))
        .collect();
    if !skills.is_empty() {
        a.extend(["-c".into(), format!("skills.config=[{}]", skills.join(","))]);
    }
    a.extend(policy().codex_args(role)); // its program rules are a file in its worktree (team writes it)
    if role.checkout == "read-only" && !a.iter().any(|x| x == "--sandbox") {
        a.extend(strings(&["--sandbox", "read-only"]));
    }
    a
}

fn read_json_object(path: &Path) -> Result<Option<Map<String, Value>>, ()> {
    match std::fs::read(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(()),
        Ok(b) => match serde_json::from_slice::<Value>(&b) {
            Ok(Value::Object(m)) => Ok(Some(m)),
            _ => Err(()),
        },
    }
}

fn write_pretty(path: &Path, data: &Map<String, Value>) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let text = serde_json::to_string_pretty(data).unwrap_or_default();
    let _ = std::fs::write(path, text);
}

fn tracked(cwd: &str, rel: &str) -> bool {
    crate::worktree::run(cwd, &["ls-files", "--error-unmatch", rel]).ok
}

/// Assieme's entries into a settings file in the agent's own folder: the project's own entries stay, ours are
/// replaced, never doubled. Kept out of the agent's commits (excluded, or skip-worktree when the project tracks
/// it). A file that isn't plain JSON is left alone: that agent's status then comes from its screen only. Returns
/// the data.
fn merge(cwd: &str, rel: &str, ours: &Map<String, Value>) -> Option<Map<String, Value>> {
    let path = Path::new(cwd).join(rel);
    let mut data = read_json_object(&path).ok()?.unwrap_or_default();
    if !data.get("hooks").is_none_or(Value::is_object) {
        return None;
    }
    for (key, value) in ours {
        if key == "hooks" {
            let hooks = data.entry("hooks").or_insert_with(|| json!({}));
            for (event, entries) in value.as_object().into_iter().flatten() {
                let mut list: Vec<Value> = hooks.get(event).and_then(Value::as_array).cloned().unwrap_or_default();
                list.retain(|e| !is_ours(e));
                list.extend(entries.as_array().cloned().unwrap_or_default());
                hooks[event] = Value::Array(list);
            }
        } else if value.is_object() && data.get(key).is_none_or(Value::is_object) {
            let mut mine = data.get(key).and_then(Value::as_object).cloned().unwrap_or_default();
            mine.extend(value.as_object().cloned().unwrap_or_default());
            data.insert(key.clone(), Value::Object(mine));
        } else {
            data.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }
    let was_tracked = tracked(cwd, rel);
    write_pretty(&path, &data);
    if was_tracked {
        crate::worktree::run(cwd, &["update-index", "--skip-worktree", rel]);
    } else {
        crate::worktree::exclude(cwd, &format!("/{rel}"));
    }
    Some(data)
}

/// Kinds that read their settings from the agent's folder only when told it is trusted: Assieme asks first.
pub const ASKS_TRUST: &[&str] = &["gemini", "cursor", "antigravity"];

type Built = (Vec<String>, BTreeMap<String, String>);

fn hook_entry(state: &str, exe: Option<&str>) -> Value {
    json!([{"hooks": [{"type": "command", "command": hook_command(state, exe)}]}])
}

fn gemini(role: &Role, cwd: &str, exe: Option<&str>, trusted: bool) -> Built {
    // Gemini CLI takes system settings only from files root owns: these go in the agent's own folder.
    let off = limits(role, Some(cwd));
    let items = if off.is_empty() { vec![] } else { tool_items("gemini", Some(cwd), false) };
    let mut ours = Map::new();
    ours.insert(
        "hooks".into(),
        json!({"SessionStart": hook_entry("idle", exe), "BeforeAgent": hook_entry("working", exe),
               "AfterAgent": hook_entry("idle", exe),
               "Notification": [{"matcher": "ToolPermission", "hooks": hook_entry("blocked", exe)[0]["hooks"]}]}),
    );
    let mut server = mcp(exe);
    server["trust"] = json!(true); // its own team tools, without asking each time
    ours.insert("mcpServers".into(), json!({"assieme": server}));
    if own(role) {
        // skills off: the project's own list (as committed) plus this agent's, so one turned back on returns
        let mut want: BTreeSet<String> = committed(cwd, ".gemini/settings.json", &["skills", "disabled"])
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str().map(String::from))
            .collect();
        want.extend(off_of(&off, "skill"));
        let want: Vec<String> = want.into_iter().collect();
        let mut have = gemini_disabled(cwd);
        have.sort();
        if want != have {
            ours.insert("skills".into(), json!({"disabled": want}));
        }
    }
    merge(cwd, ".gemini/settings.json", &ours);
    let mut a = strings(&["gemini"]);
    if let Some(m) = &role.model {
        a.extend(["-m".into(), m.clone()]);
    }
    if role.checkout == "read-only" || role.level.as_deref() == Some("read-only") {
        a.extend(strings(&["--approval-mode", "plan"]));
    }
    let test = if role.level.is_some() { test_command(Some(cwd)) } else { None };
    if let Some(policy_file) = policy().write_gemini_policy(cwd, role, test) {
        // its level and the person's rules; the workspace policy folder isn't read yet, so it is named here
        crate::worktree::exclude(cwd, &format!("/{}", crate::permissions::GEMINI_POLICY));
        a.extend(["--policy".into(), policy_file]);
    }
    let kind_of = |i: &Value, t: &str| i["type"] == t && !off.contains(i["id"].as_str().unwrap_or(""));
    let name = |i: &Value| i["name"].as_str().unwrap_or("").to_string();
    if !off_of(&off, "server").is_empty() {
        // these take lists, so they go last; an extension's own servers stay allowed with it
        let kept = policy()
            .extension_servers()
            .into_iter()
            .filter(|(ext, _)| !off.contains(&format!("extension:{ext}")))
            .flat_map(|(_, names)| names);
        a.push("--allowed-mcp-server-names".into());
        a.extend(items.iter().filter(|i| kind_of(i, "server")).map(name));
        a.extend(kept);
        a.push("assieme".into());
    }
    if !off_of(&off, "extension").is_empty() {
        let names: Vec<String> = items.iter().filter(|i| kind_of(i, "extension")).map(name).collect();
        a.push("-e".into());
        a.extend(if names.is_empty() { vec!["none".to_string()] } else { names });
    }
    let env = if trusted { BTreeMap::from([("GEMINI_CLI_TRUST_WORKSPACE".to_string(), "true".to_string())]) } else { BTreeMap::new() };
    (a, env)
}

/// A value from the project's own version of a settings file (as committed), or null: never Assieme's edits.
fn committed(cwd: &str, rel: &str, keys: &[&str]) -> Value {
    let got = crate::worktree::run(cwd, &["show", &format!("HEAD:{rel}")]);
    let Ok(mut data) = serde_json::from_str::<Value>(if got.ok { &got.out } else { "{}" }) else { return Value::Null };
    for k in keys {
        data = data.get(*k).cloned().unwrap_or(Value::Null);
    }
    data
}

fn gemini_disabled(cwd: &str) -> Vec<String> {
    read_json_object(&Path::new(cwd).join(".gemini/settings.json"))
        .ok()
        .flatten()
        .and_then(|m| m.get("skills")?.get("disabled")?.as_array().cloned())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default()
}

/// The status plugin OpenCode loads: written beside the state files, as it is compiled into the binary.
const OPENCODE_PLUGIN: &str = include_str!("../../../../assieme/opencode_status.js");

fn opencode_plugin() -> String {
    let path = crate::quiet::state_dir().join("opencode_status.js");
    if std::fs::read_to_string(&path).ok().as_deref() != Some(OPENCODE_PLUGIN) {
        let _ = std::fs::create_dir_all(crate::quiet::state_dir());
        let _ = std::fs::write(&path, OPENCODE_PLUGIN);
    }
    format!("file://{}", path.display())
}

static OPENCODE_MAJOR: Mutex<BTreeMap<String, u32>> = Mutex::new(BTreeMap::new());

/// OpenCode's major version from `--version` ("opencode v2.3.0" or "1.19.5"); 1 when it can't tell.
fn opencode_major(path: &str) -> u32 {
    if let Some(v) = OPENCODE_MAJOR.lock().unwrap_or_else(|p| p.into_inner()).get(path) {
        return *v;
    }
    let (tx, rx) = mpsc::channel();
    let program = path.to_string();
    std::thread::spawn(move || {
        let _ = tx.send(std::process::Command::new(program).arg("--version").stdin(std::process::Stdio::null()).output());
    });
    let major = rx
        .recv_timeout(Duration::from_secs(10))
        .ok()
        .and_then(Result::ok)
        .and_then(|o| {
            let out = String::from_utf8_lossy(&o.stdout).into_owned();
            let m = Regex::new(r"(\d+)\.\d+").ok()?.captures(&out)?[1].parse().ok();
            m
        })
        .unwrap_or(1);
    OPENCODE_MAJOR.lock().unwrap_or_else(|p| p.into_inner()).insert(path.to_string(), major);
    major
}

fn opencode(role: &Role, cwd: &str, exe: Option<&str>, which: Which) -> Built {
    // OpenCode reads a whole config from its environment; the status plugin calls our hook.
    let m = mcp(exe);
    let mut config = json!({"plugin": [opencode_plugin()],
        "mcp": {"assieme": {"type": "local", "command": [m["command"], m["args"][0]], "enabled": true}}});
    if role.checkout == "read-only" {
        config["permission"] = json!({"edit": "deny"});
    }
    let test = if role.level.is_some() { test_command(Some(cwd)) } else { None };
    if let Some(Value::Object(mut perms)) = policy().opencode(role, cwd, test) {
        if role.checkout == "read-only" {
            perms.insert("edit".into(), json!("deny"));
        }
        config["permission"] = Value::Object(perms);
    }
    let mut a = strings(&["opencode"]);
    if let Some(m) = &role.model {
        a.extend(["-m".into(), m.clone()]);
    }
    let found = which("opencode").map_or_else(|| "opencode".to_string(), |p| p.to_string_lossy().into_owned());
    if opencode_major(&found) >= 2 {
        a.push("--standalone".into()); // one shared server on a fixed port otherwise
    }
    let hook = [asm_exe(exe), "hook".to_string()];
    let env = BTreeMap::from([
        ("OPENCODE_CONFIG_CONTENT".to_string(), config.to_string()),
        ("ASSIEME_HOOK".to_string(), json!(hook).to_string()),
    ]);
    (a, env)
}

const CURSOR_CLI: &str = ".cursor/cli.json";
const MERGED: [&str; 4] = [".gemini/settings.json", ".cursor/hooks.json", ".cursor/mcp.json", ".agents/hooks.json"];

fn empty(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.values().all(empty),
        _ => false,
    }
}

/// Take Assieme's entries back out of the settings files `merge` and `cursor_permissions` wrote into a folder
/// (step 30's Undo, for a folder brought over from another tool): its hooks, its `assieme` server and team key,
/// and its Cursor rules. The project's own entries stay; a file left with nothing in it was Assieme's alone and
/// goes.
pub fn unmerge(cwd: &str) {
    for rel in MERGED.iter().copied().chain([CURSOR_CLI]) {
        let path = Path::new(cwd).join(rel);
        let Ok(Some(mut data)) = read_json_object(&path) else { continue };
        data.remove("assieme");
        if let Some(Value::Object(servers)) = data.get_mut("mcpServers") {
            servers.remove("assieme");
        }
        if let Some(Value::Object(hooks)) = data.get_mut("hooks") {
            for entries in hooks.values_mut() {
                let kept: Vec<Value> = entries.as_array().into_iter().flatten().filter(|e| !is_ours(e)).cloned().collect();
                *entries = Value::Array(kept);
            }
        }
        if rel == CURSOR_CLI {
            let ours = read_json_object(&Path::new(cwd).join(CURSOR_OURS)).ok().flatten().unwrap_or_default();
            if let Some(Value::Object(perms)) = data.get_mut("permissions") {
                for key in ["allow", "deny"] {
                    if let Some(list) = perms.get(key).and_then(Value::as_array) {
                        let mine = ours.get(key).and_then(Value::as_array).cloned().unwrap_or_default();
                        let kept: Vec<Value> = list.iter().filter(|x| !mine.contains(x)).cloned().collect();
                        perms.insert(key.into(), Value::Array(kept));
                    }
                }
            }
        }
        for key in ["mcpServers", "hooks"] {
            if let Some(Value::Object(m)) = data.get_mut(key) {
                m.retain(|_, v| !matches!(v, Value::Null) && !v.as_array().is_some_and(Vec::is_empty) && v.as_object().is_none_or(|o| !o.is_empty()));
                if m.is_empty() {
                    data.remove(key);
                }
            }
        }
        if tracked(cwd, rel) {
            crate::worktree::run(cwd, &["update-index", "--no-skip-worktree", rel]);
            let was = crate::worktree::run(cwd, &["show", &format!("HEAD:{rel}")]);
            if serde_json::from_str::<Value>(&was.out).ok() == Some(Value::Object(data.clone())) {
                // nothing but ours had changed: the file goes back byte for byte
                crate::worktree::run(cwd, &["checkout", "--", rel]);
                continue;
            }
        } else if empty(&Value::Object(data.clone())) || data.keys().eq(["version"]) {
            let _ = std::fs::remove_file(&path);
            continue;
        }
        write_pretty(&path, &data);
    }
    let _ = std::fs::remove_file(Path::new(cwd).join(CURSOR_OURS));
}

/// Our rules into the project's `.cursor/cli.json`, beside its own: last time's are taken out first (kept in a side
/// file), so a rule the person removed doesn't linger. Both files stay out of the agent's commits.
fn cursor_permissions(cwd: &str, ours: &Value) {
    let (path, side) = (Path::new(cwd).join(CURSOR_CLI), Path::new(cwd).join(CURSOR_OURS));
    let before = read_json_object(&side).ok().flatten().unwrap_or_default();
    let has = ["allow", "deny"].iter().any(|k| ours.get(*k).and_then(Value::as_array).is_some_and(|a| !a.is_empty()));
    if !has && before.is_empty() {
        return;
    }
    let Ok(found) = read_json_object(&path) else { return }; // not plain JSON: left alone
    let mut data = found.unwrap_or_default();
    if !data.get("permissions").is_none_or(Value::is_object) {
        return;
    }
    let perms = data.entry("permissions").or_insert_with(|| json!({}));
    for key in ["allow", "deny"] {
        let old: Vec<Value> = before.get(key).and_then(Value::as_array).cloned().unwrap_or_default();
        let mut list: Vec<Value> = perms.get(key).and_then(Value::as_array).into_iter().flatten().filter(|x| !old.contains(x)).cloned().collect();
        list.extend(ours.get(key).and_then(Value::as_array).cloned().unwrap_or_default());
        let mut seen: Vec<Value> = vec![];
        list.retain(|x| if seen.contains(x) { false } else { seen.push(x.clone()); true });
        perms[key] = Value::Array(list);
    }
    let was_tracked = tracked(cwd, CURSOR_CLI);
    write_pretty(&path, &data);
    let _ = std::fs::write(&side, ours.to_string());
    crate::worktree::exclude(cwd, &format!("/{CURSOR_OURS}"));
    if was_tracked {
        crate::worktree::run(cwd, &["update-index", "--skip-worktree", CURSOR_CLI]);
    } else {
        crate::worktree::exclude(cwd, &format!("/{CURSOR_CLI}"));
    }
}

fn cursor(role: &Role, cwd: &str, exe: Option<&str>, trusted: bool) -> Built {
    // Cursor reads hooks and MCP servers only from files, so they go in the agent's own folder.
    let hook = |s: &str| json!([{"command": hook_command(s, exe)}]);
    let obj = |v: Value| v.as_object().cloned().unwrap_or_default();
    merge(cwd, ".cursor/hooks.json", &obj(json!({"version": 1, "hooks": {
        "sessionStart": hook("idle"), "beforeSubmitPrompt": hook("working"), "stop": hook("idle")}})));
    // Cursor hands MCP servers none of the terminal's variables: these say which agent this is (as Codex's env_vars).
    let passed: Map<String, Value> = ["ASM_SESSION", "ASM_TERMINAL_ID", "XDG_STATE_HOME", "ASSIEME_BRIDGE"]
        .iter()
        .map(|n| (n.to_string(), json!(format!("${{env:{n}}}"))))
        .collect();
    let mut server = mcp(exe);
    server["env"] = Value::Object(passed);
    let mcp_data = merge(cwd, ".cursor/mcp.json", &obj(json!({"mcpServers": {"assieme": server}})));
    if let Some(mut data) = mcp_data.clone().filter(|_| own(role)) {
        // its own copy only: shared-folder servers are locked
        let off = off_of(&limits(role, Some(cwd)), "server");
        let before: BTreeSet<String> = data["mcpServers"].as_object().into_iter().flatten().map(|(k, _)| k.clone()).collect();
        if let Some(Value::Object(servers)) = data.get_mut("mcpServers") {
            for (n, v) in committed(cwd, ".cursor/mcp.json", &["mcpServers"]).as_object().into_iter().flatten() {
                // one turned back on returns
                if !servers.contains_key(n) && !off.contains(n) {
                    servers.insert(n.clone(), v.clone());
                }
            }
            for n in &off {
                servers.remove(n);
            }
            let now: BTreeSet<String> = servers.keys().cloned().collect();
            if now != before {
                // the file is already kept out of git by `merge`
                write_pretty(&Path::new(cwd).join(".cursor/mcp.json"), &data);
            }
        }
    }
    let test = if role.level.is_some() { test_command(Some(cwd)) } else { None };
    let mut ours = policy().cursor(role, cwd, test).unwrap_or_else(|| json!({"allow": [], "deny": []}));
    let mut allow = vec![json!("Mcp(assieme:*)")]; // its own team's tools: asking about each would defeat them
    allow.extend(ours["allow"].as_array().cloned().unwrap_or_default());
    ours["allow"] = Value::Array(allow);
    cursor_permissions(cwd, &ours);
    // Approving MCP servers approves them all: only when Assieme's is the only one there.
    let alone = mcp_data.is_some_and(|d| d["mcpServers"].as_object().is_some_and(|m| m.len() == 1 && m.contains_key("assieme")));
    let mut a = strings(&["cursor-agent"]);
    if alone {
        a.push("--approve-mcps".into());
    }
    if trusted {
        a.push("--trust".into());
    }
    if let Some(m) = &role.model {
        a.extend(["--model".into(), m.clone()]);
    }
    if role.checkout == "read-only" {
        a.extend(strings(&["--mode", "plan"]));
    }
    (a, BTreeMap::new())
}

const AGY_SERVER: &str = "assieme_assieme";

fn antigravity(role: &Role, cwd: &str, exe: Option<&str>, trusted: bool) -> Built {
    // Hooks from the agent's own folder (`.agents/hooks.json`); its level, the person's rules and a trusted folder
    // in settings for this session only, never saved. Its team tools come from Assieme's plugin (`antigravity`).
    let hook = |s: &str| json!([{"type": "command", "command": hook_command(s, exe)}]);
    merge(
        cwd,
        ".agents/hooks.json",
        json!({"assieme": {"SessionStart": hook("idle"), "PreInvocation": hook("working"), "Stop": hook("idle")}})
            .as_object()
            .expect("an object"),
    );
    let mut session = json!({"toolPermission": "request-review", "showFeedbackSurvey": false}); // anything not named asks
    let test = if role.level.is_some() { test_command(Some(cwd)) } else { None };
    let mut perms = policy().antigravity(role, cwd, test).unwrap_or_else(|| json!({"allow": []}));
    // Its own team's tools, as for Claude Code: asking the person about each message would defeat them. The plugin's
    // server is named "<plugin>_<server>".
    let mut allow = vec![json!(format!("mcp({AGY_SERVER}/*)"))];
    allow.extend(perms["allow"].as_array().cloned().unwrap_or_default());
    perms["allow"] = Value::Array(allow);
    session["permissions"] = perms;
    if role.level.is_some() {
        session["agentMode"] = json!(""); // not the person's auto mode: anything a rule doesn't name asks them
    }
    if trusted {
        session["trustedWorkspaces"] = json!([cwd]);
    }
    let mut a = vec!["agy".to_string(), "--settings".to_string(), session.to_string()];
    if let Some(m) = &role.model {
        a.extend(["--model".into(), m.clone()]);
    }
    if let Some(e) = &role.effort {
        a.extend(["--effort".into(), e.clone()]);
    }
    if role.checkout == "read-only" || role.level.as_deref() == Some("read-only") {
        a.extend(strings(&["--mode", "plan"]));
    }
    (a, BTreeMap::new())
}

/// An empty prefix matches every remote; pushes go to a git-remote helper (quiet.install writes it) that refuses.
/// Fetch is untouched.
pub const BLOCK_PUSH: [(&str, &str); 3] = [
    ("GIT_CONFIG_COUNT", "1"),
    ("GIT_CONFIG_KEY_0", "url.assieme-push-blocked://.pushInsteadOf"),
    ("GIT_CONFIG_VALUE_0", ""),
];

/// The command line. The agent is started by full path, so the PATH the person's shell has isn't relied on.
/// `cwd`: the agent's folder, for its permissions' paths and for the kinds that read their settings from files.
/// A container role runs inside `docker run`; `terminal` names the container, `bridge` is the port its team tools
/// reach the brain on. `trusted`: the person said yes to this project, for the kinds in ASKS_TRUST.
/// A role with a permission level is always kept from pushing.
pub fn argv_with(
    role: &Role,
    block_push: bool,
    which: Which,
    cwd: Option<&str>,
    terminal: Option<&str>,
    bridge: Option<&str>,
    trusted: bool,
) -> Result<Vec<String>, BrainError> {
    let here;
    let cwd = match cwd {
        Some(c) => c,
        None if ASKS_TRUST.contains(&role.agent.as_str()) => {
            // they write settings files: never into whatever folder this process is in
            return Err(BrainError::new(format!("{} needs the agent's own folder.", name_of(&role.agent))));
        }
        None => {
            here = std::env::current_dir().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
            &here
        }
    };
    let push: BTreeMap<String, String> = if block_push || role.level.is_some() {
        BLOCK_PUSH.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    } else {
        BTreeMap::new()
    };
    let build = |exe: Option<&str>| -> Result<Built, BrainError> {
        Ok(match role.agent.as_str() {
            "claude" => (claude(role, Some(cwd), exe), BTreeMap::new()),
            "codex" => (codex(role, exe, Some(cwd)), BTreeMap::new()),
            "gemini" => gemini(role, cwd, exe, trusted),
            "opencode" => opencode(role, cwd, exe, which),
            "cursor" => cursor(role, cwd, exe, trusted),
            "antigravity" => antigravity(role, cwd, exe, trusted),
            other => return Err(BrainError::new(format!("Unknown agent {other:?}."))),
        })
    };
    if role.container {
        let (a, extra) = build(Some("asm"))?;
        let env: BTreeMap<String, String> = push.into_iter().chain(extra).collect();
        return Ok(policy().container_run_argv(role, &a, &env, cwd, terminal.unwrap_or(&role.name), bridge));
    }
    let (mut a, extra) = build(None)?;
    if let Some(found) = which(&a[0]) {
        a[0] = found.to_string_lossy().into_owned();
    }
    let mut env: BTreeMap<String, String> = push.into_iter().chain(extra).collect();
    if Path::new(&a[0]).is_absolute() {
        // An npm-installed agent is a `#!/usr/bin/env node` script whose node sits beside it, and the core may be
        // older than this brain, with a shorter PATH. Quiet send's folder stays first, as the core sets it.
        let shim = crate::quiet::shim_dir().to_string_lossy().into_owned();
        let folder = Path::new(&a[0]).parent().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
        let current = std::env::var("PATH").unwrap_or_default();
        let rest = current.split(':').filter(|p| !p.is_empty() && *p != shim && *p != folder);
        env.insert("PATH".into(), [shim.as_str(), folder.as_str()].into_iter().chain(rest).collect::<Vec<_>>().join(":"));
    }
    if env.is_empty() {
        return Ok(a);
    }
    let mut out = vec!["/usr/bin/env".to_string()];
    out.extend(env.iter().map(|(k, v)| format!("{k}={v}")));
    out.extend(a);
    Ok(out)
}

pub fn argv(
    role: &Role,
    block_push: bool,
    cwd: Option<&str>,
    terminal: Option<&str>,
    bridge: Option<&str>,
    trusted: bool,
) -> Result<Vec<String>, BrainError> {
    argv_with(role, block_push, &crate::setup::agent_path, cwd, terminal, bridge, trusted)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn role(agent: &str) -> Role {
        Role { name: "r".into(), agent: agent.into(), checkout: "branch".into(), tools_new: true, ..Role::default() }
    }

    fn nowhere(_: &str) -> Option<PathBuf> {
        None
    }

    fn folder() -> (tempfile::TempDir, String) {
        let d = tempfile::tempdir().unwrap();
        let p = std::fs::canonicalize(d.path()).unwrap().to_string_lossy().into_owned();
        for a in [vec!["init", "-q", "-b", "main"], vec!["config", "user.name", "T"], vec!["config", "user.email", "t@t"]] {
            assert!(crate::worktree::run(&p, &a).ok);
        }
        std::fs::write(format!("{p}/f"), "x").unwrap();
        assert!(crate::worktree::run(&p, &["add", "."]).ok);
        assert!(crate::worktree::run(&p, &["commit", "-q", "-m", "x"]).ok);
        (d, p)
    }

    fn json_arg(a: &[String], flag: &str) -> Value {
        let i = a.iter().position(|x| x == flag).expect("flag present");
        serde_json::from_str(&a[i + 1]).unwrap()
    }

    #[test]
    fn model_and_effort_are_checked_in_words() {
        assert_eq!(check_model_effort("claude", Some("opus"), Some("high")), None);
        assert_eq!(check_model_effort("claude", Some("--evil"), None).unwrap(), "Model names use letters, digits, dots and dashes.");
        assert_eq!(check_model_effort("codex", Some("a b"), None).is_some(), true);
        assert_eq!(check_model_effort("gemini", None, Some("high")).unwrap(), "Gemini CLI has no thinking effort \"high\".");
        assert_eq!(check_model_effort("codex", None, Some("ultra")), None);
        assert!(check_model_effort("claude", None, Some("ultra")).is_some());
        assert_eq!(check_model_effort("claude", Some(""), Some("")), None);
    }

    #[test]
    fn only_programs_that_are_missing_are_named_and_containers_bring_their_own() {
        let roles = [role("claude"), role("claude"), role("codex"), Role { container: true, ..role("gemini") }];
        let which = |n: &str| (n == "claude").then(|| PathBuf::from("/bin/claude"));
        assert_eq!(missing_clis_with(&roles, &which), ["codex"]);
    }

    #[test]
    fn the_hook_is_asm_hook_with_a_quoted_path() {
        assert_eq!(hook_command("idle", Some("/a b/asm")), "'/a b/asm' hook idle");
        assert_eq!(hook_command("blocked", Some("asm")), "asm hook blocked");
    }

    #[test]
    fn claude_gets_hooks_team_tools_and_the_role_s_options() {
        let r = Role { model: Some("opus".into()), effort: Some("high".into()), checkout: "read-only".into(), level: Some("safe".into()), cont: true, ..role("claude") };
        let a = claude(&r, Some("/w"), Some("asm"));
        assert_eq!(a[0], "claude");
        let settings = json_arg(&a, "--settings");
        assert_eq!(settings["permissions"]["allow"], json!(["mcp__assieme"]));
        assert_eq!(settings["hooks"]["PermissionRequest"][0]["hooks"][0]["command"], "asm hook blocked");
        assert_eq!(json_arg(&a, "--mcp-config"), json!({"mcpServers": {"assieme": {"command": "asm", "args": ["mcp"]}}}));
        let joined = a.join(" ");
        for want in ["--model opus", "--effort high", "--permission-mode default", "--disallowedTools Edit Write NotebookEdit", "--continue"] {
            assert!(joined.contains(want), "{want} in {joined}");
        }
        assert!(!claude(&role("claude"), Some("/w"), None).join(" ").contains("--permission-mode"));
    }

    #[test]
    fn a_resume_wins_over_continue_but_only_where_its_folder_is() {
        let tmp = tempfile::tempdir().unwrap();
        let there = tmp.path().to_string_lossy().into_owned();
        let r = Role { resume: Some("abc-1".into()), worktree: Some(there), cont: true, ..role("claude") };
        assert!(claude(&r, None, None).join(" ").contains("--resume abc-1"));
        let gone = Role { worktree: Some("/no/such/folder".into()), ..r };
        let a = claude(&gone, None, None).join(" ");
        assert!(!a.contains("--resume") && a.contains("--continue"));
    }

    #[test]
    fn codex_trusts_exactly_our_four_hooks_and_resumes() {
        let r = Role { resume: Some("s-1".into()), checkout: "read-only".into(), model: Some("gpt".into()), effort: Some("high".into()), ..role("codex") };
        let a = codex(&r, Some("asm"), None);
        assert_eq!(a[..3], ["codex", "resume", "s-1"]);
        let state = a.iter().find(|x| x.starts_with("hooks.state=")).unwrap();
        assert_eq!(state.matches("trusted_hash").count(), 4);
        assert!(state.contains(&codex_hook_hash("Stop", "asm hook idle")));
        assert!(a.join(" ").contains("model_reasoning_effort=\"high\""));
        assert_eq!(a[a.len() - 2..], ["--sandbox", "read-only"]);
        assert_eq!(codex_hook_hash("Stop", "x"), codex_hook_hash("Stop", "x"));
        assert_ne!(codex_hook_hash("Stop", "x"), codex_hook_hash("Stop", "y"));
        assert!(codex_hook_hash("Stop", "x").starts_with("sha256:") && codex_hook_hash("Stop", "x").len() == 71);
    }

    #[test]
    fn codex_hash_matches_the_normalised_identity() {
        // sha256 of the sorted, compact JSON Codex normalises a command hook to
        let ident = r#"{"event_name":"Stop","hooks":[{"async":false,"command":"c","timeout":600,"type":"command"}]}"#;
        let want = format!("sha256:{}", Sha256::digest(ident).iter().map(|b| format!("{b:02x}")).collect::<String>());
        assert_eq!(codex_hook_hash("Stop", "c"), want);
    }

    #[test]
    fn gemini_keeps_the_project_s_own_settings_and_replaces_only_ours() {
        let (_d, p) = folder();
        let path = format!("{p}/.gemini/settings.json");
        std::fs::create_dir_all(format!("{p}/.gemini")).unwrap();
        std::fs::write(&path, r#"{"theme":"dark","hooks":{"AfterAgent":[{"hooks":[{"type":"command","command":"mine.sh"}]},{"hooks":[{"type":"command","command":"/old/asm hook idle"}]}]},"mcpServers":{"other":{"command":"x"}}}"#).unwrap();
        let r = role("gemini");
        let (a, env) = gemini(&r, &p, Some("asm"), true);
        assert_eq!(a, ["gemini"]);
        assert_eq!(env["GEMINI_CLI_TRUST_WORKSPACE"], "true");
        let got: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(got["theme"], "dark");
        assert_eq!(got["mcpServers"]["other"]["command"], "x");
        assert_eq!(got["mcpServers"]["assieme"]["trust"], true);
        let after: Vec<String> = got["hooks"]["AfterAgent"].as_array().unwrap().iter().map(|e| e["hooks"][0]["command"].as_str().unwrap().to_string()).collect();
        assert_eq!(after, ["mine.sh", "asm hook idle"], "ours replaced, never doubled");
        gemini(&r, &p, Some("asm"), false);
        let again: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(again, got);
        assert!(!std::fs::read_to_string(format!("{p}/.git/info/exclude")).unwrap().is_empty(), "kept out of commits");
    }

    #[test]
    fn a_settings_file_that_is_not_plain_json_is_left_alone() {
        let (_d, p) = folder();
        std::fs::create_dir_all(format!("{p}/.gemini")).unwrap();
        std::fs::write(format!("{p}/.gemini/settings.json"), "// comment\n{}").unwrap();
        gemini(&role("gemini"), &p, Some("asm"), false);
        assert_eq!(std::fs::read_to_string(format!("{p}/.gemini/settings.json")).unwrap(), "// comment\n{}");
    }

    #[test]
    fn unmerge_removes_only_ours_and_deletes_files_that_were_ours_alone() {
        let (_d, p) = folder();
        std::fs::create_dir_all(format!("{p}/.gemini")).unwrap();
        std::fs::write(format!("{p}/.gemini/settings.json"), r#"{"theme":"dark","mcpServers":{"other":{"command":"x"}}}"#).unwrap();
        gemini(&role("gemini"), &p, Some("asm"), false);
        cursor(&role("cursor"), &p, Some("asm"), false);
        assert!(Path::new(&format!("{p}/.cursor/hooks.json")).exists());
        unmerge(&p);
        let got: Value = serde_json::from_str(&std::fs::read_to_string(format!("{p}/.gemini/settings.json")).unwrap()).unwrap();
        assert_eq!(got, json!({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}));
        for gone in [".cursor/hooks.json", ".cursor/mcp.json", ".cursor/assieme-permissions.json", ".cursor/cli.json"] {
            assert!(!Path::new(&format!("{p}/{gone}")).exists(), "{gone}");
        }
    }

    #[test]
    fn unmerge_puts_a_tracked_file_back_byte_for_byte() {
        let (_d, p) = folder();
        std::fs::create_dir_all(format!("{p}/.gemini")).unwrap();
        let original = "{\"theme\":   \"dark\"}\n";
        std::fs::write(format!("{p}/.gemini/settings.json"), original).unwrap();
        assert!(crate::worktree::run(&p, &["add", "."]).ok);
        assert!(crate::worktree::run(&p, &["commit", "-q", "-m", "settings"]).ok);
        gemini(&role("gemini"), &p, Some("asm"), false);
        assert_ne!(std::fs::read_to_string(format!("{p}/.gemini/settings.json")).unwrap(), original);
        assert!(crate::worktree::run(&p, &["status", "--porcelain"]).out.is_empty(), "skip-worktree hides our edit");
        unmerge(&p);
        assert_eq!(std::fs::read_to_string(format!("{p}/.gemini/settings.json")).unwrap(), original);
        assert!(crate::worktree::run(&p, &["status", "--porcelain"]).out.is_empty());
    }

    #[test]
    fn cursor_approves_mcps_only_when_ours_is_the_only_server() {
        let (_d, p) = folder();
        let (a, _) = cursor(&role("cursor"), &p, Some("asm"), true);
        assert_eq!(a, ["cursor-agent", "--approve-mcps", "--trust"]);
        let perms: Value = serde_json::from_str(&std::fs::read_to_string(format!("{p}/.cursor/cli.json")).unwrap()).unwrap();
        assert_eq!(perms["permissions"]["allow"], json!(["Mcp(assieme:*)"]));
        std::fs::write(format!("{p}/.cursor/mcp.json"), r#"{"mcpServers":{"theirs":{"command":"y"}}}"#).unwrap();
        let (b, _) = cursor(&role("cursor"), &p, Some("asm"), false);
        assert_eq!(b, ["cursor-agent"]);
    }

    #[test]
    fn a_cursor_rule_the_person_removed_does_not_linger() {
        let (_d, p) = folder();
        std::fs::create_dir_all(format!("{p}/.cursor")).unwrap();
        std::fs::write(format!("{p}/.cursor/cli.json"), r#"{"permissions":{"allow":["Shell(ls)"],"deny":[]}}"#).unwrap();
        cursor_permissions(&p, &json!({"allow": ["Shell(git)"], "deny": ["Shell(rm)"]}));
        cursor_permissions(&p, &json!({"allow": [], "deny": ["Shell(rm)"]}));
        let got: Value = serde_json::from_str(&std::fs::read_to_string(format!("{p}/.cursor/cli.json")).unwrap()).unwrap();
        assert_eq!(got["permissions"], json!({"allow": ["Shell(ls)"], "deny": ["Shell(rm)"]}));
    }

    #[test]
    fn antigravity_asks_about_anything_not_named_and_trusts_the_folder_for_one_session() {
        let (_d, p) = folder();
        let r = Role { level: Some("safe".into()), model: Some("m".into()), effort: Some("low".into()), ..role("antigravity") };
        let (a, _) = antigravity(&r, &p, Some("asm"), true);
        let s = json_arg(&a, "--settings");
        assert_eq!(s["permissions"]["allow"], json!(["mcp(assieme_assieme/*)"]));
        assert_eq!((s["toolPermission"].as_str(), s["agentMode"].as_str()), (Some("request-review"), Some("")));
        assert_eq!(s["trustedWorkspaces"], json!([p]));
        assert!(a.join(" ").ends_with("--model m --effort low"));
        let hooks: Value = serde_json::from_str(&std::fs::read_to_string(format!("{p}/.agents/hooks.json")).unwrap()).unwrap();
        assert_eq!(hooks["assieme"]["Stop"][0]["command"], "asm hook idle");
    }

    #[test]
    fn a_kind_that_writes_settings_never_does_so_in_the_current_folder() {
        for k in ASKS_TRUST {
            let e = argv_with(&role(k), false, &nowhere, None, None, None, false).unwrap_err();
            assert!(e.message.ends_with("needs the agent's own folder."), "{}", e.message);
        }
    }

    #[test]
    fn pushes_are_blocked_by_the_team_rule_or_a_permission_level() {
        let one = |r: &Role, block| argv_with(r, block, &nowhere, Some("/w"), None, None, false).unwrap();
        assert_eq!(one(&role("claude"), false)[0], "claude");
        let blocked = one(&role("claude"), true);
        assert_eq!(blocked[..5], ["/usr/bin/env", "GIT_CONFIG_COUNT=1", "GIT_CONFIG_KEY_0=url.assieme-push-blocked://.pushInsteadOf", "GIT_CONFIG_VALUE_0=", "claude"]);
        let by_level = one(&Role { level: Some("safe".into()), ..role("claude") }, false);
        assert_eq!(by_level[0], "/usr/bin/env");
    }

    #[test]
    fn the_agent_is_started_by_full_path_with_its_own_folder_on_the_path() {
        let which = |n: &str| (n == "claude").then(|| PathBuf::from("/opt/node/bin/claude"));
        let a = argv_with(&role("claude"), false, &which, Some("/w"), None, None, false).unwrap();
        assert_eq!(a[0], "/usr/bin/env");
        let path = a[1].strip_prefix("PATH=").unwrap();
        let parts: Vec<&str> = path.split(':').collect();
        assert!(parts[0].ends_with("/bin") && parts[0].contains("assieme"), "{path}");
        assert_eq!(parts[1], "/opt/node/bin");
        assert_eq!(a[2], "/opt/node/bin/claude");
    }

    #[test]
    fn a_container_role_runs_inside_the_containers_own_asm() {
        let r = Role { container: true, ..role("claude") };
        let a = argv_with(&r, true, &nowhere, Some("/w"), Some("team-r"), Some("9/abc"), false).unwrap();
        assert_eq!(a[..3], ["docker", "run", "team-r"]);
        let settings = json_arg(&a, "--settings");
        assert_eq!(settings["hooks"]["Stop"][0]["hooks"][0]["command"], "asm hook idle");
    }
}
