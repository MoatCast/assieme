//! Port of `assieme/newteam.py`, owned by session D (see OWNERS.md).
//!
//! New team in the app: the starting teams, checks, saving a team folder, starting it in the background, and
//! Help me choose. The app sends the form; this module turns it into an ordinary team folder for `preset::load`.
use crate::launch::d2;
use crate::prelude::*;
use crate::{launch, musicians, preset, scripts, team, worktree};
use regex::Regex;
use serde_json::json;

pub const BUILTIN: &[&str] = &["one-agent", "builder-reviewer", "full"];

pub const CLAUDE_MODELS: &[(&str, &str)] =
    &[("fable", "Fable"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")];

/// Kind -> (its brief file, its checkout).
pub const KINDS: &[(&str, (&str, &str))] = &[
    ("planner", ("planner.md", "read-only")),
    ("builder", ("builder.md", "branch")),
    ("reviewer", ("reviewer.md", "read-only")),
    ("tester", ("tester.md", "branch")),
];

/// Kind -> what it does, in words.
pub const KIND_JOBS: &[(&str, &str)] = &[
    ("planner", "reads the project, splits the task and hands out pieces; never edits"),
    ("builder", "makes one change at a time with tests, on its own branch"),
    ("reviewer", "reads every diff and gives a verdict; never edits"),
    ("tester", "writes tests and runs the full suite, on its own branch"),
];

/// The built-in teams and briefs, compiled into the binary.
const EMBEDDED: &[(&str, &str)] = &[
    ("briefs/builder.md", include_str!("../../../../assieme/presets/briefs/builder.md")),
    ("briefs/critic.md", include_str!("../../../../assieme/presets/briefs/critic.md")),
    ("briefs/planner.md", include_str!("../../../../assieme/presets/briefs/planner.md")),
    ("briefs/reviewer.md", include_str!("../../../../assieme/presets/briefs/reviewer.md")),
    ("briefs/steward.md", include_str!("../../../../assieme/presets/briefs/steward.md")),
    ("briefs/tester.md", include_str!("../../../../assieme/presets/briefs/tester.md")),
    ("builder-reviewer/team.toml", include_str!("../../../../assieme/presets/builder-reviewer/team.toml")),
    ("full/team.toml", include_str!("../../../../assieme/presets/full/team.toml")),
    ("one-agent/team.toml", include_str!("../../../../assieme/presets/one-agent/team.toml")),
];

/// A tool id's pattern.
pub const TOOL_ID: &str = r"^(server|plugin|skill|extension):[^\x00-\x1f\x7f]{1,200}$";
/// A role's keys in team.toml, besides its brief (a file of its own).
const ROLE_KEYS: [&str; 21] = [
    "name", "agent", "job", "checkout", "parent", "model", "effort", "group", "kind", "level", "worktree", "branch",
    "resume", "musician", "container", "share_signin", "tools_new", "tools_off", "tools_known", "owns", "rules",
];
const NEEDS_SAVE: &str = "This folder has no saved version yet. Agents work from a saved version of your project, so it needs one.";
const TOO_LARGE: &str = "This folder is very large. Choose the folder for just this project.";
const SUGGEST_S: f64 = 170.0;
const MAX_ROLES: usize = 8;

/// A New team problem, tied to the form field it is about.
#[derive(Debug, Clone, PartialEq)]
pub struct NewTeamError {
    pub field: String,
    pub message: String,
    pub detail: String,
}

impl NewTeamError {
    fn new(field: &str, message: impl Into<String>) -> NewTeamError {
        NewTeamError { field: field.into(), message: message.into(), detail: String::new() }
    }
}

impl From<NewTeamError> for BrainError {
    fn from(e: NewTeamError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

/// Anything a start meets is shown next to the form's start button.
impl From<BrainError> for NewTeamError {
    fn from(e: BrainError) -> NewTeamError {
        NewTeamError { field: "start".into(), message: e.message, detail: e.detail.unwrap_or_default() }
    }
}

impl From<preset::PresetError> for NewTeamError {
    fn from(e: preset::PresetError) -> NewTeamError {
        BrainError::from(e).into()
    }
}

impl From<worktree::WorktreeError> for NewTeamError {
    fn from(e: worktree::WorktreeError) -> NewTeamError {
        BrainError::from(e).into()
    }
}

fn state_dir() -> PathBuf {
    crate::quiet::state_dir()
}

/// The built-in team presets, as a folder: written out of the binary (see `presets_dir`), so `preset::load` reads
/// them like any saved team.
pub static PRESETS_DIR: LazyLock<PathBuf> = LazyLock::new(|| state_dir().join("presets"));

/// `PRESETS_DIR` with every built-in file in it: what is missing or has changed is written again.
fn presets_dir() -> PathBuf {
    for (rel, text) in EMBEDDED {
        let path = PRESETS_DIR.join(rel);
        if std::fs::read_to_string(&path).ok().as_deref() != Some(text) {
            let _ = std::fs::create_dir_all(path.parent().unwrap_or(Path::new(".")));
            let _ = std::fs::write(&path, text);
        }
    }
    PRESETS_DIR.clone()
}

pub fn teams_dir() -> PathBuf {
    state_dir().join("teams")
}

pub fn kind_brief(kind: &str) -> String {
    let file = KINDS.iter().find(|(k, _)| *k == kind).map_or("builder.md", |(_, (f, _))| *f);
    EMBEDDED.iter().find(|(r, _)| *r == format!("briefs/{file}")).map_or_else(String::new, |(_, t)| t.to_string())
}

fn kind_checkout(kind: &str) -> Option<&'static str> {
    KINDS.iter().find(|(k, _)| *k == kind).map(|(_, (_, c))| *c)
}

fn kind_job(kind: &str) -> &'static str {
    KIND_JOBS.iter().find(|(k, _)| *k == kind).map_or("", |(_, j)| *j)
}

/// A role as the form holds it: the keys only brought-over or chosen roles have are left out when empty.
pub fn role_dict(r: &Role) -> Value {
    let mut m = json!({"name": r.name, "agent": r.agent, "job": r.job, "checkout": r.checkout, "parent": r.parent,
        "brief": r.brief, "model": r.model, "effort": r.effort, "group": r.group, "kind": r.kind, "owns": r.owns,
        "level": r.level, "rules": r.rules, "container": r.container, "share_signin": r.share_signin});
    for (k, v) in [("worktree", &r.worktree), ("branch", &r.branch), ("resume", &r.resume), ("musician", &r.musician)] {
        if let Some(v) = v.as_ref().filter(|v| !v.is_empty()) {
            m[k] = json!(v);
        }
    }
    for (k, v) in [("tools_off", &r.tools_off), ("tools_known", &r.tools_known)] {
        if !v.is_empty() {
            m[k] = json!(v);
        }
    }
    if !r.tools_new {
        m["tools_new"] = json!(false);
    }
    m
}

fn read_toml(path: &Path) -> Option<toml::Table> {
    toml::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// A saved team folder as New team's preset.
pub fn as_preset(folder: &Path, id: &str, label: Option<&str>) -> Result<Value, BrainError> {
    let tm = preset::load(folder, false)?;
    let data = read_toml(&folder.join("team.toml")).unwrap_or_default();
    let label = label.or_else(|| data.get("label")?.as_str()).unwrap_or(id);
    // load orders parents first; the form keeps the file's order
    let roles: Vec<Value> = data["role"].as_array().into_iter().flatten().filter_map(|r| tm.roles.iter().find(|x| Some(x.name.as_str()) == r["name"].as_str())).map(role_dict).collect();
    Ok(json!({"id": id, "label": label, "roles": roles}))
}

pub fn presets(teams: Option<&Path>) -> Value {
    let teams = teams.map_or_else(teams_dir, Path::to_path_buf);
    let builtin: Vec<Value> = BUILTIN.iter().filter_map(|id| as_preset(&presets_dir().join(id), id, None).ok()).collect();
    let mut saved: Vec<PathBuf> = std::fs::read_dir(&teams).into_iter().flatten().flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect();
    saved.sort_by_key(|p| std::cmp::Reverse(std::fs::metadata(p).and_then(|m| m.modified()).ok()));
    // a half-written or hand-edited folder: try the next one
    let last = saved.iter().find_map(|f| as_preset(f, &f.file_name()?.to_string_lossy(), Some("Same as last time")).ok());
    // the Project step's recent folders: newest first, each once
    let mut recent: Vec<String> = vec![];
    for f in &saved {
        let project = read_toml(&f.join("team.toml")).and_then(|d| d.get("project")?.as_str().map(String::from));
        recent.extend(project.filter(|p| !p.is_empty() && !recent.contains(p)));
    }
    recent.truncate(3);
    let kinds: Map<String, Value> = KINDS.iter().map(|(k, (_, c))| (k.to_string(), json!({"brief": kind_brief(k), "job": kind_job(k), "checkout": c}))).collect();
    json!({"presets": builtin, "practices": preset::practices(None), "last": last, "recent": recent, "models": model_options(),
           "practice_defaults": crate::settings::load(None)["practices"], "kinds": kinds, "permissions": d2::table()})
}

/// The models and thinking efforts New team offers. Codex's come from the list Codex keeps on this Mac (an
/// internal file: if it's missing or changes shape, Codex agents get their default only).
pub fn model_options_at(cache: &Path) -> Value {
    let (claude, all) = (launch::efforts("claude"), launch::efforts("codex"));
    let model_re = Regex::new(launch::MODEL).expect("a fixed pattern");
    let read: Value = std::fs::read(cache).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
    let codex: Vec<Value> = read["models"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|m| model_re.is_match(m["slug"].as_str().unwrap_or("")))
        .map(|m| {
            let levels: Vec<&str> = m["supported_reasoning_levels"].as_array().into_iter().flatten().filter_map(|x| x["effort"].as_str()).collect();
            let efforts: Vec<&str> = all.iter().copied().filter(|e| levels.contains(e)).collect();
            json!({"id": m["slug"], "label": m["slug"], "about": m["description"].as_str().unwrap_or(""), "efforts": efforts})
        })
        .collect();
    let common: Vec<&&str> = all.iter().filter(|e| !codex.is_empty() && codex.iter().all(|m| m["efforts"].as_array().is_some_and(|a| a.iter().any(|x| x == **e)))).collect();
    let models: Vec<Value> = CLAUDE_MODELS.iter().map(|(i, n)| json!({"id": i, "label": n, "efforts": claude})).collect();
    let none = json!({"models": [], "efforts": []}); // their own default only
    json!({"claude": {"models": models, "efforts": claude}, "codex": {"models": codex, "efforts": common},
           "gemini": none, "opencode": none, "cursor": none, "antigravity": none})
}

pub fn model_options() -> Value {
    let home = std::env::var_os("CODEX_HOME").filter(|v| !v.is_empty()).map(PathBuf::from);
    let home = home.unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".codex"));
    model_options_at(&home.join("models_cache.json"))
}

fn expanduser(path: &str) -> String {
    match path.strip_prefix("~") {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => format!("{}{rest}", std::env::var("HOME").unwrap_or_default()),
        _ => path.to_string(),
    }
}

pub fn project_check(path: &str) -> Value {
    use crate::setup::{self, SetupError};
    if !setup::git_ready() {
        // a remote machine: nothing to click here
        let remote = !cfg!(target_os = "macos") || std::env::var_os("ASSIEME_ASM_SOCKET").is_some_and(|v| !v.is_empty());
        return if remote {
            json!({"ok": false, "reason": "This machine needs git. Install it there, then choose the folder again."})
        } else {
            json!({"ok": false, "reason": "This needs Apple's developer tools first.", "tools": true})
        };
    }
    let path = expanduser(path);
    if !Path::new(&path).is_dir() {
        return json!({"ok": false, "reason": "This folder doesn't exist."});
    }
    let too_large = json!({"ok": false, "reason": TOO_LARGE, "too_large": true});
    if setup::too_broad(&path) {
        return too_large; // before anything walks the folder
    }
    if worktree::check_repo(&path).is_err() {
        // no repository, or no commits: counts the files a first version would hold
        return match setup::folder_preview(&path) {
            Err(SetupError::TooLarge(_)) => too_large,
            _ => json!({"ok": false, "reason": NEEDS_SAVE, "setup": true}),
        };
    }
    match setup::uncommitted(&path) {
        Err(SetupError::TooLarge(_)) => too_large,
        // shown as the folder's problem; nothing is offered to save
        Err(SetupError::Unfinished(u)) => json!({"ok": false, "reason": u.0}),
        Err(SetupError::Failed(_)) | Ok(0) => json!({"ok": true}),
        Ok(n) => json!({"ok": true, "uncommitted": n}),
    }
}

fn str_of<'a>(v: &'a Value, key: &str) -> Option<&'a str> {
    v.get(key).and_then(Value::as_str)
}

