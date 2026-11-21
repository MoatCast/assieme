//! Port of `assieme/runtime.py`, owned by the foundation (see OWNERS.md).
//!
//! The runtime layer: talks to Assieme's own core (`asm`, the fork in runtime/) over its socket. One JSON-RPC request
//! and reply per line, one connection per call.
use crate::server::BrainError;
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;

/// Variables that mark "running inside another agent session". An inherited one turns off agents' transcripts
/// (CLAUDE_CODE_CHILD_SESSION) or confuses them; configuration and logins (CODEX_HOME, tokens) are kept.
const MARKERS: [&str; 13] = [
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_PID",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_THREAD_ID",
];

/// What a core started by the brain inherits besides its session, socket and PATH (the spec's allow-list).
const KEPT_FOR_CORE: [&str; 4] = ["HOME", "USER", "LANG", "XDG_STATE_HOME"];

/// An error from the core (or from starting it), with its error kind and a hint.
#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeError {
    pub kind: String,
    pub message: String,
    pub hint: String,
}

impl RuntimeError {
    fn new(kind: &str, message: impl Into<String>, hint: &str) -> RuntimeError {
        RuntimeError { kind: kind.into(), message: message.into(), hint: hint.into() }
    }
}

impl From<RuntimeError> for BrainError {
    fn from(e: RuntimeError) -> BrainError {
        BrainError::new(e.message)
    }
}

impl std::fmt::Display for RuntimeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

