//! Port of `assieme/setup.py`, owned by session F (see OWNERS.md).
//!
//! First-run setup: Apple's developer tools, where the agents are installed, and whether they are signed in.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// A setup step failed, in words.
#[derive(Debug, Clone, PartialEq)]
pub enum SetupError {
    Failed(String),
    TooLarge(TooLarge),
    Unfinished(Unfinished),
}

/// The folder is too large to save as it is.
#[derive(Debug, Clone, PartialEq)]
pub struct TooLarge(pub String);

/// Saving the folder stopped part way.
#[derive(Debug, Clone, PartialEq)]
pub struct Unfinished(pub String);

impl From<SetupError> for BrainError {
    fn from(e: SetupError) -> BrainError {
        match e {
            SetupError::Failed(m) | SetupError::TooLarge(TooLarge(m)) | SetupError::Unfinished(Unfinished(m)) => {
                BrainError::new(m)
            }
        }
    }
}

/// Where an agent program is installed, or None: this process's PATH, the login shell's, then ~/.local/bin.
pub fn agent_path(name: &str) -> Option<PathBuf> {
    todo!("setup.agent_path")
}

pub fn folder_preview(project: &str) -> Result<Value, SetupError> {
    todo!("setup.folder_preview")
}

/// Git works (Apple's stub is only asked about, never run).
pub fn git_ready() -> bool {
    todo!("setup.git_ready")
}

/// The `setup.*` methods.
pub fn handlers(rt: Runtime) -> Handlers {
    todo!("setup.handlers")
}

/// The home folder or `/`, by resolved path.
pub fn too_broad(project: &str) -> bool {
    todo!("setup.too_broad")
}

/// How many files Save them first would commit.
pub fn uncommitted(project: &str) -> Result<usize, SetupError> {
    todo!("setup.uncommitted")
}
