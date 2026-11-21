//! Port of `assieme/runtime.py`, owned by the foundation (see OWNERS.md).
//!
//! The runtime layer: talks to Assieme's own core (`asm`, the fork in runtime/) over its socket.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// An error from the core (or from starting it), with its error kind and a hint.
#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeError {
    pub kind: String,
    pub message: String,
    pub hint: String,
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
    todo!("runtime.clean_env")
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
        todo!("runtime.Runtime.new")
    }

    /// Start this session's core (clean environment) unless it is listening.
    pub fn ensure_core(&self) -> Result<(), RuntimeError> {
        todo!("runtime.Runtime.ensure_core")
    }

    /// One request; `params` is a JSON object.
    pub fn call(&self, method: &str, params: Value) -> Result<Value, RuntimeError> {
        todo!("runtime.Runtime.call")
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
        todo!("runtime.Runtime.start")
    }

    pub fn send(&self, t: &str, text: &str, timeout_s: f64) -> Result<Value, RuntimeError> {
        todo!("runtime.Runtime.send")
    }

    pub fn keys(&self, t: &str, keys: &[&str]) -> Result<(), RuntimeError> {
        todo!("runtime.Runtime.keys")
    }

    /// Press the program's own stop key (Esc for an agent). It ends the turn, never the program.
    pub fn interrupt(&self, t: &str, timeout_s: f64) -> Result<Value, RuntimeError> {
        todo!("runtime.Runtime.interrupt")
    }

    pub fn label(&self, t: &str, labels: &BTreeMap<String, Value>) -> Result<(), RuntimeError> {
        todo!("runtime.Runtime.label")
    }

    /// The screen's text; `lines` 0 for the whole screen.
    pub fn read(&self, t: &str, lines: i64) -> Result<String, RuntimeError> {
        todo!("runtime.Runtime.read")
    }

    pub fn get(&self, t: &str) -> Result<Value, RuntimeError> {
        todo!("runtime.Runtime.get")
    }

    pub fn wait(&self, t: &str, timeout_s: f64) -> Result<Value, RuntimeError> {
        todo!("runtime.Runtime.wait")
    }

    pub fn kill(&self, t: &str) -> Result<(), RuntimeError> {
        todo!("runtime.Runtime.kill")
    }

    /// This machine's terminals with these labels.
    pub fn list(&self, labels: &BTreeMap<String, String>, all: bool) -> Result<Vec<Value>, RuntimeError> {
        todo!("runtime.Runtime.list")
    }
}
