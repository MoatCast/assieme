//! Port of `assieme/fixes.py`, owned by session C (see OWNERS.md).
//!
//! Automatic fixes: one attempt per problem, and the problem measured when it is attempted.
use crate::live::{self, Record};
use crate::model::parse_ts;
use crate::prelude::*;
use crate::pyrules::truthy;
use crate::quiet;
use serde_json::json;

pub const CHECK_S: i64 = 600;
const RESUME: &str = "assieme: the person has let you carry on. Assieme paused you because the same problem continued after its suggestion; try a different approach.";
const PAUSED: &str = "No progress for 10 min after Assieme asked it to try another way.";
const GONE: &str = "This has already carried on.";

/// One fix decision at a time across the brain.
pub static LOCK: Mutex<()> = Mutex::new(());

fn lock() -> std::sync::MutexGuard<'static, ()> {
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

pub(crate) fn obj(v: Value) -> Map<String, Value> {
    v.as_object().cloned().unwrap_or_default()
}

fn stamp(t: Timestamp) -> String {
    t.strftime("%Y-%m-%dT%H:%M:%SZ").to_string()
}

fn state() -> Value {
    let s = quiet::read_json(&quiet::path(&["fixes.json"]), json!({}));
    if s.is_object() { s } else { json!({}) }
}

fn save(state: &Value) {
    let _ = quiet::write_json(&quiet::path(&["fixes.json"]), state);
}

/// team/role -> {at, of, address, stopped, notes, measured}. Damaged reads as nobody paused.
fn paused(state: &mut Value) -> &mut Map<String, Value> {
    if !state["paused"].is_object() {
        state["paused"] = json!({});
    }
    state["paused"].as_object_mut().expect("set above")
}

fn pane<'a>(views: &'a [Value], team: &str, role: &str) -> Option<&'a Value> {
    let view = views.iter().find(|v| v["team"] == team)?;
    view["panes"].as_array()?.iter().find(|p| p["role"] == role)
}

/// Presses the agent's stop key once; it is never pressed again for this pause.
#[allow(clippy::too_many_arguments)]
fn pause(
    p: &mut Map<String, Value>,
    team: &str,
    role: &str,
    x: (&str, &str, &Value, bool),
    rt: &Runtime,
    now: Timestamp,
    log: Option<&Path>,
) {
    let (address, of, measured, press) = x;
    if p.contains_key(&format!("{team}/{role}")) {
        return;
    }
    let stopped = press && rt.interrupt(address, 3.0).is_ok(); // not retried: Resume, or the person typing in its terminal, ends the pause
    p.insert(
        format!("{team}/{role}"),
        json!({"at": stamp(now), "of": of, "address": address, "stopped": stopped, "notes": [], "measured": measured}),
    );
    live::record(Record {
        action: "fix_pause",
        agent: role,
        reason: PAUSED,
        team: Some(team),
        by: "auto",
        address: Some(address),
        log,
        now: Some(now),
        extra: obj(json!({"title": format!("Paused {role}: still stuck after a fix")})),
        ..Default::default()
    });
}

pub fn paused_teams() -> BTreeSet<String> {
    paused(&mut state()).keys().map(|k| k.split('/').next().unwrap_or("").to_string()).collect()
}

/// Keep a message for an agent paused after a fix. False when it isn't paused.
pub fn hold(team: &str, role: &str, sender: &str, text: &str) -> bool {
    let _one = lock();
    let mut st = state();
    let Some(x) = paused(&mut st).get_mut(&format!("{team}/{role}")).filter(|x| x.is_object()) else { return false };
    if !x["notes"].is_array() {
        x["notes"] = json!([]);
    }
    x["notes"].as_array_mut().expect("set above").push(json!({"from": sender, "text": text}));
    save(&st);
    true
}

/// The messages that waited, as one text to send, or none.
fn held(x: &Value) -> Vec<String> {
    let notes: Vec<String> = x["notes"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|n| format!("- from {}: {}", n["from"].as_str().unwrap_or(""), n["text"].as_str().unwrap_or("")))
        .collect();
    if notes.is_empty() {
        vec![]
    } else {
        vec![format!("Messages that arrived while you were paused:\n{}", notes.join("\n"))]
    }
}

fn send_all(rt: &Runtime, x: &Value, texts: &[String]) -> (bool, String) {
    let address = x["address"].as_str().unwrap_or("");
    match texts.iter().try_for_each(|t| rt.send(address, t, 30.0).map(drop)) {
        Ok(()) => (true, String::new()),
        Err(e) => (false, e.to_string().chars().take(120).collect()),
    }
}

