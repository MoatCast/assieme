//! Port of `assieme/activity.py`, owned by session B (see OWNERS.md).
//!
//! Activity: the action log as rows in plain words, and Undo.
//!
//! Undo exists only where it really undoes something. A message already delivered, or memory already freed, has none.
//! The brain checks the time limit, never the app.
use crate::prelude::*;
use crate::pyrules::truthy;
use crate::{launch, live, newteam, permissions, preset, quiet, settings, status, team};
use serde_json::json;

const UNDO_S: f64 = 24.0 * 3600.0;
pub const MAX_ROWS: usize = 500;
const NO_WORDS: [&str; 2] = ["menu", "answer"]; // older rows of these held the agent's question: it stays out of what is shown
const NEVER_WORDS: [&str; 1] = ["ask-changes"]; // older rows of this held a request, or a reviewer's note
const EVERY_TEAM: [&str; 3] = ["fix_mode", "practice", "undo"]; // settings apply to all teams
const TITLES: [(&str, &str); 12] = [
    ("quiet", "Started holding notes from {}"),
    ("compact", "Freed up memory for {}"),
    ("nudge", "Asked {} to try another way"),
    ("tell", "Told {} why a command is blocked"),
    ("add_agent", "Added {}"),
    ("menu", "Answered {}"),
    ("answer", "Answered {}"),
    ("deliver", "Delivered held notes to {}"),
    ("dropped", "Couldn't deliver held notes to {}"),
    ("run_found", "Let Assieme run {}"),
    ("send", "Sent a message to {}"),
    ("stop_agent", "Stopped {}"),
];
const FIX_NAMES: [(&str, &str); 4] = [
    ("nudge", "Ask stuck agents to try another way"),
    ("compact", "Free up memory between tasks"),
    ("tell", "Explain blocked commands"),
    ("quiet", "Hold routine notes until the receiver is free"),
];
/// Files Assieme wrote whole into a folder.
const OWN_FILES: [&str; 3] = [permissions::RULES_FILE, permissions::GEMINI_POLICY, launch::CURSOR_OURS];

/// Step 9's merges: the Review object holds what an Undo needs, and logs its own row.
static MERGES: Mutex<Option<Arc<Review>>> = Mutex::new(None);

pub fn register_merge(review: Arc<Review>) {
    *MERGES.lock().unwrap_or_else(|e| e.into_inner()) = Some(review);
}

/// The ids of entries an undo already took back.
pub fn undone(entries: &[Value]) -> BTreeSet<String> {
    let undoes = |e: &&Value| matches!(e["action"].as_str(), Some("undo" | "undo-merge"));
    entries.iter().filter(undoes).filter_map(|e| e["of"].as_str()).map(String::from).collect()
}

fn text<'a>(e: &'a Value, k: &str) -> &'a str {
    e[k].as_str().unwrap_or("")
}

fn ok(e: &Value) -> bool {
    e.get("ok").is_none_or(truthy)
}

fn title(e: &Value) -> String {
    if let Some(t) = e["title"].as_str().filter(|t| !t.is_empty()) {
        return t.into();
    }
    let action = text(e, "action");
    let agent = e["agent"].as_str().unwrap_or("None");
    match action {
        "fix_mode" => {
            let fix = text(e, "fix");
            let name = FIX_NAMES.iter().find(|f| f.0 == fix).map_or(fix, |f| f.1);
            format!("Set \"{name}\" to {}", if e["new"] == "auto" { "Automatic" } else { "Ask first" })
        }
        "practice" => format!("Turned {agent} {}", if truthy(&e["new"]) { "on" } else { "off" }),
        "undo" => format!("Undid: {}", e["undid"].as_str().filter(|u| !u.is_empty()).unwrap_or("an action")),
        _ => TITLES.iter().find(|t| t.0 == action).map_or(format!("{action} {agent}"), |t| t.1.replace("{}", agent)),
    }
}

/// (button label, seconds it stays available or None for as long as it applies); None where nothing can be undone.
fn undo_kind(action: &str) -> Option<(&'static str, Option<f64>)> {
    match action {
        "quiet" => Some(("Stop", None)),
        "add_agent" => Some(("Stop", Some(UNDO_S))),
        "merge" => MERGES.lock().unwrap_or_else(|e| e.into_inner()).is_some().then_some(("Undo", Some(UNDO_S))),
        "tools" | "bringover" | "fix_mode" | "run_found" | "practice" | "improve" => Some(("Undo", Some(UNDO_S))),
        _ => None,
    }
}

