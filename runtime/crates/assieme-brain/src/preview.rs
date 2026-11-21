//! Port of `assieme/preview.py`, owned by session E (see OWNERS.md).
//!
//! Live preview: the project's dev server, run in one agent's worktree on a port of its own, so the person can look at
//! the branch before merging it.
//!
//! Each preview runs in its own process group, and only that group is ever stopped. The groups are saved, so a brain
//! that restarts stops what it left running. Refusals are a `PreviewError` with a sentence for the screen.
use crate::prelude::*;
use regex::Regex;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

const START_TIMEOUT: Duration = Duration::from_secs(90);
const INSTALL_TIMEOUT: Duration = Duration::from_secs(300);
const LOST_TIMEOUT: Duration = Duration::from_secs(10); // a running server with no port this long has stopped serving
const TAIL: usize = 12;
const SCRIPTS: [&str; 3] = ["dev", "start", "serve"];
const INSTALL_SCRIPTS: [&str; 4] = ["preinstall", "install", "postinstall", "prepare"]; // what a package install runs

static ANSI: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;?]*[A-Za-z]").expect("a fixed pattern"));
static MISSING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)(?:^|\s)([\w@./-]+): command not found|Cannot find (?:module|package) '([^']+)'|No module named '?([\w.]+)")
        .expect("a fixed pattern")
});
static IN_USE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)EADDRINUSE|address already in use").expect("a fixed pattern"));
static SCRIPT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(?:npm|pnpm|yarn|bun)(?: run)? ([\w:.-]+)\s*$").expect("a fixed pattern"));

#[derive(Debug, Clone, PartialEq)]
pub struct PreviewError(pub String);

impl From<PreviewError> for BrainError {
    fn from(e: PreviewError) -> BrainError {
        BrainError::new(e.0)
    }
}

fn has(path: &str, name: &str) -> bool {
    Path::new(path).join(name).is_file()
}

fn package_manager(path: &str) -> &'static str {
    let locks = [("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lock", "bun"), ("bun.lockb", "bun")];
    locks.iter().find(|(lock, _)| has(path, lock)).map_or("npm", |(_, pm)| pm)
}

fn read(path: &str, name: &str) -> Option<String> {
    std::fs::read(Path::new(path).join(name)).ok().map(|b| String::from_utf8_lossy(&b).into_owned())
}

/// The `scripts` of a package.json text: an object, or nothing.
fn scripts_of(text: Option<String>) -> Map<String, Value> {
    let package = text.and_then(|t| crate::pyrules::json_loads(&t).ok());
    package.as_ref().and_then(|p| p["scripts"].as_object()).cloned().unwrap_or_default()
}

/// The command that starts the project's dev server, read from what is in its folder; None when we can't tell.
fn start_command(path: &str) -> Option<String> {
    if has(path, "package.json") {
        let scripts = scripts_of(read(path, "package.json"));
        for name in SCRIPTS {
            if scripts.get(name).and_then(Value::as_str).is_some_and(|s| !s.trim().is_empty()) {
                return Some(format!("{} run {name}", package_manager(path)));
            }
        }
    }
    if has(path, "manage.py") {
        return Some("python3 manage.py runserver $PORT".into());
    }
    has(path, "index.html").then(|| "python3 -m http.server $PORT --bind 127.0.0.1".into())
}

/// The install that leaves the agent's lockfile as it is: a changed or new one would read as uncommitted work.
fn install_command(path: &str) -> Option<String> {
    if !has(path, "package.json") {
        return None;
    }
    Some(match package_manager(path) {
        "npm" if has(path, "package-lock.json") || has(path, "npm-shrinkwrap.json") => "npm ci".into(),
        "npm" => "npm install --no-package-lock".into(),
        pm => format!("{pm} install --frozen-lockfile"),
    })
}

/// A Node project whose packages aren't in this worktree yet. Yarn Plug'n'Play keeps them without node_modules.
fn needs_install(path: &str) -> bool {
    let package = read(path, "package.json").and_then(|t| crate::pyrules::json_loads(&t).ok()).unwrap_or(Value::Null);
    let wants = ["dependencies", "devDependencies"].iter().any(|k| crate::pyrules::truthy(&package[k]));
    wants && !Path::new(path).join("node_modules").is_dir() && !(has(path, ".pnp.cjs") || has(path, ".pnp.js"))
}

