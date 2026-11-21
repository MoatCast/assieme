//! Port of `assieme/panes.py`, owned by session B (see OWNERS.md).
//!
//! The Terminals view: one pane per Assieme agent, with its state, the needs-you strip, and the buttons that act.
//!
//! Panes are built from the core's terminal rows (hook status, folder, program), the log agents `status::collect` finds,
//! matched by worktree folder, the same suggestions as the command-line live view (`live::suggest`), the menu on the
//! agent's screen, and suggested answers to a plain-word question (`Answers`).
use crate::prelude::*;
use crate::{adapters, brief, budget, fixes, launch, live, prompts, reports, settings, status, team};
use serde_json::json;
use sha1::{Digest, Sha1};

pub const URGENT: [&str; 3] = ["asking", "stuck", "waiting"]; // what the needs-you strip lists, in this order
const ACTIONS: [&str; 3] = ["nudge", "compact", "tell"]; // `quiet` stays in the command line
const CHANGED: &str = "This question changed. Take a look.";
pub const ANSWERED: &str = "You answered its question."; // never the question: the action log keeps no words of the agents'
const RUN_GAP_MS: i64 = 120_000; // agents of one start are created within seconds of each other

use crate::quiet::real_opt as real;

fn ms(r: &Value) -> i64 {
    r["created_ms"].as_i64().unwrap_or(0)
}

fn role_of(r: &Value) -> &str {
    r["labels"][team::ROLE].as_str().filter(|s| !s.is_empty()).unwrap_or_else(|| r["address"].as_str().unwrap_or(""))
}

use crate::quiet::stamp;

/// Terminal address -> the most recent log agent working in that terminal's folder.
pub fn matching(rows: &[Value], agents: &[Agent]) -> BTreeMap<String, Agent> {
    let mut out: BTreeMap<String, Agent> = BTreeMap::new();
    for r in rows {
        let here = real(r["cwd"].as_str());
        let best = agents.iter().filter(|a| a.cwd.is_some() && real(a.cwd.as_deref()) == here).max_by_key(|a| a.last);
        if let (Some(a), Some(address)) = (best, r["address"].as_str()) {
            out.insert(address.into(), a.clone());
        }
    }
    out
}

/// A short stable id. Never stored: a button is only ever compared with the ones on the pane right now.
pub fn id(parts: &[&str]) -> String {
    Sha1::digest(parts.join("\u{1f}")).iter().take(5).map(|b| format!("{b:02x}")).collect()
}

fn state_of(r: &Value, a: Option<&Agent>, waiting: &BTreeSet<String>) -> &'static str {
    let hook = r["status"]["state"].as_str();
    let log = a.map_or("", |a| a.state.as_str());
    if r["state"] == "finished" {
        "exited"
    } else if hook == Some("blocked") || log == "waiting on you" {
        "asking"
    } else if log == "stuck" {
        "stuck"
    } else if log == "idle, work waiting" || a.is_some_and(|a| waiting.contains(&a.name)) {
        "waiting"
    } else if hook == Some("working") || log == "working" {
        "working"
    } else {
        "idle"
    }
}

fn since(r: &Value, a: Option<&Agent>, state: &str, now: Timestamp) -> Value {
    let when = a.and_then(|a| a.ask_since.filter(|_| state == "asking").or(a.last));
    if let Some(when) = when {
        return json!((now - when).as_secs_f64().max(0.0) as i64);
    }
    match r["status_ms"].as_f64().filter(|m| *m != 0.0) {
        Some(m) => json!((now.as_millisecond() as f64 / 1000.0 - m / 1000.0).max(0.0) as i64),
        None => Value::Null,
    }
}

fn cap(text: &str) -> String {
    let mut c = text.chars();
    c.next().map_or(String::new(), |f| f.to_uppercase().chain(c).collect())
}

/// `about` (the text above a menu) is part of the id: two menus with the same choices are different questions.
pub fn button(role: &str, kind: &str, label: &str, send: Option<&str>, about: &str) -> Value {
    json!({"id": id(&[role, kind, label, send.unwrap_or("\0"), about]), "label": label, "kind": kind, "send": send})
}

