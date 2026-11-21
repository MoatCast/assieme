//! Assieme's service after the cutover: the login job `dev.assieme.service` runs `asm server start` once at login,
//! and the `assieme` core starts the brain because it finds `brain.json`. Install and health check, seen from the app.
use crate::bridge;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

pub const LABEL: &str = "dev.assieme.service";
/// The Python brain's login job, booted out and removed by every install.
pub const OLD_LABEL: &str = "dev.assieme.brain";
const BOOTSTRAP_TRIES: u32 = 20; // half a second apart: bootout returns while the old job is still stopping
const LOG_LIMIT: u64 = 5 << 20;

/// Where everything lives, from the app's Resources folder and the person's home.
pub struct Layout {
    pub resources: PathBuf,
    pub home: PathBuf,
    /// `$XDG_STATE_HOME/assieme`, else `~/.local/state/assieme`.
    pub state: PathBuf,
}

impl Layout {
    pub fn new(resources: PathBuf, home: PathBuf, xdg_state_home: Option<String>) -> Layout {
        let base = xdg_state_home.filter(|v| !v.is_empty()).map_or_else(|| home.join(".local/state"), PathBuf::from);
        Layout { resources, home, state: base.join("assieme") }
    }

    /// The job's program: it hands off to the installed core when there is one.
    pub fn program(&self) -> PathBuf {
        self.resources.join("asm-bin/asm")
    }

    pub fn plist(&self) -> PathBuf {
        self.home.join(format!("Library/LaunchAgents/{LABEL}.plist"))
    }

    pub fn core_socket(&self) -> PathBuf {
        self.state.join("asm.sock")
    }

    /// PATH for the job, the brain and every `asm` the app runs: the quiet-send stand-in, the app's asm-bin, the login PATH.
    fn path(&self, login_path: &str) -> String {
        format!("{}:{}:{login_path}", self.state.join("bin").display(), self.resources.join("asm-bin").display())
    }
}

/// The allow-listed environment, never the app's own.
pub fn environment(layout: &Layout, login_path: &str, var: impl Fn(&str) -> Option<String>) -> Vec<(String, String)> {
    let mut env = vec![
        ("ASM_SESSION".to_string(), "assieme".to_string()),
        ("ASM_SOCKET".into(), layout.core_socket().display().to_string()),
        ("HOME".into(), layout.home.display().to_string()),
        ("PATH".into(), layout.path(login_path)),
    ];
    for name in ["USER", "LANG", "XDG_STATE_HOME"] {
        if let Some(value) = var(name).filter(|v| !v.is_empty()) {
            env.push((name.into(), value));
        }
    }
    env
}

fn xml(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

fn program_line(program: &Path) -> String {
    format!("<key>ProgramArguments</key><array><string>{}</string>", xml(&program.display().to_string()))
}

/// The login job: `asm server start` once at login (RunAtLoad, no KeepAlive: the holder and the app restart the core).
pub fn plist(layout: &Layout, env: &[(String, String)]) -> String {
    let log = xml(&layout.state.join("service.log").display().to_string());
    let vars: String =
        env.iter().map(|(k, v)| format!("<key>{}</key><string>{}</string>", xml(k), xml(v))).collect::<Vec<_>>().join("");
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\"><dict>\n\
         <key>Label</key><string>{LABEL}</string>\n\
         {}<string>server</string><string>start</string></array>\n\
         <key>RunAtLoad</key><true/>\n\
         <key>EnvironmentVariables</key><dict>{vars}</dict>\n\
         <key>StandardOutPath</key><string>{log}</string>\n\
         <key>StandardErrorPath</key><string>{log}</string>\n\
         </dict></plist>\n",
        program_line(&layout.program())
    )
}

/// `brain.json`: the PATH the core gives its brain.
#[derive(serde::Serialize, serde::Deserialize)]
struct BrainJson {
    path: String,
}

/// The app moved, was renamed or updated: the job or `brain.json` names another Resources folder.
pub fn needs_install(layout: &Layout) -> bool {
    let job_ok = std::fs::read_to_string(layout.plist()).is_ok_and(|p| p.contains(&program_line(&layout.program())));
    let brain_ok = std::fs::read(layout.state.join("brain.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<BrainJson>(&b).ok())
        .is_some_and(|b| b.path.starts_with(&layout.path("")));
    !(job_ok && brain_ok)
}

fn write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let at = |e: std::io::Error| format!("can't write {}: {e}", path.display());
    std::fs::create_dir_all(path.parent().unwrap_or(Path::new("/"))).map_err(at)?;
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, bytes).map_err(at)?;
    std::fs::rename(&tmp, path).map_err(at)
}

