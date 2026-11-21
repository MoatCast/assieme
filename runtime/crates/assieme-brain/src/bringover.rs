//! Port of `assieme/bringover.py`, owned by session F (see OWNERS.md).
//!
//! Bring your team over (step 30): agent work from other tools on this Mac, as a New team draft.
//!
//! Two layers. The general scan works for any tool that uses git worktrees: it looks in the repositories Claude Code and
//! Codex worked in, and lists worktrees with work in them, with the agent and conversation found for each folder. On top,
//! one reader per tool we know adds what only that tool records (names, scripts, tasks).
//!
//! Everything here only reads. It never writes, moves or deletes the other tool's files, opens databases read-only, and
//! never sends to or stops another tool's terminals.
use crate::herdr::{py_basename, py_dirname};
use crate::prelude::*;
use rusqlite::types::Value as Sql;
use serde::Serialize;
use serde_json::json;
use std::time::{Duration, Instant, UNIX_EPOCH};

/// Per repository: a slow or broken one is listed as unreadable and skipped.
const LIMIT: Duration = Duration::from_secs(5);
const DAYS: i64 = 30;
const AGENT_PROGRAMS: [(&str, &str); 6] = [
    ("claude", "claude"),
    ("codex", "codex"),
    ("gemini", "gemini"),
    ("opencode", "opencode"),
    ("cursor-agent", "cursor"),
    ("agy", "antigravity"),
];

/// One workspace of another tool.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub(crate) struct Found {
    /// conductor, claude-squad, vibe-kanban, herdr or other
    pub tool: String,
    /// The repository's main checkout.
    pub project: String,
    /// The workspace's own name, e.g. "tokyo".
    pub name: String,
    /// None when only its branch is left.
    pub folder: Option<String>,
    pub branch: String,
    /// Commits not on the default branch.
    pub ahead: i64,
    /// Uncommitted changes.
    pub dirty: bool,
    pub kind: Option<String>,
    pub model: Option<String>,
    pub session: Option<String>,
    pub running_elsewhere: bool,
    pub notes: Vec<String>,
}

impl Found {
    fn new(tool: &str, project: &str, name: &str, folder: Option<String>, branch: &str) -> Found {
        Found {
            tool: tool.into(),
            project: project.into(),
            name: name.into(),
            folder,
            branch: branch.into(),
            ahead: 0,
            dirty: false,
            kind: None,
            model: None,
            session: None,
            running_elsewhere: false,
            notes: vec![],
        }
    }
}

/// One Claude Code or Codex session used lately.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub(crate) struct Sess {
    pub kind: String,
    pub cwd: String,
    pub session: String,
    pub model: Option<Value>,
    pub at: f64,
}

/// An agent program running, from its working folder.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Proc {
    pub kind: String,
    pub cwd: String,
}

fn real(p: &str) -> String {
    crate::herdr::realpath(p)
}

// --- what agents ran where -------------------------------------------------------------------------------------------

/// The first `n` JSON rows of a log; bad lines are skipped. A line ends at `\n`, `\r\n` or a lone `\r`, as Python's text
/// files read it.
fn rows(path: &Path, n: usize) -> Vec<Value> {
    let Ok(bytes) = std::fs::read(path) else { return vec![] };
    let text = String::from_utf8_lossy(&bytes).replace("\r\n", "\n").replace('\r', "\n");
    let mut out = vec![];
    for line in text.split_inclusive('\n') {
        if let Ok(v) = crate::pyrules::json_loads_bytes(line.as_bytes()) {
            out.push(v);
        }
        if out.len() >= n {
            break;
        }
    }
    out
}

fn files(pattern: &str) -> Vec<PathBuf> {
    let options =
        glob::MatchOptions { case_sensitive: true, require_literal_separator: true, require_literal_leading_dot: true };
    let mut out: Vec<PathBuf> = glob::glob_with(pattern, options).map(|g| g.flatten().collect()).unwrap_or_default();
    out.sort();
    out
}

fn escaped(dir: &Path) -> String {
    glob::Pattern::escape(&dir.to_string_lossy())
}

fn mtime(path: &Path) -> Option<f64> {
    path.metadata().ok()?.modified().ok()?.duration_since(UNIX_EPOCH).ok().map(|d| d.as_secs_f64())
}

fn text(v: &Value) -> Option<&str> {
    v.as_str()
}

/// Each Claude Code and Codex session used in the last `days`.
pub(crate) fn history(claude_dir: &Path, codex_dir: &Path, now: Timestamp, days: i64) -> Vec<Sess> {
    let cutoff = now.as_second() as f64 + f64::from(now.subsec_nanosecond()) / 1e9 - (days * 86400) as f64;
    let mut out = vec![];
    for path in files(&format!("{}/*/*.jsonl", escaped(claude_dir))) {
        let Some(at) = mtime(&path).filter(|at| *at >= cutoff) else { continue };
        let rows = rows(&path, 200);
        let cwd = rows.iter().find_map(|r| r.get("cwd").and_then(text));
        let model = rows.iter().find_map(|r| {
            r.get("message").filter(|m| m.is_object()).and_then(|m| m.get("model")).filter(|m| m.is_string())
        });
        if let Some(cwd) = cwd.filter(|c| !c.is_empty()) {
            let session = path.file_stem().map_or(String::new(), |s| s.to_string_lossy().into_owned());
            out.push(Sess { kind: "claude".into(), cwd: cwd.into(), session, model: model.cloned(), at });
        }
    }
    for path in files(&format!("{}/*/*/*/*.jsonl", escaped(codex_dir))) {
        let Some(at) = mtime(&path).filter(|at| *at >= cutoff) else { continue };
        let rows: Vec<Value> = rows(&path, 50).into_iter().filter(|r| r["payload"].is_object()).collect();
        let meta = rows.iter().find(|r| r["type"] == "session_meta").map_or(json!({}), |r| r["payload"].clone());
        let model = rows
            .iter()
            .find(|r| r["type"] == "turn_context")
            .map(|r| r["payload"].get("model").cloned().unwrap_or(Value::Null));
        let id = meta.get("id").filter(|i| crate::pyrules::truthy(i));
        if let (Some(cwd), Some(id)) = (meta["cwd"].as_str(), id) {
            let session = id.as_str().map_or_else(|| id.to_string(), String::from);
            out.push(Sess {
                kind: "codex".into(),
                cwd: cwd.into(),
                session,
                model: model.filter(|m| !m.is_null()),
                at,
            });
        }
    }
    out
}

