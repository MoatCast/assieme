//! Port of `assieme/budget.py`, owned by session C (see OWNERS.md).
//!
//! Daily token limits for a team and for single agents. At a limit: pause and ask (the default), or only tell.
//!
//! A pause never ends an agent. It presses the agent's own stop key if it is working, and messages sent to it through
//! Assieme wait until the person lets it carry on. Limits and pauses are kept in budgets.json in the state folder, so a
//! restarted brain still knows who is paused.
use crate::fixes::obj;
use crate::live::{self, Record};
use crate::model::parse_ts;
use crate::prelude::*;
use crate::quiet;
use serde::{Deserialize, Serialize};
use serde_json::json;

pub const TEAM: &str = "*";
const RESUME: &str = "assieme: you were paused because a daily spend limit was reached. The person has let you carry on. Continue from where you stopped.";
const GONE: &str = "This has already carried on.";
static LOCK: Mutex<()> = Mutex::new(()); // the brain serves each call on its own thread

/// One team's record. Anything damaged reads as no limits and nobody paused, never a pause nobody asked for.
#[derive(Serialize, Deserialize, Default, Clone, PartialEq)]
#[serde(default)]
struct Rec {
    limits: BTreeMap<String, i64>,
    at_limit: String,
    extra: Extra,
    paused: BTreeMap<String, Pause>,
    told: BTreeMap<String, String>,
}

/// What the person added for one day.
#[derive(Serialize, Deserialize, Default, Clone, PartialEq)]
#[serde(default)]
struct Extra {
    #[serde(skip_serializing_if = "Option::is_none")]
    day: Option<String>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    by: BTreeMap<String, i64>,
}

#[derive(Serialize, Deserialize, Default, Clone, PartialEq)]
#[serde(default)]
struct Pause {
    at: String,
    roles: BTreeMap<String, Held>,
}

#[derive(Serialize, Deserialize, Default, Clone, PartialEq)]
#[serde(default)]
struct Held {
    address: String,
    stopped: bool,
    notes: Vec<Note>,
}

#[derive(Serialize, Deserialize, Default, Clone, PartialEq)]
#[serde(default)]
struct Note {
    from: String,
    text: String,
}

fn all() -> BTreeMap<String, Value> {
    serde_json::from_value(quiet::read_json(&quiet::path(&["budgets.json"]), json!({}))).unwrap_or_default()
}

fn get(team: &str) -> Rec {
    let mut r: Rec = all().remove(team).and_then(|v| serde_json::from_value(v).ok()).unwrap_or_default();
    r.limits.retain(|_, n| *n > 0);
    if r.at_limit != "tell" {
        r.at_limit = "pause".into();
    }
    r
}

fn save(team: &str, b: &Rec) {
    let mut everything = all();
    everything.insert(team.into(), json!(b));
    let _ = quiet::write_json(&quiet::path(&["budgets.json"]), &json!(everything));
}

/// Teams the tick has to look at: one with a limit, or with someone paused.
pub fn teams() -> BTreeSet<String> {
    all().into_keys().filter(|t| !get(t).limits.is_empty() || !get(t).paused.is_empty()).collect()
}

/// 4200000 -> 4.2M, as the app writes it.
pub fn words(n: i64) -> String {
    let f = n as f64;
    if f >= 1e6 {
        format!("{:.1}", f / 1e6).trim_end_matches(".0").to_string() + "M"
    } else if f >= 1e3 {
        format!("{}K", (f / 1e3).round_ties_even()) // Python's round(): halves go to even
    } else {
        n.to_string()
    }
}

fn day(now: Timestamp) -> String {
    now.to_zoned_datetime(jiff::tz::TimeZone::system()).strftime("%Y-%m-%d").to_string()
}

fn stamp(t: Timestamp) -> String {
    t.strftime("%Y-%m-%dT%H:%M:%SZ").to_string()
}

