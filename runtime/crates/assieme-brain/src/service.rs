//! Port of `assieme/service.py`, owned by the foundation (see OWNERS.md).
//!
//! The brain as a launchd user agent (macOS): `assieme service install|uninstall|status`.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Install and start the brain's login service. Returns the job file.
pub fn install() -> Result<PathBuf, BrainError> {
    todo!("service.install")
}

pub fn running() -> bool {
    todo!("service.running")
}

pub fn uninstall() {
    todo!("service.uninstall")
}
