//! `src/pyrules.rs` against Python's answers in `tests/data/pyrules.json` (`record/pyrules_cases.py`).
#![cfg(unix)]
use assieme_brain::pyrules;
use serde_json::{Value, json};

fn cases(name: &str) -> Vec<Value> {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/pyrules.json");
    let all: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    all[name].as_array().unwrap().clone()
}

fn check(name: &str, got: impl Fn(&Value) -> Value, want: impl Fn(&Value) -> Value) {
    let wrong: Vec<String> = cases(name)
        .iter()
        .filter(|c| got(c) != want(c))
        .map(|c| format!("{}: got {}, Python {}", c["in"], got(c), want(c)))
        .collect();
    assert!(wrong.is_empty(), "{name}, {} of {} differ:\n{}", wrong.len(), cases(name).len(), wrong.join("\n"));
}

#[test]
fn fromisoformat() {
    let got =
        |c: &Value| json!(pyrules::fromisoformat(c["in"].as_str().unwrap()).map(|t| t.as_nanosecond().to_string()));
    check("fromisoformat", got, |c| c["out"].clone());
}

/// The value with [`pyrules::MARK`]ed strings written as the recorder tags them.
fn canonical(v: &Value) -> Value {
    match v {
        Value::String(s) if s.starts_with(pyrules::MARK) => {
            let what = &s[pyrules::MARK.len_utf8()..];
            match what.strip_prefix("int:") {
                Some(digits) => json!({"$py": "int", "v": digits}),
                None => json!({"$py": what}),
            }
        }
        Value::Array(a) => Value::Array(a.iter().map(canonical).collect()),
        Value::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), canonical(v))).collect()),
        other => other.clone(),
    }
}

#[test]
fn json_loads() {
    let got = |c: &Value| match pyrules::json_loads(c["in"].as_str().unwrap()) {
        Ok(v) => json!({"ok": canonical(&v)}),
        Err(_) => json!({"error": true}),
    };
    let want = |c: &Value| {
        let mut c = c.as_object().unwrap().clone();
        c.remove("in");
        Value::Object(c)
    };
    check("json_loads", got, want);
}

#[test]
fn marked_values_read_as_python_numbers() {
    let v = pyrules::json_loads("[NaN, -Infinity, 100000000000000000000000000001, 2, \"NaN\", true]").unwrap();
    let a = v.as_array().unwrap();
    assert!(pyrules::float(&a[0]).unwrap().is_nan());
    assert_eq!(pyrules::float(&a[1]), Some(f64::NEG_INFINITY));
    assert_eq!(pyrules::float(&a[2]), Some(1e29));
    assert!(a[..4].iter().all(pyrules::is_number));
    assert!(!pyrules::is_number(&a[4]) && !pyrules::is_number(&a[5]));
}

#[test]
fn splitlines() {
    check("splitlines", |c| json!(pyrules::splitlines(c["in"].as_str().unwrap())), |c| c["out"].clone());
}

#[test]
fn repr_str() {
    check("repr_str", |c| json!(pyrules::repr_str(c["in"].as_str().unwrap())), |c| c["out"].clone());
}

#[test]
fn glob() {
    let tree = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/glob_tree/");
    let got = |c: &Value| {
        let found = pyrules::glob(&std::path::Path::new(tree).join(c["in"].as_str().unwrap()));
        json!(found.iter().map(|p| p.to_str().unwrap().strip_prefix(tree).unwrap().to_string()).collect::<Vec<_>>())
    };
    check("glob", got, |c| c["out"].clone());
}

#[test]
fn fnmatchcase() {
    let got = |c: &Value| json!(pyrules::fnmatchcase(c["in"][0].as_str().unwrap(), c["in"][1].as_str().unwrap()));
    check("fnmatchcase", got, |c| c["out"].clone());
}
