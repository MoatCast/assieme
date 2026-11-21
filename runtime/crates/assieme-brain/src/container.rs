//! Port of `assieme/container.py`, owned by session D (see OWNERS.md).
//!
//! An agent in a container that sees only its own copy of the project, with Docker.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

#[derive(Debug, Clone, PartialEq)]
pub struct ContainerError(pub String);

impl From<ContainerError> for BrainError {
    fn from(e: ContainerError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// The shell command that builds this kind's image.
pub fn build_command(kind: &str) -> Result<String, ContainerError> {
    todo!("container.build_command")
}

/// `{tool, running, image_ready}`.
pub fn state(kind: &str) -> Value {
    todo!("container.state")
}