pub fn validate(roles: &[Value]) -> Result<(), NewTeamError> {
    if roles.is_empty() {
        return Err(NewTeamError::new("roles", "Add at least one agent."));
    }
    let names: Vec<&str> = roles.iter().map(|r| str_of(r, "name").unwrap_or("")).collect();
    let (name_re, branch_re, resume_re, tool_re) = (r"^[a-z0-9-]+$", r"^[A-Za-z0-9._/-]+$", r"^[A-Za-z0-9][A-Za-z0-9-]*$", TOOL_ID);
    let is = |re: &str, s: &str| Regex::new(re).is_ok_and(|r| r.is_match(s));
    for (i, r) in roles.iter().enumerate() {
        let get = |k: &str| str_of(r, k).filter(|s| !s.is_empty());
        let flag = |k: &str| matches!(r.get(k), None | Some(Value::Null | Value::Bool(_)));
        let list_ok = |k: &str| r.get(k).is_none_or(|v| v.is_null() || v.as_array().is_some_and(|a| a.iter().all(|x| x.as_str().is_some_and(|s| is(tool_re, s)))));
        let checkout = str_of(r, "checkout").unwrap_or("branch");
        let agent = str_of(r, "agent").unwrap_or("");
        let bad_tools = "That list of skills and servers can't be used.";
        let problem = if !is(name_re, names[i]) || names[..i].contains(&names[i]) {
            Some("Names use lower-case letters, digits and dashes, and must differ.".to_string())
        } else if !preset::AGENTS.contains(&agent) {
            Some("Choose which agent runs this.".into())
        } else if !flag("container") || !flag("share_signin") {
            Some("Choose where this agent works.".into())
        } else if !["branch", "read-only"].contains(&checkout) {
            Some(format!("Unknown checkout '{checkout}'."))
        } else if get("worktree").is_some_and(|w| !w.starts_with('/')) {
            Some("Its folder needs a full path.".into())
        } else if get("branch").is_some_and(|b| !is(branch_re, b) || b.starts_with('-') || b.contains("..")) {
            Some(format!("'{}' isn't a branch name Assieme can use.", get("branch").unwrap_or("")))
        } else if get("resume").is_some_and(|s| !is(resume_re, s)) {
            Some("That conversation can't be picked up.".into()) // it goes on the agent's command line
        } else if !list_ok("tools_off") || !list_ok("tools_known") || !flag("tools_new") {
            Some(bad_tools.into())
        } else if r.get("musician").is_some_and(|m| !m.is_null() && !m.as_str().is_some_and(|m| musicians::INSTRUMENTS.contains(&m))) {
            Some("That instrument isn't one Assieme draws.".into())
        } else if get("parent").is_some_and(|p| !names.contains(&p)) {
            Some(format!("Reports to '{}', which isn't in this team.", get("parent").unwrap_or("")))
        } else if str_of(r, "brief").is_none_or(|b| b.trim().is_empty()) {
            Some("Every agent needs a brief.".into())
        } else {
            let owns: Vec<String> = r["owns"].as_array().into_iter().flatten().filter_map(|x| x.as_str().map(String::from)).collect();
            let rules: Vec<Value> = r["rules"].as_array().cloned().unwrap_or_default();
            launch::check_model_effort(agent, str_of(r, "model"), str_of(r, "effort"))
                .or_else(|| str_of(r, "kind").filter(|k| kind_checkout(k).is_none()).map(|k| format!("Unknown role '{k}'.")))
                .or_else(|| str_of(r, "level").filter(|l| !d2::levels().iter().any(|x| x == l)).map(|l| format!("Unknown permission level '{l}'.")))
                .or_else(|| d2::check_owns(&owns))
                .or_else(|| d2::check_rules(&rules))
        };
        if let Some(p) = problem {
            return Err(NewTeamError::new(&format!("role:{i}"), p));
        }
    }
    Ok(())
}

