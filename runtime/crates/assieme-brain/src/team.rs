//! Port of `assieme/team.py`, owned by session D (see OWNERS.md).
//!
//! Start, list and stop a team of agents from a preset, through the runtime (Assieme's own core, `asm`).
//!
//! Starting one agent: its worktree, the agent with our status hook, the folder-trust prompt (asked once per
//! project, answered with explicit keys), then its first status report, and only then its brief and task.
use crate::prelude::*;
use crate::{brief, launch, preset, worktree};
use serde_json::json;

pub const TEAM: &str = "assieme.team";
pub const ROLE: &str = "assieme.role";
pub const BRIEFED: &str = "assieme.briefed";
pub const KIND: &str = "assieme.kind";

/// After start, and again after answering a trust prompt.
const FIRST_STATUS_S: f64 = 60.0;
/// For a screen that is still settling when the first status arrives.
const BRIEF_WAIT_S: f64 = 60.0;
const TAIL_LINES: usize = 8;

#[derive(Debug, Clone, PartialEq)]
pub struct TeamError(pub String);

impl From<TeamError> for BrainError {
    fn from(e: TeamError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// One role's start.
#[derive(Debug, Clone, PartialEq)]
pub struct Started {
    pub role: String,
    pub address: Option<String>,
    /// running, needs-you, not-reporting, exited, already-running
    pub state: String,
    pub note: String,
}

fn started(role: &str, address: Option<&str>, state: &str, note: &str) -> Started {
    Started { role: role.into(), address: address.map(String::from), state: state.into(), note: note.into() }
}

type Keys = Option<Vec<&'static str>>;

/// Everything a start reaches outside this module for: the core, the clock, what is on a screen, the bridge.
pub trait World {
    fn list(&self, labels: &BTreeMap<String, String>, all: bool) -> Result<Vec<Value>, RuntimeError>;
    fn start(
        &self,
        argv: &[String],
        cwd: &str,
        labels: &BTreeMap<String, String>,
        name: &str,
        parent: Option<&str>,
    ) -> Result<String, RuntimeError>;
    fn get(&self, t: &str) -> Result<Value, RuntimeError>;
    fn read(&self, t: &str) -> Result<String, RuntimeError>;
    fn send(&self, t: &str, text: &str) -> Result<(), RuntimeError>;
    fn keys(&self, t: &str, keys: &[&str]) -> Result<(), RuntimeError>;
    fn label(&self, t: &str, labels: &BTreeMap<String, Value>) -> Result<(), RuntimeError>;
    fn kill(&self, t: &str) -> Result<(), RuntimeError>;
    /// Seconds on a clock that only goes forward.
    fn now(&self) -> f64;
    fn sleep(&self, seconds: f64);
    fn first_run_keys(&self, screen: &str) -> Keys;
    fn trust_keys(&self, screen: &str) -> Keys;
    fn hook_review(&self, screen: &str) -> bool;
    fn missing_clis(&self, roles: &[Role]) -> Vec<String>;
    /// Where an agent's program is installed.
    fn which(&self, name: &str) -> Option<PathBuf>;
    fn container_problem(&self, agent: &str) -> Option<String>;
    fn bridge_issue(&self) -> String;
    fn bridge_port(&self) -> Option<u16>;
    fn bridge_bind(&self, ticket: &str, address: &str);
    fn bridge_revoke(&self, addresses: &[String]);
}

/// The real thing: Assieme's core and this machine.
struct Live<'a>(&'a Runtime);

impl World for Live<'_> {
    fn list(&self, labels: &BTreeMap<String, String>, all: bool) -> Result<Vec<Value>, RuntimeError> {
        self.0.list(labels, all)
    }
    fn start(
        &self,
        argv: &[String],
        cwd: &str,
        labels: &BTreeMap<String, String>,
        name: &str,
        parent: Option<&str>,
    ) -> Result<String, RuntimeError> {
        self.0.start(argv, cwd, labels, Some(name), parent)
    }
    fn get(&self, t: &str) -> Result<Value, RuntimeError> {
        self.0.get(t)
    }
    fn read(&self, t: &str) -> Result<String, RuntimeError> {
        self.0.read(t, 0)
    }
    fn send(&self, t: &str, text: &str) -> Result<(), RuntimeError> {
        self.0.send(t, text, 30.0).map(|_| ())
    }
    fn keys(&self, t: &str, keys: &[&str]) -> Result<(), RuntimeError> {
        self.0.keys(t, keys)
    }
    fn label(&self, t: &str, labels: &BTreeMap<String, Value>) -> Result<(), RuntimeError> {
        self.0.label(t, labels)
    }
    fn kill(&self, t: &str) -> Result<(), RuntimeError> {
        self.0.kill(t)
    }
    fn now(&self) -> f64 {
        static START: LazyLock<std::time::Instant> = LazyLock::new(std::time::Instant::now);
        START.elapsed().as_secs_f64()
    }
    fn sleep(&self, seconds: f64) {
        std::thread::sleep(std::time::Duration::from_secs_f64(seconds));
    }
    fn first_run_keys(&self, screen: &str) -> Keys {
        crate::prompts::first_run_keys(screen)
    }
    fn trust_keys(&self, screen: &str) -> Keys {
        crate::prompts::trust_keys(screen)
    }
    fn hook_review(&self, screen: &str) -> bool {
        crate::prompts::hook_review(screen)
    }
    fn missing_clis(&self, roles: &[Role]) -> Vec<String> {
        launch::missing_clis(roles)
    }
    fn which(&self, name: &str) -> Option<PathBuf> {
        crate::setup::agent_path(name)
    }
    fn container_problem(&self, agent: &str) -> Option<String> {
        launch::policy().container_problem(agent)
    }
    fn bridge_issue(&self) -> String {
        crate::bridge::issue()
    }
    fn bridge_port(&self) -> Option<u16> {
        crate::bridge::port()
    }
    fn bridge_bind(&self, ticket: &str, address: &str) {
        crate::bridge::bind(ticket, address);
    }
    fn bridge_revoke(&self, addresses: &[String]) {
        crate::bridge::revoke(addresses);
    }
}