impl Rec {
    /// The limit plus what the person added for today, or None when there is no limit.
    fn allowed(&self, scope: &str, today: &str) -> Option<i64> {
        let extra =
            if self.extra.day.as_deref() == Some(today) { self.extra.by.get(scope).copied().unwrap_or(0) } else { 0 };
        self.limits.get(scope).map(|l| l + extra)
    }
}

fn used_by(scope: &str, used: &BTreeMap<String, i64>, total: i64) -> i64 {
    if scope == TEAM { total } else { used.get(scope).copied().unwrap_or(0) }
}

fn who<'a>(team: &'a str, scope: &'a str) -> &'a str {
    if scope == TEAM { team } else { scope }
}

/// What the Team tab shows: limits, today's use, and one question for each pause. `used` is {role: tokens}.
pub fn view(team: &str, used: &BTreeMap<String, i64>, total: i64, now: Timestamp) -> Value {
    let (b, today) = (get(team), day(now));
    let asks: Vec<Value> = b
        .paused
        .iter()
        .map(|(scope, entry)| {
            let (who, u, allowed) = (who(team, scope), used_by(scope, used, total), b.allowed(scope, &today));
            let over = allowed.is_some_and(|a| u >= a);
            let quarter = (b.limits.get(scope).copied().unwrap_or(0) / 4).max(1);
            let its = if scope == TEAM { "the team’s" } else { "its" };
            let allowed = allowed.unwrap_or(0);
            let detail = if over {
                format!("{who} used {} of {its} {} tokens. ", words(u), words(allowed))
            } else {
                format!("{who} was paused at {its} limit, which no longer applies. ")
            };
            json!({"scope": scope, "who": who,
               "roles": entry.roles.iter().filter(|(_, x)| x.stopped).map(|(r, _)| r).collect::<Vec<_>>(),
               "title": format!("{who} is paused at today’s limit"),
               "text": if over { "Carry on past today’s limit?" } else { "Carry on?" },
               "detail": detail + "Messages to it wait until you decide.",
               "label": if over { format!("Add {} and carry on", words(quarter)) } else { "Carry on".into() },
               "add": if over { u - allowed + quarter } else { 0 },
               "since_s": parse_ts(&json!(entry.at)).map(|at| (now - at).as_secs().max(0))})
        })
        .collect();
    let told: Vec<Value> = b.told.iter().filter(|(s, d)| **d == today && b.limits.contains_key(*s)).map(|(s, d)| {
        json!({"scope": s, "day": d, "title": format!("{} reached today’s limit", who(team, s)),
               "body": format!("It used {} of {} tokens. It was not paused.", words(used_by(s, used, total)), words(b.allowed(s, &today).unwrap_or(0)))})
    }).collect();
    json!({"limit": b.limits.get(TEAM), "allowed": b.allowed(TEAM, &today), "used": total, "at_limit": b.at_limit,
           "agents": used.iter().map(|(r, n)| json!({"role": r, "used": n, "limit": b.limits.get(r)})).collect::<Vec<_>>(),
           "asks": asks, "told": told})
}

