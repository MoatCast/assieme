//! Port of `assieme/report.py`, owned by session A (see OWNERS.md).
//!
//! Sessions -> per-agent totals, the wake graph, refusals and flags; rendered as Markdown or JSON.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

pub const NOISY_LAST_HOUR: i64 = 10;
pub const REPEATED_REFUSALS: i64 = 3;

/// now/hours describe the window; with them, edges also get hourly rates and a last-hour count.
pub fn aggregate(
    sessions: &[Session],
    now: Option<Timestamp>,
    hours: Option<f64>,
    boxes: &[Value],
    machine: &[Value],
) -> Value {
    todo!("report.aggregate")
}

pub fn to_json(r: &Value, meta: &Value) -> String {
    todo!("report.to_json")
}

pub fn to_md(r: &Value, meta: &Value, show_text: bool) -> String {
    todo!("report.to_md")
}
