//! Port of `assieme/teammap.py`, owned by session B (see OWNERS.md).
//!
//! The Team tab: who wakes whom, what needs you, what could be saved and today's spend.
//!
//! Nothing is detected here. Cards come from `panes::view` (so the same advice as the command line), counts from
//! `report::aggregate` run on one team's sessions, and notes from `quiet`. Polling must stay cheap: only the brain's
//! tick reads whole logs (`Today`); `build` uses the copy it left.
use crate::prelude::*;
use crate::{activity, adapters, agenttools, budget, fixes, improve, limits, live, newteam, panes, quiet, report};
use crate::{settings, status, tasks, team};
use jiff::tz::TimeZone;
use serde_json::json;
use std::cell::RefCell;

const GROUPS_ABOVE: usize = 8; // agents; above this the map draws groups
const REFRESH_S: f64 = 120.0;
const DONE_FOR_YOU: [&str; 4] = ["nudge", "compact", "tell", "quiet"]; // fixes; what the person did is in Activity
const WORDS: [&str; 11] = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
pub const NEEDS_YOU: [&str; 3] = ["asking", "stuck", "waiting"];
pub const KIND_NAMES: [(&str, &str); 2] = [("claude", "Claude"), ("codex", "Codex")]; // the kinds whose logs carry plan limits
const DONE_ROWS: usize = 50; // fix rows sent with the map: Team overview shows 3, an agent's panel shows its own

/// A pane waiting for the person: its state, or a pause after a fix (which usually reads idle).
pub fn needs_you(p: &Value) -> bool {
    NEEDS_YOU.iter().any(|s| p["state"] == *s) || p["paused_by_fix"] == true
}

fn real(path: Option<&str>) -> Option<String> {
    path.filter(|p| !p.is_empty()).map(crate::herdr::realpath)
}

fn word(n: usize) -> String {
    WORDS.get(n).map_or(n.to_string(), |w| w.to_string())
}

fn local(t: Timestamp) -> jiff::ZonedDateTime {
    t.to_zoned_datetime(TimeZone::system())
}

fn midnight(now: Timestamp) -> Timestamp {
    local(now).start_of_day().map_or(now, |z| z.timestamp())
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v[k].as_str().unwrap_or("")
}

fn arg<'a>(p: &'a Value, k: &str) -> Result<&'a str, BrainError> {
    p[k].as_str().ok_or_else(|| BrainError::new(format!("Missing {k}.")))
}

fn objects(v: &Value, k: &str) -> Vec<Value> {
    v[k].as_array().cloned().unwrap_or_default()
}

/// Today's sessions, refreshed by the brain's tick at most once per `REFRESH_S`.
pub struct Today {
    claude_dir: PathBuf,
    codex_dir: PathBuf,
    /// (sessions, when last read, whether any read finished)
    got: Mutex<(Arc<Vec<Session>>, Option<Timestamp>, bool)>,
}

impl Today {
    fn new(claude_dir: &Path, codex_dir: &Path) -> Today {
        Today { claude_dir: claude_dir.into(), codex_dir: codex_dir.into(), got: Mutex::default() }
    }

    fn refresh(&self, now: Timestamp) {
        let mut got = self.got.lock().unwrap_or_else(|e| e.into_inner());
        if got.1.is_some_and(|at| (now - at).as_secs_f64() < REFRESH_S) {
            return;
        }
        got.1 = Some(now);
        let since = midnight(now);
        let (gemini, opencode) =
            (adapters::home().join(".gemini"), adapters::home().join(".local/share/opencode/opencode.db"));
        let mut all = vec![];
        if self.claude_dir.is_dir() {
            all.extend(adapters::claude_code(&self.claude_dir, since, false, None, None, &[]));
        }
        if self.codex_dir.is_dir() {
            all.extend(adapters::codex(&self.codex_dir, since, false));
        }
        if gemini.join("tmp").is_dir() {
            all.extend(adapters::gemini(&gemini, since, false));
        }
        if opencode.is_file() {
            all.extend(adapters::opencode(&opencode, since, false));
        }
        (got.0, got.2) = (Arc::new(all), true);
    }

    fn sessions(&self) -> Arc<Vec<Session>> {
        self.got.lock().unwrap_or_else(|e| e.into_inner()).0.clone()
    }

    pub fn ready(&self) -> bool {
        self.got.lock().unwrap_or_else(|e| e.into_inner()).2
    }
}

/// Every name a sender is tagged with: its terminal's address, its terminal's name, its role.
fn aliases(rows: &[Value]) -> BTreeMap<String, String> {
    let role =
        |r: &Value| r["labels"][team::ROLE].as_str().filter(|s| !s.is_empty()).unwrap_or(s(r, "address")).to_string();
    rows.iter()
        .flat_map(|r| {
            ["address", "name"].map(|k| (s(r, k).to_string(), role(r))).into_iter().chain([(role(r), role(r))])
        })
        .filter(|(k, _)| !k.is_empty())
        .collect()
}

/// `report::aggregate` on this team's sessions, with agents and senders named by role. A folder belongs to one role.
pub fn team_report(rows: &[Value], today: &[Session], now: Timestamp) -> Value {
    let alias = aliases(rows);
    let mut by_cwd: BTreeMap<String, &String> = BTreeMap::new();
    for r in rows {
        if let (Some(c), Some(role)) = (real(r["cwd"].as_str()), alias.get(s(r, "address"))) {
            by_cwd.entry(c).or_insert(role);
        }
    }
    let mine: Vec<Session> = today
        .iter()
        .filter_map(|x| {
            let role = by_cwd.get(&real(x.cwd.as_deref())?)?;
            let mut x = x.clone();
            x.agent = role.to_string();
            x.turns.iter_mut().for_each(|t| t.sender = alias.get(&t.sender).unwrap_or(&t.sender).clone());
            Some(x)
        })
        .collect();
    let hours = ((now - midnight(now)).as_secs_f64() / 3600.0).max(1.0);
    report::aggregate(&mine, Some(now), Some(hours), &[], &[])
}

fn edges_of(rep: &Value, roles: &BTreeSet<String>) -> Vec<Value> {
    let num = |e: &Value, k: &str| e[k].as_i64().unwrap_or(0);
    objects(rep, "edges")
        .iter()
        .filter(|e| roles.contains(s(e, "from")) && roles.contains(s(e, "to")) && e["from"] != e["to"])
        .map(|e| {
            let noisy = num(e, "last_hour") >= report::NOISY_LAST_HOUR && num(e, "empty") * 2 > num(e, "wakes");
            json!({"from": e["from"], "to": e["to"], "wakes": e["wakes"], "empty": e["empty"],
                   "noisy": noisy.then(|| json!({"per_hour": e["last_hour"]}))})
        })
        .collect()
}

