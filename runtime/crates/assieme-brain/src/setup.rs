//! Port of `assieme/setup.py`, owned by session F (see OWNERS.md).
//!
//! First-run setup: Apple's developer tools, where the agents are installed, and whether they are signed in.
// `agent_path` is the foundation's and is still declared with `todo!()`; its owner removes this allow.
#![allow(clippy::todo, unused_variables)]
use crate::prelude::*;
use serde_json::json;
use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

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

impl SetupError {
    fn failed(m: impl Into<String>) -> SetupError {
        SetupError::Failed(m.into())
    }
}

/// Where an agent program is installed, or None: this process's PATH, the login shell's, then ~/.local/bin.
pub fn agent_path(name: &str) -> Option<PathBuf> {
    todo!("setup.agent_path")
}

// --- whether the agents are signed in --------------------------------------------------------------------------------

/// Exit code 1 is what both status commands give when signed out (measured); anything else unclear counts as signed in.
const STATUS: [(&str, Option<&[&str]>); 6] = [
    ("claude", Some(&["auth", "status"])),
    ("codex", Some(&["login", "status"])),
    ("gemini", None),
    ("opencode", Some(&["auth", "list"])),
    ("cursor", Some(&["status"])),
    ("antigravity", None),
];

/// The others' program is their kind.
fn program(kind: &str) -> &str {
    match kind {
        "cursor" => "cursor-agent",
        "antigravity" => "agy",
        k => k,
    }
}

/// Gemini CLI has no status command: only whether its sign-in files exist.
const GEMINI_SIGNIN: [&str; 2] = ["~/.gemini/oauth_creds.json", "~/.gemini/google_accounts.json"];
pub(crate) const TIMEOUT: Duration = Duration::from_secs(5);

fn home() -> String {
    std::env::var("HOME").unwrap_or_default()
}

fn expanduser(p: &str) -> String {
    match p.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => format!("{}{rest}", home()),
        _ => p.to_string(),
    }
}

/// Runs a command in a process group of its own; None when it ran past `timeout`, when the whole group is killed (a git
/// hook's children would otherwise keep its pipes open).
pub(crate) fn run(
    mut cmd: Command,
    stdin: Option<Vec<u8>>,
    timeout: Duration,
) -> std::io::Result<Option<std::process::Output>> {
    use std::os::unix::process::CommandExt;
    cmd.stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    cmd.process_group(0);
    let mut child = cmd.spawn()?;
    let group = child.id() as i32;
    let feed = child.stdin.take().zip(stdin).map(|(mut pipe, data)| std::thread::spawn(move || pipe.write_all(&data)));
    fn drain(pipe: Option<impl Read + Send + 'static>) -> std::sync::mpsc::Receiver<Vec<u8>> {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut out = vec![];
            if let Some(mut p) = pipe {
                let _ = p.read_to_end(&mut out);
            }
            let _ = tx.send(out);
        });
        rx
    }
    let (out, err) = (drain(child.stdout.take()), drain(child.stderr.take()));
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(s) = child.try_wait()? {
            break Some(s);
        }
        if Instant::now() >= deadline {
            // SAFETY: killpg on the group this function started.
            unsafe { libc::killpg(group, libc::SIGKILL) };
            let _ = child.wait();
            break None;
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let Some(status) = status else { return Ok(None) };
    if let Some(f) = feed {
        let _ = f.join();
    }
    // A background child that kept a pipe open must not hold the answer back.
    let grace = Duration::from_secs(2);
    let (stdout, stderr) = (out.recv_timeout(grace).unwrap_or_default(), err.recv_timeout(grace).unwrap_or_default());
    Ok(Some(std::process::Output { status, stdout, stderr }))
}

fn is_exec(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    p.metadata().is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

/// Git works. /usr/bin/git is Apple's stub, which opens an installer dialog if run, so it is only asked about.
fn git_ready_with(found: Option<PathBuf>, darwin: bool) -> bool {
    // Linux's /usr/bin/git is git itself.
    if found.as_deref().is_some_and(|f| f != Path::new("/usr/bin/git") || !darwin) {
        return true;
    }
    let mut cmd = Command::new("xcode-select");
    cmd.arg("-p");
    matches!(run(cmd, None, TIMEOUT), Ok(Some(o)) if o.status.success())
}

/// Git works (Apple's stub is only asked about, never run).
pub fn git_ready() -> bool {
    let found =
        std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join("git")).find(|f| is_exec(f)));
    git_ready_with(found, cfg!(target_os = "macos"))
}

