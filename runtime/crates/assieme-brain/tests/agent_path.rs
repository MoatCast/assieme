//! `setup::agent_path` against Python's answers in `tests/data/agent_path.json` (`record/agent_path_cases.py`).
//! One test: it sets PATH and HOME.
#![cfg(unix)]
use assieme_brain::setup;
use serde_json::{Value, json};

const D: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/agent_path");

#[test]
fn agent_path_as_python() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/agent_path.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    unsafe { std::env::set_var("HOME", format!("{D}/home")) };
    for c in &cases {
        let i = &c["in"];
        unsafe {
            match i["path"].as_str() {
                Some(p) => std::env::set_var("PATH", p.replace("<d>", D)),
                None => std::env::remove_var("PATH"),
            }
        }
        let login = i["login"].as_str().unwrap().replace("<d>", D);
        let got = setup::agent_path_with(&i["name"].as_str().unwrap().replace("<d>", D), &|| login.clone());
        assert_eq!(json!(got.map(|p| p.display().to_string().replace(D, "<d>"))), c["out"], "{i}");
    }
}
