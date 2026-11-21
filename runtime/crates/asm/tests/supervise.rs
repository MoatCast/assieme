//! The `assieme` core starting and keeping its brain, in an isolated environment.
#![cfg(unix)]
mod common;
use common::{Env, isolated};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// A core in its own folders; stopped when the test ends, whatever happens.
struct Core(Env, &'static str);

impl Drop for Core {
    fn drop(&mut self) {
        let _ = self.asm(&["server", "stop"]).output();
    }
}

impl Core {
    fn new(session: &'static str, configured: bool) -> Core {
        let core = Core(isolated(), session);
        std::fs::create_dir_all(core.dir()).unwrap();
        if configured {
            core.configure();
        }
        core
    }

    fn configure(&self) {
        let path = std::env::var("PATH").unwrap_or_default();
        std::fs::write(self.dir().join("brain.json"), json!({ "path": path }).to_string()).unwrap();
    }

    fn asm(&self, args: &[&str]) -> Command {
        let mut cmd = self.0.cmd();
        cmd.env("ASM_SESSION", self.1).env("ASM_SOCKET", self.0.root.join("run/asm.sock"));
        cmd.args(args).stdin(Stdio::null());
        cmd
    }

    fn run(&self, args: &[&str]) {
        let out = self.asm(args).output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    }

    fn dir(&self) -> PathBuf {
        self.0.root.join("state/assieme")
    }

    fn call(&self, method: &str) -> Value {
        let mut r = BufReader::new(UnixStream::connect(self.0.root.join("run/asm.sock")).unwrap());
        r.get_mut().write_all(format!("{}\n", json!({"id": 1, "method": method})).as_bytes()).unwrap();
        let mut line = String::new();
        r.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    }

    /// Waits for a brain status `ok` accepts; returns every state seen on the way.
    fn wait(&self, ok: impl Fn(&Value) -> bool) -> (Value, Vec<String>) {
        let (at, mut seen) = (Instant::now(), Vec::<String>::new());
        loop {
            let brain = self.call("server.status")["result"]["brain"].clone();
            let state = brain["state"].as_str().unwrap_or("absent");
            if seen.last().is_none_or(|s| s != state) {
                seen.push(state.into());
            }
            if ok(&brain) {
                return (brain, seen);
            }
            assert!(at.elapsed() < Duration::from_secs(40), "brain is {brain}; seen {seen:?}");
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    /// The pid of a running brain other than `not`, once its socket answers.
    fn running(&self, not: u64) -> u64 {
        let (brain, _) = self.wait(|b| b["state"] == "running" && b["pid"].as_u64().is_some_and(|p| p != not));
        assert!(UnixStream::connect(self.dir().join("brain.sock")).is_ok());
        brain["pid"].as_u64().unwrap()
    }
}

fn gone(pid: u64) {
    let at = Instant::now();
    while unsafe { libc::kill(pid as i32, 0) } == 0 {
        assert!(at.elapsed() < Duration::from_secs(10), "process {pid} still runs");
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[test]
fn the_assieme_core_keeps_its_brain_and_takes_it_along() {
    let core = Core::new("assieme", true);
    core.run(&["server", "start"]);
    let first = core.running(0);
    unsafe { libc::kill(first as i32, libc::SIGKILL) };
    let second = core.running(first);
    assert_eq!(core.call("brain.restart")["result"]["state"], "starting");
    let third = core.running(second);
    gone(second);
    // The pipe rule: a core that restarts or stops takes its brain with it.
    core.run(&["server", "restart"]);
    gone(third);
    let fourth = core.running(third);
    core.run(&["server", "stop"]);
    gone(fourth);
}

#[test]
fn only_the_assieme_session_with_brain_json_starts_a_brain() {
    for (session, configured, error) in [("assieme", false, "not_found"), ("test", true, "unavailable")] {
        let core = Core::new(session, configured);
        core.run(&["server", "start"]);
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(core.call("server.status")["result"]["brain"], Value::Null, "{session}");
        assert_eq!(core.call("brain.restart")["error"]["data"]["kind"], error, "{session}");
    }
    // brain.restart reads brain.json again.
    let core = Core::new("assieme", false);
    core.run(&["server", "start"]);
    core.configure();
    assert_eq!(core.call("brain.restart")["result"]["state"], "starting");
    core.running(0);
}

#[test]
fn five_quick_failures_leave_it_down_until_restart() {
    let core = Core::new("assieme", true);
    // Something else answers on brain.sock: each brain leaves it alone and exits.
    let sock = core.dir().join("brain.sock");
    let other = UnixListener::bind(&sock).unwrap();
    core.run(&["server", "start"]);
    let (_, seen) = core.wait(|b| b["state"] == "down");
    assert!(seen.iter().any(|s| s == "waiting") && seen.iter().all(|s| s != "running"), "{seen:?}");
    drop(other);
    while UnixStream::connect(&sock).is_ok() {
        std::thread::sleep(Duration::from_millis(10)); // a forked test child may still hold a copy
    }
    assert_eq!(core.call("brain.restart")["result"]["state"], "starting");
    core.running(0);
}
