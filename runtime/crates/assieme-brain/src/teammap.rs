//! Port of `assieme/teammap.py`, owned by session B (see OWNERS.md).
//!
//! The Team tab: who wakes whom, what needs you, what could be saved and today's spend.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The window's data: teams, agents, their states and the fixes.
pub struct Brain {}

impl Brain {
    pub fn new(
        claude_dir: PathBuf,
        codex_dir: PathBuf,
        rt: Runtime,
        starter: Arc<Starter>,
        answers: Arc<Answers>,
    ) -> Brain {
        todo!("teammap.Brain.new")
    }

    pub fn terminals(&self) -> Vec<Value> {
        todo!("teammap.Brain.terminals")
    }

    pub fn limits(&self, now: Timestamp) -> Value {
        todo!("teammap.Brain.limits")
    }

    pub fn map(&self, team: &str) -> Result<Value, BrainError> {
        todo!("teammap.Brain.map")
    }

    pub fn view(&self, team: &str) -> Result<Value, BrainError> {
        todo!("teammap.Brain.view")
    }

    pub fn act(&self, team: &str, role: &str, fix: &str, button: &str, by: &str) -> Result<Value, BrainError> {
        todo!("teammap.Brain.act")
    }

    pub fn detail(&self, team: &str, role: &str) -> Result<Value, BrainError> {
        todo!("teammap.Brain.detail")
    }

    pub fn tip(&self, team: &str, tip: &str) -> Result<Value, BrainError> {
        todo!("teammap.Brain.tip")
    }

    pub fn stop(&self, team_name: &str) -> Result<Value, BrainError> {
        todo!("teammap.Brain.stop")
    }

    pub fn teams(&self) -> Value {
        todo!("teammap.Brain.teams")
    }

    pub fn change_setting(&self, key: &str, value: &Value) -> Result<Value, BrainError> {
        todo!("teammap.Brain.change_setting")
    }

    pub fn run_found(&self, team_name: &str, on: bool) -> Result<Value, BrainError> {
        todo!("teammap.Brain.run_found")
    }

    pub fn pairs(&self, team_name: Option<&str>) -> Value {
        todo!("teammap.Brain.pairs")
    }

    pub fn set_pair(&self, from_cwd: &str, to_cwd: &str, label: &str, on: bool) -> Result<Value, BrainError> {
        todo!("teammap.Brain.set_pair")
    }

    pub fn set_budget(&self, team: &str, limits: &Value, at_limit: &str) -> Result<Value, BrainError> {
        todo!("teammap.Brain.set_budget")
    }

    /// The loop's step: fixes, budgets, notifications. Returns what it logged.
    pub fn tick(&self, now: Option<Timestamp>, announce: Option<&Notify>) -> Vec<Value> {
        todo!("teammap.Brain.tick")
    }

    /// Today's token use, read at most once per tick.
    /// `(rows, agents, found)` of a team: `found` when it was started outside Assieme (`_resolve`, without its runtime).
    pub fn resolve(&self, team: &str, now: Timestamp) -> (Vec<Value>, Vec<Agent>, bool) {
        todo!("teammap.Brain._resolve")
    }

    pub fn today(&self) -> Value {
        todo!("teammap.Brain.today")
    }

    pub fn handlers(this: Arc<Brain>) -> Handlers {
        todo!("teammap.Brain.handlers")
    }
}

/// `report::aggregate` on this team's sessions, with agents and senders named by role.
pub fn team_report(rows: &[Value], today: &Value, now: Timestamp) -> Value {
    todo!("teammap._team_report")
}

/// Teams of agents started outside Assieme. Closed sessions don't make a team.
pub fn found_groups(agents: &[Agent], st_groups: &Map<String, Value>, taken_cwds: &BTreeSet<String>) -> Vec<Value> {
    todo!("teammap.found_groups")
}

/// The found team an agent belongs to.
pub fn group_of(a: &Agent, st_groups: &Map<String, Value>) -> String {
    todo!("teammap.group_of")
}

/// Suggested answers run the person's own agent in a folder of ours: that session isn't one of their agents.
pub fn is_own_session(a: &Agent) -> bool {
    todo!("teammap.is_own_session")
}

/// A pane waiting for the person: its state, or a pause after a fix.
pub fn needs_you(p: &Value) -> bool {
    todo!("teammap.needs_you")
}
