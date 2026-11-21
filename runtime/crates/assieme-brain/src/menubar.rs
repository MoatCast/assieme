//! Port of `assieme/menubar.py`, owned by session B (see OWNERS.md).
//!
//! The menu bar panel and Mac notifications: what the icon counts, what its panel shows, what should pop up.
//!
//! Nothing is detected here. Teams and buttons come from `Brain` (the same cards as the Team tab), finished work
//! from `Review::waiting`, stalls from `status`. The app only draws what this returns.
use crate::prelude::*;
use crate::teammap::{KIND_NAMES, NEEDS_YOU, needs_you};
use crate::{live, panes, quiet, review, settings, status};
use regex::Regex;
use serde_json::json;
use std::fmt::Write as _;

const PAUSE_S: i64 = 3600;
const ANSWER_WAIT_S: i64 = 30; // a plain-word question waits this long for its suggested answers, so they're on the notification
const TESTS_WAIT_S: f64 = 120.0; // finished work waits this long for its tests, so the notification can say how they went
const FORGET_S: i64 = 600; // a key is forgotten this long after it stops being true: a flicker doesn't notify twice
const APP_GONE_S: f64 = 60.0; // without a poll for this long the brain's plain notifications take over again
const MAX_BODY: usize = 200;
const MAX_LABEL: usize = 40; // a menu item, not a paragraph: Claude Code's menu choices can be long
const YOURS: [&str; 3] = ["merge", "discard", "stop_team"]; // you ended this agent: no need to tell you

type Teams = Vec<(String, Value, BTreeMap<String, Agent>)>;
type Events = Vec<(String, Value)>;

fn short(text: &str, limit: usize) -> String {
    let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    match text.chars().count() > limit {
        true => format!("{}…", text.chars().take(limit - 1).collect::<String>().trim_end()),
        false => text,
    }
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v[k].as_str().unwrap_or("")
}

fn says(state: &str) -> &'static str {
    match state {
        "stuck" => "seems stuck",
        "waiting" => "has work waiting",
        _ => "",
    }
}

/// An event for the app to show. `view`: where Open goes.
#[allow(clippy::too_many_arguments)]
fn event(
    kind: &str,
    team: Option<&str>,
    role: Option<&str>,
    view: Option<&str>,
    fix: &Value,
    title: &str,
    body: &str,
    buttons: Vec<Value>,
) -> Value {
    json!({"kind": kind, "team": team, "role": role, "view": view, "fix": fix, "title": title, "body": short(body, MAX_BODY), "buttons": buttons})
}

/// Notification buttons. The first is always Open, so a slip on the Options menu can never answer an agent.
fn labels(buttons: &[Value]) -> Vec<Value> {
    let mut out = vec![json!({"id": "open", "label": "Open"})];
    let mut seen = BTreeSet::from(["Open".to_string()]);
    for b in buttons.iter().filter(|b| b["kind"] != "reply") {
        let label = short(s(b, "label"), MAX_LABEL);
        if seen.insert(label.clone()) {
            out.push(json!({"id": b["id"], "label": label}));
        }
    }
    out
}

fn panes_of(built: &Value) -> impl Iterator<Item = &Value> {
    built["panes"].as_array().into_iter().flatten()
}

/// Pauses at a daily limit that can be answered now: at least one of their agents has stopped.
fn asks(built: &Value) -> Vec<&Value> {
    let listed: BTreeSet<&str> = panes_of(built).filter(|p| needs_you(p)).map(|p| s(p, "role")).collect(); // those already count, with the same button
    built["budget"]["asks"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|a| !a["fix"].is_null() && !listed.contains(s(a, "role")))
        .collect()
}

fn plain(buttons: &Value) -> Vec<Value> {
    buttons
        .as_array()
        .into_iter()
        .flatten()
        .map(|b| json!({"id": b["id"], "label": b["label"], "kind": b["kind"]}))
        .collect()
}

/// When a pause ends, or None. A damaged or finished pause is no pause.
pub fn pause_until() -> Option<Timestamp> {
    status::parse_ts(&settings::load(None)["notify_paused_until"]).filter(|at| *at > status::now_utc())
}