pub fn team_name(project: &str, taken: &BTreeSet<String>) -> String {
    let base = Path::new(project.trim_end_matches('/')).file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    let slug = Regex::new("[^a-z0-9]+").expect("a fixed pattern").replace_all(&base, "-");
    let slug = slug.trim_matches('-');
    let base = if slug.is_empty() { "team" } else { slug };
    let mut name = base.to_string();
    for n in 2.. {
        if !taken.contains(&name) {
            break;
        }
        name = format!("{base}-{n}");
    }
    name
}

/// A role as team.toml holds it: what is empty or default is left out.
fn role_table(r: &Value) -> toml::Value {
    let mut m = toml::Table::new();
    for k in ROLE_KEYS {
        let keep = match (&r[k], k) {
            (Value::Null, _) => false,
            (Value::String(s), _) => !s.is_empty() || k == "job",
            (Value::Array(a), _) => !a.is_empty(),
            (Value::Bool(b), "tools_new") => !b,
            (Value::Bool(b), _) => *b,
            _ => false,
        };
        if keep && let Ok(v) = toml::Value::try_from(&r[k]) {
            m.insert(k.into(), v);
        }
    }
    m.entry("checkout").or_insert_with(|| "branch".into());
    toml::Value::Table(m)
}

/// Step 32: an agent that doesn't take newly installed tools remembers what was there, so a later one is told apart.
fn with_known(mut r: Value, project: &str) -> Value {
    if r["tools_new"] == false && r["tools_known"].as_array().is_none_or(Vec::is_empty) {
        let items = crate::agenttools::find(str_of(&r, "agent").unwrap_or(""), Some(project), false);
        r["tools_known"] = items["items"].as_array().into_iter().flatten().map(|i| i["id"].clone()).collect();
    }
    r
}

fn instruments(roles: &[Value]) -> Vec<String> {
    musicians::pick(&roles.iter().map(|r| (str_of(r, "kind"), str_of(r, "musician"))).collect::<Vec<_>>())
}