/// The agent programs Assieme knows that are running, from their working folders (`lsof`, as the Python brain asked it:
/// `sysinfo` would add crates the notices script can't give a license text for).
pub(crate) fn running_agents() -> Vec<Proc> {
    let mut cmd = std::process::Command::new("lsof");
    cmd.args(["-a", "-d", "cwd", "-Fpcn"]);
    for (program, _) in AGENT_PROGRAMS {
        cmd.args(["-c", program]);
    }
    let Ok(Some(listed)) = crate::setup::run(cmd, None, LIMIT) else { return vec![] };
    parse_lsof(&String::from_utf8_lossy(&listed.stdout))
}

/// `lsof -Fpcn` lines: a `p` line starts a process, its `c` line names its command, each `n` line is a folder.
fn parse_lsof(text: &str) -> Vec<Proc> {
    let (mut out, mut kind) = (vec![], None::<&str>);
    for line in text.lines() {
        let (tag, value) = line.split_at(line.chars().next().map_or(0, char::len_utf8));
        match tag {
            "p" => kind = None,
            // lsof cuts a command's name at nine characters
            "c" => kind = AGENT_PROGRAMS.iter().find(|(p, _)| value.starts_with(&p[..p.len().min(9)])).map(|(_, k)| *k),
            "n" => out.extend(kind.map(|k| Proc { kind: k.into(), cwd: value.to_string() })),
            _ => {}
        }
    }
    out
}

// --- repositories and their worktrees --------------------------------------------------------------------------------

/// A git question asked within the time left, or an error when it ran out or git couldn't start.
fn git(cwd: &str, args: &[&str], deadline: Instant) -> std::io::Result<std::process::Output> {
    let left = deadline.saturating_duration_since(Instant::now());
    if left.is_zero() {
        return Err(std::io::ErrorKind::TimedOut.into());
    }
    let mut cmd = std::process::Command::new("git");
    cmd.arg("-C").arg(cwd).args(args);
    crate::setup::run(cmd, None, left)?.ok_or_else(|| std::io::ErrorKind::TimedOut.into())
}

fn out(o: &std::process::Output) -> String {
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

fn main_checkout(folder: &str, deadline: Instant) -> std::io::Result<Option<String>> {
    let r = git(folder, &["rev-parse", "--path-format=absolute", "--git-common-dir"], deadline)?;
    if !r.status.success() {
        return Ok(None);
    }
    let common = out(&r);
    Ok(if py_basename(&common) == ".git" { Some(py_dirname(&common)) } else { None })
}

/// The main checkout of each repository the sessions ran in, once each, in the order first seen.
pub(crate) fn repos_from_history(sessions: &[Sess]) -> Vec<String> {
    let mut out: Vec<String> = vec![];
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    for cwd in sessions.iter().map(|s| s.cwd.as_str()) {
        // one git call per folder, however many sessions
        if !seen.insert(cwd) || cwd.is_empty() || !Path::new(cwd).is_dir() {
            continue;
        }
        let Ok(Some(main)) = main_checkout(cwd, Instant::now() + LIMIT) else { continue };
        let main = real(&main);
        if !out.contains(&main) {
            out.push(main);
        }
    }
    out
}

/// Which tool a worktree belongs to, from where it is.
pub(crate) fn tool_of(folder: &str, home: &str) -> &'static str {
    let f = format!("{}/", folder.trim_end_matches('/'));
    if f.starts_with(&format!("{home}/conductor/")) || f.contains("/.conductor/") {
        "conductor"
    } else if f.starts_with(&format!("{home}/.claude-squad/")) {
        "claude-squad"
    } else if f.contains("/vibe-kanban/worktrees/") || f.contains("/.vibe-kanban-workspaces/") {
        "vibe-kanban"
    } else {
        "other"
    }
}

