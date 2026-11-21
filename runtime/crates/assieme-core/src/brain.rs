//! The brain the `assieme` core starts and keeps running (`<this exe> brain`).
//! Only a core whose session is `assieme` and that finds `brain.json` runs one,
//! decided here and never by a flag, so every way a core starts keeps it.

use crate::rpc::RpcError;
use schemars::JsonSchema;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const SESSION: &str = "assieme";
const FIRST_DELAY: Duration = Duration::from_secs(1);
const LONGEST_DELAY: Duration = Duration::from_secs(60);
/// A run this long resets the delay.
const SETTLED: Duration = Duration::from_secs(60);
const FAILURES: usize = 5;
const FAILURE_WINDOW: Duration = Duration::from_secs(600);
const LOG_LIMIT: u64 = 5 << 20;

/// `server.status.brain`. The state is a plain string so a new one never breaks a client.
#[derive(Serialize, JsonSchema, Clone, Debug, PartialEq)]
pub struct BrainStatus {
    /// starting, running, waiting (to start again after an exit) or down (gave up; `brain.restart` tries again).
    pub state: String,
    pub pid: Option<u32>,
}

static STATUS: Mutex<Option<BrainStatus>> = Mutex::new(None);
static RESTART: tokio::sync::Notify = tokio::sync::Notify::const_new();

/// What `server.status` reports: none when this core runs no brain.
pub fn status() -> Option<BrainStatus> {
    STATUS.lock().unwrap_or_else(|p| p.into_inner()).clone()
}

fn set(state: &str, pid: Option<u32>) {
    *STATUS.lock().unwrap_or_else(|p| p.into_inner()) = Some(BrainStatus { state: state.into(), pid });
}

/// `$XDG_STATE_HOME/assieme`, else `~/.local/state/assieme`: the brain's own folder.
pub fn dir() -> PathBuf {
    std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/state"))
        .join("assieme")
}

#[derive(serde::Deserialize)]
struct BrainJson {
    path: String,
}

/// `brain.json`'s PATH; none when the file is missing or unreadable.
fn configured(dir: &Path) -> Option<String> {
    let file = dir.join("brain.json");
    let bytes = std::fs::read(&file).ok()?;
    serde_json::from_slice::<BrainJson>(&bytes)
        .inspect_err(|e| eprintln!("core: {}: {e}: no brain", file.display()))
        .ok()
        .map(|b| b.path)
}

/// `brain.restart`: read `brain.json` again and start a fresh brain. A brain it stops is not a failure.
pub fn restart(session: &str) -> Result<BrainStatus, RpcError> {
    if session != SESSION {
        return Err(RpcError::new(
            "unavailable",
            format!("this core runs no brain: its session is {session}"),
            "the brain runs in the assieme session only",
        ));
    }
    let dir = dir();
    if configured(&dir).is_none() {
        return Err(RpcError::new(
            "not_found",
            format!("no brain configured: {} is missing", dir.join("brain.json").display()),
            "install Assieme's service from the app",
        ));
    }
    RESTART.notify_one();
    Ok(BrainStatus { state: "starting".into(), pid: None })
}

/// The restart delays: from 1 s, doubling to 60 s; a run of 60 s or more resets them;
/// the fifth exit within 10 minutes gives up.
#[derive(Default)]
struct Backoff {
    next: Option<Duration>,
    exits: Vec<Instant>,
}

impl Backoff {
    /// After an exit at `now` of a run that lasted `ran`: how long to wait, or none to give up.
    fn exited(&mut self, ran: Duration, now: Instant) -> Option<Duration> {
        if ran >= SETTLED {
            self.next = None;
        }
        self.exits.retain(|at| now.duration_since(*at) < FAILURE_WINDOW);
        self.exits.push(now);
        if self.exits.len() >= FAILURES {
            return None;
        }
        let delay = self.next.unwrap_or(FIRST_DELAY);
        self.next = Some((delay * 2).min(LONGEST_DELAY));
        Some(delay)
    }
}

/// Only what the brain needs, never the core's own environment.
fn environment(path: &str, core_socket: &Path, var: impl Fn(&str) -> Option<String>) -> Vec<(String, String)> {
    let mut env = vec![
        ("PATH".to_string(), path.to_string()),
        ("ASM_SESSION".into(), SESSION.into()),
        ("ASM_SOCKET".into(), core_socket.display().to_string()),
    ];
    for name in ["HOME", "USER", "LANG", "XDG_STATE_HOME"] {
        if let Some(value) = var(name).filter(|v| !v.is_empty()) {
            env.push((name.into(), value));
        }
    }
    env
}

/// The log, renamed to `<name>.1` first when it is over 5 MB.
fn open_log(path: &Path) -> std::io::Result<std::fs::File> {
    if std::fs::metadata(path).is_ok_and(|m| m.len() > LOG_LIMIT) {
        let mut old = path.as_os_str().to_owned();
        old.push(".1");
        let _ = std::fs::rename(path, old);
    }
    std::fs::OpenOptions::new().create(true).append(true).open(path)
}

