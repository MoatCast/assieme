//! Port of `assieme/improve.py`, owned by session F (see OWNERS.md).
//!
//! Improve this team: a team started outside Assieme, compared with the recommended setup.
//!
//! Nothing new is detected here. Findings come from what already runs: the found agents' folders, step 21's reading of
//! herdr command lines (`herdr::agent_role`), and `report::aggregate` / `detect` on the team's sessions today.
//!
//! Apply gives practice rules to running agents with their next turn, as a held note: no agent is typed into mid-work or
//! restarted. Anything that needs a restart (folders, permissions, read-only reviewers) is Review only; its fix is a New
//! team draft of the same agents, started by the person's click. Each Apply is one `improve` row in the action log; its
//! Undo is in `activity`.
//!
//! The team's own herdr terminals are no longer read (that needed `hc`): `terminals` is what a caller gives, and a team
//! found from its logs has none.
use crate::herdr::{self, Env, Word};
use crate::live::Record;
use crate::prelude::*;
use serde_json::json;

/// The team's roles, as the rows label them.
const ROLE: &str = crate::team::ROLE;

fn real(path: Option<&str>) -> Option<String> {
    path.filter(|p| !p.is_empty()).map(crate::herdr::realpath)
}

fn names(roles: &[String]) -> String {
    match roles {
        [] => String::new(),
        [one] => one.clone(),
        [init @ .., last] => format!("{} and {last}", init.join(", ")),
    }
}

/// A practice group that is on: its id, its title and the texts of its rules that are on.
type Group = (String, String, Vec<String>);

fn group_rules(practices: &Value, chosen: &Map<String, Value>) -> Vec<Group> {
    let on: BTreeSet<String> =
        crate::preset::rules_on(chosen).iter().filter_map(|r| r["id"].as_str().map(String::from)).collect();
    let mut out = vec![];
    for g in practices["groups"].as_array().into_iter().flatten() {
        let texts: Vec<String> = g["rules"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|r| r["id"].as_str().is_some_and(|i| on.contains(i)))
            .filter_map(|r| r["text"].as_str().map(String::from))
            .collect();
        if !texts.is_empty() {
            out.push((
                g["id"].as_str().unwrap_or("").to_string(),
                g["title"].as_str().unwrap_or("").to_string(),
                texts,
            ));
        }
    }
    out
}

/// What an agent is sent: the rules of these groups, as one note.
fn note_text(groups: &[&Group]) -> String {
    let lines: Vec<String> = groups.iter().flat_map(|g| g.2.iter()).map(|t| format!("- {t}")).collect();
    format!("assieme: your owner added these team practices. From your next task, follow them:\n{}", lines.join("\n"))
}

/// {(role, group id)} given to this team by an Apply that wasn't undone.
fn delivered(team_name: &str, entries: &[Value], undone: &BTreeSet<String>) -> BTreeSet<(String, String)> {
    let mut out = BTreeSet::new();
    for e in entries {
        let ok = e.get("ok").is_none_or(crate::pyrules::truthy);
        if e["action"] != "improve"
            || e["team"] != team_name
            || !ok
            || e["id"].as_str().is_some_and(|i| undone.contains(i))
        {
            continue;
        }
        for s in e["sent"].as_array().into_iter().flatten() {
            if let (Some(role), Some(group)) = (s[0].as_str(), s[3].as_str()) {
                out.insert((role.to_string(), group.to_string()));
            }
        }
    }
    out
}

/// One agent of the team: by its herdr name when a terminal was given, else by its role.
#[derive(Debug, Clone)]
struct Member {
    address: String,
    cwd: Option<String>,
    terminal: Option<Value>,
}