/// Groups for a big team, or None. A role's own `group` wins, then a lead and those reporting to it, then the first
/// word of the name.
pub fn groups(nodes: &[Value], edges: &[Value]) -> Option<Vec<Value>> {
    if nodes.len() <= GROUPS_ABOVE {
        return None;
    }
    let parent: BTreeMap<&str, &str> = nodes.iter().map(|n| (s(n, "role"), s(n, "parent"))).collect();
    let has_children: BTreeSet<&str> = parent.values().copied().filter(|p| !p.is_empty()).collect();
    let mut leads: BTreeSet<&str> = parent
        .iter()
        .filter(|(_, p)| p.is_empty() || !parent.contains_key(*p))
        .map(|(r, _)| *r)
        .filter(|r| has_children.contains(r))
        .collect();
    if let (1, Some(&boss)) = (leads.len(), leads.iter().next()) {
        // one coordinator over several leads: each of them is a group, and it stands alone
        let under: BTreeSet<&str> =
            parent.iter().filter(|(r, p)| **p == boss && has_children.contains(*r)).map(|(r, _)| *r).collect();
        if !under.is_empty() {
            leads = under.into_iter().chain([boss]).collect();
        }
    }
    let key = |n: &Value| -> String {
        if !s(n, "group").is_empty() {
            return s(n, "group").into();
        }
        let (mut r, mut seen) = (s(n, "role"), BTreeSet::new());
        while !r.is_empty() && seen.insert(r) {
            if leads.contains(r) {
                return r.into();
            }
            r = parent.get(r).copied().unwrap_or("");
        }
        s(n, "role").split('-').next().unwrap_or("").into()
    };
    let (mut out, mut where_): (Vec<(String, Value)>, BTreeMap<&str, String>) = (vec![], BTreeMap::new());
    for n in nodes {
        let k = key(n);
        where_.insert(s(n, "role"), k.clone());
        let at = out.iter().position(|g| g.0 == k).unwrap_or_else(|| {
            out.push((k.clone(), json!({"name": k, "roles": [], "states": {}, "need_you": 0, "edges": []})));
            out.len() - 1
        });
        let g = &mut out[at].1;
        g["roles"].as_array_mut()?.push(n["role"].clone());
        g["states"][s(n, "role")] = n["state"].clone();
        g["need_you"] = json!(g["need_you"].as_i64()? + NEEDS_YOU.iter().any(|x| n["state"] == *x) as i64);
    }
    let mut summed: Vec<((String, String), Value)> = vec![];
    for e in edges {
        let (Some(a), Some(b)) = (where_.get(s(e, "from")), where_.get(s(e, "to"))) else { continue };
        if a == b {
            continue;
        }
        let at = summed.iter().position(|x| x.0 == (a.clone(), b.clone())).unwrap_or_else(|| {
            summed.push(((a.clone(), b.clone()), json!({"to": b, "wakes": 0, "noisy": null})));
            summed.len() - 1
        });
        let t = &mut summed[at].1;
        t["wakes"] = json!(t["wakes"].as_i64()? + e["wakes"].as_i64().unwrap_or(0));
        if !e["noisy"].is_null()
            && (t["noisy"].is_null() || e["noisy"]["per_hour"].as_i64() > t["noisy"]["per_hour"].as_i64())
        {
            t["noisy"] = e["noisy"].clone();
        }
    }
    for ((a, _), t) in summed {
        out.iter_mut().find(|g| g.0 == a)?.1["edges"].as_array_mut()?.push(t);
    }
    Some(out.into_iter().map(|g| g.1).collect())
}

fn subtitle(panes_: &[Value], groups: &Option<Vec<Value>>, need: usize) -> String {
    let (total, mut rest) = (panes_.len(), panes_.len() - need);
    if need == 0 {
        let working = panes_.iter().filter(|p| p["state"] == "working").count();
        rest = total - working;
        let head = match (working, rest) {
            (w, r) if w > 0 && r > 0 => format!("{} working, {} idle or done.", word(w), word(r).to_lowercase()),
            (w, _) if w > 0 => format!("{} working.", word(w)),
            (_, r) if r > 0 => format!("{} idle or done.", word(r)),
            _ => String::new(),
        };
        return format!("{head} You'll get a notification if one needs you.").trim().into();
    }
    match (groups, rest) {
        (Some(g), _) => format!("{total} agents in {} groups. The other {rest} are working or done.", g.len()),
        (_, 0) => String::new(),
        (_, 1) => "The other one is working or done.".into(),
        _ => format!("The other {} are working or done.", word(rest).to_lowercase()),
    }
}

/// Minutes since the first live agent started; a row with no start time is left out.
fn running_min(rows: &[Value], now: Timestamp) -> Value {
    let first =
        rows.iter().filter(|r| r["state"] == "live").filter_map(|r| r["created_ms"].as_i64()).filter(|m| *m != 0).min();
    first.map_or(Value::Null, |f| json!((now.as_millisecond() - f).max(0) / 60_000))
}

/// An Activity row's local day and minute, as epoch ms (rows carry no finer time).
fn row_ms(r: &Value) -> Option<i64> {
    let at = jiff::civil::DateTime::strptime("%Y-%m-%d %H:%M", format!("{} {}", s(r, "day"), s(r, "at"))).ok()?;
    Some(at.to_zoned_datetime(TimeZone::system()).ok()?.timestamp().as_millisecond())
}

/// A stopped team's last run: from its first agent's start to its last agent's stop, with the fixes and the board
/// cards finished inside it (not every card the board ever finished, nor today's fixes before it).
fn run_of(rows: &[Value], done: &[Value], team_name: &str) -> Value {
    let run = panes::current_run(rows);
    let starts: Vec<i64> = run.iter().filter_map(|r| r["created_ms"].as_i64()).filter(|m| *m != 0).collect();
    let ends: Vec<i64> = run
        .iter()
        .filter_map(|r| r["status_ms"].as_i64().filter(|m| *m != 0).or(r["created_ms"].as_i64().filter(|m| *m != 0)))
        .collect();
    let (Some(&start), Some(&end)) = (starts.iter().min(), ends.iter().max()) else { return Value::Null };
    let inside = |ms: Option<i64>| ms.is_some_and(|ms| start - 60_000 < ms && ms <= end); // rows are to the minute
    let fixed = done
        .iter()
        .filter(|r| r["by"] == "auto" && (r["outcome"] == "fixed" || r["action"] == "quiet") && inside(row_ms(r)))
        .count();
    let finished = objects(&tasks::board(team_name), "cards")
        .iter()
        .filter(|c| c["status"] == "done" && inside(c["moved_ms"].as_i64()))
        .count();
    let stopped_at = Timestamp::from_millisecond(end).map_or(String::new(), |t| local(t).strftime("%H:%M").to_string());
    json!({"minutes": ((end - start) / 60_000).max(0), "fixed": fixed, "tasks_done": finished, "stopped_at": stopped_at})
}

