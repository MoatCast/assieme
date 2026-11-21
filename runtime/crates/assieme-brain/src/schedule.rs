//! Port of `assieme/schedule.py`, owned by session F (see OWNERS.md).
//!
//! Scheduled runs: a small team that starts on its own at set times, works on its own branches and stops when done.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const ENDS_NOW: &str =
    "Reported. This scheduled run ends now: its agents stop and the person reviews the branches later.";

#[derive(Debug, Clone, PartialEq)]
pub struct ScheduleError(pub String);

impl From<ScheduleError> for BrainError {
    fn from(e: ScheduleError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// Starts a saved team: `(project, task, roles, practices, name)` as `Starter::start`.
pub type StartTeam = dyn Fn(&str, &str, &[Value], &Value, Option<&str>) -> Result<Value, BrainError> + Send + Sync;

/// Scheduled runs.
pub struct Scheduler {}

impl Scheduler {
    pub fn new(
        rt: Runtime,
        start: Box<StartTeam>,
        starting: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
        announce: Option<Box<Notify>>,
    ) -> Scheduler {
        todo!("schedule.Scheduler.new")
    }

    pub fn tick(&self) {
        todo!("schedule.Scheduler.tick")
    }

    pub fn done(&self, team: &str, role: &str) -> Option<String> {
        todo!("schedule.Scheduler.done")
    }

    pub fn save(
        &self,
        name: &str,
        project: &str,
        preset: &str,
        task: &str,
        every: &Value,
        id: Option<&str>,
    ) -> Result<Value, ScheduleError> {
        todo!("schedule.Scheduler.save")
    }

    pub fn pause(&self, id: &str, paused: bool) -> Result<Value, ScheduleError> {
        todo!("schedule.Scheduler.pause")
    }

    pub fn stop(&self, id: &str) -> Result<Value, ScheduleError> {
        todo!("schedule.Scheduler.stop")
    }

    pub fn run_now(&self, id: &str) -> Result<Value, ScheduleError> {
        todo!("schedule.Scheduler.run_now")
    }

    pub fn remove(&self, id: &str) -> Result<Value, ScheduleError> {
        todo!("schedule.Scheduler.remove")
    }

    pub fn run_teams(&self) -> Vec<String> {
        todo!("schedule.Scheduler.run_teams")
    }

    pub fn rows(&self) -> Vec<Value> {
        todo!("schedule.Scheduler.rows")
    }

    pub fn handlers(this: Arc<Scheduler>) -> Handlers {
        todo!("schedule.Scheduler.handlers")
    }
}

/// The saved schedules.
pub fn load() -> Vec<Value> {
    todo!("schedule._load")
}