/// Each given terminal by its name, then each log-only agent by its role.
fn members(rows: &[Value], terminals: &[Value]) -> Vec<(String, Member)> {
    let mut out: Vec<(String, Member)> = vec![];
    fn put(out: &mut Vec<(String, Member)>, name: String, m: Member) {
        match out.iter_mut().find(|(n, _)| *n == name) {
            Some(slot) => slot.1 = m,
            None => out.push((name, m)),
        }
    }
    let role = |r: &Value| r["labels"][ROLE].as_str().unwrap_or("").to_string();
    for t in terminals {
        let address = t["address"].as_str().unwrap_or("").to_string();
        let r = rows.iter().find(|r| r["address"].as_str() == Some(address.as_str()));
        let name = t["name"]
            .as_str()
            .filter(|n| !n.is_empty())
            .map(String::from)
            .unwrap_or_else(|| r.map_or(address.clone(), role));
        put(&mut out, name, Member { address, cwd: t["cwd"].as_str().map(String::from), terminal: Some(t.clone()) });
    }
    let seen: BTreeSet<String> = out.iter().map(|(_, m)| m.address.clone()).collect();
    for r in rows {
        let address = r["address"].as_str().unwrap_or("");
        if !seen.contains(address) {
            put(
                &mut out,
                role(r),
                Member { address: address.into(), cwd: r["cwd"].as_str().map(String::from), terminal: None },
            );
        }
    }
    out
}

/// (checkout, [wide permissions]) from the command line herdr shows, or None for another program.
fn command_line(env: &Env, name: &str, t: &Value) -> Option<(String, Vec<String>)> {
    let argv: Vec<Word> = t["argv"].as_array().into_iter().flatten().map(|a| a.as_str().map(String::from)).collect();
    let (prog, args, vars) = herdr::split(&argv);
    let mut prog = prog;
    if !matches!(prog.as_deref(), Some("claude" | "codex")) {
        prog = t["agent"].as_str().filter(|a| !a.is_empty()).map(String::from).or(prog);
    }
    let prog = prog.filter(|p| p == "claude" || p == "codex")?;
    let vars: Vec<String> = vars.into_keys().collect();
    let (got, notes) = herdr::agent_role(env, name, &prog, &args, &vars, false, "");
    Some((
        got["checkout"].as_str().unwrap_or("").to_string(),
        notes.into_iter().filter(|(k, ..)| k == "wide").map(|(.., d)| d).collect(),
    ))
}

fn finding(
    id: &str,
    title: &str,
    detail: &str,
    kind: &str,
    group: Option<&str>,
    to: &[String],
    restart: bool,
) -> Map<String, Value> {
    let Value::Object(f) = json!({"id": id, "kind": kind, "title": title, "detail": detail, "group": group, "to": to, "restart": restart,
                                  "copy": null, "draft": null})
    else {
        unreachable!()
    };
    f
}

/// What a check reads from the rest of the brain.
struct Ctx {
    now: Timestamp,
    today: Value,
    /// The practice groups that are on: the person's choice (`settings.practices`) of `preset::practices()`.
    on: Vec<Group>,
    /// The action log's recent rows, and the ids of the ones undone.
    entries: Vec<Value>,
    undone: BTreeSet<String>,
}

impl Ctx {
    fn new(brain: &Brain, now: Timestamp, log: Option<&Path>) -> Ctx {
        let chosen = crate::settings::load(None)["practices"].as_object().cloned().unwrap_or_default();
        let entries = crate::live::recent_actions(log, crate::activity::MAX_ROWS);
        let on = group_rules(&crate::preset::practices(None), &chosen);
        Ctx { now, today: brain.today(), on, undone: crate::activity::undone(&entries), entries }
    }
}

/// The team: its live rows, its agents and who is in it, for a team found outside Assieme; None for any other.
struct Got {
    rows: Vec<Value>,
    agents: Vec<Agent>,
    members: Vec<(String, Member)>,
    /// The New team import source of this team, for a fix that needs a restart.
    source: Option<String>,
}