/// Exit 0 either way for these two: their words say it. Read in memory and dropped (they can name the account).
fn signed_out_words(name: &str) -> Option<Regex> {
    match name {
        "opencode" => Some(Regex::new(r"\b0 credentials").unwrap()),
        "cursor" => Some(Regex::new(r"(?i)not logged in").unwrap()),
        _ => None,
    }
}

fn signed_in(path: &Path, name: &str) -> bool {
    let Some(args) = STATUS.iter().find(|(n, _)| *n == name).and_then(|(_, a)| *a) else {
        // Antigravity has no way to ask: its first run signs in, in its own terminal.
        return name == "antigravity" || GEMINI_SIGNIN.iter().any(|f| Path::new(&expanduser(f)).exists());
    };
    let mut cmd = Command::new(path);
    cmd.args(args);
    if let Some(words) = signed_out_words(name) {
        cmd.env("NO_COLOR", "1");
        return match run(cmd, None, TIMEOUT) {
            Ok(Some(o)) => !words.is_match(&format!(
                "{}{}",
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            )),
            _ => true,
        };
    }
    match run(cmd, None, TIMEOUT) {
        Ok(Some(o)) => o.status.code() != Some(1),
        _ => true,
    }
}

/// `{git, agents: {name: {installed, signed_in, connected?}}}`.
fn status_with(paths: &BTreeMap<&str, Option<PathBuf>>) -> Value {
    // Side by side, so two hanging commands still answer in one timeout.
    let signed: BTreeMap<&str, bool> = std::thread::scope(|s| {
        let tasks: Vec<_> =
            paths.iter().map(|(n, p)| (*n, p.as_deref().map(|p| s.spawn(move || signed_in(p, n))))).collect();
        tasks.into_iter().map(|(n, t)| (n, t.is_some_and(|t| t.join().unwrap_or(false)))).collect()
    });
    let mut agents = Map::new();
    for (name, _) in STATUS {
        agents.insert(name.into(), json!({"installed": paths[name].is_some(), "signed_in": signed[name]}));
    }
    if let Some(agy) = &paths["antigravity"] {
        // its team tools come from Assieme's plugin, installed with the person's OK
        agents["antigravity"]["connected"] = json!(crate::antigravity::connected(agy));
    }
    json!({"git": git_ready(), "agents": agents})
}

pub fn status() -> Value {
    let paths = STATUS.iter().map(|(n, _)| (*n, agent_path(program(n)))).collect();
    status_with(&paths)
}

/// Open Apple's own installer for the command line developer tools.
fn install_tools() -> Value {
    let mut cmd = Command::new("xcode-select");
    cmd.arg("--install");
    let _ = run(cmd, None, TIMEOUT);
    json!({"ok": true})
}

// --- turning a folder into a project ---------------------------------------------------------------------------------

/// What Set up this folder leaves out of the first version, by name.
const EXCLUDE: [&str; 14] = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa*",
    "id_ed25519*",
    "credentials*",
    "secrets*",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".DS_Store",
];
const MAX_FILES: usize = 20000;
const LIST_TIMEOUT: Duration = Duration::from_secs(15);
/// Add and commit may run the person's hooks; just under the app's own 120 s.
const SAVE_TIMEOUT: Duration = Duration::from_secs(110);
/// What git leaves in its folder while a merge or similar is unfinished.
const UNFINISHED: [&str; 6] =
    ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply"];
const ASSIEME_DIR: &str = ".assieme";
const FIRST_MESSAGE: &str = "First version, saved by Assieme";
const BEFORE_TEAM_MESSAGE: &str = "Saved by Assieme before starting a team";

fn real(path: &str) -> String {
    std::fs::canonicalize(expanduser(path)).map_or_else(|_| expanduser(path), |p| p.to_string_lossy().into_owned())
}

/// The home folder or `/`, by resolved path, so `~/`, a trailing slash and a symlink all count.
pub fn too_broad(project: &str) -> bool {
    let real_path = real(project);
    real_path == "/" || real_path == real("~")
}

/// Runs git; returns (stdout bytes, stderr text). Optional locks off, so a read never refreshes the index.
fn git(cwd: &str, args: &[&str], timeout: Duration, stdin: Option<Vec<u8>>) -> Result<(Vec<u8>, String), SetupError> {
    let mut cmd = Command::new("git");
    cmd.args(args).current_dir(cwd).env("GIT_OPTIONAL_LOCKS", "0");
    let out = match run(cmd, stdin, timeout) {
        Ok(Some(o)) => o,
        Ok(None) => return Err(SetupError::TooLarge(TooLarge("Listing this folder took too long.".into()))),
        Err(e) => return Err(SetupError::failed(e.to_string())),
    };
    let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
    if !out.status.success() {
        let words = stderr.trim();
        return Err(SetupError::failed(if words.is_empty() {
            format!("git {} failed.", args[0])
        } else {
            words.to_string()
        }));
    }
    Ok((out.stdout, stderr))
}

