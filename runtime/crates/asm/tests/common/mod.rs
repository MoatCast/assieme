//! An isolated environment for every test that starts `asm`: a cleared
//! environment, a temporary HOME and XDG folders, `ASM_SESSION=test`.
#![allow(dead_code)]

use std::path::PathBuf;
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};

pub const BIN: &str = env!("CARGO_BIN_EXE_asm");

pub struct Env {
    pub root: PathBuf,
}

impl Drop for Env {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

impl Env {
    /// `asm` with this environment and nothing inherited but PATH.
    pub fn cmd(&self) -> Command {
        let mut cmd = Command::new(BIN);
        cmd.env_clear()
            .env("PATH", std::env::var("PATH").unwrap_or_default())
            .env("HOME", &self.root)
            .env("ASM_SESSION", "test")
            .env("XDG_RUNTIME_DIR", self.root.join("run"))
            .env("XDG_STATE_HOME", self.root.join("state"))
            .env("XDG_CONFIG_HOME", self.root.join("config"));
        cmd
    }
}

/// /var/tmp keeps socket paths under the macOS limit.
pub fn isolated() -> Env {
    static N: AtomicU32 = AtomicU32::new(0);
    let base = std::env::var_os("ASM_TEST_TMPDIR").map_or_else(|| PathBuf::from("/var/tmp"), Into::into);
    let root = base.join(format!("asm-brain-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
    let _ = std::fs::remove_dir_all(&root);
    for dir in ["run", "state", "config"] {
        std::fs::create_dir_all(root.join(dir)).unwrap();
    }
    Env { root }
}

pub fn asm(env: &Env, args: &[&str]) -> Output {
    env.cmd().args(args).stdin(Stdio::null()).output().unwrap()
}
