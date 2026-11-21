//! Port of `assieme/detect.py`, owned by session A (see OWNERS.md).
//!
//! F5 context meter additions, F6 heavy-job guard, F7 retry loops and F10 paid compute: reading commands, and the checks.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// F8: hours spent per heavy command and per refusal source.
pub fn gate_hours(sessions: &[Session]) -> BTreeMap<String, f64> {
    todo!("detect.gate_hours")
}

/// What `status` and the fix cards show: only what is happening now.
pub fn live_findings(agents: &[Agent], now: Timestamp) -> Vec<Value> {
    todo!("detect.live_findings")
}