fn read(cwd: &str, args: &[&str]) -> Result<(Vec<u8>, String), SetupError> {
    git(cwd, args, LIST_TIMEOUT, None)
}

fn names(out: &[u8]) -> Vec<String> {
    out.split(|b| *b == 0).filter(|p| !p.is_empty()).map(|p| String::from_utf8_lossy(p).into_owned()).collect()
}

/// A question git answers by succeeding or failing: only a timeout is an error.
fn works(cwd: &str, args: &[&str]) -> Result<bool, SetupError> {
    match read(cwd, args) {
        Ok(_) => Ok(true),
        Err(e @ SetupError::TooLarge(_)) => Err(e),
        Err(_) => Ok(false),
    }
}

fn is_repo(project: &str) -> Result<bool, SetupError> {
    works(project, &["rev-parse", "--show-toplevel"])
}

fn has_commits(project: &str) -> Result<bool, SetupError> {
    works(project, &["rev-parse", "--verify", "--quiet", "HEAD"])
}

/// (git folder, this folder's path inside the repository, ending in `/` or empty).
fn repo_state(project: &str) -> Result<(String, String), SetupError> {
    let out = read(project, &["rev-parse", "--absolute-git-dir", "--show-prefix"])?.0;
    let text = String::from_utf8_lossy(&out).into_owned();
    let mut lines = text.split('\n');
    Ok((lines.next().unwrap_or("").to_string(), lines.next().unwrap_or("").to_string()))
}

/// Paths `git status --porcelain -z` reports inside this folder, relative to it, leaving out .assieme/.
fn changed_files(project: &str, untracked: &str) -> Result<Vec<String>, SetupError> {
    let prefix = repo_state(project)?.1;
    let out = read(project, &["status", "--porcelain", "-z", &format!("--untracked-files={untracked}"), "--", "."])?.0;
    let (mut paths, mut skip) = (vec![], false);
    for e in names(&out) {
        if skip {
            // the old name of a rename or copy
            skip = false;
            continue;
        }
        skip = e.starts_with(['R', 'C']);
        let path: String = e.chars().skip(3).collect();
        // status paths start at the repository's root
        paths.push(path.strip_prefix(&prefix).unwrap_or(&path).to_string());
    }
    Ok(paths.into_iter().filter(|p| p != ASSIEME_DIR && !p.starts_with(&format!("{ASSIEME_DIR}/"))).collect())
}

struct Plan {
    files: Vec<String>,
    left_out: Vec<String>,
    first: bool,
}

/// The one list of what a save commits, so the preview, the warning and the save cannot disagree.
/// Reads only: not even a .git, index or exclude file is written.
fn plan(project: &str) -> Result<Plan, SetupError> {
    if too_broad(project) {
        return Err(SetupError::TooLarge(TooLarge("This folder is too large.".into())));
    }
    let in_repo = is_repo(project)?;
    if in_repo {
        let gitdir = repo_state(project)?.0;
        let midway = UNFINISHED.iter().any(|m| Path::new(&gitdir).join(m).exists())
            || !read(project, &["ls-files", "-u"])?.0.is_empty();
        if midway {
            return Err(SetupError::Unfinished(Unfinished(
                "A git merge or similar is in progress in this folder. Finish it first, then come back.".into(),
            )));
        }
    }
    let exclude: Vec<&str> = EXCLUDE.iter().flat_map(|p| ["-x", p]).collect();
    let scratch = tempfile::tempdir().map_err(|e| SetupError::failed(e.to_string()))?;
    let tmp = scratch.path().to_string_lossy().into_owned();
    let (where_, tracked): (Vec<String>, Vec<String>) = if in_repo {
        (vec![], changed_files(project, "no")?) // tracked files stay tracked whatever the exclusions say
    } else {
        let abs = std::path::absolute(project).map_err(|e| SetupError::failed(e.to_string()))?;
        read(&tmp, &["init", "-q", &tmp])?; // a throwaway repository
        (vec![format!("--git-dir={tmp}/.git"), format!("--work-tree={}", abs.display())], vec![])
    };
    let ls = |head: &[&str], tail: &[&str]| -> Result<Vec<String>, SetupError> {
        let mut args: Vec<&str> = where_.iter().map(String::as_str).collect();
        args.push("ls-files");
        args.extend(head);
        args.extend(&exclude);
        args.extend(tail);
        Ok(names(&read(project, &args)?.0))
    };
    let kept = ls(&["-o", "--exclude-standard"], &["-x", ".assieme/", "-z"])?;
    let left = ls(&["-o", "-i", "--directory"], &["-z"])?;
    let files: Vec<String> = tracked.into_iter().chain(kept).collect::<BTreeSet<_>>().into_iter().collect();
    if files.len() > MAX_FILES {
        return Err(SetupError::TooLarge(TooLarge("This folder has too many files.".into())));
    }
    let mut left_out: Vec<String> = left.iter().map(|p| p.trim_end_matches('/').to_string()).collect();
    left_out.sort();
    Ok(Plan { files, left_out, first: !in_repo || !has_commits(project)? })
}