fn base_branch(repo: &str, deadline: Instant) -> std::io::Result<Option<String>> {
    let head = git(repo, &["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], deadline)?;
    let mut names: Vec<String> = if head.status.success() {
        vec![out(&head).strip_prefix("origin/").unwrap_or(&out(&head)).to_string()]
    } else {
        vec![]
    };
    names.extend(["main".to_string(), "master".to_string()]);
    for name in names {
        if git(repo, &["rev-parse", "--verify", "--quiet", &format!("refs/heads/{name}")], deadline)?.status.success() {
            return Ok(Some(name));
        }
    }
    Ok(None)
}

/// (path, branch) of the repository's worktrees on a branch, its main checkout first.
fn worktrees(repo: &str, deadline: Instant) -> std::io::Result<Vec<(String, String)>> {
    let r = git(repo, &["worktree", "list", "--porcelain"], deadline)?;
    if !r.status.success() {
        return Err(std::io::Error::other(String::from_utf8_lossy(&r.stderr).trim().to_string()));
    }
    let (mut found, mut path) = (vec![], None::<String>);
    for line in String::from_utf8_lossy(&r.stdout).lines() {
        if let Some(p) = line.strip_prefix("worktree ") {
            path = Some(p.to_string());
        } else if let (Some(b), Some(p)) = (line.strip_prefix("branch refs/heads/"), &path) {
            found.push((p.clone(), b.to_string()));
        }
    }
    Ok(found)
}

fn inside(path: &str, folder: &str) -> bool {
    let (path, folder) = (real(path), real(folder));
    path == folder || path.starts_with(&format!("{folder}/"))
}

fn branch_of(folder: &str) -> String {
    git(folder, &["branch", "--show-current"], Instant::now() + LIMIT)
        .ok()
        .filter(|r| r.status.success())
        .map_or(String::new(), |r| out(&r))
}

/// The main checkout of the repository `path` is in, or None.
fn project_of(path: &str) -> Option<String> {
    if !Path::new(path).is_dir() {
        return None;
    }
    main_checkout(path, Instant::now() + LIMIT).ok().flatten().map(|m| real(&m))
}

/// Assieme's agent kind for a command line another tool ran, or None.
fn kind_of(program: Option<&str>) -> Option<&'static str> {
    let first = program.unwrap_or("").split_whitespace().next().map_or(String::new(), py_basename);
    AGENT_PROGRAMS
        .iter()
        .find(|(p, _)| *p == first)
        .map(|(_, k)| *k)
        .or_else(|| AGENT_PROGRAMS.iter().find(|(_, k)| *k == first).map(|(_, k)| *k))
}

// --- Claude Squad: ~/.claude-squad/state.json ------------------------------------------------------------------------

/// Its statuses: 0 running, 1 ready, 2 loading, 3 paused (worktree removed, branch kept).
const CS_RUNNING_UNTIL: i64 = 3;

/// (found, problem).
fn read_claude_squad(home: &Path) -> (Vec<Found>, Option<String>) {
    let path = home.join(".claude-squad/state.json");
    if !path.is_file() {
        return (vec![], None);
    }
    let sorry = || {
        (
            vec![],
            Some(
                "Couldn't read Claude Squad's sessions. Its agents' copies are still listed under Other agent work."
                    .to_string(),
            ),
        )
    };
    let Ok(state) =
        std::fs::read(&path).map_err(|_| ()).and_then(|b| crate::pyrules::json_loads_bytes(&b).map_err(|_| ()))
    else {
        return sorry();
    };
    let Some(state) = state.as_object() else { return sorry() };
    let mut instances = state.get("instances").filter(|i| crate::pyrules::truthy(i)).cloned().unwrap_or(json!([]));
    if let Some(s) = instances.as_str() {
        // some versions keep the list as a JSON string
        match crate::pyrules::json_loads_bytes(s.as_bytes()) {
            Ok(v) => instances = v,
            Err(_) => return sorry(),
        }
    }
    let mut found = vec![];
    for i in instances.as_array().into_iter().flatten() {
        let wt = &i["worktree"];
        let Some(repo) = wt["repo_path"].as_str().filter(|r| !r.is_empty()) else { continue };
        let folder = wt["worktree_path"].as_str().filter(|f| !f.is_empty() && Path::new(f).is_dir());
        let project = project_of(folder.unwrap_or(repo)).unwrap_or_else(|| real(repo));
        let kind = kind_of(i["program"].as_str());
        let notes = if kind.is_some() {
            vec![]
        } else {
            let program = i["program"].as_str().filter(|p| !p.is_empty()).unwrap_or("an unknown program");
            vec![format!("It ran {}, which Assieme doesn't run yet.", program.split_whitespace().next().unwrap_or(""))]
        };
        let title = i["title"]
            .as_str()
            .filter(|t| !t.is_empty())
            .map_or_else(|| py_basename(folder.unwrap_or("")), String::from);
        let branch = [&wt["branch_name"], &i["branch"]]
            .into_iter()
            .find_map(|b| b.as_str().filter(|b| !b.is_empty()))
            .unwrap_or("");
        let mut f = Found::new("claude-squad", &project, &title, folder.map(real), branch);
        f.kind = kind.map(String::from);
        f.running_elsewhere = i.get("status").and_then(Value::as_i64).unwrap_or(CS_RUNNING_UNTIL) < CS_RUNNING_UNTIL;
        f.notes = notes;
        found.push(f);
    }
    (found, None)
}

// --- Vibe Kanban: a SQLite database, opened read-only ----------------------------------------------------------------

const VK_UNSUPPORTED: &str =
    "This Vibe Kanban version isn't supported yet. Its agents' copies are still listed under Other agent work.";

/// What it needs from each table.
const VK_NEEDS: [(&str, &[&str]); 6] = [
    ("projects", &["id", "name"]),
    ("repos", &["id", "path", "setup_script", "archive_script", "dev_server_script"]),
    ("tasks", &["id", "project_id", "title", "description", "status"]),
    ("workspaces", &["id", "task_id", "container_ref", "branch", "archived", "worktree_deleted"]),
    ("workspace_repos", &["workspace_id", "repo_id"]),
    ("sessions", &["id", "workspace_id", "executor"]),
];

fn vk_status(s: &str) -> Option<&'static str> {
    Some(match s {
        "todo" => "todo",
        "inprogress" => "doing",
        "inreview" => "review",
        "done" => "done",
        "cancelled" => "cancelled",
        _ => return None,
    })
}

fn vk_agent(s: &str) -> Option<&'static str> {
    Some(match s {
        "CLAUDE_CODE" => "claude",
        "CODEX" => "codex",
        "GEMINI" => "gemini",
        "OPENCODE" => "opencode",
        "CURSOR_AGENT" | "CURSOR" => "cursor",
        _ => return None,
    })
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub(crate) struct VkTask {
    title: Option<String>,
    description: String,
    status: String,
    branch: Option<String>,
}

/// Scripts per project: `setup`, `run` and `archive`, where it has them.
type Scripts = BTreeMap<String, BTreeMap<String, String>>;
type VibeRead = (Vec<Found>, BTreeMap<String, Vec<VkTask>>, Scripts);

fn sql_text(v: &Sql) -> Option<String> {
    match v {
        Sql::Text(t) => Some(t.clone()),
        Sql::Integer(i) => Some(i.to_string()),
        Sql::Real(r) => Some(r.to_string()),
        Sql::Blob(b) => Some(String::from_utf8_lossy(b).into_owned()),
        Sql::Null => None,
    }
}

fn sql_truthy(v: &Sql) -> bool {
    match v {
        Sql::Null => false,
        Sql::Integer(i) => *i != 0,
        Sql::Real(r) => *r != 0.0,
        Sql::Text(t) => !t.is_empty(),
        Sql::Blob(b) => !b.is_empty(),
    }
}

/// A value as a lookup key: ids may be text or blobs.
fn key(v: &Sql) -> String {
    format!("{v:?}")
}

