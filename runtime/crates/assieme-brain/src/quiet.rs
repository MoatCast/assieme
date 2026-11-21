//! Port of `assieme/quiet.py`, owned by session C (see OWNERS.md).
//!
//! A3 quiet send: routine notes between agents wait for the receiver's next turn instead of waking it.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Remove exactly the notes that were sent; one that arrived meanwhile stays for the next round.
pub fn drop(delivered: &[Value]) {
    todo!("quiet._drop")
}

/// A file in the state folder.
pub fn path(parts: &[&str]) -> PathBuf {
    todo!("quiet._path")
}

/// The JSON in `path`, or `default` when it is missing or damaged.
pub fn read_json(path: &Path, default: Value) -> Value {
    let Ok(bytes) = std::fs::read(path) else { return default };
    let Ok(text) = String::from_utf8(bytes) else { return default };
    crate::pyrules::json_loads(&text).unwrap_or(default)
}

/// Folders are compared resolved: /var and /private/var, or a symlinked checkout, are the same place.
pub fn real(path: &str) -> String {
    todo!("quiet._real")
}

/// Written whole or not at all: a reader never sees half a file.
pub fn write_json(path: &Path, data: &Value) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    assieme_core::paths::write_atomic(path, &serde_json::to_vec(data)?)
}

/// Held while changing held.json: shims in several agents and the deliverer take turns.
pub fn locked() -> std::io::Result<Locked> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let file = std::fs::File::create(dir.join("held.lock"))?;
    // flock, as Python's fcntl.flock: the Python shims and the Rust brain take the same lock.
    if unsafe { libc::flock(std::os::fd::AsRawFd::as_raw_fd(&file), libc::LOCK_EX) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(Locked(file))
}

/// The lock on `held.lock`, released when dropped (closing the file releases it).
pub struct Locked(pub std::fs::File);

/// Send held notes. Returns what was sent.
pub fn deliver(
    agents: &[Agent],
    now: Option<Timestamp>,
    on_batch: Option<&dyn Fn(&Value)>,
    on_give_up: Option<&dyn Fn(&Value)>,
) -> Vec<Value> {
    todo!("quiet.deliver")
}

pub fn deliverer_alive(now: Option<Timestamp>) -> bool {
    todo!("quiet.deliverer_alive")
}

/// {to_cwd: [note, ...]} of notes waiting for delivery.
pub fn held() -> BTreeMap<String, Vec<Value>> {
    todo!("quiet.held")
}

/// A note from Assieme itself or an agent's message tool, delivered with the agent's next turn.
#[allow(clippy::too_many_arguments)]
pub fn hold_note(
    to: &str,
    to_cwd: &str,
    text: &str,
    session: Option<&str>,
    socket: Option<&Path>,
    now: Option<Timestamp>,
    sender: &str,
    from_cwd: &str,
) {
    todo!("quiet.hold_note")
}

/// Write the asm stand-in. Returns its folder, which goes first on the agents' PATH.
pub fn install() -> std::io::Result<PathBuf> {
    todo!("quiet.install")
}

pub fn is_quiet(from_cwd: &str, to_cwd: &str) -> bool {
    todo!("quiet.is_quiet")
}

pub fn opted_out(from_cwd: &str, to_cwd: &str) -> bool {
    todo!("quiet.opted_out")
}

/// Quiet pairs as [{from_cwd, to_cwd, label}].
pub fn pairs() -> Vec<Value> {
    todo!("quiet.pairs")
}

/// Share of the team's tokens a quiet pair saved.
pub fn savings(pair: &Value, entries: &[Value], edge: &Value, total_tokens: i64) -> f64 {
    todo!("quiet.savings")
}

pub fn set_pair(from_cwd: &str, to_cwd: &str, label: &str, on: bool) {
    todo!("quiet.set_pair")
}

pub fn shim_dir() -> PathBuf {
    todo!("quiet.shim_dir")
}

/// `asm ...` as the agents see it. Returns the exit code.
pub fn shim_main(argv: &[String], cwd: Option<&str>, now: Option<Timestamp>) -> i32 {
    todo!("quiet.shim_main")
}

/// `$XDG_STATE_HOME/assieme`, else `~/.local/state/assieme`.
pub fn state_dir() -> PathBuf {
    match std::env::var_os("XDG_STATE_HOME").filter(|x| !x.is_empty()) {
        Some(base) => PathBuf::from(base).join("assieme"),
        None => {
            // Python's expanduser: HOME without its trailing slashes.
            let home = std::env::home_dir().unwrap_or_default();
            let home = home.to_str().map_or(home.clone(), |h| PathBuf::from(h.trim_end_matches('/')));
            PathBuf::from(format!("{}/.local/state/assieme", home.display()))
        }
    }
}

pub fn uninstall() {
    todo!("quiet.uninstall")
}

pub fn urgent(text: &str) -> bool {
    todo!("quiet.urgent")
}
