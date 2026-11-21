//! Shared test helpers: `mod testkit;` in a test file. A temporary HOME and state folder, a temporary git repository
//! with one commit, and a fake core that logs every request and answers from a table.
#![allow(dead_code)]
use serde_json::{Value, json};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

/// A scratch folder under /var/tmp (short paths keep sockets under the macOS limit), removed on drop.
pub struct Temp(pub PathBuf);

impl Temp {
    pub fn new() -> Temp {
        static N: AtomicU32 = AtomicU32::new(0);
        let dir =
            PathBuf::from(format!("/var/tmp/brain-kit-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("home")).unwrap();
        Temp(dir)
    }

    pub fn home(&self) -> PathBuf {
        self.0.join("home")
    }

    /// `XDG_STATE_HOME`; the brain's own folder is `state/assieme`.
    pub fn state(&self) -> PathBuf {
        self.0.join("state")
    }

    /// Points this process's HOME and XDG_STATE_HOME here: only for a test file whose tests all share one Temp.
    pub fn set_env(&self) {
        unsafe {
            std::env::set_var("HOME", self.home());
            std::env::set_var("XDG_STATE_HOME", self.state());
        }
    }

    /// A git repository with one commit (`README`), on branch `main`.
    pub fn git_repo(&self, name: &str) -> PathBuf {
        let repo = self.0.join(name);
        std::fs::create_dir_all(&repo).unwrap();
        std::fs::write(repo.join("README"), "hello\n").unwrap();
        for args in [
            &["init", "-q", "-b", "main"][..],
            &["-c", "user.name=t", "-c", "user.email=t@t", "add", "README"],
            &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first"],
        ] {
            let ok =
                std::process::Command::new("git").arg("-C").arg(&repo).args(args).env("HOME", self.home()).status();
            assert!(ok.unwrap().success(), "git {args:?}");
        }
        repo
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A fake core on `sock`: each request line is logged, and answered with `answers[method]`, else "no method".
pub struct FakeCore {
    pub log: Arc<Mutex<Vec<Value>>>,
}

impl FakeCore {
    pub fn start(sock: &Path, answers: HashMap<&'static str, Value>) -> FakeCore {
        let listener = UnixListener::bind(sock).unwrap();
        let log = Arc::new(Mutex::new(vec![]));
        let kept = log.clone();
        std::thread::spawn(move || {
            for conn in listener.incoming().flatten() {
                let mut conn = BufReader::new(conn);
                let mut line = String::new();
                while conn.read_line(&mut line).unwrap_or(0) > 0 {
                    let req: Value = serde_json::from_str(&line).unwrap();
                    kept.lock().unwrap().push(json!({"method": req["method"], "params": req["params"]}));
                    let reply = match answers.get(req["method"].as_str().unwrap_or_default()) {
                        Some(result) => json!({"jsonrpc": "2.0", "id": req["id"], "result": result}),
                        None => {
                            json!({"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "no method"}})
                        }
                    };
                    let _ = conn.get_mut().write_all(format!("{reply}\n").as_bytes());
                    line.clear();
                }
            }
        });
        FakeCore { log }
    }

    /// The params of every request for `method` (`terminal.send`, `terminal.kill`, `terminal.run`, ...).
    pub fn calls(&self, method: &str) -> Vec<Value> {
        self.log.lock().unwrap().iter().filter(|r| r["method"] == method).map(|r| r["params"].clone()).collect()
    }
}