fn confirm(e: &Value) -> Option<String> {
    let agent = e["agent"].as_str().unwrap_or("None");
    match text(e, "action") {
        "add_agent" => Some(format!("This ends {agent} and anything it's doing right now. Its branch and files stay.")),
        "bringover" => Some("Stop this team's agents and forget the team? Branches and folders stay.".into()),
        _ => None, // never kill without asking
    }
}

/// Why this entry can't be undone now, or None if it can.
fn unavailable(e: &Value, undone: &BTreeSet<String>, now: Timestamp) -> Option<&'static str> {
    let action = text(e, "action");
    let id = text(e, "id");
    let Some((_, limit)) = undo_kind(action).filter(|_| !id.is_empty() && ok(e)) else {
        return Some("This can't be undone.");
    };
    if undone.contains(id) {
        return Some("This was already undone.");
    }
    let at = status::parse_ts(&e["at"]);
    if limit.is_some_and(|l| at.is_none_or(|at| (now - at).as_secs_f64() > l)) {
        return Some("It has been more than 24 hours, so this can no longer be undone.");
    }
    let applies = match action {
        "quiet" => e["pair"]
            .as_array()
            .is_some_and(|p| p.len() > 1 && quiet::is_quiet(p[0].as_str().unwrap_or(""), p[1].as_str().unwrap_or(""))),
        "merge" => truthy(&e["restore"]), // after a brain restart the record is only in the log
        _ => true,
    };
    (!applies).then_some("This has already stopped.")
}

fn hhmm(t: Option<Timestamp>) -> Value {
    t.map_or(Value::Null, |t| json!(t.to_zoned_datetime(jiff::tz::TimeZone::system()).strftime("%H:%M").to_string()))
}

/// The action log as rows, newest first. `filter` is "all", "you" or "auto".
pub fn list_rows(team: Option<&str>, filter: &str, path: Option<&Path>, now: Option<Timestamp>) -> Value {
    let now = now.unwrap_or_else(status::now_utc);
    let entries = live::recent_actions(path, MAX_ROWS);
    let undone = undone(&entries);
    let outcomes: BTreeMap<&str, &Value> =
        entries.iter().filter(|e| e["action"] == "outcome").filter_map(|e| Some((e["of"].as_str()?, e))).collect();
    let mut rows = vec![];
    for e in entries.iter().rev().filter(|e| e["action"] != "outcome") {
        let by = e["by"].as_str().unwrap_or("you");
        let action = text(e, "action");
        let theirs = e["team"].as_str() == team || (e["team"].is_null() && EVERY_TEAM.contains(&action));
        if (filter != "all" && by != filter) || (team.is_some_and(|t| !t.is_empty()) && !theirs) {
            continue;
        }
        let at = status::parse_ts(&e["at"]).map(|t| t.to_zoned_datetime(jiff::tz::TimeZone::system()));
        let mut reason = match (NEVER_WORDS.contains(&action), NO_WORDS.contains(&action) && e.get("id").is_none()) {
            (false, false) => text(e, "reason"),
            _ => "",
        }
        .to_string();
        if !ok(e) {
            reason = format!(
                "It didn't work: {}",
                Some(text(e, "detail")).filter(|d| !d.is_empty()).unwrap_or("no reason given")
            );
        }
        let out = e["id"].as_str().and_then(|id| outcomes.get(id));
        let until = status::parse_ts(&e["check_until"]);
        let outcome = match (out, until) {
            (Some(o), _) => o["result"].clone(),
            (None, Some(u)) if now < u => json!("checking"),
            _ => Value::Null,
        };
        let undo = unavailable(e, &undone, now).is_none().then(|| {
            let mut u = json!({"label": undo_kind(action).map_or("Undo", |k| k.0)});
            if let Some(c) = confirm(e) {
                u["confirm"] = json!(c);
            }
            u
        });
        rows.push(json!({
            "id": e["id"], "at": at.as_ref().map_or(String::new(), |t| t.strftime("%H:%M").to_string()),
            "day": at.as_ref().map_or(String::new(), |t| t.strftime("%Y-%m-%d").to_string()),
            "title": title(e), "reason": reason, "by": by, "ok": ok(e), "agent": e["agent"], "action": e["action"],
            "outcome": outcome, "outcome_at": out.map_or(Value::Null, |o| hhmm(status::parse_ts(&o["at"]))),
            "check_until": hhmm(until), "measured": e["measured"], "problem": e["problem"], "sent": e["sent"], "undo": undo,
        }));
    }
    json!({"rows": rows})
}

