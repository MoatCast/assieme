//! Port of `assieme/newteam.py`, owned by session D (see OWNERS.md).
//!
//! New team in the app: the starting teams, checks, saving a team folder, starting it in the background, and
//! Help me choose. The app sends the form; this module turns it into an ordinary team folder for `preset::load`.
use crate::launch::policy;
use crate::prelude::*;
use crate::team::World;
use crate::{launch, musicians, preset, scripts, team, worktree};
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

const NAME_HELP: &str = "Names use lower-case letters, digits and dashes, and must differ.";
/// How long a finished start stays on First run: 10 minutes.
const KEEP_DONE_S: f64 = 600.0;
/// A role in these after its start did not come up (needs-you is a live agent asking).
const FAILED: [&str; 2] = ["not-reporting", "exited"];
const NEEDS_SAVE: &str = "This folder has no saved version yet. Agents work from a saved version of your project, so it needs one.";
const TOO_LARGE: &str = "This folder is very large. Choose the folder for just this project.";
const NO_TOOLS: &str = "This needs Apple's developer tools first.";
const NO_GIT: &str = "This machine needs git. Install it there, then choose the folder again.";

static NAME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[a-z0-9-]+$").expect("a fixed pattern"));
/// A brought-over branch: plain git names only.
static BRANCH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9._/-]+$").expect("a fixed pattern"));
/// An agent's session id, never an option.
static RESUME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9-]*$").expect("a fixed pattern"));
/// A tool id's pattern.
pub const TOOL_ID: &str = r"^(server|plugin|skill|extension):[^\x00-\x1f\x7f]{1,200}$";
static TOOL_ID_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(TOOL_ID).expect("a fixed pattern"));

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

impl std::fmt::Display for NewTeamError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.field, self.message)
    }
}

impl From<NewTeamError> for BrainError {
    fn from(e: NewTeamError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

impl From<BrainError> for NewTeamError {
    fn from(e: BrainError) -> NewTeamError {
        NewTeamError { field: "start".into(), message: e.message, detail: e.detail.unwrap_or_default() }
    }
}

impl From<preset::PresetError> for NewTeamError {
    fn from(e: preset::PresetError) -> NewTeamError {
        NewTeamError { field: "start".into(), message: e.0, detail: String::new() }
    }
}

impl From<worktree::WorktreeError> for NewTeamError {
    fn from(e: worktree::WorktreeError) -> NewTeamError {
        NewTeamError { field: "start".into(), message: e.message, detail: e.detail }
    }
}

fn state_dir() -> PathBuf {
    crate::quiet::state_dir()
}

/// The built-in team presets, as a folder: they are written out of the binary (see `presets_dir`), so
/// `preset::load` reads them like any saved team.
pub static PRESETS_DIR: LazyLock<PathBuf> = LazyLock::new(|| state_dir().join("presets"));

/// `PRESETS_DIR` with every built-in file in it: what is missing or has changed is written again.
fn presets_dir() -> PathBuf {
    let dir = PRESETS_DIR.clone();
    for (rel, text) in EMBEDDED {
        let path = dir.join(rel);
        if std::fs::read_to_string(&path).ok().as_deref() != Some(text) {
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(&path, text);
        }
    }
    dir
}

pub fn teams_dir() -> PathBuf {
    state_dir().join("teams")
}

pub fn kind_brief(kind: &str) -> String {
    let file = KINDS.iter().find(|(k, _)| *k == kind).map_or("builder.md", |(_, (f, _))| *f);
    let rel = format!("briefs/{file}");
    EMBEDDED.iter().find(|(r, _)| *r == rel).map(|(_, t)| t.to_string()).unwrap_or_default()
}

fn kind_checkout(kind: &str) -> Option<&'static str> {
    KINDS.iter().find(|(k, _)| *k == kind).map(|(_, (_, c))| *c)
}

fn opt(s: &Option<String>) -> Value {
    s.as_ref().map_or(Value::Null, |s| json!(s))
}

pub fn role_dict(r: &Role) -> Value {
    let mut m = json!({"name": r.name, "agent": r.agent, "job": r.job, "checkout": r.checkout, "parent": opt(&r.parent),
        "brief": r.brief, "model": opt(&r.model), "effort": opt(&r.effort), "group": opt(&r.group), "kind": opt(&r.kind),
        "owns": r.owns, "level": opt(&r.level), "rules": r.rules, "container": r.container,
        "share_signin": r.share_signin});
    for (k, v) in [("worktree", &r.worktree), ("branch", &r.branch), ("resume", &r.resume)] {
        if let Some(v) = v.as_ref().filter(|v| !v.is_empty()) {
            m[k] = json!(v); // only brought-over roles
        }
    }
    if !r.tools_off.is_empty() {
        m["tools_off"] = json!(r.tools_off);
    }
    if !r.tools_new {
        m["tools_new"] = json!(false);
    }
    if !r.tools_known.is_empty() {
        m["tools_known"] = json!(r.tools_known); // only when chosen: older presets stay as they were
    }
    if let Some(mu) = r.musician.as_ref().filter(|m| !m.is_empty()) {
        m["musician"] = json!(mu);
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
    // load orders parents first; the form keeps the file's order
    let roles: Vec<Value> = data
        .get("role")
        .and_then(toml::Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|r| r.get("name")?.as_str())
        .filter_map(|n| tm.roles.iter().find(|r| r.name == n))
        .map(role_dict)
        .collect();
    let label = label.map(String::from).or_else(|| data.get("label")?.as_str().map(String::from)).unwrap_or_else(|| id.to_string());
    Ok(json!({"id": id, "label": label, "roles": roles}))
}

fn mtime(p: &Path) -> std::time::SystemTime {
    std::fs::metadata(p).and_then(|m| m.modified()).unwrap_or(std::time::UNIX_EPOCH)
}

pub fn presets(teams: Option<&Path>) -> Value {
    presets_with(teams, crate::settings::load(None)["practices"].clone(), model_options())
}

fn presets_with(teams: Option<&Path>, practice_defaults: Value, models: Value) -> Value {
    let teams = teams.map_or_else(teams_dir, Path::to_path_buf);
    let builtin: Vec<Value> = BUILTIN.iter().filter_map(|id| as_preset(&presets_dir().join(id), id, None).ok()).collect();
    let mut saved: Vec<PathBuf> =
        std::fs::read_dir(&teams).into_iter().flatten().flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect();
    saved.sort_by_key(|p| std::cmp::Reverse(mtime(p)));
    // a half-written or hand-edited folder: try the next one
    let last = saved
        .iter()
        .find_map(|f| as_preset(f, &f.file_name()?.to_string_lossy(), Some("Same as last time")).ok())
        .unwrap_or(Value::Null);
    let mut recent: Vec<String> = vec![]; // the Project step's recent folders: newest first, each once
    for f in &saved {
        let project = read_toml(&f.join("team.toml")).and_then(|d| d.get("project")?.as_str().map(String::from));
        if let Some(p) = project.filter(|p| !p.is_empty() && !recent.contains(p)) {
            recent.push(p);
        }
        if recent.len() == 3 {
            break;
        }
    }
    let kinds: Map<String, Value> = KINDS
        .iter()
        .map(|(k, (_, c))| (k.to_string(), json!({"brief": kind_brief(k), "job": kind_job(k), "checkout": c})))
        .collect();
    json!({"presets": builtin, "practices": preset::practices(None), "last": last, "recent": recent,
           "models": models, "practice_defaults": practice_defaults, "kinds": kinds,
           "permissions": policy().table()})
}

/// Kind -> what it does, in words.
pub const KIND_JOBS: &[(&str, &str)] = &[
    ("planner", "reads the project, splits the task and hands out pieces; never edits"),
    ("builder", "makes one change at a time with tests, on its own branch"),
    ("reviewer", "reads every diff and gives a verdict; never edits"),
    ("tester", "writes tests and runs the full suite, on its own branch"),
];

fn kind_job(kind: &str) -> &'static str {
    KIND_JOBS.iter().find(|(k, _)| *k == kind).map_or("", |(_, j)| *j)
}

fn codex_cache() -> PathBuf {
    let home = std::env::var_os("CODEX_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".codex")
    });
    home.join("models_cache.json")
}

/// The models and thinking efforts New team offers. Codex's come from the list Codex keeps on this Mac (an
/// internal file: if it's missing or changes shape, Codex agents get their default only).
pub fn model_options_at(cache: &Path) -> Value {
    let claude = launch::efforts("claude");
    let model_re = Regex::new(launch::MODEL).expect("a fixed pattern");
    let mut codex: Vec<Value> = vec![];
    let read = std::fs::read(cache).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok());
    for m in read.as_ref().and_then(|r| r["models"].as_array()).into_iter().flatten() {
        let slug = m["slug"].as_str().unwrap_or("");
        let levels: Vec<&str> = m["supported_reasoning_levels"].as_array().into_iter().flatten().filter_map(|x| x["effort"].as_str()).collect();
        if model_re.is_match(slug) {
            let efforts: Vec<&str> = launch::efforts("codex").iter().copied().filter(|e| levels.contains(e)).collect();
            codex.push(json!({"id": slug, "label": slug, "about": m["description"].as_str().unwrap_or(""), "efforts": efforts}));
        }
    }
    let common: Vec<&str> = launch::efforts("codex")
        .iter()
        .copied()
        .filter(|e| !codex.is_empty() && codex.iter().all(|m| m["efforts"].as_array().is_some_and(|a| a.iter().any(|x| x == e))))
        .collect();
    let claude_models: Vec<Value> = CLAUDE_MODELS.iter().map(|(i, n)| json!({"id": i, "label": n, "efforts": claude})).collect();
    let mut out = json!({"claude": {"models": claude_models, "efforts": claude}, "codex": {"models": codex, "efforts": common}});
    for k in ["gemini", "opencode", "cursor", "antigravity"] {
        out[k] = json!({"models": [], "efforts": []}); // their own default only
    }
    out
}

pub fn model_options() -> Value {
    model_options_at(&codex_cache())
}

/// What a folder check asks of the machine's setup (session F's module).
pub trait Folders: Send + Sync {
    fn git_ready(&self) -> bool;
    fn too_broad(&self, project: &str) -> bool;
    fn folder_preview(&self, project: &str) -> Result<Value, crate::setup::SetupError>;
    fn uncommitted(&self, project: &str) -> Result<usize, crate::setup::SetupError>;
}

struct Machine;

impl Folders for Machine {
    fn git_ready(&self) -> bool {
        crate::setup::git_ready()
    }
    fn too_broad(&self, project: &str) -> bool {
        crate::setup::too_broad(project)
    }
    fn folder_preview(&self, project: &str) -> Result<Value, crate::setup::SetupError> {
        crate::setup::folder_preview(project)
    }
    fn uncommitted(&self, project: &str) -> Result<usize, crate::setup::SetupError> {
        crate::setup::uncommitted(project)
    }
}

fn expanduser(path: &str) -> String {
    let home = || std::env::var("HOME").unwrap_or_default();
    match path {
        "~" => home(),
        p if p.starts_with("~/") => format!("{}{}", home(), &p[1..]),
        p => p.to_string(),
    }
}