/// The core's brain is up or on its way: `starting` and `waiting` count as running, so a normal start never asks.
fn brain_ok(status: &serde_json::Value) -> bool {
    matches!(status["brain"]["state"].as_str(), Some("starting" | "running" | "waiting"))
}

/// The health check: the core answers and its brain is up or on its way; a running brain must answer too.
pub fn running(layout: &Layout) -> bool {
    let Ok(status) = bridge::call_at_with(&layout.core_socket(), "server.status", serde_json::json!({}), Duration::from_secs(2))
    else {
        return false;
    };
    brain_ok(&status)
        && (status["brain"]["state"] != "running"
            || answers(&layout.state.join("brain.sock"), Duration::from_secs(1)))
}

/// The service at `sock` answers within `timeout`: any reply line counts, an error reply included. A service that
/// launchd has loaded but that is stuck passes `running` and fails this.
pub fn answers(sock: &Path, timeout: Duration) -> bool {
    let Ok(mut s) = UnixStream::connect(sock) else { return false };
    if s.set_read_timeout(Some(timeout)).is_err() || s.set_write_timeout(Some(timeout)).is_err() {
        return false;
    }
    // An unknown method: the brain replies with an error at once and does no work.
    if s.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"service.ping\"}\n").is_err() {
        return false;
    }
    let mut line = String::new();
    matches!(BufReader::new(&s).read_line(&mut line), Ok(n) if n > 0)
}

/// The PATH a login shell has. An app opened from Finder only has /usr/bin:/bin:..., which would hide
/// claude, codex and herdr from the background brain.
pub fn login_path(shell: &str, current: &str) -> String {
    let from_shell = Command::new(shell)
        .args(["-lc", "printf %s \"$PATH\""])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|p| !p.is_empty());
    from_shell.unwrap_or_else(|| {
        let home = std::env::var("HOME").unwrap_or_default();
        format!("{current}:/opt/homebrew/bin:/usr/local/bin:{home}/.local/bin:{home}/.cargo/bin")
    })
}

/// Runs `program` with exactly `env`; its stderr is the error.
fn run(program: &Path, args: &[&std::ffi::OsStr], env: &[(String, String)]) -> Result<(), String> {
    let out = Command::new(program)
        .args(args)
        .env_clear()
        .envs(env.iter().map(|(k, v)| (k, v)))
        .output()
        .map_err(|e| format!("can't run {}: {e}", program.display()))?;
    if out.status.success() {
        return Ok(());
    }
    Err(format!("{} failed: {}", program.display(), String::from_utf8_lossy(&out.stderr).trim()))
}