/// The window's data for one team.
#[allow(clippy::too_many_arguments)]
fn build(
    team_name: &str,
    rows: &[Value],
    agents: &[Agent],
    now: Timestamp,
    read: &dyn Fn(&str) -> String,
    answers: &Answers,
    today: &Today,
    st: &Value,
    found: bool,
    roster: &BTreeMap<String, Value>,
    plan_limits: &Value,
) -> Value {
    let v = panes::view(team_name, rows, agents, now, read, answers, st["show_commands"] == true);
    let alias = aliases(rows);
    let by_address: BTreeMap<&str, &Value> = rows.iter().map(|r| (s(r, "address"), r)).collect();
    let log_of = panes::matching(rows, agents);
    let log_name: BTreeMap<&str, &String> =
        log_of.iter().filter_map(|(t, a)| Some((a.name.as_str(), alias.get(t)?))).collect();
    let mut panes_ = objects(&v, "panes");
    let mut strip = objects(&v, "strip");
    let mut nodes: Vec<Value> = vec![];
    for p in &mut panes_ {
        let r = by_address.get(s(p, "address")).copied().unwrap_or(&Value::Null);
        p["reachable"] = json!(!s(p, "address").starts_with("log:"));
        p["session"] = json!(if found { "default" } else { "assieme" });
        if p["reachable"] == false && !p["fix"].is_null() {
            p["fix"]["buttons"] = json!([]);
        }
        let group = roster.get(s(p, "role")).map(|x| x["group"].clone()).unwrap_or(Value::Null);
        nodes.push(json!({"role": p["role"], "state": p["state"], "since_s": p["since_s"], "agent": p["agent"],
                          "parent": r["parent"].as_str().and_then(|x| alias.get(x)), "group": group}));
    }
    let roles: BTreeSet<String> = nodes.iter().map(|n| s(n, "role").to_string()).collect();
    let rep = team_report(rows, &today.sessions(), now);
    let edges = edges_of(&rep, &roles);
    let waits: Vec<Value> = log_of
        .values()
        .flat_map(|a| {
            a.waits_on
                .iter()
                .filter_map(|w| Some(json!({"from": log_name.get(a.name.as_str())?, "to": log_name.get(w.as_str())?})))
        })
        .collect();
    let gs = groups(&nodes, &edges);
    let tokens_of = |role: &str| rep["agents"][role]["tokens"].as_i64().unwrap_or(0);
    let total: i64 =
        rep["agents"].as_object().map_or(0, |a| a.values().map(|x| x["tokens"].as_i64().unwrap_or(0)).sum());
    let cwd: BTreeMap<&str, &str> =
        rows.iter().filter_map(|r| Some((alias.get(s(r, "address"))?.as_str(), r["cwd"].as_str()?))).collect();
    let mut tips = vec![];
    if !found {
        for x in objects(&rep, "send_less") {
            let (a, b) = (s(&x, "from"), s(&x, "to"));
            if let (true, true, Some(ca), Some(cb)) = (roles.contains(a), roles.contains(b), cwd.get(a), cwd.get(b))
                && !quiet::is_quiet(ca, cb)
            {
                tips.push(json!({"id": format!("{a}>{b}"), "from": a, "to": b, "share": x["share"], "from_cwd": ca, "to_cwd": cb}));
            }
        }
    }
    let mut bud = Value::Null;
    if !found {
        // agents found on this Mac get no limit: their messages can't be held
        let used = nodes.iter().map(|n| (s(n, "role").to_string(), tokens_of(s(n, "role")))).collect();
        bud = budget::view(team_name, &used, total, now);
        for ai in 0..objects(&bud, "asks").len() {
            let ask = bud["asks"][ai].clone();
            for (p, n) in panes_.iter_mut().zip(nodes.iter_mut()) {
                if !ask["roles"].as_array().is_some_and(|r| r.contains(&p["role"]))
                    || p["state"] == "asking"
                    || p["state"] == "exited"
                {
                    continue;
                }
                let role = s(p, "role").to_string();
                let mut b = panes::button(&role, "budget", s(&ask, "label"), ask["scope"].as_str(), "");
                b["add"] = ask["add"].clone();
                let mut f = panes::fix(&role, vec![b], s(&ask, "text"), None);
                f["budget"] = ask["scope"].clone();
                f["who"] = ask["who"].clone();
                f["detail"] = ask["detail"].clone();
                p["detail"] = json!("Paused at today’s limit");
                n["paused"] = json!(true);
                let a = &mut bud["asks"][ai];
                a["fix"] = a.get("fix").cloned().unwrap_or(f["id"].clone());
                a["role"] = a.get("role").cloned().unwrap_or(json!(role));
                a["buttons"] = a.get("buttons").cloned().unwrap_or(f["buttons"].clone());
                p["fix"] = f;
            }
        }
    }
    for (p, n) in panes_.iter_mut().zip(nodes.iter_mut()) {
        // paused after a fix that didn't work; a budget pause wins
        let role = s(p, "role").to_string();
        let card = if n["paused"] == true || p["state"] == "asking" || p["state"] == "exited" {
            None
        } else {
            fixes::pane_fix(team_name, &role, now)
        };
        p["paused_by_fix"] = json!(card.is_some());
        if let Some(card) = card {
            // after Esc it usually reads idle, yet it is waiting for the person
            (p["fix"], p["detail"], n["paused"]) = (card, json!("Paused: still stuck after a fix"), json!(true));
            strip.retain(|x| x["role"] != role.as_str());
            strip.push(
                json!({"role": role, "state": "stuck", "text": format!("{role} is paused: still stuck after a fix")}),
            );
        }
    }
    strip.sort_by_key(|x| panes::URGENT.iter().position(|u| x["state"] == *u));
    for (p, n) in panes_.iter_mut().zip(nodes.iter_mut()) {
        // at its plan's limit an agent waits for the reset: the person can do nothing about it
        let lim = &plan_limits[s(p, "agent")];
        if lim["reached"] == true
            && (p["state"] == "working" || p["state"] == "stuck")
            && p["paused_by_fix"] != true
            && p["fix"]["budget"].is_null()
        {
            let back = lim["resets_at"]
                .as_f64()
                .and_then(|t| Timestamp::from_second(t as i64).ok())
                .map(|t| local(t).strftime("%H:%M").to_string());
            let name = KIND_NAMES.iter().find(|k| p["agent"] == k.0).map_or(s(p, "agent"), |k| k.1);
            p["detail"] = json!(format!("{name} limit{}", back.map_or(String::new(), |b| format!(" · back at {b}"))));
            (p["state"], n["state"], p["fix"]) = (json!("at-limit"), json!("at-limit"), Value::Null);
            strip.retain(|x| x["role"] != p["role"]);
        }
    }
    let need = panes_.iter().filter(|p| needs_you(p)).count();
    let (mut title, mut sub) = (
        if need == 0 {
            "All quiet".to_string()
        } else {
            format!(
                "{} agent{} need{} you",
                word(need),
                if need == 1 { "" } else { "s" },
                if need == 1 { "s" } else { "" }
            )
        },
        subtitle(&panes_, &gs, need),
    );
    let asks = objects(&bud, "asks");
    if !asks.is_empty() && need == 0 {
        let who: Vec<&str> = asks.iter().map(|a| s(a, "who")).collect();
        let covered: BTreeSet<String> =
            asks.iter().flat_map(|a| a["roles"].as_array().into_iter().flatten()).map(Value::to_string).collect();
        title = "Paused at today’s limit".into();
        sub = if asks.iter().any(|a| a["scope"] == budget::TEAM) {
            "The agents stopped where they were. Nothing is lost, and nothing was closed.".into()
        } else {
            format!(
                "{} stopped where {}.{}",
                who.join(" and "),
                if asks.len() == 1 { "it was" } else { "they were" },
                if nodes.len() > covered.len() { " The others carry on." } else { "" }
            )
        };
    }
    let today_ = local(now).strftime("%Y-%m-%d").to_string();
    let every = objects(&activity::list_rows(Some(team_name), "all", None, Some(now)), "rows");
    let done: Vec<Value> =
        every.iter().filter(|r| DONE_FOR_YOU.iter().any(|a| r["action"] == *a)).take(DONE_ROWS).cloned().collect();
    let mine: Vec<&Value> = done.iter().filter(|r| s(r, "day") == today_ && r["by"] == "auto").collect();
    let fixes_today = json!({"fixed": mine.iter().filter(|r| r["outcome"] == "fixed" || r["action"] == "quiet").count(),
                             "checking": mine.iter().filter(|r| r["outcome"] == "checking").count()});
    let stopped = !rows.iter().any(|r| r["state"] == "live");
    let open_tasks = objects(&tasks::board(team_name), "cards").iter().filter(|c| c["status"] != "done").count();
    let shares: Map<String, Value> = if total == 0 {
        Map::new()
    } else {
        roles.iter().map(|r| (r.clone(), json!((100.0 * tokens_of(r) as f64 / total as f64).round() as i64))).collect()
    };
    let spend = (total != 0).then(|| {
        let waste = rep["empty_tokens"].as_f64().unwrap_or(0.0) / total as f64;
        json!({"tokens": total, "waste_share": (waste * 1e4).round() / 1e4})
    });
    let mut out = json!({
        "team": team_name, "found": found, "stopped": stopped, "fixes_today": fixes_today,
        "last_at": every.first().map_or(Value::Null, |r| r["at"].clone()),
        "running_min": if stopped { Value::Null } else { running_min(rows, now) },
        "open_tasks": open_tasks, "run": if stopped && !rows.is_empty() { run_of(rows, &done, team_name) } else { Value::Null },
        "title": title, "subtitle": sub, "budget": bud, "panes": panes_, "strip": strip, "nodes": nodes, "edges": edges,
        "waits": waits, "groups": gs, "tips": tips, "done": done, "limits": plan_limits, "shares": shares, "spend": spend,
        "spend_ready": today.ready(),
    });
    if found {
        out["run_by_assieme"] = json!(st["run_found"][team_name] == true);
    }
    out
}

