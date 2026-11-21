//! Port of `assieme/process.py`, owned by session A (see OWNERS.md).
//!
//! F8 process weight: merged items, how long each took from first commit to merge, and which gate or rule cost the most.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The gate or rule that cost the most hours, or None.
pub fn gate_finding(log_gates: &BTreeMap<String, f64>, check_gates: &BTreeMap<String, f64>) -> Option<Value> {
    todo!("process.gate_finding")
}

/// Merged items in the window across these folders' repositories; `gh`: run `gh` too.
pub fn report(cwds: &[String], since: Timestamp, now: Timestamp, gh: bool) -> Value {
    todo!("process.report")
}

/// `gh` with these arguments in `cwd`; its standard output.
pub fn run_gh(cmd: &[String], cwd: &str) -> Result<String, BrainError> {
    todo!("process.run_gh")
}
