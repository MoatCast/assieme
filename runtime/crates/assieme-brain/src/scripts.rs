//! Port of `assieme/scripts.py`, owned by session D (see OWNERS.md).
//!
//! Setup, run and archive scripts a team brought over from another tool (step 30).
//!
//! Each script runs only after the person has said yes to that exact line for that team, in its agent's copy, with the
//! same clean environment as the live preview.
use crate::prelude::*;
use std::io::Read;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const WHICH: [&str; 3] = ["setup", "run", "archive"];
/// This Mac only, as the preview.
const LOCAL: [(&str, &str); 2] = [("HOST", "127.0.0.1"), ("HOSTNAME", "127.0.0.1")];
const KEPT_CHARS: usize = 4000;

static VAR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\$(?:\{(CONDUCTOR_[A-Z_]+)\}|(CONDUCTOR_[A-Z_]+))").expect("a fixed pattern"));

/// (line, unknown): Conductor's variables as Assieme's, still as variables, so quoting in the line keeps working:
/// the port is `$PORT`, the agent's folder is `$PWD` (scripts run in it), and the project folder stays
/// `$CONDUCTOR_ROOT_PATH`, which `env` sets. Variables Assieme has no match for are kept and named.
pub fn rewrite(line: &str) -> (String, Vec<String>) {
    let mut unknown: Vec<String> = vec![];
    let out = VAR.replace_all(line, |m: &regex::Captures| {
        let name = m.get(1).or_else(|| m.get(2)).map_or("", |g| g.as_str());
        match name {
            "CONDUCTOR_PORT" => "${PORT}".to_string(),
            "CONDUCTOR_WORKSPACE_PATH" => "${PWD}".to_string(),
            "CONDUCTOR_ROOT_PATH" => "${CONDUCTOR_ROOT_PATH}".to_string(),
            _ => {
                if !unknown.iter().any(|u| u == name) {
                    unknown.push(name.to_string());
                }
                m[0].to_string()
            }
        }
    });
    (out.into_owned(), unknown)
}

fn state_file() -> PathBuf {
    let base = std::env::var_os("XDG_STATE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local").join("state")
    });
    base.join("assieme").join("scripts-ok.json")
}

fn load(file: &Path) -> Map<String, Value> {
    std::fs::read(file).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default()
}

fn allowed_in(file: &Path, team: &str, which: &str, line: &str) -> bool {
    load(file).get(team).and_then(|t| t.get(which)).and_then(Value::as_str) == Some(line)
}

fn allow_in(file: &Path, team: &str, which: &str, line: &str) -> Result<(), BrainError> {
    if !WHICH.contains(&which) {
        return Err(BrainError::new(format!("Unknown script {which:?}.")));
    }
    let mut data = load(file);
    let mine = data.entry(team).or_insert_with(|| Value::Object(Map::new()));
    if let Value::Object(m) = mine {
        m.insert(which.to_string(), Value::String(line.to_string()));
    } else {
        *mine = serde_json::json!({ which: line });
    }
    let fail = |e: std::io::Error| BrainError::new(e.to_string());
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir).map_err(fail)?;
    }
    let tmp = file.with_extension("json.tmp");
    std::fs::write(&tmp, Value::Object(data).to_string()).map_err(fail)?;
    std::fs::rename(&tmp, file).map_err(fail)
}

pub fn allowed(team: &str, which: &str, line: &str) -> bool {
    allowed_in(&state_file(), team, which, line)
}

/// The person said yes to running this exact line for this team. `which`: setup, run or archive.
pub fn allow(team: &str, which: &str, line: &str) -> Result<(), BrainError> {
    allow_in(&state_file(), team, which, line)
}

/// What a brought-over script expects beside Assieme's own: the project folder under Conductor's name.
pub fn env(project: &str) -> BTreeMap<String, String> {
    BTreeMap::from([("CONDUCTOR_ROOT_PATH".to_string(), project.to_string())])
}

fn tail(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let n = text.chars().count();
    text.chars().skip(n.saturating_sub(KEPT_CHARS)).collect()
}

fn drain(mut r: impl Read + Send + 'static) -> std::thread::JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut buf = vec![];
        let _ = r.read_to_end(&mut buf);
        buf
    })
}

/// `run` with the base environment given: the line, rewritten, run with /bin/sh in `folder`.
pub fn run_in(
    base: BTreeMap<String, String>,
    line: &str,
    folder: &str,
    project: &str,
    port: Option<u16>,
    timeout: f64,
) -> Value {
    let line = rewrite(line).0;
    let mut vars = base;
    vars.extend(LOCAL.iter().map(|(k, v)| (k.to_string(), v.to_string())));
    vars.extend(env(project));
    if let Some(p) = port.filter(|p| *p != 0) {
        vars.insert("PORT".into(), p.to_string());
    }
    let result = |ok: bool, output: String| serde_json::json!({"ok": ok, "output": output});
    let spawned = Command::new("/bin/sh")
        .args(["-c", &line])
        .current_dir(folder)
        .env_clear()
        .envs(vars)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0) // so a timeout stops what the script started too
        .spawn();
    let mut child = match spawned {
        Ok(c) => c,
        Err(e) => return result(false, e.to_string()),
    };
    let (out, err) = (drain(child.stdout.take().expect("piped")), drain(child.stderr.take().expect("piped")));
    let deadline = Instant::now() + Duration::from_secs_f64(timeout.max(0.0));
    let status = loop {
        match child.try_wait() {
            Ok(Some(s)) => break Some(s),
            Ok(None) if Instant::now() >= deadline => break None,
            Ok(None) => std::thread::sleep(Duration::from_millis(10)),
            Err(e) => return result(false, e.to_string()),
        }
    };
    let Some(status) = status else {
        // SAFETY: killpg on the group this function made for the child; no memory is touched.
        unsafe { libc::killpg(child.id() as i32, libc::SIGKILL) };
        let _ = child.wait();
        let _ = (out.join(), err.join());
        return result(false, format!("It took longer than {timeout} seconds, so it was stopped."));
    };
    let mut all = out.join().unwrap_or_default();
    all.extend(err.join().unwrap_or_default());
    result(status.success(), tail(&all))
}