/// Write the team folder `preset::load` reads. `scripts`: setup, run and archive lines brought over from another
/// tool (step 30).
pub fn save(project: &str, roles: &[Value], practices: &Map<String, Value>, name: &str, teams: &Path, scripts: Option<&BTreeMap<String, String>>) -> Result<PathBuf, BrainError> {
    let scripts: BTreeMap<_, _> = scripts.into_iter().flatten().filter(|(_, v)| !v.is_empty()).collect();
    if let Some(k) = scripts.keys().find(|k| !preset::SCRIPTS.contains(&k.as_str())) {
        return Err(BrainError::new(format!("Unknown script {k}.")));
    }
    let picked = instruments(roles); // step 31: saved once
    let roles: Vec<Value> = roles
        .iter()
        .zip(picked)
        .map(|(r, m)| {
            let mut r = with_known(r.clone(), project);
            r["musician"] = json!(m);
            r
        })
        .collect();
    let folder = teams.join(name);
    let io = |e: std::io::Error| BrainError::new(e.to_string());
    if folder.is_dir() {
        std::fs::remove_dir_all(&folder).map_err(io)?;
    }
    std::fs::create_dir_all(&folder).map_err(io)?;
    let on = |v: &Value| !matches!(v, Value::Null | Value::Bool(false)) && v.as_str() != Some("") && v.as_f64() != Some(0.0);
    let mut file = toml::Table::from_iter([
        ("name".to_string(), name.into()),
        ("project".to_string(), preset::absolute(Path::new(project)).to_string_lossy().as_ref().into()),
        ("role".to_string(), toml::Value::Array(roles.iter().map(role_table).collect())),
    ]);
    if !practices.is_empty() {
        file.insert("practices".into(), toml::Value::Table(practices.iter().map(|(k, v)| (k.clone(), on(v).into())).collect()));
    }
    if !scripts.is_empty() {
        file.insert("scripts".into(), toml::Value::Table(scripts.iter().map(|(k, v)| (k.to_string(), v.as_str().into())).collect()));
    }
    for r in &roles {
        let brief = str_of(r, "brief").unwrap_or("").trim_end_matches('\n');
        std::fs::write(folder.join(format!("{}.md", str_of(r, "name").unwrap_or(""))), format!("{brief}\n")).map_err(io)?;
    }
    std::fs::write(folder.join("team.toml"), toml::to_string(&file).map_err(|e| BrainError::new(e.to_string()))?).map_err(io)?;
    Ok(folder)
}

/// The saved team and its roles as the form holds them, in the file's order.
fn saved(folder: &Path) -> Result<(Team, Vec<Value>), BrainError> {
    let tm = preset::load(folder, false)?;
    let order = read_toml(&folder.join("team.toml")).map(|d| d["role"].as_array().cloned().unwrap_or_default()).unwrap_or_default();
    let roles = order.iter().filter_map(|r| tm.roles.iter().find(|x| Some(x.name.as_str()) == r["name"].as_str())).map(role_dict).collect();
    Ok((tm, roles))
}

fn resave(tm: &Team, roles: &[Value], folder: &Path) -> Result<(), BrainError> {
    save(&tm.project, roles, &tm.practices, &tm.name, folder.parent().unwrap_or(Path::new(".")), Some(&tm.scripts)).map(|_| ())
}

/// Take a role back out of a saved team, so it doesn't return at the next start and its name is free again.
pub fn forget_role(folder: &Path, name: &str) -> Result<(), BrainError> {
    let (tm, mut roles) = saved(folder)?;
    let up = tm.roles.iter().find(|r| r.name == name).and_then(|g| g.parent.clone());
    roles.retain(|r| r["name"] != name);
    for r in &mut roles {
        // whoever reported to it now reports one level up, or the saved team would no longer load
        if r["parent"] == name {
            r["parent"] = json!(up);
        }
    }
    resave(&tm, &roles, folder)
}

/// Save the team again with one role's fields changed. `changes` holds the fields by their Python names.
pub fn rewrite_role(folder: &Path, name: &str, changes: &Map<String, Value>) -> Result<(), BrainError> {
    let (tm, mut roles) = saved(folder)?;
    if let Some(r) = roles.iter_mut().find(|r| r["name"] == name) {
        r.as_object_mut().map(|m| m.extend(changes.clone()));
    }
    resave(&tm, &roles, folder)
}

fn change(field: &str, value: Value) -> Map<String, Value> {
    Map::from_iter([(field.to_string(), value)])
}

/// After a brought-over agent's first start: later starts don't resume that conversation again.
pub fn clear_resume(folder: &Path, name: &str) -> Result<(), BrainError> {
    rewrite_role(folder, name, &change("resume", Value::Null))
}

/// After merging a brought-over agent: its next start makes an Assieme copy; the old folder stays where it is.
pub fn clear_worktree(folder: &Path, name: &str) -> Result<(), BrainError> {
    let mut gone = change("worktree", Value::Null);
    gone.insert("branch".into(), Value::Null);
    rewrite_role(folder, name, &gone)
}

/// What the start record says about brought-over agents whose folder has gone: they get a new copy.
fn gone_notes(roles: &[Role]) -> Vec<String> {
    roles
        .iter()
        .filter_map(|r| {
            let (w, b) = (r.worktree.as_deref().filter(|w| !w.is_empty() && !Path::new(w).is_dir())?, r.branch.as_deref().filter(|b| !b.is_empty())?);
            let base = Path::new(w.trim_end_matches('/')).file_name()?.to_string_lossy().into_owned();
            Some(format!("{base}'s folder isn't there any more. Its branch {b} is kept: start gives it a new copy."))
        })
        .collect()
}

fn load_saved(name: &str, teams: Option<&Path>, practices: bool) -> Result<Team, preset::PresetError> {
    preset::load(&teams.map_or_else(teams_dir, Path::to_path_buf).join(name), practices)
}

/// {role: {job, group, parent}}; {} for a team started from the command line.
pub fn roster(name: &str, teams: Option<&Path>) -> BTreeMap<String, Value> {
    // with the roles bigger-team presets add
    let roles = load_saved(name, teams, true).map(|t| t.roles).unwrap_or_default();
    roles.into_iter().map(|r| (r.name, json!({"job": r.job, "group": r.group, "parent": r.parent}))).collect()
}

/// Step 31: each role's instrument and colours, for the app to draw. {} for a team with no saved folder.
pub fn band(name: &str, teams: Option<&Path>) -> Value {
    let Ok(tm) = load_saved(name, teams, true) else { return json!({}) };
    let kinds: Vec<Option<&str>> = tm.roles.iter().map(|r| r.kind.as_deref()).collect();
    let (assigned, rules) = (musicians::assign(&tm.roles), musicians::rules(&kinds));
    // chosen_for: why the rule picked it, only while the person hasn't picked another
    let one = |i: usize| (tm.roles[i].name.clone(), json!({"musician": assigned[i], "palette": musicians::palette(&kinds, i), "chosen_for": (assigned[i] == rules[i].0).then_some(&rules[i].1)}));
    Value::Object((0..tm.roles.len()).map(one).collect())
}