/// The menu bar's data and the app's notifications.
pub struct Menubar {
    brain: Arc<Brain>,
    reviews: Arc<Review>,
    store: PathBuf,
    lock: Mutex<()>,
    polled: Mutex<Option<Timestamp>>,
}

impl Menubar {
    pub fn new(brain: Arc<Brain>, reviews: Arc<Review>) -> Menubar {
        Menubar {
            brain,
            reviews,
            store: quiet::state_dir().join("notified-app.json"),
            lock: Mutex::default(),
            polled: Mutex::default(),
        }
    }

    fn load(&self) -> Map<String, Value> {
        quiet::read_json(&self.store, json!({})).as_object().cloned().unwrap_or_default()
    }

    /// What the agent said last, from the transcript both CLIs keep, read again only when it changes.
    fn last_words(&self, a: &Agent) -> String {
        let said = if a.source == "codex" {
            self.reviews.read_once(&a.path, review::codex_said).1
        } else {
            self.reviews.read_once(&a.path, review::claude_said)
        };
        said.last().cloned().unwrap_or_default()
    }

    /// (name, built, agent by address) for every team the Team tab lists.
    fn teams(&self, now: Timestamp) -> Teams {
        let names: Vec<String> =
            self.brain.teams()["teams"].as_array().into_iter().flatten().map(|t| s(t, "name").to_string()).collect();
        names
            .into_iter()
            .map(|name| {
                let (built, rows, agents) = self.brain.built(&name, now);
                (name, built, panes::matching(&rows, &agents))
            })
            .collect()
    }

    fn panel(&self, teams: &Teams, st: &Value) -> Value {
        let mut shown: Vec<Value> = vec![];
        for (name, built, _) in teams {
            let mut items: Vec<Value> = panes_of(built)
                .filter(|p| needs_you(p))
                .map(|p| {
                    let asking = p["state"] == "asking" || p["paused_by_fix"] == true;
                    let text = Some(s(&p["fix"], "text")).filter(|t| !t.is_empty()).unwrap_or(s(p, "detail"));
                    json!({"role": p["role"], "state": p["state"], "since_s": p["since_s"], "text": if asking { short(text, MAX_BODY) } else { String::new() },
                           "says": says(s(p, "state")), "fix": p["fix"]["id"], "buttons": plain(&p["fix"]["buttons"])})
                })
                .collect();
            items.extend(asks(built).iter().map(|a| {
                json!({"role": a["role"], "state": "asking", "since_s": a["since_s"], "text": short(s(a, "title"), MAX_BODY), "says": "",
                       "fix": a["fix"], "buttons": plain(&a["buttons"])})
            }));
            shown.push(json!({"name": name, "where": "This Mac", "need": items.len(), "items": items}));
        }
        shown.sort_by_key(|t| -t["need"].as_i64().unwrap_or(0)); // stable: what needs you first, otherwise as the Team tab lists them
        let tokens: i64 = teams.iter().map(|(_, b, _)| b["spend"]["tokens"].as_i64().unwrap_or(0)).sum();
        json!({"count": shown.iter().map(|t| t["need"].as_i64().unwrap_or(0)).sum::<i64>(), "teams": shown,
               "spend_tokens": self.brain.today.ready().then_some(tokens),
               "auto_fixes": st["fixes"].as_object().map_or(0, |f| f.values().filter(|v| *v == "auto").count()),
               "paused_until": pause_until().map(|p| p.to_zoned_datetime(jiff::tz::TimeZone::system()).strftime("%H:%M").to_string()),
               "limits": self.brain.limits(status::now_utc())})
    }

    pub fn state(&self) -> Value {
        self.panel(&self.teams(status::now_utc()), &settings::load(None))
    }