pub fn undo(id: &str, rt: &Runtime, path: Option<&Path>, now: Option<Timestamp>) -> Value {
    let now = now.unwrap_or_else(status::now_utc);
    let entries = live::recent_actions(path, MAX_ROWS);
    let Some(e) = entries.iter().find(|x| x["id"] == id) else {
        return json!({"ok": false, "detail": "This can't be undone."});
    };
    if let Some(why) = unavailable(e, &undone(&entries), now) {
        return json!({"ok": false, "detail": why});
    }
    if let Err(err) = run_undo(e, rt) {
        // a failed Undo is a message on the row, and stays available to try again
        return json!({"ok": false, "detail": err.message.chars().take(120).collect::<String>()});
    }
    if text(e, "action") != "merge" {
        // a merge logs its own undo row
        let mut extra = Map::new();
        extra.extend([("of".into(), json!(id)), ("undid".into(), json!(title(e)))]);
        live::record(live::Record {
            action: "undo",
            agent: e["agent"].as_str().unwrap_or(""),
            reason: "You undid this.",
            team: e["team"].as_str(),
            log: path,
            now: Some(now),
            extra,
            ..Default::default()
        });
    }
    json!({"ok": true, "detail": ""})
}

fn run_undo(e: &Value, rt: &Runtime) -> Result<(), BrainError> {
    let s = |k: &str| e[k].as_str().unwrap_or("");
    match s("action") {
        "quiet" => {
            let p = e["pair"].as_array().ok_or_else(|| BrainError::new("This has already stopped."))?;
            let at = |i: usize| p.get(i).and_then(Value::as_str).unwrap_or("");
            quiet::set_pair(at(0), at(1), at(2), false); // notes already held are still delivered
        }
        "tools" => crate::agenttools::undo(e)?,
        "bringover" => {
            // Stop the team, forget it, and take back the files Assieme wrote into the brought-over folders.
            // Branches, commits, folders and the other tool's own files stay as they are.
            team::stop(rt, s("team"))?;
            let _ = std::fs::remove_dir_all(newteam::teams_dir().join(s("team")));
            for folder in e["adopted"].as_array().into_iter().flatten().filter_map(Value::as_str) {
                if Path::new(folder).is_dir() {
                    launch::unmerge(folder);
                }
                OWN_FILES.iter().for_each(|rel| drop(std::fs::remove_file(Path::new(folder).join(rel))));
            }
        }
        "fix_mode" => drop(settings::set_value(&format!("fixes.{}", s("fix")), &e["old"], None)?),
        "run_found" => drop(settings::set_value(&format!("run_found.{}", s("team")), &json!(truthy(&e["old"])), None)?),
        "practice" => apply_practice(s("group"), truthy(&e["old"]), rt)?,
        "improve" => crate::improve::undo(e, rt)?,
        "add_agent" => {
            let info = rt.get(s("address"))?;
            let label = |k: &str| info["labels"][k].as_str().unwrap_or("");
            if info["state"] != "live" {
                return Err(BrainError::new("This agent has already stopped."));
            }
            if label(team::TEAM) != s("team") || label(team::ROLE) != s("agent") {
                return Err(BrainError::new("That terminal is no longer this agent.")); // only remove terminals you started
            }
            rt.kill(s("address"))?;
            // no saved folder to tidy is fine: the agent itself has stopped
            let _ = newteam::forget_role(&newteam::teams_dir().join(s("team")), s("agent"));
        }
        "merge" => {
            let review = MERGES.lock().unwrap_or_else(|e| e.into_inner()).clone();
            let review = review.ok_or_else(|| BrainError::new("This can't be undone."))?;
            review.remember(s("id"), &e["restore"]); // after a brain restart the record is only in the log
            review.undo(s("id"))?;
        }
        _ => return Err(BrainError::new("This can't be undone.")),
    }
    Ok(())
}