/// The environment without agent markers: an inherited Claude Code marker turns off agents' transcripts.
pub fn clean_env(env: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    env.iter().filter(|(k, _)| !MARKERS.contains(&k.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect()
}

/// The core's socket: `ASSIEME_ASM_SOCKET` for a brain on a remote machine, else the state folder's.
pub fn socket_path() -> PathBuf {
    match std::env::var_os("ASSIEME_ASM_SOCKET").filter(|s| !s.is_empty()) {
        Some(sock) => sock.into(),
        None => crate::quiet::state_dir().join("asm.sock"),
    }
}

/// A whole number as an integer, as Python's own numbers go out.
fn number(x: f64) -> Value {
    if x.fract() == 0.0 && x.abs() < 9e15 { json!(x as i64) } else { json!(x) }
}

/// A client of Assieme's own core, one connection per call.
#[derive(Debug, Clone, PartialEq)]
pub struct Runtime {
    pub session: String,
    pub sock: PathBuf,
}

impl Runtime {
    /// `session` is "assieme" for the app's core; `sock` None: the session's own socket.
    pub fn new(session: &str, sock: Option<PathBuf>) -> Runtime {
        Runtime { session: session.into(), sock: sock.unwrap_or_else(socket_path) }
    }

    /// Start this session's core (clean environment) unless it is listening; a stale socket file is removed.
    pub fn ensure_core(&self) -> Result<(), RuntimeError> {
        if UnixStream::connect(&self.sock).is_ok() {
            return Ok(());
        }
        if crate::pyrules::which("asm").is_none() {
            return Err(RuntimeError::new(
                "asm_missing",
                "Assieme's runtime is not installed",
                "reinstall Assieme, which carries it",
            ));
        }
        let failed =
            |e: std::io::Error| RuntimeError::new("start_failed", format!("Assieme's runtime did not start: {e}"), "");
        if self.sock.exists() {
            std::fs::remove_file(&self.sock).map_err(failed)?;
        }
        if let Some(dir) = self.sock.parent() {
            std::fs::create_dir_all(dir).map_err(failed)?;
        }
        // Agents inherit the core's PATH, so their `asm send` goes through quiet send.
        let shim = crate::quiet::install().map_err(failed)?;
        let path = format!("{}:{}", shim.display(), std::env::var("PATH").unwrap_or_default());
        let mut cmd = std::process::Command::new("asm");
        cmd.args(["server", "start"]).env_clear();
        for key in KEPT_FOR_CORE {
            if let Some(v) = std::env::var_os(key) {
                cmd.env(key, v);
            }
        }
        let out = cmd
            .env("ASM_SESSION", &self.session)
            .env("ASM_SOCKET", &self.sock)
            .env("PATH", path)
            .output()
            .map_err(failed)?;
        if !out.status.success() {
            let said = if out.stderr.is_empty() { &out.stdout } else { &out.stderr };
            let said = String::from_utf8_lossy(said);
            let said = said.trim_matches(crate::pyrules::isspace);
            return Err(RuntimeError::new("start_failed", format!("Assieme's runtime did not start: {said}"), ""));
        }
        Ok(())
    }

    /// One request; `params` is a JSON object.
    pub fn call(&self, method: &str, params: Value) -> Result<Value, RuntimeError> {
        let stream = match UnixStream::connect(&self.sock) {
            Ok(s) => s,
            Err(_) => {
                self.ensure_core()?;
                UnixStream::connect(&self.sock).map_err(|_| {
                    let at = self.sock.display();
                    RuntimeError::new("unavailable", format!("cannot reach Assieme's runtime at {at}"), "")
                })?
            }
        };
        let closed =
            || RuntimeError::new("unavailable", format!("Assieme's runtime closed the connection during {method}"), "");
        let request = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
        let mut line = request.to_string().into_bytes();
        line.push(b'\n');
        let mut reader = BufReader::new(stream);
        reader.get_mut().write_all(&line).map_err(|_| closed())?;
        let mut reply = vec![];
        if reader.read_until(b'\n', &mut reply).unwrap_or(0) == 0 {
            return Err(closed());
        }
        // Python fails there with its own error; here it is a bad reply from the core.
        let bad = || {
            RuntimeError::new(
                "bad_reply",
                format!("Assieme's runtime gave a reply that can't be read, to {method}"),
                "",
            )
        };
        let msg = crate::pyrules::json_loads_bytes(&reply).map_err(|_| bad())?;
        if let Some(e) = msg.get("error") {
            let data = e.get("data").filter(|d| crate::pyrules::truthy(d)).cloned().unwrap_or(json!({}));
            let text = |v: Option<&Value>, default: &str| v.and_then(Value::as_str).unwrap_or(default).to_string();
            return Err(RuntimeError {
                kind: text(data.get("kind"), "error"),
                message: text(e.get("message"), ""),
                hint: text(data.get("hint"), ""),
            });
        }
        msg.get("result").cloned().ok_or_else(bad)
    }

    /// A new terminal's address.
    pub fn start(
        &self,
        argv: &[String],
        cwd: &str,
        labels: &BTreeMap<String, String>,
        name: Option<&str>,
        parent: Option<&str>,
    ) -> Result<String, RuntimeError> {
        let mut p = Map::new();
        p.insert("argv".into(), json!(argv));
        p.insert("cwd".into(), json!(cwd));
        p.insert("labels".into(), json!(labels));
        p.insert("wait".into(), json!(false));
        if let Some(name) = name.filter(|n| !n.is_empty()) {
            p.insert("name".into(), json!(name));
        }
        if let Some(parent) = parent.filter(|n| !n.is_empty()) {
            p.insert("parent".into(), json!(parent));
            // The parent only hears its news: merging or stopping it must not end its reports.
            p.insert("outlive_parent".into(), json!(true));
        }
        let got = self.call("terminal.run", Value::Object(p))?;
        got["address"].as_str().map(String::from).ok_or_else(|| {
            RuntimeError::new("bad_reply", "Assieme's runtime gave a reply that can't be read, to terminal.run", "")
        })
    }

    pub fn send(&self, t: &str, text: &str, timeout_s: f64) -> Result<Value, RuntimeError> {
        self.call("terminal.send", json!({"terminal": t, "text": text, "timeout": number(timeout_s)}))
    }

    pub fn keys(&self, t: &str, keys: &[&str]) -> Result<(), RuntimeError> {
        self.call("terminal.keys", json!({"terminal": t, "keys": keys})).map(drop)
    }

    /// Press the program's own stop key (Esc for an agent). It ends the turn, never the program.
    pub fn interrupt(&self, t: &str, timeout_s: f64) -> Result<Value, RuntimeError> {
        self.call("terminal.interrupt", json!({"terminal": t, "timeout": number(timeout_s)}))
    }

    pub fn label(&self, t: &str, labels: &BTreeMap<String, Value>) -> Result<(), RuntimeError> {
        self.call("terminal.label", json!({"terminal": t, "labels": labels})).map(drop)
    }

    /// The screen's text; `lines` 0 for the whole screen.
    pub fn read(&self, t: &str, lines: i64) -> Result<String, RuntimeError> {
        let got = self.call("terminal.read", json!({"terminal": t, "lines": lines}))?;
        got["text"].as_str().map(String::from).ok_or_else(|| {
            RuntimeError::new("bad_reply", "Assieme's runtime gave a reply that can't be read, to terminal.read", "")
        })
    }

    pub fn get(&self, t: &str) -> Result<Value, RuntimeError> {
        self.call("terminal.get", json!({"terminal": t}))
    }

    pub fn wait(&self, t: &str, timeout_s: f64) -> Result<Value, RuntimeError> {
        self.call("terminal.wait", json!({"terminal": t, "timeout": number(timeout_s)}))
    }

    pub fn kill(&self, t: &str) -> Result<(), RuntimeError> {
        self.call("terminal.kill", json!({"terminal": t})).map(drop)
    }

    /// This machine's terminals. Another machine's (`gpu-box/t7`) belong to the brain there.
    pub fn list(&self, labels: &BTreeMap<String, String>, all: bool) -> Result<Vec<Value>, RuntimeError> {
        let got = self.call("terminal.list", json!({"labels": labels, "all": all}))?;
        let terminals = got["terminals"].as_array().cloned().unwrap_or_default();
        Ok(terminals.into_iter().filter(|t| !t["address"].as_str().unwrap_or_default().contains('/')).collect())
    }
}
