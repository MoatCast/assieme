//! Port of `assieme/worktree.py`, owned by session D (see OWNERS.md).
//!
//! One git worktree per role, under <project>/.assieme/worktrees/<role>. Uses the git command.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Git options that run no hook or watcher a container agent could have planted.
pub const SAFE: &[&str] = &["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"];

#[derive(Debug, Clone, PartialEq)]
pub struct WorktreeError {
    pub message: String,
    pub detail: String,
}

impl From<WorktreeError> for BrainError {
    fn from(e: WorktreeError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

/// `git -C cwd args`, with SAFE in a container agent's clone.
pub fn git(cwd: &str, args: &[&str]) -> std::io::Result<std::process::Output> {
    todo!("worktree._git")
}

/// A container agent's copy: a repository of its own at <project>/.assieme/worktrees/<role>.
pub fn is_clone(path: &str) -> bool {
    todo!("worktree.is_clone")
}

/// Fetch each container agent's branch from its clone into the project.
pub fn sync(project: &str) {
    todo!("worktree.sync")
}

/// The tool a brought-over folder belongs to, from where it is; None when the path doesn't say.
pub fn tool_name(folder: &str) -> Option<&'static str> {
    todo!("worktree.tool_name")
}
