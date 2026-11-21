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
struct Core {
    env: Env,
    session: &'static str,
}

impl Drop for Core {
    fn drop(&mut self) {
        let _ = self.asm(&["server", "stop"]).output();
    }
}

impl Core {
    fn new(session: &'static str) -> Core {
        Core { env: isolated(), session }
    }

    fn asm(&self, args: &[&str]) -> Command {
        let mut cmd = self.env.cmd();
        cmd.env("ASM_SESSION", self.session).env("ASM_SOCKET", self.socket()).args(args).stdin(Stdio::null());
        cmd
    }

    fn socket(&self) -> PathBuf {
        self.env.root.join("run/asm.sock")
    }

    fn brain_dir(&self) -> PathBuf {
        self.env.root.join("state/assieme")
    }

    fn configure(&self) {
        std::fs::create_dir_all(self.brain_dir()).unwrap();
        let path = std::env::var("PATH").unwrap_or_default();
        std::fs::write(self.brain_dir().join("brain.json"), json!({ "path": path }).to_string()).unwrap();
    }

    fn start(&self) {
        let out = self.asm(&["server", "start"]).output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    }

    fn call(&self, method: &str) -> Value {
        let stream = UnixStream::connect(self.socket()).unwrap();
        let mut r = BufReader::new(stream);
        let request = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}});
        r.get_mut().write_all(format!("{request}\n").as_bytes()).unwrap();
        let mut line = String::new();
        r.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    }

    fn brain(&self) -> Value {
        self.call("server.status")["result"]["brain"].clone()
    }

    /// Waits for a brain status `ok` accepts, noting every state seen on the way.
    fn wait(&self, limit: Duration, seen: &mut Vec<String>, ok: impl Fn(&Value) -> bool) -> Value {
        let at = Instant::now();
        loop {
            let brain = self.brain();
            if let Some(state) = brain["state"].as_str()
                && seen.last().map(String::as_str) != Some(state)
            {
                seen.push(state.into());
            }
            if ok(&brain) {
                return brain;
            }
            assert!(at.elapsed() < limit, "brain is {brain} after {limit:?}; seen {seen:?}");
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    fn running(&self, not: Option<u64>) -> u64 {
        let brain = self.wait(Duration::from_secs(15), &mut vec![], |b| {
            b["state"] == "running" && b["pid"].as_u64().is_some() && b["pid"].as_u64() != not
        });
        brain["pid"].as_u64().unwrap()
    }

    fn core_pid(&self) -> u64 {
        self.call("server.status")["result"]["pid"].as_u64().unwrap()
    }
}

fn alive(pid: u64) -> bool {
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

fn gone_within(pid: u64, limit: Duration) {
    let at = Instant::now();
    while alive(pid) {
        assert!(at.elapsed() < limit, "process {pid} still runs after {limit:?}");
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn brain_answers(core: &Core) -> bool {
    UnixStream::connect(core.brain_dir().join("brain.sock")).is_ok()
}

#[test]
fn the_assieme_core_keeps_its_brain_and_takes_it_along() {
    let core = Core::new("assieme");
    core.configure();
    core.start();
    let first = core.running(None);
    assert!(brain_answers(&core));

    // A brain that dies is started again.
    unsafe { libc::kill(first as i32, libc::SIGKILL) };
    let second = core.running(Some(first));

    // brain.restart replaces it.
    let reply = core.call("brain.restart");
    assert_eq!(reply["result"]["state"], "starting", "{reply}");
    let third = core.running(Some(second));
    gone_within(second, Duration::from_secs(5));

    // A restarted core takes its brain along and starts a new one.
    let old_core = core.core_pid();
    let out = core.asm(&["server", "restart"]).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    gone_within(third, Duration::from_secs(10));
    assert_ne!(core.core_pid(), old_core);
    let fourth = core.running(Some(third));

    // A stopped core leaves no brain behind.
    let out = core.asm(&["server", "stop"]).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    gone_within(fourth, Duration::from_secs(10));
    let log = std::fs::read_to_string(core.brain_dir().join("brain.log")).unwrap_or_default();
    assert!(!log.contains("panicked"), "{log}");
}

#[test]
fn brain_json_turns_the_brain_on_and_restart_reads_it_again() {
    let core = Core::new("assieme");
    core.start();
    assert_eq!(core.brain(), Value::Null, "no brain.json: no brain");
    let reply = core.call("brain.restart");
    assert_eq!(reply["error"]["data"]["kind"], "not_found", "{reply}");
    assert!(!core.brain_dir().join("brain.sock").exists());

    core.configure();
    assert_eq!(core.call("brain.restart")["result"]["state"], "starting");
    core.running(None);
    assert!(brain_answers(&core));
}

#[test]
fn other_sessions_never_start_a_brain() {
    let core = Core::new("test");
    core.configure();
    core.start();
    std::thread::sleep(Duration::from_millis(500));
    assert_eq!(core.brain(), Value::Null);
    assert!(!core.brain_dir().join("brain.sock").exists());
    let reply = core.call("brain.restart");
    assert_eq!(reply["error"]["data"]["kind"], "unavailable", "{reply}");
}

#[test]
fn five_quick_failures_leave_it_down_until_restart() {
    let core = Core::new("assieme");
    core.configure();
    // Something else answers on brain.sock: each brain leaves it alone and exits.
    let sock = core.brain_dir().join("brain.sock");
    let other = UnixListener::bind(&sock).unwrap();
    core.start();
    let mut seen = vec![];
    // Delays of 1, 2, 4 and 8 s, then down.
    core.wait(Duration::from_secs(40), &mut seen, |b| b["state"] == "down");
    assert!(seen.contains(&"waiting".to_string()), "{seen:?}");
    assert!(seen.iter().all(|s| s != "running"), "{seen:?}");
    std::thread::sleep(Duration::from_millis(1500));
    assert_eq!(core.brain()["state"], "down", "it stays down");

    drop(other);
    let at = Instant::now();
    while UnixStream::connect(&sock).is_ok() {
        assert!(at.elapsed() < Duration::from_secs(5), "the dropped listener still answers");
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(core.call("brain.restart")["result"]["state"], "starting");
    core.running(None);
}
