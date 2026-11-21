//! Assieme's service after the cutover: the login job `dev.assieme.service` runs `asm server start` once at login,
//! and the `assieme` core starts the brain because it finds `brain.json`. Install and health check, seen from the app.
use crate::bridge;
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

    fn old_plist(&self) -> PathBuf {
        self.home.join(format!("Library/LaunchAgents/{OLD_LABEL}.plist"))
    }

    pub fn core_socket(&self) -> PathBuf {
        self.state.join("asm.sock")
    }

    fn brain_json(&self) -> PathBuf {
        self.state.join("brain.json")
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

/// The app moved, was renamed or updated: the job or `brain.json` names another Resources folder.
pub fn needs_install(layout: &Layout) -> bool {
    let job_ok = std::fs::read_to_string(layout.plist()).is_ok_and(|p| p.contains(&program_line(&layout.program())));
    let brain_ok = std::fs::read(layout.brain_json())
        .ok()
        .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok())
        .and_then(|v| v["path"].as_str().map(String::from))
        .is_some_and(|path| path.starts_with(&layout.path("")));
    !(job_ok && brain_ok)
}

fn write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let at = |e: std::io::Error| format!("can't write {}: {e}", path.display());
    std::fs::create_dir_all(path.parent().unwrap_or(Path::new("/"))).map_err(at)?;
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, bytes).map_err(at)?;
    std::fs::rename(&tmp, path).map_err(at)
}

fn uid() -> Result<String, String> {
    let out = Command::new("id").arg("-u").output().map_err(|e| e.to_string())?;
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
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
            || crate::service::answers(&layout.state.join("brain.sock"), Duration::from_secs(1)))
}

/// Install Assieme's service; safe to repeat. Boots out both jobs, bootstraps the new one, runs `asm server start` and
/// waits for the core, then asks for `brain.restart` only when the brain is down or absent.
pub fn install(layout: &Layout, login_path: &str, launchctl: &str, var: impl Fn(&str) -> Option<String>) -> Result<(), String> {
    let program = layout.program();
    if !program.exists() {
        return Err(format!("Assieme's runtime is missing at {}; rebuild the app with app/scripts/bundle.sh", program.display()));
    }
    let env = environment(layout, login_path, var);
    let path = &env.iter().find(|(k, _)| k == "PATH").expect("PATH is in the list").1;
    write(&layout.brain_json(), serde_json::json!({ "path": path }).to_string().as_bytes())?;
    let log = layout.state.join("service.log");
    if std::fs::metadata(&log).is_ok_and(|m| m.len() > LOG_LIMIT) {
        let _ = std::fs::rename(&log, layout.state.join("service.log.1"));
    }
    write(&layout.plist(), plist(layout, &env).as_bytes())?;
    let _ = std::fs::remove_file(layout.old_plist()); // or the Python brain comes back at the next login
    let domain = format!("gui/{}", uid()?);
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
    let out = Command::new(&program)
        .args(["server", "start"])
        .env_clear()
        .envs(env.iter().map(|(k, v)| (k, v)))
        .output()
        .map_err(|e| format!("can't run {}: {e}", program.display()))?;
    if !out.status.success() {
        return Err(format!("asm server start failed: {}", String::from_utf8_lossy(&out.stderr).trim()));
    }
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
        std::fs::create_dir_all(&l.state).unwrap();
        let log = r.join("log");
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
        assert!(lines[0].starts_with("launchctl bootout gui/") && lines[0].ends_with("/dev.assieme.brain"), "{got}");
        assert!(lines[1].ends_with("/dev.assieme.service"), "{got}");
        assert!(lines[2].starts_with("launchctl bootstrap gui/") && lines[2].ends_with("dev.assieme.service.plist"), "{got}");
        assert!(lines[3].starts_with("asm server start USER=me SECRET= PATH="), "{got}");
        assert!(lines[3].contains("/Assieme.app/Contents/Resources/asm-bin:/usr/bin"), "{got}");
        assert!(!r.join("home/Library/LaunchAgents/dev.assieme.brain.plist").exists(), "the old job is gone for good");
        assert_eq!(*calls.lock().unwrap(), ["server.status", "brain.restart"], "absent brain: restarted");
        assert!(!needs_install(&l));
        let brain: Value = serde_json::from_slice(&std::fs::read(l.state.join("brain.json")).unwrap()).unwrap();
        assert!(brain["path"].as_str().unwrap().ends_with("asm-bin:/usr/bin"), "{brain}");

        // Again, with a brain on its way: nothing is restarted.
        std::fs::remove_file(l.core_socket()).unwrap();
        let calls = fake_core(&l.core_socket(), json!({"state": "waiting"}));
        install(&l, "/usr/bin", launchctl.to_str().unwrap(), var).unwrap();
        assert_eq!(*calls.lock().unwrap(), ["server.status"]);
        std::fs::remove_file(l.core_socket()).unwrap();
        let calls = fake_core(&l.core_socket(), json!({"state": "down"}));
        install(&l, "/usr/bin", launchctl.to_str().unwrap(), var).unwrap();
        assert_eq!(*calls.lock().unwrap(), ["server.status", "brain.restart"]);

        // The app moved: both the job and brain.json name the old place.
        assert!(needs_install(&layout(&r, "Moved.app")));
        let _ = std::fs::remove_dir_all(&r);
    }

    #[test]
    fn a_failing_bootstrap_or_start_is_reported() {
        let r = root("fail");
        let l = layout(&r, "Assieme.app");
        assert!(install(&l, "/usr/bin", "/usr/bin/true", var).unwrap_err().contains("bundle.sh"));
        script(&l.program(), "echo no core >&2; exit 1");
        let err = install(&l, "/usr/bin", "/usr/bin/true", var).unwrap_err();
        assert!(err.contains("no core"), "{err}");
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
}
