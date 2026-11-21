//! `src/rules.rs` against Python's answers in `tests/data/rules.json` (`record/rules_cases.py`). HOME is the fixture
//! home in every test of this file.
#![cfg(unix)]
use assieme_brain::rules;
use serde_json::{Value, json};
use std::path::Path;

const FILES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/rules_files");

fn data() -> Value {
    static HOME: std::sync::Once = std::sync::Once::new();
    HOME.call_once(|| unsafe { std::env::set_var("HOME", format!("{FILES}/home")) });
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/rules.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// Machine-free paths, as the recorder writes them.
fn short(v: Value) -> Value {
    serde_json::from_str(&v.to_string().replace(FILES, "<files>")).unwrap()
}

fn pairs(v: &Value) -> Vec<(String, String)> {
    serde_json::from_value(v.clone()).unwrap()
}

#[test]
fn load_as_python() {
    let home = format!("{FILES}/home");
    for c in data()["load"].as_array().unwrap() {
        let cwd = c["in"][0].as_str().map(|d| if d.is_empty() { String::new() } else { format!("{home}/{d}") });
        let got = rules::load(cwd.as_deref(), &Path::new(&home).join(c["in"][1].as_str().unwrap()));
        assert_eq!(short(json!(got)), c["out"], "{}", c["in"]);
    }
}

#[test]
fn explain_and_refused_as_python() {
    for c in data()["explain"].as_array().unwrap() {
        let i = &c["in"];
        let path = i[4].as_str().map(|p| p.replace("<files>", FILES));
        let got = rules::explain(
            i[0].as_str().unwrap(),
            i[1].as_str().unwrap(),
            i[2].as_str().unwrap_or(""),
            &pairs(&i[3]),
            path.as_deref(),
        );
        assert_eq!(json!(got), c["out"], "{i}");
        assert_eq!(json!(rules::REFUSED.is_match(i[0].as_str().unwrap())), c["refused"], "{i}");
    }
}

#[test]
fn running_settings_as_python() {
    let d = data()["running_settings"].clone();
    let dir = format!("{FILES}/home");
    std::fs::write(format!("{dir}/proj1/team.json"), r#"{"permissions": {"deny": ["Bash(sudo:*)"]}}"#).unwrap();
    std::fs::write(format!("{dir}/proj 7/rel.json"), r#"{"permissions": {"deny": ["Bash(docker:*)"]}}"#).unwrap();
    let ps = d["ps"].as_str().unwrap().replace("<files>", FILES);
    let cwd = |pid: &str| d["cwds"][pid].as_str().map(|c| c.replace("<home>", &dir));
    let got = rules::settings_of(&ps, &cwd);
    std::fs::remove_file(format!("{dir}/proj1/team.json")).unwrap();
    std::fs::remove_file(format!("{dir}/proj 7/rel.json")).unwrap();
    assert_eq!(short(json!(got)), d["out"]);
}
