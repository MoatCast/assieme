//! Port of `assieme/preview.py`, owned by session E (see OWNERS.md).
//!
//! Live preview: the project's dev server, run in one agent's worktree on a port of its own, so the person can look at
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

#[derive(Debug, Clone, PartialEq)]
pub struct PreviewError(pub String);

impl From<PreviewError> for BrainError {
    fn from(e: PreviewError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// The run script previews, one per agent.
pub struct Previews {}

impl Previews {
    pub fn new(reviews: Arc<Review>) -> Previews {
        todo!("preview.Previews.new")
    }

    pub fn commands(&self) -> BTreeMap<String, String> {
        todo!("preview.Previews.commands")
    }

    pub fn remember(&self, project: &str, command: &str) {
        todo!("preview.Previews.remember")
    }

    pub fn stop_left_behind(&self) {
        todo!("preview.Previews.stop_left_behind")
    }

    pub fn start(
        &self,
        team: &str,
        role: &str,
        command: Option<&str>,
        install: bool,
        confirm: Option<&str>,
    ) -> Result<Value, PreviewError> {
        todo!("preview.Previews.start")
    }

    pub fn stop(&self, team: &str, role: &str) -> Value {
        todo!("preview.Previews.stop")
    }

    pub fn sweep(&self) {
        todo!("preview.Previews.sweep")
    }

    pub fn list(&self) -> Vec<Value> {
        todo!("preview.Previews.list")
    }

    pub fn handlers(this: Arc<Previews>) -> Handlers {
        todo!("preview.Previews.handlers")
    }
}
