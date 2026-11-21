//! Port of `assieme/preset.py`, owned by session D (see OWNERS.md).
//!
//! Team presets: a folder with team.toml and one Markdown brief per role (briefs use the core's template format).
use crate::launch::policy;
use crate::prelude::*;
use serde_json::json;

pub const AGENTS: &[&str] = &["claude", "codex", "gemini", "opencode", "cursor", "antigravity"];

const CHECKOUTS: [&str; 2] = ["branch", "read-only"];
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

impl std::fmt::Display for PresetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// One role of a saved team.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Role {
    pub name: String,
    pub agent: String,
    pub brief_path: String,
    pub brief: String,
    pub model: Option<String>,
    /// "branch" by default.
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
    pub tools_new: bool,
    pub tools_known: Vec<String>,
    pub musician: Option<String>,
    /// Never saved: this start picks up the agent's last conversation (Restart with these).
    pub cont: bool,
}

impl Role {
    /// A role with Assieme's defaults: its own branch, newly installed tools on.
    pub fn new(name: &str, agent: &str) -> Role {
        Role { name: name.into(), agent: agent.into(), checkout: "branch".into(), tools_new: true, ..Role::default() }
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

pub const STEWARD: &str = "steward";
const STEWARD_LINE: &str = "When you would stop, or need a decision only the person could make, send it to steward with \
message_agent first. Call report only when steward tells you it needs the person.";

/// The role's name, never its job's wording; the app's agentCount matches it.
static RESEARCHER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^(research|investigat)").expect("a fixed pattern"));

fn ids(v: Option<&toml::Value>) -> Vec<String> {
    v.and_then(toml::Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
        .unwrap_or_default()
}

/// The brief without the template's `---` header.
fn body(text: &str) -> &str {
    if let Some(rest) = text.strip_prefix("---\n") {
        if let Some(end) = rest.find("\n---") {
            return rest[end + 4..].trim_start_matches('\n');
        }
    }
    text
}

fn parents_first(roles: Vec<Role>) -> Result<Vec<Role>, PresetError> {
    let by_name: BTreeMap<&str, &Role> = roles.iter().map(|r| (r.name.as_str(), r)).collect();
    let mut out: Vec<&Role> = vec![];
    fn visit<'a>(
        r: &'a Role,
        path: &mut Vec<&'a str>,
        by_name: &BTreeMap<&str, &'a Role>,
        out: &mut Vec<&'a Role>,
    ) -> Result<(), PresetError> {
        if out.iter().any(|o| o.name == r.name) {
            return Ok(());
        }
        if path.contains(&r.name.as_str()) {
            let chain: Vec<&str> = path.iter().copied().chain([r.name.as_str()]).collect();
            return Err(PresetError(format!("roles form a parent loop: {}", chain.join(" -> "))));
        }
        if let Some(parent) = r.parent.as_deref().and_then(|p| by_name.get(p)) {
            path.push(&r.name);
            visit(parent, path, by_name, out)?;
            path.pop();
        }
        out.push(r);
        Ok(())
    }
    for r in &roles {
        visit(r, &mut vec![], &by_name, &mut out)?;
    }
    Ok(out.into_iter().cloned().collect())
}

fn parse_practices(text: &str) -> Value {
    let data: toml::Table = toml::from_str(text).unwrap_or_default();
    let get = |k: &str| serde_json::to_value(data.get(k)).unwrap_or(Value::Null);
    let groups: Vec<Value> = get("group")
        .as_array()
        .into_iter()
        .flatten()
        .map(|g| {
            let mut m = g.as_object().cloned().unwrap_or_default();
            let rules = m.remove("rule").unwrap_or_else(|| json!([]));
            m.insert("rules".into(), rules);
            Value::Object(m)
        })
        .collect();
    let list = |k: &str| if get(k).is_array() { get(k) } else { json!([]) };
    json!({"groups": groups, "basics": list("basic"), "bigger": list("bigger")})
}

static DEFAULT_PRACTICES: LazyLock<Value> = LazyLock::new(|| parse_practices(PRACTICES));

/// `practices.toml`, compiled into the binary, unless `path` names another.
pub fn practices(path: Option<&Path>) -> Value {
    match path {
        None => DEFAULT_PRACTICES.clone(),
        Some(p) => parse_practices(&std::fs::read_to_string(p).unwrap_or_default()),
    }
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
    let mut out: BTreeSet<String> = arr(p, "groups").map(|g| id_of(g).to_string()).collect();
    out.extend(arr(p, "groups").flat_map(|g| arr(g, "rules")).map(|r| id_of(r).to_string()));
    out.extend(arr(p, "bigger").map(|b| id_of(b).to_string()));
    out
}

fn on(chosen: &Map<String, Value>, id: &str) -> bool {
    chosen.get(id) != Some(&Value::Bool(false))
}

/// The rules agents get: a rule is on when its group and itself are both on.
pub fn rules_on(chosen: &Map<String, Value>) -> Vec<Value> {
    arr(&DEFAULT_PRACTICES, "groups")
        .filter(|g| on(chosen, id_of(g)))
        .flat_map(|g| arr(g, "rules"))
        .filter(|r| on(chosen, id_of(r)))
        .cloned()
        .collect()
}

pub fn rule_on(chosen: &Map<String, Value>, rule_id: &str) -> bool {
    rules_on(chosen).iter().any(|r| id_of(r) == rule_id)
}

fn practice_rules(chosen: &Map<String, Value>) -> String {
    let lines: Vec<String> = rules_on(chosen).iter().map(|r| format!("- {}", r["text"].as_str().unwrap_or(""))).collect();
    if lines.is_empty() { String::new() } else { format!("\n\n## Team practices\n\n{}\n", lines.join("\n")) }
}

/// Roles the presets for bigger teams add, and lines for the briefs of the rest. Parents come first.
fn bigger(mut roles: Vec<Role>, chosen: &Map<String, Value>) -> Vec<Role> {
    let names: BTreeSet<String> = roles.iter().map(|r| r.name.clone()).collect();
    let mut added: Vec<Role> = vec![];
    if chosen.get("research-pairs") == Some(&Value::Bool(true)) {
        for r in roles.iter_mut() {
            let critic = format!("{}-critic", r.name);
            if RESEARCHER.is_match(&r.name) && !names.contains(&critic) {
                r.brief = format!(
                    "{}\n\nBefore you report a finding, send it with its sources to {critic} and wait for its check. \
                     Report only what it confirmed, and say what it could not.\n",
                    r.brief.trim_end_matches('\n')
                );
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
            }
        }
    }
    if chosen.get("steward") == Some(&Value::Bool(true)) && !names.contains(STEWARD) {
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

/// `'text'`, as Python prints a name in a message.
fn q(s: &str) -> String {
    format!("'{s}'")
}

fn text(t: &toml::Table, key: &str) -> Option<String> {
    t.get(key).and_then(toml::Value::as_str).map(String::from)
}

fn read_role(raw: &toml::Table, path: &str, folder: &Path) -> Result<Role, PresetError> {
    let name = text(raw, "name").filter(|n| !n.is_empty()).ok_or_else(|| PresetError(format!("{path}: every role needs a name")))?;
    let agent = text(raw, "agent").unwrap_or_default();
    if !AGENTS.contains(&agent.as_str()) {
        return Err(PresetError(format!("{path}: role {} has agent {}; use one of {}", q(&name), q(&agent), AGENTS.join(", "))));
    }
    let checkout = text(raw, "checkout").unwrap_or_else(|| "branch".into());
    if !CHECKOUTS.contains(&checkout.as_str()) {
        return Err(PresetError(format!("{path}: role {} has checkout {}; use one of {}", q(&name), q(&checkout), CHECKOUTS.join(", "))));
    }
    let brief_path = folder.join(text(raw, "brief").unwrap_or_else(|| format!("{name}.md")));
    let brief = match std::fs::read_to_string(&brief_path) {
        Ok(t) => body(&t).to_string(),
        Err(_) => {
            return Err(PresetError(format!("role {}: brief file not found: {}", q(&name), brief_path.display())));
        }
    };
    let (model, effort) = (text(raw, "model"), text(raw, "effort"));
    let problem = crate::launch::check_model_effort(&agent, model.as_deref(), effort.as_deref());
    if let Some(p) = problem {
        return Err(PresetError(format!("{path}: role {}: {p}", q(&name))));
    }
    let owns = ids(raw.get("owns"));
    let rules: Vec<Value> = raw.get("rules").and_then(|r| serde_json::to_value(r).ok()).and_then(|r| r.as_array().cloned()).unwrap_or_default();
    let level = text(raw, "level");
    let problem = match &level {
        Some(l) if !policy().levels().contains(l) => Some(format!("unknown permission level {}", q(l))),
        _ => policy().check_owns(&owns).or_else(|| policy().check_rules(&rules)),
    };
    if let Some(p) = problem {
        return Err(PresetError(format!("{path}: role {}: {p}", q(&name))));
    }
    let flag = |k: &str| raw.get(k).and_then(toml::Value::as_bool) == Some(true);
    let rules = rules
        .iter()
        .map(|x| {
            let program: Vec<Value> = x["program"].as_array().cloned().unwrap_or_default();
            json!({"decision": x["decision"], "program": program, "words": x["words"].as_str().unwrap_or("")})
        })
        .collect();
    Ok(Role {
        name: name.clone(),
        agent,
        brief_path: brief_path.to_string_lossy().into_owned(),
        brief,
        model,
        checkout,
        parent: text(raw, "parent"),
        job: text(raw, "job").unwrap_or_default(),
        effort,
        group: text(raw, "group"),
        kind: text(raw, "kind"),
        owns,
        level,
        rules,
        container: flag("container"),
        share_signin: flag("share_signin"),
        worktree: text(raw, "worktree"),
        branch: text(raw, "branch"),
        resume: text(raw, "resume"),
        tools_off: ids(raw.get("tools_off")),
        tools_new: raw.get("tools_new").and_then(toml::Value::as_bool) != Some(false),
        tools_known: ids(raw.get("tools_known")),
        musician: text(raw, "musician").filter(|m| crate::musicians::INSTRUMENTS.contains(&m.as_str())),
        cont: false,
    })
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

pub fn load(folder: &Path, practices: bool) -> Result<Team, PresetError> {
    let path = folder.join("team.toml");
    let shown = path.display().to_string();
    let source = std::fs::read_to_string(&path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            PresetError(format!("no team.toml in {}", folder.display()))
        } else {
            PresetError(format!("{shown}: {e}"))
        }
    })?;
    let data: toml::Table = toml::from_str(&source).map_err(|e| PresetError(format!("{shown}: {}", e.message())))?;
    let name = text(&data, "name").filter(|n| !n.is_empty()).ok_or_else(|| PresetError(format!("{shown}: the team needs a name")))?;
    let project_in = text(&data, "project").unwrap_or_else(|| ".".into());
    let project_in = match project_in.strip_prefix("~/") {
        Some(rest) => PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(rest),
        None => PathBuf::from(&project_in),
    };
    let project = absolute(&folder.join(project_in)).to_string_lossy().into_owned();
    let mut roles: Vec<Role> = vec![];
    for raw in data.get("role").and_then(toml::Value::as_array).into_iter().flatten().filter_map(toml::Value::as_table) {
        let role = read_role(raw, &shown, folder)?;
        if roles.iter().any(|r| r.name == role.name) {
            return Err(PresetError(format!("{shown}: duplicate role name {}", q(&role.name))));
        }
        roles.push(role);
    }
    if roles.is_empty() {
        return Err(PresetError(format!("{shown}: the team has no roles ([[role]] tables)")));
    }
    for r in &roles {
        if let Some(p) = r.parent.as_deref().filter(|p| !roles.iter().any(|x| x.name == *p)) {
            return Err(PresetError(format!("role {}: parent {} is not a role in this team", q(&r.name), q(p))));
        }
    }
    let chosen: Map<String, Value> = data
        .get("practices")
        .and_then(|p| serde_json::to_value(p).ok())
        .and_then(|p| p.as_object().cloned())
        .unwrap_or_default();
    let known = keys();
    let unknown: Vec<&str> = chosen.keys().filter(|k| !known.contains(*k)).map(String::as_str).collect(); // sorted
    if !unknown.is_empty() {
        return Err(PresetError(format!("{shown}: unknown practice {}", unknown.join(", "))));
    }
    if practices {
        roles = bigger(roles, &chosen);
        let extra = practice_rules(&chosen);
        for r in &mut roles {
            r.brief = format!("{}{extra}", r.brief.trim_end_matches('\n'));
        }
    }
    let scripts = data
        .get("scripts")
        .and_then(toml::Value::as_table)
        .into_iter()
        .flatten()
        .filter(|(k, _)| SCRIPTS.contains(&k.as_str()))
        .filter_map(|(k, v)| v.as_str().filter(|s| !s.trim().is_empty()).map(|s| (k.clone(), s.to_string())))
        .collect();
    Ok(Team { name, project, practices: chosen, roles: parents_first(roles)?, scripts })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn folder(toml: &str, briefs: &[(&str, &str)]) -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("team.toml"), toml).unwrap();
        for (n, t) in briefs {
            std::fs::write(d.path().join(n), t).unwrap();
        }
        d
    }

    fn err(toml: &str, briefs: &[(&str, &str)]) -> String {
        load(folder(toml, briefs).path(), true).unwrap_err().0
    }

    const TWO: &str = r#"
name = "demo"
project = "../proj"
[practices]
steward = false
[scripts]
setup = "make"
run = "  "
deploy = "x"
[[role]]
name = "lead"
agent = "claude"
brief = "lead.md"
[[role]]
name = "dev"
agent = "codex"
parent = "lead"
level = "safe"
owns = ["src/"]
musician = "trumpet"
tools_new = false
tools_off = ["skill:a", 3]
rules = [{ decision = "block", program = ["npm", "publish"] }]
"#;

    #[test]
    fn loads_a_team_with_everything_a_role_can_hold() {
        let d = folder(TWO, &[("lead.md", "---\nname: x\n---\nLead the team.\n"), ("dev.md", "Build.")]);
        let tm = load(d.path(), false).unwrap();
        assert_eq!(tm.name, "demo");
        assert_eq!(PathBuf::from(&tm.project), d.path().parent().unwrap().join("proj"));
        assert_eq!(tm.scripts, BTreeMap::from([("setup".to_string(), "make".to_string())]));
        let (lead, dev) = (&tm.roles[0], &tm.roles[1]);
        assert_eq!((lead.brief.as_str(), lead.checkout.as_str(), lead.tools_new), ("Lead the team.\n", "branch", true));
        assert_eq!(dev.parent.as_deref(), Some("lead"));
        assert_eq!((dev.level.as_deref(), dev.owns.clone(), dev.musician.as_deref()), (Some("safe"), vec!["src/".to_string()], Some("trumpet")));
        assert_eq!((dev.tools_new, dev.tools_off.clone()), (false, vec!["skill:a".to_string()]));
        assert_eq!(dev.rules, vec![json!({"decision": "block", "program": ["npm", "publish"], "words": ""})]);
    }

    #[test]
    fn parents_come_first_whatever_the_file_order() {
        let t = "name='t'\n[[role]]\nname='b'\nagent='claude'\nparent='a'\n[[role]]\nname='a'\nagent='claude'\n";
        let d = folder(t, &[("a.md", "A"), ("b.md", "B")]);
        let names: Vec<_> = load(d.path(), false).unwrap().roles.into_iter().map(|r| r.name).collect();
        assert_eq!(names, ["a", "b"]);
    }

    #[test]
    fn says_what_is_wrong_in_words() {
        let one = |extra: &str| format!("name='t'\n[[role]]\nname='a'\nagent='claude'\n{extra}");
        let empty = tempfile::tempdir().unwrap();
        assert!(load(empty.path(), true).unwrap_err().0.starts_with("no team.toml in "));
        assert!(err("name = ", &[]).contains("team.toml"));
        assert!(err("[[role]]\nname='a'", &[]).ends_with("the team needs a name"));
        assert!(err("name='t'", &[]).ends_with("the team has no roles ([[role]] tables)"));
        assert!(err("name='t'\n[[role]]\nagent='claude'", &[]).ends_with("every role needs a name"));
        assert!(err(&one("").replace("claude", "gpt"), &[]).ends_with("role 'a' has agent 'gpt'; use one of claude, codex, gemini, opencode, cursor, antigravity"));
        assert!(err(&one("checkout='x'"), &[]).ends_with("role 'a' has checkout 'x'; use one of branch, read-only"));
        assert!(err(&one(""), &[]).contains("role 'a': brief file not found: "));
        assert!(err(&one("effort='x'"), &[("a.md", "A")]).ends_with("role 'a': Claude Code has no thinking effort \"x\"."));
        assert!(err(&one("level='root'"), &[("a.md", "A")]).ends_with("role 'a': unknown permission level 'root'"));
        assert!(err(&one("parent='z'"), &[("a.md", "A")]).ends_with("role 'a': parent 'z' is not a role in this team"));
        let dup = format!("{}[[role]]\nname='a'\nagent='claude'\n", one(""));
        assert!(err(&dup, &[("a.md", "A")]).ends_with("duplicate role name 'a'"));
        let looped = "name='t'\n[[role]]\nname='a'\nagent='claude'\nparent='b'\n[[role]]\nname='b'\nagent='claude'\nparent='a'\n";
        assert!(err(looped, &[("a.md", "A"), ("b.md", "B")]).starts_with("roles form a parent loop: "));
    }

    #[test]
    fn an_unknown_practice_is_named() {
        let t = "name='t'\n[practices]\nzzz=true\naaa=false\n[[role]]\nname='a'\nagent='claude'\n";
        assert!(err(t, &[("a.md", "A")]).ends_with("unknown practice aaa, zzz"));
    }

    #[test]
    fn practices_add_their_rules_to_every_brief_until_turned_off() {
        let t = "name='t'\n[[role]]\nname='a'\nagent='claude'\n";
        let d = folder(t, &[("a.md", "Do it.\n\n")]);
        let plain = load(d.path(), false).unwrap().roles[0].brief.clone();
        let full = load(d.path(), true).unwrap().roles[0].brief.clone();
        assert_eq!(plain, "Do it.\n\n");
        assert!(full.starts_with("Do it.\n\n## Team practices\n\n- "), "{full}");
        let id = id_of(&arr(&practices(None), "groups").next().unwrap()["rules"][0]).to_string();
        let mut chosen = Map::new();
        assert!(rule_on(&chosen, &id));
        chosen.insert(id.clone(), json!(false));
        assert!(!rule_on(&chosen, &id));
        let group = id_of(arr(&practices(None), "groups").next().unwrap()).to_string();
        chosen.remove(&id);
        chosen.insert(group, json!(false));
        assert!(!rule_on(&chosen, &id), "a rule is off when its group is");
    }

    #[test]
    fn keys_hold_groups_rules_and_bigger_team_ids() {
        let k = keys();
        let p = practices(None);
        assert!(k.contains(id_of(&p["groups"][0])) && k.contains(id_of(&p["groups"][0]["rules"][0])));
        assert!(k.contains("steward") && k.contains("research-pairs"));
    }

    #[test]
    fn a_steward_and_research_critics_are_added_when_chosen() {
        let t = "name='t'\n[practices]\nsteward=true\nresearch-pairs=true\n\
                 [[role]]\nname='lead'\nagent='claude'\nmodel='opus'\n\
                 [[role]]\nname='researcher'\nagent='codex'\nparent='lead'\ngroup='g'\n";
        let d = folder(t, &[("lead.md", "L"), ("researcher.md", "R")]);
        let tm = load(d.path(), true).unwrap();
        let names: Vec<_> = tm.roles.iter().map(|r| r.name.as_str()).collect();
        assert_eq!(names, ["lead", "researcher", "researcher-critic", "steward"]);
        let critic = &tm.roles[2];
        assert_eq!((critic.parent.as_deref(), critic.checkout.as_str(), critic.agent.as_str(), critic.group.as_deref()), (Some("researcher"), "read-only", "codex", Some("g")));
        let steward = &tm.roles[3];
        assert_eq!((steward.parent.as_deref(), steward.agent.as_str(), steward.model.as_deref()), (Some("lead"), "claude", Some("opus")));
        assert!(tm.roles[0].brief.contains("send it to steward with message_agent first"));
        assert!(tm.roles[1].brief.contains("send it with its sources to researcher-critic"));
        assert!(tm.roles[2].brief.contains("send it to steward"), "the critic is told too");
        assert!(!load(d.path(), false).unwrap().roles.iter().any(|r| r.name == "steward"));
    }

    #[test]
    fn the_built_in_teams_load() {
        for id in ["one-agent", "builder-reviewer", "full"] {
            let dir = format!("{}/../../../assieme/presets/{id}", env!("CARGO_MANIFEST_DIR"));
            let tm = load(Path::new(&dir), true).unwrap_or_else(|e| panic!("{id}: {e}"));
            assert!(!tm.roles.is_empty());
        }
    }
}
