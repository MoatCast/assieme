//! Every method answers from Rust, and every call the app makes reaches its handler's parameters.
#![cfg(unix)]
mod common;
use common::{Env, isolated};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::process::{Child, Stdio};
use std::time::{Duration, Instant};

struct Brain(Child, BufReader<UnixStream>);

impl Drop for Brain {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[allow(clippy::zombie_processes)] // Brain's Drop waits
fn start(env: &Env) -> Brain {
    let sock = env.root.join("run/brain.sock");
    let mut child = env
        .cmd()
        .args(["brain", "--socket"])
        .arg(&sock)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let at = Instant::now();
    loop {
        if let Ok(s) = UnixStream::connect(&sock) {
            return Brain(child, BufReader::new(s));
        }
        if at.elapsed() > Duration::from_secs(10) {
            let _ = child.kill();
            let _ = child.wait();
            panic!("the brain never answered");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn ask(brain: &mut Brain, method: &str, params: Value) -> Option<String> {
    let line = json!({"id": 1, "method": method, "params": params});
    writeln!(brain.1.get_mut(), "{line}").unwrap();
    let mut reply = String::new();
    brain.1.read_line(&mut reply).unwrap();
    let reply: Value = serde_json::from_str(&reply).unwrap();
    reply["error"]["message"].as_str().map(String::from)
}

fn data(name: &str) -> Value {
    let path = format!("{}/../assieme-brain/tests/data/{name}", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn no_method_says_not_ported() {
    let mut brain = start(&isolated());
    for name in data("methods.json").as_array().unwrap() {
        let name = name.as_str().unwrap();
        let error = ask(&mut brain, name, json!({}));
        assert!(!error.is_some_and(|e| e.starts_with("Not ported yet:")), "{name}");
    }
}

#[test]
fn app_calls_reach_their_handlers() {
    let mut brain = start(&isolated());
    for call in data("app_calls.json").as_array().unwrap() {
        let method = call["method"].as_str().unwrap();
        let params: serde_json::Map<String, Value> =
            call["keys"].as_array().unwrap().iter().map(|k| (k.as_str().unwrap().to_string(), Value::Null)).collect();
        let error = ask(&mut brain, method, Value::Object(params)).unwrap_or_default();
        assert!(!error.contains("missing field") && !error.contains("unknown field"), "{method}: {error}");
    }
}