/// Starts supervising when this is the `assieme` core; once it serves.
pub fn start(session: &str, core_socket: &Path) {
    if session == SESSION {
        tokio::spawn(supervise(core_socket.to_path_buf()));
    }
}

fn spawn(path: &str, dir: &Path, core_socket: &Path) -> std::io::Result<tokio::process::Child> {
    crate::platform::private_dir(dir)?;
    let log = open_log(&dir.join("brain.log"))?;
    let exe = crate::client::exe()?;
    let mut cmd = tokio::process::Command::new(exe);
    cmd.arg("brain")
        .arg("--socket")
        .arg(dir.join("brain.sock"))
        .env_clear()
        .envs(environment(path, core_socket, |n| std::env::var(n).ok()))
        .current_dir(dir)
        // The brain exits when this pipe closes: with the core, however it ends.
        .stdin(std::process::Stdio::piped())
        .stdout(log.try_clone()?)
        .stderr(log);
    cmd.spawn()
}

#[cfg(unix)]
fn answers(sock: &Path) -> bool {
    std::os::unix::net::UnixStream::connect(sock).is_ok()
}

#[cfg(not(unix))]
fn answers(_: &Path) -> bool {
    false
}

enum Ended {
    Exited(Duration),
    Restart,
}

async fn supervise(core_socket: PathBuf) {
    let mut backoff = Backoff::default();
    let dir = dir();
    loop {
        let Some(path) = configured(&dir) else {
            *STATUS.lock().unwrap_or_else(|p| p.into_inner()) = None;
            RESTART.notified().await;
            continue;
        };
        set("starting", None);
        let started = Instant::now();
        let ended = match spawn(&path, &dir, &core_socket) {
            Err(e) => {
                eprintln!("core: cannot start the brain: {e}");
                Ended::Exited(Duration::ZERO)
            }
            Ok(mut child) => {
                let pid = child.id();
                // Held here: `wait` would close it, and the brain exits when it closes.
                let _pipe = child.stdin.take();
                set("starting", pid);
                let sock = dir.join("brain.sock");
                let mut answered = false;
                loop {
                    tokio::select! {
                        exit = child.wait() => {
                            eprintln!("core: the brain exited ({})", exit.map_or_else(|e| e.to_string(), |s| s.to_string()));
                            break Ended::Exited(started.elapsed());
                        }
                        () = RESTART.notified() => {
                            let _ = child.kill().await;
                            break Ended::Restart;
                        }
                        () = tokio::time::sleep(Duration::from_millis(100)), if !answered => {
                            if answers(&sock) {
                                answered = true;
                                set("running", pid);
                            }
                        }
                    }
                }
            }
        };
        let ran = match ended {
            Ended::Restart => {
                backoff = Backoff::default();
                continue;
            }
            Ended::Exited(ran) => ran,
        };
        match backoff.exited(ran, Instant::now()) {
            Some(delay) => {
                set("waiting", None);
                tokio::select! {
                    () = tokio::time::sleep(delay) => {}
                    () = RESTART.notified() => backoff = Backoff::default(),
                }
            }
            None => {
                eprintln!(
                    "core: the brain failed {FAILURES} times within 10 minutes: it stays down until brain.restart"
                );
                set("down", None);
                RESTART.notified().await;
                backoff = Backoff::default();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delays_double_to_a_minute_reset_after_a_long_run_and_give_up_at_five() {
        let s = Duration::from_secs;
        let t = Instant::now();
        // (delay before, ran, seconds since start, exits already in the window, expected)
        let cases = [
            (None, s(0), 0, 0, Some(s(1))),
            (Some(s(2)), s(0), 0, 1, Some(s(2))),
            (Some(s(32)), s(0), 0, 0, Some(s(32))),
            (Some(s(60)), s(0), 0, 0, Some(s(60))),
            (Some(s(8)), s(60), 0, 0, Some(s(1))),
            (Some(s(8)), s(0), 599, 4, None),
            (Some(s(8)), s(0), 600, 4, Some(s(8))),
        ];
        for (i, (next, ran, at, before, want)) in cases.into_iter().enumerate() {
            let mut b = Backoff { next, exits: vec![t; before] };
            assert_eq!(b.exited(ran, t + s(at)), want, "case {i}");
        }
    }

    #[test]
    fn the_brain_gets_only_its_allow_list() {
        let var = |n: &str| (n != "XDG_STATE_HOME").then(|| format!("<{n}>"));
        let mut env = environment("/a:/b", Path::new("/s/asm.sock"), var);
        env.sort();
        let names: Vec<_> = env.iter().map(|(k, v)| format!("{k}={v}")).collect();
        assert_eq!(
            names,
            [
                "ASM_SESSION=assieme",
                "ASM_SOCKET=/s/asm.sock",
                "HOME=<HOME>",
                "LANG=<LANG>",
                "PATH=/a:/b",
                "USER=<USER>"
            ]
        );
    }
}