    fn needs(teams: &Teams) -> Events {
        let mut out = vec![];
        for (name, built, _) in teams {
            for p in
                panes_of(built).filter(|p| needs_you(p) && p["since_s"].as_i64().unwrap_or(0) <= live::NOTIFY_MAX_AGE)
            {
                let (f, role, state) = (&p["fix"], s(p, "role"), s(p, "state"));
                let buttons = f["buttons"].as_array().cloned().unwrap_or_default();
                let text =
                    [s(f, "text"), s(p, "detail"), "Waiting for you"].into_iter().find(|t| !t.is_empty()).unwrap_or("");
                let mk = |title: &str| {
                    event(
                        "needs_you",
                        Some(name),
                        Some(role),
                        Some("terminals"),
                        &f["id"],
                        title,
                        text,
                        labels(&buttons),
                    )
                };
                if p["paused_by_fix"] == true {
                    // it usually reads idle after Esc
                    out.push((
                        format!("need|{name}|{role}|paused|"),
                        mk(&format!("{role} is paused: still stuck after a fix")),
                    ));
                    continue;
                }
                if state == "asking" {
                    if !buttons.iter().any(|b| b["kind"] != "reply")
                        && p["since_s"].as_i64().filter(|s| *s != 0).unwrap_or(ANSWER_WAIT_S) < ANSWER_WAIT_S
                    {
                        continue; // its answers may be on the way
                    }
                    // Only a question's words identify it: a stuck agent's detail changes as the minutes pass.
                    out.push((format!("need|{name}|{role}|{state}|{text}"), mk(&format!("{role} is asking you"))));
                } else {
                    out.push((format!("need|{name}|{role}|{state}|"), mk(&format!("{role} {}", says(state)))));
                }
            }
        }
        out
    }

    fn budgets(teams: &Teams) -> Events {
        let mut out = vec![];
        for (name, built, _) in teams {
            for a in asks(built) {
                let key = format!("budget|{name}|{}|{}", s(a, "scope"), s(a, "fix"));
                out.push((
                    key,
                    event(
                        "needs_you",
                        Some(name),
                        a["role"].as_str(),
                        Some("terminals"),
                        &a["fix"],
                        s(a, "title"),
                        s(a, "detail"),
                        labels(a["buttons"].as_array().map_or(&[], Vec::as_slice)),
                    ),
                ));
            }
            for t in built["budget"]["told"].as_array().into_iter().flatten() {
                let key = format!("budget-told|{name}|{}|{}", s(t, "scope"), s(t, "day"));
                out.push((
                    key,
                    event("needs_you", Some(name), None, None, &Value::Null, s(t, "title"), s(t, "body"), vec![]),
                ));
            }
        }
        out
    }

    /// One notification per kind per reset, once agents wait for it.
    fn limits(teams: &Teams, plan_limits: &Value) -> Events {
        let mut out = vec![];
        for (kind, lim) in plan_limits.as_object().into_iter().flatten() {
            let n = teams
                .iter()
                .flat_map(|t| panes_of(&t.1))
                .filter(|p| p["state"] == "at-limit" && p["agent"] == kind.as_str())
                .count();
            if lim["reached"] != true || n == 0 {
                continue;
            }
            let back = lim["resets_at"]
                .as_f64()
                .and_then(|t| Timestamp::from_second(t as i64).ok())
                .map(|t| t.to_zoned_datetime(jiff::tz::TimeZone::system()).strftime("%H:%M").to_string());
            let wait = if n == 1 { "1 agent waits".to_string() } else { format!("{n} agents wait") };
            let body = back.map_or(format!("{wait} for it to reset."), |b| format!("{wait} until {b}."));
            let name = KIND_NAMES.iter().find(|k| k.0 == kind).map_or(kind.as_str(), |k| k.1);
            let key = format!("limit|{kind}|{}", crate::pyrules::repr(&lim["resets_at"]));
            out.push((
                key,
                event("stalls", None, None, None, &Value::Null, &format!("{name} reached its limit"), &body, vec![]),
            ));
        }
        out
    }