/// `question`: the agent's plain-word question, when that is what it waits on (an answer goes in the team brief).
pub fn fix(role: &str, buttons: Vec<Value>, text: &str, question: Option<&str>) -> Value {
    let ids: Vec<&str> = buttons.iter().filter_map(|b| b["id"].as_str()).collect();
    json!({"id": id(&[role, text, &ids.join(",")]), "text": text, "buttons": buttons, "question": question})
}

/// The rows of the team's latest start: the core keeps finished terminals of earlier runs under the same name.
pub fn current_run(rows: &[Value]) -> Vec<Value> {
    let live = rows.iter().filter(|r| r["state"] == "live").map(ms);
    let first = live.min().unwrap_or_else(|| rows.iter().map(ms).max().unwrap_or(0));
    rows.iter().filter(|r| r["state"] == "live" || ms(r) >= first - RUN_GAP_MS).cloned().collect()
}

/// One row per role: the live terminal, else the newest (a restarted team keeps its old, finished rows).
fn latest(rows: Vec<Value>) -> Vec<Value> {
    let mut best: Vec<((bool, i64), Value)> = vec![];
    for r in rows {
        let key = (r["state"] == "live", ms(&r));
        match best.iter_mut().find(|b| role_of(&b.1) == role_of(&r)) {
            Some(b) if key > b.0 => *b = (key, r),
            Some(_) => {}
            None => best.push((key, r)),
        }
    }
    best.into_iter().map(|b| b.1).collect()
}

/// The program an argv runs: past a leading `env` and its KEY=VALUE words (the push block wraps agents in them).
fn program(argv: &Value) -> String {
    let words: Vec<&str> = argv.as_array().map_or(vec![], |a| a.iter().filter_map(Value::as_str).collect());
    let base = |w: &str| Path::new(w).file_name().map_or(String::new(), |f| f.to_string_lossy().into());
    let from = words.iter().position(|w| base(w) != "env" && !w.contains('=')).unwrap_or(words.len().saturating_sub(1));
    words.get(from).map_or(String::new(), |w| base(w))
}

