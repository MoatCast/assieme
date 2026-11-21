//! Port of `assieme/settings.py`, owned by the foundation (see OWNERS.md).
//!
//! The app's settings: a small JSON file in the state folder. A damaged file gives the defaults, which are Automatic:
//! only the exact word 'ask' keeps a fix on asking.
use crate::pyrules;
use crate::server::BrainError;
use serde_json::{Map, Value, json};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// The brain serves each call on its own thread: one change at a time.
static WRITE: Mutex<()> = Mutex::new(());

const FIXES: [&str; 4] = ["nudge", "compact", "tell", "quiet"];
const NOTIFY: [&str; 3] = ["needs_you", "stalls", "finished"];

/// What the fixes act on (Settings › Fixes): (name, low, high, the words when out of bounds).
const BOUNDS: [(&str, f64, f64, &str); 2] = [
    ("quiet_minutes", 5.0, 120.0, "Enter a number of minutes from 5 to 120."),
    ("memory_full", 0.5, 0.95, "Enter a share from 50% to 95%."),
];

pub fn defaults() -> Value {
    json!({"fixes": {"nudge": "auto", "compact": "auto", "tell": "auto", "quiet": "auto"},
           "fixes_notice": false, "fixes_version": 2, "run_found": {},
           "practices": {},
           "notify": {"needs_you": true, "stalls": true, "finished": true},
           "notify_paused_until": null,
           "show_commands": false,
           "groups": {},
           "first_run_done": false,
           "thresholds": {"quiet_minutes": 15, "memory_full": 0.75}})
}

pub fn path() -> PathBuf {
    crate::quiet::state_dir().join("settings.json")
}

fn in_bounds(name: &str, value: &Value) -> bool {
    let Some(&(_, lo, hi, _)) = BOUNDS.iter().find(|b| b.0 == name) else { return false };
    !value.is_boolean() && pyrules::is_number(value) && pyrules::float(value).is_some_and(|v| lo <= v && v <= hi)
}

fn dict(raw: &Map<String, Value>, key: &str) -> Map<String, Value> {
    raw.get(key).and_then(Value::as_object).cloned().unwrap_or_default()
}

/// Python's `isinstance(v, int) and not isinstance(v, bool)`.
fn int(v: Option<&Value>) -> Option<Value> {
    let v = v?;
    let is_int =
        v.is_i64() || v.is_u64() || v.as_str().is_some_and(|s| s.starts_with(&format!("{}int:", pyrules::MARK)));
    is_int.then(|| v.clone())
}

fn clean(raw: &Map<String, Value>) -> Value {
    let (fixes, notify, thresholds) = (dict(raw, "fixes"), dict(raw, "notify"), dict(raw, "thresholds"));
    let is_true = |k: &str| json!(raw.get(k) == Some(&Value::Bool(true)));
    let mut s = defaults();
    s["fixes"] = FIXES
        .iter()
        .map(|&k| (k.to_string(), json!(if fixes.get(k) == Some(&json!("ask")) { "ask" } else { "auto" })))
        .collect();
    s["notify"] = NOTIFY.iter().map(|&k| (k.to_string(), json!(notify.get(k) != Some(&Value::Bool(false))))).collect();
    s["practices"] = dict(raw, "practices").into_iter().filter(|(_, v)| v.is_boolean()).collect();
    s["groups"] = dict(raw, "groups")
        .into_iter()
        .filter(|(_, v)| v.as_str().is_some_and(|t| !pyrules::split(t).is_empty()))
        .collect();
    s["show_commands"] = is_true("show_commands");
    s["notify_paused_until"] = raw.get("notify_paused_until").filter(|v| v.is_string()).cloned().unwrap_or(Value::Null);
    s["first_run_done"] = is_true("first_run_done");
    s["fixes_notice"] = is_true("fixes_notice");
    s["fixes_version"] = int(raw.get("fixes_version")).unwrap_or(json!(1)); // a file without it is from before 2
    s["run_found"] = dict(raw, "run_found").into_iter().filter(|(_, v)| *v == Value::Bool(true)).collect();
    for (name, ..) in BOUNDS {
        if let Some(v) = thresholds.get(name).filter(|v| in_bounds(name, v)) {
            s["thresholds"][name] = v.clone();
        }
    }
    s
}

fn read(file: &Path) -> Option<Value> {
    let text = String::from_utf8(std::fs::read(file).ok()?).ok()?;
    pyrules::json_loads(&text).ok()
}

/// (top, sub) of a key that may be set to `value`.
pub fn check(key: &str, value: &Value) -> Result<(String, String), BrainError> {
    let (top, sub) = key.split_once('.').unwrap_or((key, ""));
    if let Some(&(.., words)) = BOUNDS.iter().find(|b| top == "thresholds" && b.0 == sub)
        && !in_bounds(sub, value)
    {
        return Err(BrainError::new(words));
    }
    let good = match top {
        "fixes" => FIXES.contains(&sub) && (value == "ask" || value == "auto"),
        "notify" => NOTIFY.contains(&sub) && value.is_boolean(),
        "practices" | "run_found" => !sub.is_empty() && value.is_boolean(),
        "show_commands" | "fixes_notice" | "first_run_done" => sub.is_empty() && value.is_boolean(),
        "notify_paused_until" => sub.is_empty() && (value.is_null() || value.is_string()),
        "groups" => sub.is_empty() && value.is_object(),
        "thresholds" => BOUNDS.iter().any(|b| b.0 == sub),
        _ => false,
    };
    if !good {
        return Err(BrainError::new(format!("no setting {} with that value", pyrules::repr_str(key))));
    }
    Ok((top.into(), sub.into()))
}

/// The settings, cleaned: a damaged file gives the defaults.
pub fn load(file: Option<&Path>) -> Value {
    match read(file.map_or_else(path, Path::to_path_buf).as_path()) {
        Some(Value::Object(raw)) => clean(&raw),
        _ => defaults(),
    }
}

/// Once, for an install from before fixes ran themselves: switch every fix to automatic and owe the notice.
pub fn migrate(file: Option<&Path>) -> bool {
    let _write = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    let file = file.map_or_else(path, Path::to_path_buf);
    let Some(Value::Object(raw)) = read(&file) else { return false };
    let mut s = clean(&raw);
    if pyrules::float(&s["fixes_version"]).unwrap_or(0.0) >= 2.0 {
        return false;
    }
    s["fixes"] = FIXES.iter().map(|&k| (k.to_string(), json!("auto"))).collect();
    s["fixes_notice"] = json!(true);
    s["fixes_version"] = json!(2);
    // Python lets a failed write escape and the brain logs it; here it is a no.
    crate::quiet::write_json(&file, &s).is_ok()
}

pub fn set_value(key: &str, value: &Value, file: Option<&Path>) -> Result<Value, BrainError> {
    let (top, sub) = check(key, value)?;
    let _write = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    let mut s = load(file);
    if sub.is_empty() {
        s[&top] = value.clone();
    } else {
        s[&top][&sub] = value.clone();
    }
    let at = file.map_or_else(path, Path::to_path_buf);
    crate::quiet::write_json(&at, &s).map_err(|e| BrainError::new(e.to_string()))?;
    Ok(load(file))
}

/// The saved value of a fix threshold, or its default.
pub fn threshold(name: &str) -> f64 {
    pyrules::float(&load(None)["thresholds"][name]).expect("a fix threshold's name")
}