fn hold_pair(team_name: &str, t: &Value, now: Option<Timestamp>, by: &str, mut extra: Map<String, Value>) -> Value {
    let label = format!("{} to {}", s(t, "from"), s(t, "to"));
    quiet::set_pair(s(t, "from_cwd"), s(t, "to_cwd"), &label, true);
    extra.insert("pair".into(), json!([t["from_cwd"], t["to_cwd"], label]));
    let reason =
        format!("{}% of today's tokens went on these wake-ups.", (t["share"].as_f64().unwrap_or(0.0) * 100.0).round());
    live::record(live::Record {
        action: "quiet",
        agent: &label,
        reason: &reason,
        team: Some(team_name),
        by,
        now,
        extra,
        ..Default::default()
    })
}

fn apply_tip(team_name: &str, tip_id: &str, built: &Value) -> Value {
    match objects(built, "tips").iter().find(|x| x["id"] == tip_id) {
        Some(t) => {
            hold_pair(team_name, t, None, "", Map::new());
            json!({"ok": true, "detail": ""})
        }
        None => json!({"ok": false, "detail": "This tip has changed. Take another look."}),
    }
}

/// The first tip of each team, held at once: it works from the next note, so it is judged fixed straight away.
fn hold_automatically(views: &BTreeMap<String, Value>, now: Timestamp) -> Vec<Value> {
    let mut out = vec![];
    for (name, built) in views.iter().filter(|(_, v)| v["found"] != true) {
        // a pair the person un-held is never held again by itself (quiet::opted_out)
        let Some(t) = objects(built, "tips").into_iter().find(|x| {
            !quiet::is_quiet(s(x, "from_cwd"), s(x, "to_cwd")) && !quiet::opted_out(s(x, "from_cwd"), s(x, "to_cwd"))
        }) else {
            continue;
        };
        let edge = objects(built, "edges")
            .into_iter()
            .find(|e| e["from"] == t["from"] && e["to"] == t["to"])
            .unwrap_or(Value::Null);
        let what = match edge["noisy"]["per_hour"].as_i64() {
            Some(n) => format!("{n} wake-ups an hour with no work"),
            None => "Repeated wake-ups with no work".into(),
        };
        let extra = Map::from_iter([
            ("title".into(), json!(format!("Held {}'s routine notes until {} was free", s(&t, "from"), s(&t, "to")))),
            ("measured".into(), json!({"what": what, "minutes": null, "tokens": null})),
        ]);
        let e = hold_pair(name, &t, Some(now), "auto", extra);
        let of = Map::from_iter([("of".into(), e["id"].clone()), ("result".into(), json!("fixed"))]);
        let outcome = live::record(live::Record {
            action: "outcome",
            agent: s(&e, "agent"),
            team: Some(name),
            by: "auto",
            now: Some(now),
            extra: of,
            ..Default::default()
        });
        out.extend([e, outcome]);
    }
    out
}

fn wakers(a: &Agent, alias: &BTreeMap<String, String>, now: Timestamp) -> Vec<Value> {
    let mut counts: BTreeMap<&str, i64> = BTreeMap::new();
    for (ts, sender) in &a.wakes {
        if (now - *ts).as_secs_f64() <= 3600.0 && sender != crate::model::HUMAN {
            *counts.entry(alias.get(sender).map_or(sender.as_str(), String::as_str)).or_default() += 1;
        }
    }
    let mut counts: Vec<_> = counts.into_iter().collect();
    counts.sort_by_key(|(r, n)| (-n, *r));
    counts.into_iter().map(|(r, n)| json!({"role": r, "count": n})).collect()
}

fn named(text: &str, alias: &BTreeMap<String, String>) -> String {
    match text.rsplit_once("Took a note from ") {
        Some((head, who)) => format!("{head}Took a note from {}", alias.get(who).map_or(who, String::as_str)),
        None => text.into(),
    }
}

fn detail_of(
    role: &str,
    built: &Value,
    agents: &[Agent],
    roster: &BTreeMap<String, Value>,
    now: Timestamp,
    rows: &[Value],
    show_commands: bool,
) -> Value {
    let Some(p) = objects(built, "panes").into_iter().find(|x| x["role"] == role) else { return Value::Null };
    let alias = aliases(rows);
    let here = rows.iter().find(|r| r["address"] == p["address"]).and_then(|r| real(r["cwd"].as_str()));
    let a = agents
        .iter()
        .filter(|a| a.cwd.is_some() && (real(a.cwd.as_deref()) == here || (here.is_none() && a.name == role)))
        .max_by_key(|a| a.last);
    let memory = a.filter(|a| a.context != 0).and_then(|a| {
        let pct = (100.0 * a.context as f64 / status::memory_limit(a)? as f64).round().min(100.0) as i64;
        Some(json!({"pct": pct, "amber": pct > 75}))
    });
    let recent = a.map_or(vec![], |a| status::recent_actions(&a.path, &a.source, show_commands, 4));
    let empty_wakes: i64 =
        objects(built, "edges").iter().filter(|e| e["to"] == role).filter_map(|e| e["empty"].as_i64()).sum();
    json!({"role": role, "cli": p["agent"], "job": roster.get(role).map_or("", |r| s(r, "job")), "state": p["state"],
           "since_s": p["since_s"], "detail": p["detail"], "fix": p["fix"], "address": p["address"],
           "reachable": p["reachable"], "session": p["session"], "command": p["command"], "memory": memory,
           "recent": recent.iter().map(|(ts, t)| json!({"at": local(*ts).strftime("%H:%M").to_string(), "text": named(t, &alias)})).collect::<Vec<_>>(),
           "wakers": a.map_or(vec![], |a| wakers(a, &alias, now)), "empty_wakes": empty_wakes})
}