fn tail(w: &dyn World, t: &str) -> String {
    let screen = w.read(t).unwrap_or_default();
    let lines: Vec<&str> = screen.trim_end().lines().collect();
    lines[lines.len().saturating_sub(TAIL_LINES)..].join("\n")
}

fn labels_of(name: Option<&str>) -> BTreeMap<String, String> {
    name.map(|n| BTreeMap::from([(TEAM.to_string(), n.to_string())])).unwrap_or_default()
}

fn status_with(w: &dyn World, name: Option<&str>) -> Result<Vec<Value>, RuntimeError> {
    let mut rows = vec![];
    for x in w.list(&labels_of(name), true)? {
        let labels = &x["labels"];
        let Some(team) = labels[TEAM].as_str() else { continue };
        if name.is_some_and(|n| n != team) {
            continue;
        }
        rows.push(json!({"team": team, "role": labels[ROLE], "address": x["address"], "state": x["state"],
                         "status": x["status"]["state"], "briefed": labels[BRIEFED] == "1", "cwd": x["cwd"]}));
    }
    Ok(rows)
}

/// The terminals of a team (all teams when None).
pub fn status(rt: &Runtime, name: Option<&str>) -> Result<Vec<Value>, RuntimeError> {
    status_with(&Live(rt), name)
}

fn stop_with(w: &dyn World, name: &str) -> Result<usize, RuntimeError> {
    let live: Vec<String> = status_with(w, Some(name))?
        .iter()
        .filter(|r| r["state"] == "live")
        .filter_map(|r| r["address"].as_str().map(String::from))
        .collect();
    for a in &live {
        w.kill(a)?;
    }
    w.bridge_revoke(&live);
    Ok(live.len())
}

/// Stop a team's live terminals. Returns how many.
pub fn stop(rt: &Runtime, name: &str) -> Result<usize, RuntimeError> {
    stop_with(&Live(rt), name)
}

const OWNS: &str = "You own these files: {files}. Edit only these; ask before changing any other file.";
const WAIT_FOR_PARENT: &str = "{parent} will send you your work. Until then, wait and do nothing.";
const REPORT_TO_YOU: &str = "These agents report to you and wait for you to send them work (with message_agent):\n{agents}\n\
They do nothing until you do this. As soon as there is work for one of them, such as a commit to review, send it. You are not finished until you have.";

fn tools_text() -> String {
    format!(
        "Your assieme tools: message_agent reaches a teammate by name, list_teammates shows who is here. If you are \
         stuck, or only the person can decide, call report and end your turn. Don't use `asm send`.\n{} When the \
         person answers a question of yours, call record_decision so no teammate asks again.\n\
         The person follows the work on a board of cards. add_task makes a card for a piece of work; set_task moves \
         one: doing when you start it, review when it is committed, done when there is nothing to review. \
         list_tasks shows the cards.",
        brief::READ_FIRST
    )
}

/// All the core accepts in a name.
pub fn terminal_name(team_name: &str, role_name: &str) -> String {
    format!("{team_name}-{role_name}")
        .chars()
        .map(|c| if c.is_alphanumeric() || matches!(c, '_' | '.' | '-') { c } else { '-' })
        .collect()
}