/// Step 31: the instrument picked in Agent details, saved with the role. Only how it looks changes.
pub fn set_musician(name: &str, role: &str, musician: &str, teams: Option<&Path>) -> Result<Value, NewTeamError> {
    let field = |m: &str| NewTeamError::new("musician", m);
    if !musicians::INSTRUMENTS.contains(&musician) {
        return Err(field("That instrument isn't one Assieme draws."));
    }
    let tm = load_saved(name, teams, false).map_err(|_| field("This team was started from the command line, so it has no saved instruments."))?;
    if !tm.roles.iter().any(|r| r.name == role) {
        return Err(field(&format!("{role} is not in this team.")));
    }
    rewrite_role(&teams.map_or_else(teams_dir, Path::to_path_buf).join(name), role, &change("musician", json!(musician)))?;
    Ok(json!({"ok": true}))
}

type Shared = Arc<Mutex<Value>>;

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// A finished start stays on First run for 10 minutes; one that failed until the person retries or removes it.
const KEEP_DONE_S: f64 = 600.0;

/// A start's progress record, as the snapshot shows it (`done_at` is dropped from it).
fn record(team: &str, project: &str, roles: &[&str]) -> Shared {
    let roles: Vec<Value> = roles.iter().map(|r| json!({"role": r, "state": "starting", "detail": ""})).collect();
    Arc::new(Mutex::new(json!({"team": team, "project": project, "done": false, "error": null, "done_at": null, "roles": roles})))
}

fn failed(r: &Value) -> bool {
    r["done"] == true && (!r["error"].is_null() || r["roles"].as_array().into_iter().flatten().any(|x| ["not-reporting", "exited"].iter().any(|s| x["state"] == *s)))
}

/// Starts teams in the background and remembers the ones starting. Checks and saves at once, then runs
/// `team::start` in a thread. Records live in memory; the teams themselves outlive the brain.
pub struct Starter {
    rt: Runtime,
    teams: PathBuf,
    /// The teams of scheduled runs, which may share a project with another team.
    scheduled: Box<dyn Fn() -> BTreeSet<String> + Send + Sync>,
    /// The records, and project -> name while checks run.
    recs: Mutex<(Vec<Shared>, BTreeMap<String, String>)>,
}

impl Starter {
    pub fn new(rt: Runtime, teams: PathBuf, scheduled: Box<dyn Fn() -> BTreeSet<String> + Send + Sync>) -> Starter {
        Starter { rt, teams, scheduled, recs: Mutex::default() }
    }

    fn project_of(&self, name: &str) -> Option<String> {
        // None: started from the command line, or the folder is gone
        read_toml(&self.teams.join(name).join("team.toml"))?.get("project")?.as_str().map(String::from)
    }

    /// `allow_scripts`: the brought-over scripts the person said yes to in New team, for this team's name.
    /// `name` is a scheduled run's own team name; New team leaves it to the project's folder name.
    #[allow(clippy::too_many_arguments)]
    pub fn start(&self, project: &str, task: &str, roles: &[Value], practices: &Value, name: Option<&str>, scripts_: Option<&BTreeMap<String, String>>, allow_scripts: &[String]) -> Result<Value, BrainError> {
        validate(roles)?;
        let project = expanduser(project);
        let check = project_check(&project);
        if check["ok"] != true {
            return Err(NewTeamError::new("project", check["reason"].as_str().unwrap_or("")).into());
        }
        let project = preset::absolute(Path::new(&project)).to_string_lossy().into_owned();
        // no live teams while the assieme core isn't running yet
        let live: BTreeSet<String> = team::status(&self.rt, None).unwrap_or_default().iter().filter(|r| r["state"] == "live").filter_map(|r| r["team"].as_str().map(String::from)).collect();
        let name = {
            // held only for bookkeeping: git work below must not block records()
            let mut st = lock(&self.recs);
            let open: Vec<Value> = st.0.iter().map(|r| lock(r).clone()).filter(|r| r["done"] == false).collect();
            if st.1.contains_key(&project) || open.iter().any(|r| r["project"] == project.as_str()) {
                return Err(NewTeamError::new("start", "This team is still starting.").into());
            }
            let sched = (self.scheduled)();
            let mine: BTreeSet<&str> = roles.iter().filter_map(|r| str_of(r, "name")).collect();
            let blocks = |other: &str| {
                // worktrees are per role name: these would share one; else New team beside New team: one per project
                roster(other, Some(&self.teams)).keys().any(|k| mine.contains(k.as_str())) || (!sched.contains(other) && !name.is_some_and(|n| sched.contains(n)))
            };
            if let Some(first) = live.iter().find(|n| self.project_of(n).as_deref() == Some(&project) && blocks(n)) {
                return Err(NewTeamError::new("project", format!("This project already has a running team: {first}. Stop it first.")).into());
            }
            let taken: BTreeSet<String> = live.iter().cloned().chain(st.1.values().cloned()).chain(open.iter().filter_map(|r| r["team"].as_str().map(String::from))).collect();
            if let Some(n) = name.filter(|n| taken.contains(*n)) {
                return Err(NewTeamError::new("start", format!("{n} is still running.")).into());
            }
            let chosen = name.map_or_else(|| team_name(&project, &taken), String::from);
            st.1.insert(project.clone(), chosen.clone());
            chosen
        };
        let built = (|| -> Result<(Team, BTreeMap<String, String>), NewTeamError> {
            let folder = save(&project, roles, practices.as_object().unwrap_or(&Map::new()), &name, &self.teams, scripts_)?;
            let tm = preset::load(&folder, true)?;
            for which in allow_scripts {
                if let Some(line) = tm.scripts.get(which) {
                    scripts::allow(&name, which, line)?;
                }
            }
            let missing = launch::missing_clis(&tm.roles);
            if !missing.is_empty() {
                return Err(NewTeamError::new("start", format!("not installed: {}. Install it, then start again.", missing.join(", "))));
            }
            container_ready(&tm.roles)?;
            // Copies made by this start get the team's setup script; brought-over folders are already set up.
            let fresh = tm.roles.iter().filter(|r| r.checkout != "read-only" && !r.worktree.as_deref().is_some_and(|d| Path::new(d).is_dir())).map(|r| (r.name.clone(), worktree::folder(&tm, r))).filter(|(_, f)| !Path::new(f).is_dir()).collect();
            for r in &tm.roles {
                worktree::ensure(&tm, r)?; // every worktree now, so a dirty one is reported to the form, not the log
            }
            Ok((tm, fresh))
        })();
        lock(&self.recs).1.remove(&project);
        let (tm, fresh) = built?;
        let rec = record(&name, &project, &tm.roles.iter().map(|r| r.name.as_str()).collect::<Vec<_>>());
        let notes = gone_notes(&tm.roles);
        if !notes.is_empty() {
            lock(&rec)["notes"] = json!(notes);
        }
        lock(&self.recs).0.push(rec.clone());
        let (rt, teams, task) = (self.rt.clone(), self.teams.clone(), task.to_string());
        std::thread::spawn(move || {
            // anything here would otherwise vanish with the thread
            let run = || -> Result<(), BrainError> {
                setup_scripts(&rec, &tm, &fresh);
                // Clicking Start in the app is the person's agreement to trust the folder.
                let results = team::start(&tm, Some(&task), &rt, &|_| true, None)?;
                for r in tm.roles.iter().filter(|r| r.resume.is_some()) {
                    // a conversation brought over is picked up once; later starts begin fresh
                    if results.iter().any(|s| s.role == r.name && s.address.is_some()) {
                        clear_resume(&teams.join(&tm.name), &r.name)?;
                    }
                }
                fill(&rec, &results);
                Ok(())
            };
            finish(&rec, std::panic::catch_unwind(std::panic::AssertUnwindSafe(run)), |_| {});
        });
        Ok(json!({"team": name}))
    }

