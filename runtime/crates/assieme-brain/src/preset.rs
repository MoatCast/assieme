//! Port of `assieme/preset.py`, owned by session D (see OWNERS.md).
//!
//! Team presets: a folder with team.toml and one Markdown brief per role (briefs use the core's template format).
use crate::launch::d2;
use crate::prelude::*;
use regex::Regex;
use serde::Deserialize;
use serde_json::json;

pub const AGENTS: &[&str] = &["claude", "codex", "gemini", "opencode", "cursor", "antigravity"];

pub const SCRIPTS: [&str; 3] = ["setup", "run", "archive"];
const PRACTICES: &str = include_str!("../../../../assieme/practices.toml");
const CRITIC: &str = include_str!("../../../../assieme/presets/briefs/critic.md");
const STEWARD_BRIEF: &str = include_str!("../../../../assieme/presets/briefs/steward.md");

#[derive(Debug, Clone, PartialEq)]
pub struct PresetError(pub String);

impl From<PresetError> for BrainError {
    fn from(e: PresetError) -> BrainError {
        BrainError::new(e.0)
    }
}

fn branch() -> String {
    "branch".into()
}

fn yes() -> bool {
    true
}

/// One role of a saved team. In team.toml `brief` names the brief's file.
#[derive(Debug, Clone, PartialEq, Default, Deserialize)]
#[serde(default)]
pub struct Role {
    pub name: String,
    pub agent: String,
    #[serde(rename = "brief")]
    pub brief_path: String,
    #[serde(skip)]
    pub brief: String,
    pub model: Option<String>,
    /// "branch" by default.
    #[serde(default = "branch")]
    pub checkout: String,
    pub parent: Option<String>,
    pub job: String,
    pub effort: Option<String>,
    /// Where it sits on the map of a big team.
    pub group: Option<String>,
    /// planner, builder, reviewer, tester; None for a custom role.
    pub kind: Option<String>,
    /// Paths in the project only this agent edits.
    pub owns: Vec<String>,
    /// safe, read-only or trusted; None: started as before levels existed.
    pub level: Option<String>,
    /// The person's own: {decision, program, words}.
    pub rules: Vec<Value>,
    pub container: bool,
    pub share_signin: bool,
    pub worktree: Option<String>,
    pub branch: Option<String>,
    pub resume: Option<String>,
    pub tools_off: Vec<String>,
    /// True by default.
    #[serde(default = "yes")]
    pub tools_new: bool,
    pub tools_known: Vec<String>,
    pub musician: Option<String>,
    /// Never saved: this start picks up the agent's last conversation (Restart with these).
    #[serde(skip)]
    pub cont: bool,
}

impl Role {
    /// A role with Assieme's defaults: its own branch, newly installed tools on.
    pub fn new(name: &str, agent: &str) -> Role {
        Role { name: name.into(), agent: agent.into(), checkout: branch(), tools_new: true, ..Role::default() }
    }
}

/// A saved team.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Team {
    pub name: String,
    pub project: String,
    pub practices: Map<String, Value>,
    pub roles: Vec<Role>,
    pub scripts: BTreeMap<String, String>,
}

#[derive(Deserialize)]
struct File {
    name: String,
    project: Option<String>,
    #[serde(default)]
    practices: Map<String, Value>,
    #[serde(default)]
    scripts: BTreeMap<String, Value>,
    #[serde(default)]
    role: Vec<Role>,
}

pub const STEWARD: &str = "steward";
const STEWARD_LINE: &str = "When you would stop, or need a decision only the person could make, send it to steward with \
message_agent first. Call report only when steward tells you it needs the person.";

/// The role's name, never its job's wording; the app's agentCount matches it.
static RESEARCHER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^(research|investigat)").expect("a fixed pattern"));

/// The brief without the template's `---` header.
fn body(text: &str) -> &str {
    if let Some(rest) = text.strip_prefix("---\n")
        && let Some(end) = rest.find("\n---")
    {
        return rest[end + 4..].trim_start_matches('\n');
    }
    text
}

fn parents_first(roles: Vec<Role>) -> Result<Vec<Role>, PresetError> {
    fn visit(roles: &[Role], i: usize, path: &mut Vec<usize>, out: &mut Vec<usize>) -> Result<(), PresetError> {
        if out.contains(&i) {
            return Ok(());
        }
        if path.contains(&i) {
            let chain: Vec<&str> = path.iter().chain([&i]).map(|j| roles[*j].name.as_str()).collect();
            return Err(PresetError(format!("roles form a parent loop: {}", chain.join(" -> "))));
        }
        if let Some(p) = roles.iter().position(|r| Some(&r.name) == roles[i].parent.as_ref()) {
            path.push(i);
            visit(roles, p, path, out)?;
            path.pop();
        }
        out.push(i);
        Ok(())
    }
    let mut out = vec![];
    for i in 0..roles.len() {
        visit(&roles, i, &mut vec![], &mut out)?;
    }
    Ok(out.into_iter().map(|i| roles[i].clone()).collect())
}