pub fn folder_preview(project: &str) -> Result<Value, SetupError> {
    let plan = plan(&expanduser(project))?;
    Ok(
        json!({"files": plan.files.len(), "sample": plan.files.iter().take(500).collect::<Vec<_>>(), "left_out": plan.left_out,
              "empty": plan.files.is_empty(), "first": plan.first}),
    )
}

/// How many files Save them first would commit.
pub fn uncommitted(project: &str) -> Result<usize, SetupError> {
    Ok(plan(project)?.files.len())
}

fn add_exclusions(project: &str) -> Result<(), SetupError> {
    let out = read(project, &["rev-parse", "--git-path", "info/exclude"])?.0;
    let path = String::from_utf8_lossy(&out).trim().to_string();
    let path = if Path::new(&path).is_absolute() { PathBuf::from(path) } else { Path::new(project).join(path) };
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let have: Vec<&str> = text.lines().collect();
    let wanted = EXCLUDE.iter().copied().chain([".assieme/"]);
    let missing: Vec<&str> = wanted.filter(|p| !have.contains(p)).collect();
    if !missing.is_empty() {
        let io = |e: std::io::Error| SetupError::failed(e.to_string());
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(io)?;
        }
        let lead = if have.last().is_some_and(|l| !l.is_empty()) { "\n" } else { "" };
        let mut f = std::fs::OpenOptions::new().append(true).create(true).open(&path).map_err(io)?;
        f.write_all(format!("{lead}{}\n", missing.join("\n")).as_bytes()).map_err(io)?;
    }
    Ok(())
}

/// A git call that changes the repository. Killed for taking too long, git leaves .git/index.lock, which would fail
/// every later git command, so one that was not there before the call is removed.
fn write(project: &str, lock: &Path, args: &[&str], stdin: Option<Vec<u8>>) -> Result<(), SetupError> {
    let had = lock.exists();
    match git(project, args, SAVE_TIMEOUT, stdin) {
        Ok(_) => Ok(()),
        Err(SetupError::TooLarge(_)) => {
            // the only way `git` raises it is the timeout
            if !had {
                let _ = std::fs::remove_file(lock);
            }
            Err(SetupError::failed("Saving took too long. Check for a slow git hook in this folder, then try again."))
        }
        Err(e) => Err(e),
    }
}

fn configured(project: &str, key: &str) -> bool {
    read(project, &["config", key]).is_ok_and(|(out, _)| !out.iter().all(u8::is_ascii_whitespace))
}

/// git init if needed, the exclusions, stage exactly the previewed files, one commit. Safe to repeat after a failure.
pub fn folder_save(project: &str) -> Result<Value, SetupError> {
    let project = expanduser(project);
    let plan = plan(&project)?; // refuses a too-broad folder even if the app already checked
    if !plan.first && plan.files.is_empty() {
        return Ok(json!({"ok": true})); // nothing a save would commit
    }
    if !is_repo(&project)? {
        read(&project, &["init", "-q"])?;
    }
    add_exclusions(&project)?;
    let lock = Path::new(&repo_state(&project)?.0).join("index.lock");
    let names = plan.files.iter().map(|f| f.as_bytes().to_vec()).collect::<Vec<_>>().join(&0u8);
    if !plan.files.is_empty() {
        // -A with a pathspec also stages deletions of those paths
        let args = ["--literal-pathspecs", "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul"];
        write(&project, &lock, &args, Some(names.clone()))?;
    }
    let mut args: Vec<&str> = vec![];
    // Named paths: anything the person staged elsewhere in the repository stays out of this commit.
    if !plan.files.is_empty() {
        args.push("--literal-pathspecs");
    }
    if !["user.name", "user.email"].iter().all(|k| configured(&project, k)) {
        args.extend(["-c", "user.name=Assieme", "-c", "user.email=assieme@localhost"]); // this one commit only
    }
    args.extend(["commit", "-q"]);
    if plan.first {
        args.push("--allow-empty");
    }
    args.extend(["-m", if plan.first { FIRST_MESSAGE } else { BEFORE_TEAM_MESSAGE }]);
    if !plan.files.is_empty() {
        args.extend(["--pathspec-from-file=-", "--pathspec-file-nul"]);
    }
    write(&project, &lock, &args, Some(names))?;
    Ok(json!({"ok": true}))
}

