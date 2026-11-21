//! Port of `assieme/live.py`, owned by session A (see OWNERS.md).
//!
//! What the live view was built on: suggestions for each alert, the Activity log, and notifications. The terminal
//! view itself and `assieme watch` are not ported.
#![allow(clippy::todo, unused_variables)]
use crate::adapters::{home, secs};
use crate::prelude::*;
use crate::status::ago;
use serde_json::json;

pub const NOTIFY_MAX_AGE: i64 = 2 * 3600; // older items are shown, but never pop up a notification
pub const REFUSAL_WINDOW: i64 = 30 * 60; // two refusals of the same command within this window trigger the breaker
pub const COMPACT_AT: i64 = 150_000; // suggest compacting an idle agent whose context is at least this big

/// Append one entry to a JSONL log. A log that cannot be written must not stop the brain.
pub fn log(path: &Path, entry: &Value) {
    use std::io::Write;
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{entry}");
    }
}

/// Notify once per new item in the owner's queue and per new stall. Returns the (title, body) pairs sent.
/// `notifier`: None is [`notify`]; the menu bar passes one that shows nothing, to mark items seen. `switches`: the
/// `notify` settings (`needs_you`, `stalls`); None has both on.
///
/// Seen items are remembered in the state folder, so a restart doesn't repeat them. Only names and kinds go into a
/// notification, never message text.
pub fn announce(
    queue: &[Value],
    stalls: &[Value],
    now: Timestamp,
    notifier: Option<&Notify>,
    switches: Option<&Value>,
) -> Vec<(String, String)> {
    let store = log_path().with_file_name("notified.json");
    let mut seen = match std::fs::read(&store).ok().and_then(|b| serde_json::from_slice(&b).ok()) {
        Some(Value::Object(o)) => o,
        _ => Map::new(),
    };
    let on = |k: &str| switches.and_then(|s| s.get(k)).is_none_or(crate::pyrules::truthy);
    let mut sent: Vec<(String, String, String)> = vec![];
    for q in queue.iter().filter(|_| on("needs_you")) {
        let key = q["key"].as_str().unwrap_or("");
        let seconds = q["seconds"].as_f64().unwrap_or(0.0);
        if seen.contains_key(key) || seconds > NOTIFY_MAX_AGE as f64 {
            continue;
        }
        let blocks: Vec<&str> =
            q["blocks"].as_array().map(|b| b.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
        let held = if blocks.is_empty() { String::new() } else { format!("; holds up {}", blocks.join(", ")) };
        let body = format!("{}, waiting {}{held}", q["kind"].as_str().unwrap_or(""), ago(seconds));
        sent.push((key.into(), format!("{} needs you", q["agent"].as_str().unwrap_or("")), body));
    }
    for st in stalls.iter().filter(|_| on("stalls")) {
        let mut roots: Vec<&str> =
            st["roots"].as_array().map(|r| r.iter().filter_map(|r| r["agent"].as_str()).collect()).unwrap_or_default();
        roots.sort();
        let key = format!("stall|{}", roots.join("|"));
        if !seen.contains_key(&key) && st["quiet_seconds"].as_f64().unwrap_or(0.0) <= NOTIFY_MAX_AGE as f64 {
            let text: String = st["text"].as_str().unwrap_or("").chars().take(220).collect();
            sent.push((key, "Team stalled".into(), text));
        }
    }
    for (key, title, body) in &sent {
        let title = format!("assieme: {title}");
        match notifier {
            Some(n) => n(&title, body),
            None => notify(&title, body),
        }
        seen.insert(key.clone(), json!(isoformat(now)));
    }
    if !sent.is_empty() {
        let cutoff = isoformat(now - jiff::SignedDuration::from_hours(48));
        seen.retain(|_, v| v.as_str().is_some_and(|v| v >= cutoff.as_str()));
        if let Some(dir) = store.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(&store, Value::Object(seen).to_string());
    }
    sent.into_iter().map(|(_, t, b)| (t, b)).collect()
}

/// Python's `datetime.isoformat()` of a UTC time, as the seen store keeps it.
fn isoformat(t: Timestamp) -> String {
    let fmt = if t.subsec_nanosecond() / 1000 != 0 { "%Y-%m-%dT%H:%M:%S%.6f+00:00" } else { "%Y-%m-%dT%H:%M:%S+00:00" };
    t.strftime(fmt).to_string()
}

pub fn log_path() -> PathBuf {
    let base = std::env::var("XDG_STATE_HOME").ok().filter(|s| !s.is_empty());
    base.map(PathBuf::from).unwrap_or_else(|| home().join(".local/state")).join("assieme/actions.jsonl")
}

/// A desktop notification: macOS Notification Centre, notify-send on Linux, else the terminal bell.
pub fn notify(title: &str, body: &str) {
    use std::process::{Command, Stdio};
    let mut cmd = if cfg!(target_os = "macos") {
        let quote = |s: &str| serde_json::to_string(s).unwrap_or_default();
        let mut c = Command::new("osascript");
        c.args(["-e", &format!("display notification {} with title {}", quote(body), quote(title))]);
        c
    } else if crate::setup::agent_path("notify-send").is_some() {
        let mut c = Command::new("notify-send");
        c.args([title, body]);
        c
    } else {
        use std::io::Write;
        let _ = std::io::stdout().write_all(b"\x07");
        return;
    };
    let Ok(mut child) = cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn() else { return };
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while matches!(child.try_wait(), Ok(None)) {
        if std::time::Instant::now() > deadline {
            let _ = child.kill();
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    let _ = child.wait();
}

/// The last `n` entries of the Activity log (all of them for 0, as Python's `[-0:]`).
pub fn recent_actions(path: Option<&Path>, n: usize) -> Vec<Value> {
    let path = path.map(PathBuf::from).unwrap_or_else(log_path);
    let Ok(text) = std::fs::read_to_string(path) else { return vec![] };
    let lines = crate::adapters::text_lines(&text);
    let from = if n == 0 { 0 } else { lines.len().saturating_sub(n) };
    lines[from..].iter().filter_map(|l| crate::adapters::loads(l)).filter(Value::is_object).collect()
}

/// Twelve random hex digits, as Python's `uuid.uuid4().hex[:12]`.
fn short_id() -> String {
    use std::io::Read;
    let mut b = [0u8; 6];
    let read = std::fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut b));
    if read.is_err() {
        let n = Timestamp::now().as_nanosecond() as u64 ^ (std::process::id() as u64) << 32;
        b.copy_from_slice(&n.to_le_bytes()[..6]);
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// One Activity row. `extra` is what Undo needs (see activity.UNDO). Returns the entry. An empty `agent` is written
/// as null (Python callers passed None).
pub fn record(row: Record<'_>) -> Value {
    let by = match row.by {
        "" | "you" => match crate::serve::caller() {
            By::Assistant => "assistant", // the person asked through their assistant (step 18a)
            By::You => "you",
        },
        by => by,
    };
    let at = row.now.unwrap_or_else(crate::status::now_utc).strftime("%Y-%m-%dT%H:%M:%SZ").to_string();
    let mut entry = json!({"id": short_id(), "at": at, "action": row.action,
                           "agent": (!row.agent.is_empty()).then_some(row.agent), "address": row.address,
                           "team": row.team, "by": by, "reason": row.reason, "ok": !row.failed,
                           "detail": row.detail});
    if let Some(o) = entry.as_object_mut() {
        o.extend(row.extra);
    }
    log(&row.log.map(PathBuf::from).unwrap_or_else(log_path), &entry);
    entry
}

/// `live.record`'s keyword arguments: `Record { action, agent, reason, ..Record::default() }`.
#[derive(Default)]
pub struct Record<'a> {
    pub action: &'a str,
    pub agent: &'a str,
    pub reason: &'a str,
    /// Python's default is True.
    pub failed: bool,
    pub detail: &'a str,
    pub team: Option<&'a str>,
    /// "you" when empty.
    pub by: &'a str,
    pub address: Option<&'a str>,
    pub log: Option<&'a Path>,
    pub now: Option<Timestamp>,
    pub extra: Map<String, Value>,
}

/// The interactive loop. Returns the exit code.
pub fn run(claude_dir: &Path, codex_dir: &Path, hours: f64, use_herdr: bool) -> i32 {
    todo!("live.run")
}

/// What a suggestion holds: the alert in plain words, who it is about, and the fix (an action Assieme carries out,
/// or a hint when the fix is the person's to make).
fn suggestion(
    text: String,
    agent: &str,
    address: Option<&String>,
    action: Option<&str>,
    message: &str,
) -> Map<String, Value> {
    let v = json!({"text": text, "agent": agent, "address": address, "action": action, "message": message,
                   "hint": "", "yours": false, "pair": null, "label": ""});
    v.as_object().cloned().unwrap_or_default()
}

/// Report and status may shorten names differently; fall back to the last part of the name.
fn find<'a>(agents: &'a [Agent], name: &str) -> Option<&'a Agent> {
    if let Some(a) = agents.iter().find(|a| a.name == name) {
        return Some(a);
    }
    let tail = |n: &str| n.rsplit('-').next().unwrap_or("").rsplit(':').next().unwrap_or("").to_string();
    let near: Vec<&Agent> = agents.iter().filter(|a| tail(&a.name) == tail(name)).collect();
    if near.len() == 1 { Some(near[0]) } else { None }
}