/// None when the database isn't one this reader knows.
fn vibe_query(con: &rusqlite::Connection) -> rusqlite::Result<Option<VibeRead>> {
    let cols = |t: &str| -> rusqlite::Result<BTreeSet<String>> {
        let mut st = con.prepare(&format!("PRAGMA table_info({t})"))?;
        let names = st.query_map([], |r| r.get::<_, String>(1))?.collect::<rusqlite::Result<_>>()?;
        Ok(names)
    };
    for (t, need) in VK_NEEDS {
        let have = cols(t)?;
        if !need.iter().all(|c| have.contains(*c)) {
            return Ok(None);
        }
    }
    let q = |sql: &str, params: &[&dyn rusqlite::ToSql]| -> rusqlite::Result<Vec<Vec<Sql>>> {
        let mut st = con.prepare(sql)?;
        let n = st.column_count();
        let rows =
            st.query_map(params, |r| (0..n).map(|i| r.get::<_, Sql>(i)).collect::<rusqlite::Result<Vec<_>>>())?;
        rows.collect()
    };
    // id -> (path, setup, archive, dev server), the first row of an id keeping its place
    let mut repos: Vec<(String, Vec<Sql>)> = vec![];
    for r in q("SELECT id, path, setup_script, archive_script, dev_server_script FROM repos", &[])? {
        match repos.iter_mut().find(|(k, _)| *k == key(&r[0])) {
            Some(slot) => slot.1 = r[1..].to_vec(),
            None => repos.push((key(&r[0]), r[1..].to_vec())),
        }
    }
    let mut scripts: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    for (_, r) in &repos {
        let Some(path) = sql_text(&r[0]) else { continue };
        let given = [("setup", &r[1]), ("run", &r[3]), ("archive", &r[2])];
        let got = given
            .into_iter()
            .filter(|(_, v)| sql_truthy(v))
            .filter_map(|(k, v)| Some((k.to_string(), sql_text(v)?)))
            .collect();
        scripts.insert(real(&path), got);
    }
    let resumable = cols("execution_processes")?.is_superset(&["id", "session_id"].map(String::from).into())
        && cols("coding_agent_turns")?
            .is_superset(&["execution_process_id", "agent_session_id"].map(String::from).into());
    // id -> (project id, title, description, status)
    let mut task_rows: Vec<(String, Vec<Sql>)> = vec![];
    for r in q("SELECT id, project_id, title, description, status FROM tasks", &[])? {
        match task_rows.iter_mut().find(|(k, _)| *k == key(&r[0])) {
            Some(slot) => slot.1 = r[1..].to_vec(),
            None => task_rows.push((key(&r[0]), r[1..].to_vec())),
        }
    }
    let task = |tid: &Sql| task_rows.iter().find(|(k, _)| *k == key(tid)).map(|(_, r)| r);
    let (mut project_repo, mut newest_branch): (BTreeMap<String, String>, BTreeMap<String, Option<String>>) =
        (BTreeMap::new(), BTreeMap::new());
    let mut found = vec![];
    let all = "SELECT id, task_id, container_ref, branch, archived, worktree_deleted FROM workspaces ORDER BY rowid";
    for w in q(all, &[])? {
        let (wid, tid, container, branch, archived, deleted) =
            (&w[0], &w[1], sql_text(&w[2]), sql_text(&w[3]), &w[4], &w[5]);
        let rid = q("SELECT repo_id FROM workspace_repos WHERE workspace_id = ?", &[wid])?
            .into_iter()
            .next()
            .map(|r| r[0].clone());
        let Some(repo) = rid.and_then(|rid| repos.iter().find(|(k, _)| *k == key(&rid)).map(|(_, r)| r)) else {
            continue;
        };
        let Some(project) = sql_text(&repo[0]).map(|p| real(&p)) else { continue };
        if let Some(t) = task(tid) {
            project_repo.insert(key(&t[0]), project.clone());
            newest_branch.insert(key(tid), branch.clone());
        }
        if sql_truthy(archived) {
            continue;
        }
        let mut folder = container.clone().filter(|c| !c.is_empty() && Path::new(c).is_dir() && !sql_truthy(deleted));
        if let Some(f) = folder.clone().filter(|f| Path::new(f).join(py_basename(&project)).is_dir()) {
            folder = Some(format!("{f}/{}", py_basename(&project))); // a workspace over several repositories
        }
        let sess = q("SELECT id, executor FROM sessions WHERE workspace_id = ? ORDER BY rowid DESC LIMIT 1", &[wid])?;
        let kind = sess.first().and_then(|s| vk_agent(&sql_text(&s[1]).unwrap_or_default().to_uppercase()));
        let mut session = None;
        if let (Some(s), true) = (sess.first(), resumable) {
            let latest = "SELECT t.agent_session_id FROM coding_agent_turns t JOIN execution_processes e ON t.execution_process_id = e.id \
                          WHERE e.session_id = ? AND t.agent_session_id IS NOT NULL ORDER BY t.rowid DESC LIMIT 1";
            session = q(latest, &[&s[0]])?.first().and_then(|t| sql_text(&t[0]));
        }
        let title = task(tid).and_then(|t| sql_text(&t[1])).filter(|t| !t.is_empty());
        let branch = branch.unwrap_or_default();
        let name = title.unwrap_or_else(|| py_basename(&container.filter(|c| !c.is_empty()).unwrap_or(branch.clone())));
        let mut f = Found::new("vibe-kanban", &project, &name, folder.map(|f| real(&f)), &branch);
        (f.kind, f.session) = (kind.map(String::from), session);
        found.push(f);
    }
    let mut tasks: BTreeMap<String, Vec<VkTask>> = BTreeMap::new();
    for (tid, r) in &task_rows {
        let (Some(project), Some(status)) =
            (project_repo.get(&key(&r[0])), sql_text(&r[3]).as_deref().and_then(vk_status))
        else {
            continue;
        };
        let task = VkTask {
            title: sql_text(&r[1]),
            description: sql_text(&r[2]).unwrap_or_default(),
            status: status.into(),
            branch: newest_branch.get(tid).cloned().flatten(),
        };
        tasks.entry(project.clone()).or_default().push(task);
    }
    Ok(Some((found, tasks, scripts)))
}

/// (found, tasks per project, scripts per project, problem).
fn read_vibe_kanban(home: &Path) -> (Vec<Found>, BTreeMap<String, Vec<VkTask>>, Scripts, Option<String>) {
    let path = home.join("Library/Application Support/ai.bloop.vibe-kanban/db.v2.sqlite");
    let unsupported = || (vec![], BTreeMap::new(), BTreeMap::new(), Some(VK_UNSUPPORTED.to_string()));
    if !path.is_file() {
        return (vec![], BTreeMap::new(), BTreeMap::new(), None);
    }
    let Ok(con) = rusqlite::Connection::open_with_flags(
        &path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) else {
        return unsupported();
    };
    let _ = con.busy_timeout(LIMIT);
    match vibe_query(&con) {
        Ok(Some((found, tasks, scripts))) => (found, tasks, scripts, None),
        _ => unsupported(),
    }
}

// --- Conductor: ~/conductor/workspaces/<repo>/<city>, older .conductor/<city>; scripts in conductor.json -------------

