//! `src/runtime.rs` against Python's answers in `tests/data/runtime.json` (`record/runtime_cases.py`): the same
//! requests reach a fake core, and its replies give the same results and errors.
#![cfg(unix)]
use assieme_brain::runtime::{Runtime, RuntimeError, clean_env};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixListener;
use std::sync::{Arc, Mutex};

fn data() -> Value {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/runtime.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn scratch(name: &str) -> std::path::PathBuf {
    let dir = std::path::PathBuf::from(format!("/var/tmp/brain-rt-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Answers each connection with the next reply (None: close without one); keeps each request.
fn fake_core(sock: &std::path::Path, replies: Vec<Option<String>>) -> Arc<Mutex<Vec<Value>>> {
    let listener = UnixListener::bind(sock).unwrap();
    let seen = Arc::new(Mutex::new(vec![]));
    let kept = seen.clone();
    std::thread::spawn(move || {
        for reply in replies {
            let (conn, _) = listener.accept().unwrap();
            let mut conn = BufReader::new(conn);
            let mut line = String::new();
            conn.read_line(&mut line).unwrap();
            kept.lock().unwrap().push(serde_json::from_str(&line).unwrap());
            if let Some(reply) = reply {
                conn.get_mut().write_all(format!("{reply}\n").as_bytes()).unwrap();
            }
        }
    });
    seen
}

fn strs(v: &Value) -> Vec<String> {
    serde_json::from_value(v.clone()).unwrap()
}

fn run(rt: &Runtime, call: &[Value]) -> Result<Value, RuntimeError> {
    let s = |i: usize| call[i].as_str().unwrap();
    let f = |i: usize| call[i].as_f64().unwrap();
    let map = |i: usize| serde_json::from_value::<BTreeMap<String, String>>(call[i].clone()).unwrap();
    Ok(match s(0) {
        "call" => rt.call(s(1), call[2].clone())?,
        "start" => json!(rt.start(&strs(&call[1]), s(2), &map(3), call[4].as_str(), call[5].as_str())?),
        "send" => rt.send(s(1), s(2), f(3))?,
        "keys" => json!(rt.keys(s(1), &strs(&call[2]).iter().map(String::as_str).collect::<Vec<_>>())?),
        "interrupt" => rt.interrupt(s(1), f(2))?,
        "label" => json!(rt.label(s(1), &serde_json::from_value(call[2].clone()).unwrap())?),
        "read" => json!(rt.read(s(1), call[2].as_i64().unwrap())?),
        "get" => rt.get(s(1))?,
        "wait" => rt.wait(s(1), f(2))?,
        "kill" => json!(rt.kill(s(1))?),
        "list" => json!(rt.list(&map(1), call[2].as_bool().unwrap())?),
        other => panic!("no case for {other}"),
    })
}

#[test]
fn calls_as_python() {
    for (n, c) in data()["calls"].as_array().unwrap().iter().enumerate() {
        let dir = scratch(&n.to_string());
        let sock = dir.join("asm.sock");
        let call = c["call"].as_array().unwrap();
        if c["no_core"] == true {
            continue; // calls_without_a_core_or_asm
        }
        let replies = c["replies"].as_array().unwrap().iter().map(|r| r.as_str().map(String::from)).collect();
        let seen = fake_core(&sock, replies);
        let got = run(&Runtime::new("test", Some(sock.clone())), call);
        assert_eq!(Value::Array(seen.lock().unwrap().clone()), c["requests"], "{call:?}");
        match got {
            Ok(v) => assert_eq!(v, c["ok"], "{call:?}"),
            Err(e) if c.get("raised").is_some() => assert_eq!(e.kind, "bad_reply", "{call:?}"),
            Err(e) => {
                let e = json!({"kind": e.kind, "message": e.message.replace(sock.to_str().unwrap(), "<sock>"), "hint": e.hint});
                assert_eq!(e, c["error"], "{call:?}");
            }
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}

#[test]
fn calls_without_a_core_or_asm() {
    let c = data()["calls"].as_array().unwrap().iter().find(|c| c["no_core"] == true).unwrap().clone();
    let dir = scratch("nocore");
    // Only this test reads PATH.
    unsafe { std::env::set_var("PATH", "") };
    let e = Runtime::new("test", Some(dir.join("asm.sock"))).call("x", json!({})).unwrap_err();
    assert_eq!(json!({"kind": e.kind, "message": e.message, "hint": e.hint}), c["error"]);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn clean_env_as_python() {
    let c = data()["clean_env"].clone();
    let env: BTreeMap<String, String> = serde_json::from_value(c["in"].clone()).unwrap();
    assert_eq!(json!(clean_env(&env)), c["out"]);
}

#[test]
fn a_core_that_answers_is_not_started_again() {
    let dir = scratch("answers");
    let sock = dir.join("asm.sock");
    let _listener = UnixListener::bind(&sock).unwrap();
    assert_eq!(Runtime::new("test", Some(sock)).ensure_core(), Ok(()));
    let _ = std::fs::remove_dir_all(&dir);
}