/// Let it carry on: RESUME, then the messages held for it.
pub fn resume(team: &str, role: &str, rt: &Runtime, log: Option<&Path>, now: Option<Timestamp>, by: &str) -> Value {
    let now = now.unwrap_or_else(crate::status::now_utc);
    let x = {
        let _one = lock();
        let mut st = state();
        let Some(x) = paused(&mut st).remove(&format!("{team}/{role}")).filter(|x| x.is_object()) else {
            return json!({"ok": false, "detail": GONE});
        };
        save(&st);
        x
    };
    let (ok, detail) = send_all(rt, &x, &[vec![RESUME.to_string()], held(&x)].concat());
    live::record(Record {
        action: "fix_resume",
        agent: role,
        reason: "You let it carry on.",
        failed: !ok,
        detail: &detail,
        team: Some(team),
        by,
        address: x["address"].as_str(),
        log,
        now: Some(now),
        extra: obj(json!({"title": format!("Let {role} carry on")})),
    });
    json!({"ok": ok, "detail": detail})
}

/// The Needs-you card of an agent paused after a fix, or None.
pub fn pane_fix(team: &str, role: &str, _now: Timestamp) -> Option<Value> {
    let st = state();
    let x = st["paused"].get(format!("{team}/{role}")).filter(|x| x.is_object())?;
    let at =
        parse_ts(&x["at"]).map(|t| t.to_zoned_datetime(jiff::tz::TimeZone::system()).strftime("%H:%M").to_string());
    let m = &x["measured"];
    let detail = if m.is_object() {
        let what = m["what"]
            .as_str()
            .filter(|w| !w.is_empty())
            .map_or_else(|| format!("no progress for {} min", m["minutes"]), String::from);
        let tokens = m["tokens"]
            .as_i64()
            .filter(|t| *t != 0)
            .map(|t| format!(" {} tokens used on it.", crate::budget::words(t)));
        format!("Before the fix: {what}.{}", tokens.unwrap_or_default())
    } else {
        String::new()
    };
    Some(
        json!({"id": format!("fixpause:{team}/{role}"), "pause": {"at": at.unwrap_or_default(), "of": x["of"]}, "text": PAUSED,
        "detail": detail, "question": null,
        "buttons": [{"id": "hint", "label": "Give it a hint", "kind": "reply"}, {"id": "resume", "label": "Resume as is", "kind": "action"}]}),
    )
}

/// Minute precision, so every poll of one problem agrees.
pub fn problem_key(team: &str, role: &str, action: &str, started: Timestamp) -> String {
    format!("{team}/{role}/{action}/{}", started.strftime("%Y%m%dT%H%M"))
}

pub fn attempted(key: &str, entries: &[Value]) -> bool {
    entries.iter().any(|e| e["problem"] == key)
}

pub fn measure(pane: &Value, tokens_now: Option<i64>, tokens_at_start: Option<i64>) -> Value {
    let spent = tokens_now.zip(tokens_at_start).map(|(a, b)| a - b);
    json!({"minutes": (pane["since_s"].as_f64().unwrap_or(0.0) / 60.0).floor() as i64,
           "what": pane["detail"].as_str().unwrap_or(""), "tokens": spent})
}

/// The token count when this problem began, remembered the first tick it was seen. A problem that began after the
/// remembered one was seen is a new one.
pub fn start_tokens(
    team: &str,
    role: &str,
    started: Timestamp,
    tokens_now: Option<i64>,
    now: Timestamp,
) -> Option<i64> {
    let _one = lock();
    let mut st = state();
    if !st["starts"].is_object() {
        st["starts"] = json!({});
    }
    let k = format!("{team}/{role}");
    if parse_ts(&st["starts"][&k]["at"]).is_none_or(|seen| started > seen) {
        st["starts"][&k] = json!({"at": stamp(now), "tokens": tokens_now});
        save(&st);
    }
    st["starts"][&k]["tokens"].as_i64()
}

/// `%Y-%m-%dT%H:%M:%SZ`, CHECK_S after `now`.
pub fn check_until(now: Timestamp) -> String {
    stamp(now + jiff::SignedDuration::from_secs(CHECK_S))
}

/// The person made a paused agent go on: a turn began after the pause. The hook alone can't say so, as Claude Code's
/// Stop hook doesn't run on Esc and may still read working. An agent never pressed (it was asking) has no stale hook:
/// working is enough, as answering a permission prompt continues its turn rather than starting one.
fn carried_on(pane: &Value, x: &Value) -> bool {
    parse_ts(&pane["turn_at"]).zip(parse_ts(&x["at"])).is_some_and(|(turn, at)| turn > at)
        || (!truthy(&x["stopped"]) && pane["state"] == "working")
}