/// Install Assieme's service; safe to repeat. Boots out both jobs, bootstraps the new one, runs `asm server start` and
/// waits for the core, then asks for `brain.restart` only when the brain is down or absent.
pub fn install(layout: &Layout, login_path: &str, launchctl: &str, var: impl Fn(&str) -> Option<String>) -> Result<(), String> {
    let program = layout.program();
    if !program.exists() {
        return Err(format!("Assieme's runtime is missing at {}; rebuild the app with app/scripts/bundle.sh", program.display()));
    }
    let core = layout.resources.join("asm/Assieme Core.app/Contents/MacOS/assieme-core");
    if !core.exists() {
        return Err(format!("Assieme's core is missing at {}; rebuild the app with app/scripts/bundle.sh", core.display()));
    }
    let env = environment(layout, login_path, var);
    // `asm` hands off to the installed `current` core, and an older one has no brain supervision: update it first.
    let dest = layout.home.join(".local/bin/assieme-core");
    run(&core, &["--asm-install-bundle".as_ref(), core.as_os_str(), dest.as_os_str()], &env)?;
    let path = env.iter().find(|(k, _)| k == "PATH").expect("PATH is in the list").1.clone();
    write(&layout.state.join("brain.json"), &serde_json::to_vec(&BrainJson { path }).expect("plain strings"))?;
    let log = layout.state.join("service.log");
    if std::fs::metadata(&log).is_ok_and(|m| m.len() > LOG_LIMIT) {
        let _ = std::fs::rename(&log, layout.state.join("service.log.1"));
    }
    write(&layout.plist(), plist(layout, &env).as_bytes())?;
    // Or the Python brain comes back at the next login.
    let _ = std::fs::remove_file(layout.home.join(format!("Library/LaunchAgents/{OLD_LABEL}.plist")));
    let uid = Command::new("id").arg("-u").output().map_err(|e| e.to_string())?.stdout;
    let domain = format!("gui/{}", String::from_utf8_lossy(&uid).trim());
    for label in [OLD_LABEL, LABEL] {
        let _ = Command::new(launchctl).args(["bootout", &format!("{domain}/{label}")]).output(); // not loaded is fine
    }
    let mut tries = 0;
    loop {
        let out = Command::new(launchctl)
            .args(["bootstrap", &domain])
            .arg(layout.plist())
            .output()
            .map_err(|e| format!("can't run launchctl: {e}"))?;
        if out.status.success() {
            break;
        }
        tries += 1;
        if tries >= BOOTSTRAP_TRIES {
            return Err(format!(
                "launchctl bootstrap failed ({}): {}",
                out.status.code().unwrap_or(-1),
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    run(&program, &["server".as_ref(), "start".as_ref()], &env)?;
    let status = bridge::call_at(&layout.core_socket(), "server.status", serde_json::json!({}))?;
    if status["brain"].is_null() || status["brain"]["state"] == "down" {
        bridge::call_at(&layout.core_socket(), "brain.restart", serde_json::json!({}))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    use std::io::{BufRead, BufReader, Write};
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;
    use std::sync::{Arc, Mutex};

    fn root(name: &str) -> PathBuf {
        let d = PathBuf::from("/tmp").join(format!("asv-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn script(path: &Path, body: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }

    fn layout(root: &Path, app: &str) -> Layout {
        Layout::new(root.join(app).join("Contents/Resources"), root.join("home"), None)
    }

    fn var(name: &str) -> Option<String> {
        match name {
            "USER" => Some("me".into()),
            "SECRET" => Some("leaked".into()),
            _ => None,
        }
    }

    fn core(l: &Layout) -> PathBuf {
        l.resources.join("asm/Assieme Core.app/Contents/MacOS/assieme-core")
    }

    /// A core that answers server.status with `brain` and records every method.
    fn fake_core(sock: &Path, brain: Value) -> Arc<Mutex<Vec<String>>> {
        let calls = Arc::new(Mutex::new(vec![]));
        let listener = UnixListener::bind(sock).unwrap();
        let seen = calls.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let mut r = BufReader::new(stream);
                let mut line = String::new();
                if r.read_line(&mut line).unwrap_or(0) == 0 {
                    continue;
                }
                let req: Value = serde_json::from_str(&line).unwrap();
                let method = req["method"].as_str().unwrap().to_string();
                seen.lock().unwrap().push(method.clone());
                let result = if method == "server.status" { json!({"brain": brain}) } else { json!({"state": "starting"}) };
                let _ = r.get_mut().write_all(format!("{}\n", json!({"id": 1, "result": result})).as_bytes());
            }
        });
        calls
    }

    #[test]
    fn the_environment_is_an_allow_list() {
        let l = Layout::new("/A.app/Contents/Resources".into(), "/Users/me".into(), Some("/x/state".into()));
        let env = environment(&l, "/opt/homebrew/bin:/usr/bin", var);
        let get = |k: &str| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str());
        assert_eq!(get("PATH"), Some("/x/state/assieme/bin:/A.app/Contents/Resources/asm-bin:/opt/homebrew/bin:/usr/bin"));
        assert_eq!(get("ASM_SOCKET"), Some("/x/state/assieme/asm.sock"));
        assert_eq!(get("ASM_SESSION"), Some("assieme"));
        assert_eq!(get("HOME"), Some("/Users/me"));
        assert_eq!(get("USER"), Some("me"));
        assert_eq!(get("SECRET"), None);
        assert_eq!(env.len(), 5, "{env:?}");
    }

    #[test]
    fn the_job_starts_the_server_once_at_login() {
        let l = Layout::new("/My <App>.app/Contents/Resources".into(), "/Users/me".into(), None);
        let p = plist(&l, &[("PATH".into(), "/a&b".into())]);
        assert!(p.contains("<key>Label</key><string>dev.assieme.service</string>"), "{p}");
        assert!(
            p.contains("<string>/My &lt;App&gt;.app/Contents/Resources/asm-bin/asm</string><string>server</string><string>start</string>"),
            "{p}"
        );
        assert!(p.contains("<key>RunAtLoad</key><true/>"));
        assert!(!p.contains("KeepAlive"));
        assert!(p.contains("<key>PATH</key><string>/a&amp;b</string>"));
        assert!(p.contains("/Users/me/.local/state/assieme/service.log"));
        let check = Command::new("plutil").arg("-lint").arg("-").stdin(std::process::Stdio::piped()).spawn();
        if let Ok(mut lint) = check {
            lint.stdin.take().unwrap().write_all(p.as_bytes()).unwrap();
            assert!(lint.wait().unwrap().success(), "plutil rejects {p}");
        }
    }

    #[test]
    fn install_is_safe_to_repeat_and_restarts_only_a_missing_brain() {
        let r = root("install");
        let l = layout(&r, "Assieme.app");
        assert!(install(&l, "/usr/bin", "/usr/bin/true", var).unwrap_err().contains("bundle.sh"));
        std::fs::create_dir_all(&l.state).unwrap();
        let log = r.join("log");
        script(&core(&l), &format!(r#"echo "core $*" >> {}"#, log.display()));
        script(&l.program(), &format!(r#"echo "asm $* USER=$USER SECRET=$SECRET PATH=$PATH" >> {}"#, log.display()));
        let launchctl = r.join("launchctl");
        script(&launchctl, &format!(r#"echo "launchctl $*" >> {}"#, log.display()));
        std::fs::create_dir_all(r.join("home/Library/LaunchAgents")).unwrap();
        std::fs::write(r.join("home/Library/LaunchAgents/dev.assieme.brain.plist"), "old").unwrap();
        let calls = fake_core(&l.core_socket(), Value::Null);
        assert!(needs_install(&l));

        install(&l, "/usr/bin", launchctl.to_str().unwrap(), var).unwrap();
        let got = std::fs::read_to_string(&log).unwrap();
        let lines: Vec<&str> = got.lines().collect();
        assert!(lines[0].starts_with("core --asm-install-bundle "), "{got}");
        assert!(lines[1].starts_with("launchctl bootout gui/") && lines[1].ends_with("/dev.assieme.brain"), "{got}");
        assert!(lines[2].ends_with("/dev.assieme.service"), "{got}");
        assert!(lines[3].starts_with("launchctl bootstrap gui/") && lines[3].ends_with("dev.assieme.service.plist"), "{got}");
        assert!(lines[4].starts_with("asm server start USER=me SECRET= PATH="), "{got}");
        assert!(lines[4].contains("/Assieme.app/Contents/Resources/asm-bin:/usr/bin"), "{got}");
        assert!(!r.join("home/Library/LaunchAgents/dev.assieme.brain.plist").exists(), "the old job is gone for good");
        assert_eq!(*calls.lock().unwrap(), ["server.status", "brain.restart"], "absent brain: restarted");
        assert!(!needs_install(&l));

        // Again: only a brain that is down is restarted.
        for (state, want) in [("waiting", &["server.status"][..]), ("down", &["server.status", "brain.restart"])] {
            std::fs::remove_file(l.core_socket()).unwrap();
            let calls = fake_core(&l.core_socket(), json!({ "state": state }));
            install(&l, "/usr/bin", launchctl.to_str().unwrap(), var).unwrap();
            assert_eq!(*calls.lock().unwrap(), want, "{state}");
        }

        // The app moved: both the job and brain.json name the old place.
        assert!(needs_install(&layout(&r, "Moved.app")));
        let _ = std::fs::remove_dir_all(&r);
    }

    #[test]
    fn starting_and_waiting_count_as_running() {
        for (state, ok) in [("starting", true), ("running", true), ("waiting", true), ("down", false), ("later", false)] {
            assert_eq!(brain_ok(&json!({"brain": {"state": state}})), ok, "{state}");
        }
        assert!(!brain_ok(&json!({"brain": null})));
        let r = root("health");
        let l = layout(&r, "Assieme.app");
        assert!(!running(&l), "no core");
        std::fs::create_dir_all(&l.state).unwrap();
        fake_core(&l.core_socket(), json!({"state": "starting"}));
        assert!(running(&l));
        let _ = std::fs::remove_dir_all(&r);
    }
    fn listener(name: &str) -> (std::os::unix::net::UnixListener, PathBuf) {
        let p = std::env::temp_dir().join(format!("assieme-svc-{}-{name}.sock", std::process::id()));
        let _ = std::fs::remove_file(&p);
        (std::os::unix::net::UnixListener::bind(&p).unwrap(), p)
    }

    #[test]
    fn answers_false_when_socket_silent() {
        let (l, p) = listener("silent");
        let t = std::thread::spawn(move || {
            let (s, _) = l.accept().unwrap();
            std::thread::sleep(Duration::from_secs(3)); // accepts, never replies
            drop(s);
        });
        let start = std::time::Instant::now();
        assert!(!answers(&p, Duration::from_secs(1)));
        assert!(start.elapsed() < Duration::from_millis(1500));
        drop(t);
    }

    #[test]
    fn answers_true_on_any_reply() {
        let (l, p) = listener("error");
        std::thread::spawn(move || {
            let (mut s, _) = l.accept().unwrap();
            let mut line = String::new();
            BufReader::new(&s).read_line(&mut line).unwrap();
            s.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"error\":{\"code\":-32601,\"message\":\"no method\"}}\n").unwrap();
        });
        assert!(answers(&p, Duration::from_secs(1)));
    }

    #[test]
    fn answers_false_when_no_socket() {
        assert!(!answers(Path::new("/nonexistent/assieme-brain.sock"), Duration::from_secs(1)));
    }

    #[test]
    fn login_path_comes_from_the_login_shell() {
        let r = root("loginsh");
        let shell = r.join("loginsh");
        script(&shell, r#"[ "$1" = -lc ] && printf '/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin'"#);
        assert_eq!(login_path(shell.to_str().unwrap(), "/usr/bin:/bin"), "/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin");
    }

    #[test]
    fn login_path_falls_back_with_common_folders() {
        let p = login_path("/nope/shell", "/usr/bin:/bin");
        assert!(p.starts_with("/usr/bin:/bin:"), "{p}");
        assert!(p.contains("/opt/homebrew/bin") && p.contains("/.local/bin"), "{p}");
    }

    #[test]
    fn install_leaves_one_brain() {
        let r = root("one");
        let l = layout(&r, "Assieme.app");
        std::fs::create_dir_all(&l.state).unwrap();
        let log = r.join("log");
        script(&l.program(), "exit 0");
        script(&core(&l), "exit 0");
        // The old job is loaded while its plist exists; bootout unloads it.
        let launchctl = r.join("launchctl");
        script(&launchctl, &format!(r#"echo "$*" >> {}"#, log.display()));
        let old = r.join("home/Library/LaunchAgents/dev.assieme.brain.plist");
        std::fs::create_dir_all(old.parent().unwrap()).unwrap();
        std::fs::write(&old, "old").unwrap();
        fake_core(&l.core_socket(), json!({"state": "running"}));
        install(&l, "/usr/bin", launchctl.to_str().unwrap(), var).unwrap();
        let got = std::fs::read_to_string(&log).unwrap();
        let (out, new) = (got.find("/dev.assieme.brain").expect("old job booted out"), got.find("bootstrap").unwrap());
        assert!(got.contains("bootout gui/") && out < new, "{got}");
        assert!(got.contains("/dev.assieme.service.plist"), "{got}");
        assert!(!old.exists());
    }

    #[test]
    fn install_updates_the_current_core_first() {
        let r = root("current");
        let l = layout(&r, "Assieme.app");
        std::fs::create_dir_all(&l.state).unwrap();
        let log = r.join("log");
        script(&l.program(), &format!(r#"echo "server $*" >> {}"#, log.display()));
        script(&core(&l), &format!(r#"echo "core $*" >> {}"#, log.display()));
        let calls = fake_core(&l.core_socket(), Value::Null);
        install(&l, "/usr/bin", "/usr/bin/true", var).unwrap();
        let got = std::fs::read_to_string(&log).unwrap();
        let want = format!("core --asm-install-bundle {0} {1}/home/.local/bin/assieme-core\nserver server start\n", core(&l).display(), r.display());
        assert_eq!(got, want);
        assert_eq!(*calls.lock().unwrap(), ["server.status", "brain.restart"], "the restart comes after the install");
    }
}