/// (found, scripts per project, problem). Its own database isn't documented, so it isn't read: the agent and its
/// conversation come from the general scan.
fn read_conductor(home: &Path) -> (Vec<Found>, Scripts, Option<String>) {
    let h = escaped(home);
    let mut folders = files(&format!("{h}/conductor/workspaces/*/*"));
    let repos = files(&format!("{h}/conductor/repos/*"));
    let mains: BTreeSet<String> =
        folders.iter().chain(&repos).filter_map(|f| project_of(&f.to_string_lossy())).collect();
    for m in &mains {
        folders.extend(files(&format!("{}/.conductor/*", glob::Pattern::escape(m))));
    }
    let folders: BTreeSet<String> = folders.iter().map(|f| f.to_string_lossy().into_owned()).collect();
    let mut found: Vec<Found> = vec![];
    for folder in &folders {
        let Some(project) = project_of(folder) else { continue };
        if real(folder) == project {
            continue;
        }
        found.push(Found::new("conductor", &project, &py_basename(folder), Some(real(folder)), &branch_of(folder)));
    }
    let projects: BTreeSet<String> = found.iter().map(|f| f.project.clone()).chain(mains).collect();
    let mut scripts = BTreeMap::new();
    for project in projects {
        let Some(given) = std::fs::read(Path::new(&project).join("conductor.json"))
            .ok()
            .and_then(|b| crate::pyrules::json_loads_bytes(&b).ok())
            .and_then(|c| c.get("scripts").filter(|s| crate::pyrules::truthy(s)).cloned())
        else {
            continue;
        };
        let given = given.as_object().cloned().unwrap_or_default();
        let kept = ["setup", "run", "archive"]
            .into_iter()
            .filter_map(|k| Some((k.to_string(), given.get(k)?.as_str().filter(|v| !v.is_empty())?.to_string())))
            .collect();
        scripts.insert(project, kept);
    }
    (found, scripts, None)
}

/// (found, unreadable). A worktree is listed when it is not the main checkout or Assieme's own, has work in it, and
/// either sits where a known tool keeps worktrees or had an agent in it.
pub(crate) fn scan(
    repos: &[String],
    sessions: &[Sess],
    procs: &[Proc],
    home: &str,
    limit: Duration,
) -> (Vec<Found>, Vec<String>) {
    let home = real(home);
    let (mut found, mut unreadable) = (vec![], vec![]);
    for repo in repos {
        let deadline = Instant::now() + limit;
        let attempt = || -> std::io::Result<Vec<Found>> {
            let base = base_branch(repo, deadline)?;
            let trees = worktrees(repo, deadline)?;
            let mut mine = vec![];
            for (path, branch) in trees.iter().skip(1) {
                if format!("{path}/").contains("/.assieme/worktrees/") || base.as_deref() == Some(branch.as_str()) {
                    continue;
                }
                let mut ahead = 0;
                if let Some(base) = &base {
                    let r = git(repo, &["rev-list", "--count", &format!("{base}..{branch}")], deadline)?;
                    ahead = if r.status.success() { out(&r).parse().unwrap_or(0) } else { 0 };
                }
                let dirty = !out(&git(path, &["status", "--porcelain"], deadline)?).is_empty();
                if ahead == 0 && !dirty {
                    continue;
                }
                let mut here: Vec<&Sess> =
                    sessions.iter().filter(|s| !s.cwd.is_empty() && inside(&s.cwd, path)).collect();
                here.sort_by(|a, b| a.at.total_cmp(&b.at));
                let there: Vec<&Proc> = procs.iter().filter(|p| !p.cwd.is_empty() && inside(&p.cwd, path)).collect();
                let tool = tool_of(&real(path), &home);
                if tool == "other" && here.is_empty() && there.is_empty() {
                    continue; // the person's own worktree: no agent worked there
                }
                let last = here.last();
                let mut f = Found::new(tool, &real(&trees[0].0), &py_basename(path), Some(real(path)), branch);
                f.ahead = ahead;
                f.dirty = dirty;
                f.kind = last.map(|l| l.kind.clone()).or_else(|| there.first().map(|p| p.kind.clone()));
                f.model = last.and_then(|l| l.model.as_ref().and_then(|m| m.as_str().map(String::from)));
                f.session = last.map(|l| l.session.clone());
                f.running_elsewhere = !there.is_empty();
                mine.push(f);
            }
            Ok(mine)
        };
        match attempt() {
            Ok(mine) => found.extend(mine),
            Err(_) => unreadable.push(repo.clone()),
        }
    }
    (found, unreadable)
}

// --- what the sheet shows, and the New team draft --------------------------------------------------------------------

fn tool_label(tool: &str) -> &'static str {
    match tool {
        "conductor" => "Conductor",
        "vibe-kanban" => "Vibe Kanban",
        "claude-squad" => "Claude Squad",
        "herdr" => "herdr",
        _ => "another tool",
    }
}

const LOOKED_FOR: [&str; 4] = ["Conductor", "Vibe Kanban", "Claude Squad", "herdr"];
fn script_words(which: &str) -> Option<&'static str> {
    Some(match which {
        "setup" => "runs in each new agent’s copy",
        "run" => "becomes the live preview",
        "archive" => "runs before a copy is archived",
        _ => return None,
    })
}
/// The order a project's scripts are listed in.
const SCRIPT_ORDER: [&str; 3] = ["setup", "run", "archive"];
/// The kinds whose conversation Assieme picks up again (`launch::argv`).
const RESUMES: [&str; 2] = ["claude", "codex"];
const ORDER: [&str; 4] = ["conductor", "vibe-kanban", "claude-squad", "other"];

struct Group {
    tool: String,
    project: String,
    items: Vec<Found>,
    scripts: BTreeMap<String, String>,
    tasks: Vec<VkTask>,
}

/// id → group, from the latest find: the sheet's next steps use what it showed.
static LAST: Mutex<BTreeMap<String, Arc<Group>>> = Mutex::new(BTreeMap::new());

/// Folders saved teams already work in: brought over before, so not listed again.
fn adopted(teams: &Path) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    for path in files(&format!("{}/*/team.toml", escaped(teams))) {
        let Some(doc) = std::fs::read_to_string(path).ok().and_then(|t| t.parse::<toml::Table>().ok()) else {
            continue;
        };
        for r in doc.get("role").and_then(|r| r.as_array()).into_iter().flatten() {
            if let Some(w) = r.get("worktree").and_then(|w| w.as_str()).filter(|w| !w.is_empty()) {
                out.insert(real(w));
            }
        }
    }
    out
}

/// Work counts for a reader's workspace, from git; a folder now on another branch uses that branch.
fn fill(f: &mut Found, base_of: &mut dyn FnMut(&str) -> Option<String>) {
    let attempt = |f: &mut Found, base_of: &mut dyn FnMut(&str) -> Option<String>| -> std::io::Result<()> {
        let deadline = || Instant::now() + LIMIT;
        if let Some(folder) = f.folder.clone() {
            let now = branch_of(&folder);
            if !now.is_empty() && now != f.branch {
                f.notes.push(format!(
                    "{} was on {}; its folder is on {now} now, so that branch is used.",
                    f.name, f.branch
                ));
                f.branch = now;
            }
            f.dirty = !out(&git(&folder, &["status", "--porcelain"], deadline())?).is_empty();
        }
        if let (Some(base), false) = (base_of(&f.project), f.branch.is_empty()) {
            let r = git(&f.project, &["rev-list", "--count", &format!("{base}..{}", f.branch)], deadline())?;
            f.ahead = if r.status.success() { out(&r).parse().unwrap_or(0) } else { 0 };
        }
        Ok(())
    };
    let _ = attempt(f, base_of);
}

