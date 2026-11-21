//! Port of `assieme/review.py`, owned by session E (see OWNERS.md).
//!
//! Review and merge: each agent's branch, its diff and checks, and what the person decides about it.
//!
//! Git is the source of truth: the branches `assieme/<team>/<role>` are the list. The brain keeps only test results
//! per commit and undo records, both in memory. Refusals are a `ReviewError` with a sentence for the screen.
use crate::prelude::*;
use crate::team::{ROLE, TEAM};
use crate::{adapters, live, newteam, scripts, setup, status, tasks, team, worktree};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::any::Any;
use std::process::Command;
use std::sync::MutexGuard;
use std::time::{Duration, SystemTime};

const MAX_DIFF: usize = 2_000_000;
const TESTS_TIMEOUT: Duration = Duration::from_secs(900);
const PUSH_TIMEOUT: Duration = Duration::from_secs(120);
const DEPENDENCIES: [&str; 3] = ["node_modules", ".venv", "venv"];
const EDITORS: [&str; 4] = ["cursor", "code", "zed", "subl"];
const LOCAL_CHANGES: &str =
    "Your main checkout has uncommitted changes in the same files. Commit or set them aside, then merge again.";

static NAME: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._-]*$").expect("a fixed pattern"));
static NOTE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^[\s`>]*ASSIEME-NOTE\s+(\{.*\})").expect("a fixed pattern"));
static CANT_START: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)No module named pytest|Failed to spawn|command not found").expect("a pattern"));

#[derive(Debug, Clone, PartialEq)]
pub struct ReviewError {
    pub message: String,
    pub detail: String,
}

impl From<ReviewError> for BrainError {
    fn from(e: ReviewError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

fn err(message: impl Into<String>) -> ReviewError {
    ReviewError { message: message.into(), detail: String::new() }
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn check(state: &str, text: &str, detail: &str) -> Value {
    json!({"state": state, "text": text, "detail": detail})
}

/// What a git call said: exit code (-1 when git could not start), stdout and stderr.
struct G {
    code: i32,
    out: String,
    err: String,
}

impl G {
    fn ok(&self) -> bool {
        self.code == 0
    }
}

fn git(cwd: &str, args: &[&str]) -> G {
    match worktree::git(cwd, args) {
        Ok(o) => G {
            code: o.status.code().unwrap_or(-1),
            out: String::from_utf8_lossy(&o.stdout).into_owned(),
            err: String::from_utf8_lossy(&o.stderr).into_owned(),
        },
        Err(e) => G { code: -1, out: String::new(), err: e.to_string() },
    }
}

fn text_of(v: &Value) -> String {
    v.as_str().map_or_else(|| v.to_string(), String::from)
}

fn team_file(folder: &Path) -> Option<toml::Table> {
    toml::from_str(&std::fs::read_to_string(folder.join("team.toml")).ok()?).ok()
}

/// The project's test command, read from what is in its folder; None when there isn't one we can run.
pub fn test_command(path: &Path) -> Option<Vec<String>> {
    let has = |name: &str| path.join(name).is_file();
    let cmd = |words: &[&str]| Some(words.iter().map(|w| w.to_string()).collect());
    if has("uv.lock") {
        cmd(&["uv", "run", "pytest", "-q"])
    } else if has("pytest.ini") || has("tox.ini") || has("pyproject.toml") || has("setup.py") {
        cmd(&["python3", "-m", "pytest", "-q"])
    } else if has("package.json") {
        let script = std::fs::read_to_string(path.join("package.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|j| j["scripts"]["test"].as_str().map(String::from))
            .unwrap_or_default();
        let runnable =
            !script.is_empty() && !script.contains("no test specified") && path.join("node_modules").is_dir();
        if runnable { cmd(&["npm", "test", "--silent"]) } else { None }
    } else if has("Cargo.toml") {
        cmd(&["cargo", "test"])
    } else if has("go.mod") {
        cmd(&["go", "test", "./..."])
    } else if has("Makefile") {
        let text = String::from_utf8_lossy(&std::fs::read(path.join("Makefile")).unwrap_or_default()).into_owned();
        text.lines().any(|l| l.starts_with("test:")).then(|| vec!["make".into(), "test".into()])
    } else {
        None
    }
}

/// What a finished test run means, in words for the Checks list.
fn test_result(cmd: &[String], rc: i32, output: &str) -> Value {
    if rc == 5 && cmd.iter().any(|c| c == "pytest") {
        return check("none", "No tests found", ""); // pytest: nothing collected
    }
    // A Cargo workspace prints one result per crate; others end on the total (Vitest's file count comes first).
    let count = |word: &str| -> i64 {
        let pattern = Regex::new(&format!(r"(\d+) {word}")).expect("a pattern");
        let found: Vec<i64> = pattern.captures_iter(output).filter_map(|c| c[1].parse().ok()).collect();
        if cmd[0] == "cargo" { found.iter().sum() } else { found.last().copied().unwrap_or(0) }
    };
    let (passed, failed) = (count("passed"), count("failed"));
    let total = passed + failed;
    if rc == 127 || (rc != 0 && total == 0 && CANT_START.is_match(output)) {
        return check("none", "Couldn't run the tests", ""); // a real failure may log those words too
    }
    if rc == 0 {
        let text = if total > 0 { format!("Tests pass, {passed} of {total}") } else { "Tests pass".into() };
        return check("ok", &text, "");
    }
    let lines: Vec<&str> = output.trim().lines().collect();
    let tail = lines[lines.len().saturating_sub(15)..].join("\n");
    check("bad", &if failed > 0 { format!("Tests fail, {failed} of {total}") } else { "Tests fail".into() }, &tail)
}

/// `git diff --numstat -z`: a rename puts its two paths in the next two fields.
fn numstat(text: &str) -> Vec<Value> {
    let (parts, mut files, mut i) = (text.split('\0').collect::<Vec<_>>(), vec![], 0);
    while i < parts.len() && !parts[i].is_empty() {
        let mut f = parts[i].splitn(3, '\t');
        let (Some(added), Some(deleted), Some(path)) = (f.next(), f.next(), f.next()) else { break };
        i += 1;
        let mut path = path;
        if path.is_empty() {
            path = parts.get(i + 1).copied().unwrap_or_default();
            i += 2;
        }
        let n = |s: &str| s.parse::<i64>().unwrap_or(0);
        files.push(json!({"path": path, "add": n(added), "del": n(deleted), "binary": added == "-"}));
    }
    files
}

/// What Claude Code said in this transcript.
pub fn claude_said(path: &Path) -> Vec<String> {
    let rows = adapters::rows(path);
    let said = rows.iter().filter(|o| o.get("type").is_some_and(|t| t == "assistant"));
    said.filter_map(|o| adapters::text_of(&o.get("message").unwrap_or(&Value::Null)["content"]))
        .filter(|x| !x.is_empty())
        .collect()
}

/// (its folder, what Codex said) in this transcript.
pub fn codex_said(path: &Path) -> (Option<String>, Vec<String>) {
    let (mut cwd, mut said) = (None, vec![]);
    for o in adapters::rows(path) {
        let p = o.get("payload").unwrap_or(&Value::Null);
        if o.get("type").is_some_and(|t| t == "session_meta") {
            cwd = p["cwd"].as_str().map(String::from);
        } else if p["type"] == "message" && p["role"] == "assistant" {
            said.push(adapters::joined(p.get("content"), " ").unwrap_or_default());
        }
    }
    (cwd, said)
}

/// What Undo needs to bring a merged agent back; it is also the row's `restore` in the Activity log.
#[derive(Clone, Serialize, Deserialize)]
struct Undo {
    team: String,
    role: String,
    project: String,
    base: String,
    branch: String,
    path: String,
    tip: String,
    old: String,
    new: String,
    notes: Vec<String>,
    archived: bool,
    clone: bool,
    kept: bool,
}

type Kept = BTreeMap<String, (String, String)>;

#[derive(Default)]
struct State {
    tests: BTreeMap<(String, String), Value>,
    test_locks: BTreeMap<String, Arc<Mutex<()>>>,
    undos: BTreeMap<String, Undo>,
    transcripts: BTreeMap<PathBuf, (SystemTime, Box<dyn Any + Send>)>,
}

/// Tells the preview of (team, role) to stop; what it answers says whether one was running.
pub type OnArchive = dyn Fn(&str, &str) -> Value + Send + Sync;

/// Review: each agent's branch, its checks, merge, undo and discard.
pub struct Review {
    rt: Runtime,
    teams: PathBuf,
    lock: Mutex<()>, // one merge, undo or discard at a time
    state: Arc<Mutex<State>>,
    on_archive: Mutex<Box<OnArchive>>,
    /// The Activity log; None is the brain's own.
    pub log: Option<PathBuf>,
}

impl Review {
    pub fn new(rt: Runtime, teams: PathBuf) -> Review {
        Review {
            rt,
            teams,
            lock: Mutex::new(()),
            state: Arc::default(),
            on_archive: Mutex::new(Box::new(|_, _| Value::Null)),
            log: None,
        }
    }

    /// The brain stops the agent's preview before its folder goes.
    pub fn set_on_archive(&self, f: Box<OnArchive>) {
        *lock(&self.on_archive) = f;
    }

    fn rows(&self, name: Option<&str>) -> Vec<Value> {
        let labels = name.map(|n| BTreeMap::from([(TEAM.to_string(), n.to_string())])).unwrap_or_default();
        let Ok(rows) = self.rt.list(&labels, true) else { return vec![] }; // the core isn't running: no terminals
        let of = |r: &Value| r["labels"][TEAM].as_str().map(String::from);
        rows.into_iter().filter(|r| of(r).is_some_and(|t| name.is_none_or(|n| n == t))).collect()
    }

    fn known_teams(&self) -> BTreeMap<String, String> {
        let mut out = BTreeMap::new();
        for e in std::fs::read_dir(&self.teams).into_iter().flatten().flatten() {
            let project = team_file(&e.path()).and_then(|t| t.get("project")?.as_str().map(String::from));
            if let Some(p) = project.filter(|p| Path::new(p).is_dir()) {
                out.insert(e.file_name().to_string_lossy().into_owned(), p);
            }
        }
        for r in self.rows(None) {
            // started from the command line: no saved folder
            if let Some((head, _)) = r["cwd"].as_str().unwrap_or("").split_once("/.assieme/worktrees/")
                && Path::new(head).is_dir()
            {
                out.entry(r["labels"][TEAM].as_str().unwrap_or("").to_string()).or_insert(head.to_string());
            }
        }
        out
    }

    fn project(&self, name: &str) -> Result<String, ReviewError> {
        if !NAME.is_match(name) {
            return Err(err(format!("{name:?} is not a team name.")));
        }
        self.known_teams().remove(name).ok_or_else(|| err(format!("Can't find the project for team {name}.")))
    }

    /// An agent's project, base branch, branch and worktree folder; an error when it has no branch.
    pub fn worktree(&self, team: &str, role: &str) -> Result<(String, String, String, String), ReviewError> {
        if !NAME.is_match(role) {
            return Err(err(format!("{role:?} is not an agent name.")));
        }
        let project = self.project(team)?;
        let (branch, path) = self
            .kept(team)
            .remove(role)
            .unwrap_or_else(|| (format!("assieme/{team}/{role}"), format!("{project}/.assieme/worktrees/{role}")));
        worktree::sync(&project);
        if !has_branch(&project, &branch) {
            return Err(err(format!("{role} has no branch to review. It may have been merged or discarded.")));
        }
        Ok((project.clone(), self.base(&project)?, branch, path))
    }

    pub fn team_live(&self, team: &str) -> bool {
        self.rows(Some(team)).iter().any(|r| r["state"] == "live")
    }

    fn base(&self, project: &str) -> Result<String, ReviewError> {
        let head = git(project, &["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]);
        let head = head.ok().then(|| head.out.trim().strip_prefix("origin/").map(String::from)).flatten();
        let current = git(project, &["branch", "--show-current"]).out.trim().to_string();
        let names = head.into_iter().chain(["main".into(), "master".into(), current]);
        names
            .into_iter()
            .find(|n| !n.is_empty() && has_branch(project, n))
            .ok_or_else(|| err("This project has no main branch to compare with."))
    }

    /// {role: (branch, folder)} of the team's agents brought over in their own worktree (step 30), while that folder
    /// is there.
    fn kept(&self, name: &str) -> Kept {
        let roles = team_file(&self.teams.join(name)).and_then(|t| t.get("role")?.as_array().cloned());
        let field = |r: &toml::Value, k: &str| r.get(k)?.as_str().filter(|s| !s.is_empty()).map(String::from);
        let found = roles
            .unwrap_or_default()
            .iter()
            .filter_map(|r| Some((field(r, "name")?, (field(r, "branch")?, field(r, "worktree")?))))
            .collect::<Kept>();
        found.into_iter().filter(|(_, v)| Path::new(&v.1).is_dir()).collect()
    }

    fn team_branches(&self, project: &str, name: &str) -> Vec<(String, String)> {
        let prefix = format!("refs/heads/assieme/{name}/");
        let out = git(project, &["for-each-ref", "--format=%(refname)", &prefix]).out;
        let kept = self.kept(name);
        let own = out.split_whitespace().filter_map(|r| r.strip_prefix(&prefix)).filter(|r| !kept.contains_key(*r));
        let mut all: Vec<(String, String)> = own.map(|r| (r.to_string(), format!("assieme/{name}/{r}"))).collect();
        all.extend(kept.into_iter().map(|(k, v)| (k, v.0)));
        all
    }

    fn ahead(&self, project: &str, base: &str, branch: &str) -> usize {
        let r = git(project, &["rev-list", "--count", &format!("{base}..{branch}")]);
        if r.ok() { r.out.trim().parse().unwrap_or(0) } else { 0 }
    }

    /// Every agent's branch with commits main doesn't have, including those of stopped teams.
    pub fn waiting(&self) -> Vec<Value> {
        let mut out = vec![];
        for (name, project) in self.known_teams() {
            worktree::sync(&project);
            let Ok(base) = self.base(&project) else { continue }; // not a repository any more, or no commits yet
            for (role, branch) in self.team_branches(&project, &name) {
                let ahead = self.ahead(&project, &base, &branch);
                let last = git(&project, &["log", "-1", "--format=%H%n%ct%n%s", &format!("refs/heads/{branch}")]).out;
                let mut f = last.lines();
                let (tip, at, subject) = (f.next().unwrap_or(""), f.next().unwrap_or("0"), f.next().unwrap_or(""));
                if ahead > 0 {
                    out.push(json!({"team": name, "project": base_name(&project), "role": role, "branch": branch,
                                    "ahead": ahead, "tip": tip, "at": at.parse::<i64>().unwrap_or(0), "subject": subject}));
                }
            }
        }
        out.sort_by_key(|w| (w["team"].to_string(), w["role"].to_string()));
        out
    }

    fn files(&self, project: &str, base: &str, branch: &str) -> Vec<Value> {
        numstat(&git(project, &["diff", "--numstat", "-z", "-M", &format!("{base}...{branch}")]).out)
    }

    fn agent(&self, name: &str, role: &str) -> Value {
        let mine = |r: &&Value| r["labels"][ROLE] == role && r["state"] == "live";
        let rows = self.rows(Some(name));
        let Some(r) = rows.iter().filter(mine).max_by_key(|r| r["created_ms"].as_i64().unwrap_or(0)) else {
            return json!({"live": false, "working": false, "address": null});
        };
        json!({"live": true, "working": r["status"]["state"] == "working", "address": r["address"]})
    }

    /// The test result for this commit, starting the run when there is none yet.
    fn tests_check(&self, project: &str, path: &str, tip: &str, retest: bool) -> Value {
        let key = (project.to_string(), tip.to_string());
        let mut st = lock(&self.state);
        if retest && st.tests.get(&key).is_some_and(|t| matches!(t["state"].as_str(), Some("bad" | "none"))) {
            st.tests.remove(&key); // opening Review again tries again, say after installing what was missing
        }
        if let Some(got) = st.tests.get(&key) {
            return got.clone();
        }
        let cmd = Path::new(path).is_dir().then(|| test_command(Path::new(path))).flatten();
        let first = cmd.as_ref().map_or_else(
            || check("none", "Couldn't run the tests", ""),
            |_| check("running", "Running the tests…", ""),
        );
        st.tests.insert(key, first.clone());
        if let Some(cmd) = cmd {
            let (state, project, path, tip) =
                (self.state.clone(), project.to_string(), path.to_string(), tip.to_string());
            std::thread::spawn(move || run_tests(&state, &project, &path, &tip, &cmd));
        }
        first
    }

    fn ignored(project: &str) -> BTreeSet<String> {
        let text = std::fs::read_to_string(Path::new(project).join(".assieme/review-ignored.json")).unwrap_or_default();
        serde_json::from_str(&text).unwrap_or_default()
    }

    fn set_ignored(project: &str, ids: &BTreeSet<String>) -> std::io::Result<()> {
        let path = Path::new(project).join(".assieme/review-ignored.json");
        std::fs::create_dir_all(path.parent().unwrap_or(Path::new(".")))?;
        std::fs::write(path, json!(ids).to_string())
    }

    /// What a transcript said, read once per file version: Review polls every few seconds.
    pub fn read_once<T: Clone + Send + 'static>(&self, path: &Path, read: fn(&Path) -> T) -> T {
        let when = std::fs::metadata(path).and_then(|m| m.modified()).unwrap_or(SystemTime::UNIX_EPOCH);
        let cached = lock(&self.state)
            .transcripts
            .get(path)
            .filter(|c| c.0 == when)
            .and_then(|c| c.1.downcast_ref::<T>().cloned());
        cached.unwrap_or_else(|| {
            let got = read(path);
            lock(&self.state).transcripts.insert(path.to_path_buf(), (when, Box::new(got.clone())));
            got
        })
    }

    /// What the agent in this folder said, from the transcripts both CLIs keep. Reviewers can't write files (Claude
    /// Code without Write, Codex in a read-only sandbox), but what they say is always recorded.
    fn said(&self, path: &Path) -> Vec<String> {
        let real = std::fs::canonicalize(path).unwrap_or(path.to_path_buf());
        let home = PathBuf::from(crate::rules::home());
        let slug =
            Regex::new("[^A-Za-z0-9]").expect("a pattern").replace_all(&real.to_string_lossy(), "-").into_owned();
        let mut out = vec![];
        for f in crate::pyrules::glob(&home.join(".claude/projects").join(slug).join("*.jsonl")) {
            out.extend(self.read_once(&f, claude_said));
        }
        let meta = std::fs::metadata(path);
        let since = meta.and_then(|m| m.created().or_else(|_| m.modified())).unwrap_or(SystemTime::UNIX_EPOCH);
        for f in crate::pyrules::glob(&home.join(".codex/sessions/*/*/*/*.jsonl")) {
            // sessions older than this worktree can't be about it
            if std::fs::metadata(&f).and_then(|m| m.modified()).is_ok_and(|m| m >= since) {
                let (cwd, said) = self.read_once(&f, codex_said);
                if cwd.is_some_and(|c| std::fs::canonicalize(c).is_ok_and(|c| c == real)) {
                    out.extend(said);
                }
            }
        }
        out
    }

    /// What reviewers said about this branch. They are agents' words: bad lines are skipped, never fatal.
    fn notes(&self, project: &str, role: &str, branch: &str) -> Vec<Value> {
        let root = Path::new(project).join(".assieme/worktrees");
        let ignored = Self::ignored(project);
        let mut authors: Vec<String> = std::fs::read_dir(&root)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        authors.sort();
        let mut out: Vec<Value> = vec![];
        for author in authors {
            for found in NOTE.captures_iter(&self.said(&root.join(&author)).join("\n")) {
                let Ok(n) = serde_json::from_str::<Value>(&found[1]) else { continue };
                let (Some(line), true) = (n["line"].as_i64(), n["branch"] == role || n["branch"] == branch) else {
                    continue;
                };
                let (file, text) = (text_of(&n["file"]), text_of(&n["text"]));
                let id = sha1::Sha1::digest(format!("{author}\0{file}\0{line}\0{text}"))
                    .iter()
                    .take(5)
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>();
                let note = json!({"author": author, "file": file, "line": line, "text": text, "id": id});
                if !text.trim().is_empty() && !ignored.contains(&id) && !out.contains(&note) {
                    out.push(note);
                }
            }
        }
        out
    }

    pub fn get(&self, team: &str, role: &str, retest: bool) -> Result<Value, ReviewError> {
        let (project, base, branch, path) = self.worktree(team, role)?;
        let (tip, mut files) = (tip(&project, &branch), self.files(&project, &base, &branch));
        // The viewer reads git's plain format, whatever the person's own git settings say.
        let range = format!("{base}...{branch}");
        let plain = [
            "-c",
            "core.quotepath=off",
            "diff",
            "-M",
            "--no-color",
            "--no-ext-diff",
            "--src-prefix=a/",
            "--dst-prefix=b/",
        ];
        let diff = git(&project, &[&plain[..], &[&range]].concat()).out;
        let too_big = diff.len() > MAX_DIFF;
        for f in &mut files {
            f["missing"] = json!(!Path::new(&path).join(f["path"].as_str().unwrap_or("")).is_file());
        }
        let mut tests = self.tests_check(&project, &path, &tip, retest);
        tests["id"] = json!("tests");
        let mut checks = vec![tests];
        let conflicts = match conflicts(&project, &base, &branch) {
            None => ("none", "Couldn't check for conflicts".to_string()),
            Some(f) if f.is_empty() => ("ok", format!("No conflicts with {base}")),
            Some(f) => {
                let more = if f.len() > 3 { format!(" and {} more", f.len() - 3) } else { String::new() };
                ("bad", format!("Conflicts with {base} in {}{more}", f[..f.len().min(3)].join(", ")))
            }
        };
        checks.push(json!({"id": "conflicts", "state": conflicts.0, "text": conflicts.1, "detail": ""}));
        let mine: BTreeSet<&str> = files.iter().filter_map(|f| f["path"].as_str()).collect();
        let mut overlaps = vec![];
        for (other, other_branch) in self.team_branches(&project, team).iter().filter(|(o, _)| o != role) {
            let theirs = git(&project, &["diff", "--name-only", "-z", &format!("{base}...{other_branch}")]).out;
            let shared: BTreeSet<&str> = theirs.split('\0').filter(|p| mine.contains(p)).collect();
            if !shared.is_empty() {
                overlaps.push(format!("{other} also changed {}", shared.into_iter().collect::<Vec<_>>().join(", ")));
            }
        }
        let text = if overlaps.is_empty() { "Only touched its own files".to_string() } else { overlaps.join("; ") };
        checks.push(json!({"id": "own-files", "state": if overlaps.is_empty() { "ok" } else { "warn" }, "text": text, "detail": ""}));
        if dirty(&path) {
            checks.push(json!({"id": "uncommitted", "state": "bad", "text": "Has uncommitted changes", "detail": ""}));
        }
        Ok(
            json!({"team": team, "role": role, "project": base_name(&project), "base": base, "branch": branch, "tip": tip,
                  "ahead": self.ahead(&project, &base, &branch), "files": files, "diff": if too_big { "" } else { &diff },
                  "too_big": too_big, "checks": checks, "notes": self.notes(&project, role, &branch),
                  "agent": self.agent(team, role)}),
        )
    }

    /// What a board card shows about this agent's branch, or None when it has none. The tests run, as on opening
    /// Review, only when there is something to review.
    pub fn card(&self, team: &str, role: &str) -> Option<Value> {
        let (project, base, branch, path) = self.worktree(team, role).ok()?;
        let (tip, files, ahead) =
            (tip(&project, &branch), self.files(&project, &base, &branch), self.ahead(&project, &base, &branch));
        let sum = |k: &str| files.iter().map(|f| f[k].as_i64().unwrap_or(0)).sum::<i64>();
        let tests = (ahead > 0)
            .then(|| self.tests_check(&project, &path, &tip, false))
            .map(|t| json!({"state": t["state"], "text": t["text"]}));
        let notes: Vec<Value> = self.notes(&project, role, &branch).iter().map(|n| n["author"].clone()).collect();
        Some(
            json!({"ahead": ahead, "files": files.len(), "add": sum("add"), "del": sum("del"), "tests": tests, "notes": notes}),
        )
    }

    fn write_log(&self, mut entry: Value) {
        entry["at"] = json!(status::now_utc().strftime("%Y-%m-%dT%H:%M:%SZ").to_string());
        entry["by"] = json!("you");
        entry["ok"] = json!(true);
        live::log(&self.log.clone().unwrap_or_else(live::log_path), &entry);
    }

    fn same_tip(project: &str, branch: &str, expected: &str, role: &str) -> Result<(), ReviewError> {
        if tip(project, branch) == expected {
            Ok(())
        } else {
            Err(err(format!("{role} added commits since you looked. Look again first.")))
        }
    }

    /// Keep the tip under refs/assieme, stop the agent, remove its folder and branch. Returns what went wrong, if
    /// anything. The branch must go: `worktree::ensure` refuses a role whose branch has no worktree.
    fn archive(&self, name: &str, role: &str, at: [&str; 3], tip: &str, force: bool) -> Option<String> {
        let [project, branch, path] = at;
        let secs = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map_or(0, |d| d.as_secs());
        let keep = format!("refs/assieme/archive/{name}/{role}/{secs}");
        if !git(project, &["update-ref", &keep, tip]).ok() {
            return Some("Couldn't set its work aside, so its copy was left in place.".into());
        }
        if let Some(address) = self.agent(name, role)["address"].as_str()
            && let Err(e) = self.rt.kill(address)
        {
            return Some(format!("Couldn't stop {role}: {e}"));
        }
        if self.kept(name).contains_key(role) {
            // brought over (step 30): its folder and branch belong to the other tool
            if let Err(e) = newteam::clear_worktree(&self.teams.join(name), role) {
                return Some(e.message);
            }
            let short = path.replacen(&crate::rules::home(), "~", 1);
            return Some(format!(
                "Its folder stays in {short}: {} owns it.",
                worktree::tool_name(path).unwrap_or("the tool it came from")
            ));
        }
        self.archive_script(project, name, role, path);
        if worktree::is_clone(path) {
            // a container agent's copy: its commits are already in the project
            if dirty(path) && !force {
                return Some("Its copy has uncommitted changes, so it was left in place.".into());
            }
            let _ = std::fs::remove_dir_all(path);
        } else if Path::new(path).is_dir() {
            let r =
                git(project, &[&["worktree", "remove"][..], if force { &["--force"] } else { &[] }, &[path]].concat());
            if !r.ok() {
                return Some(format!("Couldn't clean up its copy: {}", r.err.trim()));
            }
        }
        if !branch.starts_with(&format!("assieme/{name}/")) {
            // brought over, its folder gone meanwhile: never theirs
            return Some(format!("Its branch {branch} isn't Assieme's, so it was left as it is."));
        }
        let r = git(project, &["branch", "-D", branch]);
        (!r.ok()).then(|| format!("Couldn't delete its branch: {}", r.err.trim()))
    }

    /// The team's archive script (brought over, step 30) in the copy about to go, if the person said yes to it.
    fn archive_script(&self, project: &str, name: &str, role: &str, path: &str) {
        let line = team_file(&self.teams.join(name))
            .and_then(|t| t.get("scripts")?.get("archive")?.as_str().map(String::from));
        let Some(line) =
            line.filter(|l| !l.is_empty() && Path::new(path).is_dir() && scripts::allowed(name, "archive", l))
        else {
            return;
        };
        let got = scripts::run(&line, path, project, None, 300.0);
        let ok = got["ok"] == true;
        let reason = if ok { "Ran the team archive script" } else { "The archive script didn't finish" };
        let detail = got["output"].as_str().unwrap_or("");
        live::record(live::Record {
            action: "archive-script",
            agent: role,
            reason,
            failed: !ok,
            detail,
            team: Some(name),
            by: "assieme",
            ..Default::default()
        });
    }

    fn do_merge(&self, project: &str, base: &str, branch: &str, role: &str) -> Result<(String, String), ReviewError> {
        let message = format!("Merge {role}'s work ({branch})");
        let old = tip(project, base);
        if let Some(checkout) = checkout_of(project, base) {
            let merging = |c: &str| git(c, &["rev-parse", "--verify", "--quiet", "MERGE_HEAD"]).ok();
            if merging(&checkout) {
                return Err(err(format!(
                    "Your {base} checkout is in the middle of a merge. Finish it, then merge again."
                )));
            }
            let r = git(&checkout, &["merge", "--no-ff", "--no-edit", "-m", &message, branch]);
            if !r.ok() {
                let said = format!("{}{}", r.err, r.out);
                if merging(&checkout) {
                    git(&checkout, &["merge", "--abort"]);
                }
                if said.contains("local changes") || said.contains("untracked working tree files") {
                    return Err(err(LOCAL_CHANGES));
                }
                return Err(err(format!("Couldn't merge: {}", said.trim())));
            }
        } else {
            // main isn't checked out anywhere, so no files to update
            let tree = git(project, &["merge-tree", "--write-tree", base, branch]);
            if !tree.ok() {
                // exit 1 still prints a tree, with conflict markers in its files
                return Err(err(format!(
                    "Couldn't merge: {}",
                    if tree.err.is_empty() { &tree.out } else { &tree.err }.trim()
                )));
            }
            let tree_id = tree.out.split_whitespace().next().unwrap_or("");
            let commit = git(project, &["commit-tree", tree_id, "-p", base, "-p", branch, "-m", &message]);
            let done = git(project, &["update-ref", &format!("refs/heads/{base}"), commit.out.trim(), &old]);
            if !commit.ok() || !done.ok() {
                return Err(err(format!(
                    "Couldn't merge: {}",
                    if commit.err.is_empty() { &done.err } else { &commit.err }.trim()
                )));
            }
        }
        Ok((old, tip(project, base)))
    }

    pub fn merge(&self, team: &str, role: &str, tip: &str, force: bool) -> Result<Value, ReviewError> {
        let out = self.merged(team, role, tip, force)?;
        tasks::merged(team, role, out["base"].as_str().unwrap_or(""), out["undo"].as_str().unwrap_or(""), None);
        Ok(out)
    }

    fn merged(&self, team: &str, role: &str, expected: &str, force: bool) -> Result<Value, ReviewError> {
        let _op = lock(&self.lock);
        let (project, base, branch, path) = self.worktree(team, role)?;
        Self::same_tip(&project, &branch, expected, role)?;
        if self.ahead(&project, &base, &branch) == 0 {
            return Err(err(format!("{role} has nothing new to merge.")));
        }
        if dirty(&path) {
            return Err(err(format!("{role} has uncommitted changes. Ask it to commit them first.")));
        }
        if let Some(found) = conflicts(&project, &base, &branch).filter(|f| !f.is_empty()) {
            return Err(err(format!(
                "This conflicts with {base} in {}. Ask {role} to fix that first.",
                found.join(", ")
            )));
        }
        let tests = lock(&self.state).tests.get(&(project.clone(), expected.to_string())).map(|t| t["state"].clone());
        if !force && tests.is_some_and(|s| s == "bad" || s == "running") {
            return Err(err("The tests have not passed on this branch."));
        }
        if !force && self.agent(team, role)["working"] == true {
            return Err(err(format!("{role} is still working.")));
        }
        let notes: BTreeSet<String> =
            self.notes(&project, role, &branch).iter().filter_map(|n| n["id"].as_str().map(String::from)).collect();
        let (clone, kept) = (worktree::is_clone(&path), self.kept(team).contains_key(role));
        let (old, new) = self.do_merge(&project, &base, &branch, role)?;
        // the merge stands: old notes showing again is the lesser problem
        let _ = Self::set_ignored(&project, &Self::ignored(&project).union(&notes).cloned().collect());
        let previewed = (lock(&self.on_archive))(team, role)["stopped"] == true;
        let problem = self.archive(team, role, [&project, &branch, &path], expected, false);
        let uid = uuid::Uuid::new_v4().simple().to_string();
        let undo = Undo {
            team: team.into(),
            role: role.into(),
            project,
            base: base.clone(),
            branch,
            path,
            tip: expected.into(),
            old,
            new,
            notes: notes.into_iter().collect(),
            archived: problem.is_none(),
            clone,
            kept,
        };
        self.write_log(json!({"id": uid, "team": team, "agent": role, "action": "merge", "undo": true,
                              "title": format!("Merged {role} into {base}"),
                              "reason": problem.as_deref().unwrap_or("Its copy was cleaned up."), "restore": undo}));
        lock(&self.state).undos.insert(uid.clone(), undo);
        Ok(json!({"role": role, "base": base, "problem": problem, "undo": uid, "preview_stopped": previewed}))
    }

    /// After a brain restart the undo record is only in the Activity row's `restore`: take it back, unless still held.
    pub fn remember(&self, id: &str, restore: &Value) {
        if let Ok(undo) = serde_json::from_value::<Undo>(restore.clone()) {
            lock(&self.state).undos.entry(id.to_string()).or_insert(undo);
        }
    }

    pub fn undo(&self, id: &str) -> Result<Value, ReviewError> {
        let (out, team) = self.undone(id)?;
        tasks::unmerged(&team, id, None);
        Ok(out)
    }

    fn undone(&self, id: &str) -> Result<(Value, String), ReviewError> {
        let _op = lock(&self.lock);
        let rec = lock(&self.state).undos.get(id).cloned().ok_or_else(|| err("This can no longer be undone here."))?;
        let Undo { project, base, branch, path, .. } = &rec;
        if tip(project, base) != rec.new {
            return Err(err(format!("{base} has moved on since the merge, so it can't be undone here.")));
        }
        let remote = format!("refs/remotes/origin/{base}");
        if git(project, &["rev-parse", "--verify", "--quiet", &remote]).ok()
            && git(project, &["merge-base", "--is-ancestor", &rec.new, &remote]).ok()
        {
            return Err(err("This merge has already reached origin, so it can't be undone here."));
        }
        if rec.archived && (has_branch(project, branch) || Path::new(path).exists()) {
            return Err(err(format!("{} has started again, so this can't be undone here.", rec.role)));
        }
        let r = match checkout_of(project, base) {
            Some(checkout) => git(&checkout, &["reset", "--keep", &rec.old]),
            None => git(project, &["update-ref", &format!("refs/heads/{base}"), &rec.old, &rec.new]),
        };
        if !r.ok() {
            return Err(err(format!(
                "Your {base} checkout has uncommitted changes in the same files. Commit or set them aside, then undo again."
            )));
        }
        if !has_branch(project, branch) {
            git(project, &["branch", branch, &rec.tip]);
        }
        if !Path::new(path).is_dir() && !rec.clone {
            // a container's copy is cloned again when it starts
            let r = git(project, &["worktree", "add", path, branch]);
            if !r.ok() {
                return Err(err(format!("The merge is undone, but its copy didn't come back: {}", r.err.trim())));
            }
        }
        if rec.kept {
            // brought over: it works in that folder on that branch again
            let back = Map::from_iter([("worktree".to_string(), json!(path)), ("branch".to_string(), json!(branch))]);
            newteam::rewrite_role(&self.teams.join(&rec.team), &rec.role, &back).map_err(|e| err(e.message))?;
        }
        let rest = Self::ignored(project).difference(&rec.notes.iter().cloned().collect()).cloned().collect();
        let _ = Self::set_ignored(project, &rest);
        lock(&self.state).undos.remove(id);
        self.write_log(json!({"id": uuid::Uuid::new_v4().simple().to_string(), "team": rec.team, "agent": rec.role,
                              "action": "undo-merge", "of": id, "undo": false, "title": format!("Undid the merge of {}", rec.role),
                              "reason": "Its work is back, ready to review."}));
        Ok((json!({"role": rec.role, "base": base}), rec.team))
    }

    pub fn discard(&self, team: &str, role: &str, tip: &str) -> Result<Value, ReviewError> {
        let out = self.discarded(team, role, tip)?;
        tasks::discarded(team, role, None);
        Ok(out)
    }

    fn discarded(&self, team: &str, role: &str, expected: &str) -> Result<Value, ReviewError> {
        let _op = lock(&self.lock);
        let (project, _, branch, path) = self.worktree(team, role)?;
        Self::same_tip(&project, &branch, expected, role)?;
        let notes: BTreeSet<String> =
            self.notes(&project, role, &branch).iter().filter_map(|n| n["id"].as_str().map(String::from)).collect();
        Self::set_ignored(&project, &Self::ignored(&project).union(&notes).cloned().collect())
            .map_err(|e| err(e.to_string()))?;
        (lock(&self.on_archive))(team, role);
        let problem = self.archive(team, role, [&project, &branch, &path], expected, true);
        self.write_log(
            json!({"id": uuid::Uuid::new_v4().simple().to_string(), "team": team, "agent": role, "action": "discard",
                              "undo": false, "title": format!("Discarded {role}'s work"),
                              "reason": problem.as_deref().unwrap_or("Its copy was removed.")}),
        );
        Ok(json!({"role": role, "problem": problem}))
    }

    pub fn pull_request(&self, team: &str, role: &str) -> Result<Value, ReviewError> {
        let (project, base, branch, _) = self.worktree(team, role)?;
        let origin = git(&project, &["remote", "get-url", "origin"]);
        if !origin.ok() {
            return Err(err("This project has no remote called origin, so there's nowhere to open a pull request."));
        }
        // a password prompt would hang the brain
        let env = crate::runtime::clean_env(&std::env::vars().collect())
            .into_iter()
            .chain([("GIT_TERMINAL_PROMPT".into(), "0".into())]);
        let mut push = Command::new("git");
        push.args(["-C", &project, "push", "-u", "origin", &branch]).env_clear().envs(env);
        let pushed = setup::run(push, None, PUSH_TIMEOUT).map_err(|e| err(format!("Couldn't push the branch: {e}")))?;
        let pushed = pushed.ok_or_else(|| err("Couldn't push the branch: it took over 2 minutes."))?;
        if !pushed.status.success() {
            return Err(err(format!("Couldn't push the branch: {}", String::from_utf8_lossy(&pushed.stderr).trim())));
        }
        let logged = |title: &str| {
            self.write_log(json!({"id": uuid::Uuid::new_v4().simple().to_string(), "team": team, "agent": role, "undo": false,
                                  "action": "pull-request", "title": title, "reason": format!("Pushed {branch} to origin.")}));
        };
        let elsewhere = json!({"url": null, "detail": "The branch is pushed. Open the pull request on your git host."});
        let Some(gh) = setup::agent_path("gh") else {
            logged(&format!("Pushed {role}'s branch for a pull request"));
            let github = Regex::new(r"github\.com[:/](.+?)(?:\.git)?/?$").expect("a pattern");
            return Ok(github.captures(origin.out.trim()).map_or(
                elsewhere,
                |g| json!({"url": format!("https://github.com/{}/compare/{base}...{branch}?expand=1", &g[1])}),
            ));
        };
        let mut pr = Command::new(gh);
        pr.args(["pr", "create", "--fill", "--base", &base, "--head", &branch]).current_dir(&project);
        let Some(r) = setup::run(pr, None, PUSH_TIMEOUT).map_err(|e| err(e.to_string()))? else {
            return Err(err("The branch is pushed, but the pull request didn't open: it took over 2 minutes."));
        };
        let (stdout, stderr) = (String::from_utf8_lossy(&r.stdout), String::from_utf8_lossy(&r.stderr));
        let url = Regex::new(r"https://\S+")
            .expect("a pattern")
            .find(&format!("{stdout}{stderr}"))
            .map(|m| m.as_str().to_string());
        if let Some(url) = url.filter(|_| r.status.success() || stderr.contains("already exists")) {
            logged(&format!("Opened a pull request for {role}"));
            return Ok(json!({"url": url}));
        }
        if stderr.contains("known GitHub host") {
            // a remote gh knows nothing about: nothing went wrong
            logged(&format!("Pushed {role}'s branch for a pull request"));
            return Ok(elsewhere);
        }
        logged(&format!("Pushed {role}'s branch; the pull request didn't open"));
        Err(err(format!("The branch is pushed, but the pull request didn't open: {}", stderr.trim())))
    }

    /// Send the agent back to work; one that stopped starts again in the same worktree.
    pub fn ask(&self, team: &str, role: &str, text: &str) -> Result<Value, ReviewError> {
        let text = text.trim();
        if text.is_empty() {
            return Err(err("Say what should change."));
        }
        self.worktree(team, role)?;
        let agent = self.agent(team, role);
        let restarted = agent["live"] == true;
        if restarted {
            self.rt.send(agent["address"].as_str().unwrap_or(""), text, 30.0).map_err(|e| err(e.message))?;
        } else {
            let stopped = |m: String, detail: Option<String>| ReviewError {
                message: format!("{role} has stopped and couldn't start again: {m}"),
                detail: detail.unwrap_or_default(),
            };
            let tm = crate::preset::load(&self.teams.join(team), true).map_err(|e| stopped(e.0, None))?;
            let started = team::start(&tm, Some(text), &self.rt, &|_| true, Some(role))
                .map_err(|e| stopped(e.message, e.detail))?;
            if let Some(s) = started.first().filter(|s| s.state != "running") {
                return Err(stopped(if s.note.is_empty() { s.state.clone() } else { s.note.clone() }, None));
            }
        }
        // never the words: they can be an agent's
        self.write_log(json!({"id": uuid::Uuid::new_v4().simple().to_string(), "team": team, "agent": role, "action": "ask-changes",
                              "undo": false, "title": format!("Asked {role} for changes"), "reason": ""}));
        Ok(json!({"restarted": !restarted}))
    }

    pub fn note(&self, team: &str, role: &str, note: &str, action: &str) -> Result<Value, ReviewError> {
        let (project, _, branch, _) = self.worktree(team, role)?;
        let n = self
            .notes(&project, role, &branch)
            .into_iter()
            .find(|x| x["id"] == note)
            .ok_or_else(|| err("This note has changed. Take another look."))?;
        match action {
            "fix" => {
                let (author, file, line, text) =
                    (text_of(&n["author"]), text_of(&n["file"]), text_of(&n["line"]), text_of(&n["text"]));
                self.ask(team, role, &format!("{author} on {file} line {line}: {text}\n\nPlease fix it and commit."))?;
            }
            "ignore" => {}
            _ => return Err(err(format!("Unknown action {action:?}."))),
        }
        let ids = Self::ignored(&project).into_iter().chain([note.to_string()]).collect();
        Self::set_ignored(&project, &ids).map_err(|e| err(e.to_string()))?;
        Ok(json!({"ok": true}))
    }

    pub fn open_editor(&self, team: &str, role: &str, file: &str, line: Option<i64>) -> Result<Value, ReviewError> {
        let (project, base, branch, path) = self.worktree(team, role)?;
        if !self.files(&project, &base, &branch).iter().any(|f| f["path"] == file) {
            return Err(err("That file isn't in this diff.")); // the app's word isn't enough to open a path
        }
        let full = Path::new(&path).join(file).to_string_lossy().into_owned();
        if !Path::new(&full).is_file() {
            return Err(err("This file was deleted on this branch."));
        }
        let where_ = match line.filter(|l| *l > 0) {
            Some(l) => format!("{full}:{l}"),
            None => full.clone(),
        };
        let editor = EDITORS.iter().find_map(|e| setup::agent_path(e).map(|p| (*e, p)));
        let (program, args) = match editor {
            None => (PathBuf::from("open"), vec!["-t".to_string(), full]),
            Some(("cursor" | "code", p)) => (p, vec!["--goto".into(), where_]),
            Some((_, p)) => (p, vec![where_]),
        };
        let mut cmd = Command::new(program);
        cmd.args(args);
        let r =
            setup::run(cmd, None, Duration::from_secs(15)).map_err(|e| err(format!("Couldn't open an editor: {e}")))?;
        let r = r.ok_or_else(|| err("Couldn't open an editor: it took too long."))?;
        if !r.status.success() {
            return Err(err(format!("Couldn't open an editor: {}", String::from_utf8_lossy(&r.stderr).trim())));
        }
        Ok(json!({"ok": true}))
    }

    pub fn handlers(this: Arc<Review>) -> Handlers {
        #[derive(Deserialize, Default)]
        #[serde(default)]
        struct P {
            team: String,
            role: String,
            tip: String,
            id: String,
            text: String,
            note: String,
            action: String,
            file: String,
            force: bool,
            retest: bool,
            line: Option<i64>,
        }
        let h = |f: fn(&Review, P) -> Result<Value, ReviewError>| -> Handler {
            let this = this.clone();
            Box::new(move |p, _| {
                let p = if p.is_null() { json!({}) } else { p };
                Ok(f(&this, serde_json::from_value(p).map_err(|e| BrainError::new(e.to_string()))?)?)
            })
        };
        vec![
            ("review.waiting", h(|r, _| Ok(json!(r.waiting())))),
            ("review.get", h(|r, p| r.get(&p.team, &p.role, p.retest))),
            ("review.merge", h(|r, p| r.merge(&p.team, &p.role, &p.tip, p.force))),
            ("review.undo", h(|r, p| r.undo(&p.id))),
            ("review.discard", h(|r, p| r.discard(&p.team, &p.role, &p.tip))),
            ("review.pull_request", h(|r, p| r.pull_request(&p.team, &p.role))),
            ("review.ask", h(|r, p| r.ask(&p.team, &p.role, &p.text))),
            ("review.note", h(|r, p| r.note(&p.team, &p.role, &p.note, &p.action))),
            ("review.open_editor", h(|r, p| r.open_editor(&p.team, &p.role, &p.file, p.line))),
        ]
    }
}

use crate::server::Handler;
use sha1::Digest;

fn base_name(project: &str) -> String {
    Path::new(project).file_name().map_or_else(String::new, |n| n.to_string_lossy().into_owned())
}

fn has_branch(project: &str, branch: &str) -> bool {
    git(project, &["rev-parse", "--verify", "--quiet", &format!("refs/heads/{branch}")]).ok()
}

fn tip(project: &str, branch: &str) -> String {
    git(project, &["rev-parse", &format!("refs/heads/{branch}")]).out.trim().to_string()
}

fn dirty(path: &str) -> bool {
    Path::new(path).is_dir() && !git(path, &["status", "--porcelain"]).out.trim().is_empty()
}

/// None when git can't tell (too old, say), else the conflicting files.
fn conflicts(project: &str, base: &str, branch: &str) -> Option<Vec<String>> {
    let r = git(project, &["merge-tree", "--write-tree", "--name-only", "--no-messages", base, branch]);
    match r.code {
        0 => Some(vec![]),
        1 => Some(r.out.lines().skip(1).filter(|l| !l.is_empty()).map(String::from).collect()),
        _ => None,
    }
}

/// The folder that has `branch` checked out, if any.
fn checkout_of(project: &str, branch: &str) -> Option<String> {
    let (mut at, want) = (None, format!("branch refs/heads/{branch}"));
    for line in git(project, &["worktree", "list", "--porcelain"]).out.lines() {
        if let Some(w) = line.strip_prefix("worktree ") {
            at = Some(w.to_string());
        } else if line == want {
            return at;
        }
    }
    None
}

/// In a throwaway checkout of the commit: the result is about exactly that commit, and whatever the tests generate
/// can't look like the agent's uncommitted work.
fn run_tests(state: &Mutex<State>, project: &str, path: &str, tip: &str, cmd: &[String]) {
    let gate = lock(state).test_locks.entry(project.to_string()).or_default().clone();
    let _one_suite_at_a_time = lock(&gate);
    let checkout = format!("{project}/.assieme/test-runs/{tip}");
    git(project, &["worktree", "remove", "--force", &checkout]); // left behind if the brain stopped mid-run
    let cant = || check("none", "Couldn't run the tests", "");
    let result = if !git(project, &["worktree", "add", "--detach", &checkout, tip]).ok() {
        cant()
    } else {
        for name in DEPENDENCIES {
            // installed in the agent's worktree, and not part of the commit
            let (from, to) = (Path::new(path).join(name), Path::new(&checkout).join(name));
            if from.is_dir() && !to.exists() {
                let _ = std::os::unix::fs::symlink(from, to);
            }
        }
        let env = crate::runtime::clean_env(&std::env::vars().collect()); // no watch mode, no colour codes
        let mut run = Command::new(&cmd[0]);
        run.args(&cmd[1..]).current_dir(&checkout).env_clear().envs(env).env("CI", "1").env("NO_COLOR", "1");
        match setup::run(run, None, TESTS_TIMEOUT) {
            Ok(Some(o)) => {
                let output = format!("{}{}", String::from_utf8_lossy(&o.stdout), String::from_utf8_lossy(&o.stderr));
                test_result(cmd, o.status.code().unwrap_or(-1), &output)
            }
            Ok(None) => check("bad", "Tests took over 15 minutes, so they were stopped", ""),
            Err(_) => cant(),
        }
    };
    lock(state).tests.insert((project.to_string(), tip.to_string()), result);
    git(project, &["worktree", "remove", "--force", &checkout]);
}

#[cfg(test)]
#[path = "../tests/testkit/mod.rs"]
mod testkit;

/// Merge safety, against real temporary repositories: what a merge, an undo and an archive do to git, and what
/// they refuse to touch. `merged`, `undone` and `discarded` are the public methods without the board's update.
#[cfg(test)]
mod tests {
    use super::testkit::{FakeCore, Temp};
    use super::*;

    fn sh(dir: &Path, args: &[&str]) -> String {
        let o = Command::new("git").arg("-C").arg(dir).args(args).output().unwrap();
        assert!(o.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&o.stderr));
        String::from_utf8_lossy(&o.stdout).trim().to_string()
    }

    fn commit(dir: &Path, file: &str, text: &str) {
        std::fs::write(dir.join(file), text).unwrap();
        sh(dir, &["add", file]);
        sh(dir, &["commit", "-q", "-m", file]);
    }

    /// A project with a team `team` whose agent `dev` has a worktree and a branch, and a core with `dev` live or not.
    struct World {
        _tmp: Temp,
        core: FakeCore,
        review: Review,
        project: PathBuf,
        dev: PathBuf,
        team: String,
    }

    fn world(team: &str, live: bool) -> World {
        let tmp = Temp::new();
        let (project, teams, sock) = (tmp.git_repo("proj"), tmp.0.join("teams"), tmp.0.join("core.sock"));
        std::fs::create_dir_all(teams.join(team)).unwrap();
        std::fs::write(teams.join(team).join("team.toml"), format!("project = {:?}\n", project.display())).unwrap();
        let dev = project.join(".assieme/worktrees/dev");
        sh(&project, &["worktree", "add", "-q", "-b", &format!("assieme/{team}/dev"), dev.to_str().unwrap()]);
        let labels = json!({TEAM: team, ROLE: "dev"});
        let rows = if live { json!([{"address": "t1", "labels": labels, "state": "live"}]) } else { json!([]) };
        let core = FakeCore::start(
            &sock,
            [("terminal.list", json!({"terminals": rows})), ("terminal.kill", json!(null))].into(),
        );
        sh(&project, &["config", "user.name", "t"]);
        sh(&project, &["config", "user.email", "t@t"]);
        let mut review = Review::new(Runtime::new("t", Some(sock)), teams);
        review.log = Some(tmp.0.join("actions.jsonl"));
        World { _tmp: tmp, core, review, project, dev, team: team.into() }
    }

    impl World {
        fn tip(&self) -> String {
            sh(&self.project, &["rev-parse", &format!("refs/heads/assieme/{}/dev", self.team)])
        }
        fn main(&self) -> String {
            sh(&self.project, &["rev-parse", "refs/heads/main"])
        }
        fn merge(&self) -> Result<Value, ReviewError> {
            self.review.merged(&self.team, "dev", &self.tip(), false)
        }
        fn branch_exists(&self) -> bool {
            has_branch(self.project.to_str().unwrap(), &format!("assieme/{}/dev", self.team))
        }
    }

    /// A merge that cannot go through is refused with a sentence, and leaves main, the branch and the folder as they were.
    #[test]
    fn refuses_what_would_hurt() {
        // (name, how the world is made unsafe, what the refusal says)
        type Setup = fn(&World);
        let cases: [(&str, Setup, &str); 5] = [
            (
                "conflict",
                |w| {
                    commit(&w.dev, "README", "dev\n");
                    commit(&w.project, "README", "main\n");
                },
                "conflicts with main in README",
            ),
            (
                "dirty",
                |w| {
                    commit(&w.dev, "a", "a\n");
                    std::fs::write(w.dev.join("loose"), "x").unwrap();
                },
                "uncommitted changes",
            ),
            (
                "local",
                |w| {
                    commit(&w.dev, "README", "dev\n");
                    std::fs::write(w.project.join("README"), "mine\n").unwrap();
                },
                "uncommitted changes in the same files",
            ),
            ("nothing", |_| {}, "nothing new to merge"),
            (
                "busy",
                |w| {
                    commit(&w.dev, "a", "a\n");
                    std::fs::write(w.project.join(".git/MERGE_HEAD"), w.main()).unwrap();
                },
                "in the middle of a merge",
            ),
        ];
        for (name, unsafe_, says) in cases {
            let w = world(&format!("refuse-{name}"), false);
            unsafe_(&w);
            let (main, tip) = (w.main(), w.tip());
            let e = w.merge().unwrap_err();
            assert!(e.message.contains(says), "{name}: {}", e.message);
            let head = sh(&w.project, &["rev-parse", "HEAD"]);
            assert_eq!((w.main(), w.tip(), head), (main.clone(), tip, main), "{name}: main moved");
            assert!(w.branch_exists() && w.dev.is_dir(), "{name}: the agent's copy went");
            let marker = w.project.join(".git/MERGE_HEAD");
            assert_eq!(marker.exists(), name == "busy", "{name}: a half-done merge was left");
        }
    }

    /// A commit added after the person looked is never merged unseen.
    #[test]
    fn refuses_a_tip_it_was_not_shown() {
        let w = world("stale", false);
        commit(&w.dev, "a", "a\n");
        let seen = w.tip();
        commit(&w.dev, "b", "b\n");
        let e = w.review.merged(&w.team, "dev", &seen, false).unwrap_err();
        assert!(e.message.contains("added commits since you looked"));
        assert_eq!(sh(&w.project, &["rev-list", "--count", "main"]), "1");
    }

    /// Merge, then undo: main is back where it was, and the agent's branch and folder with its work.
    #[test]
    fn merges_and_undoes_with_main_checked_out_or_not() {
        for detached in [false, true] {
            let w = world(&format!("undo-{detached}"), true);
            commit(&w.dev, "a.txt", "a\n");
            let (before, tip) = (w.main(), w.tip());
            if detached {
                sh(&w.project, &["checkout", "-q", "--detach"]); // main is checked out nowhere
            }
            let done = w.merge().unwrap();
            assert_eq!(done["problem"], Value::Null, "{done}");
            assert_eq!(
                sh(&w.project, &["rev-list", "--parents", "-n1", "main"]).split(' ').count(),
                3,
                "a merge commit"
            );
            assert_eq!(sh(&w.project, &["show", "main:a.txt"]), "a");
            assert!(!w.branch_exists() && !w.dev.exists(), "archived");
            assert_eq!(w.core.calls("terminal.kill"), [json!({"terminal": "t1"})], "its agent was stopped");
            let uid = done["undo"].as_str().unwrap();
            let logged = std::fs::read_to_string(w.review.log.as_ref().unwrap()).unwrap();
            let row: Value =
                logged.lines().filter_map(|l| serde_json::from_str(l).ok()).find(|r: &Value| r["id"] == uid).unwrap();
            // a brain restart: the record survives only in the logged row
            lock(&w.review.state).undos.clear();
            w.review.remember(uid, &row["restore"]);

            let (out, team) = w.review.undone(uid).unwrap();
            assert_eq!((out["role"].as_str(), team.as_str()), (Some("dev"), w.team.as_str()));
            assert_eq!((w.main(), w.tip()), (before, tip));
            assert!(w.dev.join("a.txt").is_file(), "its copy is back with its work");
            assert!(w.review.undone(uid).is_err(), "an undo is used once");
        }
    }

    /// An undo that would take someone else's work with it, or run into a restarted agent, is refused.
    #[test]
    fn refuses_to_undo_over_later_changes() {
        let (w, again) = (world("moved", false), world("restarted", false));
        for (w, make_later, says) in [
            (&w, (|w: &World| commit(&w.project, "later", "x\n")) as fn(&World), "moved on"),
            (&again, |w| drop(sh(&w.project, &["branch", &format!("assieme/{}/dev", w.team)])), "started again"),
        ] {
            commit(&w.dev, "a", "a\n");
            let uid = w.merge().unwrap()["undo"].as_str().unwrap().to_string();
            make_later(w);
            let after = w.main();
            let e = w.review.undone(&uid).unwrap_err();
            assert!(e.message.contains(says), "{}", e.message);
            assert_eq!(w.main(), after, "main was left alone");
        }
    }

    /// Discarding keeps the work under refs/assieme before it removes anything, even from a dirty folder.
    #[test]
    fn discard_keeps_the_tip_and_stops_the_preview() {
        let w = world("discard", true);
        commit(&w.dev, "a", "a\n");
        std::fs::write(w.dev.join("loose"), "x").unwrap();
        let (tip, main) = (w.tip(), w.main());
        let stopped = Arc::new(Mutex::new(vec![]));
        let seen = stopped.clone();
        w.review.set_on_archive(Box::new(move |t, r| {
            seen.lock().unwrap().push(format!("{t}/{r}"));
            json!({"stopped": true})
        }));
        let out = w.review.discarded(&w.team, "dev", &tip).unwrap();
        assert_eq!(out["problem"], Value::Null, "{out}");
        assert_eq!(*stopped.lock().unwrap(), ["discard/dev"]);
        assert!(!w.branch_exists() && !w.dev.exists());
        assert_eq!(w.main(), main);
        let kept = sh(&w.project, &["for-each-ref", "--format=%(objectname)", "refs/assieme/archive/discard/dev/"]);
        assert_eq!(kept, tip, "the work is still in git");
        assert_eq!(w.core.calls("terminal.kill").len(), 1);
    }
}
