//! Port of `assieme/scripts.py`, owned by session D (see OWNERS.md).
//!
//! Setup, run and archive scripts a team brought over from another tool (step 30).
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The person said yes to running this exact line for this team. `which`: setup, run or archive.
pub fn allow(team: &str, which: &str, line: &str) -> Result<(), BrainError> {
    todo!("scripts.allow")
}

pub fn allowed(team: &str, which: &str, line: &str) -> bool {
    todo!("scripts.allowed")
}

/// What a brought-over script expects beside Assieme's own.
pub fn env(project: &str) -> BTreeMap<String, String> {
    todo!("scripts.env")
}

/// (line, unknown): Conductor's variables as Assieme's.
pub fn rewrite(line: &str) -> (String, Vec<String>) {
    todo!("scripts.rewrite")
}

/// `{ok, output}`: the line, rewritten, run with /bin/sh in `folder`.
pub fn run(line: &str, folder: &str, project: &str, port: Option<u16>, timeout: f64) -> Value {
    todo!("scripts.run")
}
