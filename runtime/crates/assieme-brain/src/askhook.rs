//! Port of `assieme/askhook.py`, owned by session G (see OWNERS.md).
//!
//! Step 18a: Claude Code's PreToolUse hook in the Assieme plugin. It answers "ask" for every Assieme tool that changes
#[allow(unused_imports)]
use crate::prelude::*;

pub const CHANGE_TOOLS: &[&str] = &[
    "answer",
    "send_message",
    "nudge",
    "compact",
    "undo",
    "start_team",
    "add_agent",
    "stop_agent",
    "stop_team",
    "start_draft",
];