fn plural(n: usize, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

fn summary(items: &[Found]) -> String {
    let mut kinds: Vec<&str> = vec![];
    for f in items {
        let name = f.kind.as_deref().and_then(|k| crate::launch::NAMES.iter().find(|(i, _)| *i == k)).map(|(_, n)| *n);
        if let Some(n) = name.filter(|n| !kinds.contains(n)) {
            kinds.push(n);
        }
    }
    let unmerged = items.iter().filter(|f| f.ahead > 0).count();
    let noun = if items[0].tool == "claude-squad" { ("session", "sessions") } else { ("workspace", "workspaces") };
    let mut parts = vec![plural(items.len(), noun.0, noun.1)];
    if !kinds.is_empty() {
        parts.push(kinds.join(", "));
    }
    if unmerged > 0 {
        parts.push(format!("{unmerged} with unmerged work"));
    }
    parts.join(" · ")
}

/// What the sheet lists: one row per tool and project. `folder`: a project the person chose, looked in even when no
/// agent history points at it.
pub fn find(folder: Option<&str>) -> Value {
    let home = std::env::var("HOME").unwrap_or_default();
    let teams = crate::newteam::teams_dir();
    find_with(&Inputs {
        home: &home,
        folder,
        teams: &teams,
        now: Timestamp::now(),
        sessions: None,
        procs: None,
        herdr_teams: None,
    })
}

/// What `find` reads from this Mac, so a test can give its own.
pub(crate) struct Inputs<'a> {
    pub home: &'a str,
    pub folder: Option<&'a str>,
    pub teams: &'a Path,
    pub now: Timestamp,
    pub sessions: Option<Vec<Sess>>,
    pub procs: Option<Vec<Proc>>,
    pub herdr_teams: Option<Vec<Value>>,
}

pub(crate) fn find_with(i: &Inputs) -> Value {
    let home = real(i.home);
    let home_path = Path::new(&home);
    let sessions = i.sessions.clone().unwrap_or_else(|| {
        history(&home_path.join(".claude/projects"), &home_path.join(".codex/sessions"), i.now, DAYS)
    });
    let procs = i.procs.clone().unwrap_or_else(running_agents);
    let (squad, squad_problem) = read_claude_squad(home_path);
    let (vibe, tasks, vibe_scripts, vibe_problem) = read_vibe_kanban(home_path);
    let (cond, cond_scripts, cond_problem) = read_conductor(home_path);
    let mut readers: Vec<Found> = [squad, vibe, cond].concat();
    let mut repos: Vec<String> = repos_from_history(&sessions);
    repos.extend(readers.iter().map(|f| f.project.clone()));
    if let Some(folder) = i.folder {
        repos.push(project_of(folder).unwrap_or_else(|| folder.to_string()));
    }
    let mut unique: Vec<String> = vec![];
    for r in repos.iter().map(|r| real(r)) {
        if !unique.contains(&r) {
            unique.push(r);
        }
    }
    let (scanned, unreadable) = scan(&unique, &sessions, &procs, &home, LIMIT);
    // by folder, in the order first found (a later one of the same folder replaces the earlier)
    let mut by_folder: Vec<(String, Found)> = vec![];
    for f in scanned.into_iter().filter(|f| f.folder.is_some()) {
        let folder = f.folder.clone().unwrap_or_default();
        match by_folder.iter_mut().find(|(k, _)| *k == folder) {
            Some(slot) => slot.1 = f,
            None => by_folder.push((folder, f)),
        }
    }
    let mut bases: BTreeMap<String, Option<String>> = BTreeMap::new();
    let mut base_of = |p: &str| -> Option<String> {
        bases.entry(p.to_string()).or_insert_with(|| base_branch(p, Instant::now() + LIMIT).ok().flatten()).clone()
    };
    for f in &mut readers {
        let s = f
            .folder
            .as_ref()
            .and_then(|folder| by_folder.iter().position(|(k, _)| k == folder))
            .map(|at| by_folder.remove(at).1);
        if !unreadable.contains(&f.project) {
            // a repository that hung once isn't asked again
            fill(f, &mut base_of);
        }
        if let Some(s) = s {
            f.kind = f.kind.take().or(s.kind);
            f.model = f.model.take().or(s.model);
            f.session = f.session.take().or(s.session);
            f.running_elsewhere = f.running_elsewhere || s.running_elsewhere;
        }
    }
    let skip = adopted(i.teams);
    let mut groups: Vec<((String, String), Vec<Found>)> = vec![];
    for f in readers.into_iter().chain(by_folder.into_iter().map(|(_, f)| f)) {
        if f.folder.as_ref().is_some_and(|folder| skip.contains(folder)) {
            continue;
        }
        let k = (f.tool.clone(), f.project.clone());
        match groups.iter_mut().find(|(g, _)| *g == k) {
            Some((_, its)) => its.push(f),
            None => groups.push((k, vec![f])),
        }
    }
    let order = |tool: &str| ORDER.iter().position(|t| *t == tool).unwrap_or(ORDER.len());
    groups.sort_by(|a, b| (order(&a.0.0), &a.0.1).cmp(&(order(&b.0.0), &b.0.1)));
    let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
    last.clear();
    let mut projects = vec![];
    for ((tool, project), mut its) in groups {
        its.sort_by(|a, b| (-a.ahead, !a.dirty, &a.name).cmp(&(-b.ahead, !b.dirty, &b.name)));
        let names: Vec<String> = its.iter().map(|f| f.name.clone()).collect();
        for f in &mut its {
            // several attempts at one task share its title: each keeps its own row
            if names.iter().filter(|n| **n == f.name).count() > 1 {
                f.name = format!("{} ({})", f.name, f.branch);
            }
        }
        let id = format!("{tool}:{project}");
        let given = match tool.as_str() {
            "conductor" => cond_scripts.get(&project),
            "vibe-kanban" => vibe_scripts.get(&project),
            _ => None,
        };
        let group = Group {
            tool: tool.clone(),
            project: project.clone(),
            scripts: given.cloned().unwrap_or_default(),
            tasks: if tool == "vibe-kanban" { tasks.get(&project).cloned().unwrap_or_default() } else { vec![] },
            items: its,
        };
        let where_ = tool_label(&tool);
        projects.push(json!({
            "id": id, "tool": tool, "label": py_basename(&project), "summary": summary(&group.items), "count": group.items.len(),
            "running": if group.items.iter().any(|f| f.running_elsewhere) { json!(format!("Still running in {where_}: stop it there first.")) } else { Value::Null },
        }));
        last.insert(id, Arc::new(group));
    }
    drop(last);
    let herdr_teams =
        i.herdr_teams.clone().unwrap_or_else(|| crate::herdr::find()["teams"].as_array().cloned().unwrap_or_default());
    for t in herdr_teams {
        let n = t["agents"].as_u64().unwrap_or(0) as usize;
        projects.push(json!({
            "id": format!("herdr:{}", t["id"].as_str().unwrap_or("")), "tool": "herdr", "label": t["label"], "count": n,
            "summary": format!("{} · {}", t["where"].as_str().unwrap_or(""), plural(n, "agent", "agents")),
            "running": if crate::pyrules::truthy(&t["running"]) { json!("Still running in herdr: stop it there first.") } else { Value::Null },
        }));
    }
    let problems: Vec<String> = [squad_problem, vibe_problem, cond_problem].into_iter().flatten().collect();
    json!({"projects": projects, "unreadable": unreadable, "problems": problems, "looked_for": LOOKED_FOR})
}