/// Carry out the fixes switched to automatic on teams started in Assieme. Only buttons that are fixes: a question is
/// the person's to answer. One attempt per problem (`fixes::problem_key`), measured as it starts. `tokens` is
/// (team, role) -> (tokens now, tokens when the problem began).
fn run_automatic(
    views: &BTreeMap<String, Value>,
    st: &Value,
    rt: &Runtime,
    now: Timestamp,
    tokens: &Tokens,
) -> Vec<Value> {
    let mut out = vec![];
    let _one = fixes::LOCK.lock().unwrap_or_else(|e| e.into_inner()); // a failed try counts as the attempt too
    let mut entries = live::recent_actions(None, 500);
    for (name, built) in views {
        for p in objects(built, "panes") {
            for b in p["fix"]["buttons"].as_array().into_iter().flatten() {
                let action = s(b, "action");
                if b["kind"] != "action"
                    || st["fixes"][action] != "auto"
                    || (action == "compact" && p["memory_full"] != true)
                {
                    continue; // asking can come sooner; doing it without asking waits until memory really is full
                }
                let role = s(&p, "role");
                let started = now - jiff::SignedDuration::from_secs(p["since_s"].as_i64().unwrap_or(0));
                let key = fixes::problem_key(name, role, action, started);
                // since_s jitters by seconds between ticks, so a start near a minute edge may key a minute off
                if (-1..=1).any(|k| {
                    fixes::attempted(
                        &fixes::problem_key(name, role, action, started + jiff::SignedDuration::from_mins(k)),
                        &entries,
                    )
                }) {
                    continue;
                }
                let sent = s(b, "send");
                let (ok, detail) = match rt.send(s(&p, "address"), sent, 30.0) {
                    Ok(_) => (true, "sent".to_string()),
                    Err(e) => (false, e.message.chars().take(120).collect()), // a failed fix is a row in Activity, and the others still run
                };
                let (now_t, start_t) = tokens.get(&(name.clone(), role.to_string())).copied().unwrap_or_default();
                let mut extra = Map::from_iter([
                    ("problem".into(), json!(key)),
                    ("refused".into(), json!(fixes::refused_command(sent))),
                    ("measured".into(), fixes::measure(&p, now_t, start_t)),
                    ("check_until".into(), json!(fixes::check_until(now))),
                ]);
                if action == "nudge" {
                    extra.insert("sent".into(), json!(sent)); // a nudge's words are the same for every agent; a tell's can name a refused command
                }
                let reason = s(&p["fix"], "text");
                let entry = live::record(live::Record {
                    action,
                    agent: role,
                    reason,
                    failed: !ok,
                    detail: &detail,
                    team: Some(name),
                    by: "auto",
                    address: p["address"].as_str(),
                    now: Some(now),
                    extra,
                    ..Default::default()
                });
                entries.push(entry.clone());
                out.push(entry);
            }
        }
    }
    out
}

/// Remove Agent on a start that failed: stop its terminal if one runs and take it out of the saved team.
fn remove_agent(rt: &Runtime, team_name: &str, role: &str) -> Result<Value, BrainError> {
    let rows = rt.list(&BTreeMap::from([(team::TEAM.to_string(), team_name.to_string())]), false).unwrap_or_default();
    for r in rows.iter().filter(|r| r["state"] == "live" && r["labels"][team::ROLE] == role) {
        rt.kill(s(r, "address"))?;
    }
    let _ = newteam::forget_role(&newteam::teams_dir().join(team_name), role); // no saved folder to tidy: the agent itself has stopped
    let reason = "You removed it after it couldn't start. Its branch and files stay.";
    live::record(live::Record {
        action: "stop_agent",
        agent: role,
        reason,
        team: Some(team_name),
        ..Default::default()
    });
    Ok(json!({"ok": true, "detail": ""}))
}

/// The found team an agent belongs to: the person's choice, else its repository (a worktree counts as the project it
/// was made from), else its folder.
pub fn group_of(a: &Agent, st_groups: &Map<String, Value>) -> String {
    if let Some(chosen) =
        real(a.cwd.as_deref()).and_then(|c| st_groups.get(&c)).and_then(Value::as_str).filter(|c| !c.is_empty())
    {
        return chosen.into();
    }
    let cwd = a.cwd.as_deref().unwrap_or("").trim_end_matches('/');
    // the worktree of a team that's no longer running
    let cwd = cwd.split("/.assieme/worktrees/").next().unwrap_or(cwd);
    let repo = repository(cwd);
    Path::new(&repo).file_name().map_or("Other folders".into(), |f| f.to_string_lossy().into())
}

/// The main checkout of the repository a folder is in; the folder itself outside git. Asked once per folder: the
/// team view polls every few seconds. shortcut: a folder that later moves into another repository keeps its first
/// answer until the brain restarts.
fn repository(cwd: &str) -> String {
    static KNOWN: LazyLock<Mutex<BTreeMap<String, String>>> = LazyLock::new(Mutex::default);
    if let Some(hit) = KNOWN.lock().unwrap_or_else(|e| e.into_inner()).get(cwd) {
        return hit.clone();
    }
    let asked = (!cwd.is_empty() && Path::new(cwd).is_dir())
        .then(|| {
            std::process::Command::new("git")
                .args(["-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"])
                .output()
                .ok()
        })
        .flatten()
        .filter(|o| o.status.success())
        .map(|o| PathBuf::from(String::from_utf8_lossy(&o.stdout).trim()))
        .filter(|c| !c.as_os_str().is_empty());
    let got = match asked {
        Some(c) if c.file_name().is_some_and(|f| f == ".git") => c.parent().map(|p| p.to_string_lossy().into_owned()),
        Some(c) => Some(c.to_string_lossy().into_owned()),
        None => None,
    };
    let got = got.unwrap_or_else(|| cwd.to_string());
    KNOWN.lock().unwrap_or_else(|e| e.into_inner()).insert(cwd.into(), got.clone());
    got
}

/// Suggested answers run the person's own agent in a folder of ours: that session isn't one of their agents.
pub fn is_own_session(a: &Agent) -> bool {
    real(a.cwd.as_deref()) == real(crate::answers::default_workdir().to_str())
}

/// Teams of agents started outside Assieme, in the order they first appear. Closed sessions don't make a team.
pub fn found_groups(
    agents: &[Agent],
    st_groups: &Map<String, Value>,
    taken_cwds: &BTreeSet<String>,
) -> Vec<(String, Vec<Agent>)> {
    let taken: BTreeSet<String> = taken_cwds.iter().filter_map(|c| real(Some(c))).collect();
    let mut out: Vec<(String, Vec<Agent>)> = vec![];
    for a in agents.iter().filter(|a| {
        a.state != "exited" && !real(a.cwd.as_deref()).is_some_and(|c| taken.contains(&c)) && !is_own_session(a)
    }) {
        let g = group_of(a, st_groups);
        match out.iter_mut().find(|x| x.0 == g) {
            Some(x) => x.1.push(a.clone()),
            None => out.push((g, vec![a.clone()])),
        }
    }
    out
}

