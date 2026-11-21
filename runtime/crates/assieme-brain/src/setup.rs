//! Port of `assieme/setup.py`, owned by session F (see OWNERS.md).
//!
//! First-run setup: Apple's developer tools, where the agents are installed, and whether they are signed in.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// A setup step failed, in words.
#[derive(Debug, Clone, PartialEq)]
pub enum SetupError {
    Failed(String),
    TooLarge(TooLarge),
    Unfinished(Unfinished),
}

/// The folder is too large to save as it is.
#[derive(Debug, Clone, PartialEq)]
pub struct TooLarge(pub String);

/// Saving the folder stopped part way.
#[derive(Debug, Clone, PartialEq)]
pub struct Unfinished(pub String);

impl From<SetupError> for BrainError {
    fn from(e: SetupError) -> BrainError {
        match e {
            SetupError::Failed(m) | SetupError::TooLarge(TooLarge(m)) | SetupError::Unfinished(Unfinished(m)) => {
                BrainError::new(m)
            }
        }
    }
}

/// Where an agent program is installed, or None: this process's PATH, the login shell's, then ~/.local/bin.
pub fn agent_path(name: &str) -> Option<PathBuf> {
    static LOGIN: Mutex<Option<(std::time::Instant, String)>> = Mutex::new(None);
    agent_path_with(name, &|| {
        let mut cache = LOGIN.lock().unwrap_or_else(|e| e.into_inner());
        match &*cache {
            Some((at, value)) if at.elapsed() < LOGIN_PATH_TTL => value.clone(),
            _ => cache.insert((std::time::Instant::now(), login_path())).1.clone(),
        }
    })
}

/// How long the login shell's PATH is kept before it is read again.
pub const LOGIN_PATH_TTL: std::time::Duration = std::time::Duration::from_secs(60);

/// `agent_path` with the login shell's PATH from `login` (read only when this process's PATH has no such program).
pub fn agent_path_with(name: &str, login: &dyn Fn() -> String) -> Option<PathBuf> {
    if let Some(found) = crate::pyrules::which(name) {
        return Some(found);
    }
    let local = format!("{}/.local/bin", crate::rules::home());
    for d in login().split(':').chain([local.as_str()]) {
        let p = Path::new(d).join(name);
        if !d.is_empty() && p.is_file() && crate::pyrules::executable(&p) {
            return Some(p);
        }
    }
    None
}

/// Marks the PATH line among whatever the person's rc files print.
const PATH_MARK: &str = "__assieme_path__=";

/// The PATH the person's login shell has; an app opened from Finder only has the short system one.
fn login_path() -> String {
    use std::process::{Command, Stdio};
    // -i too: nvm, where npm puts Claude Code and Codex, loads in .zshrc.
    let shell = std::env::var("SHELL").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| "/bin/zsh".into());
    let Ok(mut child) = Command::new(shell)
        .args(["-ilc", &format!("echo {PATH_MARK}$PATH")])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return String::new();
    };
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while child.try_wait().ok().flatten().is_none() {
        if std::time::Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            return String::new();
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    let Ok(out) = child.wait_with_output() else { return String::new() };
    let text = String::from_utf8_lossy(&out.stdout).into_owned();
    let last = crate::pyrules::splitlines(&text).into_iter().rev().find_map(|l| l.split_once(PATH_MARK));
    last.map_or(String::new(), |(_, path)| path.trim_matches(crate::pyrules::isspace).to_string())
}

pub fn folder_preview(project: &str) -> Result<Value, SetupError> {
    todo!("setup.folder_preview")
}

/// Git works (Apple's stub is only asked about, never run).
pub fn git_ready() -> bool {
    todo!("setup.git_ready")
}

/// The `setup.*` methods.
pub fn handlers(rt: Runtime) -> Handlers {
    todo!("setup.handlers")
}

/// The home folder or `/`, by resolved path.
pub fn too_broad(project: &str) -> bool {
    todo!("setup.too_broad")
}

/// How many files Save them first would commit.
pub fn uncommitted(project: &str) -> Result<usize, SetupError> {
    todo!("setup.uncommitted")
}