#[allow(clippy::too_many_arguments)]
pub fn view(
    team_name: &str,
    rows: &[Value],
    agents: &[Agent],
    now: Timestamp,
    read: &dyn Fn(&str) -> String,
    answers: &Answers,
    show_commands: bool,
) -> Value {
    let mut rows = latest(current_run(rows));
    rows.sort_by_key(ms);
    let memory_full = settings::threshold("memory_full"); // Settings › Fixes; read once per view
    let matched = matching(&rows, agents);
    let addr: BTreeMap<String, String> = matched.iter().map(|(t, a)| (a.name.clone(), t.clone())).collect();
    let stalls = status::stalls(agents, now);
    let queue = status::owner_queue(agents, now);
    let roots: BTreeSet<String> = stalls
        .iter()
        .flat_map(|s| s["roots"].as_array().into_iter().flatten())
        .filter(|r| crate::pyrules::truthy(&r["waiting"]))
        .filter_map(|r| r["agent"].as_str().map(String::from))
        .collect();
    let holds: BTreeMap<&str, Vec<&Value>> =
        queue.iter().filter_map(|q| Some((q["agent"].as_str()?, q["blocks"].as_array()?.iter().collect()))).collect();
    let suggestions: Vec<Value> = live::suggest(agents, &stalls, &addr, &queue, &[], Some(now), &[])
        .into_iter()
        .filter(|s| ACTIONS.contains(&s["action"].as_str().unwrap_or("")))
        .collect();
    let (mut out, mut strip) = (vec![], vec![]);
    for r in &rows {
        let role = role_of(r);
        let address = r["address"].as_str().unwrap_or("");
        let a = matched.get(address);
        let mut state = state_of(r, a, &roots);
        // The agent's own word beats guessing from logs; a real prompt on screen beats both.
        let mut report = None;
        if state == "exited" {
            reports::clear(team_name, role);
        } else if state != "asking" {
            report = reports::current(team_name, role, r);
        }
        if let Some(rep) = &report {
            state = if rep["state"] == "need_decision" { "asking" } else { "stuck" };
        }
        let kind = r["labels"][team::KIND].as_str().map(String::from).unwrap_or_else(|| {
            let argv0 = program(&r["argv"]); // a container runs `docker`
            launch::CLIS.iter().find(|c| c.1 == argv0).map_or("claude", |c| c.0).into()
        });
        let refusals: Vec<Value> = a.map_or(vec![], |a| {
            let window = (live::REFUSAL_WINDOW + fixes::CHECK_S) as f64;
            a.refused.iter().filter(|x| (now - x.0).as_secs_f64() <= window).map(|x| json!([stamp(x.0), x.1])).collect()
        });
        let mut p = json!({
            "role": role, "agent": kind, "address": address, "state": state, "since_s": since(r, a, state, now),
            "detail": "", "live": state != "exited", "fix": null, "command": null,
            "memory_full": a.is_some_and(|a| status::memory_limit(a).is_some_and(|l| a.context as f64 > l as f64 * memory_full)),
            // for fixes::check_outcomes only: Brain::map strips it, as a label can hold command arguments
            "refused": refusals,
            "turn_at": a.and_then(|a| a.turn_start).map(stamp), // brain-only too
            "busy": r["status"]["state"] == "working", // mid-turn by its own hook, whatever the logs suggest
        });
        if let Some(rep) = &report {
            p["since_s"] = json!(
                (now.as_millisecond() as f64 / 1000.0 - rep["ms"].as_f64().unwrap_or(0.0) / 1000.0).max(0.0) as i64
            );
        }
        let note = report.as_ref().and_then(|r| r["note"].as_str());
        if state == "exited" {
            let screen = read(address);
            p["detail"] = json!(screen.lines().map(str::trim).rfind(|l| !l.is_empty()).unwrap_or(""));
        } else if state == "asking" {
            let screen = read(address);
            let menu = if report.is_some() { vec![] } else { prompts::menu(&screen) };
            let mut q = note.map(String::from);
            let about = if menu.is_empty() { String::new() } else { prompts::menu_context(&screen) };
            let ours = prompts::permission_labels(&menu);
            let mut buttons: Vec<Value> = menu
                .iter()
                .map(|(n, label)| {
                    button(role, "menu", ours.get(n).copied().unwrap_or(label), Some(&n.to_string()), &about)
                })
                .collect();
            if menu.is_empty() {
                q = q.or_else(|| prompts::question(&screen));
                let got = q.as_deref().and_then(|q| answers.get(&format!("{team_name}/{role}/{}", id(&[q])), q));
                buttons = got
                    .iter()
                    .flat_map(|g| g.as_array().into_iter().flatten().filter_map(Value::as_str))
                    .map(|x| button(role, "answer", x, Some(x), ""))
                    .collect();
            }
            buttons.push(button(role, "reply", "Reply", None, ""));
            let blocks = a.and_then(|a| holds.get(a.name.as_str())).map_or(0, Vec::len);
            p["detail"] = json!(if blocks > 0 {
                format!("Holding up {blocks} agent{}", if blocks == 1 { "" } else { "s" })
            } else {
                "Waiting for you".into()
            });
            let ask_kind = a.map(|a| a.ask_kind.as_str()).unwrap_or("");
            let text = [q.as_deref().unwrap_or(""), about.lines().last().unwrap_or(""), ask_kind, "Waiting for you"]
                .into_iter()
                .find(|t| !t.is_empty())
                .unwrap_or("");
            p["fix"] = fix(role, buttons, text, q.as_deref().filter(|_| menu.is_empty()));
            strip.push(json!({"role": role, "state": state, "text": format!("{role} is asking: {text}")}));
        } else {
            let s = a.and_then(|a| suggestions.iter().find(|s| s["agent"] == a.name.as_str()));
            let said = match (s, a) {
                (Some(s), Some(a)) => s["text"].as_str().unwrap_or("").replace(&a.name, role), // the log's name for it is a long folder path
                _ => String::new(),
            };
            let detail = a.map_or("", |a| a.detail.as_str());
            if let (Some(last), "stuck") = (a.and_then(|a| a.cmds.last()), state) {
                p["command"] = json!(if show_commands { last.clone() } else { adapters::program(last) });
            }
            if let Some(note) = note {
                p["detail"] = json!(note);
                strip.push(json!({"role": role, "state": state, "text": format!("{role} is stuck: {note}")}));
                if s.is_none() {
                    p["fix"] = fix(role, vec![button(role, "reply", "Reply", None, "")], note, None);
                }
            } else if state == "stuck" || state == "waiting" {
                p["detail"] = json!(cap(if detail.is_empty() { &said } else { detail }));
                let verb = if state == "stuck" { "seems stuck" } else { "has work waiting" };
                let more = if detail.is_empty() { String::new() } else { format!(": {detail}") };
                strip.push(json!({"role": role, "state": state, "text": format!("{role} {verb}{more}")}));
            }
            if let Some(s) = s {
                let action = s["action"].as_str().unwrap_or("");
                let label = s["label"].as_str().filter(|l| !l.is_empty()).unwrap_or(match action {
                    "nudge" if state == "stuck" => "Ask it to try another way",
                    "nudge" => "Nudge",
                    "compact" => "Compact",
                    _ => "Tell it to stop retrying",
                });
                if p["detail"] == "" {
                    p["detail"] = json!(cap(&said));
                }
                let mut b = button(role, "action", label, s["message"].as_str(), "");
                b["action"] = json!(action);
                p["fix"] = fix(role, vec![b], &said, None);
            }
        }
        out.push(p);
    }
    strip.sort_by_key(|x| URGENT.iter().position(|u| x["state"] == *u));
    json!({"team": team_name, "panes": out, "strip": strip})
}

