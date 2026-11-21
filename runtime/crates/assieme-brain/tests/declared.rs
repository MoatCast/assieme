//! The foundation's contract with the parallel sessions: every socket method is served (if only with "Not ported
//! yet"), and every name one session's module uses from another's is declared.
#![cfg(unix)]
use serde_json::Value;

fn data(name: &str) -> Value {
    let path = format!("{}/tests/data/{name}", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn every_method_is_in_the_table() {
    let want: Vec<String> = serde_json::from_value(data("methods.json")).unwrap();
    let have: Vec<String> = assieme_brain::methods().names().iter().map(|s| s.to_string()).collect();
    assert_eq!(have, want);
}

#[test]
fn every_method_answers_not_ported_yet() {
    let methods = assieme_brain::methods();
    let reply = methods.reply(br#"{"id": 1, "method": "brain.snapshot"}"#);
    assert_eq!(reply["error"]["message"], "Not ported yet: brain.snapshot.");
}

/// A name's Rust spelling: Python's, without leading underscores; a class without its trailing one.
fn rust_name(name: &str, kind: &str) -> String {
    let name = name.trim_start_matches('_');
    if kind == "class" { name.trim_end_matches('_').to_string() } else { name.to_string() }
}

#[test]
fn every_cross_session_name_is_declared() {
    let mut missing = vec![];
    for e in data("cross_names.json").as_array().unwrap() {
        let (module, name, kind) =
            (e["module"].as_str().unwrap(), e["name"].as_str().unwrap(), e["kind"].as_str().unwrap());
        let path = format!("{}/src/{module}.rs", env!("CARGO_MANIFEST_DIR"));
        let source = std::fs::read_to_string(&path).unwrap_or_default();
        let name = regex::escape(&rust_name(name, kind));
        let item =
            format!(r"(?m)^\s*pub (fn|struct|enum|const|static|type|mod) (r#)?{name}\b|^pub use [\w:]*\b{name};");
        if !regex::Regex::new(&item).unwrap().is_match(&source) {
            missing.push(format!("{module}.{name}"));
        }
    }
    assert!(missing.is_empty(), "not declared: {missing:?}");
}
