//! Port of `assieme/tools.py`, owned by session D (see OWNERS.md).
//!
//! Step 32: the skills, plugins and MCP servers each agent kind has installed, so each agent gets only what it needs.
//! Read-only. A server's command, arguments and environment often hold keys, so they are never read into an item, a
//! notice or a log.
use crate::prelude::*;
use regex::Regex;
use serde_json::json;

const SELF: [&str; 2] = ["assieme", "assieme_assieme"]; // Assieme's own team server: always on
const SKILL_SHARED: &str =
    "Gemini CLI reads skill settings from the project folder, so every Gemini agent working there gets the same ones.";
const SERVER_HOME: &str = "Cursor reads this server from your home folder, so every Cursor agent gets it.";
const SERVER_SHARED: &str =
    "Cursor reads its servers from the project folder, so every Cursor agent working there gets the same ones.";
const OPENCODE_SERVER: &str = "OpenCode can't turn one server off for a single agent without being given that server's whole settings, so every OpenCode agent gets the same ones.";
const CLAUDE_SKILL: &str = "Claude Code can turn skills off only all together, with \"All skills\".";
const ANTIGRAVITY: &str =
    "Antigravity can't be given its own set of skills and servers yet: it gets all installed skills and servers.";

fn item(kind: &str, name: &str, description: &str, why: &str) -> Value {
    json!({"id": format!("{kind}:{name}"), "type": kind, "name": name, "description": description, "locked": !why.is_empty(),
           "why": why})
}

fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}

/// Read what other tools wrote, keeping a notice for each file that can't be read.
struct Reader(Vec<String>);

impl Reader {
    fn file(&mut self, path: &Path, parse: &dyn Fn(&str) -> Option<Value>) -> Value {
        if !path.is_file() {
            return json!({});
        }
        match std::fs::read_to_string(path).ok().and_then(|t| parse(&t)).filter(Value::is_object) {
            Some(v) => v,
            None => {
                self.0.push(format!("Couldn't read {}.", path.display()));
                json!({})
            }
        }
    }

    fn json(&mut self, path: &Path) -> Value {
        // shortcut: whole-line comments only in .jsonc
        static COMMENT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?m)^\s*//.*$").expect("a fixed pattern"));
        let jsonc = path.extension().is_some_and(|e| e == "jsonc");
        self.file(path, &|t| serde_json::from_str(&if jsonc { COMMENT.replace_all(t, "") } else { t.into() }).ok())
    }

    fn toml(&mut self, path: &Path) -> Value {
        self.file(path, &|t| toml::from_str(t).ok())
    }
}

/// Server names that are on, never their settings.
fn names(servers: &Value) -> Vec<String> {
    let on = |v: &Value| !v.is_object() || (v["enabled"] != false && v["disabled"] != true);
    servers.as_object().map_or(vec![], |m| {
        m.iter().filter(|(n, v)| !SELF.contains(&n.as_str()) && on(v)).map(|(n, _)| n.clone()).collect()
    })
}

fn description(skill_md: &Path) -> String {
    use std::io::Read;
    let mut head = vec![];
    let _ = std::fs::File::open(skill_md).and_then(|f| f.take(4096).read_to_end(&mut head));
    static D: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?m)^description:\s*(.+)$").expect("a fixed pattern"));
    D.captures(&String::from_utf8_lossy(&head))
        .map_or(String::new(), |c| c[1].trim().trim_matches(['"', '\'']).to_string())
}

