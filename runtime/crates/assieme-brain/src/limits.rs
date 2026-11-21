//! Port of `assieme/limits.py`, owned by session A (see OWNERS.md).
//!
//! Plan limits, read from each agent kind's own logs: how much of the plan's window is used, and when it comes back.
//! Limits are per account, not per agent: every agent of one kind shares one entry, and the latest log line wins.
//! Only the limit fields are read, never message text.
//! - Codex writes `rate_limits` (primary and secondary windows, `used_percent`, `resets_at`) on `token_count` events.
//! - Claude Code writes no running percentage to its transcript, so a Claude entry exists only while a limit is
//!   reached, from `rate_limit_info` (`status`, `resetsAt`, `rateLimitType`).
use crate::adapters::{mtime_or_zero, seconds};
use crate::prelude::*;
use crate::pyrules;
use crate::status::tail_rows;
use serde_json::json;

const NEWEST: usize = 5; // logs per kind read: the account's latest state is in the newest ones
const TAIL: u64 = 128 * 1024;

fn newest(mut paths: Vec<PathBuf>) -> Vec<PathBuf> {
    paths.sort_by(|a, b| mtime_or_zero(b).total_cmp(&mtime_or_zero(a))); // stable: equal times keep their order
    paths.truncate(NEWEST);
    paths
}

/// (seconds since 1970, row) for every timed row in the tails of these logs, oldest first.
fn timed_rows(paths: &[PathBuf]) -> Vec<(f64, Map<String, Value>)> {
    let mut out = vec![];
    for p in paths {
        let Ok(rows) = tail_rows(p, TAIL) else { continue }; // a folder, or removed since it was listed
        for o in rows {
            if let Some(ts) = o.get("timestamp").and_then(crate::model::parse_ts) {
                out.push((seconds(ts), o));
            }
        }
    }
    out.sort_by(|a, b| a.0.total_cmp(&b.0));
    out
}

/// A number as JSON: whole numbers Python kept as int stay int.
fn number(v: &Value) -> Value {
    match v {
        Value::Number(_) => v.clone(),
        other => json!(pyrules::float(other)),
    }
}

fn codex(paths: &[PathBuf], now: f64) -> Option<Value> {
    let mut latest = None;
    for (t, o) in timed_rows(paths) {
        if let Some(Value::Object(p)) = o.get("payload")
            && p.get("type").and_then(Value::as_str) == Some("token_count")
            && let Some(Value::Object(rl)) = p.get("rate_limits")
        {
            latest = Some((t, rl.clone()));
        }
    }
    let (t, rl) = latest?;
    let mut windows: Vec<(f64, Option<Value>)> = vec![];
    for w in [rl.get("primary"), rl.get("secondary")] {
        let Some(Value::Object(w)) = w else { continue };
        let Some(used) = w.get("used_percent").filter(|v| pyrules::is_number(v) || v.is_boolean()) else { continue };
        let used = if let Value::Bool(b) = used { *b as i64 as f64 } else { pyrules::float(used)? };
        let at = |k: &str| w.get(k).filter(|v| pyrules::is_number(v) || v.is_boolean());
        let flag = |v: &Value| if let Value::Bool(b) = v { json!(*b as i64) } else { number(v) };
        // missing or malformed: an unknown reset time, not a crash; older Codex gives resets_in_seconds
        let mut resets = at("resets_at").map(flag);
        if resets.is_none()
            && let Some(r) = at("resets_in_seconds")
        {
            resets = Some(json!(t + pyrules::float(&flag(r)).unwrap_or(0.0)));
        }
        if resets.as_ref().and_then(pyrules::float).is_some_and(|r| r <= now) {
            continue; // that window has started over: its old use no longer counts
        }
        windows.push((used, resets));
    }
    // max(..., key=used): the first of equal ones, and NaN never replaces what came before
    let (mut used, mut resets) = windows.first().cloned()?;
    for (u, r) in windows.into_iter().skip(1) {
        if u > used {
            (used, resets) = (u, r);
        }
    }
    let shown = if 100.0 < used { 100.0 } else { used }; // min(used, 100.0): NaN stays
    Some(json!({"used": if shown.is_nan() { Value::Null } else { json!(shown) }, "reached": used >= 100.0,
                "resets_at": resets}))
}

fn claude(paths: &[PathBuf], now: f64) -> Option<Value> {
    let mut latest = None;
    for (_, o) in timed_rows(paths) {
        if let Some(Value::Object(info)) = o.get("rate_limit_info") {
            latest = Some(info.clone());
        }
    }
    let latest = latest?;
    if latest.get("status").and_then(Value::as_str) != Some("rejected") {
        return None;
    }
    let resets = latest.get("resetsAt").filter(|v| pyrules::is_number(v) || v.is_boolean());
    let resets = match resets? {
        Value::Bool(b) => *b as i64 as f64,
        v => pyrules::float(v)?,
    };
    if resets <= now {
        return None; // past its reset: back to work, even before a new line arrives
    }
    Some(json!({"used": null, "reached": true, "resets_at": resets}))
}

/// `{kind: {used, reached, resets_at}}`; a kind with no data is left out. `used` is a percentage (None when the log
/// doesn't say), `resets_at` seconds since 1970 or None.
pub fn read(claude_dir: &Path, codex_dir: &Path, now: Timestamp) -> Result<Value, BrainError> {
    let now = seconds(now);
    let mut out = Map::new();
    if let Some(c) = claude(&newest(pyrules::glob(&claude_dir.join("*/*.jsonl"))), now) {
        out.insert("claude".into(), c);
    }
    if let Some(c) = codex(&newest(crate::adapters::glob_deep(codex_dir, ".jsonl")), now) {
        out.insert("codex".into(), c);
    }
    Ok(Value::Object(out))
}