/// Agents started outside Assieme, in the shape of the core's rows. They can only be watched.
fn found_rows(name: &str, agents: &[Agent]) -> Vec<Value> {
    agents
        .iter()
        .map(|a| json!({"address": format!("log:{}", a.name), "state": if a.state == "exited" { "finished" } else { "live" },
                        "status": null, "cwd": a.cwd, "created_ms": 0, "argv": [if a.source == "codex" { "codex" } else { "claude" }],
                        "labels": {team::TEAM: name, team::ROLE: a.name}}))
        .collect()
}

/// (team, role) -> (tokens now, tokens when its problem began).
type Tokens = BTreeMap<(String, String), (Option<i64>, Option<i64>)>;
type Call = Box<dyn Fn(&Brain, &Value) -> Result<Value, BrainError> + Send + Sync>;
type Seen = BTreeMap<String, Box<dyn std::any::Any>>;

thread_local! {
    static SEEN: RefCell<Option<Seen>> = const { RefCell::new(None) };
}

/// Within `Brain::one_view`, on this thread, `read` runs once per key; outside it, every call is fresh.
fn once<T: Clone + 'static>(key: &str, read: impl FnOnce() -> T) -> T {
    let hit = SEEN.with(|s| s.borrow().as_ref().and_then(|m| m.get(key)).and_then(|b| b.downcast_ref::<T>()).cloned());
    hit.unwrap_or_else(|| {
        let got = read();
        SEEN.with(|s| s.borrow_mut().as_mut().map(|m| m.insert(key.into(), Box::new(got.clone()))));
        got
    })
}

struct EndView;

impl Drop for EndView {
    fn drop(&mut self) {
        SEEN.with(|s| *s.borrow_mut() = None);
    }
}

/// The window's data: teams, agents, their states and the fixes. One object so that `team.view`, `team.map` and the
/// buttons see the same team.
pub struct Brain {
    claude_dir: PathBuf,
    codex_dir: PathBuf,
    rt: Runtime,
    starter: Arc<Starter>,
    answers: Arc<Answers>,
    pub today: Today,
}

impl Brain {
    pub fn new(
        claude_dir: PathBuf,
        codex_dir: PathBuf,
        rt: Runtime,
        starter: Arc<Starter>,
        answers: Arc<Answers>,
    ) -> Brain {
        let today = Today::new(&claude_dir, &codex_dir);
        Brain { claude_dir, codex_dir, rt, starter, answers, today }
    }

    /// Within this, on this thread, logs and terminals are read once: a poll asks about every team, and each would read
    /// them all again. Outside it, every call is fresh, so a press always acts on what is true now.
    pub fn one_view<T>(&self, f: impl FnOnce() -> T) -> T {
        SEEN.with(|s| *s.borrow_mut() = Some(Seen::new()));
        let _end = EndView;
        f()
    }

    fn assieme_rows(&self, name: Option<&str>) -> Vec<Value> {
        let labels: BTreeMap<String, String> =
            name.map(|n| (team::TEAM.to_string(), n.to_string())).into_iter().collect();
        let rows = once(&format!("rows:{name:?}"), || self.rt.list(&labels, true).unwrap_or_default());
        rows.into_iter()
            .filter(|r| r["labels"][team::TEAM].as_str().is_some_and(|t| !t.is_empty() && name.is_none_or(|n| n == t)))
            .collect()
    }

    /// Every agent the logs show, read once per view.
    pub fn collect(&self, now: Timestamp) -> Vec<Agent> {
        once("agents", || {
            let running = once("running", status::running_cwds);
            status::collect(&self.claude_dir, &self.codex_dir, now, 12.0, false, None, running.as_ref(), None)
        })
    }

    fn found(&self, now: Timestamp) -> (Vec<Agent>, Vec<(String, Vec<Agent>)>) {
        let agents = self.collect(now);
        let taken = self
            .assieme_rows(None)
            .iter()
            .filter(|r| r["state"] == "live")
            .filter_map(|r| r["cwd"].as_str().map(String::from))
            .collect();
        let groups = found_groups(&agents, settings::load(None)["groups"].as_object().unwrap_or(&Map::new()), &taken);
        (agents, groups)
    }

    /// `(rows, agents, found)` of a team: `found` when it was started outside Assieme (they can only be watched).
    pub fn resolve(&self, name: &str, now: Timestamp) -> (Vec<Value>, Vec<Agent>, bool) {
        let rows = self.assieme_rows(Some(name));
        if rows.iter().any(|r| r["state"] == "live") {
            return (rows, self.collect(now), false);
        }
        // A stopped team and the person's own agents in that folder usually share a name: the running ones win.
        let (agents, groups) = self.found(now);
        match groups.into_iter().find(|g| g.0 == name) {
            None => (rows, agents, false),
            Some((_, group)) => (found_rows(name, &group), agents, true),
        }
    }

