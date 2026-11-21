//! Port of `assieme/team.py`, owned by session D (see OWNERS.md).
//!
//! Start, list and stop a team of agents from a preset, through the runtime (Assieme's own core, `asm`).
//!
//! Starting one agent: its worktree, the agent with our status hook, the folder-trust prompt (asked once per
//! project, answered with explicit keys), then its first status report, and only then its brief and task.
use crate::prelude::*;
use crate::{brief, launch, preset, worktree};
use serde_json::json;
use std::time::{Duration, Instant};

pub const TEAM: &str = "assieme.team";
pub const ROLE: &str = "assieme.role";
pub const BRIEFED: &str = "assieme.briefed";
pub const KIND: &str = "assieme.kind";

/// After start, and again after answering a trust prompt; and for a screen still settling when the first status arrives.
const WAIT: Duration = Duration::from_secs(60);
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

fn tail(rt: &Runtime, t: &str) -> String {
    let screen = rt.read(t, 0).unwrap_or_default();
    let lines: Vec<&str> = screen.trim_end().lines().collect();
    lines[lines.len().saturating_sub(TAIL_LINES)..].join("\n")
}

/// The terminals of a team (all teams when None).
pub fn status(rt: &Runtime, name: Option<&str>) -> Result<Vec<Value>, RuntimeError> {
    let labels = name.map(|n| BTreeMap::from([(TEAM.to_string(), n.to_string())])).unwrap_or_default();
    Ok(rt
        .list(&labels, true)?
        .into_iter()
        .filter_map(|x| {
            let (labels, team) = (&x["labels"], x["labels"][TEAM].as_str()?);
            (name.is_none_or(|n| n == team)).then(|| {
                json!({"team": team, "role": labels[ROLE], "address": x["address"], "state": x["state"],
                       "status": x["status"]["state"], "briefed": labels[BRIEFED] == "1", "cwd": x["cwd"]})
            })
        })
        .collect())
}

/// Stop a team's live terminals. Returns how many.
pub fn stop(rt: &Runtime, name: &str) -> Result<usize, RuntimeError> {
    let live: Vec<String> = status(rt, Some(name))?
        .iter()
        .filter(|r| r["state"] == "live")
        .filter_map(|r| r["address"].as_str().map(String::from))
        .collect();
    for a in &live {
        rt.kill(a)?;
    }
    crate::bridge::revoke(&live);
    Ok(live.len())
}

/// All the core accepts in a name.
pub fn terminal_name(team_name: &str, role_name: &str) -> String {
    format!("{team_name}-{role_name}")
        .chars()
        .map(|c| if c.is_alphanumeric() || "_.-".contains(c) { c } else { '-' })
        .collect()
}

/// An agent with a parent gets its work from that parent: the whole task would have it do the same work again.
/// A parent is told who reports to it here, in its first message: a note sent mid-turn can go missing.
fn send_brief(rt: &Runtime, t: &str, role: &Role, task: Option<&str>, tm: &Team) -> Result<(), BrainError> {
    let mut parts = vec![
        role.brief.trim().to_string(),
        format!(
            "Your assieme tools: message_agent reaches a teammate by name, list_teammates shows who is here. If you are \
             stuck, or only the person can decide, call report and end your turn. Don't use `asm send`.\n{} When the \
             person answers a question of yours, call record_decision so no teammate asks again.\n\
             The person follows the work on a board of cards. add_task makes a card for a piece of work; set_task moves \
             one: doing when you start it, review when it is committed, done when there is nothing to review. \
             list_tasks shows the cards.",
            brief::READ_FIRST
        ),
    ];
    if !role.owns.is_empty() {
        parts.insert(
            1,
            format!(
                "You own these files: {}. Edit only these; ask before changing any other file.",
                role.owns.join(", ")
            ),
        );
    }
    // Without the goal: the top agent has it as its task, and one with a parent would start on the whole of it
    // instead of waiting for its share. It is in `team_brief` for any agent that looks.
    parts.extend(Some(brief::text(&tm.name, false)).filter(|s| !s.is_empty()));
    // it waits for stops, not work
    let kids: Vec<String> = tm
        .roles
        .iter()
        .filter(|r| r.parent.as_deref() == Some(&role.name) && r.name != preset::STEWARD)
        .map(|r| format!("- {}", r.name))
        .collect();
    if !kids.is_empty() {
        parts.push(format!(
            "These agents report to you and wait for you to send them work (with message_agent):\n{}\n\
             They do nothing until you do this. As soon as there is work for one of them, such as a commit to review, \
             send it. You are not finished until you have.",
            kids.join("\n")
        ));
    }
    match (task.filter(|t| !t.is_empty()), &role.parent) {
        (Some(t), _) => parts.push(t.into()),
        (None, Some(p)) => parts.push(format!("{p} will send you your work. Until then, wait and do nothing.")),
        (None, None) => {}
    }
    let (text, deadline) = (parts.join("\n\n"), Instant::now() + WAIT);
    loop {
        match rt.send(t, &text, 30.0) {
            Ok(_) => break,
            // Codex's start-up logo can keep animating past the 5 s the core waits for a screen to settle.
            Err(e) if e.kind == "unavailable" && e.message.contains("still starting") && Instant::now() < deadline => {
                std::thread::sleep(Duration::from_secs(1));
            }
            Err(e) => return Err(e.into()),
        }
    }
    rt.label(t, &BTreeMap::from([(BRIEFED.to_string(), json!("1"))]))?;
    Ok(())
}