fn tail(log: &Path) -> String {
    let text = std::fs::read(log).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
    let text = ANSI.replace_all(&text, "").replace('\r', "\n");
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    lines[lines.len().saturating_sub(TAIL)..].join("\n")
}

/// What went wrong, in words, and the fix the screen offers: install, retry or command.
fn why(
    command: &str,
    role: &str,
    rc: Option<i32>,
    output: &str,
    path: &str,
    installed: bool,
) -> (String, &'static str) {
    if rc.is_none() {
        return (format!("{command} is running but didn't open a port, so it was stopped."), "command");
    }
    if IN_USE.is_match(output) {
        return (format!("{command} stopped: its port was already taken. Try again to get another one."), "retry");
    }
    let found = MISSING.captures(output);
    if found.is_some() || rc == Some(127) || needs_install(path) {
        let what = found.and_then(|c| c.iter().skip(1).flatten().next().map(|m| m.as_str().to_string()));
        let thing = what.map_or("something it needs isn't installed".into(), |w| format!("{w} isn't installed"));
        if installed || install_command(path).is_none() {
            return (format!("{command} stopped: {thing} in {role}'s copy. Check the start command."), "command");
        }
        let fix = "Install the project's packages there, then try again.";
        return (format!("{command} stopped: {thing} in {role}'s copy. {fix}"), "install");
    }
    (format!("{command} stopped before it opened a port."), "command")
}

fn run(program: &str, args: &[&str]) -> String {
    let out = Command::new(program).args(args).stdin(Stdio::null()).output();
    out.map(|o| String::from_utf8_lossy(&o.stdout).into_owned()).unwrap_or_default()
}

fn ports_of(pgid: i32) -> Vec<u16> {
    let out = run("lsof", &["-a", "-nP", "-iTCP", "-sTCP:LISTEN", "-g", &pgid.to_string(), "-Fn"]);
    let ports: BTreeSet<u16> =
        out.lines().filter(|l| l.starts_with('n')).filter_map(|l| l.rsplit(':').next()?.parse().ok()).collect();
    ports.into_iter().collect()
}