/// An agent with a parent gets its work from that parent: the whole task would have it do the same work again.
/// A parent is told who reports to it here, in its first message: a note sent mid-turn can go missing.
fn brief_text(role: &Role, task: Option<&str>, tm: &Team) -> String {
    let mut parts = vec![role.brief.trim().to_string(), tools_text()];
    if !role.owns.is_empty() {
        parts.insert(1, OWNS.replace("{files}", &role.owns.join(", ")));
    }
    // Without the goal: the top agent has it as its task, and one with a parent would start on the whole of it
    // instead of waiting for its share. It is in `team_brief` for any agent that looks.
    let shared = brief::text(&tm.name, false);
    if !shared.is_empty() {
        parts.push(shared);
    }
    // it waits for stops, not work
    let kids: Vec<String> = tm
        .roles
        .iter()
        .filter(|r| r.parent.as_deref() == Some(&role.name) && r.name != preset::STEWARD)
        .map(|r| format!("- {}", r.name))
        .collect();
    if !kids.is_empty() {
        parts.push(REPORT_TO_YOU.replace("{agents}", &kids.join("\n")));
    }
    match (task.filter(|t| !t.is_empty()), &role.parent) {
        (Some(t), _) => parts.push(t.to_string()),
        (None, Some(p)) => parts.push(WAIT_FOR_PARENT.replace("{parent}", p)),
        (None, None) => {}
    }
    parts.join("\n\n")
}

fn send_brief(w: &dyn World, t: &str, role: &Role, task: Option<&str>, tm: &Team) -> Result<(), BrainError> {
    let text = brief_text(role, task, tm);
    let deadline = w.now() + BRIEF_WAIT_S;
    loop {
        match w.send(t, &text) {
            Ok(()) => break,
            // Codex's start-up logo can keep animating past the 5 s the core waits for a screen to settle.
            Err(e) if e.kind == "unavailable" && e.message.contains("still starting") && w.now() < deadline => {
                w.sleep(1.0);
            }
            Err(e) => return Err(e.into()),
        }
    }
    w.label(t, &BTreeMap::from([(BRIEFED.to_string(), json!("1"))]))?;
    Ok(())
}

/// An agent added to a running team: its parent's first message is long gone.
fn tell_parent(w: &dyn World, role: &Role, addresses: &BTreeMap<String, Option<String>>) {
    let parent = role.parent.as_deref().and_then(|p| addresses.get(p)).and_then(Option::as_deref);
    if let Some(p) = parent {
        // the parent has gone: the new agent still started
        let _ = w.send(
            p,
            &format!(
                "assieme: {0} now reports to you and waits for work. Reach it with message_agent, as \"{0}\".",
                role.name
            ),
        );
    }
}

fn asking_hint(role: &Role) -> String {
    format!(
        "{} is asking something before it starts (its own setup or terms). Open {} in Terminals and answer it.",
        launch::name_of(&role.agent),
        role.name
    )
}

fn review_hint(role: &Role) -> String {
    // Assieme's own hooks are trusted at launch; this is someone else's new hook. The CLI adds the attach command.
    format!("{} wants you to review new hooks once. Open {} in Terminals and choose.", launch::name_of(&role.agent), role.name)
}

/// Codex reads every rules file in its folder: one file for all the team's Codex agents working there.
fn codex_rules(tm: &Team, role: &Role, cwd: &str) {
    let there: Vec<&Role> =
        tm.roles.iter().filter(|r| r.agent == "codex" && (r.name == role.name || worktree::folder(tm, r) == cwd)).collect();
    if there.iter().any(|r| r.level.is_some()) {
        worktree::exclude(&tm.project, crate::permissions::RULES_FILE);
    }
    launch::policy().write_codex_rules(cwd, &there);
}

fn trust_question(tm: &Team, role: &Role) -> String {
    format!("Trust {} so {} agents can read, edit and run files there?", tm.project, role.agent)
}

struct Ctx<'a> {
    tm: &'a Team,
    w: &'a dyn World,
    ask: &'a dyn Fn(&str) -> bool,
}

