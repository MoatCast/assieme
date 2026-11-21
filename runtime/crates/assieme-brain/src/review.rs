//! Port of `assieme/review.py`, owned by session E (see OWNERS.md).
//!
//! Review and merge: each agent's branch, its diff and checks, and what the person decides about it.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

#[derive(Debug, Clone, PartialEq)]
pub struct ReviewError {
    pub message: String,
    pub detail: String,
}

impl From<ReviewError> for BrainError {
    fn from(e: ReviewError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

/// Review: each agent's branch, its checks, merge, undo and discard.
pub struct Review {}

impl Review {
    pub fn new(rt: Runtime, teams: PathBuf) -> Review {
        todo!("review.Review.new")
    }

    pub fn worktree(&self, team: &str, role: &str) -> Option<String> {
        todo!("review.Review.worktree")
    }

    pub fn team_live(&self, team: &str) -> bool {
        todo!("review.Review.team_live")
    }

    pub fn waiting(&self) -> Vec<Value> {
        todo!("review.Review.waiting")
    }

    pub fn get(&self, team: &str, role: &str, retest: bool) -> Result<Value, ReviewError> {
        todo!("review.Review.get")
    }

    pub fn card(&self, team: &str, role: &str) -> Option<Value> {
        todo!("review.Review.card")
    }

    pub fn merge(&self, team: &str, role: &str, tip: &str, force: bool) -> Result<Value, ReviewError> {
        todo!("review.Review.merge")
    }

    pub fn undo(&self, id: &str) -> Result<Value, ReviewError> {
        todo!("review.Review.undo")
    }

    pub fn discard(&self, team: &str, role: &str, tip: &str) -> Result<Value, ReviewError> {
        todo!("review.Review.discard")
    }

    pub fn pull_request(&self, team: &str, role: &str) -> Result<Value, ReviewError> {
        todo!("review.Review.pull_request")
    }

    pub fn ask(&self, team: &str, role: &str, text: &str) -> Result<Value, ReviewError> {
        todo!("review.Review.ask")
    }

    pub fn note(&self, team: &str, role: &str, note: &str, action: &str) -> Result<Value, ReviewError> {
        todo!("review.Review.note")
    }

    pub fn open_editor(&self, team: &str, role: &str, file: &str, line: Option<i64>) -> Result<Value, ReviewError> {
        todo!("review.Review.open_editor")
    }

    /// What a transcript said, read once per file version.
    pub fn read_once<T: Clone>(&self, path: &Path, read: fn(&Path) -> T) -> T {
        todo!("review.Review._read_once")
    }

    pub fn handlers(this: Arc<Review>) -> Handlers {
        todo!("review.Review.handlers")
    }
}

/// What Claude Code said in this transcript.
pub fn claude_said(path: &Path) -> Vec<String> {
    todo!("review._claude_said")
}

/// (its folder, what Codex said) in this transcript.
pub fn codex_said(path: &Path) -> (Option<String>, Vec<String>) {
    todo!("review._codex_said")
}

/// The project's test command; None when there isn't one we can run.
pub fn test_command(path: &Path) -> Option<Vec<String>> {
    todo!("review.test_command")
}