/// TERM to the preview's own process group, KILL to whatever is left after 3 seconds.
fn kill(pgid: i32, mut child: Option<&mut Child>) {
    if pgid <= 0 {
        return; // already stopped: the id may belong to someone else by now
    }
    // SAFETY: signals only the process group id this preview recorded.
    let gone =
        || unsafe { libc::killpg(pgid, 0) } != 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH);
    if unsafe { libc::killpg(pgid, libc::SIGTERM) } != 0 {
        return;
    }
    for _ in 0..30 {
        if let Some(c) = child.as_deref_mut() {
            let _ = c.try_wait(); // reap the shell, or it counts as alive
        }
        if gone() {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    unsafe { libc::killpg(pgid, libc::SIGKILL) };
    if let Some(c) = child {
        let _ = c.wait();
    }
}

/// Stops the saved preview groups whose processes still run in the same worktree (a reused process group id is
/// someone else's).
fn stop_saved(running: &Map<String, Value>) {
    let real = |p: &str| std::fs::canonicalize(p).unwrap_or_else(|_| p.into());
    for entry in running.values() {
        let (Some(pgid), Some(path)) = (entry["pgid"].as_i64().filter(|p| *p > 1), entry["path"].as_str()) else {
            continue;
        };
        let cwds = run("lsof", &["-a", "-g", &pgid.to_string(), "-d", "cwd", "-Fn"]);
        if cwds.lines().filter_map(|l| l.strip_prefix('n')).any(|c| real(c) == real(path)) {
            kill(pgid as i32, None);
        }
    }
}

struct Item {
    team: String,
    role: String,
    project: String,
    path: String,
    command: String,
    port: u16,
    child: Child,
    pgid: Option<i32>,
    log: PathBuf,
    started: Instant,
    installed: bool,
    timeout: Duration,
    state: &'static str,
    url: Option<String>,
    shown_port: Option<u16>,
    reason: String,
    output: String,
    fix: Option<&'static str>,
    lost: Option<Instant>,
    team_live: bool,
}

impl Item {
    fn view(&self) -> Value {
        let project = Path::new(&self.project).file_name().map(|n| n.to_string_lossy().into_owned());
        json!({"team": self.team, "role": self.role, "project": project, "state": self.state,
               "command": self.command, "url": self.url, "port": self.shown_port, "reason": self.reason,
               "output": self.output, "fix": self.fix,
               "install": if self.fix == Some("install") { install_command(&self.path) } else { None }})
    }
}

type Key = (String, String);
type Shared = Arc<Mutex<Item>>;

#[derive(Default)]
struct State {
    items: BTreeMap<Key, Shared>,
    /// The fingerprint of what the person saw run, per agent.
    confirmed: BTreeMap<Key, String>,
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// The run script previews, one per agent.
pub struct Previews {
    reviews: Arc<Review>,
    file: PathBuf,
    logs: PathBuf,
    state: Mutex<State>,
}

impl Previews {
    pub fn new(reviews: Arc<Review>) -> Previews {
        let dir = crate::quiet::state_dir();
        Previews { reviews, file: dir.join("previews.json"), logs: dir.join("previews"), state: Mutex::default() }
    }

    // --- saved state: the start command per project, and the process groups running now

    fn saved(&self, key: &str) -> Map<String, Value> {
        match crate::quiet::read_json(&self.file, Value::Null).get(key) {
            Some(Value::Object(o)) => o.clone(),
            _ => Map::new(),
        }
    }

    /// Writes the start commands (as given, else as saved) and the groups running now; `st` is the held state.
    fn save(&self, st: &State, commands: Option<Map<String, Value>>) {
        let running: Map<String, Value> = st
            .items
            .iter()
            .filter_map(|((t, r), item)| {
                let i = lock(item);
                Some((format!("{t}/{r}"), json!({"pgid": i.pgid?, "path": i.path})))
            })
            .collect();
        let commands = commands.unwrap_or_else(|| self.saved("commands"));
        let _ = crate::quiet::write_json(&self.file, &json!({"commands": commands, "running": running}));
    }

    pub fn commands(&self) -> BTreeMap<String, String> {
        self.saved("commands").into_iter().filter_map(|(k, v)| Some((k, v.as_str()?.to_string()))).collect()
    }

    /// A run script brought over from another tool (step 30) as the project's start command, unless the person has
    /// already given one. It is still shown before its first run, like any command they didn't type.
    pub fn remember(&self, project: &str, command: &str) {
        let st = lock(&self.state);
        let mut saved = self.saved("commands");
        if !saved.get(project).is_some_and(crate::pyrules::truthy) {
            saved.insert(project.into(), json!(crate::scripts::rewrite(command).0));
            self.save(&st, Some(saved));
        }
    }

    /// At brain start: stop the previews a previous brain left running, if their processes still run in the same
    /// worktree (a reused process group id is someone else's).
    pub fn stop_left_behind(&self) {
        let st = lock(&self.state);
        stop_saved(&self.saved("running"));
        self.save(&st, None);
    }

    // --- what the app calls

    /// What the person is shown before a first run, step by step: each command, with the content of the package
    /// scripts it runs that the branch changed against the base branch (the agent may have written them), and of
    /// the main script it runs. manage.py counts as a script when the branch changed it.
    fn what_runs(project: &str, base: &str, path: &str, cmd: &str, first: Option<&str>) -> Value {
        let show = |name: &str| {
            let out = crate::worktree::git(project, &["show", &format!("{base}:{name}")]).ok()?;
            out.status.success().then(|| String::from_utf8_lossy(&out.stdout).into_owned())
        };
        let (now, before) = (scripts_of(read(path, "package.json")), scripts_of(show("package.json")));
        let script = |n: &str, always: bool| -> Vec<Value> {
            let changed = now.get(n) != before.get(n);
            match now.get(n) {
                Some(v) if always || changed => {
                    vec![json!({"name": n, "runs": v.as_str().map_or(v.to_string(), String::from), "changed": changed})]
                }
                _ => vec![],
            }
        };
        let name = SCRIPT.captures(cmd).map(|c| c[1].to_string());
        let mut mine = name.as_ref().map_or(vec![], |n| {
            [script(&format!("pre{n}"), false), script(n, true), script(&format!("post{n}"), false)].concat()
        });
        let managed = read(path, "manage.py").filter(|_| cmd.contains("manage.py"));
        if let Some(m) = managed.filter(|m| Some(m) != show("manage.py").as_ref()) {
            mine.push(json!({"name": "manage.py", "runs": m.trim(), "changed": true}));
        }
        let mut steps = vec![];
        if let Some(line) = first {
            steps.push(json!({"line": line, "scripts": INSTALL_SCRIPTS.iter().flat_map(|n| script(n, false)).collect::<Vec<_>>()}));
        }
        steps.push(json!({"line": cmd, "scripts": mine}));
        let changed: Vec<&Value> = steps
            .iter()
            .flat_map(|s| s["scripts"].as_array().into_iter().flatten())
            .filter(|x| x["changed"] == true)
            .map(|x| &x["name"])
            .collect();
        let info = json!({"command": cmd, "install": first, "steps": steps, "changed": changed});
        let sum = Sha256::digest(json!([path, info]).to_string());
        let fingerprint: String = sum.iter().take(8).map(|b| format!("{b:02x}")).collect();
        let mut info = info;
        info["fingerprint"] = json!(fingerprint);
        info
    }

    /// Out of the list under the lock; the caller stops it outside, so a slow stop holds nobody up.
    fn take(&self, key: &Key) -> Option<Shared> {
        let mut st = lock(&self.state);
        let item = st.items.remove(key);
        if item.is_some() {
            self.save(&st, None);
        }
        item
    }

    fn stop_item(item: &Shared) {
        let mut i = lock(item);
        let pgid = i.pgid.unwrap_or(0);
        kill(pgid, Some(&mut i.child));
    }

    pub fn start(
        &self,
        team: &str,
        role: &str,
        command: Option<&str>,
        install: bool,
        confirm: Option<&str>,
    ) -> Result<Value, PreviewError> {
        let (project, base, _, path) = self.reviews.worktree(team, role).map_err(|e| PreviewError(e.message))?;
        if !Path::new(&path).is_dir() {
            return Err(PreviewError(format!("{role}'s copy isn't there any more, so it can't be previewed.")));
        }
        let typed = command.unwrap_or_default().trim();
        let key = (team.to_string(), role.to_string());
        let name = Path::new(&project).file_name().map(|n| n.to_string_lossy().into_owned());
        let cmd = {
            let st = lock(&self.state);
            if !typed.is_empty() {
                let mut saved = self.saved("commands");
                saved.insert(project.clone(), json!(typed));
                self.save(&st, Some(saved));
            }
            let kept = self.commands().get(&project).cloned().filter(|c| !c.is_empty());
            Some(typed.to_string()).filter(|t| !t.is_empty()).or(kept).or_else(|| start_command(&path))
        };
        let Some(cmd) = cmd else {
            return Ok(json!({"team": team, "role": role, "project": name, "state": "needs_command"}));
        };
        let first = (install || needs_install(&path)).then(|| install_command(&path)).flatten();
        let mut shown = Self::what_runs(&project, &base, &path, &cmd, first.as_deref());
        let fingerprint = shown["fingerprint"].as_str().unwrap_or_default().to_string();
        let runs_nothing = shown["steps"].as_array().is_some_and(|s| s.iter().all(|st| st["scripts"] == json!([])));
        // The person just typed it, and it runs nothing an agent wrote.
        let confirm =
            if !typed.is_empty() && first.is_none() && runs_nothing { Some(fingerprint.as_str()) } else { confirm };
        if lock(&self.state).confirmed.get(&key) != Some(&fingerprint) {
            if confirm != Some(fingerprint.as_str()) {
                // Nothing the agent wrote runs before the person has seen it.
                for (k, v) in [
                    ("team", json!(team)),
                    ("role", json!(role)),
                    ("project", json!(name)),
                    ("state", json!("confirm")),
                ] {
                    shown[k] = v;
                }
                return Ok(shown);
            }
            lock(&self.state).confirmed.insert(key.clone(), fingerprint);
        }
        if let Some(old) = self.take(&key) {
            Self::stop_item(&old); // one preview per agent: starting again replaces it
        }
        let port = std::net::TcpListener::bind("127.0.0.1:0").and_then(|l| l.local_addr()).map_or(0, |a| a.port());
        let fail = |e: std::io::Error| PreviewError(format!("Couldn't start {cmd}: {e}"));
        std::fs::create_dir_all(&self.logs).map_err(fail)?;
        let log = self.logs.join(format!("{team}-{role}.log"));
        let out = std::fs::File::create(&log).map_err(fail)?;
        let mut env = crate::runtime::clean_env(&std::env::vars().collect());
        env.extend(crate::scripts::env(&project));
        env.extend(
            [("HOST", "127.0.0.1"), ("HOSTNAME", "127.0.0.1"), ("BROWSER", "none")].map(|(k, v)| (k.into(), v.into())),
        );
        env.insert("PORT".into(), port.to_string()); // the app opens it
        let line = first.as_ref().map_or(cmd.clone(), |f| format!("{f} && {cmd}"));
        let child = Command::new("/bin/sh")
            .args(["-c", &line])
            .current_dir(&path)
            .env_clear()
            .envs(env)
            .stdin(Stdio::null())
            .stdout(out.try_clone().map_err(fail)?)
            .stderr(out)
            .process_group(0) // its own process group: Stop ends exactly this
            .spawn()
            .map_err(fail)?;
        let item = Item {
            team: team.into(),
            role: role.into(),
            project,
            path,
            command: cmd,
            port,
            pgid: Some(child.id() as i32),
            child,
            log,
            started: Instant::now(),
            installed: first.is_some(),
            timeout: if first.is_some() { INSTALL_TIMEOUT } else { START_TIMEOUT },
            state: "starting",
            url: None,
            shown_port: None,
            reason: String::new(),
            output: String::new(),
            fix: None,
            lost: None,
            team_live: self.reviews.team_live(team), // a stopped team's branch can be previewed too
        };
        let item = Arc::new(Mutex::new(item));
        let mut st = lock(&self.state);
        st.items.insert(key, item.clone());
        self.save(&st, None);
        drop(st);
        Ok(self.refresh(&item))
    }

    pub fn stop(&self, team: &str, role: &str) -> Value {
        let item = self.take(&(team.to_string(), role.to_string()));
        if let Some(item) = &item {
            Self::stop_item(item);
        }
        json!({"stopped": item.is_some()})
    }

    /// Previews stop when their worktree is gone (merged, discarded, removed by hand) or their team was running
    /// and has stopped, however it was stopped.
    pub fn sweep(&self) {
        let items: Vec<(Key, Shared)> = lock(&self.state).items.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        for (key, item) in items {
            let (gone, was_live) = {
                let i = lock(&item);
                (!Path::new(&i.path).is_dir(), i.team_live)
            };
            if gone || (was_live && !self.reviews.team_live(&key.0)) {
                self.stop(&key.0, &key.1);
            }
        }
    }

    pub fn list(&self) -> Vec<Value> {
        self.sweep();
        let items: Vec<Shared> = lock(&self.state).items.values().cloned().collect();
        items.iter().map(|i| self.refresh(i)).collect() // lsof per preview, outside the state's lock
    }

    fn refresh(&self, item: &Shared) -> Value {
        let (view, failed) = {
            let mut i = lock(item);
            let failed = Self::update(&mut i);
            (i.view(), failed)
        };
        if failed {
            self.save(&lock(&self.state), None);
        }
        view
    }

    /// Looks at the preview's process and port; true when it has just been found failed.
    fn update(i: &mut Item) -> bool {
        if i.state == "failed" {
            return false;
        }
        let rc = i.child.try_wait().ok().flatten().map(|s| s.code().unwrap_or(-1));
        let ports = if rc.is_some() { vec![] } else { ports_of(i.pgid.unwrap_or(0)) };
        let now = Instant::now();
        if !ports.is_empty() {
            let port = if ports.contains(&i.port) { i.port } else { ports[0] };
            (i.state, i.url, i.shown_port, i.lost) =
                ("running", Some(format!("http://localhost:{port}")), Some(port), None);
            return false;
        }
        if rc.is_none() && i.state == "running" {
            let lost = *i.lost.get_or_insert(now);
            if now - lost <= LOST_TIMEOUT {
                return false; // a restart in the dev server's own watcher closes its port for a moment
            }
        } else if rc.is_none() && now - i.started <= i.timeout {
            return false;
        }
        let lost = rc.is_none() && i.state == "running";
        kill(i.pgid.unwrap_or(0), Some(&mut i.child));
        i.output = tail(&i.log);
        (i.reason, i.fix) = if lost {
            (format!("{} stopped listening on its port, so it was stopped.", i.command), Some("retry"))
        } else {
            let (reason, fix) = why(&i.command, &i.role, rc, &i.output, &i.path, i.installed);
            (reason, Some(fix))
        };
        (i.state, i.url, i.pgid) = ("failed", None, None);
        true
    }

    pub fn handlers(this: Arc<Previews>) -> Handlers {
        let text = |p: &Value, k: &str| p[k].as_str().unwrap_or_default().to_string();
        let (a, b, c) = (this.clone(), this.clone(), this);
        vec![
            ("preview.list", Box::new(move |_, _| Ok(json!(a.list())))),
            (
                "preview.start",
                Box::new(move |p, _| {
                    let (command, confirm) = (p["command"].as_str(), p["confirm"].as_str());
                    let install = crate::pyrules::truthy(&p["install"]);
                    Ok(b.start(&text(&p, "team"), &text(&p, "role"), command, install, confirm)?)
                }),
            ),
            ("preview.stop", Box::new(move |p, _| Ok(c.stop(&text(&p, "team"), &text(&p, "role"))))),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sleeper(dir: &str) -> Child {
        let mut cmd = Command::new("/bin/sh");
        cmd.args(["-c", "sleep 60 & wait"]).current_dir(dir).process_group(0).stdout(Stdio::null());
        cmd.spawn().unwrap()
    }

    fn alive(pgid: i32) -> bool {
        // SAFETY: signal 0 only asks whether the group exists.
        unsafe { libc::killpg(pgid, 0) == 0 }
    }

    /// Only the preview's own process group is stopped, with what it started; and a saved group is stopped only while
    /// it still runs in the saved worktree.
    #[test]
    fn stops_its_own_group_and_only_where_it_was_left() {
        let (here, elsewhere) = (std::fs::canonicalize("/var/tmp").unwrap(), std::fs::canonicalize("/").unwrap());
        let (here, elsewhere) = (here.to_str().unwrap(), elsewhere.to_str().unwrap());
        let (mut a, mut b) = (sleeper(here), sleeper(here));
        let (ga, gb) = (a.id() as i32, b.id() as i32);
        std::thread::sleep(Duration::from_millis(300));
        stop_saved(json!({"a": {"pgid": ga, "path": elsewhere}}).as_object().unwrap());
        assert!(alive(ga), "a group running somewhere else is not ours");
        stop_saved(json!({"a": {"pgid": ga, "path": here}, "bad": {"pgid": 1, "path": here}}).as_object().unwrap());
        for _ in 0..50 {
            let _ = a.try_wait();
            std::thread::sleep(Duration::from_millis(100));
            if !alive(ga) {
                break;
            }
        }
        assert!(!alive(ga) && alive(gb), "the saved group went, the other did not");
        kill(gb, Some(&mut b));
        assert!(!alive(gb), "its child went with it");
    }

    /// A server that opens no port by its timeout is stopped, and the reason says what to do.
    #[test]
    fn a_silent_server_is_stopped_with_a_reason() {
        let child = sleeper("/var/tmp");
        let pgid = child.id() as i32;
        let mut item = Item {
            team: "t".into(),
            role: "r".into(),
            project: "/p".into(),
            path: "/var/tmp".into(),
            command: "npm run dev".into(),
            port: 1,
            child,
            pgid: Some(pgid),
            log: "/var/tmp/no-such-log".into(),
            started: Instant::now(),
            installed: false,
            timeout: Duration::ZERO,
            state: "starting",
            url: None,
            shown_port: None,
            reason: String::new(),
            output: String::new(),
            fix: None,
            lost: None,
            team_live: false,
        };
        std::thread::sleep(Duration::from_millis(5));
        assert!(Previews::update(&mut item));
        assert_eq!((item.state, item.fix, item.pgid), ("failed", Some("command"), None));
        assert!(item.reason.contains("didn't open a port"), "{}", item.reason);
        assert!(!alive(pgid));
    }
}
