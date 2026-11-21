//! Command line tools (Settings › General): a launcher named `assieme` that runs `asm cli` from the app. It goes in
//! ~/.local/bin when that folder is on the login PATH, else in /usr/local/bin through the Mac's password dialog.
//! Remove deletes only a launcher Assieme wrote, known by its marker line.
use std::path::{Path, PathBuf};
use std::process::Command;

pub const MARK: &str = "# written by Assieme";
const SYSTEM_DIR: &str = "/usr/local/bin";

#[derive(serde::Serialize, Debug, PartialEq)]
pub struct CliStatus {
    /// "missing", "installed", or "moved": Assieme's launcher points at an app that is no longer where it was.
    pub state: &'static str,
    pub path: Option<String>,
}

pub fn launcher(asm: &Path) -> String {
    format!("#!/bin/sh\n{MARK}\nexec {} cli \"$@\"\n", sh_quote(asm)) // quoted: an app folder may hold " or $
}

/// ~/.local/bin when the login PATH has it, else /usr/local/bin.
pub fn target(login_path: &str, home: &Path) -> PathBuf {
    let local = home.join(".local/bin");
    let on_path = login_path.split(':').any(|d| Path::new(d) == local);
    if on_path { local } else { PathBuf::from(SYSTEM_DIR) }.join("assieme")
}

fn ours(path: &Path) -> Option<String> {
    std::fs::read_to_string(path).ok().filter(|s| s.lines().any(|l| l == MARK))
}

pub fn status(home: &Path, asm: &Path) -> CliStatus {
    for p in [home.join(".local/bin/assieme"), PathBuf::from(SYSTEM_DIR).join("assieme")] {
        if let Some(text) = ours(&p) {
            let state = if text == launcher(asm) { "installed" } else { "moved" };
            return CliStatus { state, path: Some(p.display().to_string()) };
        }
    }
    CliStatus { state: "missing", path: None }
}