pub fn project_check_with(f: &dyn Folders, path: &str) -> Value {
    use crate::setup::SetupError;
    if !f.git_ready() {
        // a remote machine: nothing to click here
        if !cfg!(target_os = "macos") || std::env::var_os("ASSIEME_ASM_SOCKET").is_some_and(|v| !v.is_empty()) {
            return json!({"ok": false, "reason": NO_GIT});
        }
        return json!({"ok": false, "reason": NO_TOOLS, "tools": true});
    }
    let path = expanduser(path);
    if !Path::new(&path).is_dir() {
        return json!({"ok": false, "reason": "This folder doesn't exist."});
    }
    let too_large = json!({"ok": false, "reason": TOO_LARGE, "too_large": true});
    if f.too_broad(&path) {
        return too_large; // before anything walks the folder
    }
    if worktree::check_repo(&path).is_err() {
        // no repository, or no commits
        if let Err(SetupError::TooLarge(_)) = f.folder_preview(&path) {
            return too_large; // counts the files a first version would hold
        }
        return json!({"ok": false, "reason": NEEDS_SAVE, "setup": true});
    }
    match f.uncommitted(&path) {
        Err(SetupError::TooLarge(_)) => too_large,
        // shown as the folder's problem; nothing is offered to save
        Err(SetupError::Unfinished(u)) => json!({"ok": false, "reason": u.0}),
        Err(SetupError::Failed(_)) | Ok(0) => json!({"ok": true}),
        Ok(n) => json!({"ok": true, "uncommitted": n}),
    }
}

pub fn project_check(path: &str) -> Value {
    project_check_with(&Machine, path)
}

fn str_of<'a>(v: &'a Value, key: &str) -> Option<&'a str> {
    v.get(key).and_then(Value::as_str)
}

fn is_bool_or_null(v: Option<&Value>) -> bool {
    matches!(v, None | Some(Value::Null) | Some(Value::Bool(_)))
}

fn strings_of(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(String::from)).collect()
}

pub fn validate(roles: &[Value]) -> Result<(), NewTeamError> {
    if roles.is_empty() {
        return Err(NewTeamError::new("roles", "Add at least one agent."));
    }
    let names: Vec<&str> = roles.iter().map(|r| str_of(r, "name").unwrap_or("")).collect();
    for (i, r) in roles.iter().enumerate() {
        let field = format!("role:{i}");
        let bad = |m: String| Err(NewTeamError::new(&field, m));
        if !NAME.is_match(names[i]) || names[..i].contains(&names[i]) {
            return bad(NAME_HELP.into());
        }
        let agent = str_of(r, "agent").unwrap_or("");
        if !preset::AGENTS.contains(&agent) {
            return bad("Choose which agent runs this.".into());
        }
        if ["container", "share_signin"].iter().any(|k| !is_bool_or_null(r.get(*k))) {
            return bad("Choose where this agent works.".into());
        }
        let checkout = r.get("checkout").and_then(Value::as_str).unwrap_or("branch");
        if !["branch", "read-only"].contains(&checkout) {
            return bad(format!("Unknown checkout '{checkout}'."));
        }
        if let Some(w) = str_of(r, "worktree").filter(|w| !w.is_empty()) {
            if !w.starts_with('/') {
                return bad("Its folder needs a full path.".into());
            }
        }
        if let Some(b) = str_of(r, "branch").filter(|b| !b.is_empty()) {
            if !BRANCH.is_match(b) || b.starts_with('-') || b.contains("..") {
                return bad(format!("'{b}' isn't a branch name Assieme can use."));
            }
        }
        if let Some(s) = str_of(r, "resume").filter(|s| !s.is_empty()) {
            if !RESUME.is_match(s) {
                return bad("That conversation can't be picked up.".into()); // it goes on the agent's command line
            }
        }
        for key in ["tools_off", "tools_known"] {
            if let Some(v) = r.get(key).filter(|v| !v.is_null()) {
                let ok = v.as_array().is_some_and(|a| a.iter().all(|x| x.as_str().is_some_and(|s| TOOL_ID_RE.is_match(s))));
                if !ok {
                    return bad("That list of skills and servers can't be used.".into());
                }
            }
        }
        if let Some(m) = r.get("musician").filter(|m| !m.is_null()) {
            if !m.as_str().is_some_and(|m| musicians::INSTRUMENTS.contains(&m)) {
                return bad("That instrument isn't one Assieme draws.".into());
            }
        }
        if !is_bool_or_null(r.get("tools_new")) {
            return bad("That list of skills and servers can't be used.".into());
        }
        if let Some(p) = str_of(r, "parent").filter(|p| !p.is_empty()) {
            if !names.contains(&p) {
                return bad(format!("Reports to '{p}', which isn't in this team."));
            }
        }
        if str_of(r, "brief").is_none_or(|b| b.trim().is_empty()) {
            return bad("Every agent needs a brief.".into());
        }
        let mut problem = launch::check_model_effort(agent, str_of(r, "model"), str_of(r, "effort"));
        if let Some(k) = r.get("kind").and_then(Value::as_str) {
            if kind_checkout(k).is_none() {
                problem = problem.or_else(|| Some(format!("Unknown role '{k}'.")));
            }
        }
        if let Some(l) = r.get("level").and_then(Value::as_str) {
            if !policy().levels().iter().any(|x| x == l) {
                problem = problem.or_else(|| Some(format!("Unknown permission level '{l}'.")));
            }
        }
        let owns = strings_of(r.get("owns"));
        let rules: Vec<Value> = r.get("rules").and_then(Value::as_array).cloned().unwrap_or_default();
        let problem = problem.or_else(|| policy().check_owns(&owns)).or_else(|| policy().check_rules(&rules));
        if let Some(p) = problem {
            return bad(p);
        }
    }
    Ok(())
}

pub fn team_name(project: &str, taken: &BTreeSet<String>) -> String {
    let base = Path::new(project.trim_end_matches('/')).file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    let mut slug = String::new();
    for c in base.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            slug.push(c);
        } else if !slug.ends_with('-') {
            slug.push('-');
        }
    }
    let slug = slug.trim_matches('-');
    let base = if slug.is_empty() { "team" } else { slug };
    let (mut name, mut n) = (base.to_string(), 2);
    while taken.contains(&name) {
        name = format!("{base}-{n}");
        n += 1;
    }
    name
}

/// A TOML string: written as a JSON string, which TOML accepts. Non-ASCII stays as it is, as escaped pairs would be
/// refused.
fn toml_str(v: &str) -> String {
    json!(v).to_string()
}

fn role_toml(r: &Value) -> Vec<String> {
    let s = |k: &str| str_of(r, k).unwrap_or("");
    let mut out = vec![
        "[[role]]".to_string(),
        format!("name = {}", toml_str(s("name"))),
        format!("agent = {}", toml_str(s("agent"))),
        format!("job = {}", toml_str(s("job"))),
        format!("checkout = {}", toml_str(r.get("checkout").and_then(Value::as_str).unwrap_or("branch"))),
    ];
    for key in ["parent", "model", "effort", "group", "kind", "level", "worktree", "branch", "resume", "musician"] {
        if !s(key).is_empty() {
            out.push(format!("{key} = {}", toml_str(s(key))));
        }
    }
    for key in ["container", "share_signin"] {
        if r.get(key) == Some(&Value::Bool(true)) {
            out.push(format!("{key} = true"));
        }
    }
    if r.get("tools_new") == Some(&Value::Bool(false)) {
        out.push("tools_new = false".into());
    }
    for key in ["tools_off", "tools_known", "owns"] {
        if r.get(key).and_then(Value::as_array).is_some_and(|a| !a.is_empty()) {
            out.push(format!("{key} = {}", r[key])); // a JSON list of strings is a TOML array
        }
    }
    if let Some(rules) = r.get("rules").and_then(Value::as_array).filter(|a| !a.is_empty()) {
        let tables: Vec<String> = rules
            .iter()
            .map(|x| {
                format!(
                    "{{decision = {}, program = {}, words = {}}}",
                    toml_str(x["decision"].as_str().unwrap_or("")),
                    x["program"],
                    toml_str(x["words"].as_str().unwrap_or(""))
                )
            })
            .collect();
        out.push(format!("rules = [{}]", tables.join(", ")));
    }
    out.push(String::new());
    out
}

/// Step 32: an agent that doesn't take newly installed tools remembers what was there, so a later one is told apart.
fn known(r: &Value, project: &str) -> Value {
    if r.get("tools_new") != Some(&Value::Bool(false)) || r.get("tools_known").and_then(Value::as_array).is_some_and(|a| !a.is_empty()) {
        return r.clone();
    }
    let ids: Vec<Value> = crate::agenttools::find(str_of(r, "agent").unwrap_or(""), Some(project), false)["items"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|i| i["id"].clone())
        .collect();
    let mut r = r.clone();
    r["tools_known"] = json!(ids);
    r
}

fn io_err(e: std::io::Error) -> BrainError {
    BrainError::new(e.to_string())
}

fn kinds_and_saved(roles: &[Value]) -> Vec<String> {
    let items: Vec<(Option<&str>, Option<&str>)> = roles.iter().map(|r| (str_of(r, "kind"), str_of(r, "musician"))).collect();
    musicians::pick(&items)
}