    /// One more agent for a running team: added to its saved folder, then started like any other.
    pub fn add_agent(&self, team_name: &str, role: &Value) -> Result<Value, NewTeamError> {
        let folder = self.teams.join(team_name);
        if !folder.join("team.toml").is_file() {
            return Err(NewTeamError::new("start", "This team was started from the command line, so agents can't be added here."));
        }
        // no core running means nothing of this team is
        let rows = self.rt.list(&BTreeMap::from([(team::TEAM.to_string(), team_name.to_string())]), false).unwrap_or_default();
        if !rows.iter().any(|r| r["state"] == "live" && r["labels"][team::TEAM] == team_name) {
            return Err(NewTeamError::new("start", "This team has stopped. Start it again to add an agent."));
        }
        let mut role = role.clone();
        let mut all: Vec<Value> = saved(&folder)?.1;
        all.push(role.clone());
        role["musician"] = json!(instruments(&all).pop()); // the next by the rule; nobody else's changes
        if let Some(checkout) = str_of(&role, "kind").and_then(kind_checkout) {
            // kept in team.toml: later instruments and colours count by it
            role["checkout"] = json!(str_of(&role, "checkout").unwrap_or(checkout));
            if str_of(&role, "brief").is_none_or(|b| b.trim().is_empty()) {
                role["brief"] = json!(kind_brief(str_of(&role, "kind").unwrap_or("")));
            }
        }
        all.pop();
        all.push(role.clone());
        validate(&all)?;
        let new = Role { container: role["container"] == true, ..Role::new(str_of(&role, "name").unwrap_or(""), str_of(&role, "agent").unwrap_or("")) };
        let missing = launch::missing_clis(std::slice::from_ref(&new));
        if !missing.is_empty() {
            return Err(NewTeamError::new("start", format!("not installed: {}. Install it, then try again.", missing.join(", "))));
        }
        container_ready(std::slice::from_ref(&new))?;
        let name = new.name.clone();
        let io = |e: std::io::Error| NewTeamError::new("start", e.to_string());
        std::fs::write(folder.join(format!("{name}.md")), format!("{}\n", str_of(&role, "brief").unwrap_or("").trim_end_matches('\n'))).map_err(io)?;
        let mut file = std::fs::read_to_string(folder.join("team.toml")).map_err(io)?;
        file.push_str(&format!("\n{}", toml::to_string(&toml::Table::from_iter([("role".to_string(), toml::Value::Array(vec![role_table(&role)]))])).unwrap_or_default()));
        std::fs::write(folder.join("team.toml"), file).map_err(io)?;
        let tm = preset::load(&folder, true)?;
        if let Err(e) = worktree::ensure(&tm, tm.roles.iter().find(|r| r.name == name).unwrap_or(&new)) {
            let _ = forget_role(&folder, &name);
            return Err(e.into());
        }
        let rec = record(team_name, &tm.project, &[&name]);
        lock(&self.recs).0.push(rec.clone());
        let rt = self.rt.clone();
        std::thread::spawn(move || {
            let run = || -> Result<(), BrainError> {
                let s = team::start(&tm, None, &rt, &|_| true, Some(&name))?.into_iter().next().ok_or_else(|| BrainError::new("no agent was started"))?;
                fill(&rec, std::slice::from_ref(&s));
                if s.state == "exited" {
                    forget_role(&folder, &name)?;
                } else {
                    crate::live::record(crate::live::Record { action: "add_agent", agent: &name, reason: "You added it to this team.", team: Some(&tm.name), address: s.address.as_deref(), ..Default::default() });
                }
                Ok(())
            };
            finish(&rec, std::panic::catch_unwind(std::panic::AssertUnwindSafe(run)), |_| {
                let _ = forget_role(&folder, &name);
            });
        });
        Ok(json!({"team": team_name}))
    }

    /// The person retried or removed an agent whose start failed: its "Couldn't start" row goes.
    pub fn forget(&self, team_name: &str, role: &str) {
        let mut st = lock(&self.recs);
        for r in &st.0 {
            let mut r = lock(r);
            if r["team"] == team_name {
                r["roles"] = r["roles"].as_array().into_iter().flatten().filter(|x| x["role"] != role).cloned().collect();
            }
        }
        st.0.retain(|r| lock(r)["roles"].as_array().is_some_and(|a| !a.is_empty()));
    }

    pub fn records(&self) -> Vec<Value> {
        let mut st = lock(&self.recs);
        st.0.retain(|r| {
            let r = lock(r);
            r["done"] == false || now() - r["done_at"].as_f64().unwrap_or(0.0) <= KEEP_DONE_S || failed(&r)
        });
        st.0.iter().map(|r| lock(r).clone()).map(|mut r| { r.as_object_mut().map(|m| m.remove("done_at")); r }).collect()
    }
}