/// Judge each automatic fix not yet judged, once, and log an `outcome` entry. Everything is read from the log, so a
/// restart forgets nothing. A fix whose agent has exited gets none, and so does one whose send failed.
/// A nudge that ends still stuck pauses its agent; a pause ends when the agent exits or the person makes it work.
pub fn check_outcomes(views: &[Value], rt: &Runtime, log: Option<&Path>, now: Option<Timestamp>) -> Vec<Value> {
    let now = now.unwrap_or_else(crate::status::now_utc);
    let (mut out, mut carried) = (vec![], vec![]);
    {
        let _one = lock();
        let mut st = state();
        let before = st.clone();
        let p = paused(&mut st);
        let entries = live::recent_actions(log, 500);
        let done: BTreeSet<&str> =
            entries.iter().filter(|e| e["action"] == "outcome").filter_map(|e| e["of"].as_str()).collect();
        for e in &entries {
            let Some(until) = parse_ts(&e["check_until"]) else { continue };
            let id = e["id"].as_str().unwrap_or("");
            if e["by"] != "auto" || !truthy(&e["problem"]) || done.contains(id) || e["ok"] == false {
                continue;
            }
            let (team, agent) = (e["team"].as_str().unwrap_or(""), e["agent"].as_str().unwrap_or(""));
            let Some(pane) = pane(views, team, agent).filter(|p| p["state"] != "exited") else { continue };
            let action = e["action"].as_str().unwrap_or("");
            let fixed = match action {
                "nudge" => pane["state"] == "working",
                "compact" => !truthy(&pane["memory_full"]),
                _ => {
                    // tell: still stuck once the command it was about is refused after the fix, fixed if not by the time is up
                    let Some(at) = parse_ts(&e["at"]).filter(|_| truthy(&e["refused"])) else { continue }; // an older entry, or a warning tell
                    let again = pane["refused"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .any(|r| r[1] == e["refused"] && parse_ts(&r[0]).is_some_and(|t| t > at));
                    if !again && now < until {
                        continue;
                    }
                    !again
                }
            };
            if !fixed && now < until {
                continue;
            }
            out.push(live::record(Record {
                action: "outcome",
                agent,
                team: Some(team),
                by: "auto",
                log,
                now: Some(now),
                extra: obj(json!({"of": id, "result": if fixed { "fixed" } else { "still_stuck" }})),
                ..Default::default()
            }));
            if action == "nudge" && !fixed {
                // a real question on screen: Esc would refuse it, so it is not pressed
                let address = pane["address"].as_str().unwrap_or("");
                pause(p, team, agent, (address, id, &e["measured"], pane["state"] != "asking"), rt, now, log);
            }
        }
        for k in p.keys().cloned().collect::<Vec<_>>() {
            let (team, role) = k.split_once('/').unwrap_or((&k, ""));
            if !views.iter().any(|v| v["team"] == team) {
                continue; // not looked at this tick
            }
            let x = p[&k].clone();
            match pane(views, team, role).filter(|q| q["state"] != "exited" && q["address"] == x["address"]) {
                None => {
                    p.remove(&k); // it exited, or a new agent has its name
                    let n = x["notes"].as_array().map_or(0, Vec::len);
                    if n > 0 {
                        live::record(Record {
                            action: "fix_dropped",
                            agent: role,
                            reason: "It stopped while paused.",
                            team: Some(team),
                            by: "auto",
                            log,
                            now: Some(now),
                            extra: obj(
                                json!({"title": format!("Did not deliver {n} message{} to {role}", if n == 1 { "" } else { "s" })}),
                            ),
                            ..Default::default()
                        });
                    }
                }
                Some(q) if carried_on(q, &x) => {
                    p.remove(&k);
                    carried.push((team.to_string(), role.to_string(), x));
                }
                Some(_) => {}
            }
        }
        if st != before {
            save(&st);
        }
    }
    for (team, role, x) in carried {
        // sent after the lock, as resume does
        let (ok, detail) = send_all(rt, &x, &held(&x));
        live::record(Record {
            action: "fix_resume",
            agent: &role,
            reason: "It started working again.",
            failed: !ok,
            detail: &detail,
            team: Some(&team),
            by: "auto",
            address: x["address"].as_str(),
            log,
            now: Some(now),
            extra: obj(json!({"title": format!("Let {role} carry on")})),
        });
    }
    out
}

/// The command a refusal-loop tell is about; None for any other tell.
pub fn refused_command(send: &str) -> Option<String> {
    static RE: LazyLock<regex::Regex> =
        LazyLock::new(|| regex::Regex::new(r"^assieme: `(.+)` has been refused \d+ times").expect("fixed pattern"));
    RE.captures(send).map(|m| m[1].to_string())
}