fn review_hint(role: &Role) -> String {
    // Assieme's own hooks are trusted at launch; this is someone else's new hook. The CLI adds the attach command.
    format!(
        "{} wants you to review new hooks once. Open {} in Terminals and choose.",
        launch::name_of(&role.agent),
        role.name
    )
}

#[allow(clippy::too_many_arguments)]
fn start_role(
    tm: &Team,
    rt: &Runtime,
    ask: &dyn Fn(&str) -> bool,
    role: &Role,
    task: Option<&str>,
    trusted: &mut BTreeSet<String>,
    parent: Option<&str>,
    cwd: &str,
) -> Result<Started, BrainError> {
    let question = format!("Trust {} so {} agents can read, edit and run files there?", tm.project, role.agent);
    // they never ask on screen: Assieme asks first
    if launch::ASKS_TRUST.contains(&role.agent.as_str()) && !trusted.contains(&tm.project) {
        if !ask(&question) {
            return Ok(started(&role.name, None, "exited", "folder not trusted"));
        }
        trusted.insert(tm.project.clone());
    }
    if role.agent == "codex" {
        // Codex reads every rules file in its folder: one file for all the team's Codex agents working there.
        let there: Vec<&Role> = tm
            .roles
            .iter()
            .filter(|r| r.agent == "codex" && (r.name == role.name || worktree::folder(tm, r) == cwd))
            .collect();
        if there.iter().any(|r| r.level.is_some()) {
            worktree::exclude(&tm.project, crate::permissions::RULES_FILE);
        }
        launch::d2::write_codex_rules(cwd, &there);
    }
    let name = terminal_name(&tm.name, &role.name);
    let (ticket, port) =
        if role.container { (Some(crate::bridge::issue()), crate::bridge::port()) } else { (None, None) };
    let bridge = port.map(|p| format!("{p}/{}", ticket.as_deref().unwrap_or("")));
    let argv = launch::argv(
        role,
        preset::rule_on(&tm.practices, "no-push"),
        Some(cwd),
        Some(&name),
        bridge.as_deref(),
        trusted.contains(&tm.project),
    )?;
    let labels = BTreeMap::from([
        (TEAM.into(), tm.name.clone()),
        (ROLE.into(), role.name.clone()),
        (KIND.into(), role.agent.clone()),
    ]);
    let t = rt.start(&argv, cwd, &labels, Some(&name), parent)?;
    if let Some(ticket) = &ticket {
        crate::bridge::bind(ticket, &t);
    }
    let mut deadline = Instant::now() + WAIT;
    loop {
        let info = rt.get(&t)?;
        if info["state"] == "finished" {
            return Ok(started(&role.name, Some(&t), "exited", &tail(rt, &t)));
        }
        let screen = rt.read(&t, 0)?;
        // a new machine's look and safety notes: not a permission. Antigravity reports "blocked" on its color
        // scheme screen, so this comes before the brief
        if let Some(first) = crate::prompts::first_run_keys(&screen) {
            rt.keys(&t, &first)?;
            deadline = Instant::now() + WAIT;
            std::thread::sleep(Duration::from_secs(1));
            continue;
        }
        let trust = crate::prompts::trust_keys(&screen);
        let review = crate::prompts::hook_review(&screen);
        if info["status"]["state"] == "blocked" && trust.is_none() && !review {
            // its own setup or terms: the person's to answer, not ours
            let hint = format!(
                "{} is asking something before it starts (its own setup or terms). Open {} in Terminals and answer it.",
                launch::name_of(&role.agent),
                role.name
            );
            return Ok(started(&role.name, Some(&t), "needs-you", &hint));
        }
        if info["status"].as_object().is_some_and(|s| !s.is_empty()) {
            send_brief(rt, &t, role, task, tm)?;
            return Ok(started(&role.name, Some(&t), "running", ""));
        }
        if let Some(keys) = trust {
            if !trusted.contains(&tm.project) {
                if !ask(&question) {
                    rt.kill(&t)?;
                    return Ok(started(&role.name, Some(&t), "exited", "folder not trusted"));
                }
                trusted.insert(tm.project.clone());
            }
            rt.keys(&t, &keys)?;
            deadline = Instant::now() + WAIT;
        } else if review {
            return Ok(started(&role.name, Some(&t), "needs-you", &review_hint(role)));
        }
        if Instant::now() >= deadline {
            return Ok(started(&role.name, Some(&t), "not-reporting", &tail(rt, &t)));
        }
        std::thread::sleep(Duration::from_secs(1));
    }
}