    fn stops(&self, teams: &Teams, ready_roles: &BTreeSet<(String, String)>) -> Events {
        static STOPPED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?is)^\W*stopped:\W*(.*)").unwrap());
        let mut ended: BTreeSet<(String, String)> = live::recent_actions(None, 200)
            .iter()
            .filter(|e| YOURS.contains(&s(e, "action")))
            .map(|e| (s(e, "team").to_string(), s(e, "agent").to_string()))
            .collect();
        ended.extend(ready_roles.iter().cloned());
        let mut out = vec![];
        for (name, built, matched) in teams {
            for p in panes_of(built) {
                if p["since_s"].as_i64().is_none_or(|s| s > live::NOTIFY_MAX_AGE) || p["paused_by_fix"] == true {
                    continue; // no age: an old terminal this app has never seen, not news; a paused agent isn't done
                }
                let (role, address) = (s(p, "role"), s(p, "address"));
                let said = (p["state"] == "idle").then(|| matched.get(address)).flatten().map(|a| self.last_words(a));
                let said = said.as_deref().and_then(|w| STOPPED.captures(w)).map(|c| c[1].to_string());
                let (key, body) = if let Some(said) = said {
                    (
                        format!("stop|{name}|{role}|{}", said.chars().take(80).collect::<String>()),
                        said.lines().next().unwrap_or("").to_string(),
                    )
                } else if p["state"] == "exited" && !ended.contains(&(name.clone(), role.to_string())) {
                    (format!("stop|{name}|{role}|{address}"), "It has stopped running.".to_string())
                } else {
                    continue;
                };
                let body = if body.is_empty() { "It stopped and reported back." } else { &body };
                out.push((
                    key,
                    event(
                        "finished",
                        Some(name),
                        Some(role),
                        Some("terminals"),
                        &Value::Null,
                        &format!("{role} stopped"),
                        body,
                        vec![json!({"id": "open", "label": "Open"})],
                    ),
                ));
            }
        }
        out
    }

    /// Asking Review starts the project's tests and reads its whole diff, so only for a notification still to come.
    fn ready(
        &self,
        waiting: &[Value],
        teams: &Teams,
        now: Timestamp,
        known: &Map<String, Value>,
        showing: bool,
    ) -> Events {
        let states: BTreeMap<(&str, &str), &str> = teams
            .iter()
            .flat_map(|(name, b, _)| {
                panes_of(b).map(move |p| {
                    ((name.as_str(), s(p, "role")), if p["paused_by_fix"] == true { "stuck" } else { s(p, "state") })
                })
            })
            .collect();
        let mut out = vec![];
        for w in waiting {
            let age = now.as_second() as f64 - w["at"].as_f64().unwrap_or(0.0);
            let state = states.get(&(s(w, "team"), s(w, "role"))).copied().unwrap_or("");
            if state == "working" || NEEDS_YOU.contains(&state) || age > live::NOTIFY_MAX_AGE as f64 {
                continue; // a busy agent's commit is routine progress; a question notifies on its own
            }
            let key = format!(
                "ready|{}|{}|{}",
                s(w, "team"),
                s(w, "role"),
                if w["tip"].is_null() { "None" } else { s(w, "tip") }
            );
            if known.contains_key(&key) {
                out.push((key, Value::Null)); // still true: remembered, so it can't pop up again
                continue;
            }
            if !showing {
                continue;
            }
            // Starts the tests once per commit, as opening Review would. Review keeps the result. Its folder may be
            // gone: the work is still ready.
            let tests = self
                .reviews
                .get(s(w, "team"), s(w, "role"), false)
                .ok()
                .and_then(|r| {
                    r["checks"].as_array()?.iter().find(|c| c["id"] == "tests").map(|c| s(c, "state").to_string())
                })
                .unwrap_or("none".into());
            if tests == "running" && age < TESTS_WAIT_S {
                continue;
            }
            let said = match tests.as_str() {
                "ok" => " Tests pass.",
                "bad" => " Tests fail.",
                _ => "",
            };
            let body = format!("{} finished “{}”.{said}", s(w, "role"), s(w, "subject"));
            out.push((
                key,
                event(
                    "finished",
                    Some(s(w, "team")),
                    Some(s(w, "role")),
                    Some("review"),
                    &Value::Null,
                    "Ready to review",
                    &body,
                    vec![],
                ),
            ));
        }
        out
    }

    fn stalls(stalls: &[Value]) -> Events {
        stalls
            .iter()
            .filter(|st| st["quiet_seconds"].as_i64().unwrap_or(0) <= live::NOTIFY_MAX_AGE)
            .map(|st| {
                let mut roots: Vec<&str> =
                    st["roots"].as_array().into_iter().flatten().map(|r| s(r, "agent")).collect();
                roots.sort();
                (
                    format!("stall|{}", roots.join("|")),
                    event("stalls", None, None, None, &Value::Null, "Team stalled", s(st, "text"), vec![]),
                )
            })
            .collect()
    }

    /// What should pop up now, once each. Paused: nothing is marked sent, so what's still true when it ends notifies
    /// then, and what was resolved meanwhile never does.
    fn events(
        &self,
        teams: &Teams,
        waiting: &[Value],
        stalls: &[Value],
        now: Timestamp,
        st: &Value,
        plan_limits: &Value,
    ) -> Vec<Value> {
        let ready_roles: BTreeSet<(String, String)> =
            waiting.iter().map(|w| (s(w, "team").to_string(), s(w, "role").to_string())).collect();
        let paused = pause_until();
        let showing = paused.is_none() && st["notify"]["finished"] != false;
        let mut found = Self::needs(teams);
        found.extend(Self::budgets(teams));
        found.extend(self.stops(teams, &ready_roles));
        found.extend(self.ready(waiting, teams, now, &self.load(), showing));
        found.extend(Self::stalls(stalls));
        found.extend(Self::limits(teams, plan_limits));
        let _lock = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let mut seen = self.load();
        let before: BTreeSet<String> = seen.keys().cloned().collect();
        let (stamp, forget) = (iso(now), iso(now - jiff::SignedDuration::from_secs(FORGET_S)));
        let mut out = vec![];
        for (key, mut e) in found {
            let on = e.is_object() && st["notify"][s(&e, "kind")] != false;
            if seen.contains_key(&key) {
                seen.insert(key, json!(stamp));
            } else if paused.is_none() && on {
                e["key"] = json!(key);
                seen.insert(key, json!(stamp));
                out.push(e);
            }
        }
        seen.retain(|_, v| v.as_str().is_some_and(|v| v >= forget.as_str()));
        if seen.keys().cloned().collect::<BTreeSet<_>>() != before {
            let _ = quiet::write_json(&self.store, &Value::Object(seen));
        }
        out
    }

    /// The app's regular call: the count for the icon and what to pop up.
    pub fn poll(&self) -> Value {
        let now = status::now_utc();
        *self.polled.lock().unwrap_or_else(|e| e.into_inner()) = Some(now);
        let st = settings::load(None);
        let (teams, agents) = self.brain.one_view(|| (self.teams(now), self.brain.collect(now)));
        let stalls = status::stalls(&agents, now);
        // The brain's plain notifications take over if the app quits: they mustn't repeat what's been shown.
        live::announce(
            &status::owner_queue(&agents, now),
            &stalls,
            now,
            Some(&|_: &str, _: &str| {}),
            Some(&st["notify"]),
        );
        let panel = self.panel(&teams, &st);
        let events = self.events(&teams, &self.reviews.waiting(), &stalls, now, &st, &panel["limits"]);
        json!({"count": panel["count"], "panel": panel, "events": events})
    }

    /// The app polled recently, so it shows notifications itself.
    pub fn app_watching(&self, now: Option<Timestamp>) -> bool {
        let polled = *self.polled.lock().unwrap_or_else(|e| e.into_inner());
        polled.is_some_and(|p| (now.unwrap_or_else(status::now_utc) - p).as_secs_f64() < APP_GONE_S)
    }

    pub fn pause(&self) -> Value {
        let until = status::now_utc() + jiff::SignedDuration::from_secs(PAUSE_S);
        let _ =
            settings::set_value("notify_paused_until", &json!(until.strftime("%Y-%m-%dT%H:%M:%SZ").to_string()), None);
        self.state()
    }

    pub fn resume(&self) -> Value {
        let _ = settings::set_value("notify_paused_until", &Value::Null, None);
        self.state()
    }

    pub fn handlers(this: Arc<Menubar>) -> Handlers {
        let on = |name: &'static str, f: fn(&Menubar) -> Value| -> (&'static str, crate::server::Handler) {
            let m = this.clone();
            (name, Box::new(move |_, _| Ok(f(&m))))
        };
        vec![
            on("menu.state", Menubar::state),
            on("menu.poll", Menubar::poll),
            on("notifications.pause", Menubar::pause),
            on("notifications.resume", Menubar::resume),
        ]
    }
}

/// A time that sorts as text, for the keys remembered between polls.
fn iso(t: Timestamp) -> String {
    let mut out = String::new();
    let _ = write!(out, "{}", t.strftime("%Y-%m-%dT%H:%M:%S%.6fZ"));
    out
}