fn nudge_text(a: &Agent) -> &'static str {
    if a.detail.starts_with("same command") {
        "assieme: you have run the same command several times in a row. Stop retrying it. Say what you are trying \
         to do, then try a different approach."
    } else if a.detail.starts_with("subagent") {
        "assieme: one of your subagents has gone silent. Check it or stop it, then carry on."
    } else if a.detail.starts_with("no reply") {
        "assieme: you have not answered for a while. Reply with your status: done, working on what, or blocked on what."
    } else {
        "assieme: no progress for a while. If a run is slow, narrow it. If you are stuck, say what is blocking you."
    }
}

/// Alerts -> suggestions, most urgent first: stall blockers, what waits on you, stuck agents, lost messages,
/// refusal loops, then oversized contexts. `done`: `[agent, action]` pairs carried out recently, kept hidden.
/// Each suggestion is `{text, agent, address, action, message, hint, yours, pair, label}`.
pub fn suggest(
    agents: &[Agent],
    stalls: &[Value],
    addr: &BTreeMap<String, String>,
    queue: &[Value],
    done: &[Value],
    now: Option<Timestamp>,
    send_less: &[Value],
) -> Vec<Value> {
    let by: BTreeMap<&str, &Agent> = agents.iter().map(|a| (a.name.as_str(), a)).collect();
    let mut seen: Vec<(Value, Value)> = done
        .iter()
        .filter_map(|d| {
            d.as_array().map(|p| (p.first().cloned().unwrap_or_default(), p.get(1).cloned().unwrap_or_default()))
        })
        .collect();
    let mut out: Vec<Value> = vec![];
    let mut add = |s: Map<String, Value>| {
        let key = (s["agent"].clone(), s["action"].clone());
        if !seen.contains(&key) {
            seen.push(key);
            out.push(Value::Object(s));
        }
    };
    let names = |v: &Value| -> Vec<String> {
        v.as_array().map(|a| a.iter().map(crate::adapters::py_str).collect()).unwrap_or_default()
    };

    for st in stalls {
        for root in st["roots"].as_array().into_iter().flatten() {
            let Some(r) = root["agent"].as_str().and_then(|n| by.get(n)) else { continue };
            let waiting = names(&root["waiting"]);
            if r.state == "idle" && r.waits_on.is_empty() && !waiting.is_empty() {
                let list = waiting.join(", ");
                add(suggestion(
                    format!("{} is idle while {} agent(s) wait on it ({list})", r.name, waiting.len()),
                    &r.name,
                    addr.get(&r.name),
                    Some("nudge"),
                    &format!("assieme: {list} are waiting on you. Check your messages and reply to them."),
                ));
            }
        }
    }
    let fallback: Vec<Value>;
    let queue = if queue.is_empty() {
        fallback = agents
            .iter()
            .filter(|a| a.state == "waiting on you")
            .map(|a| json!({"agent": a.name, "kind": a.detail, "seconds": null, "blocks": []}))
            .collect();
        &fallback[..]
    } else {
        queue
    };
    for q in queue {
        let agent = crate::adapters::py_str(&q["agent"]);
        let place = addr.get(&agent).map_or("its terminal".into(), |a| format!("hc attach {a}"));
        let waited = q["seconds"].as_f64().map_or(String::new(), |s| format!(", waiting {}", ago(s)));
        let blocks = names(&q["blocks"]);
        let held = if blocks.is_empty() { String::new() } else { format!("; holds up {}", blocks.join(", ")) };
        let mut s = suggestion(
            format!("{agent}: {}{waited}{held}", crate::adapters::py_str(&q["kind"])),
            &agent,
            addr.get(&agent),
            None,
            "",
        );
        s.insert("hint".into(), json!(format!("answer it in {place}")));
        s.insert("yours".into(), json!(true));
        add(s);
    }
    for a in agents.iter().filter(|a| a.state == "stuck") {
        add(suggestion(
            format!("{} is stuck: {}", a.name, a.detail),
            &a.name,
            addr.get(&a.name),
            Some("nudge"),
            nudge_text(a),
        ));
    }
    for a in agents.iter().filter(|a| a.state == "idle, work waiting") {
        add(suggestion(
            format!("{} has work waiting: {}", a.name, a.detail),
            &a.name,
            addr.get(&a.name),
            Some("nudge"),
            "assieme: a message for you may not have started a turn. Check your latest messages and act on them.",
        ));
    }
    let now = now.unwrap_or_else(crate::status::now_utc);
    for a in agents.iter().filter(|a| a.state != "exited") {
        // A5: the second refusal of the same command is the moment to stop the loop
        type Refusal<'a> = ((&'a str, &'a str), (usize, &'a str)); // (label, why) -> (count, advice)
        let mut recent: Vec<Refusal> = vec![];
        for (ts, label, why, advice) in &a.refused {
            if secs(now, *ts) <= REFUSAL_WINDOW as f64 {
                match recent.iter_mut().find(|(k, _)| *k == (label.as_str(), why.as_str())) {
                    Some((_, v)) => *v = (v.0 + 1, advice),
                    None => recent.push(((label, why), (1, advice))),
                }
            }
        }
        if let Some(((label, why), (n, advice))) = recent.into_iter().find(|(_, (n, _))| *n >= 2) {
            let fix = if advice.is_empty() { String::new() } else { format!(". Rule fix: {advice}") };
            add(suggestion(
                format!("{}: `{label}` refused {n} times by {why}{fix}", a.name),
                &a.name,
                addr.get(&a.name),
                Some("tell"),
                &format!(
                    "assieme: `{label}` has been refused {n} times by {why}. Do not retry it. Do the task another \
                     way, or ask the owner if you really need it."
                ),
            ));
        }
    }
    let warnings = crate::status::warnings(agents, now);
    for w in &warnings {
        // F6, F7: told once per agent, after any refusal loop; F10 is the owner's to fix
        let agent = w["agent"].as_str().unwrap_or("");
        let what = w["what"].as_str().unwrap_or("").to_string();
        if let Some(message) = w["message"].as_str().filter(|m| !m.is_empty()) {
            let mut s = suggestion(what, agent, addr.get(agent), Some("tell"), message);
            s.insert("label".into(), w["label"].clone());
            add(s);
        } else if w["kind"] == "box" {
            let mut s = suggestion(what, agent, None, None, "");
            s.insert("hint".into(), w["fix"].clone());
            s.insert("yours".into(), json!(true));
            add(s);
        }
    }
    let growing: BTreeMap<&str, i64> = warnings
        .iter()
        .filter(|w| w["kind"] == "growth")
        .filter_map(|w| Some((w["agent"].as_str()?, w["per_hour"].as_i64()?)))
        .collect();
    for a in agents {
        // A2: an idle team agent with a big context re-reads all of it on every wake; a one-off session won't be woken
        if a.state == "idle" && a.context >= COMPACT_AT && !a.peers.is_empty() {
            let pace = growing
                .get(a.name.as_str())
                .map_or(String::new(), |r| format!(" and is growing {}K an hour", r.div_euclid(1000)));
            add(suggestion(
                format!("{} holds {}K tokens of context{pace}; every wake re-reads it", a.name, a.context / 1000),
                &a.name,
                addr.get(&a.name),
                Some("compact"),
                "/compact",
            ));
        }
    }
    for x in send_less {
        // A3: the costliest empty pairs can be made quiet with one key
        let (from, to) = (x["from"].as_str().unwrap_or(""), x["to"].as_str().unwrap_or(""));
        let (Some(f), Some(t)) = (find(agents, from), find(agents, to)) else { continue };
        let (Some(fc), Some(tc)) =
            (f.cwd.as_deref().filter(|c| !c.is_empty()), t.cwd.as_deref().filter(|c| !c.is_empty()))
        else {
            continue;
        };
        if crate::quiet::is_quiet(fc, tc) {
            continue;
        }
        let tokens = x["empty_tokens"].as_f64().unwrap_or(0.0) / 1e6;
        let mut s = suggestion(
            format!("{from} -> {to}: {} of {} wakes did no work ({tokens:.0}M tokens in 24 h)", x["empty"], x["wakes"]),
            &f.name,
            None,
            Some("quiet"),
            "",
        );
        s.insert("pair".into(), json!([fc, tc, format!("{from} -> {to}")]));
        add(s);
    }
    out
}