/// Start a team's roles (only `only` when given); `ask` answers a yes-or-no question.
pub fn start(
    tm: &Team,
    task: Option<&str>,
    rt: &Runtime,
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
    let missing = launch::missing_clis(&roles.iter().map(|r| (*r).clone()).collect::<Vec<_>>());
    if !missing.is_empty() {
        return Err(TeamError(format!(
            "not installed: {}. Install it, then start the team again.",
            missing.join(", ")
        ))
        .into());
    }
    for r in roles.iter().filter(|r| r.container) {
        if let Some(p) = launch::d2::container_problem(&r.agent).filter(|p| !p.is_empty()) {
            return Err(TeamError(format!("{} runs in a container. {p}", r.name)).into());
        }
    }
    worktree::check_repo(&tm.project)?;
    let task = task.filter(|t| !t.is_empty());
    if let (Some(t), None) = (task, only) {
        brief::save(&tm.name, Some(t), None)?; // what the whole team is for; rules and decisions stay
    }
    let live: BTreeMap<String, Value> = status(rt, Some(&tm.name))?
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
    let mut out = vec![];
    for role in roles {
        // asked of this one agent, or the team's top
        let mine = if only.is_some() || role.parent.is_none() { task } else { None };
        let s = match live.get(&role.name) {
            None => {
                let parent = role.parent.as_deref().and_then(|p| addresses.get(p)).and_then(Option::as_deref);
                let s = start_role(tm, rt, ask, role, mine, &mut trusted, parent, &cwds[&role.name])?;
                if let (Some(p), true) = (parent, only.is_some() && task.is_none() && s.state == "running") {
                    // an agent added to a running team: its parent's first message is long gone. The parent may
                    // have gone: the new agent still started.
                    let note = format!(
                        "assieme: {0} now reports to you and waits for work. Reach it with message_agent, as \"{0}\".",
                        role.name
                    );
                    let _ = rt.send(p, &note, 30.0);
                }
                s
            }
            Some(row) if row["briefed"] == true => started(&role.name, row["address"].as_str(), "already-running", ""),
            Some(row) if !row["status"].is_null() => {
                // live but never briefed, e.g. after the user answered Codex's hook review
                send_brief(rt, row["address"].as_str().unwrap_or(""), role, mine, tm)?;
                started(&role.name, row["address"].as_str(), "running", "")
            }
            Some(row) => started(&role.name, row["address"].as_str(), "needs-you", &review_hint(role)),
        };
        addresses.insert(role.name.clone(), s.address.clone());
        out.push(s);
    }
    Ok(out)
}