/// The brain's tick: pause or tell at a limit, and stop paused agents that are still working. Returns what it logged.
pub fn check(
    team: &str,
    built: &Value,
    rt: &Runtime,
    now: Timestamp,
    announce: Option<&Notify>,
    log: Option<&Path>,
) -> Vec<Value> {
    let bud = &built["budget"];
    if !crate::pyrules::truthy(bud) || built["spend_ready"] == false {
        return vec![];
    }
    let used: BTreeMap<String, i64> = bud["agents"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|a| Some((a["role"].as_str()?.to_string(), a["used"].as_i64()?)))
        .collect();
    let (total, today) = (bud["used"].as_i64().unwrap_or(0), day(now));
    let here: BTreeMap<&str, &Value> = built["panes"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|p| p["state"] != "exited")
        .filter_map(|p| Some((p["role"].as_str()?, p)))
        .collect();
    let mut out = vec![];
    let mut say = |action: &str, who: &str, title: &str, reason: &str| {
        out.push(live::record(Record {
            action,
            agent: who,
            reason,
            team: Some(team),
            by: "auto",
            log,
            now: Some(now),
            extra: obj(json!({"title": title})),
            ..Default::default()
        }));
        announce.iter().for_each(|f| f(title, reason));
    };
    let _one = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut b = get(team);
    let before = b.clone();
    b.paused.retain(|_, e| {
        // an agent that has gone is no longer paused
        e.roles.retain(|r, _| here.contains_key(r.as_str()));
        !e.roles.is_empty()
    });
    for (scope, _) in b.limits.clone() {
        let (u, who) = (used_by(&scope, &used, total), who(team, &scope).to_string());
        let allowed = b.allowed(&scope, &today).unwrap_or(i64::MAX);
        let roles: Vec<&str> = if scope == TEAM {
            here.keys().copied().collect()
        } else {
            here.keys().copied().filter(|r| *r == scope).collect()
        };
        if u < allowed || b.paused.contains_key(&scope) || roles.is_empty() {
            continue;
        }
        let spent = format!("It used {} of its {} tokens today.", words(u), words(allowed));
        if b.at_limit == "tell" {
            if b.told.get(&scope) != Some(&today) {
                b.told.insert(scope, today.clone());
                say(
                    "budget_reached",
                    &who,
                    &format!("{who} reached today’s limit"),
                    &format!("{spent} You chose to be told only."),
                );
            }
            continue;
        }
        let roles = roles
            .iter()
            .map(|r| {
                (
                    r.to_string(),
                    Held { address: here[r]["address"].as_str().unwrap_or("").into(), ..Default::default() },
                )
            })
            .collect();
        b.paused.insert(scope, Pause { at: stamp(now), roles });
        say(
            "budget_pause",
            &who,
            &format!("Paused {who} at today’s limit"),
            &format!("{spent} Nothing was closed: it carries on when you say so."),
        );
    }
    for entry in b.paused.values_mut() {
        for (role, x) in entry.roles.iter_mut() {
            let Some(p) = here.get(role.as_str()) else { continue };
            if x.stopped || p["state"] == "asking" {
                continue; // a real question from its CLI is the person's to answer first
            }
            // an agent that seems stuck is often still mid-turn
            if (crate::pyrules::truthy(&p["busy"]) || p["state"] == "working") && rt.interrupt(&x.address, 3.0).is_err()
            {
                continue; // tried again at the next tick
            }
            x.stopped = true;
        }
    }
    if b != before {
        save(team, &b);
    }
    out
}

/// Keep a message for a paused agent. False when it isn't paused, so the caller sends it as usual.
pub fn hold(team: &str, role: &str, sender: &str, text: &str) -> bool {
    let _one = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut b = get(team);
    let Some(x) = b.paused.values_mut().find_map(|e| e.roles.get_mut(role)) else { return false };
    x.notes.push(Note { from: sender.into(), text: text.into() });
    save(team, &b);
    true
}

/// Under the lock. Tells each agent of this pause to carry on, unless another limit still holds it.
#[allow(clippy::too_many_arguments)]
fn carry_on(
    team: &str,
    b: &mut Rec,
    scope: &str,
    add: i64,
    rt: &Runtime,
    now: Timestamp,
    by: &str,
    why: &str,
    log: Option<&Path>,
) -> Value {
    let entry = b.paused.remove(scope).unwrap_or_default();
    if add > 0 {
        let today = day(now);
        if b.extra.day.as_deref() != Some(&today) {
            b.extra = Extra { day: Some(today), by: BTreeMap::new() };
        }
        *b.extra.by.entry(scope.into()).or_default() += add;
    }
    let mut failed = vec![];
    for (role, x) in entry.roles {
        if let Some(other) = b.paused.values_mut().find_map(|e| e.roles.get_mut(&role)) {
            other.notes.extend(x.notes); // its own limit, or the team's, still holds it: its messages wait there
            continue;
        }
        let mut text = RESUME.to_string();
        if !x.notes.is_empty() {
            text += "\n\nMessages that arrived while you were paused:\n";
            text += &x.notes.iter().map(|n| format!("- from {}: {}", n.from, n.text)).collect::<Vec<_>>().join("\n");
        }
        if let Err(e) = rt.send(&x.address, &text, 30.0) {
            failed.push(format!("{role}: {}", e.to_string().chars().take(80).collect::<String>()));
        }
    }
    save(team, b);
    let who = who(team, scope);
    let detail = failed.join("; ");
    let reason =
        why.to_string() + &if add > 0 { format!(" Added {} tokens for today.", words(add)) } else { String::new() };
    live::record(Record {
        action: "budget_resume",
        agent: who,
        reason: &reason,
        failed: !failed.is_empty(),
        detail: &detail,
        team: Some(team),
        by,
        log,
        now: Some(now),
        extra: obj(json!({"title": format!("Let {who} carry on")})),
        ..Default::default()
    });
    json!({"ok": failed.is_empty(), "detail": detail})
}

