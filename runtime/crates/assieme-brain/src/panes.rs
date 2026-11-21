//! Port of `assieme/panes.py`, owned by session B (see OWNERS.md).
//!
//! The Terminals view: one pane per Assieme agent, with its state, the needs-you strip, and the buttons that act.
#[allow(unused_imports)]
use crate::prelude::*;

pub const ANSWERED: &str = "You answered its question.";