fn group(id: &str) -> Result<Arc<Group>, BrainError> {
    LAST.lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(id)
        .cloned()
        .ok_or_else(|| BrainError::new("That project isn't in the list any more. Look again."))
}

fn becomes(f: &Found, n: usize) -> String {
    let name = |k: &str| crate::launch::NAMES.iter().find(|(i, _)| *i == k).map(|(_, n)| *n);
    let who = [name(f.kind.as_deref().unwrap_or("claude")).map(String::from), f.model.clone()]
        .into_iter()
        .flatten()
        .filter(|x| !x.is_empty())
        .collect::<Vec<_>>()
        .join(", ");
    let work = if f.ahead > 0 {
        format!("{} not merged", plural(f.ahead as usize, "commit", "commits"))
    } else if f.dirty {
        "uncommitted changes".into()
    } else {
        "nothing new yet".into()
    };
    format!("Becomes builder-{n} · {who} · {work}")
}

/// `{label, tool, rows, scripts}`: the "What it becomes" step for one project.
pub fn workspaces(id: &str) -> Result<Value, BrainError> {
    workspaces_with(id, &crate::scripts::rewrite)
}

/// `rewrite`: the script rewriter (`scripts::rewrite`), which gives the line and the variables it doesn't set.
pub(crate) fn workspaces_with(id: &str, rewrite: &dyn Fn(&str) -> (String, Vec<String>)) -> Result<Value, BrainError> {
    let g = group(id)?;
    let (mut rows, mut n) = (vec![], 0usize);
    for f in &g.items {
        let ticked = f.ahead > 0 || f.dirty;
        n += usize::from(ticked);
        rows.push(json!({"name": f.name, "branch": f.branch, "ticked": ticked, "becomes": becomes(f, if ticked { n } else { n + 1 }),
                         "note": if ticked { "" } else { "Left out: nothing new on its branch. Tick it to bring it anyway." }}));
    }
    let lines: Vec<Value> = SCRIPT_ORDER
        .iter()
        .filter_map(|k| Some(json!({"which": k, "line": rewrite(g.scripts.get(*k)?).0, "becomes": script_words(k)})))
        .collect();
    Ok(json!({"label": py_basename(&g.project), "tool": tool_label(&g.tool), "rows": rows, "scripts": lines}))
}

/// What the draft asks of New team.
pub(crate) struct Deps<'a> {
    pub kind_brief: &'a dyn Fn(&str) -> String,
    /// Whether a model can't be used by this agent kind.
    pub model_problem: &'a dyn Fn(&str, &str) -> bool,
    pub rewrite: &'a dyn Fn(&str) -> (String, Vec<String>),
}

/// The New team draft: one agent per ticked workspace, working in that folder on that branch.
pub fn draft(id: &str, ticked: &[String]) -> Result<Value, BrainError> {
    if let Some(source) = id.strip_prefix("herdr:") {
        let mut d = crate::herdr::draft(source)?;
        d["from"] = json!("herdr");
        return Ok(d);
    }
    let deps = Deps {
        kind_brief: &crate::newteam::kind_brief,
        model_problem: &|agent, model| crate::launch::check_model_effort(agent, Some(model), None).is_some(),
        rewrite: &crate::scripts::rewrite,
    };
    draft_with(id, ticked, &deps)
}