pub fn resume(
    team: &str,
    scope: &str,
    add: i64,
    rt: &Runtime,
    now: Option<Timestamp>,
    by: &str,
    log: Option<&Path>,
) -> Value {
    let _one = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut b = get(team);
    if !b.paused.contains_key(scope) {
        return json!({"ok": false, "detail": GONE});
    }
    carry_on(team, &mut b, scope, add, rt, now.unwrap_or_else(crate::status::now_utc), by, "You let it carry on.", log)
}

/// Save the limits (tokens a day; none or 0 removes one). A pause the new limits no longer call for carries on.
#[allow(clippy::too_many_arguments)]
pub fn set_limits(
    team: &str,
    limits: &Value,
    at_limit: &str,
    rt: &Runtime,
    used: &BTreeMap<String, i64>,
    total: i64,
    now: Option<Timestamp>,
    log: Option<&Path>,
) -> Result<Value, BrainError> {
    let now = now.unwrap_or_else(crate::status::now_utc);
    if at_limit != "pause" && at_limit != "tell" {
        return Err(BrainError::new("Choose \"Pause and ask me\" or \"Just tell me\"."));
    }
    let mut clean = BTreeMap::new();
    for (scope, n) in limits.as_object().into_iter().flatten() {
        if n.is_null() || n == "" || n.as_f64() == Some(0.0) {
            continue;
        }
        match n.as_f64() {
            Some(f) if n.is_number() && f > 0.0 => clean.insert(scope.clone(), f as i64),
            _ => return Err(BrainError::new("A limit is a number of tokens above zero.")),
        };
    }
    let _one = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut b = get(team);
    let changed = (&b.limits, &b.at_limit) != (&clean, &at_limit.to_string());
    (b.limits, b.at_limit, b.told) = (clean.clone(), at_limit.into(), BTreeMap::new());
    save(team, &b);
    if changed {
        let mut parts: Vec<String> = clean
            .iter()
            .map(|(s, n)| format!("{}: {} tokens a day.", if s == TEAM { "Team" } else { s }, words(*n)))
            .collect();
        if clean.is_empty() {
            parts.push("No limits.".into());
        } else {
            parts.push(if at_limit == "pause" {
                "At a limit: pause and ask.".into()
            } else {
                "At a limit: just tell me.".into()
            });
        }
        live::record(Record {
            action: "budget_set",
            agent: team,
            reason: &parts.join(" "),
            team: Some(team),
            log,
            now: Some(now),
            extra: obj(json!({"title": format!("Changed {team}’s daily limits")})),
            ..Default::default()
        });
    }
    let today = day(now);
    for scope in b.paused.keys().cloned().collect::<Vec<_>>() {
        let allowed = b.allowed(&scope, &today);
        if at_limit == "tell" || allowed.is_none_or(|a| used_by(&scope, used, total) < a) {
            carry_on(team, &mut b, &scope, 0, rt, now, "you", "You changed the limit.", log);
        }
    }
    Ok(json!(get(team)))
}
