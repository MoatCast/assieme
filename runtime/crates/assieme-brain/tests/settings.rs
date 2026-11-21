//! `src/settings.rs` against Python's answers in `tests/data/settings.json` (`record/settings_cases.py`).
#![cfg(unix)]
use assieme_brain::{pyrules, settings};
use serde_json::{Value, json};

/// Recorded `{"$py": "nan"}` back to the marked string `json_loads` gives, and the reverse.
fn to_rust(v: &Value) -> Value {
    match v {
        Value::Object(o) if o.get("$py") == Some(&json!("nan")) => json!(format!("{}nan", pyrules::MARK)),
        Value::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), to_rust(v))).collect()),
        other => other.clone(),
    }
}

#[test]
fn settings_as_python() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/settings.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let dir = std::path::PathBuf::from(format!("/var/tmp/brain-settings-{}", std::process::id()));
    let mut wrong = vec![];
    for c in &cases {
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("settings.json");
        if let Some(text) = c["file"].as_str() {
            std::fs::write(&file, text).unwrap();
        }
        let call = c["call"].as_array().unwrap();
        let mut got = match call[0].as_str().unwrap() {
            "load" => json!({"ok": settings::load(Some(&file))}),
            "migrate" => json!({"ok": settings::migrate(Some(&file))}),
            _ => match settings::set_value(call[1].as_str().unwrap(), &to_rust(&call[2]), Some(&file)) {
                Ok(v) => json!({"ok": v}),
                Err(e) => json!({"error": e.message}),
            },
        };
        let after = std::fs::read_to_string(&file).ok().filter(|t| Some(t.as_str()) != c["file"].as_str());
        got["after"] = after.map_or(Value::Null, |t| pyrules::json_loads(&t).unwrap());
        let want = to_rust(&json!({"ok": c["ok"], "error": c["error"], "after": c["after"]}));
        let got = json!({"ok": got["ok"], "error": got["error"], "after": got["after"]});
        if got != want {
            wrong.push(format!("{} {}:\n  got    {got}\n  Python {want}", c["file"], c["call"]));
        }
    }
    let _ = std::fs::remove_dir_all(&dir);
    assert!(wrong.is_empty(), "{} of {} differ:\n{}", wrong.len(), cases.len(), wrong.join("\n"));
}