    fn read(&self) -> impl Fn(&str) -> String + '_ {
        // a terminal that just went away reads as empty, never as a failed poll
        |address| {
            if address.starts_with("log:") { String::new() } else { self.rt.read(address, 40).unwrap_or_default() }
        }
    }

    /// The team's data, its terminal rows and its agents. The menu bar reads all three.
    pub fn built(&self, team_name: &str, now: Timestamp) -> (Value, Vec<Value>, Vec<Agent>) {
        let (rows, agents, found) = self.resolve(team_name, now);
        let roster = newteam::roster(team_name, None);
        let built = build(
            team_name,
            &rows,
            &agents,
            now,
            &self.read(),
            &self.answers,
            &self.today,
            &settings::load(None),
            found,
            &roster,
            &self.limits(now),
        );
        (built, rows, agents)
    }

    pub fn limits(&self, now: Timestamp) -> Value {
        once("limits", || limits::read(&self.claude_dir, &self.codex_dir, now).unwrap_or_else(|_| json!({})))
    }

    /// What leaves the brain: refused-command labels can hold arguments, so only the tick reads them.
    pub fn map(&self, team_name: &str) -> Result<Value, BrainError> {
        let mut built = self.built(team_name, status::now_utc()).0;
        for p in built["panes"].as_array_mut().into_iter().flatten().filter_map(Value::as_object_mut) {
            p.remove("refused");
            p.remove("turn_at");
        }
        Ok(built)
    }

    pub fn view(&self, team_name: &str) -> Result<Value, BrainError> {
        let built = self.map(team_name)?;
        let reachable: Vec<Value> = objects(&built, "panes").into_iter().filter(|p| p["reachable"] == true).collect();
        Ok(json!({"team": team_name, "strip": built["strip"], "panes": reachable}))
    }

    pub fn act(&self, team_name: &str, role: &str, fix: &str, button: &str, by: &str) -> Result<Value, BrainError> {
        if fix == format!("fixpause:{team_name}/{role}") && button == "resume" {
            return Ok(fixes::resume(team_name, role, &self.rt, None, None, by)); // it logs its own row; a second press does nothing
        }
        let now = &|| self.map(team_name).unwrap_or_default();
        Ok(panes::act(team_name, role, fix, button, now, &self.rt, None, None, by))
    }

    pub fn detail(&self, team_name: &str, role: &str) -> Result<Value, BrainError> {
        let now = status::now_utc();
        let (rows, agents, found) = self.resolve(team_name, now);
        let (st, roster) = (settings::load(None), newteam::roster(team_name, None));
        let built = build(
            team_name,
            &rows,
            &agents,
            now,
            &self.read(),
            &self.answers,
            &self.today,
            &st,
            found,
            &roster,
            &self.limits(now),
        );
        Ok(detail_of(role, &built, &agents, &roster, now, &rows, st["show_commands"] == true))
    }

    pub fn tip(&self, team_name: &str, tip: &str) -> Result<Value, BrainError> {
        Ok(apply_tip(team_name, tip, &self.map(team_name)?))
    }

    pub fn stop(&self, team_name: &str) -> Result<Value, BrainError> {
        let n = team::stop(&self.rt, team_name)?;
        let extra = Map::from_iter([("title".into(), json!(format!("Stopped {team_name}")))]);
        let reason = format!("You stopped its {n} agent(s).");
        live::record(live::Record {
            action: "stop_team",
            agent: team_name,
            reason: &reason,
            team: Some(team_name),
            extra,
            ..Default::default()
        });
        Ok(json!({"stopped": n}))
    }

    pub fn teams(&self) -> Value {
        let (_, groups) = self.found(status::now_utc());
        let mut out: Vec<Value> = vec![];
        for r in self.assieme_rows(None) {
            let name = s(&r["labels"], team::TEAM);
            let at = out.iter().position(|t| t["name"] == name).unwrap_or_else(|| {
                out.push(json!({"name": name, "kind": "assieme", "agents": 0}));
                out.len() - 1
            });
            out[at]["agents"] = json!(out[at]["agents"].as_i64().unwrap_or(0) + (r["state"] == "live") as i64);
        }
        for (name, agents) in &groups {
            match out.iter_mut().find(|t| t["name"] == name.as_str()) {
                Some(t) if t["agents"] != 0 => {}
                Some(t) => *t = json!({"name": name, "kind": "found", "agents": agents.len()}),
                None => out.push(json!({"name": name, "kind": "found", "agents": agents.len()})),
            }
        }
        let found_agents: Vec<Value> = groups
            .iter()
            .flat_map(|(g, xs)| {
                xs.iter().map(move |a| json!({"name": a.name, "cwd": real(a.cwd.as_deref()), "group": g}))
            })
            .collect();
        json!({"teams": out.into_iter().filter(|t| t["agents"] != 0).collect::<Vec<_>>(), "found_agents": found_agents})
    }

    pub fn change_setting(&self, key: &str, value: &Value) -> Result<Value, BrainError> {
        match key.split_once('.') {
            Some(("fixes", sub)) => {
                settings::check(key, value)?;
                activity::change_fix(sub, value.as_str().unwrap_or(""), None, None)?;
            }
            Some(("practices", sub)) => {
                settings::check(key, value)?;
                activity::change_practice(sub, value == true, &self.rt, None, None)?;
            }
            _ => drop(settings::set_value(key, value, None)?),
        }
        Ok(settings::load(None))
    }

    pub fn run_found(&self, team_name: &str, on: bool) -> Result<Value, BrainError> {
        let old = settings::load(None)["run_found"][team_name] == true;
        settings::set_value(&format!("run_found.{team_name}"), &json!(on), None)?;
        let mut extra = Map::from_iter([("old".into(), json!(old)), ("new".into(), json!(on))]);
        if !on {
            extra.insert("title".into(), json!(format!("Stopped running {team_name}")));
        }
        let reason = if on { "You let Assieme run this team." } else { "You took this team back." };
        live::record(live::Record {
            action: "run_found",
            agent: team_name,
            reason,
            team: Some(team_name),
            extra,
            ..Default::default()
        });
        Ok(json!({"ok": true}))
    }

    pub fn pairs(&self, team_name: Option<&str>) -> Value {
        let now = status::now_utc();
        let rows = self.assieme_rows(None);
        let day = midnight(now);
        let today_only: Vec<Value> = live::recent_actions(None, activity::MAX_ROWS)
            .into_iter()
            .filter(|e| status::parse_ts(&e["at"]).unwrap_or(day) >= day)
            .collect();
        let mut out = vec![];
        for mut p in quiet::pairs() {
            let mut saved = Value::Null;
            let names: BTreeSet<&str> = rows
                .iter()
                .filter(|r| real(r["cwd"].as_str()).as_deref() == p["to_cwd"].as_str())
                .map(|r| s(&r["labels"], team::TEAM))
                .collect();
            for name in names {
                let mine: Vec<Value> = rows.iter().filter(|r| s(&r["labels"], team::TEAM) == name).cloned().collect();
                let by_cwd: BTreeMap<String, String> = mine
                    .iter()
                    .filter_map(|r| {
                        Some((real(r["cwd"].as_str())?, aliases(std::slice::from_ref(r)).remove(s(r, "address"))?))
                    })
                    .collect();
                let rep = team_report(&mine, &self.today.sessions(), now);
                let at = |k: &str| p[k].as_str().and_then(|c| by_cwd.get(c));
                let edge = objects(&rep, "edges").into_iter().find(|e| {
                    at("from_cwd").is_some_and(|f| e["from"] == f.as_str())
                        && at("to_cwd").is_some_and(|t| e["to"] == t.as_str())
                });
                let total: i64 =
                    rep["agents"].as_object().map_or(0, |a| a.values().filter_map(|x| x["tokens"].as_i64()).sum());
                saved = json!(quiet::savings(&p, &today_only, &edge.unwrap_or(Value::Null), total));
            }
            p["saved"] = saved;
            out.push(p);
        }
        let choices: Vec<Value> = rows
            .iter()
            .filter(|r| {
                r["state"] == "live" && team_name.is_none_or(|t| t.is_empty() || s(&r["labels"], team::TEAM) == t)
            })
            .map(|r| json!({"role": r["labels"][team::ROLE], "cwd": real(r["cwd"].as_str())}))
            .collect();
        json!({"pairs": out, "agents": choices, "held": quiet::held().values().map(Vec::len).sum::<usize>()})
    }

    pub fn set_pair(&self, from_cwd: &str, to_cwd: &str, label: &str, on: bool) -> Result<Value, BrainError> {
        quiet::set_pair(from_cwd, to_cwd, label, on);
        if on {
            let to = real(Some(to_cwd));
            let rows = self.assieme_rows(None);
            let owner =
                rows.iter().find(|r| real(r["cwd"].as_str()) == to).and_then(|r| r["labels"][team::TEAM].as_str());
            let extra = Map::from_iter([("pair".into(), json!([real(Some(from_cwd)), to, label]))]);
            live::record(live::Record {
                action: "quiet",
                agent: label,
                reason: "You added this in Settings.",
                team: owner,
                extra,
                ..Default::default()
            });
        }
        Ok(json!({"ok": true}))
    }

    pub fn set_budget(&self, team_name: &str, limits: &Value, at_limit: &str) -> Result<Value, BrainError> {
        let bud = self.map(team_name)?["budget"].take();
        if bud.is_null() {
            return Err(BrainError::new("Limits are for teams started in Assieme."));
        }
        let used = objects(&bud, "agents")
            .iter()
            .map(|a| (s(a, "role").to_string(), a["used"].as_i64().unwrap_or(0)))
            .collect();
        budget::set_limits(
            team_name,
            limits,
            at_limit,
            &self.rt,
            &used,
            bud["used"].as_i64().unwrap_or(0),
            None,
            None,
        )?;
        self.map(team_name)
    }

    /// The loop's step: fixes, budgets, notifications. Returns what it logged.
    pub fn tick(&self, now: Option<Timestamp>, announce: Option<&Notify>) -> Vec<Value> {
        let now = now.unwrap_or_else(status::now_utc);
        self.today.refresh(now);
        let st = settings::load(None);
        let (limited, paused) = (budget::teams(), fixes::paused_teams()); // a pause ends by itself even once every fix asks first
        let auto = st["fixes"].as_object().is_some_and(|f| f.values().any(|v| v == "auto"));
        if limited.is_empty() && !auto && paused.is_empty() {
            return vec![];
        }
        let names: BTreeSet<String> = self
            .assieme_rows(None)
            .iter()
            .filter(|r| r["state"] == "live")
            .map(|r| s(&r["labels"], team::TEAM).to_string())
            .collect();
        let views: BTreeMap<String, Value> = names
            .iter()
            .filter(|n| auto || limited.contains(*n) || paused.contains(*n))
            .map(|n| (n.clone(), self.built(n, now).0))
            .collect();
        for n in names.intersection(&limited) {
            budget::check(n, &views[n], &self.rt, now, announce, None);
        }
        if !auto && paused.is_empty() {
            return vec![];
        }
        let mut done =
            if auto { run_automatic(&views, &st, &self.rt, now, &self.problem_tokens(&views, now)) } else { vec![] };
        if st["fixes"]["quiet"] == "auto" {
            done.extend(hold_automatically(&views, now));
        }
        let all: Vec<Value> = views.into_values().collect();
        done.extend(fixes::check_outcomes(&all, &self.rt, None, Some(now)));
        done
    }

    /// (team, role) -> (tokens now, tokens when its problem began), for the panes that have a problem.
    fn problem_tokens(&self, views: &BTreeMap<String, Value>, now: Timestamp) -> Tokens {
        let mut out = BTreeMap::new();
        for (n, built) in views {
            let rep = team_report(&self.assieme_rows(Some(n)), &self.today.sessions(), now);
            for p in objects(built, "panes").iter().filter(|p| !p["fix"].is_null()) {
                let role = s(p, "role");
                let t = rep["agents"][role]["tokens"].as_i64();
                let began = now - jiff::SignedDuration::from_secs(p["since_s"].as_i64().unwrap_or(0));
                out.insert(
                    (n.clone(), role.to_string()),
                    (t, t.and_then(|t| fixes::start_tokens(n, role, began, Some(t), now))),
                );
            }
        }
        out
    }

    /// Today's sessions, read at most once per tick.
    pub fn today(&self) -> Arc<Vec<Session>> {
        self.today.sessions()
    }

    pub fn handlers(this: Arc<Brain>) -> Handlers {
        let mut out: Handlers = vec![];
        let mut add = |name: &'static str, f: Call| {
            let brain = this.clone();
            out.push((name, Box::new(move |p, _| f(&brain, &p))));
        };
        let teams_dir = newteam::teams_dir;
        add("team.improve", Box::new(|b, p| improve::check(b, arg(p, "team")?, None, None, None)));
        add(
            "team.improve_apply",
            Box::new(|b, p| {
                let ids: Vec<String> =
                    p["ids"].as_array().into_iter().flatten().filter_map(Value::as_str).map(String::from).collect();
                improve::apply(b, arg(p, "team")?, &ids, None, None, None)
            }),
        );
        add("team.view", Box::new(|b, p| b.view(arg(p, "team")?)));
        add("team.map", Box::new(|b, p| b.map(arg(p, "team")?)));
        add(
            "pane.act",
            Box::new(|b, p| b.act(arg(p, "team")?, arg(p, "role")?, arg(p, "fix")?, arg(p, "button")?, "you")),
        );
        add("agent.detail", Box::new(|b, p| b.detail(arg(p, "team")?, arg(p, "role")?)));
        add(
            "tools.find",
            Box::new(|_, p| Ok(agenttools::find(arg(p, "kind")?, p["project"].as_str(), p["own_copy"] != false))),
        );
        add(
            "tools.suggest",
            Box::new(|_, p| {
                Ok(agenttools::suggest(
                    arg(p, "role_kind")?,
                    arg(p, "kind")?,
                    p["project"].as_str(),
                    p["own_copy"] != false,
                ))
            }),
        );
        add(
            "agent.set_tools",
            Box::new(move |_, p| {
                let off: Vec<String> = p["tools_off"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(String::from)
                    .collect();
                let saved = agenttools::set_tools(
                    &teams_dir(),
                    arg(p, "team")?,
                    arg(p, "role")?,
                    &off,
                    p["tools_new"] == true,
                )?;
                Ok(json!({"ok": true, "id": saved["id"]}))
            }),
        );
        add(
            "agent.restart",
            Box::new(move |b, p| {
                b.starter.forget(arg(p, "team")?, arg(p, "role")?); // Try again on a failed start
                agenttools::restart(&teams_dir(), &b.rt, arg(p, "team")?, arg(p, "role")?)
            }),
        );
        add(
            "agent.remove",
            Box::new(|b, p| {
                b.starter.forget(arg(p, "team")?, arg(p, "role")?);
                remove_agent(&b.rt, arg(p, "team")?, arg(p, "role")?)
            }),
        );
        add("agent.tools", Box::new(move |_, p| agenttools::for_agent(&teams_dir(), arg(p, "team")?, arg(p, "role")?)));
        add("team.band", Box::new(|_, p| Ok(newteam::band(arg(p, "team")?, None))));
        add(
            "agent.set_musician",
            Box::new(|_, p| Ok(newteam::set_musician(arg(p, "team")?, arg(p, "role")?, arg(p, "musician")?, None)?)),
        );
        add("tip.apply", Box::new(|b, p| b.tip(arg(p, "team")?, arg(p, "tip")?)));
        add("team.add_agent", Box::new(|b, p| Ok(b.starter.add_agent(arg(p, "team")?, &p["role"])?)));
        add("team.stop", Box::new(|b, p| b.stop(arg(p, "team")?)));
        add(
            "budget.set",
            Box::new(|b, p| b.set_budget(arg(p, "team")?, &p["limits"], p["at_limit"].as_str().unwrap_or("pause"))),
        );
        add("teams.list", Box::new(|b, _| Ok(b.teams())));
        add(
            "activity.list",
            Box::new(|_, p| {
                Ok(activity::list_rows(p["team"].as_str(), p["filter"].as_str().unwrap_or("all"), None, None))
            }),
        );
        add("activity.undo", Box::new(|b, p| Ok(activity::undo(arg(p, "id")?, &b.rt, None, None))));
        add("settings.get", Box::new(|_, _| Ok(settings::load(None))));
        add("settings.set", Box::new(|b, p| b.change_setting(arg(p, "key")?, &p["value"])));
        add("quiet.pairs", Box::new(|b, p| Ok(b.pairs(p["team"].as_str()))));
        add(
            "quiet.set",
            Box::new(|b, p| {
                b.set_pair(arg(p, "from_cwd")?, arg(p, "to_cwd")?, p["label"].as_str().unwrap_or(""), p["on"] != false)
            }),
        );
        add("team.run_found", Box::new(|b, p| b.run_found(arg(p, "team")?, p["on"] == true)));
        add("groups.set", Box::new(|_, p| settings::set_value("groups", &p["groups"], None)));
        out
    }
}