/// AppleScript's string literal for `s`.
fn quoted(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Runs `command` as root through the Mac's own password dialog.
fn as_admin(osascript: &str, command: &str) -> Result<(), String> {
    let script = format!("do shell script {} with administrator privileges", quoted(command));
    let out = Command::new(osascript).args(["-e", &script]).output().map_err(|e| e.to_string())?;
    if out.status.success() {
        return Ok(());
    }
    let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
    Err(if err.contains("-128") { "The Mac password was cancelled, so the command line tools were not installed.".into() } else { err })
}

fn sh_quote(p: &Path) -> String {
    format!("'{}'", p.display().to_string().replace('\'', "'\\''"))
}

pub fn install(asm: &Path, login_path: &str, home: &Path, osascript: &str) -> Result<CliStatus, String> {
    let to = target(login_path, home);
    if to.symlink_metadata().is_ok() && ours(&to).is_none() { // a link counts, even a broken one
        return Err(format!("{} already exists and was not written by Assieme. Remove it first to install the command line tools.", to.display()));
    }
    if to.starts_with(home) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(to.parent().unwrap()).map_err(|e| e.to_string())?;
        std::fs::write(&to, launcher(asm)).map_err(|e| e.to_string())?;
        std::fs::set_permissions(&to, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    } else {
        let tmp = std::env::temp_dir().join(format!("assieme-launcher-{}", std::process::id()));
        std::fs::write(&tmp, launcher(asm)).map_err(|e| e.to_string())?;
        let done = as_admin(osascript, &format!("mkdir -p {} && install -m 755 {} {}", SYSTEM_DIR, sh_quote(&tmp), sh_quote(&to)));
        let _ = std::fs::remove_file(&tmp);
        done?;
    }
    Ok(status(home, asm))
}

pub fn remove(asm: &Path, home: &Path, osascript: &str) -> Result<CliStatus, String> {
    let found = status(home, asm);
    let Some(path) = found.path.clone().map(PathBuf::from) else { return Ok(found) };
    if path.starts_with(home) {
        std::fs::remove_file(&path).map_err(|e| e.to_string())?;
    } else {
        as_admin(osascript, &format!("rm -f {}", sh_quote(&path)))?;
    }
    Ok(status(home, asm))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn home(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("assieme-cli-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn launcher_runs_asm_cli() {
        assert_eq!(launcher(Path::new("/A/assieme-core")), format!("#!/bin/sh\n{MARK}\nexec '/A/assieme-core' cli \"$@\"\n"));
    }

    #[test]
    fn launcher_quotes_a_path_with_shell_characters() {
        let text = launcher(Path::new("/Apps/it's \"$HOME\"/asm"));
        assert!(text.contains("exec '/Apps/it'\\''s \"$HOME\"/asm' cli"), "{text}");
    }

    #[test]
    fn writes_launcher_in_local_bin_when_on_path() {
        let h = home("local");
        let py = Path::new("/Applications/Assieme.app/Contents/Resources/asm-bin/assieme-core");
        let path = format!("/usr/bin:{}/.local/bin", h.display());
        let got = install(py, &path, &h, "/nope/osascript").unwrap();
        assert_eq!(got, CliStatus { state: "installed", path: Some(h.join(".local/bin/assieme").display().to_string()) });
        let text = std::fs::read_to_string(h.join(".local/bin/assieme")).unwrap();
        assert_eq!(text, format!("#!/bin/sh\n{MARK}\nexec '{}' cli \"$@\"\n", py.display()));
    }

    #[test]
    fn not_on_path_goes_to_usr_local_bin() {
        assert_eq!(target("/usr/bin:/bin", Path::new("/Users/me")), PathBuf::from("/usr/local/bin/assieme"));
    }

    #[test]
    fn status_installed_missing_moved() {
        let h = home("status");
        let py = Path::new("/A/asm");
        if !PathBuf::from("/usr/local/bin/assieme").exists() {
            assert_eq!(status(&h, py).state, "missing");
        }
        std::fs::create_dir_all(h.join(".local/bin")).unwrap();
        std::fs::write(h.join(".local/bin/assieme"), launcher(py)).unwrap();
        assert_eq!(status(&h, py).state, "installed");
        assert_eq!(status(&h, Path::new("/Moved/asm")).state, "moved");
    }

    #[test]
    fn remove_leaves_a_foreign_file_alone() {
        let h = home("foreign");
        let p = h.join(".local/bin/assieme");
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(&p, "#!/bin/sh\necho mine\n").unwrap();
        let py = Path::new("/A/asm");
        remove(py, &h, "/nope/osascript").unwrap();
        assert!(p.exists());
        let on_path = format!("{}/.local/bin", h.display());
        assert!(install(py, &on_path, &h, "/nope/osascript").unwrap_err().contains("not written by Assieme"));
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "#!/bin/sh\necho mine\n");
    }

    #[test]
    fn remove_deletes_ours() {
        let h = home("remove");
        let py = Path::new("/A/asm");
        install(py, &format!("{}/.local/bin", h.display()), &h, "/nope").unwrap();
        remove(py, &h, "/nope").unwrap();
        assert!(!h.join(".local/bin/assieme").exists());
    }

    #[test]
    fn admin_install_runs_through_osascript() {
        if Path::new("/usr/local/bin/assieme").symlink_metadata().is_ok() {
            return; // this Mac has its own: install stops before asking for the password
        }
        let h = home("admin");
        let log = h.join("log");
        let osa = h.join("osascript");
        std::fs::write(&osa, format!("#!/bin/sh\nprintf '%s\\n' \"$@\" > '{}'\n", log.display())).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&osa, std::fs::Permissions::from_mode(0o755)).unwrap();
        let _ = install(Path::new("/A/asm"), "/usr/bin", &h, osa.to_str().unwrap());
        let got = std::fs::read_to_string(&log).unwrap();
        assert!(got.contains("with administrator privileges") && got.contains("install -m 755"), "{got}");
        assert!(got.contains("/usr/local/bin/assieme"), "{got}");
    }
}