/// The Activity row for notes held for an agent's next turn and now delivered. `notes` are quiet's held notes.
pub fn record_delivery(notes: &[Value], why: &str, now: Option<Timestamp>) -> Option<Value> {
    let last = notes.last()?;
    let mut senders = Map::new();
    for n in notes {
        let from = crate::adapters::py_str(&n["from_cwd"]);
        let count = senders.get(&from).and_then(Value::as_i64).unwrap_or(0);
        senders.insert(from, json!(count + 1));
    }
    let to = crate::adapters::py_str(&last["to"]);
    let role = last.get("role").filter(|r| crate::pyrules::truthy(r)).map_or(to.clone(), crate::adapters::py_str);
    let mut extra = Map::new();
    extra.insert("to_cwd".into(), last["to_cwd"].clone());
    extra.insert("from".into(), Value::Object(senders));
    let reason = format!("{} note(s) held for its next turn.", notes.len());
    Some(record(Record {
        action: "deliver",
        agent: &role,
        reason: &reason,
        detail: why,
        by: "auto",
        team: last["team"].as_str(),
        address: Some(&to),
        now,
        extra,
        ..Record::default()
    }))
}

/// The Activity row for held notes dropped after a day because their agent has stopped.
pub fn record_given_up(notes: &[Value], now: Option<Timestamp>) -> Option<Value> {
    let last = notes.last()?;
    let to = crate::adapters::py_str(&last["to"]);
    let role = last.get("role").filter(|r| crate::pyrules::truthy(r)).map_or(to.clone(), crate::adapters::py_str);
    let reason = format!("{} note(s) waited a day, but it has stopped.", notes.len());
    Some(record(Record {
        action: "dropped",
        agent: &role,
        reason: &reason,
        failed: true,
        detail: "It has stopped.",
        by: "auto",
        team: last["team"].as_str(),
        address: Some(&to),
        now,
        ..Record::default()
    }))
}

/// `assieme watch`. Returns the exit code.
pub fn watch(claude_dir: &Path, codex_dir: &Path, hours: f64, use_herdr: bool) -> i32 {
    todo!("live.watch")
}

/// One round of `assieme watch`. Returns what it did, one line each.
pub fn watch_once(claude_dir: &Path, codex_dir: &Path, hours: f64, use_herdr: bool, announcing: bool) -> Vec<String> {
    todo!("live.watch_once")
}