fn team_of(rows: Vec<Value>, agents: Vec<Agent>, found: bool, terminals: &[Value]) -> Option<Got> {
    let rows: Vec<Value> = rows.into_iter().filter(|r| r["state"] == "live").collect();
    if !found || rows.is_empty() {
        return None;
    }
    let members = members(&rows, terminals);
    Some(Got { rows, agents, members, source: None })
}

fn findings(c: &Ctx, team_name: &str, got: Option<&Got>) -> Value {
    let mut out = json!({"team": team_name, "found": got.is_some(), "findings": [], "in_place": []});
    let Some(got) = got else { return out };
    let Got { rows, agents, members, source } = got;
    let reach: BTreeSet<&str> =
        members.iter().filter(|(_, m)| !m.address.starts_with("log:")).map(|(n, _)| n.as_str()).collect();
    // A log row stands for every terminal in its folder: what its log shows is about all of them.
    let mut in_folder: Vec<(Option<String>, Vec<String>)> = vec![];
    for (n, m) in members {
        let cwd = real(m.cwd.as_deref());
        match in_folder.iter_mut().find(|(c, _)| *c == cwd) {
            Some(slot) => slot.1.push(n.clone()),
            None => in_folder.push((cwd, vec![n.clone()])),
        }
    }
    let role_of = |r: &Value| r["labels"][ROLE].as_str().unwrap_or("").to_string();
    let names_of: BTreeMap<String, Vec<String>> = rows
        .iter()
        .map(|r| {
            let folder = real(r["cwd"].as_str());
            let at = in_folder.iter().find(|(c, _)| *c == folder).map(|(_, ns)| ns.clone());
            (role_of(r), at.unwrap_or_else(|| vec![role_of(r)]))
        })
        .collect();
    let (mut findings, mut in_place): (Vec<Map<String, Value>>, Vec<String>) = (vec![], vec![]);

    // Each agent its own folder (rule 2): needs a restart.
    let shared: Vec<&Vec<String>> =
        in_folder.iter().filter(|(c, ns)| c.is_some() && ns.len() > 1).map(|(_, ns)| ns).collect();
    for ns in &shared {
        let detail = format!(
            "{} work in the same folder, so they can edit the same files. Give each its own branch.",
            names(ns)
        );
        findings.push(finding(
            &format!("folder:{}", ns[0]),
            "Agents share one folder",
            &detail,
            "review",
            None,
            &[],
            true,
        ));
    }
    if in_folder.len() > 1 && shared.is_empty() {
        in_place.push("each agent has its own folder".into());
    }

    // Safe permissions (16) and read-only reviewers (14), from the command lines herdr shows: need a restart.
    let mut lines: Vec<(&str, String, Vec<String>)> = vec![];
    let mut env = None;
    for (n, m) in members {
        if let Some(t) = &m.terminal {
            let env = env.get_or_insert_with(Env::real);
            if let Some((checkout, wide)) = command_line(env, n, t) {
                lines.push((n, checkout, wide));
            }
        }
    }
    let reads = |n: &str| matches!(herdr::kind_of(n), "reviewer" | "planner");
    for (n, checkout, wide) in &lines {
        if !wide.is_empty() {
            let detail = format!(
                "{n} runs with {}. Start it again with safe permissions: its own copy, and no pushes.",
                wide.join(", ")
            );
            findings.push(finding(
                &format!("permissions:{n}"),
                "An agent runs with wide permissions",
                &detail,
                "review",
                None,
                &[],
                true,
            ));
        }
        if reads(n) && checkout != "read-only" {
            let detail = format!("{n} reviews or plans but can edit files. Start it again read-only.");
            findings.push(finding(
                &format!("reviewer:{n}"),
                "A reviewer can edit files",
                &detail,
                "review",
                None,
                &[],
                true,
            ));
        }
    }
    if !lines.is_empty() && lines.iter().all(|(_, _, w)| w.is_empty()) {
        in_place.push("safe permissions".into());
    }
    let readers: Vec<&String> = lines.iter().filter(|(n, ..)| reads(n)).map(|(_, c, _)| c).collect();
    if !readers.is_empty() && readers.iter().all(|c| *c == "read-only") {
        in_place.push("read-only reviewers".into());
    }

    // What today's sessions show: wake-ups for nothing, loops, review rounds, heavy runs, refusals.
    let rep = crate::teammap::team_report(rows, &c.today, c.now);
    let roles: BTreeSet<&String> = names_of.keys().collect();
    let has = |v: &Value, k: &str| v[k].as_str().is_some_and(|s| roles.contains(&s.to_string()));
    let talk =
        rep["edges"].as_array().into_iter().flatten().any(|e| has(e, "from") && has(e, "to") && e["from"] != e["to"]);
    let wasted: Vec<&Value> =
        rep["send_less"].as_array().into_iter().flatten().filter(|x| has(x, "from") && has(x, "to")).collect();
    for x in &wasted {
        let from = x["from"].as_str().unwrap_or("");
        let share = (x["share"].as_f64().unwrap_or(0.0) * 100.0).round_ties_even();
        let detail = format!(
            "{from} woke {} {} times today and {} of those did no work ({share}% of today’s tokens). Give {from} the rules for fewer, cheaper messages.",
            x["to"].as_str().unwrap_or(""),
            x["wakes"],
            x["empty"]
        );
        findings.push(finding(
            &format!("messages:{from}"),
            "Agents wake each other for nothing",
            &detail,
            "apply",
            Some("messages"),
            &names_of[from],
            false,
        ));
    }
    if talk && wasted.is_empty() {
        in_place.push("no wasted wake-ups".into());
    }
    let mine: Vec<Agent> = agents.iter().filter(|a| roles.contains(&a.name)).cloned().collect();
    let mut seen: BTreeSet<(&str, String)> = BTreeSet::new();
    let all: Vec<Value> = rep["findings"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .chain(crate::detect::live_findings(&mine, c.now))
        .collect();
    for f in &all {
        let group = match f["kind"].as_str().unwrap_or("") {
            "loop" | "review" => "stopping",
            "repeats" | "overlap" => "machine",
            _ => continue,
        };
        let who = f["agent"].as_str().unwrap_or("");
        if !roles.contains(&who.to_string()) || !seen.insert((group, who.to_string())) {
            continue;
        }
        let (title, fix) = if group == "stopping" {
            ("No stop rules", "Add the two-attempt limit and a notification when it stops.")
        } else {
            ("Heavy test runs", "Give it the rules that keep your Mac usable.")
        };
        let detail = format!("{}. {fix}", f["what"].as_str().unwrap_or(""));
        findings.push(finding(&format!("{group}:{who}"), title, &detail, "apply", Some(group), &names_of[who], false));
    }
    for x in rep["refusals"].as_array().into_iter().flatten() {
        let agent = x["agent"].as_str().unwrap_or("");
        if roles.contains(&agent.to_string()) && x["count"].as_i64().unwrap_or(0) >= crate::report::REPEATED_REFUSALS {
            let (command, why) = (x["command"].as_str().unwrap_or(""), x["why"].as_str().unwrap_or(""));
            let detail = format!(
                "{agent} tried `{command}` {} times and {why} refused it each time. Check whether it should be allowed, or change the brief so it stops trying.",
                x["count"]
            );
            // never a one-click allow: widening a rule is the person's call
            findings.push(finding(
                &format!("refused:{agent}:{command}"),
                "A command keeps being refused",
                &detail,
                "review",
                None,
                &[],
                false,
            ));
        }
    }

    // The practices themselves: a team started outside Assieme was given none, unless Improve gave them.
    let on = &c.on;
    let given = delivered(team_name, &c.entries, &c.undone);
    let lacking: Vec<String> = members
        .iter()
        .map(|(n, _)| n.clone())
        .filter(|n| on.iter().any(|g| !given.contains(&(n.clone(), g.0.clone()))))
        .collect();
    if !on.is_empty() && !lacking.is_empty() {
        let it = if lacking.len() == 1 { "it doesn’t" } else { "they don’t" };
        let detail = format!(
            "{} started outside Assieme, so {it} follow the {} practice group{} you have on.",
            names(&lacking),
            on.len(),
            if on.len() != 1 { "s" } else { "" }
        );
        findings.push(finding(
            "practices",
            "Agents don’t have the team practices",
            &detail,
            "apply",
            Some("*"),
            &lacking,
            false,
        ));
    } else if !on.is_empty() {
        in_place.push("team practices".into());
    }

    for f in &mut findings {
        if f["kind"] == "apply" {
            let group = f["group"].as_str().unwrap_or("").to_string();
            let groups: Vec<&Group> =
                if group == "*" { on.iter().collect() } else { on.iter().filter(|g| g.0 == group).collect() };
            let to: Vec<String> =
                f["to"].as_array().into_iter().flatten().filter_map(|n| n.as_str().map(String::from)).collect();
            let to: Vec<String> =
                to.into_iter().filter(|n| groups.iter().any(|g| !given.contains(&(n.clone(), g.0.clone())))).collect();
            f.insert("groups".into(), json!(groups.iter().map(|g| &g.0).collect::<Vec<_>>()));
            if to.iter().any(|n| reach.contains(n.as_str())) {
                f.insert("to".into(), json!(to.iter().filter(|n| reach.contains(n.as_str())).collect::<Vec<_>>()));
            } else {
                f.insert("to".into(), json!(to));
                if !to.is_empty() {
                    // outside herdr: nothing reaches it, so the person pastes it
                    f.insert("kind".into(), json!("review"));
                    f.insert("copy".into(), json!(note_text(&groups)));
                    let one = to.len() == 1;
                    let more = format!(
                        " Assieme can’t send {} messages, because {} in a terminal Assieme doesn’t control, so paste these rules into the next message.",
                        if one { "it" } else { "them" },
                        if one { "it runs" } else { "they run" }
                    );
                    let detail = format!("{}{more}", f["detail"].as_str().unwrap_or(""));
                    f.insert("detail".into(), json!(detail));
                }
            }
        } else if f["restart"] == true {
            f.insert("draft".into(), json!(source));
        }
    }
    out["in_place"] = json!(in_place);
    out["findings"] = findings
        .into_iter()
        .filter(|f| f["kind"] == "review" || f["to"].as_array().is_some_and(|t| !t.is_empty()))
        .map(Value::Object)
        .collect();
    out
}

/// `{team, found, findings, in_place}`. `terminals`: herdr's terminals for this team, none when None.
pub fn check(
    brain: &Brain,
    team_name: &str,
    now: Option<Timestamp>,
    log: Option<&Path>,
    terminals: Option<&[Value]>,
) -> Result<Value, BrainError> {
    let now = now.unwrap_or_else(Timestamp::now);
    let (rows, agents, found) = brain.resolve(team_name, now);
    let got = team_of(rows, agents, found, terminals.unwrap_or_default());
    Ok(findings(&Ctx::new(brain, now, log), team_name, got.as_ref()))
}

/// Give each chosen finding's rules to its agents with their next turn. One action log row per finding.
pub fn apply(
    brain: &Brain,
    team_name: &str,
    ids: &[String],
    now: Option<Timestamp>,
    log: Option<&Path>,
    terminals: Option<&[Value]>,
) -> Result<Value, BrainError> {
    let now = now.unwrap_or_else(Timestamp::now);
    let (rows, agents, found) = brain.resolve(team_name, now);
    let got = team_of(rows, agents, found, terminals.unwrap_or_default());
    let c = &Ctx::new(brain, now, log);
    let got = got.as_ref();
    let checked = findings(c, team_name, got);
    let empty = vec![];
    let members = got.map_or(&empty, |g| &g.members);
    let on = &c.on;
    let mut given = delivered(team_name, &c.entries, &c.undone);
    let mut done = vec![];
    for f in checked["findings"].as_array().into_iter().flatten() {
        if !ids.iter().any(|i| f["id"] == *i) || f["kind"] != "apply" {
            continue;
        }
        let (mut sent, mut notes): (Vec<Value>, Vec<Value>) = (vec![], vec![]);
        let wanted: Vec<&str> = f["groups"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
        for role in f["to"].as_array().into_iter().flatten().filter_map(Value::as_str) {
            let Some(m) = members.iter().find(|(n, _)| n == role).map(|(_, m)| m) else { continue };
            let Some(cwd) = m.cwd.as_deref().filter(|c| !c.is_empty()) else { continue };
            if m.address.starts_with("log:") {
                continue;
            }
            let mine: Vec<&str> =
                wanted.iter().copied().filter(|g| !given.contains(&(role.to_string(), g.to_string()))).collect();
            let groups: Vec<&Group> = mine.iter().filter_map(|g| on.iter().find(|x| x.0 == *g)).collect();
            let text = note_text(&groups);
            crate::quiet::hold_note(&m.address, cwd, &text, None, None, None, "assieme", "");
            let at = crate::herdr::realpath(cwd);
            notes.push(json!([m.address, at, text]));
            for g in &mine {
                sent.push(json!([role, m.address, at, g]));
                given.insert((role.to_string(), g.to_string()));
            }
        }
        if sent.is_empty() {
            continue;
        }
        let who: BTreeSet<String> = sent.iter().filter_map(|s| s[0].as_str().map(String::from)).collect();
        let who: Vec<String> = who.into_iter().collect();
        let group = f["group"].as_str().unwrap_or("");
        let what = if group == "*" {
            "the team practices".to_string()
        } else {
            format!("“{}”", on.iter().find(|g| g.0 == group).map_or("", |g| g.1.as_str()))
        };
        let mut extra = Map::new();
        extra.insert("title".into(), json!(format!("Gave {} {what}", names(&who))));
        extra.insert("sent".into(), Value::Array(sent));
        extra.insert("notes".into(), Value::Array(notes));
        let entry = crate::live::record(Record {
            action: "improve",
            agent: &names(&who),
            reason: f["detail"].as_str().unwrap_or(""),
            team: Some(team_name),
            log,
            now: Some(now),
            extra,
            ..Record::default()
        });
        done.push(json!({"id": f["id"], "entry": entry["id"]}));
    }
    Ok(json!({"ok": true, "applied": done}))
}

/// A note still held is taken back; one already delivered is followed by a note that it no longer applies.
pub fn undo(e: &Value, _rt: &Runtime) -> Result<(), BrainError> {
    let practices = crate::preset::practices(None);
    let held: Vec<Value> = crate::quiet::held().into_values().flatten().collect();
    let title_of = |g: &str| {
        let found = practices["groups"].as_array().into_iter().flatten().find(|x| x["id"] == g);
        found.and_then(|x| x["title"].as_str()).unwrap_or(g).to_string()
    };
    for n in e["notes"].as_array().into_iter().flatten() {
        let (address, cwd, body) = (text(&n[0]), text(&n[1]), text(&n[2]));
        let mine: Vec<Value> =
            held.iter().filter(|h| h["to"] == address && h["to_cwd"] == cwd && h["text"] == body).cloned().collect();
        if !mine.is_empty() {
            crate::quiet::drop(&mine);
            continue;
        }
        let sent = e["sent"].as_array().into_iter().flatten().filter(|s| s[1] == address && s[2] == cwd);
        let groups: Vec<String> = sent.map(|s| format!("“{}”", title_of(text(&s[3])))).collect();
        let note = format!(
            "assieme: your owner took back these team practices, so you no longer need to follow them: {}.",
            groups.join(", ")
        );
        crate::quiet::hold_note(address, cwd, &note, None, None, None, "assieme", "");
    }
    Ok(())
}

fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