fn container_ready(roles: &[Role]) -> Result<(), NewTeamError> {
    for r in roles.iter().filter(|r| r.container) {
        if let Some(p) = d2::container_problem(&r.agent).filter(|p| !p.is_empty()) {
            return Err(NewTeamError::new("start", format!("{} runs in a container. {p}", r.name)));
        }
    }
    Ok(())
}

fn fill(rec: &Shared, results: &[team::Started]) {
    let mut r = lock(rec);
    for row in r["roles"].as_array_mut().into_iter().flatten() {
        if let Some(s) = results.iter().find(|s| row["role"] == s.role.as_str()) {
            (row["state"], row["detail"]) = (json!(s.state), json!(s.note));
        }
    }
}

/// Marks a start done, with the error a failure or panic left; `on_error` runs first.
fn finish(rec: &Shared, outcome: std::thread::Result<Result<(), BrainError>>, on_error: impl FnOnce(&str)) {
    let error = match outcome {
        Ok(Ok(())) => None,
        Ok(Err(e)) => Some(e.message),
        Err(p) => Some(p.downcast_ref::<&str>().map(|s| s.to_string()).or_else(|| p.downcast_ref::<String>().cloned()).unwrap_or_else(|| "something went wrong".into())),
    };
    if let Some(e) = &error {
        on_error(e);
    }
    let mut r = lock(rec);
    (r["error"], r["done"], r["done_at"]) = (json!(error), json!(true), json!(now()));
}

/// The team's setup script in each copy this start made, when the person has said yes to it; otherwise the record
/// asks for that yes, and the agents start without it.
fn setup_scripts(rec: &Shared, tm: &Team, fresh: &BTreeMap<String, String>) {
    let Some(line) = tm.scripts.get("setup").filter(|_| !fresh.is_empty()) else { return };
    if !scripts::allowed(&tm.name, "setup", line) {
        lock(rec)["ask_scripts"] = json!([{"which": "setup", "line": line}]);
        return;
    }
    for (name, path) in fresh {
        let got = scripts::run(line, path, &tm.project, None, 300.0);
        let ok = got["ok"] == true;
        let reason = if ok { "Ran the team setup in its copy" } else { "The team setup didn't finish" };
        let detail = got["output"].as_str().unwrap_or("");
        crate::live::record(crate::live::Record { action: "setup", agent: name, reason, failed: !ok, detail, team: Some(&tm.name), by: "assieme", ..Default::default() });
    }
}

fn ask(prompt: &str, project: &str, label: &str) -> Result<String, crate::ask::AskError> {
    crate::ask::ask_agent(prompt, project, SUGGEST_S, Some(label), false)
}

fn ask_error(e: crate::ask::AskError, pre: &str) -> NewTeamError {
    NewTeamError::new("suggest", if e.missing { e.message } else { format!("{pre}{}", e.message) })
}

fn models_text(agents: &[String], models: &Value) -> String {
    let list = |v: &Value| v.as_array().into_iter().flatten().filter_map(|x| x.as_str()).collect::<Vec<_>>().join(", ");
    let mut out = vec![];
    for a in agents {
        let ms = models[a]["models"].as_array().cloned().unwrap_or_default();
        if a == "claude" {
            let ids: Vec<&str> = ms.iter().filter_map(|m| m["id"].as_str()).collect();
            out.push(format!("- claude models, strongest and most expensive first: {}. Efforts: {}", ids.join(", "), list(&models[a]["efforts"])));
        } else if ms.is_empty() {
            out.push(format!("- {a}: its own default only; leave model and effort out"));
        }
        for m in ms.iter().filter(|_| a != "claude") {
            let about = m["about"].as_str().filter(|s| !s.is_empty()).unwrap_or("no description");
            out.push(format!("- {a} model {} ({about}). Efforts: {}", m["id"].as_str().unwrap_or(""), list(&m["efforts"])));
        }
    }
    out.join("\n")
}

/// Help me choose: the person's own Claude Code (else Codex), read-only in the project, suggests a team with each
/// agent's model and thinking effort, among what New team offers on this machine. `size`, `review` and `budget`
/// are New team's choices as the app sends them.
pub fn suggest(project: &str, size: &Value, review: &Value, budget: &Value, task: &str, models: Option<&Value>) -> Result<Value, NewTeamError> {
    let (have, models) = (crate::ask::installed(), models.cloned().unwrap_or_else(model_options));
    let word = |kind: &str, v: &Value| {
        let s = v.as_str().map_or_else(|| v.to_string(), String::from);
        let known = [("size", "small", "a small fix"), ("size", "feature", "a feature"), ("size", "large", "a large change"), ("review", "every", "every change"),
            ("review", "end", "just the end result"), ("budget", "lean", "keep it lean (fewer agents, cheaper)"), ("budget", "fast", "speed matters more than cost")];
        known.iter().find(|(k, w, _)| *k == kind && *w == s).map_or(s.clone(), |(_, _, t)| t.to_string())
    };
    let kinds: Vec<String> = KIND_JOBS.iter().map(|(k, v)| format!("- {k}: {v}")).collect();
    let task = task.trim();
    let what = if task.is_empty() { format!("The change is {}.", word("size", size)) } else { format!("The task: {task}.") };
    let prompt = format!(
        "Suggest an agent team for the code in this folder. Read enough of the project to judge it.\n\n\
         {what} The person wants to review {}. Budget: {}.\n\n\
         Role kinds you can use:\n{}\n\n\
         Use 1 to {MAX_ROLES} agents. Each agent is one of these, the ones installed: {}. Names use lower-case letters, \
         digits and dashes, and differ.\n\n\
         Also choose each agent's model and thinking effort. Think harder where mistakes cost most (a reviewer usually \
         thinks harder than a builder); on a lean budget prefer cheaper models and lower effort. Leave either out to \
         keep that agent's own default. Only these exist on this machine:\n{}\n\n\
         Reply with only one JSON object: {{\"roles\": [{{\"name\": \"...\", \"kind\": \"...\", \"agent\": \"...\", \
         \"job\": \"one short line\", \"model\": \"...\", \"effort\": \"...\"}}], \"why\": \"one sentence\"}}",
        word("review", review),
        word("budget", budget),
        kinds.join("\n"),
        have.join(", "),
        models_text(&have, &models)
    );
    let pre = "Couldn't get a suggestion: ";
    let wrong = |m: String| NewTeamError::new("suggest", format!("{pre}{m}"));
    let text = ask(&prompt, project, "3 minutes").map_err(|e| ask_error(e, pre))?;
    let data = crate::ask::first_json(&text).filter(|d| d.get("roles").is_some_and(Value::is_array)).ok_or_else(|| wrong("the answer wasn't a team.".into()))?;
    let raw = data["roles"].as_array().cloned().unwrap_or_default();
    if raw.is_empty() || raw.len() > MAX_ROLES {
        return Err(wrong(if raw.is_empty() { "no agents in the answer.".into() } else { format!("more than {MAX_ROLES} agents.") }));
    }
    let planner = raw.iter().find(|r| r["kind"] == "planner").and_then(|r| r["name"].as_str());
    let mut roles = vec![];
    for r in &raw {
        let kind = r["kind"].as_str().unwrap_or("");
        let checkout = kind_checkout(kind).ok_or_else(|| wrong(format!("unknown role kind '{kind}'.")))?;
        let agent = r["agent"].as_str().unwrap_or("");
        if !have.iter().any(|i| i == agent) {
            return Err(wrong(format!("it chose {agent}, which is not installed.")));
        }
        // the suggested model and effort if this machine can run them; anything else is Default, which always runs
        let cli = &models[agent];
        let chosen = cli["models"].as_array().into_iter().flatten().find(|m| m["id"] == r["model"]);
        let allowed = chosen.map_or(&cli["efforts"], |c| &c["efforts"]);
        let effort = r["effort"].as_str().filter(|e| allowed.as_array().is_some_and(|a| a.iter().any(|x| x == e)));
        roles.push(json!({"name": r["name"].as_str().unwrap_or(""), "agent": agent, "job": r["job"].as_str().unwrap_or(""), "checkout": checkout,
            "parent": if kind == "planner" { Value::Null } else { json!(planner) }, "brief": kind_brief(kind),
            "model": chosen.map(|c| c["id"].clone()), "effort": effort}));
    }
    Ok(json!({"roles": roles, "why": data.get("why").and_then(Value::as_str).unwrap_or("")}))
}

