//! Port of `assieme/remote.py`, owned by session E (see OWNERS.md).
//!
//! Remote machines (step 15): another machine's agents and brain, reached only through the core's machine line.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

#[derive(Debug, Clone, PartialEq)]
pub struct RemoteError {
    pub message: String,
    pub detail: String,
}

impl From<RemoteError> for BrainError {
    fn from(e: RemoteError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

/// Other machines: their brains, their teams, and calls routed to them.
pub struct Remotes {}

impl Remotes {
    pub fn new(rt: Runtime) -> Remotes {
        todo!("remote.Remotes.new")
    }

    pub fn machines(&self) -> Vec<Value> {
        todo!("remote.Remotes.machines")
    }

    pub fn remote_rows(&self) -> Vec<Value> {
        todo!("remote.Remotes.remote_rows")
    }

    pub fn teams(&self) -> Vec<Value> {
        todo!("remote.Remotes.teams")
    }

    /// (exit code, output) of one line run on that machine.
    pub fn exec(&self, machine: &str, line: &str, timeout_s: f64, stdin: &[u8]) -> Result<(i32, String), RemoteError> {
        todo!("remote.Remotes.exec")
    }

    pub fn call(
        &self,
        machine: &str,
        method: &str,
        params: Option<&Value>,
        timeout_s: Option<f64>,
    ) -> Result<Value, RemoteError> {
        todo!("remote.Remotes.call")
    }

    /// The table with the methods another machine may answer routed there.
    pub fn wrap(this: Arc<Remotes>, handlers: Handlers) -> Handlers {
        todo!("remote.Remotes.wrap")
    }

    pub fn check(&self, machine: &str) -> Result<Value, RemoteError> {
        todo!("remote.Remotes.check")
    }

    pub fn test(&self, target: &str) -> Result<Value, RemoteError> {
        todo!("remote.Remotes.test")
    }

    pub fn setup(&self, target: &str) -> Result<Value, RemoteError> {
        todo!("remote.Remotes.setup")
    }

    pub fn start_brain(&self, machine: &str, restart: bool) -> Result<(), RemoteError> {
        todo!("remote.Remotes.start_brain")
    }

    pub fn connect(&self, machine: &str) -> Result<Value, RemoteError> {
        todo!("remote.Remotes.connect")
    }

    pub fn forget(&self, machine: &str) -> Value {
        todo!("remote.Remotes.forget")
    }

    pub fn open_terminal(&self, target: Option<&str>, machine: Option<&str>) -> Result<Value, RemoteError> {
        todo!("remote.Remotes.open_terminal")
    }

    pub fn machines_view(&self) -> Vec<Value> {
        todo!("remote.Remotes.machines_view")
    }

    pub fn handlers(this: Arc<Remotes>) -> Handlers {
        todo!("remote.Remotes.handlers")
    }

    pub fn tick(&self, watching: bool) {
        todo!("remote.Remotes.tick")
    }
}

/// `assieme call <method>`: one request to this machine's brain. Returns the exit code.
pub fn call_main(method: &str, sock: Option<&Path>) -> i32 {
    todo!("remote.call_main")
}

/// `remote.version` and `remote.repos`.
pub fn remote_handlers() -> Handlers {
    todo!("remote.remote_handlers")
}
