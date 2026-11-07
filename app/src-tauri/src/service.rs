//! The brain's background service, seen from the app: is it running, and install it with the bundled Python.
use std::path::Path;
use std::process::Command;

pub const LABEL: &str = "dev.assieme.brain";

/// `launchctl print gui/<uid>/dev.assieme.brain` succeeds only when the service is loaded.
pub fn running(launchctl: &str) -> bool {
    let uid = Command::new("id").arg("-u").output().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    let Ok(uid) = uid else { return false };
    Command::new(launchctl)
        .args(["print", &format!("gui/{uid}/{LABEL}")])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
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

/// Run `<python> -P -m assieme service install`, with `hc_bin` first on PATH so the service records it.
pub fn install(python: &Path, hc_bin: &Path) -> Result<(), String> {
    if !python.exists() {
        return Err(format!("Assieme's Python is missing at {}; rebuild the app with app/scripts/bundle.sh", python.display()));
    }
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into());
    let path = format!("{}:{}", hc_bin.display(), login_path(&shell, &std::env::var("PATH").unwrap_or_default()));
    let out = Command::new(python)
        .args(["-P", "-m", "assieme", "service", "install"])
        .env("PATH", path)
        .output()
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr)).trim().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    fn script(name: &str, body: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("assieme-svc-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join(name);
        std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        p
    }

    #[test]
    fn service_running_reads_launchctl() {
        let up = script("up", r#"[ "$1" = print ] && case "$2" in gui/*/dev.assieme.brain) exit 0;; esac; exit 9"#);
        let down = script("down", "exit 113");
        assert!(running(up.to_str().unwrap()));
        assert!(!running(down.to_str().unwrap()));
    }

    #[test]
    fn login_path_comes_from_the_login_shell() {
        let shell = script("loginsh", r#"[ "$1" = -lc ] && printf '/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin'"#);
        assert_eq!(login_path(shell.to_str().unwrap(), "/usr/bin:/bin"), "/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin");
    }

    #[test]
    fn login_path_falls_back_with_common_folders() {
        let p = login_path("/nope/shell", "/usr/bin:/bin");
        assert!(p.starts_with("/usr/bin:/bin:"), "{p}");
        assert!(p.contains("/opt/homebrew/bin") && p.contains("/.local/bin"), "{p}");
    }

    #[test]
    fn service_install_runs_bundled_python() {
        let log = std::env::temp_dir().join(format!("assieme-svc-{}-log", std::process::id()));
        let _ = std::fs::remove_file(&log);
        let python = script("python3", &format!(r#"echo "$@ PATH=$PATH" > {}"#, log.display()));
        install(&python, Path::new("/bundle/hc-bin")).unwrap();
        let got = std::fs::read_to_string(&log).unwrap();
        assert!(got.starts_with("-P -m assieme service install PATH=/bundle/hc-bin:"), "{got}");
    }

    #[test]
    fn service_install_failure_has_the_output() {
        let python = script("bad", "echo boom >&2; exit 1");
        assert!(install(&python, Path::new("/x")).unwrap_err().contains("boom"));
    }

    #[test]
    fn missing_python_is_explained() {
        assert!(install(Path::new("/nope/python3"), Path::new("/x")).unwrap_err().contains("bundle.sh"));
    }
}