fn folder_create(path: &str) -> Result<Value, SetupError> {
    let path = expanduser(path);
    if std::fs::symlink_metadata(&path).is_ok() {
        return Err(SetupError::failed("That folder already exists."));
    }
    std::fs::create_dir_all(&path).map_err(|e| {
        // Python's `strerror`: the words without "(os error N)"
        let text = e.to_string();
        SetupError::failed(text.split(" (os error").next().unwrap_or(&text))
    })?;
    Ok(json!({"path": path}))
}

// --- install and sign-in terminals -----------------------------------------------------------------------------------

/// On every install and sign-in terminal; nothing else is ever stopped or read through setup.*.
const LABEL: &str = "assieme.setup";

/// The commands shown to the person are these strings, and these strings are what run (spec section 3).
fn agents() -> Value {
    json!({
        "claude": {"name": "Claude Code", "install": "curl -fsSL https://claude.ai/install.sh | bash",
                   "signin": "claude auth login", "status": "claude auth status"},
        "codex": {"name": "Codex", "install": "curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh",
                  "signin": "codex login", "status": "codex login status"},
        "gemini": {"name": "Gemini CLI", "install": "npm install -g @google/gemini-cli", "signin": "gemini", "status": ""},
        "opencode": {"name": "OpenCode", "install": "npm install -g --allow-scripts=opencode-ai opencode-ai",
                     "signin": "opencode auth login", "status": "opencode auth list"},
        "cursor": {"name": "Cursor", "install": "curl -fsS https://cursor.com/install | bash",
                   "signin": "cursor-agent login", "status": "cursor-agent status"},
        "antigravity": {"name": "Antigravity", "install": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
                        "signin": "agy", "status": ""},
    })
}

/// One terminal per (agent, kind): pressing the button again reattaches to the one still running.
pub(crate) fn start(rt: &Runtime, agent: &str, kind: &str, command: &str) -> Result<Value, SetupError> {
    let value = format!("{agent}-{kind}");
    let labels = BTreeMap::from([(LABEL.to_string(), value.clone())]);
    for x in rt.list(&labels, false).map_err(|e| SetupError::failed(e.message))? {
        if x["state"] == "live" {
            return Ok(json!({"address": x["address"]}));
        }
    }
    let argv = ["/bin/sh".to_string(), "-c".to_string(), command.to_string()];
    let address = rt
        .start(&argv, &expanduser("~"), &labels, Some(&format!("setup-{value}")), None)
        .map_err(|e| SetupError::failed(e.message))?;
    Ok(json!({"address": address}))
}

fn known(agent: &str) -> Result<Value, SetupError> {
    agents().get(agent).cloned().ok_or_else(|| SetupError::failed("Unknown agent."))
}

fn quote(s: &str) -> String {
    shlex::try_quote(s).map_or_else(|_| s.to_string(), |q| q.into_owned())
}

fn install_agent(rt: &Runtime, agent: &str) -> Result<Value, SetupError> {
    let info = known(agent)?;
    let mut command = info["install"].as_str().unwrap_or("").to_string();
    if command.starts_with("npm ") {
        // nvm's npm and node aren't on a running core's PATH
        let Some(npm) = agent_path("npm") else {
            return Err(SetupError::failed(format!(
                "{} installs with npm, which comes with Node.js. Install Node.js first.",
                info["name"].as_str().unwrap_or("")
            )));
        };
        let dir = npm.parent().map_or(String::new(), |p| p.to_string_lossy().into_owned());
        command = format!("PATH={}:\"$PATH\" {command}", quote(&dir));
    }
    start(rt, agent, "install", &command)
}

