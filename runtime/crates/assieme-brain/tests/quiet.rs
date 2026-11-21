//! quiet's state files (the foundation's part) against Python's answers in `tests/data/quiet.json`
//! (`record/quiet_cases.py`), and the lock. One test: it sets the process environment.
#![cfg(unix)]
use assieme_brain::{pyrules, quiet};
use serde_json::{Value, json};
use std::path::Path;

fn canonical(v: &Value) -> Value {
    match v {
        Value::String(s) if s.starts_with(pyrules::MARK) => json!({"$py": &s[pyrules::MARK.len_utf8()..]}),
        Value::Array(a) => Value::Array(a.iter().map(canonical).collect()),
        Value::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), canonical(v))).collect()),
        other => other.clone(),
    }
}

#[test]
fn state_files_as_python() {
    let crate_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let data: Value =
        serde_json::from_str(&std::fs::read_to_string(crate_dir.join("tests/data/quiet.json")).unwrap()).unwrap();
    for c in data["read_json"].as_array().unwrap() {
        let got = quiet::read_json(&crate_dir.join(c["in"].as_str().unwrap()), json!("DEFAULT"));
        assert_eq!(canonical(&got), c["out"], "{}", c["in"]);
    }
    for c in data["state_dir"].as_array().unwrap() {
        unsafe {
            std::env::remove_var("XDG_STATE_HOME");
            for (k, v) in c["in"].as_object().unwrap() {
                std::env::set_var(k, v.as_str().unwrap());
            }
        }
        assert_eq!(quiet::state_dir().to_str().unwrap(), c["out"], "{}", c["in"]);
    }

    // write_json makes its folder, replaces the file whole, and leaves no temporary file.
    let root = std::path::PathBuf::from(format!("/var/tmp/brain-quiet-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let file = root.join("deep/er/held.json");
    quiet::write_json(&file, &json!([{"to": "é"}])).unwrap();
    quiet::write_json(&file, &json!([1, 2])).unwrap();
    assert_eq!(quiet::read_json(&file, json!(null)), json!([1, 2]));
    assert_eq!(std::fs::read_dir(file.parent().unwrap()).unwrap().count(), 1);

    // locked() makes the state folder and holds held.lock against any other holder, Python's flock included.
    unsafe { std::env::set_var("XDG_STATE_HOME", root.join("state")) };
    let lock = quiet::locked().unwrap();
    let other = std::fs::File::open(root.join("state/assieme/held.lock")).unwrap();
    assert!(!assieme_core::platform::try_lock(&other), "the lock is held");
    drop(lock);
    assert!(assieme_core::platform::try_lock(&other), "dropping releases it");
    let _ = std::fs::remove_dir_all(&root);
}