fn parse_practices(text: &str) -> Value {
    let data: Value = toml::from_str(text).unwrap_or_default();
    let list = |k: &str| data[k].as_array().cloned().unwrap_or_default();
    let groups: Vec<Value> = list("group")
        .into_iter()
        .map(|mut g| {
            g["rules"] = g.as_object_mut().and_then(|m| m.remove("rule")).unwrap_or(json!([]));
            g
        })
        .collect();
    json!({"groups": groups, "basics": list("basic"), "bigger": list("bigger")})
}

static DEFAULT_PRACTICES: LazyLock<Value> = LazyLock::new(|| parse_practices(PRACTICES));

/// `practices.toml`, compiled into the binary, unless `path` names another.
pub fn practices(path: Option<&Path>) -> Value {
    path.map_or_else(|| DEFAULT_PRACTICES.clone(), |p| parse_practices(&std::fs::read_to_string(p).unwrap_or_default()))
}

fn arr<'a>(v: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    v[key].as_array().into_iter().flatten()
}

fn id_of(v: &Value) -> &str {
    v["id"].as_str().unwrap_or("")
}

/// Every key a choice may hold: group, rule and bigger-team ids.
pub fn keys() -> BTreeSet<String> {
    let p = &*DEFAULT_PRACTICES;
    let rules = arr(p, "groups").flat_map(|g| arr(g, "rules"));
    arr(p, "groups").chain(rules).chain(arr(p, "bigger")).map(|x| id_of(x).to_string()).collect()
}

fn on(chosen: &Map<String, Value>, id: &str) -> bool {
    chosen.get(id) != Some(&Value::Bool(false))
}

/// The rules agents get: a rule is on when its group and itself are both on.
pub fn rules_on(chosen: &Map<String, Value>) -> Vec<Value> {
    let groups = arr(&DEFAULT_PRACTICES, "groups").filter(|g| on(chosen, id_of(g)));
    groups.flat_map(|g| arr(g, "rules")).filter(|r| on(chosen, id_of(r))).cloned().collect()
}

pub fn rule_on(chosen: &Map<String, Value>, rule_id: &str) -> bool {
    rules_on(chosen).iter().any(|r| id_of(r) == rule_id)
}

/// Roles the presets for bigger teams add, and lines for the briefs of the rest. Parents come first.
fn bigger(mut roles: Vec<Role>, chosen: &Map<String, Value>) -> Vec<Role> {
    let chose = |k: &str| chosen.get(k) == Some(&Value::Bool(true));
    let has = |roles: &[Role], n: &str| roles.iter().any(|r| r.name == n);
    let mut added: Vec<Role> = vec![];
    if chose("research-pairs") {
        for i in 0..roles.len() {
            let (r, critic) = (&roles[i], format!("{}-critic", roles[i].name));
            if RESEARCHER.is_match(&r.name) && !has(&roles, &critic) {
                added.push(Role {
                    brief: CRITIC.into(),
                    model: r.model.clone(),
                    checkout: "read-only".into(),
                    parent: Some(r.name.clone()),
                    job: format!("Checks every claim of {} at the source", r.name),
                    effort: r.effort.clone(),
                    group: r.group.clone(),
                    ..Role::new(&critic, &r.agent)
                });
                roles[i].brief = format!(
                    "{}\n\nBefore you report a finding, send it with its sources to {critic} and wait for its check. \
                     Report only what it confirmed, and say what it could not.\n",
                    r.brief.trim_end_matches('\n')
                );
            }
        }
    }
    if chose("steward") && !has(&roles, STEWARD) {
        let top = roles.iter().find(|r| r.parent.is_none()).unwrap_or(&roles[0]).clone();
        for r in roles.iter_mut().chain(added.iter_mut()) {
            r.brief = format!("{}\n\n{STEWARD_LINE}\n", r.brief.trim_end_matches('\n'));
        }
        added.push(Role {
            brief: STEWARD_BRIEF.into(),
            model: top.model.clone(),
            checkout: "read-only".into(),
            parent: Some(top.name.clone()),
            job: "Settles routine stops, so only real decisions reach you".into(),
            effort: top.effort.clone(),
            ..Role::new(STEWARD, &top.agent)
        });
    }
    roles.extend(added);
    roles
}