#[allow(clippy::too_many_arguments)]
fn start_role(
    c: &Ctx,
    role: &Role,
    task: Option<&str>,
    trusted: &mut BTreeSet<String>,
    parent: Option<&str>,
    cwd: &str,
) -> Result<Started, BrainError> {
    let (tm, w) = (c.tm, c.w);
    // they never ask on screen: Assieme asks first
    if launch::ASKS_TRUST.contains(&role.agent.as_str()) && !trusted.contains(&tm.project) {
        if !(c.ask)(&trust_question(tm, role)) {
            return Ok(started(&role.name, None, "exited", "folder not trusted"));
        }
        trusted.insert(tm.project.clone());
    }
    if role.agent == "codex" {
        codex_rules(tm, role, cwd);
    }
    let name = terminal_name(&tm.name, &role.name);
    let (ticket, port) = if role.container { (Some(w.bridge_issue()), w.bridge_port()) } else { (None, None) };
    let bridge = port.map(|p| format!("{p}/{}", ticket.as_deref().unwrap_or("")));
    let argv = launch::argv_with(
        role,
        preset::rule_on(&tm.practices, "no-push"),
        &|n| w.which(n),
        Some(cwd),
        Some(&name),
        bridge.as_deref(),
        trusted.contains(&tm.project),
    )?;
    let labels = BTreeMap::from([
        (TEAM.to_string(), tm.name.clone()),
        (ROLE.to_string(), role.name.clone()),
        (KIND.to_string(), role.agent.clone()),
    ]);
    let t = w.start(&argv, cwd, &labels, &name, parent)?;
    if let Some(ticket) = &ticket {
        w.bridge_bind(ticket, &t);
    }
    let mut deadline = w.now() + FIRST_STATUS_S;
    loop {
        let info = w.get(&t)?;
        if info["state"] == "finished" {
            return Ok(started(&role.name, Some(&t), "exited", &tail(w, &t)));
        }
        let screen = w.read(&t)?;
        // a new machine's look and safety notes: not a permission
        if let Some(first) = w.first_run_keys(&screen) {
            // Antigravity reports "blocked" on its color scheme screen, so this comes before the brief
            w.keys(&t, &first)?;
            deadline = w.now() + FIRST_STATUS_S;
            w.sleep(1.0);
            continue;
        }
        let has_status = info["status"].as_object().is_some_and(|s| !s.is_empty());
        if info["status"]["state"] == "blocked" && w.trust_keys(&screen).is_none() && !w.hook_review(&screen) {
            // its own setup or terms: the person's to answer, not ours
            return Ok(started(&role.name, Some(&t), "needs-you", &asking_hint(role)));
        }
        if has_status {
            send_brief(w, &t, role, task, tm)?;
            return Ok(started(&role.name, Some(&t), "running", ""));
        }
        if let Some(keys) = w.trust_keys(&screen) {
            if !trusted.contains(&tm.project) {
                if !(c.ask)(&trust_question(tm, role)) {
                    w.kill(&t)?;
                    return Ok(started(&role.name, Some(&t), "exited", "folder not trusted"));
                }
                trusted.insert(tm.project.clone());
            }
            w.keys(&t, &keys)?;
            deadline = w.now() + FIRST_STATUS_S;
        } else if w.hook_review(&screen) {
            return Ok(started(&role.name, Some(&t), "needs-you", &review_hint(role)));
        }
        if w.now() >= deadline {
            return Ok(started(&role.name, Some(&t), "not-reporting", &tail(w, &t)));
        }
        w.sleep(1.0);
    }
}

pub fn start_with(
    w: &dyn World,
    tm: &Team,
    task: Option<&str>,
    ask: &dyn Fn(&str) -> bool,
    only: Option<&str>,
) -> Result<Vec<Started>, BrainError> {
    let mut roles: Vec<&Role> = tm.roles.iter().collect();
    if let Some(o) = only.filter(|o| !o.is_empty()) {
        roles.retain(|r| r.name == o);
        if roles.is_empty() {
            return Err(TeamError(format!("no role named '{o}' in team '{}'", tm.name)).into());
        }
    }
    let wanted: Vec<Role> = roles.iter().map(|r| (*r).clone()).collect();
    let missing = w.missing_clis(&wanted);
    if !missing.is_empty() {
        return Err(TeamError(format!("not installed: {}. Install it, then start the team again.", missing.join(", "))).into());
    }
    for r in &roles {
        if let Some(p) = r.container.then(|| w.container_problem(&r.agent)).flatten().filter(|p| !p.is_empty()) {
            return Err(TeamError(format!("{} runs in a container. {p}", r.name)).into());
        }
    }
    worktree::check_repo(&tm.project)?;
    let task = task.filter(|t| !t.is_empty());
    if let (Some(t), None) = (task, only) {
        brief::save(&tm.name, Some(t), None)?; // what the whole team is for; rules and decisions stay
    }
    let live: BTreeMap<String, Value> = status_with(w, Some(&tm.name))?
        .into_iter()
        .filter(|r| r["state"] == "live")
        .filter_map(|r| Some((r["role"].as_str()?.to_string(), r)))
        .collect();
    // Every worktree before the first agent, so a branch or worktree problem refuses before anything runs.
    let mut cwds = BTreeMap::new();
    for r in roles.iter().filter(|r| !live.contains_key(&r.name)) {
        cwds.insert(r.name.clone(), worktree::ensure(tm, r)?);
    }
    let mut trusted = BTreeSet::new();
    let mut addresses: BTreeMap<String, Option<String>> =
        live.iter().map(|(n, r)| (n.clone(), r["address"].as_str().map(String::from))).collect();
    let c = Ctx { tm, w, ask };
    let mut out = vec![];
    for role in roles {
        // asked of this one agent, or the team's top
        let mine = if only.is_some() || role.parent.is_none() { task } else { None };
        let s = match live.get(&role.name) {
            None => {
                let parent = role.parent.as_deref().and_then(|p| addresses.get(p)).and_then(Option::as_deref);
                let s = start_role(&c, role, mine, &mut trusted, parent, &cwds[&role.name])?;
                if only.is_some() && task.is_none() && s.state == "running" {
                    tell_parent(w, role, &addresses);
                }
                s
            }
            Some(row) if row["briefed"] == true => started(&role.name, row["address"].as_str(), "already-running", ""),
            Some(row) if !row["status"].is_null() => {
                // live but never briefed, e.g. after the user answered Codex's hook review
                send_brief(w, row["address"].as_str().unwrap_or(""), role, mine, tm)?;
                started(&role.name, row["address"].as_str(), "running", "")
            }
            Some(row) => started(&role.name, row["address"].as_str(), "needs-you", &review_hint(role)),
        };
        addresses.insert(role.name.clone(), s.address.clone());
        out.push(s);
    }
    Ok(out)
}