/// Improve this brief: the person's own Claude Code (else Codex), read-only in the project, rewrites it. Nothing is
/// saved: the editor shows the result and the person keeps or undoes it.
pub fn improve_brief(project: &str, role: &Value) -> Result<Value, NewTeamError> {
    let kind = str_of(role, "kind").filter(|k| kind_checkout(k).is_some());
    let mut lines = vec![
        kind.map_or_else(|| "Role: custom.".to_string(), |k| format!("Role: {k} ({}).", kind_job(k))),
        format!("Its job in one line: {}", str_of(role, "job").filter(|j| !j.is_empty()).unwrap_or("(not written yet)")),
    ];
    let owns: Vec<&str> = role["owns"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
    if !owns.is_empty() {
        lines.push(format!("Files only it edits: {}", owns.join(", ")));
    }
    if let Some(p) = str_of(role, "parent").filter(|p| !p.is_empty()) {
        lines.push(format!("It reports to the agent named {p}."));
    }
    let prompt = format!(
        "Improve the brief below for one AI coding agent in a team working on the code in this folder. Read enough of \
         the project to make it concrete: name the real files, commands and checks. Keep it short: one line on what it \
         does, then a few bullet points. Keep every instruction the person wrote unless it is wrong for this project. \
         Leave out reporting format, stop rules and handoff format: they are added automatically.\n\n{}\n\n\
         The current brief, between triple quotes:\n\"\"\"\n{}\n\"\"\"\n\nReply with only one JSON object: {{\"brief\": \"...\"}}",
        lines.join("\n"),
        str_of(role, "brief").unwrap_or("").trim()
    );
    let pre = "Couldn't improve the brief: ";
    let text = ask(&prompt, project, "3 minutes").map_err(|e| ask_error(e, pre))?;
    let got = crate::ask::first_json(&text).and_then(|d| d.get("brief")?.as_str().map(String::from)).filter(|b| !b.trim().is_empty() && b.chars().count() <= 6000);
    got.map(|b| json!({"brief": format!("{}\n", b.trim())})).ok_or_else(|| NewTeamError::new("suggest", format!("{pre}the answer wasn't a brief.")))
}

/// Add a rule: plain words to a rule on the program run, for the person to see before Add. Nothing is saved.
pub fn rule_draft(project: &str, words: &str) -> Result<Value, NewTeamError> {
    let words = words.trim();
    if words.is_empty() {
        return Err(NewTeamError::new("suggest", "Write the rule in a few words first."));
    }
    let clipped: String = words.chars().take(300).collect();
    let prompt = format!(
        "A person is setting what one AI coding agent may run in the project in this folder. They wrote a rule in\n\
         plain words, quoted below between triple quotes. Turn it into one rule on the program the agent runs.\n\n\
         \"\"\"\n{clipped}\n\"\"\"\n\n\
         A rule is the program and, if needed, its first few arguments, matched as a prefix of the command run: [\"npm\",\n\
         \"publish\"] matches \"npm publish --tag next\". If the words name something in this project (a script, a launcher, a\n\
         tool), read the project to find the exact path or command, such as [\"build/cloud/run.sh\"]. Words only: no spaces\n\
         inside a word, no wildcards, no shell symbols.\n\n\
         Reply with only one JSON object: {{\"decision\": \"block\" or \"allow\", \"program\": [\"...\", \"...\"]}}\n\
         If the words don't describe running a program, reply {{\"unclear\": \"one short sentence saying why\"}}"
    );
    let pre = "Couldn't turn that into a rule: ";
    let text = crate::ask::ask_agent(&prompt, project, 120.0, Some("2 minutes"), false).map_err(|e| ask_error(e, pre))?;
    let data = crate::ask::first_json(&text).unwrap_or_default();
    if let Some(u) = data.get("unclear").and_then(Value::as_str) {
        return Err(NewTeamError::new("suggest", format!("{pre}{u}")));
    }
    let program = d2::check_rule(data.get("decision"), data.get("program")).map_err(|e| NewTeamError::new("suggest", format!("{pre}{e}")))?;
    Ok(json!({"decision": data["decision"], "program": program, "words": words}))
}