/// A path as `os.path.abspath` makes it: made absolute, `.` and `..` folded away, no symlinks followed.
pub fn absolute(path: &Path) -> PathBuf {
    let full = if path.is_absolute() { path.to_path_buf() } else { std::env::current_dir().unwrap_or_default().join(path) };
    let mut out = PathBuf::new();
    for part in full.components() {
        match part {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            other => out.push(other),
        }
    }
    out
}

fn check_role(r: &mut Role, folder: &Path) -> Result<(), String> {
    if r.name.is_empty() {
        return Err("every role needs a name".into());
    }
    let name = &r.name;
    if !AGENTS.contains(&r.agent.as_str()) {
        return Err(format!("role '{name}' has agent '{}'; use one of {}", r.agent, AGENTS.join(", ")));
    }
    if !["branch", "read-only"].contains(&r.checkout.as_str()) {
        return Err(format!("role '{name}' has checkout '{}'; use one of branch, read-only", r.checkout));
    }
    let file = if r.brief_path.is_empty() { format!("{name}.md") } else { r.brief_path.clone() };
    let path = folder.join(file);
    r.brief = std::fs::read_to_string(&path)
        .map(|t| body(&t).to_string())
        .map_err(|_| format!("role '{name}': brief file not found: {}", path.display()))?;
    r.brief_path = path.to_string_lossy().into_owned();
    let problem = crate::launch::check_model_effort(&r.agent, r.model.as_deref(), r.effort.as_deref()).or_else(|| {
        match &r.level {
            Some(l) if !d2::levels().contains(l) => Some(format!("unknown permission level '{l}'")),
            _ => d2::check_owns(&r.owns).or_else(|| d2::check_rules(&r.rules)),
        }
    });
    r.musician = r.musician.take().filter(|m| crate::musicians::INSTRUMENTS.contains(&m.as_str()));
    problem.map_or(Ok(()), |p| Err(format!("role '{name}': {p}")))
}

pub fn load(folder: &Path, practices: bool) -> Result<Team, PresetError> {
    let shown = folder.join("team.toml").display().to_string();
    let err = |m: String| PresetError(format!("{shown}: {m}"));
    let source = std::fs::read_to_string(&shown).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => PresetError(format!("no team.toml in {}", folder.display())),
        _ => err(e.to_string()),
    })?;
    let mut f: File = toml::from_str(&source).map_err(|e| err(e.message().to_string()))?;
    if f.name.is_empty() {
        return Err(err("the team needs a name".into()));
    }
    if f.role.is_empty() {
        return Err(err("the team has no roles ([[role]] tables)".into()));
    }
    let home = std::env::var("HOME").unwrap_or_default();
    let project = f.project.as_deref().unwrap_or(".");
    let project = absolute(&folder.join(project.strip_prefix("~/").map_or_else(|| PathBuf::from(project), |r| Path::new(&home).join(r))));
    for i in 0..f.role.len() {
        check_role(&mut f.role[i], folder).map_err(&err)?;
        if f.role[..i].iter().any(|r| r.name == f.role[i].name) {
            return Err(err(format!("duplicate role name '{}'", f.role[i].name)));
        }
    }
    for r in &f.role {
        if let Some(p) = r.parent.as_deref().filter(|p| !f.role.iter().any(|x| x.name == *p)) {
            return Err(PresetError(format!("role '{}': parent '{p}' is not a role in this team", r.name)));
        }
    }
    let known = keys();
    let unknown: Vec<&str> = f.practices.keys().filter(|k| !known.contains(*k)).map(String::as_str).collect();
    if !unknown.is_empty() {
        return Err(err(format!("unknown practice {}", unknown.join(", "))));
    }
    let mut roles = f.role;
    if practices {
        roles = bigger(roles, &f.practices);
        let lines: Vec<String> = rules_on(&f.practices).iter().map(|r| format!("- {}", r["text"].as_str().unwrap_or(""))).collect();
        let extra = if lines.is_empty() { String::new() } else { format!("\n\n## Team practices\n\n{}\n", lines.join("\n")) };
        for r in &mut roles {
            r.brief = format!("{}{extra}", r.brief.trim_end_matches('\n'));
        }
    }
    let scripts = f
        .scripts
        .into_iter()
        .filter(|(k, _)| SCRIPTS.contains(&k.as_str()))
        .filter_map(|(k, v)| v.as_str().filter(|s| !s.trim().is_empty()).map(|s| (k, s.to_string())))
        .collect();
    Ok(Team { name: f.name, project: project.to_string_lossy().into_owned(), practices: f.practices, roles: parents_first(roles)?, scripts })
}