/// The shown string with its program resolved to an absolute path: a running core keeps its old PATH.
fn signin_agent(rt: &Runtime, agent: &str) -> Result<Value, SetupError> {
    let info = known(agent)?;
    let Some(path) = agent_path(program(agent)) else {
        return Err(SetupError::failed(format!("{} isn't installed yet.", info["name"].as_str().unwrap_or(""))));
    };
    let signin = info["signin"].as_str().unwrap_or("");
    let mut words = vec![quote(&path.to_string_lossy())];
    words.extend(signin.split_once(' ').map(|(_, rest)| rest.to_string()));
    start(rt, agent, "signin", &words.join(" "))
}

/// The terminal, or None if it is gone. Anything not started for setup is refused: the address comes from the app.
fn setup_terminal(rt: &Runtime, address: &str) -> Result<Option<Value>, SetupError> {
    let Ok(info) = rt.get(address) else { return Ok(None) };
    if !info["labels"].as_object().is_some_and(|l| l.contains_key(LABEL)) {
        return Err(SetupError::failed("That is not a setup terminal."));
    }
    Ok(Some(info))
}

fn terminal_state(rt: &Runtime, address: &str) -> Result<Value, SetupError> {
    let live = setup_terminal(rt, address)?.is_some_and(|i| i["state"] == "live");
    Ok(json!(if live { "running" } else { "finished" }))
}

fn install_stop(rt: &Runtime, address: &str) -> Result<Value, SetupError> {
    if setup_terminal(rt, address)?.is_some_and(|i| i["state"] == "live") {
        rt.kill(address).map_err(|e| SetupError::failed(e.message))?;
    }
    Ok(json!({"ok": true}))
}

fn container_status(agent: &str) -> Result<Value, SetupError> {
    known(agent)?;
    Ok(crate::container::state(agent))
}

/// Download and build: in a setup terminal, like an install, so it can be watched and survives the app.
fn container_build(rt: &Runtime, agent: &str) -> Result<Value, SetupError> {
    known(agent)?;
    start(rt, agent, "container", &crate::container::build_command(agent).map_err(|e| SetupError::failed(e.0))?)
}

/// Open Docker Desktop, which starts its engine. Never installs anything.
fn container_open() -> Value {
    let mut cmd = Command::new("open");
    cmd.args(["-a", "Docker"]);
    let _ = run(cmd, None, TIMEOUT);
    json!({"ok": true})
}

/// The `setup.*` methods.
pub fn handlers(rt: Runtime) -> Handlers {
    let rt = Arc::new(rt);
    let text =
        |p: &Value, k: &str| p[k].as_str().map(String::from).ok_or_else(|| BrainError::new(format!("Missing {k}.")));
    let mut out: Handlers = vec![];
    let mut add = |name: &'static str, f: Box<dyn Fn(Value) -> Result<Value, BrainError> + Send + Sync>| {
        out.push((name, Box::new(move |p, _| f(p))));
    };
    add("setup.commands", Box::new(|_| Ok(agents())));
    let r = rt.clone();
    add("setup.install_agent", Box::new(move |p| Ok(install_agent(&r, &text(&p, "agent")?)?)));
    let r = rt.clone();
    add("setup.signin_agent", Box::new(move |p| Ok(signin_agent(&r, &text(&p, "agent")?)?)));
    let r = rt.clone();
    add("setup.terminal_state", Box::new(move |p| Ok(terminal_state(&r, &text(&p, "address")?)?)));
    let r = rt.clone();
    add("setup.install_stop", Box::new(move |p| Ok(install_stop(&r, &text(&p, "address")?)?)));
    let r = rt.clone();
    add("setup.connect_antigravity", Box::new(move |_| Ok(crate::antigravity::connect(&r)?)));
    add("setup.status", Box::new(|_| Ok(status())));
    add("setup.install_tools", Box::new(|_| Ok(install_tools())));
    add("setup.folder_preview", Box::new(move |p| Ok(folder_preview(&text(&p, "project")?)?)));
    add("setup.folder_save", Box::new(move |p| Ok(folder_save(&text(&p, "project")?)?)));
    add("setup.folder_create", Box::new(move |p| Ok(folder_create(&text(&p, "path")?)?)));
    add("container.status", Box::new(move |p| Ok(container_status(&text(&p, "agent")?)?)));
    let r = rt;
    add("container.build", Box::new(move |p| Ok(container_build(&r, &text(&p, "agent")?)?)));
    add("container.open", Box::new(|_| Ok(container_open())));
    out
}

#[cfg(test)]
mod tests;
