//! Port of `assieme/reports.py`, owned by session A (see OWNERS.md).
//!
//! What agents say about themselves through the MCP server: stuck, or needing a decision. One report per team and
//! role, in a small file so it survives a brain restart. A report lasts until the agent's next turn (the core counts
//! turns), or a new one replaces it. A teammate's note isn't the person's answer, so a turn it wakes doesn't count.
use crate::prelude::*;
use crate::quiet;
use serde_json::json;

/// What the panes show.
pub const SHOWN: &[&str] = &["stuck", "need_decision"];
/// What an agent may report: `done` and `working` clear an earlier report.
pub const STATES: &[&str] = &["stuck", "need_decision", "done", "working"];

/// The brain serves each call on its own thread.
static WRITE: Mutex<()> = Mutex::new(());

fn file() -> PathBuf {
    quiet::state_dir().join("reports.json")
}

fn all() -> Map<String, Value> {
    match quiet::read_json(&file(), json!({})) {
        Value::Object(o) => o,
        _ => Map::new(),
    }
}

pub fn save(
    team: &str,
    role: &str,
    state: &str,
    note: &str,
    now: Option<Timestamp>,
    turn: Option<i64>,
) -> Result<(), BrainError> {
    if !STATES.contains(&state) {
        return Err(BrainError::new(format!("report one of: {}.", STATES.join(", "))));
    }
    let note = note.trim_matches(crate::pyrules::isspace);
    if SHOWN.contains(&state) && note.is_empty() {
        return Err(BrainError::new("Say in one line what is wrong or what needs deciding."));
    }
    let _held = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    let mut reports = all();
    let key = format!("{team}/{role}");
    let had = reports.remove(&key).is_some_and(|r| crate::pyrules::truthy(&r));
    if SHOWN.contains(&state) {
        let ms = crate::adapters::seconds(now.unwrap_or_else(Timestamp::now)) * 1000.0;
        let note: String = note.chars().take(300).collect();
        reports.insert(key, json!({"state": state, "note": note, "ms": ms, "turn": turn, "noted": 0}));
    }
    if had || SHOWN.contains(&state) {
        // the panes clear on every poll: only write when something changed
        quiet::write_json(&file(), &Value::Object(reports)).map_err(|e| BrainError::new(e.to_string()))?;
    }
    Ok(())
}

pub fn clear(team: &str, role: &str) {
    let _ = save(team, role, "done", "", None, None);
}

/// The turn in progress or just ended. The core counts a turn when it ends, so one under way is one more.
pub fn running_turn(row: &Value) -> Option<i64> {
    let turn = row.get("turn").and_then(Value::as_i64)?;
    let working = row.get("status").and_then(|s| s.get("state")).and_then(Value::as_str) == Some("working");
    Some(turn + working as i64)
}

/// A teammate's note is on its way to this agent: the turn it wakes leaves the report standing.
pub fn noted(team: &str, role: &str) {
    let _held = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    let mut reports = all();
    if let Some(Value::Object(rep)) = reports.get_mut(&format!("{team}/{role}")) {
        let n = rep.get("noted").and_then(Value::as_i64).unwrap_or(0);
        rep.insert("noted".into(), json!(n + 1));
        let _ = quiet::write_json(&file(), &Value::Object(reports));
    }
}

/// The report that still holds for this terminal row, or None. One that no longer holds is removed: Settings says
/// an agent's words are kept only until its next turn.
pub fn current(team: &str, role: &str, row: &Value) -> Option<Value> {
    let rep = all().remove(&format!("{team}/{role}"))?;
    let num = |v: Option<&Value>| v.and_then(Value::as_f64).unwrap_or(0.0);
    let stale = match &rep {
        Value::Object(r) => {
            let turn = running_turn(row);
            let reported = r.get("turn").and_then(Value::as_i64);
            !r.get("note").is_some_and(crate::pyrules::truthy)
                || num(row.get("created_ms")) > num(r.get("ms"))
                || matches!((turn, reported), (Some(t), Some(at)) if t > at + num(r.get("noted")) as i64)
        }
        _ => true,
    };
    if stale {
        clear(team, role);
        return None;
    }
    Some(rep)
}
