//! Port of `assieme/tasks.py`, owned by session C (see OWNERS.md).
//!
//! The task board: what a team is doing, as cards in To do, Doing, Review and Done.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// An agent's terminal address -> (team, role, its row).
pub type Me = dyn Fn(&str) -> Option<(String, String, Value)> + Send + Sync;

/// The team boards.
pub struct Board {}

impl Board {
    pub fn new(brain: Arc<Brain>, reviews: Arc<Review>, me: Box<Me>) -> Board {
        todo!("tasks.Board.new")
    }

    pub fn get(&self, team: &str) -> Result<Value, BrainError> {
        todo!("tasks.Board.get")
    }

    pub fn add(&self, team: &str, title: &str, role: Option<&str>) -> Result<Value, BrainError> {
        todo!("tasks.Board.add")
    }

    pub fn r#move(&self, team: &str, id: &str, status: &str) -> Result<Value, BrainError> {
        todo!("tasks.Board.move")
    }

    pub fn tasks(&self, address: &str) -> Result<Value, BrainError> {
        todo!("tasks.Board.tasks")
    }

    pub fn task_add(&self, address: &str, title: &str, assignee: &str) -> Result<Value, BrainError> {
        todo!("tasks.Board.task_add")
    }

    pub fn task_set(
        &self,
        address: &str,
        task: &str,
        status: &str,
        note: &str,
        waiting_for_person: bool,
    ) -> Result<Value, BrainError> {
        todo!("tasks.Board.task_set")
    }

    pub fn handlers(this: Arc<Board>) -> Handlers {
        todo!("tasks.Board.handlers")
    }
}

/// A new card.
pub fn add(team: &str, title: &str, role: Option<&str>, by: &str, now: Option<Timestamp>) -> Result<Value, BrainError> {
    todo!("tasks.add")
}

pub fn board(team: &str) -> Value {
    todo!("tasks.board")
}

pub fn discarded(team: &str, role: &str, now: Option<Timestamp>) {
    todo!("tasks.discarded")
}

/// A new team's board: empty, titled with the first sentence of its task.
pub fn fresh(team: &str, task: &str) {
    todo!("tasks.fresh")
}

/// Review merged this agent's branch: its cards in Doing and Review are done. `undo` names the merge.
pub fn merged(team: &str, role: &str, base: &str, undo: &str, now: Option<Timestamp>) {
    todo!("tasks.merged")
}

/// Set a card's status. `note` None keeps the note it has.
pub fn r#move(
    team: &str,
    id: &str,
    status: &str,
    note: Option<&str>,
    needs_you: bool,
    now: Option<Timestamp>,
) -> Result<Value, BrainError> {
    todo!("tasks.move")
}

pub fn unmerged(team: &str, undo: &str, now: Option<Timestamp>) {
    todo!("tasks.unmerged")
}