/// `{ok, output}`: the line, rewritten, run with /bin/sh in `folder`; its output kept (the last 4000 characters).
pub fn run(line: &str, folder: &str, project: &str, port: Option<u16>, timeout: f64) -> Value {
    let base: BTreeMap<String, String> = std::env::vars().collect();
    run_in(crate::runtime::clean_env(&base), line, folder, project, port, timeout)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sh(line: &str, folder: &Path, port: Option<u16>, timeout: f64) -> Value {
        let base = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
        run_in(base, line, folder.to_str().unwrap(), "/the/project", port, timeout)
    }

    #[test]
    fn rewrites_conductor_variables_and_names_unknown_ones() {
        let (line, unknown) = rewrite("cd $CONDUCTOR_WORKSPACE_PATH && serve ${CONDUCTOR_PORT} $CONDUCTOR_ROOT_PATH $CONDUCTOR_DB $CONDUCTOR_DB");
        assert_eq!(line, "cd ${PWD} && serve ${PORT} ${CONDUCTOR_ROOT_PATH} $CONDUCTOR_DB $CONDUCTOR_DB");
        assert_eq!(unknown, ["CONDUCTOR_DB"]);
    }

    #[test]
    fn a_yes_covers_that_exact_line_for_that_team_only() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("a").join("scripts-ok.json");
        assert!(!allowed_in(&file, "t", "setup", "make"));
        allow_in(&file, "t", "setup", "make").unwrap();
        assert!(allowed_in(&file, "t", "setup", "make"));
        assert!(!allowed_in(&file, "t", "setup", "make all"));
        assert!(!allowed_in(&file, "t", "run", "make"));
        assert!(!allowed_in(&file, "other", "setup", "make"));
        allow_in(&file, "t", "run", "go").unwrap();
        assert!(allowed_in(&file, "t", "setup", "make"), "a second yes keeps the first");
        assert!(allow_in(&file, "t", "deploy", "x").is_err());
    }

    #[test]
    fn a_damaged_file_means_no_yes() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("f.json");
        std::fs::write(&file, "[1, 2").unwrap();
        assert!(!allowed_in(&file, "t", "setup", "x"));
        allow_in(&file, "t", "setup", "x").unwrap();
        assert!(allowed_in(&file, "t", "setup", "x"));
    }

    #[test]
    fn runs_in_the_folder_with_the_scripts_variables() {
        let dir = tempfile::tempdir().unwrap();
        let got = sh("echo $CONDUCTOR_ROOT_PATH $PORT $HOST; pwd", dir.path(), Some(4000), 10.0);
        assert_eq!(got["ok"], true);
        let out = got["output"].as_str().unwrap();
        assert!(out.starts_with("/the/project 4000 127.0.0.1\n"), "{out}");
        assert!(out.trim_end().ends_with(dir.path().file_name().unwrap().to_str().unwrap()), "{out}");
    }

    #[test]
    fn a_failing_script_is_not_ok_and_keeps_both_streams() {
        let dir = tempfile::tempdir().unwrap();
        let got = sh("echo out; echo err >&2; exit 3", dir.path(), None, 10.0);
        assert_eq!(got["ok"], false);
        assert_eq!(got["output"], "out\nerr\n");
    }

    #[test]
    fn keeps_the_last_4000_characters() {
        let dir = tempfile::tempdir().unwrap();
        let got = sh("i=0; while [ $i -lt 5000 ]; do printf x; i=$((i+1)); done; printf END", dir.path(), None, 10.0);
        let out = got["output"].as_str().unwrap();
        assert_eq!(out.chars().count(), 4000);
        assert!(out.ends_with("xEND"));
    }

    #[test]
    fn a_slow_script_is_stopped_with_what_it_started() {
        let dir = tempfile::tempdir().unwrap();
        let started = Instant::now();
        let got = sh("sleep 30 & sleep 30", dir.path(), None, 0.3);
        assert!(started.elapsed() < Duration::from_secs(10));
        assert_eq!(got["ok"], false);
        assert_eq!(got["output"], "It took longer than 0.3 seconds, so it was stopped.");
    }

    #[test]
    fn a_missing_folder_is_an_error_not_a_panic() {
        let got = sh("true", Path::new("/no/such/folder"), None, 5.0);
        assert_eq!(got["ok"], false);
        assert!(!got["output"].as_str().unwrap().is_empty());
    }
}