/// (title, rules it covers, whether running agents are told) for a group, rule or bigger-team key.
pub fn practice_item(key: &str) -> (String, Vec<Value>, bool) {
    let p = preset::practices(None);
    for g in p["groups"].as_array().into_iter().flatten() {
        if g["id"] == key {
            return (text(g, "title").into(), g["rules"].as_array().cloned().unwrap_or_default(), true);
        }
        if let Some(r) = g["rules"].as_array().into_iter().flatten().find(|r| r["id"] == key) {
            return (text(r, "title").into(), vec![r.clone()], true);
        }
    }
    let b = p["bigger"].as_array().into_iter().flatten().find(|b| b["id"] == key);
    (b.map_or(key, |b| text(b, "title")).into(), vec![], false)
}

fn apply_practice(key: &str, on: bool, rt: &Runtime) -> Result<(), BrainError> {
    let chosen = |s: &Value| s["practices"].as_object().cloned().unwrap_or_default();
    let before = chosen(&settings::load(None));
    let after = chosen(&settings::set_value(&format!("practices.{key}"), &json!(on), None)?);
    let (title, rules, tell) = practice_item(key);
    // Agents hear only about rules whose real state changes: what new teams get (`preset::rule_on`), so a rule turned
    // on inside a group that is off is saved for later but told to no one. A bigger-team preset shapes the next team only.
    let rules: Vec<&Value> = rules
        .iter()
        .filter(|r| preset::rule_on(&after, text(r, "id")) != preset::rule_on(&before, text(r, "id")))
        .collect();
    if !tell || rules.is_empty() {
        return Ok(());
    }
    let mut note = format!("assieme: the team practice \"{title}\" is now {}.", if on { "on" } else { "off" });
    if on {
        let list: Vec<String> = rules.iter().map(|r| format!("- {}", text(r, "text"))).collect();
        note += &format!(" From your next task, follow these:\n{}", list.join("\n"));
    } else {
        note += " You no longer need to follow it.";
    }
    let Ok(rows) = rt.list(&BTreeMap::new(), false) else { return Ok(()) }; // no core running means no agents to tell
    for r in rows.iter().filter(|r| r["state"] == "live" && !r["labels"][team::TEAM].is_null() && truthy(&r["cwd"])) {
        quiet::hold_note(
            text(r, "address"),
            text(r, "cwd"),
            &note,
            Some(&rt.session),
            Some(&rt.sock),
            None,
            "assieme",
            "",
        );
    }
    Ok(())
}

/// Saved as New team's default; each running agent is told with its next turn, through quiet send.
pub fn change_practice(
    key: &str,
    on: bool,
    rt: &Runtime,
    log: Option<&Path>,
    now: Option<Timestamp>,
) -> Result<Value, BrainError> {
    let bigger = preset::practices(None)["bigger"].as_array().into_iter().flatten().any(|b| b["id"] == key);
    let old = settings::load(None)["practices"].get(key).map_or(!bigger, truthy);
    apply_practice(key, on, rt)?;
    let extra = Map::from_iter([("group".into(), json!(key)), ("old".into(), json!(old)), ("new".into(), json!(on))]);
    let title = practice_item(key).0;
    Ok(live::record(live::Record {
        action: "practice",
        agent: &title,
        reason: "You changed this in Settings.",
        log,
        now,
        extra,
        ..Default::default()
    }))
}

pub fn change_fix(fix: &str, mode: &str, log: Option<&Path>, now: Option<Timestamp>) -> Result<Value, BrainError> {
    let old = settings::load(None)["fixes"][fix].as_str().unwrap_or("ask").to_string();
    settings::set_value(&format!("fixes.{fix}"), &json!(mode), None)?;
    let extra = Map::from_iter([("fix".into(), json!(fix)), ("old".into(), json!(old)), ("new".into(), json!(mode))]);
    Ok(live::record(live::Record {
        action: "fix_mode",
        agent: fix,
        reason: "You changed this in Settings.",
        log,
        now,
        extra,
        ..Default::default()
    }))
}
