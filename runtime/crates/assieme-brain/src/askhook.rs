//! Step 18a: Claude Code's PreToolUse hook in the Assieme plugin. It answers "ask" for every Assieme tool that changes
//! something, so Claude Code shows its own Allow prompt, which reaches the phone (Assieme's own question doesn't). The
//! server started by the plugin takes the call as the person's yes (`actions::CLIENT_ASKS`). Reads pass untouched.
//! In a mode where Claude Code may approve without the person, a change is refused instead.
use crate::prelude::*;
use serde_json::json;
use std::io::Read;

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
// shortcut: unverified whether these modes show an "ask" to the person; refuse, until checked on a real session.
const NO_PERSON: [&str; 3] = ["bypassPermissions", "dontAsk", "auto"];

/// The hook's answer to one event, or None when the tool only reads.
pub fn answer(input: &[u8]) -> Option<Value> {
    // can't tell what it is: ask
    let event = serde_json::from_slice::<Value>(input).ok().filter(Value::is_object).unwrap_or_default();
    let asks = match event.get("tool_name") {
        None | Some(Value::Null) if event.is_object() => false,
        Some(Value::String(t)) => {
            let name = t.rsplit("__").next().unwrap_or("");
            CHANGE_TOOLS.contains(&name) || name == "app_action" // app_action runs changes only
        }
        _ => true,
    };
    let refuse = event["permission_mode"].as_str().is_some_and(|m| NO_PERSON.contains(&m));
    let (decision, why) = if refuse {
        (
            "deny",
            "Assieme changes need your own yes, and this session’s permission mode can approve without asking you. Switch to the default mode to make it.",
        )
    } else {
        ("ask", "Assieme asks before changing anything.")
    };
    asks.then(|| json!({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": why}}))
}

pub fn main() -> i32 {
    let mut input = vec![];
    let _ = std::io::stdin().read_to_end(&mut input);
    if let Some(reply) = answer(&input) {
        println!("{reply}");
    }
    0
}