/// Write the team folder `preset::load` reads; TOML strings are written as JSON strings, which TOML accepts.
/// `scripts`: setup, run and archive lines brought over from another tool (step 30).
pub fn save(
    project: &str,
    roles: &[Value],
    practices: &Map<String, Value>,
    name: &str,
    teams: &Path,
    scripts: Option<&BTreeMap<String, String>>,
) -> Result<PathBuf, BrainError> {
    let scripts: BTreeMap<&String, &String> = scripts.into_iter().flatten().filter(|(_, v)| !v.is_empty()).collect();
    let unknown: Vec<&str> = scripts.keys().filter(|k| !preset::SCRIPTS.contains(&k.as_str())).map(|k| k.as_str()).collect();
    if !unknown.is_empty() {
        return Err(BrainError::new(format!("Unknown script {}.", unknown.join(", "))));
    }
    let picked = kinds_and_saved(roles); // step 31: saved once
    let roles: Vec<Value> = roles
        .iter()
        .zip(picked)
        .map(|(r, m)| {
            let mut r = known(r, project);
            r["musician"] = json!(m);
            r
        })
        .collect();
    let folder = teams.join(name);
    if folder.is_dir() {
        std::fs::remove_dir_all(&folder).map_err(io_err)?;
    }
    std::fs::create_dir_all(&folder).map_err(io_err)?;
    let absolute = preset::absolute(Path::new(project));
    let mut out = vec![format!("name = {}", toml_str(name)), format!("project = {}", toml_str(&absolute.to_string_lossy())), String::new()];
    if !practices.is_empty() {
        out.push("[practices]".into());
        out.extend(practices.iter().map(|(k, v)| format!("{k} = {}", if truthy(v) { "true" } else { "false" })));
        out.push(String::new());
    }
    if !scripts.is_empty() {
        out.push("[scripts]".into());
        out.extend(scripts.iter().map(|(k, v)| format!("{k} = {}", toml_str(v))));
        out.push(String::new());
    }
    for r in &roles {
        out.extend(role_toml(r));
        let brief = str_of(r, "brief").unwrap_or("");
        std::fs::write(folder.join(format!("{}.md", str_of(r, "name").unwrap_or(""))), format!("{}\n", brief.trim_end_matches('\n')))
            .map_err(io_err)?;
    }
    std::fs::write(folder.join("team.toml"), out.join("\n")).map_err(io_err)?;
    Ok(folder)
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The roles of a saved team folder as the form holds them, in the file's order.
fn saved_roles(folder: &Path) -> Result<(Team, Vec<String>), BrainError> {
    let tm = preset::load(folder, false)?;
    let order = read_toml(&folder.join("team.toml"))
        .and_then(|d| d.get("role").cloned())
        .and_then(|r| r.as_array().cloned())
        .map(|a| a.iter().filter_map(|r| r.get("name")?.as_str().map(String::from)).collect())
        .unwrap_or_default();
    Ok((tm, order))
}

fn resave(tm: &Team, roles: &[Value], folder: &Path) -> Result<(), BrainError> {
    let teams = folder.parent().unwrap_or(Path::new("."));
    save(&tm.project, roles, &tm.practices, &tm.name, teams, Some(&tm.scripts)).map(|_| ())
}

/// Take a role back out of a saved team, so it doesn't return at the next start and its name is free again.
pub fn forget_role(folder: &Path, name: &str) -> Result<(), BrainError> {
    let (tm, order) = saved_roles(folder)?;
    let gone = tm.roles.iter().find(|r| r.name == name);
    let mut roles: Vec<Value> = order
        .iter()
        .filter(|n| *n != name)
        .filter_map(|n| tm.roles.iter().find(|r| r.name == *n))
        .map(role_dict)
        .collect();
    for r in &mut roles {
        // whoever reported to it now reports one level up, or the saved team would no longer load
        if r["parent"] == name {
            r["parent"] = gone.map_or(Value::Null, |g| opt(&g.parent));
        }
    }
    resave(&tm, &roles, folder)
}

/// Save the team again with one role's fields changed, in the file's order and with its scripts. `changes` holds
/// the fields by their Python names.
pub fn rewrite_role(folder: &Path, name: &str, changes: &Map<String, Value>) -> Result<(), BrainError> {
    let (tm, order) = saved_roles(folder)?;
    let mut roles: Vec<Value> = order.iter().filter_map(|n| tm.roles.iter().find(|r| r.name == *n)).map(role_dict).collect();
    if let Some(r) = roles.iter_mut().find(|r| r["name"] == name) {
        for (k, v) in changes {
            r[k] = v.clone();
        }
    }
    resave(&tm, &roles, folder)
}

/// What the start record says about brought-over agents whose folder has gone: they get a new copy.
fn gone_notes(roles: &[Role]) -> Vec<String> {
    roles
        .iter()
        .filter_map(|r| {
            let (w, b) = (r.worktree.as_deref().filter(|w| !w.is_empty())?, r.branch.as_deref().filter(|b| !b.is_empty())?);
            (!Path::new(w).is_dir()).then(|| {
                let base = Path::new(w.trim_end_matches('/')).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                format!("{base}'s folder isn't there any more. Its branch {b} is kept: start gives it a new copy.")
            })
        })
        .collect()
}

/// After a brought-over agent's first start: later starts don't resume that conversation again.
pub fn clear_resume(folder: &Path, name: &str) -> Result<(), BrainError> {
    rewrite_role(folder, name, &json!({"resume": null}).as_object().cloned().unwrap_or_default())
}

/// After merging a brought-over agent: its next start makes an Assieme copy; the old folder stays where it is.
pub fn clear_worktree(folder: &Path, name: &str) -> Result<(), BrainError> {
    rewrite_role(folder, name, &json!({"worktree": null, "branch": null}).as_object().cloned().unwrap_or_default())
}

/// {role: {job, group, parent}}; {} for a team started from the command line.
pub fn roster(name: &str, teams: Option<&Path>) -> BTreeMap<String, Value> {
    let folder = teams.map_or_else(teams_dir, Path::to_path_buf).join(name);
    // with the roles bigger-team presets add
    let Ok(tm) = preset::load(&folder, true) else { return BTreeMap::new() };
    tm.roles.iter().map(|r| (r.name.clone(), json!({"job": r.job, "group": opt(&r.group), "parent": opt(&r.parent)}))).collect()
}

/// Step 31: each role's instrument and colours, for the app to draw. {} for a team with no saved folder.
pub fn band(name: &str, teams: Option<&Path>) -> Value {
    let folder = teams.map_or_else(teams_dir, Path::to_path_buf).join(name);
    let Ok(tm) = preset::load(&folder, true) else { return json!({}) };
    let kinds: Vec<Option<&str>> = tm.roles.iter().map(|r| r.kind.as_deref()).collect();
    let (assigned, rules) = (musicians::assign(&tm.roles), musicians::rules(&kinds));
    let mut out = Map::new();
    for (i, r) in tm.roles.iter().enumerate() {
        let (m, (rule, why)) = (&assigned[i], &rules[i]);
        // chosen_for: why the rule picked it, only while the person hasn't picked another
        out.insert(r.name.clone(), json!({"musician": m, "palette": musicians::palette(&kinds, i),
            "chosen_for": (m == rule).then_some(why)}));
    }
    Value::Object(out)
}

/// Step 31: the instrument picked in Agent details, saved with the role. Only how it looks changes.
pub fn set_musician(name: &str, role: &str, musician: &str, teams: Option<&Path>) -> Result<Value, NewTeamError> {
    if !musicians::INSTRUMENTS.contains(&musician) {
        return Err(NewTeamError::new("musician", "That instrument isn't one Assieme draws."));
    }
    let folder = teams.map_or_else(teams_dir, Path::to_path_buf).join(name);
    let Ok(tm) = preset::load(&folder, false) else {
        return Err(NewTeamError::new("musician", "This team was started from the command line, so it has no saved instruments."));
    };
    if !tm.roles.iter().any(|r| r.name == role) {
        return Err(NewTeamError::new("musician", format!("{role} is not in this team.")));
    }
    rewrite_role(&folder, role, &json!({"musician": musician}).as_object().cloned().unwrap_or_default())?;
    Ok(json!({"ok": true}))
}

/// One start's progress, as the snapshot shows it.
#[derive(Debug, Clone)]
struct Rec {
    team: String,
    project: String,
    done: bool,
    error: Option<String>,
    done_at: Option<f64>,
    roles: Vec<(String, String, String)>, // role, state, detail
    notes: Vec<String>,
    ask_scripts: Vec<Value>,
}

impl Rec {
    fn failed(&self) -> bool {
        self.done && (self.error.is_some() || self.roles.iter().any(|(_, s, _)| FAILED.contains(&s.as_str())))
    }

    fn value(&self) -> Value {
        let roles: Vec<Value> = self.roles.iter().map(|(r, s, d)| json!({"role": r, "state": s, "detail": d})).collect();
        let mut v = json!({"team": self.team, "project": self.project, "done": self.done, "error": self.error, "roles": roles});
        if !self.notes.is_empty() {
            v["notes"] = json!(self.notes);
        }
        if !self.ask_scripts.is_empty() {
            v["ask_scripts"] = json!(self.ask_scripts);
        }
        v
    }
}

type Shared = Arc<Mutex<Rec>>;

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

#[derive(Default)]
struct State {
    recs: Vec<Shared>,
    /// project -> name, while checks run
    busy: BTreeMap<String, String>,
}

struct Inner {
    rt: Runtime,
    teams: PathBuf,
    /// The teams of scheduled runs, which may share a project with another team.
    scheduled: Box<dyn Fn() -> BTreeSet<String> + Send + Sync>,
    folders: Box<dyn Folders>,
    state: Mutex<State>,
    now: fn() -> f64,
}

fn clock() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// Starts teams in the background and remembers the ones starting.
///
/// Checks and saves at once, then runs `team::start` in a thread, keeping a progress record per team for the
/// snapshot. Records live in memory; the teams themselves outlive the brain.
pub struct Starter {
    inner: Arc<Inner>,
}

/// What a prepared start hands to its thread.
#[derive(Debug)]
struct Prepared {
    rec: Shared,
    tm: Team,
    fresh: BTreeMap<String, String>,
}

impl Starter {
    pub fn new(rt: Runtime, teams: PathBuf, scheduled: Box<dyn Fn() -> BTreeSet<String> + Send + Sync>) -> Starter {
        let inner = Inner { rt, teams, scheduled, folders: Box::new(Machine), state: Mutex::new(State::default()), now: clock };
        Starter { inner: Arc::new(inner) }
    }

    fn world(&self) -> team::Live {
        team::Live(self.inner.rt.clone())
    }

    fn live_names(&self, w: &dyn World) -> BTreeSet<String> {
        // the assieme core isn't running yet: no live teams
        team::status_with(w, None)
            .map(|rows| rows.iter().filter(|r| r["state"] == "live").filter_map(|r| r["team"].as_str().map(String::from)).collect())
            .unwrap_or_default()
    }

    fn project_of(&self, name: &str) -> Option<String> {
        // None: started from the command line, or the folder is gone
        read_toml(&self.inner.teams.join(name).join("team.toml"))?.get("project")?.as_str().map(String::from)
    }

    /// `allow_scripts`: the brought-over scripts the person said yes to in New team, for this team's name.
    /// `name` is a scheduled run's own team name; New team leaves it to the project's folder name.
    #[allow(clippy::too_many_arguments)]
    pub fn start(
        &self,
        project: &str,
        task: &str,
        roles: &[Value],
        practices: &Value,
        name: Option<&str>,
        scripts: Option<&BTreeMap<String, String>>,
        allow_scripts: &[String],
    ) -> Result<Value, BrainError> {
        let w = self.world();
        let p = self.prepare(&w, project, roles, practices, name, scripts, allow_scripts)?;
        let (inner, team_name) = (self.inner.clone(), p.tm.name.clone());
        let task = task.to_string();
        std::thread::spawn(move || {
            let w = team::Live(inner.rt.clone());
            run_with(&inner, &w, &p.rec, &p.tm, &task, &p.fresh);
        });
        Ok(json!({"team": team_name}))
    }

    #[allow(clippy::too_many_arguments)]
    fn prepare(
        &self,
        w: &dyn World,
        project: &str,
        roles: &[Value],
        practices: &Value,
        name: Option<&str>,
        scripts_: Option<&BTreeMap<String, String>>,
        allow_scripts: &[String],
    ) -> Result<Prepared, NewTeamError> {
        let inner = &*self.inner;
        validate(roles)?;
        let project = expanduser(project);
        let check = project_check_with(&*inner.folders, &project);
        if check["ok"] != true {
            return Err(NewTeamError::new("project", check["reason"].as_str().unwrap_or("")));
        }
        let project = preset::absolute(Path::new(&project)).to_string_lossy().into_owned();
        let live = self.live_names(w);
        let name = {
            // held only for bookkeeping: git work below must not block records()
            let mut st = lock(&inner.state);
            let open = st.recs.iter().map(|r| lock(r).clone()).filter(|r| !r.done).collect::<Vec<_>>();
            if st.busy.contains_key(&project) || open.iter().any(|r| r.project == project) {
                return Err(NewTeamError::new("start", "This team is still starting."));
            }
            let sched = (inner.scheduled)();
            let mine: BTreeSet<String> = roles.iter().map(|r| str_of(r, "name").unwrap_or("").to_string()).collect();
            let blocks = |other: &str| {
                // worktrees are per role name: these would share one
                if roster(other, Some(&inner.teams)).keys().any(|k| mine.contains(k)) {
                    return true;
                }
                !sched.contains(other) && !name.is_some_and(|n| sched.contains(n)) // New team beside New team: one per project
            };
            let running: Vec<&String> = live.iter().filter(|n| self.project_of(n).as_deref() == Some(&project) && blocks(n)).collect();
            if let Some(first) = running.first() {
                return Err(NewTeamError::new("project", format!("This project already has a running team: {first}. Stop it first.")));
            }
            let mut taken = live.clone();
            taken.extend(st.busy.values().cloned());
            taken.extend(open.iter().map(|r| r.team.clone()));
            if name.is_some_and(|n| taken.contains(n)) {
                return Err(NewTeamError::new("start", format!("{} is still running.", name.unwrap_or(""))));
            }
            let chosen = name.map_or_else(|| team_name(&project, &taken), String::from);
            st.busy.insert(project.clone(), chosen.clone());
            chosen
        };
        let built = (|| -> Result<(Team, BTreeMap<String, String>), NewTeamError> {
            let practices = practices.as_object().cloned().unwrap_or_default();
            let folder = save(&project, roles, &practices, &name, &inner.teams, scripts_)?;
            let tm = preset::load(&folder, true).map_err(|e| NewTeamError::new("start", e.0))?;
            for which in allow_scripts {
                if let Some(line) = tm.scripts.get(which) {
                    scripts::allow(&name, which, line)?;
                }
            }
            let missing = w.missing_clis(&tm.roles);
            if !missing.is_empty() {
                return Err(NewTeamError::new("start", format!("not installed: {}. Install it, then start again.", missing.join(", "))));
            }
            container_ready(w, &tm.roles)?;
            // Copies made by this start get the team's setup script; brought-over folders are already set up.
            let fresh = tm
                .roles
                .iter()
                .filter(|r| r.checkout != "read-only" && !r.worktree.as_deref().is_some_and(|d| Path::new(d).is_dir()))
                .filter(|r| !Path::new(&worktree::folder(&tm, r)).is_dir())
                .map(|r| (r.name.clone(), worktree::folder(&tm, r)))
                .collect();
            for r in &tm.roles {
                // every worktree now, so a dirty one is reported to the form, not the log
                worktree::ensure(&tm, r)?;
            }
            Ok((tm, fresh))
        })();
        let mut st = lock(&inner.state);
        st.busy.remove(&project);
        let (tm, fresh) = built?;
        let rec = Rec {
            team: name,
            project,
            done: false,
            error: None,
            done_at: None,
            roles: tm.roles.iter().map(|r| (r.name.clone(), "starting".to_string(), String::new())).collect(), // with preset-added roles
            notes: gone_notes(&tm.roles),
            ask_scripts: vec![],
        };
        let rec = Arc::new(Mutex::new(rec));
        st.recs.push(rec.clone());
        Ok(Prepared { rec, tm, fresh })
    }

    /// One more agent for a running team: added to its saved folder, then started like any other.
    pub fn add_agent(&self, team_name: &str, role: &Value) -> Result<Value, NewTeamError> {
        let w = self.world();
        let (rec, tm, name, folder) = self.prepare_added(&w, team_name, role)?;
        let inner = self.inner.clone();
        std::thread::spawn(move || run_added(&inner, &team::Live(inner.rt.clone()), &rec, &tm, &name, &folder));
        Ok(json!({"team": team_name}))
    }

    fn prepare_added(&self, w: &dyn World, team_name: &str, role: &Value) -> Result<(Shared, Team, String, PathBuf), NewTeamError> {
        let folder = self.inner.teams.join(team_name);
        if !folder.join("team.toml").is_file() {
            return Err(NewTeamError::new("start", "This team was started from the command line, so agents can't be added here."));
        }
        // no core running means nothing of this team is
        let rows = w.list(&BTreeMap::from([(team::TEAM.to_string(), team_name.to_string())]), false).unwrap_or_default();
        if !rows.iter().any(|r| r["state"] == "live" && r["labels"][team::TEAM] == team_name) {
            return Err(NewTeamError::new("start", "This team has stopped. Start it again to add an agent."));
        }
        let mut role = role.clone();
        let tm = preset::load(&folder, false)?;
        let mut all: Vec<Value> = tm.roles.iter().map(role_dict).collect();
        all.push(role.clone());
        role["musician"] = json!(kinds_and_saved(&all).pop().unwrap_or_default()); // the next by the rule; nobody else's changes
        let kind = str_of(&role, "kind").map(String::from); // kept in team.toml: later instruments and colours count by it
        if let Some(checkout) = kind.as_deref().and_then(kind_checkout) {
            if role.get("checkout").is_none() {
                role["checkout"] = json!(checkout);
            }
            if str_of(&role, "brief").is_none_or(|b| b.trim().is_empty()) {
                role["brief"] = json!(kind_brief(kind.as_deref().unwrap_or("")));
            }
        }
        let mut with_new: Vec<Value> = tm.roles.iter().map(role_dict).collect();
        with_new.push(role.clone());
        validate(&with_new)?;
        let new = Role { container: role.get("container") == Some(&Value::Bool(true)), ..Role::new(str_of(&role, "name").unwrap_or(""), str_of(&role, "agent").unwrap_or("")) };
        let missing = w.missing_clis(std::slice::from_ref(&new));
        if !missing.is_empty() {
            return Err(NewTeamError::new("start", format!("not installed: {}. Install it, then try again.", missing.join(", "))));
        }
        container_ready(w, std::slice::from_ref(&new))?;
        let name = new.name.clone();
        let io = |e: std::io::Error| NewTeamError::new("start", e.to_string());
        std::fs::write(folder.join(format!("{name}.md")), format!("{}\n", str_of(&role, "brief").unwrap_or("").trim_end_matches('\n'))).map_err(io)?;
        {
            use std::io::Write;
            let mut f = std::fs::OpenOptions::new().append(true).open(folder.join("team.toml")).map_err(io)?;
            writeln!(f, "{}", role_toml(&role).join("\n")).map_err(io)?;
        }
        let tm = preset::load(&folder, true)?;
        let mine = tm.roles.iter().find(|r| r.name == name).cloned().unwrap_or(new);
        if let Err(e) = worktree::ensure(&tm, &mine) {
            let _ = forget_role(&folder, &name);
            return Err(e.into());
        }
        let rec = Rec {
            team: team_name.into(),
            project: tm.project.clone(),
            done: false,
            error: None,
            done_at: None,
            roles: vec![(name.clone(), "starting".into(), String::new())],
            notes: vec![],
            ask_scripts: vec![],
        };
        let rec = Arc::new(Mutex::new(rec));
        lock(&self.inner.state).recs.push(rec.clone());
        Ok((rec, tm, name, folder))
    }

    /// The person retried or removed an agent whose start failed: its "Couldn't start" row goes.
    pub fn forget(&self, team_name: &str, role: &str) {
        let mut st = lock(&self.inner.state);
        for r in &st.recs {
            let mut r = lock(r);
            if r.team == team_name {
                r.roles.retain(|(n, _, _)| n != role);
            }
        }
        st.recs.retain(|r| !lock(r).roles.is_empty());
    }

    pub fn records(&self) -> Vec<Value> {
        let mut st = lock(&self.inner.state);
        let now = (self.inner.now)();
        // A start that failed stays until the person retries or removes its agents, not ten minutes.
        st.recs.retain(|r| {
            let r = lock(r);
            !r.done || now - r.done_at.unwrap_or(now) <= KEEP_DONE_S || r.failed()
        });
        st.recs.iter().map(|r| lock(r).value()).collect()
    }
}

fn panic_message(p: &(dyn std::any::Any + Send)) -> String {
    p.downcast_ref::<&str>().map(|s| s.to_string()).or_else(|| p.downcast_ref::<String>().cloned()).unwrap_or_else(|| "something went wrong".into())
}

fn container_ready(w: &dyn World, roles: &[Role]) -> Result<(), NewTeamError> {
    for r in roles {
        if let Some(p) = r.container.then(|| w.container_problem(&r.agent)).flatten().filter(|p| !p.is_empty()) {
            return Err(NewTeamError::new("start", format!("{} runs in a container. {p}", r.name)));
        }
    }
    Ok(())
}

fn finish(inner: &Inner, rec: &Shared) {
    let mut r = lock(rec);
    r.done = true;
    r.done_at = Some((inner.now)());
}

/// The team's setup script in each copy this start made, when the person has said yes to it; otherwise the record
/// asks for that yes, and the agents start without it.
fn setup_scripts(rec: &Shared, tm: &Team, fresh: &BTreeMap<String, String>) {
    let Some(line) = tm.scripts.get("setup").filter(|_| !fresh.is_empty()) else { return };
    if !scripts::allowed(&tm.name, "setup", line) {
        lock(rec).ask_scripts = vec![json!({"which": "setup", "line": line})];
        return;
    }
    for (name, path) in fresh {
        let got = scripts::run(line, path, &tm.project, None, 300.0);
        let ok = got["ok"] == true;
        crate::live::record(crate::live::Record {
            action: "setup",
            agent: name,
            reason: if ok { "Ran the team setup in its copy" } else { "The team setup didn't finish" },
            failed: !ok,
            detail: got["output"].as_str().unwrap_or(""),
            team: Some(&tm.name),
            by: "assieme",
            ..Default::default()
        });
    }
}

fn run_with(inner: &Inner, w: &dyn World, rec: &Shared, tm: &Team, task: &str, fresh: &BTreeMap<String, String>) {
    let work = std::panic::AssertUnwindSafe(|| -> Result<(), BrainError> {
        setup_scripts(rec, tm, fresh);
        // Clicking Start in the app is the person's agreement to trust the folder.
        let results = team::start_with(w, tm, Some(task), &|_| true, None)?;
        for r in tm.roles.iter().filter(|r| r.resume.is_some()) {
            // a conversation brought over is picked up once; later starts begin fresh
            if results.iter().any(|s| s.role == r.name && s.address.is_some()) {
                clear_resume(&inner.teams.join(&tm.name), &r.name)?;
            }
        }
        let mut r = lock(rec);
        for row in r.roles.iter_mut() {
            if let Some(s) = results.iter().find(|s| s.role == row.0) {
                (row.1, row.2) = (s.state.clone(), s.note.clone());
            }
        }
        Ok(())
    });
    // anything here would otherwise vanish with the thread
    match std::panic::catch_unwind(work) {
        Ok(Ok(())) => {}
        Ok(Err(e)) => lock(rec).error = Some(e.message),
        Err(p) => lock(rec).error = Some(panic_message(&*p)),
    }
    finish(inner, rec);
}

fn run_added(inner: &Inner, w: &dyn World, rec: &Shared, tm: &Team, name: &str, folder: &Path) {
    let work = std::panic::AssertUnwindSafe(|| -> Result<(), BrainError> {
        let got = team::start_with(w, tm, None, &|_| true, Some(name))?;
        let s = got.into_iter().next().ok_or_else(|| BrainError::new("no agent was started"))?;
        {
            let mut r = lock(rec);
            (r.roles[0].1, r.roles[0].2) = (s.state.clone(), s.note.clone());
        }
        if s.state == "exited" {
            forget_role(folder, name)?;
        } else {
            crate::live::record(crate::live::Record {
                action: "add_agent",
                agent: name,
                reason: "You added it to this team.",
                team: Some(&tm.name),
                address: s.address.as_deref(),
                ..Default::default()
            });
        }
        Ok(())
    });
    let failed = match std::panic::catch_unwind(work) {
        Ok(Ok(())) => None,
        Ok(Err(e)) => Some(e.message),
        Err(p) => Some(panic_message(&*p)),
    };
    if let Some(msg) = failed {
        lock(rec).error = Some(msg);
        let _ = forget_role(folder, name);
    }
    finish(inner, rec);
}

const SUGGEST_TIMEOUT_S: f64 = 170.0;
const MAX_ROLES: usize = 8;
const IMPROVE_TIMEOUT_S: f64 = 170.0;
const BRIEF_MAX: usize = 6000;
const RULE_TIMEOUT_S: f64 = 120.0;

fn word(kind: &str, v: &Value) -> String {
    let s = v.as_str().map_or_else(|| v.to_string(), String::from);
    let known = match (kind, s.as_str()) {
        ("size", "small") => "a small fix",
        ("size", "feature") => "a feature",
        ("size", "large") => "a large change",
        ("review", "every") => "every change",
        ("review", "end") => "just the end result",
        ("budget", "lean") => "keep it lean (fewer agents, cheaper)",
        ("budget", "fast") => "speed matters more than cost",
        _ => return s,
    };
    known.to_string()
}

fn models_text(agents: &[String], models: &Value) -> String {
    let mut out = vec![];
    for a in agents {
        let ms: Vec<&Value> = models[a]["models"].as_array().into_iter().flatten().collect();
        let list = |v: &Value| v.as_array().into_iter().flatten().filter_map(|x| x.as_str()).collect::<Vec<_>>().join(", ");
        if a == "claude" {
            let ids: Vec<&str> = ms.iter().filter_map(|m| m["id"].as_str()).collect();
            out.push(format!("- claude models, strongest and most expensive first: {}. Efforts: {}", ids.join(", "), list(&models[a]["efforts"])));
        } else if ms.is_empty() {
            out.push(format!("- {a}: its own default only; leave model and effort out"));
        } else {
            for m in ms {
                let about = m["about"].as_str().filter(|s| !s.is_empty()).unwrap_or("no description");
                out.push(format!("- {a} model {} ({about}). Efforts: {}", m["id"].as_str().unwrap_or(""), list(&m["efforts"])));
            }
        }
    }
    out.join("\n")
}

fn suggest_prompt(size: &Value, review: &Value, budget: &Value, agents: &[String], models: &Value, task: &str) -> String {
    let kinds: Vec<String> = KIND_JOBS.iter().map(|(k, v)| format!("- {k}: {v}")).collect();
    let what = if task.is_empty() { format!("The change is {}.", word("size", size)) } else { format!("The task: {task}.") };
    format!(
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
        agents.join(", "),
        models_text(agents, models)
    )
}

/// The suggested model and effort if this machine can run them; anything else is Default, which always runs.
fn model_effort(r: &Value, cli: &Value) -> (Option<String>, Option<String>) {
    let chosen = cli["models"].as_array().into_iter().flatten().find(|m| m["id"] == r["model"]);
    let allowed = chosen.map_or(&cli["efforts"], |c| &c["efforts"]);
    let effort = r["effort"].as_str().filter(|e| allowed.as_array().is_some_and(|a| a.iter().any(|x| x == e)));
    (chosen.and_then(|c| c["id"].as_str().map(String::from)), effort.map(String::from))
}

fn team_from(data: Option<Map<String, Value>>, installed: &[String], models: &Value) -> Result<Value, String> {
    let Some(data) = data.filter(|d| d.get("roles").is_some_and(Value::is_array)) else {
        return Err("the answer wasn't a team.".into());
    };
    let raw = data["roles"].as_array().cloned().unwrap_or_default();
    if raw.is_empty() {
        return Err("no agents in the answer.".into());
    }
    if raw.len() > MAX_ROLES {
        return Err(format!("more than {MAX_ROLES} agents."));
    }
    let planner = raw.iter().find(|r| r["kind"] == "planner").and_then(|r| r["name"].as_str());
    let mut roles = vec![];
    for r in &raw {
        let kind = r["kind"].as_str().unwrap_or("");
        let Some(checkout) = kind_checkout(kind) else {
            return Err(format!("unknown role kind {}.", r["kind"].as_str().map_or_else(|| r["kind"].to_string(), |k| format!("'{k}'"))));
        };
        let agent = r["agent"].as_str().unwrap_or("");
        if !installed.iter().any(|i| i == agent) {
            return Err(format!("it chose {}, which is not installed.", r["agent"].as_str().unwrap_or("None")));
        }
        let (model, effort) = model_effort(r, models.get(agent).unwrap_or(&json!({"models": [], "efforts": []})));
        roles.push(json!({"name": r["name"].as_str().unwrap_or(""), "agent": agent, "job": r["job"].as_str().unwrap_or(""),
            "checkout": checkout, "parent": if kind == "planner" { Value::Null } else { json!(planner) },
            "brief": kind_brief(kind), "model": model, "effort": effort}));
    }
    Ok(json!({"roles": roles, "why": data.get("why").and_then(Value::as_str).unwrap_or("")}))
}

fn ask_error(e: crate::ask::AskError, pre: &str) -> NewTeamError {
    NewTeamError::new("suggest", if e.missing { e.message } else { format!("{pre}{}", e.message) })
}

/// Help me choose: the person's own Claude Code (else Codex), read-only in the project, suggests a team with each
/// agent's model and thinking effort, among what New team offers on this machine. `size`, `review` and `budget`
/// are New team's choices as the app sends them.
pub fn suggest(
    project: &str,
    size: &Value,
    review: &Value,
    budget: &Value,
    task: &str,
    models: Option<&Value>,
) -> Result<Value, NewTeamError> {
    let have = crate::ask::installed();
    let models = models.cloned().unwrap_or_else(model_options);
    let pre = "Couldn't get a suggestion: ";
    let text = crate::ask::ask_agent(&suggest_prompt(size, review, budget, &have, &models, task.trim()), project, SUGGEST_TIMEOUT_S, Some("3 minutes"), false)
        .map_err(|e| ask_error(e, pre))?;
    team_from(crate::ask::first_json(&text), &have, &models).map_err(|e| NewTeamError::new("suggest", format!("{pre}{e}")))
}

fn brief_prompt(role: &Value) -> String {
    let kind = str_of(role, "kind");
    let mut lines = vec![
        kind.filter(|k| kind_checkout(k).is_some())
            .map_or_else(|| "Role: custom.".to_string(), |k| format!("Role: {k} ({}).", kind_job(k))),
        format!("Its job in one line: {}", str_of(role, "job").filter(|j| !j.is_empty()).unwrap_or("(not written yet)")),
    ];
    let owns = strings_of(role.get("owns"));
    if !owns.is_empty() {
        lines.push(format!("Files only it edits: {}", owns.join(", ")));
    }
    if let Some(p) = str_of(role, "parent").filter(|p| !p.is_empty()) {
        lines.push(format!("It reports to the agent named {p}."));
    }
    format!(
        "Improve the brief below for one AI coding agent in a team working on the code in this folder. Read enough of \
         the project to make it concrete: name the real files, commands and checks. Keep it short: one line on what it \
         does, then a few bullet points. Keep every instruction the person wrote unless it is wrong for this project. \
         Leave out reporting format, stop rules and handoff format: they are added automatically.\n\n{}\n\n\
         The current brief, between triple quotes:\n\"\"\"\n{}\n\"\"\"\n\nReply with only one JSON object: {{\"brief\": \"...\"}}",
        lines.join("\n"),
        str_of(role, "brief").unwrap_or("").trim()
    )
}

fn brief_from(data: Option<Map<String, Value>>) -> Option<String> {
    let got = data?.get("brief")?.as_str()?.to_string();
    (!got.trim().is_empty() && got.chars().count() <= BRIEF_MAX).then(|| format!("{}\n", got.trim()))
}

/// Improve this brief: the person's own Claude Code (else Codex), read-only in the project, rewrites it. Nothing is
/// saved: the editor shows the result and the person keeps or undoes it.
pub fn improve_brief(project: &str, role: &Value) -> Result<Value, NewTeamError> {
    let pre = "Couldn't improve the brief: ";
    let text = crate::ask::ask_agent(&brief_prompt(role), project, IMPROVE_TIMEOUT_S, Some("3 minutes"), false).map_err(|e| ask_error(e, pre))?;
    brief_from(crate::ask::first_json(&text))
        .map(|b| json!({"brief": b}))
        .ok_or_else(|| NewTeamError::new("suggest", format!("{pre}the answer wasn't a brief.")))
}

fn rule_prompt(words: &str) -> String {
    format!(
        "A person is setting what one AI coding agent may run in the project in this folder. They wrote a rule in\n\
         plain words, quoted below between triple quotes. Turn it into one rule on the program the agent runs.\n\n\
         \"\"\"\n{words}\n\"\"\"\n\n\
         A rule is the program and, if needed, its first few arguments, matched as a prefix of the command run: [\"npm\",\n\
         \"publish\"] matches \"npm publish --tag next\". If the words name something in this project (a script, a launcher, a\n\
         tool), read the project to find the exact path or command, such as [\"build/cloud/run.sh\"]. Words only: no spaces\n\
         inside a word, no wildcards, no shell symbols.\n\n\
         Reply with only one JSON object: {{\"decision\": \"block\" or \"allow\", \"program\": [\"...\", \"...\"]}}\n\
         If the words don't describe running a program, reply {{\"unclear\": \"one short sentence saying why\"}}"
    )
}

fn rule_from(data: Option<Map<String, Value>>, words: &str) -> Result<Value, NewTeamError> {
    let pre = "Couldn't turn that into a rule: ";
    let data = data.unwrap_or_default();
    if let Some(u) = data.get("unclear").and_then(Value::as_str) {
        return Err(NewTeamError::new("suggest", format!("{pre}{u}")));
    }
    let program = policy().check_rule(data.get("decision"), data.get("program")).map_err(|e| NewTeamError::new("suggest", format!("{pre}{e}")))?;
    Ok(json!({"decision": data["decision"], "program": program, "words": words}))
}

/// Add a rule: plain words to a rule on the program run, for the person to see before Add. Nothing is saved.
pub fn rule_draft(project: &str, words: &str) -> Result<Value, NewTeamError> {
    let words = words.trim();
    if words.is_empty() {
        return Err(NewTeamError::new("suggest", "Write the rule in a few words first."));
    }
    let pre = "Couldn't turn that into a rule: ";
    let clipped: String = words.chars().take(300).collect();
    let text = crate::ask::ask_agent(&rule_prompt(&clipped), project, RULE_TIMEOUT_S, Some("2 minutes"), false).map_err(|e| ask_error(e, pre))?;
    rule_from(crate::ask::first_json(&text), words)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::team::tests::{Fake, repo, with_state};

    fn good(name: &str) -> Value {
        json!({"name": name, "agent": "claude", "brief": "Do it."})
    }

    fn msg(roles: &[Value]) -> (String, String) {
        let e = validate(roles).unwrap_err();
        (e.field, e.message)
    }

    #[test]
    fn the_form_is_checked_field_by_field_in_words() {
        assert_eq!(validate(&[good("a"), good("b")]), Ok(()));
        assert_eq!(msg(&[]), ("roles".into(), "Add at least one agent.".into()));
        let bad = |extra: Value| {
            let mut r = good("a");
            for (k, v) in extra.as_object().unwrap() {
                r[k] = v.clone();
            }
            msg(&[good("zero"), r])
        };
        assert_eq!(msg(&[good("A")]).1, NAME_HELP);
        assert_eq!(msg(&[good("a"), good("a")]), ("role:1".into(), NAME_HELP.into()));
        assert_eq!(bad(json!({"agent": "gpt"})).1, "Choose which agent runs this.");
        assert_eq!(bad(json!({"container": "yes"})).1, "Choose where this agent works.");
        assert_eq!(bad(json!({"checkout": "x"})).1, "Unknown checkout 'x'.");
        assert_eq!(bad(json!({"worktree": "rel/path"})).1, "Its folder needs a full path.");
        assert_eq!(bad(json!({"branch": "--evil"})).1, "'--evil' isn't a branch name Assieme can use.");
        assert_eq!(bad(json!({"branch": "a..b"})).1, "'a..b' isn't a branch name Assieme can use.");
        assert_eq!(bad(json!({"resume": "-x"})).1, "That conversation can't be picked up.");
        assert_eq!(bad(json!({"tools_off": ["nope"]})).1, "That list of skills and servers can't be used.");
        assert_eq!(bad(json!({"tools_known": "x"})).1, "That list of skills and servers can't be used.");
        assert_eq!(bad(json!({"tools_new": 3})).1, "That list of skills and servers can't be used.");
        assert_eq!(bad(json!({"musician": "kazoo"})).1, "That instrument isn't one Assieme draws.");
        assert_eq!(bad(json!({"parent": "ghost"})).1, "Reports to 'ghost', which isn't in this team.");
        assert_eq!(bad(json!({"brief": "  \n"})).1, "Every agent needs a brief.");
        assert_eq!(bad(json!({"effort": "ultra"})).1, "Claude Code has no thinking effort \"ultra\".");
        assert_eq!(bad(json!({"model": "a b"})).1, "Model names use letters, digits, dots and dashes.");
        assert_eq!(bad(json!({"kind": "wizard"})).1, "Unknown role 'wizard'.");
        assert_eq!(bad(json!({"level": "god"})).1, "Unknown permission level 'god'.");
        assert_eq!(validate(&[json!({"name": "a", "agent": "claude", "brief": "x", "tools_off": ["skill:my skill"], "musician": "piano", "worktree": "/abs", "branch": "feat/x-1", "resume": "abc-123"})]), Ok(()));
    }

    #[test]
    fn a_team_is_named_after_its_folder_and_never_twice() {
        let taken: BTreeSet<String> = ["my-app".to_string(), "my-app-2".to_string()].into();
        assert_eq!(team_name("/Users/x/My App!/", &BTreeSet::new()), "my-app");
        assert_eq!(team_name("/Users/x/My App", &taken), "my-app-3");
        assert_eq!(team_name("/Users/x/___", &BTreeSet::new()), "team");
        assert_eq!(team_name("/", &BTreeSet::new()), "team");
    }

    fn rich_roles() -> Vec<Value> {
        vec![
            json!({"name": "lead", "agent": "claude", "job": "plans — \"quoted\" ✓ 🎻", "checkout": "read-only", "kind": "planner",
                   "brief": "Lead.\n\nSecond paragraph with ünïcode and a \\ backslash.\n\n\n", "model": "opus", "effort": "high",
                   "owns": ["src/a.rs", "docs/"], "group": "g1"}),
            json!({"name": "dev", "agent": "codex", "job": "builds", "parent": "lead", "kind": "builder", "brief": "Build.", "container": true,
                   "share_signin": true, "level": "safe", "tools_new": false, "tools_off": ["skill:a b"], "tools_known": ["server:x"],
                   "rules": [{"decision": "block", "program": ["npm", "publish"], "words": "never publish \"ever\""}]}),
            json!({"name": "mine", "agent": "gemini", "brief": "Mine.", "worktree": "/some/where", "branch": "feat/x", "resume": "abc-1"}),
        ]
    }

    #[test]
    fn a_saved_team_loads_back_exactly() {
        let dir = tempfile::tempdir().unwrap();
        let practices = json!({"steward": false, "fresh-start": true}).as_object().cloned().unwrap();
        let scripts = BTreeMap::from([("setup".to_string(), "make \"all\"".to_string()), ("run".to_string(), String::new())]);
        let folder = save("/proj/x", &rich_roles(), &practices, "demo", dir.path(), Some(&scripts)).unwrap();
        let tm = preset::load(&folder, false).unwrap();
        assert_eq!((tm.name.as_str(), tm.project.as_str()), ("demo", "/proj/x"));
        assert_eq!(tm.practices, practices);
        assert_eq!(tm.scripts, BTreeMap::from([("setup".to_string(), "make \"all\"".to_string())]));
        let names: Vec<_> = tm.roles.iter().map(|r| r.name.as_str()).collect();
        assert_eq!(names, ["lead", "dev", "mine"]);
        let (lead, dev, mine) = (&tm.roles[0], &tm.roles[1], &tm.roles[2]);
        assert_eq!(lead.job, "plans — \"quoted\" ✓ 🎻");
        assert_eq!(lead.brief, "Lead.\n\nSecond paragraph with ünïcode and a \\ backslash.\n");
        assert_eq!((lead.checkout.as_str(), lead.owns.clone(), lead.group.as_deref()), ("read-only", vec!["src/a.rs".to_string(), "docs/".to_string()], Some("g1")));
        assert_eq!((dev.parent.as_deref(), dev.container, dev.share_signin, dev.tools_new), (Some("lead"), true, true, false));
        assert_eq!((dev.tools_off.clone(), dev.tools_known.clone()), (vec!["skill:a b".to_string()], vec!["server:x".to_string()]));
        assert_eq!(dev.rules, vec![json!({"decision": "block", "program": ["npm", "publish"], "words": "never publish \"ever\""})]);
        assert_eq!((mine.worktree.as_deref(), mine.branch.as_deref(), mine.resume.as_deref()), (Some("/some/where"), Some("feat/x"), Some("abc-1")));
        let saved: Vec<_> = tm.roles.iter().map(|r| r.musician.as_deref().unwrap()).collect();
        assert_eq!(saved, ["piano", "violin", "flute"], "each role's instrument is chosen once and saved");
        assert!(saved.iter().all(|m| crate::musicians::INSTRUMENTS.contains(m)));
    }

    #[test]
    fn saving_over_a_team_replaces_it_and_refuses_unknown_scripts() {
        let dir = tempfile::tempdir().unwrap();
        save("/p", &rich_roles(), &Map::new(), "demo", dir.path(), None).unwrap();
        save("/p", &[good("only")], &Map::new(), "demo", dir.path(), None).unwrap();
        assert_eq!(preset::load(&dir.path().join("demo"), false).unwrap().roles.len(), 1);
        assert!(!dir.path().join("demo/lead.md").exists());
        let bad = BTreeMap::from([("deploy".to_string(), "x".to_string())]);
        assert_eq!(save("/p", &[good("a")], &Map::new(), "t", dir.path(), Some(&bad)).unwrap_err().message, "Unknown script deploy.");
    }

    #[test]
    fn an_instrument_chosen_once_does_not_change_when_agents_come_and_go() {
        let dir = tempfile::tempdir().unwrap();
        let roles = vec![json!({"name": "a", "agent": "claude", "kind": "builder", "brief": "x"}), json!({"name": "b", "agent": "claude", "kind": "builder", "brief": "x"})];
        let folder = save("/p", &roles, &Map::new(), "t", dir.path(), None).unwrap();
        forget_role(&folder, "a").unwrap();
        let tm = preset::load(&folder, false).unwrap();
        assert_eq!(tm.roles[0].musician.as_deref(), Some("cello"), "b stays the cello though it is now the first builder");
    }

    #[test]
    fn forgetting_a_role_hands_its_children_to_its_parent() {
        let dir = tempfile::tempdir().unwrap();
        let roles = vec![good("top"), json!({"name": "mid", "agent": "claude", "brief": "m", "parent": "top"}), json!({"name": "low", "agent": "claude", "brief": "l", "parent": "mid"})];
        let folder = save("/p", &roles, &Map::new(), "t", dir.path(), None).unwrap();
        forget_role(&folder, "mid").unwrap();
        let tm = preset::load(&folder, false).unwrap();
        assert_eq!(tm.roles.iter().map(|r| (r.name.as_str(), r.parent.as_deref())).collect::<Vec<_>>(), [("top", None), ("low", Some("top"))]);
        assert!(!folder.join("mid.md").exists());
    }

    #[test]
    fn a_brought_over_role_is_released_or_its_conversation_cleared_in_place() {
        let dir = tempfile::tempdir().unwrap();
        let folder = save("/p", &rich_roles(), &Map::new(), "t", dir.path(), None).unwrap();
        clear_resume(&folder, "mine").unwrap();
        let r = &preset::load(&folder, false).unwrap().roles[2];
        assert_eq!((r.resume.as_deref(), r.worktree.as_deref(), r.branch.as_deref()), (None, Some("/some/where"), Some("feat/x")));
        clear_worktree(&folder, "mine").unwrap();
        let tm = preset::load(&folder, false).unwrap();
        assert_eq!((tm.roles[2].worktree.clone(), tm.roles[2].branch.clone()), (None, None));
        assert_eq!(tm.roles.len(), 3, "the others are as they were");
        assert_eq!(tm.roles[2].musician.as_deref(), Some("flute"));
    }

    #[test]
    fn instruments_are_picked_and_drawn_from_what_is_saved() {
        let dir = tempfile::tempdir().unwrap();
        let roles = vec![json!({"name": "a", "agent": "claude", "kind": "planner", "brief": "x"}), json!({"name": "b", "agent": "claude", "kind": "builder", "brief": "x"})];
        save("/p", &roles, &Map::new(), "t", dir.path(), None).unwrap();
        let drawn = band("t", Some(dir.path()));
        assert_eq!(drawn["a"], json!({"musician": "piano", "palette": 0, "chosen_for": "the planner"}));
        assert_eq!(drawn["b"]["chosen_for"], "the first builder");
        set_musician("t", "b", "trumpet", Some(dir.path())).unwrap();
        let drawn = band("t", Some(dir.path()));
        assert_eq!(drawn["b"], json!({"musician": "trumpet", "palette": 0, "chosen_for": null}));
        assert_eq!(set_musician("t", "b", "kazoo", Some(dir.path())).unwrap_err().message, "That instrument isn't one Assieme draws.");
        assert_eq!(set_musician("t", "zz", "piano", Some(dir.path())).unwrap_err().message, "zz is not in this team.");
        assert!(set_musician("nope", "b", "piano", Some(dir.path())).unwrap_err().message.starts_with("This team was started from the command line"));
        assert_eq!(band("nope", Some(dir.path())), json!({}));
        assert!(roster("nope", Some(dir.path())).is_empty());
    }

    #[test]
    fn the_roster_names_each_role_s_job_group_and_parent() {
        let dir = tempfile::tempdir().unwrap();
        save("/p", &rich_roles(), &Map::new(), "t", dir.path(), None).unwrap();
        let r = roster("t", Some(dir.path()));
        assert_eq!(r["dev"], json!({"job": "builds", "group": null, "parent": "lead"}));
        assert_eq!(r["lead"]["group"], "g1");
    }

    #[test]
    fn the_start_screen_lists_built_in_teams_then_the_last_one_and_recent_projects() {
        with_state(|| {
            let dir = tempfile::tempdir().unwrap();
            for (name, project) in [("old", "/p/one"), ("mid", "/p/two"), ("new", "/p/one"), ("newest", "/p/three"), ("extra", "/p/four")] {
                save(project, &[good("a")], &Map::new(), name, dir.path(), None).unwrap();
                std::thread::sleep(std::time::Duration::from_millis(15));
            }
            std::fs::create_dir_all(dir.path().join("broken")).unwrap(); // a half-written folder
            std::fs::write(dir.path().join("broken/team.toml"), "name = ").unwrap();
            let got = presets_with(Some(dir.path()), json!({"fresh-start": true}), json!({}));
            let ids: Vec<_> = got["presets"].as_array().unwrap().iter().map(|p| p["id"].as_str().unwrap()).collect();
            assert_eq!(ids, ["one-agent", "builder-reviewer", "full"]);
            assert_eq!(got["last"]["id"], "extra");
            assert_eq!(got["last"]["label"], "Same as last time");
            assert_eq!(got["recent"], json!(["/p/four", "/p/three", "/p/one"]));
            assert_eq!(got["kinds"]["builder"]["checkout"], "branch");
            assert!(got["kinds"]["planner"]["brief"].as_str().unwrap().len() > 20);
            assert_eq!(got["practice_defaults"], json!({"fresh-start": true}));
            assert!(got["practices"]["groups"].is_array());
            let one = &got["presets"][0];
            assert_eq!(one["label"], "One agent");
            assert_eq!(one["roles"][0]["name"], "builder");
            assert!(one["roles"][0]["brief"].as_str().unwrap().len() > 20);
        });
    }

    #[test]
    fn with_no_saved_team_there_is_no_last() {
        with_state(|| {
            let dir = tempfile::tempdir().unwrap();
            let got = presets_with(Some(&dir.path().join("none")), json!({}), json!({}));
            assert_eq!((got["last"].is_null(), got["recent"].clone()), (true, json!([])));
        });
    }

    #[test]
    fn codex_models_come_from_the_list_codex_keeps() {
        let dir = tempfile::tempdir().unwrap();
        let cache = dir.path().join("models_cache.json");
        std::fs::write(&cache, json!({"models": [
            {"slug": "gpt-a", "description": "Fast", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}, {"effort": "nonsense"}]},
            {"slug": "gpt-b", "supported_reasoning_levels": [{"effort": "high"}, {"effort": "xhigh"}]},
            {"slug": "bad slug!", "supported_reasoning_levels": []},
            {"supported_reasoning_levels": []}]}).to_string()).unwrap();
        let got = model_options_at(&cache);
        assert_eq!(got["codex"]["models"].as_array().unwrap().len(), 2);
        assert_eq!(got["codex"]["models"][0], json!({"id": "gpt-a", "label": "gpt-a", "about": "Fast", "efforts": ["low", "high"]}));
        assert_eq!(got["codex"]["models"][1]["about"], "");
        assert_eq!(got["codex"]["efforts"], json!(["high"]), "only what every model has");
        assert_eq!(got["claude"]["models"][0], json!({"id": "fable", "label": "Fable", "efforts": ["low", "medium", "high", "xhigh", "max"]}));
        assert_eq!(got["gemini"], json!({"models": [], "efforts": []}));
        for broken in ["not json", "[]", "{\"models\": 4}"] {
            std::fs::write(&cache, broken).unwrap();
            assert_eq!(model_options_at(&cache)["codex"], json!({"models": [], "efforts": []}), "{broken}");
        }
        assert_eq!(model_options_at(&dir.path().join("missing"))["codex"]["models"], json!([]));
    }

    struct FakeFolders {
        git: bool,
        broad: bool,
        preview: Result<Value, crate::setup::SetupError>,
        count: Result<usize, crate::setup::SetupError>,
    }

    impl Folders for FakeFolders {
        fn git_ready(&self) -> bool {
            self.git
        }
        fn too_broad(&self, _: &str) -> bool {
            self.broad
        }
        fn folder_preview(&self, _: &str) -> Result<Value, crate::setup::SetupError> {
            self.preview.clone()
        }
        fn uncommitted(&self, _: &str) -> Result<usize, crate::setup::SetupError> {
            self.count.clone()
        }
    }

    fn folders() -> FakeFolders {
        FakeFolders { git: true, broad: false, preview: Ok(json!({})), count: Ok(0) }
    }

    #[test]
    fn a_project_is_checked_before_a_team_is_made() {
        use crate::setup::{SetupError, TooLarge, Unfinished};
        let (_d, p) = repo();
        assert_eq!(project_check_with(&folders(), &p), json!({"ok": true}));
        assert_eq!(project_check_with(&FakeFolders { count: Ok(3), ..folders() }, &p), json!({"ok": true, "uncommitted": 3}));
        assert_eq!(project_check_with(&FakeFolders { count: Err(SetupError::Failed("x".into())), ..folders() }, &p), json!({"ok": true}));
        assert_eq!(project_check_with(&FakeFolders { count: Err(SetupError::Unfinished(Unfinished("Stopped half way.".into()))), ..folders() }, &p), json!({"ok": false, "reason": "Stopped half way."}));
        let large = json!({"ok": false, "reason": TOO_LARGE, "too_large": true});
        assert_eq!(project_check_with(&FakeFolders { count: Err(SetupError::TooLarge(TooLarge("big".into()))), ..folders() }, &p), large);
        assert_eq!(project_check_with(&FakeFolders { broad: true, ..folders() }, &p), large);
        assert_eq!(project_check_with(&folders(), "/no/such/folder"), json!({"ok": false, "reason": "This folder doesn't exist."}));
        let plain = tempfile::tempdir().unwrap();
        let q = plain.path().to_str().unwrap();
        assert_eq!(project_check_with(&folders(), q), json!({"ok": false, "reason": NEEDS_SAVE, "setup": true}));
        let big = FakeFolders { preview: Err(SetupError::TooLarge(TooLarge("big".into()))), ..folders() };
        assert_eq!(project_check_with(&big, q), large);
        let no_git = project_check_with(&FakeFolders { git: false, ..folders() }, &p);
        assert_eq!(no_git["ok"], false);
        assert!(no_git["reason"] == NO_TOOLS || no_git["reason"] == NO_GIT);
    }

    fn starter(teams: &Path, scheduled: &[&str]) -> Starter {
        let sched: BTreeSet<String> = scheduled.iter().map(|s| s.to_string()).collect();
        let inner = Inner {
            rt: Runtime::new("assieme", None),
            teams: teams.to_path_buf(),
            scheduled: Box::new(move || sched.clone()),
            folders: Box::new(folders()),
            state: Mutex::new(State::default()),
            now: || 1000.0,
        };
        Starter { inner: Arc::new(inner) }
    }

    fn go(s: &Starter, w: &Fake, project: &str, roles: &[Value], name: Option<&str>) -> Result<Prepared, NewTeamError> {
        s.prepare(w, project, roles, &json!({}), name, None, &[])
    }

    #[test]
    fn a_start_saves_the_team_makes_every_copy_and_records_it_as_starting() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let s = starter(teams.path(), &[]);
            let w = Fake::default();
            let got = go(&s, &w, &p, &[good("lead"), json!({"name": "dev", "agent": "claude", "brief": "d", "parent": "lead"})], None).unwrap();
            assert_eq!(got.tm.name, team_name(&p, &BTreeSet::new()));
            assert!(teams.path().join(&got.tm.name).join("team.toml").is_file());
            assert!(Path::new(&format!("{p}/.assieme/worktrees/dev")).is_dir());
            assert_eq!(got.fresh.keys().collect::<Vec<_>>(), ["dev", "lead"]);
            let recs = s.records();
            assert_eq!(recs.len(), 1);
            assert_eq!(recs[0]["roles"], json!([{"role": "lead", "state": "starting", "detail": ""}, {"role": "dev", "state": "starting", "detail": ""}]));
            assert_eq!(recs[0]["done"], false);
            let again = go(&s, &w, &p, &[good("lead")], None).unwrap_err();
            assert_eq!((again.field.as_str(), again.message.as_str()), ("start", "This team is still starting."));
        });
    }

    #[test]
    fn the_background_run_fills_in_each_role_and_finishes_the_record() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let s = starter(teams.path(), &[]);
            let w = Fake::default();
            let got = go(&s, &w, &p, &[good("lead")], None).unwrap();
            run_with(&s.inner, &w, &got.rec, &got.tm, "Do the thing", &got.fresh);
            let recs = s.records();
            assert_eq!((recs[0]["done"].clone(), recs[0]["error"].clone()), (json!(true), Value::Null));
            assert_eq!(recs[0]["roles"][0]["state"], "running");
            assert!(w.sent.borrow()[0].1.ends_with("Do the thing"));
            assert_eq!(crate::brief::load(&got.tm.name)["goal"], "Do the thing");
            // a new start in the same project is allowed once this one is done
            let again = go(&s, &w, &p, &[good("lead")], None);
            assert!(again.is_ok(), "{:?}", again.err());
        });
    }

    #[test]
    fn a_failing_start_is_kept_with_its_error_until_the_person_deals_with_it() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let s = starter(teams.path(), &[]);
            let w = Fake::default();
            let got = go(&s, &w, &p, &[good("lead")], None).unwrap();
            *w.busy.borrow_mut() = 10_000; // the brief can never be sent
            run_with(&s.inner, &w, &got.rec, &got.tm, "x", &got.fresh);
            let rec = lock(&got.rec).clone();
            assert!(rec.error.is_some_and(|e| e.contains("still starting")));
            assert!(rec.done);
            let late = Starter { inner: Arc::new(Inner { now: || 1000.0 + 100_000.0, ..unwrap_inner(&s) }) };
            assert_eq!(late.records().len(), 1, "a failed start is not dropped after ten minutes");
            late.forget(&got.tm.name, "lead");
            assert!(late.records().is_empty());
        });
    }

    fn unwrap_inner(s: &Starter) -> Inner {
        let st = lock(&s.inner.state);
        Inner {
            rt: s.inner.rt.clone(),
            teams: s.inner.teams.clone(),
            scheduled: Box::new(BTreeSet::new),
            folders: Box::new(folders()),
            state: Mutex::new(State { recs: st.recs.clone(), busy: BTreeMap::new() }),
            now: clock,
        }
    }

    #[test]
    fn a_finished_start_leaves_the_screen_after_ten_minutes() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let s = starter(teams.path(), &[]);
            let w = Fake::default();
            let got = go(&s, &w, &p, &[good("lead")], None).unwrap();
            run_with(&s.inner, &w, &got.rec, &got.tm, "x", &got.fresh);
            assert_eq!(s.records().len(), 1);
            let later = Starter { inner: Arc::new(Inner { now: || 1000.0 + KEEP_DONE_S + 1.0, ..unwrap_inner(&s) }) };
            assert!(later.records().is_empty());
        });
    }

    #[test]
    fn a_project_with_a_running_team_refuses_another_unless_they_share_nothing_and_one_is_scheduled() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            save(&p, &[good("lead")], &Map::new(), "running-one", teams.path(), None).unwrap();
            let w = Fake::default();
            *w.listing.borrow_mut() = vec![json!({"address": "t1", "state": "live", "labels": {team::TEAM: "running-one", team::ROLE: "lead"}})];
            let s = starter(teams.path(), &[]);
            let e = go(&s, &w, &p, &[good("other")], None).unwrap_err();
            assert_eq!((e.field.as_str(), e.message.as_str()), ("project", "This project already has a running team: running-one. Stop it first."));
            let s = starter(teams.path(), &["running-one"]);
            assert!(go(&s, &w, &p, &[good("other")], Some("night-run")).is_ok(), "a scheduled run beside it, no role names shared");
            let s = starter(teams.path(), &["running-one"]);
            let e = go(&s, &w, &p, &[good("lead")], Some("night-run")).unwrap_err();
            assert_eq!(e.field, "project", "the same role name would share a copy");
        });
    }

    #[test]
    fn a_scheduled_runs_own_name_cannot_be_taken_twice() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let w = Fake::default();
            *w.listing.borrow_mut() = vec![json!({"address": "t1", "state": "live", "labels": {team::TEAM: "night-run", team::ROLE: "x"}})];
            let e = go(&starter(teams.path(), &[]), &w, &p, &[good("lead")], Some("night-run")).unwrap_err();
            assert_eq!(e.message, "night-run is still running.");
        });
    }

    #[test]
    fn a_bad_form_a_missing_program_or_a_dirty_copy_is_reported_and_leaves_nothing_busy() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let s = starter(teams.path(), &[]);
            let w = Fake { missing: vec!["codex".into()], ..Fake::default() };
            assert_eq!(go(&s, &w, &p, &[], None).err().unwrap().field, "roles");
            let e = go(&s, &w, &p, &[json!({"name": "a", "agent": "codex", "brief": "x"})], None).err().unwrap();
            assert_eq!(e.message, "not installed: codex. Install it, then start again.");
            std::fs::create_dir_all(format!("{p}/.assieme/worktrees/lead")).unwrap();
            let e = go(&s, &Fake::default(), &p, &[good("lead")], None).err().unwrap();
            assert!(e.message.contains("A folder is in the way of lead's copy") && !e.detail.is_empty(), "{e:?}");
            std::fs::remove_dir_all(format!("{p}/.assieme/worktrees/lead")).unwrap();
            assert!(go(&s, &Fake::default(), &p, &[good("lead")], None).is_ok(), "the failed attempts left the project free");
            let bad_project = Starter { inner: Arc::new(Inner { folders: Box::new(FakeFolders { git: true, ..folders() }), ..unwrap_inner(&s) }) };
            assert_eq!(go(&bad_project, &w, "/no/such/folder", &[good("lead")], None).err().unwrap().field, "project");
        });
    }

    #[test]
    fn a_setup_script_waits_for_the_persons_yes() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let s = starter(teams.path(), &[]);
            let w = Fake::default();
            let scripts_in = BTreeMap::from([("setup".to_string(), "touch ran-setup".to_string())]);
            let got = s.prepare(&w, &p, &[good("lead")], &json!({}), None, Some(&scripts_in), &[]).unwrap();
            setup_scripts(&got.rec, &got.tm, &got.fresh);
            assert_eq!(lock(&got.rec).ask_scripts, vec![json!({"which": "setup", "line": "touch ran-setup"})]);
            assert!(!Path::new(&format!("{p}/.assieme/worktrees/lead/ran-setup")).exists());
            assert_eq!(s.records()[0]["ask_scripts"][0]["which"], "setup");
        });
    }

    #[test]
    fn the_scripts_the_person_agreed_to_are_remembered_for_that_team() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let s = starter(teams.path(), &[]);
            let scripts_in = BTreeMap::from([("setup".to_string(), "make".to_string()), ("run".to_string(), "go".to_string())]);
            let got = s.prepare(&Fake::default(), &p, &[good("lead")], &json!({}), None, Some(&scripts_in), &["setup".to_string()]).unwrap();
            assert!(scripts::allowed(&got.tm.name, "setup", "make"));
            assert!(!scripts::allowed(&got.tm.name, "run", "go"));
        });
    }

    #[test]
    fn an_agent_is_added_to_a_running_team_and_a_failed_add_takes_it_back_out() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let folder = save(&p, &[good("lead")], &Map::new(), "demo", teams.path(), None).unwrap();
            let s = starter(teams.path(), &[]);
            let w = Fake::default();
            let new = json!({"name": "extra", "agent": "claude", "kind": "reviewer", "brief": "", "parent": "lead"});
            let e = s.prepare_added(&w, "demo", &new).unwrap_err();
            assert_eq!(e.message, "This team has stopped. Start it again to add an agent.");
            *w.listing.borrow_mut() = vec![json!({"address": "t1", "state": "live", "labels": {team::TEAM: "demo", team::ROLE: "lead"}})];
            let e = s.prepare_added(&w, "nope", &new).unwrap_err();
            assert!(e.message.starts_with("This team was started from the command line"));
            let (rec, tm, name, folder2) = s.prepare_added(&w, "demo", &new).unwrap();
            assert_eq!((name.as_str(), folder2), ("extra", folder.clone()));
            let r = tm.roles.iter().find(|r| r.name == "extra").unwrap();
            assert_eq!((r.checkout.as_str(), r.kind.as_deref(), r.musician.as_deref()), ("read-only", Some("reviewer"), Some("reviewer")));
            assert!(r.brief.len() > 20, "an empty brief gets the kind's own");
            assert!(Path::new(&format!("{p}/.assieme/worktrees/extra")).is_dir());
            assert_eq!(lock(&rec).roles, vec![("extra".to_string(), "starting".to_string(), String::new())]);
            // the agent exits at once: it is taken back out
            *w.first.borrow_mut() = Some(json!({"state": "finished"}));
            run_added(&s.inner, &w, &rec, &tm, "extra", &folder);
            assert_eq!(preset::load(&folder, false).unwrap().roles.len(), 1);
            assert!(lock(&rec).done);
            // a name already taken is refused by the form check
            let dup = json!({"name": "lead", "agent": "claude", "brief": "x"});
            assert_eq!(s.prepare_added(&w, "demo", &dup).unwrap_err().field, "role:1");
        });
    }

    #[test]
    fn an_add_whose_copy_cannot_be_made_leaves_the_saved_team_as_it_was() {
        with_state(|| {
            let (_d, p) = repo();
            let teams = tempfile::tempdir().unwrap();
            let folder = save(&p, &[good("lead")], &Map::new(), "demo", teams.path(), None).unwrap();
            assert!(worktree::run(&p, &["branch", "assieme/demo/extra"]).ok);
            let w = Fake::default();
            *w.listing.borrow_mut() = vec![json!({"address": "t1", "state": "live", "labels": {team::TEAM: "demo"}})];
            let e = starter(teams.path(), &[]).prepare_added(&w, "demo", &good("extra")).unwrap_err();
            assert!(e.message.contains("is left over from an earlier team"), "{e:?}");
            assert_eq!(preset::load(&folder, false).unwrap().roles.len(), 1);
        });
    }

    #[test]
    fn brought_over_agents_whose_folder_has_gone_are_told_about_it() {
        let here = tempfile::tempdir().unwrap();
        let roles = [
            Role { worktree: Some("/gone/place/".into()), branch: Some("feat".into()), ..Role::new("a", "claude") },
            Role { worktree: Some(here.path().to_string_lossy().into_owned()), branch: Some("feat".into()), ..Role::new("b", "claude") },
            Role { worktree: Some("/gone".into()), ..Role::new("c", "claude") },
        ];
        assert_eq!(gone_notes(&roles), ["place's folder isn't there any more. Its branch feat is kept: start gives it a new copy."]);
    }

    fn models() -> Value {
        json!({"claude": {"models": [{"id": "opus", "label": "Opus", "efforts": ["low", "high"]}], "efforts": ["low", "high"]},
               "codex": {"models": [{"id": "gpt-a", "about": "Fast", "efforts": ["low"]}], "efforts": ["low"]},
               "gemini": {"models": [], "efforts": []}})
    }

    #[test]
    fn the_suggestion_prompt_says_what_the_person_chose_and_what_exists() {
        let agents = vec!["claude".to_string(), "codex".to_string(), "gemini".to_string()];
        let p = suggest_prompt(&json!("feature"), &json!("every"), &json!("lean"), &agents, &models(), "");
        assert!(p.contains("The change is a feature. The person wants to review every change. Budget: keep it lean (fewer agents, cheaper)."), "{p}");
        assert!(p.contains("- claude models, strongest and most expensive first: opus. Efforts: low, high"));
        assert!(p.contains("- codex model gpt-a (Fast). Efforts: low"));
        assert!(p.contains("- gemini: its own default only; leave model and effort out"));
        assert!(p.contains("Use 1 to 8 agents. Each agent is one of these, the ones installed: claude, codex, gemini."));
        assert!(p.contains("- planner: reads the project, splits the task and hands out pieces; never edits"));
        let with_task = suggest_prompt(&json!("odd"), &json!("x"), &json!("y"), &agents, &models(), "Fix the bug");
        assert!(with_task.contains("The task: Fix the bug. The person wants to review x. Budget: y."));
    }

    fn answer(v: Value) -> Option<Map<String, Value>> {
        v.as_object().cloned()
    }

    #[test]
    fn an_answer_becomes_a_team_only_with_what_is_installed_and_offered() {
        let installed = vec!["claude".to_string(), "codex".to_string()];
        let ok = answer(json!({"why": "Small.", "roles": [
            {"name": "boss", "kind": "planner", "agent": "claude", "job": "plans", "model": "opus", "effort": "high"},
            {"name": "dev", "kind": "builder", "agent": "codex", "job": "builds", "model": "ghost-model", "effort": "low"},
            {"name": "rev", "kind": "reviewer", "agent": "claude", "model": "opus", "effort": "ultra"}]}));
        let got = team_from(ok, &installed, &models()).unwrap();
        assert_eq!(got["why"], "Small.");
        let r = got["roles"].as_array().unwrap();
        assert_eq!((r[0]["parent"].clone(), r[0]["checkout"].clone(), r[0]["model"].clone(), r[0]["effort"].clone()), (Value::Null, json!("read-only"), json!("opus"), json!("high")));
        assert_eq!((r[1]["parent"].clone(), r[1]["model"].clone(), r[1]["effort"].clone()), (json!("boss"), Value::Null, json!("low")), "an unknown model is Default, its effort among the agent's own");
        assert_eq!((r[2]["model"].clone(), r[2]["effort"].clone()), (json!("opus"), Value::Null), "an effort the model lacks is dropped");
        assert_eq!(r[1]["brief"], kind_brief("builder"));
        let fail = |v: Value| team_from(answer(v), &installed, &models()).unwrap_err();
        assert_eq!(team_from(None, &installed, &models()).unwrap_err(), "the answer wasn't a team.");
        assert_eq!(fail(json!({"roles": "x"})), "the answer wasn't a team.");
        assert_eq!(fail(json!({"roles": []})), "no agents in the answer.");
        assert_eq!(fail(json!({"roles": vec![json!({}); 9]})), "more than 8 agents.");
        assert_eq!(fail(json!({"roles": [{"kind": "wizard", "agent": "claude"}]})), "unknown role kind 'wizard'.");
        assert_eq!(fail(json!({"roles": [{"kind": "builder", "agent": "gemini"}]})), "it chose gemini, which is not installed.");
    }

    #[test]
    fn an_improved_brief_must_be_text_and_not_huge() {
        assert_eq!(brief_from(answer(json!({"brief": "  Better.  "}))), Some("Better.\n".into()));
        assert_eq!(brief_from(answer(json!({"brief": "   "}))), None);
        assert_eq!(brief_from(answer(json!({"brief": 4}))), None);
        assert_eq!(brief_from(answer(json!({"brief": "x".repeat(6001)}))), None);
        assert_eq!(brief_from(answer(json!({"brief": "x".repeat(6000)}))).map(|b| b.len()), Some(6001));
        assert_eq!(brief_from(None), None);
        let p = brief_prompt(&json!({"kind": "builder", "job": "", "owns": ["a/"], "parent": "boss", "brief": " Old. "}));
        assert!(p.contains("Role: builder (makes one change at a time with tests, on its own branch).\nIts job in one line: (not written yet)\nFiles only it edits: a/\nIt reports to the agent named boss."));
        assert!(p.contains("\"\"\"\nOld.\n\"\"\""));
        assert!(brief_prompt(&json!({"brief": "x"})).contains("Role: custom."));
    }

    #[test]
    fn plain_words_become_a_rule_only_when_the_answer_is_one() {
        let ok = rule_from(answer(json!({"decision": "block", "program": ["npm", "publish"]})), "never publish").unwrap();
        assert_eq!(ok, json!({"decision": "block", "program": ["npm", "publish"], "words": "never publish"}));
        let unclear = rule_from(answer(json!({"unclear": "That isn't a program."})), "be nice").unwrap_err();
        assert_eq!((unclear.field.as_str(), unclear.message.as_str()), ("suggest", "Couldn't turn that into a rule: That isn't a program."));
        assert!(rule_prompt("no deploys").contains("\"\"\"\nno deploys\n\"\"\""));
        assert_eq!(rule_draft("/p", "   ").unwrap_err().message, "Write the rule in a few words first.");
    }

    #[test]
    fn a_role_as_the_form_holds_it_leaves_out_what_only_brought_over_roles_have() {
        let r = Role { brief: "b".into(), job: "j".into(), ..Role::new("a", "claude") };
        let v = role_dict(&r);
        assert_eq!(v, json!({"name": "a", "agent": "claude", "job": "j", "checkout": "branch", "parent": null, "brief": "b",
            "model": null, "effort": null, "group": null, "kind": null, "owns": [], "level": null, "rules": [],
            "container": false, "share_signin": false}));
        let full = Role { worktree: Some("/w".into()), tools_new: false, tools_off: vec!["skill:a".into()], musician: Some("piano".into()), ..r };
        let v = role_dict(&full);
        assert_eq!((v["worktree"].clone(), v["tools_new"].clone(), v["tools_off"].clone(), v["musician"].clone()), (json!("/w"), json!(false), json!(["skill:a"]), json!("piano")));
        assert!(v.get("branch").is_none() && v.get("tools_known").is_none());
    }
}