pub(crate) fn draft_with(id: &str, ticked: &[String], deps: &Deps) -> Result<Value, BrainError> {
    let g = group(id)?;
    let mut wanted: Vec<&String> = vec![];
    for t in ticked {
        if !wanted.contains(&t) {
            wanted.push(t);
        }
    }
    // in the order the sheet lists them
    let chosen: Vec<&Found> = wanted.iter().filter_map(|t| g.items.iter().find(|f| f.name == **t)).collect();
    if chosen.is_empty() {
        return Err(BrainError::new("Tick at least one workspace to bring over."));
    }
    let tool = tool_label(&g.tool);
    let mut notes: Vec<(String, Vec<String>)> = vec![];
    let mut note = |text: &str, agent: Option<&str>| {
        let at = notes.iter().position(|(t, _)| t == text).unwrap_or_else(|| {
            notes.push((text.to_string(), vec![]));
            notes.len() - 1
        });
        notes[at].1.extend(agent.map(String::from));
    };
    let by_branch_task: BTreeMap<&str, &VkTask> =
        g.tasks.iter().filter_map(|t| Some((t.branch.as_deref().filter(|b| !b.is_empty())?, t))).collect();
    let (mut roles, mut by_branch) = (vec![], BTreeMap::<String, String>::new());
    let clis: Vec<&str> = crate::launch::CLIS.iter().map(|(k, _)| *k).collect();
    for (n, f) in chosen.iter().enumerate() {
        let name = format!("builder-{}", n + 1);
        let agent = f.kind.as_deref().filter(|k| clis.contains(k)).unwrap_or("claude");
        match f.kind.as_deref() {
            None => note(
                "Which agent worked here isn't recorded, so they start with Claude Code. Change it if needed.",
                Some(&name),
            ),
            Some(k) if !clis.contains(&k) => {
                note("They ran an agent Assieme doesn't start yet, so they start with Claude Code.", Some(&name))
            }
            _ => {}
        }
        let task = by_branch_task.get(f.branch.as_str());
        let mut brief = task.map_or(String::new(), |t| {
            format!("{}\n\n{}", t.title.clone().unwrap_or_default(), t.description).trim().to_string()
        });
        if brief.is_empty() {
            brief = (deps.kind_brief)("builder");
            note("No brief was found, so they get the standard builder brief. Check it before you start.", Some(&name));
        }
        let mut role = json!({"name": name, "agent": agent, "job": f.name, "checkout": "branch", "parent": null, "brief": brief,
                              "kind": "builder", "branch": f.branch, "level": "safe"});
        if let Some(m) = f.model.as_deref().filter(|m| !(deps.model_problem)(agent, m)) {
            role["model"] = json!(m);
        }
        match &f.folder {
            Some(folder) => role["worktree"] = json!(folder),
            None => note("Their folder is gone, so each gets a fresh copy of its branch.", Some(&name)),
        }
        match &f.session {
            Some(s) if RESUMES.contains(&agent) && f.kind.as_deref() == Some(agent) => role["resume"] = json!(s),
            _ if f.folder.is_some() => {
                note("No conversation was found to pick up, so they start a new one with their brief.", Some(&name))
            }
            _ => {}
        }
        if f.running_elsewhere {
            note(
                &format!("Still running in {tool}: stop them there first, or they are skipped at Start."),
                Some(&name),
            );
        }
        for t in &f.notes {
            note(t, Some(&name));
        }
        roles.push(role);
        by_branch.insert(f.branch.clone(), name);
    }
    note(
        &format!(
            "They start with Assieme's safe permissions: each works in its own folder and can't push. Wider permissions set in {tool} are not carried over."
        ),
        None,
    );
    let cards: Vec<Value> = g
        .tasks
        .iter()
        .filter(|t| t.status != "cancelled")
        .map(
            |t| json!({"title": t.title, "status": t.status, "role": t.branch.as_ref().and_then(|b| by_branch.get(b))}),
        )
        .collect();
    let cancelled = g.tasks.len() - cards.len();
    if cancelled > 0 {
        note(
            &format!(
                "{cancelled} cancelled task{} left off the board.",
                if cancelled != 1 { "s were" } else { " was" }
            ),
            None,
        );
    }
    let unknown: BTreeSet<String> =
        g.scripts.iter().filter(|(k, _)| script_words(k).is_some()).flat_map(|(_, v)| (deps.rewrite)(v).1).collect();
    if !unknown.is_empty() {
        note(
            &format!(
                "Its scripts use {}, which Assieme doesn't set.",
                unknown.into_iter().collect::<Vec<_>>().join(", ")
            ),
            None,
        );
    }
    let scripts: Map<String, Value> = g
        .scripts
        .iter()
        .filter(|(k, _)| script_words(k).is_some())
        .map(|(k, v)| (k.clone(), json!((deps.rewrite)(v).0)))
        .collect();
    let running =
        chosen.iter().any(|f| f.running_elsewhere).then(|| format!("Still running in {tool}: stop it there first."));
    Ok(json!({"project": g.project, "task": "", "roles": roles, "cards": cards, "scripts": scripts,
              "notes": notes.iter().map(|(t, a)| json!({"text": t, "agents": a})).collect::<Vec<_>>(), "running": running,
              "from": "bringover", "tool": tool, "label": py_basename(&g.project)}))
}

// --- at Start --------------------------------------------------------------------------------------------------------

/// (roles to start, skipped): an agent still running in the other tool's folder isn't started beside it.
pub fn not_running(roles: &[Value], tool: &str) -> (Vec<Value>, Vec<Value>) {
    not_running_with(roles, tool, &running_agents())
}

pub(crate) fn not_running_with(roles: &[Value], tool: &str, procs: &[Proc]) -> (Vec<Value>, Vec<Value>) {
    let (mut kept, mut skipped) = (vec![], vec![]);
    for r in roles {
        let busy = r["worktree"]
            .as_str()
            .filter(|w| !w.is_empty())
            .is_some_and(|w| procs.iter().any(|p| !p.cwd.is_empty() && inside(&p.cwd, w)));
        if busy {
            skipped
                .push(json!({"role": r["name"], "reason": format!("Still running in {tool}: stop it there first.")}));
        } else {
            kept.push(r.clone());
        }
    }
    (kept, skipped)
}

/// After a brought-over team is saved and starting: its tasks on the board, its run script as the preview command, and
/// one Activity row that Undo turns back.
pub fn started(
    team: &str,
    project: &str,
    roles: &[Value],
    cards: &[Value],
    scripts: &BTreeMap<String, String>,
    origin: &Value,
    remember: &dyn Fn(&str, &str),
) {
    let add = |title: &str, role: Option<&str>| crate::tasks::add(team, title, role, "assieme", None);
    let set = |id: &str, status: &str| crate::tasks::r#move(team, id, status, None, false, None).map(|_| ());
    let record = |row: crate::live::Record| {
        crate::live::record(row);
    };
    started_with(team, project, roles, cards, scripts, origin, remember, &add, &set, &record);
}

/// A new card: its title and role.
type AddCard<'a> = dyn Fn(&str, Option<&str>) -> Result<Value, BrainError> + 'a;

#[allow(clippy::too_many_arguments)]
pub(crate) fn started_with(
    team: &str,
    project: &str,
    roles: &[Value],
    cards: &[Value],
    scripts: &BTreeMap<String, String>,
    origin: &Value,
    remember: &dyn Fn(&str, &str),
    add: &AddCard<'_>,
    set: &dyn Fn(&str, &str) -> Result<(), BrainError>,
    record: &dyn Fn(crate::live::Record),
) {
    for c in cards {
        let Ok(card) = add(c["title"].as_str().unwrap_or(""), c["role"].as_str()) else { continue };
        if let (Some(status), Some(id)) = (c["status"].as_str().filter(|s| *s != "todo"), card["id"].as_str()) {
            let _ = set(id, status);
        }
    }
    if let Some(run) = scripts.get("run").filter(|r| !r.is_empty()) {
        remember(project, run);
    }
    let adopted: Vec<Value> =
        roles.iter().filter_map(|r| r["worktree"].as_str().filter(|w| !w.is_empty()).map(|w| json!(w))).collect();
    let (tool, label) = (origin["tool"].as_str().unwrap_or(""), origin["label"].as_str().unwrap_or(""));
    let mut extra = Map::new();
    extra.insert("title".into(), json!(format!("Brought over {label} from {tool}")));
    extra.insert("adopted".into(), Value::Array(adopted));
    record(crate::live::Record {
        action: "bringover",
        reason: &format!("Its agents work in the folders they had in {tool}."),
        team: Some(team),
        extra,
        ..crate::live::Record::default()
    });
}

#[cfg(test)]
mod tests;