/// Start a team's roles (only `only` when given); `ask` answers a yes-or-no question.
pub fn start(
    tm: &Team,
    task: Option<&str>,
    rt: &Runtime,
    ask: &dyn Fn(&str) -> bool,
    only: Option<&str>,
) -> Result<Vec<Started>, BrainError> {
    start_with(&Live(rt), tm, task, ask, only)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    /// A core in memory: terminals are made by `start`, and each one's replies are scripted.
    #[derive(Default)]
    struct Fake {
        clock: RefCell<f64>,
        made: RefCell<Vec<(String, Vec<String>, BTreeMap<String, String>, Option<String>)>>,
        terminals: RefCell<BTreeMap<String, Value>>,
        screens: RefCell<BTreeMap<String, String>>,
        sent: RefCell<Vec<(String, String)>>,
        keyed: RefCell<Vec<(String, Vec<String>)>>,
        killed: RefCell<Vec<String>>,
        labelled: RefCell<Vec<String>>,
        revoked: RefCell<Vec<String>>,
        listing: RefCell<Vec<Value>>,
        /// Sends fail this many times with "still starting".
        busy: RefCell<u32>,
        /// What a new terminal reports at the first look (default: a working agent).
        first: RefCell<Option<Value>>,
        missing: Vec<String>,
    }

    impl World for Fake {
        fn list(&self, labels: &BTreeMap<String, String>, _: bool) -> Result<Vec<Value>, RuntimeError> {
            Ok(self.listing.borrow().iter().filter(|x| labels.iter().all(|(k, v)| x["labels"][k] == *v)).cloned().collect())
        }
        fn start(&self, argv: &[String], _: &str, labels: &BTreeMap<String, String>, name: &str, parent: Option<&str>) -> Result<String, RuntimeError> {
            let t = format!("t{}", self.made.borrow().len() + 1);
            self.made.borrow_mut().push((name.into(), argv.to_vec(), labels.clone(), parent.map(String::from)));
            let info = self.first.borrow().clone().unwrap_or_else(|| json!({"state": "live", "status": {"state": "idle"}}));
            self.terminals.borrow_mut().insert(t.clone(), info);
            Ok(t)
        }
        fn get(&self, t: &str) -> Result<Value, RuntimeError> {
            Ok(self.terminals.borrow()[t].clone())
        }
        fn read(&self, t: &str) -> Result<String, RuntimeError> {
            Ok(self.screens.borrow().get(t).cloned().unwrap_or_default())
        }
        fn send(&self, t: &str, text: &str) -> Result<(), RuntimeError> {
            if *self.busy.borrow() > 0 {
                *self.busy.borrow_mut() -= 1;
                return Err(RuntimeError { kind: "unavailable".into(), message: "the screen is still starting".into(), hint: String::new() });
            }
            self.sent.borrow_mut().push((t.into(), text.into()));
            Ok(())
        }
        fn keys(&self, t: &str, keys: &[&str]) -> Result<(), RuntimeError> {
            self.keyed.borrow_mut().push((t.into(), keys.iter().map(|k| k.to_string()).collect()));
            // answering the prompt gets the agent going
            self.terminals.borrow_mut().insert(t.into(), json!({"state": "live", "status": {"state": "idle"}}));
            self.screens.borrow_mut().insert(t.into(), String::new());
            Ok(())
        }
        fn label(&self, t: &str, labels: &BTreeMap<String, Value>) -> Result<(), RuntimeError> {
            self.labelled.borrow_mut().push(format!("{t} {labels:?}"));
            Ok(())
        }
        fn kill(&self, t: &str) -> Result<(), RuntimeError> {
            self.killed.borrow_mut().push(t.into());
            Ok(())
        }
        fn now(&self) -> f64 {
            *self.clock.borrow()
        }
        fn sleep(&self, s: f64) {
            *self.clock.borrow_mut() += s;
        }
        fn first_run_keys(&self, screen: &str) -> Keys {
            screen.contains("PICK A COLOR").then(|| vec!["enter"])
        }
        fn trust_keys(&self, screen: &str) -> Keys {
            screen.contains("Yes, I trust this folder").then(|| vec!["down", "enter"])
        }
        fn hook_review(&self, screen: &str) -> bool {
            screen.contains("Hooks need review")
        }
        fn missing_clis(&self, _: &[Role]) -> Vec<String> {
            self.missing.clone()
        }
        fn which(&self, _: &str) -> Option<PathBuf> {
            None
        }
        fn container_problem(&self, _: &str) -> Option<String> {
            None
        }
        fn bridge_issue(&self) -> String {
            "ticket".into()
        }
        fn bridge_port(&self) -> Option<u16> {
            Some(9)
        }
        fn bridge_bind(&self, _: &str, _: &str) {}
        fn bridge_revoke(&self, a: &[String]) {
            self.revoked.borrow_mut().extend(a.iter().cloned());
        }
    }

    fn repo() -> (tempfile::TempDir, String) {
        let d = tempfile::tempdir().unwrap();
        let p = std::fs::canonicalize(d.path()).unwrap().to_string_lossy().into_owned();
        for a in [vec!["init", "-q", "-b", "main"], vec!["config", "user.name", "T"], vec!["config", "user.email", "t@t"]] {
            assert!(worktree::run(&p, &a).ok);
        }
        std::fs::write(format!("{p}/f"), "x").unwrap();
        assert!(worktree::run(&p, &["add", "."]).ok);
        assert!(worktree::run(&p, &["commit", "-q", "-m", "x"]).ok);
        (d, p)
    }

    fn team(project: &str, roles: Vec<Role>) -> Team {
        Team { name: "demo".into(), project: project.into(), roles, ..Team::default() }
    }

    fn role(name: &str, agent: &str, parent: Option<&str>) -> Role {
        Role { brief: format!("You are {name}."), parent: parent.map(String::from), ..Role::new(name, agent) }
    }

    fn yes(_: &str) -> bool {
        true
    }

    /// Runs `body` with the state folder in a temporary place, since a start saves the team's goal.
    fn with_state<T>(body: impl FnOnce() -> T) -> T {
        static ONE: Mutex<()> = Mutex::new(());
        let _g = ONE.lock().unwrap_or_else(|p| p.into_inner());
        let dir = tempfile::tempdir().unwrap();
        // SAFETY: tests that set the environment hold ONE, so no other thread reads it meanwhile.
        unsafe { std::env::set_var("XDG_STATE_HOME", dir.path()) };
        body()
    }

    #[test]
    fn a_names_characters_are_the_ones_the_core_accepts() {
        assert_eq!(terminal_name("my team", "dév/1"), "my-team-dév-1");
    }

    #[test]
    fn an_agent_is_started_in_its_own_copy_then_briefed_with_its_task() {
        with_state(|| {
            let (_d, p) = repo();
            let tm = team(&p, vec![Role { owns: vec!["src/".into()], ..role("lead", "claude", None) }, role("dev", "claude", Some("lead"))]);
            let w = Fake::default();
            let got = start_with(&w, &tm, Some("Ship it"), &yes, None).unwrap();
            assert_eq!(got.iter().map(|s| (s.role.as_str(), s.state.as_str())).collect::<Vec<_>>(), [("lead", "running"), ("dev", "running")]);
            let made = w.made.borrow();
            assert_eq!(made[0].0, "demo-lead");
            assert_eq!(made[0].2[TEAM], "demo");
            assert_eq!(made[0].2[KIND], "claude");
            assert_eq!(made[1].3.as_deref(), Some("t1"), "a child starts under its parent");
            let sent = w.sent.borrow();
            let (lead, dev) = (&sent[0].1, &sent[1].1);
            assert!(lead.starts_with("You are lead.\n\nYou own these files: src/."), "{lead}");
            assert!(lead.contains("- dev") && lead.ends_with("Ship it"), "{lead}");
            assert!(dev.ends_with("lead will send you your work. Until then, wait and do nothing."), "{dev}");
            assert!(!dev.contains("Ship it"));
            assert!(Path::new(&format!("{p}/.assieme/worktrees/dev")).is_dir());
            assert_eq!(w.labelled.borrow().len(), 2);
        });
    }

    #[test]
    fn the_teams_goal_is_saved_unless_one_agent_is_added() {
        with_state(|| {
            let (_d, p) = repo();
            let tm = team(&p, vec![role("lead", "claude", None)]);
            start_with(&Fake::default(), &tm, Some("The goal"), &yes, None).unwrap();
            assert_eq!(brief::load("demo")["goal"], "The goal");
            start_with(&Fake::default(), &tm, Some("Other"), &yes, Some("lead")).unwrap();
            assert_eq!(brief::load("demo")["goal"], "The goal", "a task for one added agent is not the team's goal");
        });
    }

    #[test]
    fn an_unknown_role_a_missing_program_or_a_bad_project_stop_everything_first() {
        with_state(|| {
            let (_d, p) = repo();
            let tm = team(&p, vec![role("lead", "claude", None)]);
            let w = Fake::default();
            let e = start_with(&w, &tm, None, &yes, Some("nobody")).unwrap_err();
            assert_eq!(e.message, "no role named 'nobody' in team 'demo'");
            let gone = Fake { missing: vec!["codex".into(), "gemini".into()], ..Fake::default() };
            let e = start_with(&gone, &tm, None, &yes, None).unwrap_err();
            assert_eq!(e.message, "not installed: codex, gemini. Install it, then start the team again.");
            let not_git = tempfile::tempdir().unwrap();
            let bad = team(not_git.path().to_str().unwrap(), vec![role("lead", "claude", None)]);
            assert!(start_with(&w, &bad, None, &yes, None).unwrap_err().message.contains("isn't set up to keep versions yet"));
            assert!(w.made.borrow().is_empty(), "nothing ran");
        });
    }

    #[test]
    fn a_worktree_problem_refuses_before_any_agent_runs() {
        with_state(|| {
            let (_d, p) = repo();
            std::fs::create_dir_all(format!("{p}/.assieme/worktrees/dev")).unwrap();
            let tm = team(&p, vec![role("lead", "claude", None), role("dev", "claude", Some("lead"))]);
            let w = Fake::default();
            let e = start_with(&w, &tm, None, &yes, None).unwrap_err();
            assert!(e.message.contains("A folder is in the way of dev's copy"), "{}", e.message);
            assert!(w.made.borrow().is_empty());
        });
    }

    #[test]
    fn a_kind_that_needs_trust_asks_once_per_project_and_a_no_stops_that_agent() {
        with_state(|| {
            let (_d, p) = repo();
            let tm = team(&p, vec![role("a", "gemini", None), role("b", "gemini", None)]);
            let asked = RefCell::new(vec![]);
            let ask = |q: &str| {
                asked.borrow_mut().push(q.to_string());
                true
            };
            let w = Fake::default();
            start_with(&w, &tm, None, &ask, None).unwrap();
            assert_eq!(asked.borrow().len(), 1, "once per project");
            assert_eq!(asked.borrow()[0], format!("Trust {p} so gemini agents can read, edit and run files there?"));
            assert!(w.made.borrow()[0].1.join(" ").contains("GEMINI_CLI_TRUST_WORKSPACE=true"));
            let no = Fake::default();
            let got = start_with(&no, &team(&p, vec![role("c", "gemini", None)]), None, &|_: &str| false, None).unwrap();
            assert_eq!(got[0], started("c", None, "exited", "folder not trusted"));
            assert!(no.made.borrow().is_empty());
        });
    }

    #[test]
    fn a_trust_prompt_on_screen_is_answered_with_keys_after_asking() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            *w.first.borrow_mut() = Some(json!({"state": "live", "status": null}));
            w.screens.borrow_mut().insert("t1".into(), "Yes, I trust this folder".into());
            let got = start_with(&w, &team(&p, vec![role("a", "claude", None)]), None, &yes, None).unwrap();
            assert_eq!(got[0].state, "running");
            assert_eq!(w.keyed.borrow()[0], ("t1".to_string(), vec!["down".to_string(), "enter".to_string()]));
            let w = Fake::default();
            *w.first.borrow_mut() = Some(json!({"state": "live", "status": null}));
            w.screens.borrow_mut().insert("t1".into(), "Yes, I trust this folder".into());
            let (_d2, p2) = repo();
            let got = start_with(&w, &team(&p2, vec![role("a", "claude", None)]), None, &|_: &str| false, None).unwrap();
            assert_eq!(got[0], started("a", Some("t1"), "exited", "folder not trusted"));
            assert_eq!(*w.killed.borrow(), ["t1"]);
        });
    }

    #[test]
    fn a_first_run_screen_is_passed_with_enter_before_anything_else() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            *w.first.borrow_mut() = Some(json!({"state": "live", "status": {"state": "blocked"}}));
            w.screens.borrow_mut().insert("t1".into(), "PICK A COLOR".into());
            let got = start_with(&w, &team(&p, vec![role("a", "antigravity", None)]), None, &yes, None).unwrap();
            assert_eq!(got[0].state, "running");
            assert_eq!(w.keyed.borrow()[0].1, ["enter"]);
        });
    }

    #[test]
    fn an_agent_asking_its_own_question_or_a_hook_review_is_the_persons_to_answer() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            *w.first.borrow_mut() = Some(json!({"state": "live", "status": {"state": "blocked"}}));
            let got = start_with(&w, &team(&p, vec![role("a", "claude", None)]), None, &yes, None).unwrap();
            assert_eq!(got[0].state, "needs-you");
            assert!(got[0].note.starts_with("Claude Code is asking something before it starts"), "{}", got[0].note);
            let (_d2, p2) = repo();
            let w = Fake::default();
            *w.first.borrow_mut() = Some(json!({"state": "live", "status": null}));
            w.screens.borrow_mut().insert("t1".into(), "Hooks need review".into());
            let got = start_with(&w, &team(&p2, vec![role("a", "codex", None)]), None, &yes, None).unwrap();
            assert_eq!(got[0].state, "needs-you");
            assert_eq!(got[0].note, "Codex wants you to review new hooks once. Open a in Terminals and choose.");
        });
    }

    #[test]
    fn an_agent_that_never_reports_is_not_reporting_after_a_minute_with_its_screen_tail() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            *w.first.borrow_mut() = Some(json!({"state": "live", "status": null}));
            let long: String = (1..=12).map(|i| format!("line {i}\n")).collect();
            w.screens.borrow_mut().insert("t1".into(), long);
            let got = start_with(&w, &team(&p, vec![role("a", "claude", None)]), None, &yes, None).unwrap();
            assert_eq!(got[0].state, "not-reporting");
            assert_eq!(got[0].note, "line 5\nline 6\nline 7\nline 8\nline 9\nline 10\nline 11\nline 12");
            assert!(*w.clock.borrow() >= 60.0 && *w.clock.borrow() < 62.0);
        });
    }

    #[test]
    fn an_agent_that_has_already_finished_is_exited_with_its_last_lines() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            *w.first.borrow_mut() = Some(json!({"state": "finished"}));
            w.screens.borrow_mut().insert("t1".into(), "boom\n".into());
            let got = start_with(&w, &team(&p, vec![role("a", "claude", None)]), None, &yes, None).unwrap();
            assert_eq!(got[0], started("a", Some("t1"), "exited", "boom"));
        });
    }

    #[test]
    fn a_brief_waits_out_a_screen_that_is_still_starting_but_not_forever() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            *w.busy.borrow_mut() = 3;
            let got = start_with(&w, &team(&p, vec![role("a", "codex", None)]), None, &yes, None).unwrap();
            assert_eq!(got[0].state, "running");
            assert_eq!(w.sent.borrow().len(), 1);
            let (_d2, p2) = repo();
            let w = Fake::default();
            *w.busy.borrow_mut() = 1000;
            let e = start_with(&w, &team(&p2, vec![role("a", "claude", None)]), None, &yes, None).unwrap_err();
            assert!(e.message.contains("still starting"));
        });
    }

    #[test]
    fn a_team_already_running_is_left_alone_and_a_new_agent_tells_its_parent() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            let row = |role: &str, addr: &str, briefed: &str, status: Value| {
                json!({"address": addr, "state": "live", "status": status, "cwd": "/w",
                       "labels": {TEAM: "demo", ROLE: role, BRIEFED: briefed}})
            };
            *w.listing.borrow_mut() = vec![
                row("lead", "t9", "1", json!({"state": "idle"})),
                row("review", "t8", "", json!({"state": "idle"})),
                row("stuck", "t7", "", Value::Null),
            ];
            let tm = team(&p, vec![role("lead", "claude", None), role("review", "claude", Some("lead")), role("stuck", "claude", Some("lead")), role("new", "claude", Some("lead"))]);
            let got = start_with(&w, &tm, None, &yes, Some("new")).unwrap();
            assert_eq!(got.len(), 1);
            assert_eq!(w.made.borrow()[0].3.as_deref(), Some("t9"));
            let sent = w.sent.borrow();
            assert!(sent.iter().any(|(t, m)| t == "t9" && m.contains("new now reports to you")), "{sent:?}");
            drop(sent);
            let w2 = Fake::default();
            *w2.listing.borrow_mut() = w.listing.borrow().clone();
            let all = start_with(&w2, &tm, None, &yes, None).unwrap();
            let states: Vec<_> = all.iter().map(|s| s.state.as_str()).collect();
            assert_eq!(states, ["already-running", "running", "needs-you", "running"]);
            assert_eq!(w2.made.borrow().len(), 1, "only the missing one is started");
        });
    }

    #[test]
    fn a_container_role_gets_a_bridge_ticket_and_the_project_s_push_block() {
        with_state(|| {
            let (_d, p) = repo();
            let w = Fake::default();
            let r = Role { container: true, ..role("a", "claude", None) };
            start_with(&w, &team(&p, vec![r]), None, &yes, None).unwrap();
            assert!(w.made.borrow()[0].1.join(" ").contains("docker run demo-a"));
        });
    }

    #[test]
    fn status_lists_only_this_teams_terminals_and_stop_kills_the_live_ones() {
        let w = Fake::default();
        let row = |team: &str, role: &str, addr: &str, state: &str| {
            json!({"address": addr, "state": state, "status": {"state": "idle"}, "cwd": "/w",
                   "labels": {TEAM: team, ROLE: role, BRIEFED: "1"}})
        };
        *w.listing.borrow_mut() = vec![row("demo", "a", "t1", "live"), row("demo", "b", "t2", "finished"), row("other", "c", "t3", "live"), json!({"address": "t4", "labels": {}})];
        let all = status_with(&w, None).unwrap();
        assert_eq!(all.len(), 3, "a terminal that is not a team's is skipped");
        let mine = status_with(&w, Some("demo")).unwrap();
        assert_eq!(mine.len(), 2);
        assert_eq!(mine[0], json!({"team": "demo", "role": "a", "address": "t1", "state": "live", "status": "idle", "briefed": true, "cwd": "/w"}));
        assert_eq!(stop_with(&w, "demo").unwrap(), 1);
        assert_eq!(*w.killed.borrow(), ["t1"]);
        assert_eq!(*w.revoked.borrow(), ["t1"]);
    }
}