/// Carry out one button, only if it is still on the pane right now (the person may have answered in the terminal
/// meanwhile). Menus press the digit; answers and actions are sent as a message. Every press is logged.
#[allow(clippy::too_many_arguments)]
pub fn act(
    team_name: &str,
    role: &str,
    fix: &str,
    button: &str,
    view_now: &dyn Fn() -> Value,
    rt: &Runtime,
    log: Option<&Path>,
    now: Option<Timestamp>,
    by: &str,
) -> Value {
    let view = view_now();
    let p = view["panes"].as_array().and_then(|ps| ps.iter().find(|x| x["role"] == role));
    let f = p.map(|p| &p["fix"]).filter(|f| f["id"] == fix);
    let Some((p, f, b)) =
        p.zip(f).and_then(|(p, f)| Some((p, f, f["buttons"].as_array()?.iter().find(|x| x["id"] == button)?)))
    else {
        return json!({"ok": false, "detail": CHANGED});
    };
    let (kind, send, address) =
        (b["kind"].as_str().unwrap_or(""), b["send"].as_str().unwrap_or(""), p["address"].as_str().unwrap_or(""));
    match kind {
        "reply" => return json!({"ok": true, "detail": ""}), // the app opens the pane for typing
        // carry on after a pause at a daily limit; it logs its own row
        "budget" => return budget::resume(team_name, send, b["add"].as_i64().unwrap_or(0), rt, now, by, log),
        _ => {}
    }
    let result = if kind == "menu" {
        rt.keys(address, &[send]).map(|_| format!("pressed {send}"))
    } else {
        rt.send(address, send, 30.0).map(|_| "sent".into())
    };
    // a failed press is a message on the pane, never a dead brain call
    let (ok, detail) = match result {
        Ok(d) => (true, d),
        Err(e) => (false, e.message.chars().take(120).collect()),
    };
    let action = b["action"].as_str().unwrap_or(kind); // nudge, compact or tell; menu; answer
    live::record(live::Record {
        action,
        agent: role,
        reason: if b["action"].is_null() { ANSWERED } else { f["text"].as_str().unwrap_or("") },
        failed: !ok,
        detail: &detail,
        team: Some(team_name),
        by,
        address: Some(address),
        log,
        now,
        ..Default::default()
    });
    if let (true, "answer", Some(q)) = (ok, kind, f["question"].as_str()) {
        let _ = brief::add(team_name, role, q, b["label"].as_str().unwrap_or(""), "you", now);
    }
    json!({"ok": ok, "detail": detail})
}
