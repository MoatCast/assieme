//! Port of `assieme/newteam.py`, owned by session D (see OWNERS.md).
//!
//! New team in the app: the starting teams, checks, saving a team folder, starting it in the background, and
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

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

/// A New team problem, tied to the form field it is about.
#[derive(Debug, Clone, PartialEq)]
pub struct NewTeamError {
    pub field: String,
    pub message: String,
    pub detail: String,
}

impl From<NewTeamError> for BrainError {
    fn from(e: NewTeamError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

/// The built-in team presets, compiled into the binary.
pub static PRESETS_DIR: LazyLock<PathBuf> = LazyLock::new(|| todo!("newteam.PRESETS_DIR"));

/// Starts teams in the background and remembers the ones starting.
pub struct Starter {}

impl Starter {
    pub fn new(rt: Runtime, teams: PathBuf, scheduled: Box<dyn Fn() -> BTreeSet<String> + Send + Sync>) -> Starter {
        todo!("newteam.Starter.new")
    }

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
        todo!("newteam.Starter.start")
    }

    pub fn add_agent(&self, team_name: &str, role: &Value) -> Result<Value, NewTeamError> {
        todo!("newteam.Starter.add_agent")
    }

    pub fn forget(&self, team_name: &str, role: &str) {
        todo!("newteam.Starter.forget")
    }

    pub fn records(&self) -> Vec<Value> {
        todo!("newteam.Starter.records")
    }
}

/// A tool id's pattern.
pub const TOOL_ID: &str = r"^(server|plugin|skill|extension):[^\x00-\x1f\x7f]{1,200}$";

/// Kind -> what it does, in words.
pub const KIND_JOBS: &[(&str, &str)] = &[
    ("planner", "reads the project, splits the task and hands out pieces; never edits"),
    ("builder", "makes one change at a time with tests, on its own branch"),
    ("reviewer", "reads every diff and gives a verdict; never edits"),
    ("tester", "writes tests and runs the full suite, on its own branch"),
];

/// A saved team folder as New team's preset.
pub fn as_preset(folder: &Path, id: &str, label: Option<&str>) -> Result<Value, BrainError> {
    todo!("newteam._as_preset")
}

/// Save the team again with one role's fields changed. `changes` holds the fields by their Python names.
pub fn rewrite_role(folder: &Path, name: &str, changes: &Map<String, Value>) -> Result<(), BrainError> {
    todo!("newteam._rewrite_role")
}

pub fn role_dict(r: &Role) -> Value {
    todo!("newteam._role_dict")
}

/// Step 31: each role's instrument and colours. {} for a team with no saved folder.
pub fn band(name: &str, teams: Option<&Path>) -> Value {
    todo!("newteam.band")
}

pub fn clear_worktree(folder: &Path, name: &str) -> Result<(), BrainError> {
    todo!("newteam.clear_worktree")
}

/// Take a role back out of a saved team.
pub fn forget_role(folder: &Path, name: &str) -> Result<(), BrainError> {
    todo!("newteam.forget_role")
}

pub fn improve_brief(project: &str, role: &Value) -> Result<Value, NewTeamError> {
    todo!("newteam.improve_brief")
}

pub fn kind_brief(kind: &str) -> String {
    todo!("newteam.kind_brief")
}

/// The models and thinking efforts New team offers.
pub fn model_options() -> Value {
    todo!("newteam.model_options")
}

pub fn presets(teams: Option<&Path>) -> Value {
    todo!("newteam.presets")
}

pub fn project_check(path: &str) -> Value {
    todo!("newteam.project_check")
}

/// {role: {job, group, parent}}; {} for a team started from the command line.
pub fn roster(name: &str, teams: Option<&Path>) -> BTreeMap<String, Value> {
    todo!("newteam.roster")
}

pub fn rule_draft(project: &str, words: &str) -> Result<Value, NewTeamError> {
    todo!("newteam.rule_draft")
}

pub fn set_musician(name: &str, role: &str, musician: &str, teams: Option<&Path>) -> Result<Value, NewTeamError> {
    todo!("newteam.set_musician")
}

/// `size`, `review` and `budget` are New team's choices as the app sends them.
pub fn suggest(
    project: &str,
    size: &Value,
    review: &Value,
    budget: &Value,
    task: &str,
    models: Option<&Value>,
) -> Result<Value, NewTeamError> {
    todo!("newteam.suggest")
}

/// A free team name for this project.
pub fn team_name(project: &str, taken: &BTreeSet<String>) -> String {
    todo!("newteam.team_name")
}

pub fn teams_dir() -> PathBuf {
    todo!("newteam.teams_dir")
}

pub fn validate(roles: &[Value]) -> Result<(), NewTeamError> {
    todo!("newteam.validate")
}