/// The folders inside one, sorted, dot folders left out.
fn subdirs(dir: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(dir)
        .map(|d| {
            d.flatten()
                .map(|e| e.path())
                .filter(|p| !p.file_name().is_some_and(|n| n.to_string_lossy().starts_with('.')))
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

/// (name, SKILL.md path) from skill folders, first folder wins.
fn skills(folders: &[PathBuf]) -> Vec<(String, PathBuf)> {
    let mut out: Vec<(String, PathBuf)> = vec![];
    for md in folders.iter().flat_map(|f| subdirs(f)).map(|d| d.join("SKILL.md")).filter(|m| m.is_file()) {
        let name = md.parent().and_then(Path::file_name).map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        if !out.iter().any(|(n, _)| *n == name) {
            out.push((name, md));
        }
    }
    out
}

fn skill_items(found: &[(String, PathBuf)], why: &str) -> Vec<Value> {
    found.iter().map(|(n, p)| item("skill", n, &description(p), why)).collect()
}

fn servers(kind: &str, list: Vec<String>, why: &str) -> Vec<Value> {
    list.iter().map(|n| item(kind, n, "", why)).collect()
}

fn claude(r: &mut Reader, project: Option<&Path>, home: &Path, _own_copy: bool) -> Vec<Value> {
    let cfg = r.json(&home.join(".claude.json"));
    let mut list = names(&cfg["mcpServers"]);
    if let Some(p) = project {
        list.extend(names(&cfg["projects"][p.to_string_lossy().as_ref()]["mcpServers"]));
        list.extend(names(&r.json(&p.join(".mcp.json"))["mcpServers"]));
    }
    let mut items = servers("server", list, "");
    let enabled = r.json(&home.join(".claude/settings.json"))["enabledPlugins"].clone();
    let installed = r.json(&home.join(".claude/plugins/installed_plugins.json"))["plugins"].clone();
    for (pid, _) in enabled.as_object().into_iter().flatten().filter(|(_, on)| **on == true) {
        let at = installed[pid].as_array().and_then(|a| a.last()).map_or("", |l| text(&l["installPath"])).to_string();
        let manifest =
            if at.is_empty() { json!({}) } else { r.json(&Path::new(&at).join(".claude-plugin/plugin.json")) };
        items.push(item("plugin", pid, text(&manifest["description"]), ""));
    }
    let folders: Vec<PathBuf> =
        [Some(home.join(".claude/skills")), project.map(|p| p.join(".claude/skills"))].into_iter().flatten().collect();
    let found = skills(&folders);
    if !found.is_empty() {
        items.push(item(
            "skill",
            "*",
            "Every skill this agent can use. Claude Code turns them on or off together.",
            "",
        ));
        items.extend(skill_items(&found, CLAUDE_SKILL));
    }
    items
}

fn codex_skills(r: &mut Reader, home: &Path) -> (Value, Vec<(String, PathBuf)>) {
    let cfg = r.toml(&home.join(".codex/config.toml"));
    let mut out = skills(&[home.join(".codex/skills")]);
    for (pid, v) in cfg["plugins"].as_object().into_iter().flatten() {
        let Some((name, market)) = pid.split_once('@').filter(|_| v.is_object() && v["enabled"] != false) else {
            continue;
        };
        if let Some(latest) = subdirs(&home.join(".codex/plugins/cache").join(market).join(name)).pop() {
            out.extend(skills(&[latest.join("skills")]).into_iter().map(|(n, p)| (format!("{name}:{n}"), p)));
        }
    }
    (cfg, out)
}

fn codex(r: &mut Reader, _project: Option<&Path>, home: &Path, _own_copy: bool) -> Vec<Value> {
    let (cfg, found) = codex_skills(r, home);
    let mut items = servers("server", names(&cfg["mcp_servers"]), "");
    items.extend(skill_items(&found, ""));
    items
}

/// Each Gemini extension's manifest and name.
fn gemini_extensions(r: &mut Reader, home: &Path) -> Vec<(String, Value)> {
    let dir = home.join(".gemini/extensions");
    subdirs(&dir)
        .into_iter()
        .filter(|d| d.join("gemini-extension.json").is_file())
        .map(|d| {
            let m = r.json(&d.join("gemini-extension.json"));
            let fallback = d.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            (Some(text(&m["name"])).filter(|n| !n.is_empty()).map_or(fallback, String::from), m)
        })
        .collect()
}

fn gemini(r: &mut Reader, project: Option<&Path>, home: &Path, own_copy: bool) -> Vec<Value> {
    let mut list = names(&r.json(&home.join(".gemini/settings.json"))["mcpServers"]);
    if let Some(p) = project {
        list.extend(names(&r.json(&p.join(".gemini/settings.json"))["mcpServers"]));
    }
    let mut items = servers("server", list, "");
    items.extend(gemini_extensions(r, home).iter().map(|(n, m)| item("extension", n, text(&m["description"]), "")));
    items.extend(skill_items(
        &skills(&[home.join(".gemini/skills"), home.join(".agents/skills")]),
        if own_copy { "" } else { SKILL_SHARED },
    ));
    items
}

fn opencode(r: &mut Reader, project: Option<&Path>, home: &Path, _own_copy: bool) -> Vec<Value> {
    let dir = home.join(".config/opencode");
    let paths = [dir.join("opencode.json"), dir.join("opencode.jsonc")]
        .into_iter()
        .chain(project.map(|p| p.join("opencode.json")));
    let list = paths.flat_map(|p| names(&r.json(&p)["mcp"])).collect();
    servers("server", list, OPENCODE_SERVER)
}

fn cursor(r: &mut Reader, project: Option<&Path>, home: &Path, own_copy: bool) -> Vec<Value> {
    let mut items = servers("server", names(&r.json(&home.join(".cursor/mcp.json"))["mcpServers"]), SERVER_HOME);
    if let Some(p) = project {
        items.extend(servers(
            "server",
            names(&r.json(&p.join(".cursor/mcp.json"))["mcpServers"]),
            if own_copy { "" } else { SERVER_SHARED },
        ));
    }
    items
}

fn home() -> PathBuf {
    PathBuf::from(crate::rules::home())
}

/// `{items, notices}` for one agent kind. `own_copy`: the agent works in its own copy of the project, so settings
/// read from the project folder are its alone.
pub fn find_in(kind: &str, project: Option<&str>, home: &Path, own_copy: bool) -> Value {
    let read: fn(&mut Reader, Option<&Path>, &Path, bool) -> Vec<Value> = match kind {
        "antigravity" => return json!({"items": [], "notices": [ANTIGRAVITY]}),
        "claude" => claude,
        "codex" => codex,
        "gemini" => gemini,
        "opencode" => opencode,
        "cursor" => cursor,
        _ => return json!({"items": [], "notices": []}),
    };
    let mut r = Reader(vec![]);
    let project = project.map(|p| crate::preset::absolute(Path::new(p)));
    // A name Assieme can't save, or that would read as an option on a command line, isn't offered: it stays on.
    let usable = Regex::new(crate::newteam::TOOL_ID).expect("a fixed pattern");
    let mut seen = BTreeSet::new();
    let items: Vec<Value> = read(&mut r, project.as_deref(), home, own_copy)
        .into_iter()
        .filter(|i| {
            seen.insert(text(&i["id"]).to_string())
                && usable.is_match(text(&i["id"]))
                && !text(&i["name"]).starts_with('-')
        })
        .collect();
    json!({"items": items, "notices": r.0})
}

pub fn find(kind: &str, project: Option<&str>, own_copy: bool) -> Value {
    find_in(kind, project, &home(), own_copy)
}

/// {extension name: [server names]} for Gemini CLI: names only, so its allow-list keeps them.
pub fn extension_servers() -> BTreeMap<String, Vec<String>> {
    gemini_extensions(&mut Reader(vec![]), &home()).into_iter().map(|(n, m)| (n, names(&m["mcpServers"]))).collect()
}

/// {skill id: SKILL.md path}, for the kinds that turn single skills off by path (Codex).
pub fn skill_paths(kind: &str, _cwd: &str) -> BTreeMap<String, String> {
    if kind != "codex" {
        return BTreeMap::new();
    }
    codex_skills(&mut Reader(vec![]), &home())
        .1
        .into_iter()
        .map(|(n, p)| (format!("skill:{n}"), p.to_string_lossy().into_owned()))
        .collect()
}

const DEPLOY: [&str; 7] = ["deploy", "publish", "release", "vercel", "netlify", "runpod", "aws"];
const BROWSER: [&str; 6] = ["browser", "chrome", "playwright", "puppeteer", "computer-use", "computer use"];

/// Ids to turn off for a role (planner, builder, reviewer, tester; any other gets everything). Only words in an
/// item's name or description move it off: anything the rules can't place stays on.
pub fn suggest(role_kind: &str, items: &[Value]) -> Vec<String> {
    let words: Vec<&str> = match role_kind {
        "reviewer" | "planner" => DEPLOY.iter().chain(&BROWSER).copied().collect(),
        "builder" | "tester" => DEPLOY.to_vec(),
        _ => return vec![],
    };
    let any = words.iter().map(|w| regex::escape(w)).collect::<Vec<_>>().join("|");
    let pattern = Regex::new(&format!("(?:^|[^a-z0-9])(?:{any})(?:[^a-z0-9]|$)")).expect("escaped words"); // whole words
    let hit = |i: &&Value| {
        i["locked"] != true
            && pattern.is_match(&format!("{} {}", text(&i["name"]), text(&i["description"])).to_lowercase())
    };
    items.iter().filter(hit).map(|i| text(&i["id"]).to_string()).collect()
}

/// What is off when this role starts: its saved choice, plus anything new when it doesn't take new ones.
pub fn off(role: &Role, items: &[Value]) -> BTreeSet<String> {
    let ids: BTreeSet<String> =
        items.iter().filter(|i| i["locked"] != true).map(|i| text(&i["id"]).to_string()).collect();
    let mut out: BTreeSet<String> = role.tools_off.iter().filter(|i| ids.contains(*i)).cloned().collect();
    if !role.tools_new {
        out.extend(ids.iter().filter(|i| !role.tools_known.contains(i)).cloned());
    }
    out
}
