//! Port of `assieme/schedule.py`, owned by session F (see OWNERS.md).
//!
//! Scheduled runs: a small team that starts on its own at set times, works on its own branches and stops when done.
//! The brain's tick fires runs. A time missed while the Mac slept runs once when it wakes; a run still going at the next
//! time is skipped. A run ends when its top agent reports done: the agents stop and their branches wait in Review.
//! Schedules are kept in schedules.json in the state folder.
use crate::live::Record;
use crate::prelude::*;
use jiff::{Span, civil::Date, tz::TimeZone};
use regex::Regex;
use serde_json::json;

pub const ENDS_NOW: &str =
    "Reported. This scheduled run ends now: its agents stop and the person reviews the branches later.";

const DAYS: [&str; 7] = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DONE_NOTE: &str = "This is a scheduled run that nobody watches live. Work on your own branch and commit there; \
never merge or push. When the whole job is finished, call report with state \"done\": that ends the run and stops \
every agent of the team. The person reviews the branches later.";

/// The brain serves each call on its own thread, and the tick runs on another.
static LOCK: Mutex<()> = Mutex::new(());

/// A problem the form shows next to its field, as `field: message`.
#[derive(Debug, Clone, PartialEq)]
pub struct ScheduleError(pub String);

impl ScheduleError {
    fn new(field: &str, message: &str) -> ScheduleError {
        ScheduleError(format!("{field}: {message}"))
    }
}

impl From<ScheduleError> for BrainError {
    fn from(e: ScheduleError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// Starts a saved team: `(project, task, roles, practices, name)` as `Starter::start`.
pub type StartTeam = dyn Fn(&str, &str, &[Value], &Value, Option<&str>) -> Result<Value, BrainError> + Send + Sync;

// --- the file ------------------------------------------------------------------------------------------------------

fn file() -> PathBuf {
    crate::quiet::path(&["schedules.json"])
}

/// The saved schedules.
pub fn load() -> Vec<Value> {
    match crate::quiet::read_json(&file(), json!([])) {
        Value::Array(items) => {
            items.into_iter().filter(|s| s.is_object() && crate::pyrules::truthy(&s["id"])).collect()
        }
        _ => vec![],
    }
}

fn save_all(items: &[Value]) {
    // A write that fails leaves the old file whole; the next tick tries again.
    let _ = crate::quiet::write_json(&file(), &Value::Array(items.to_vec()));
}

fn lock() -> std::sync::MutexGuard<'static, ()> {
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// Sets fields of one schedule.
fn put(s: &mut Value, fields: Value) {
    if let (Some(o), Value::Object(f)) = (s.as_object_mut(), fields) {
        o.extend(f);
    }
}

/// Changes one schedule under the lock, so an edit from the app and the tick never undo each other.
fn update(id: &str, fields: Value) -> Option<Value> {
    let _lock = lock();
    let mut items = load();
    let s = items.iter_mut().find(|x| x["id"] == id)?;
    put(s, fields);
    let s = s.clone();
    save_all(&items);
    Some(s)
}

fn iso(t: Timestamp) -> String {
    t.strftime("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// Our own `iso` strings only: a hand-edited file with another form reads as no time.
fn parse(v: &Value) -> Option<Timestamp> {
    v.as_str()?.parse().ok()
}

fn flag(s: &Value, k: &str) -> bool {
    s[k].as_bool().unwrap_or(false)
}

fn text<'a>(s: &'a Value, k: &str) -> &'a str {
    s[k].as_str().unwrap_or("")
}

// --- time ----------------------------------------------------------------------------------------------------------

fn every_n(every: &Value) -> i64 {
    every["n"].as_i64().unwrap_or(1)
}

/// The planned time at or before `t` (it may be before the schedule was made), and the one after it.
fn slot(every: &Value, created: Timestamp, t: Timestamp, tz: &TimeZone) -> (Timestamp, Timestamp) {
    let kind = text(every, "kind");
    if kind == "hours" || kind == "minutes" {
        let step = (if kind == "minutes" { 60 } else { 3600 } * 1_000_000_000 * every_n(every)) as i128;
        let (c, now) = (created.as_nanosecond(), t.as_nanosecond());
        let slot = c + (now - c).div_euclid(step) * step;
        let make = |n: i128| Timestamp::from_nanosecond(n).unwrap_or(t);
        return (make(slot), make(slot + step));
    }
    let (h, m) =
        text(every, "at").split_once(':').map_or((0, 0), |(h, m)| (h.parse().unwrap_or(0), m.parse().unwrap_or(0)));
    let days = if kind == "week" { 7 } else { 1 };
    // The wall-clock time on that day, with that day's own UTC offset (the system's DST rules).
    let at = |d: Date| tz.to_zoned_datetime(d.at(h, m, 0, 0)).map_or(t, |z| z.timestamp());
    let back = |d: Date, n: i64| d.checked_sub(Span::new().days(n)).unwrap_or(d);
    let mut day = t.to_zoned_datetime(tz.clone()).date();
    if kind == "week" {
        let want = every["day"].as_i64().unwrap_or(0);
        day = back(day, (i64::from(day.weekday().to_monday_zero_offset()) - want).rem_euclid(7));
    }
    if at(day) > t {
        day = back(day, days);
    }
    (at(day), at(back(day, -days)))
}

/// The last planned time at or before now, or None before the first one after the schedule was made.
fn latest(every: &Value, created: Timestamp, now: Timestamp, tz: &TimeZone) -> Option<Timestamp> {
    Some(slot(every, created, now, tz).0).filter(|s| *s > created)
}

fn when_words(every: &Value) -> String {
    let at = text(every, "at");
    match text(every, "kind") {
        "day" => format!("Every day at {at}"),
        "week" => format!("{}s at {at}", DAYS[every["day"].as_u64().unwrap_or(0) as usize % 7]),
        k => {
            let (n, unit) = (every_n(every), if k == "hours" { "hour" } else { "minute" });
            if n == 1 { format!("Every {unit}") } else { format!("Every {n} {unit}s") }
        }
    }
}

fn day_words(t: Timestamp, now: Timestamp, tz: &TimeZone) -> String {
    let (t, now) = (t.to_zoned_datetime(tz.clone()), now.to_zoned_datetime(tz.clone()));
    let day = match (t.date() - now.date()).get_days() {
        0 => "today".to_string(),
        1 => "tomorrow".to_string(),
        -1 => "yesterday".to_string(),
        2..=6 => DAYS[t.weekday().to_monday_zero_offset() as usize].to_string(),
        _ => format!("{} {}", t.day(), MONTHS[t.month() as usize - 1]),
    };
    format!("{day} at {:02}:{:02}", t.hour(), t.minute())
}

fn parse_every(raw: &Value) -> Result<Value, ScheduleError> {
    let bad = |m: &str| ScheduleError::new("every", m);
    let kind = raw["kind"]
        .as_str()
        .filter(|k| ["day", "week", "hours", "minutes"].contains(k))
        .ok_or_else(|| bad("Choose when it runs."))?;
    if kind == "day" || kind == "week" {
        let at = raw["at"].as_str().filter(|a| Regex::new(r"^([01]\d|2[0-3]):[0-5]\d$").is_ok_and(|r| r.is_match(a)));
        let mut out = json!({"kind": kind, "at": at.ok_or_else(|| bad("Give a time such as 02:00."))?});
        if kind == "week" {
            out["day"] = raw["day"]
                .as_i64()
                .filter(|d| (0..7).contains(d))
                .ok_or_else(|| bad("Choose a day of the week."))?
                .into();
        }
        return Ok(out);
    }
    let (low, high) = if kind == "hours" { (1, 24 * 7) } else { (5, 24 * 60) };
    match raw["n"].as_i64().filter(|n| (low..=high).contains(n)) {
        Some(n) => Ok(json!({"kind": kind, "n": n})),
        None => Err(bad(&format!("Choose between {low} and {high} {kind}."))),
    }
}

/// Python's `os.path.abspath(os.path.expanduser(path))`.
fn abspath(path: &str) -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    let joined = match path.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => format!("{home}{rest}"),
        _ if path.starts_with('/') => path.to_string(),
        _ => format!("{}/{path}", std::env::current_dir().unwrap_or_default().display()),
    };
    let mut parts: Vec<&str> = vec![];
    for p in joined.split('/') {
        match p {
            "" | "." => {}
            ".." => drop(parts.pop()),
            p => parts.push(p),
        }
    }
    format!("/{}", parts.join("/"))
}

fn base(path: &str) -> String {
    Path::new(path).file_name().map_or(String::new(), |f| f.to_string_lossy().into_owned())
}

// --- the teams a schedule starts -----------------------------------------------------------------------------------

fn folder(teams: &Path, id: &str) -> Result<PathBuf, ScheduleError> {
    match id.strip_prefix("saved:") {
        _ if crate::newteam::BUILTIN.contains(&id) => Ok(crate::newteam::PRESETS_DIR.join(id)),
        Some(d) if !d.is_empty() && !d.contains('/') => Ok(teams.join(d)),
        _ => Err(ScheduleError::new("preset", "Choose a team.")),
    }
}

/// The agents and models a team runs unattended, as the app shows them: "Claude Code, Sonnet and Opus".
fn who(roles: &[crate::preset::Role]) -> String {
    let name = |table: &[(&str, &str)], k: &str| {
        table.iter().find(|(i, _)| *i == k).map_or(k.to_string(), |(_, n)| n.to_string())
    };
    let unique = |xs: Vec<String>| {
        xs.iter().enumerate().filter(|(i, x)| !xs[..*i].contains(x)).map(|(_, x)| x.clone()).collect::<Vec<_>>()
    };
    let join = |xs: Vec<String>| match xs.split_last() {
        Some((last, init)) if !init.is_empty() => format!("{} and {last}", init.join(", ")),
        Some((last, _)) => last.clone(),
        None => String::new(),
    };
    let agents = unique(roles.iter().map(|r| name(crate::launch::NAMES, &r.agent)).collect());
    let models = unique(
        roles
            .iter()
            .map(|r| r.model.as_deref().map_or("its default model".into(), |m| name(crate::newteam::CLAUDE_MODELS, m)))
            .collect(),
    );
    format!("{}, {}", join(agents), join(models))
}

/// What the row says about its team, kept on the schedule so polling the rows reads no team folders.
fn described(teams: &Path, id: &str) -> Value {
    let Some((folder, tm)) = folder(teams, id).ok().and_then(|f| crate::preset::load(&f, false).ok().map(|tm| (f, tm)))
    else {
        return json!({"preset_label": id.strip_prefix("saved:").unwrap_or(id), "who": ""});
    };
    let label = match id.strip_prefix("saved:") {
        Some(d) => format!("{d} (saved)"),
        None => crate::newteam::as_preset(&folder, id, None)
            .ok()
            .and_then(|p| p["label"].as_str().map(String::from))
            .unwrap_or_default(),
    };
    json!({"preset_label": label, "who": who(&tm.roles)})
}

/// The teams a schedule can start: the built-in ones, then every saved team that isn't a schedule's own.
fn options(teams: &Path) -> Vec<Value> {
    let own: BTreeSet<String> = load().iter().map(|s| text(s, "team").to_string()).collect();
    let mut saved: Vec<String> = std::fs::read_dir(teams)
        .map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect())
        .unwrap_or_default();
    saved.retain(|d| teams.join(d).join("team.toml").is_file() && !own.contains(d));
    saved.sort();
    let ids = crate::newteam::BUILTIN.iter().map(|i| i.to_string()).chain(saved.iter().map(|d| format!("saved:{d}")));
    // a half-written or hand-edited folder is not offered
    ids.filter_map(|id| {
        let d = described(teams, &id);
        let loaded = crate::preset::load(&folder(teams, &id).ok()?, false).ok()?;
        Some(json!({"id": id, "label": d["preset_label"], "who": who(&loaded.roles)}))
    })
    .collect()
}

// --- the scheduler -------------------------------------------------------------------------------------------------

/// Scheduled runs.
pub struct Scheduler {
    rt: Runtime,
    teams: PathBuf,
    start: Box<StartTeam>,
    starting: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
    announce: Option<Box<Notify>>,
}

impl Scheduler {
    pub fn new(
        rt: Runtime,
        start: Box<StartTeam>,
        starting: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
        announce: Option<Box<Notify>>,
    ) -> Scheduler {
        Scheduler { rt, teams: crate::newteam::teams_dir(), start, starting, announce }
    }

    fn going(&self, name: &str) -> bool {
        // The core isn't running: nothing of this team is.
        crate::team::status(&self.rt, Some(name)).is_ok_and(|rows| rows.iter().any(|r| r["state"] == "live"))
            || (self.starting)().iter().any(|r| r["team"] == name && !flag(r, "done"))
    }

    /// One Activity row.
    fn log(&self, s: &Value, action: &str, title: &str, reason: &str, ok: bool, alert: bool) {
        let extra = json!({"title": title}).as_object().cloned().unwrap_or_default();
        // A row that didn't work shows its detail ("It didn't work: ..."), so the why goes there too.
        let detail = if ok { "" } else { reason };
        crate::live::record(Record {
            action,
            agent: text(s, "name"),
            reason,
            failed: !ok,
            detail,
            team: s["team"].as_str(),
            by: "auto",
            now: Some(Timestamp::now()),
            extra,
            ..Record::default()
        });
        if let (true, Some(announce)) = (alert, &self.announce) {
            announce(title, reason);
        }
    }

    fn notify_finished(&self) -> bool {
        flag(&crate::settings::load(None)["notify"], "finished")
    }

    /// Why the background start of this team failed, or None: New team's start records an error, or agents that
    /// never got going.
    fn start_failure(&self, name: &str) -> Option<String> {
        let records = (self.starting)();
        let rec = records.iter().rev().find(|r| r["team"] == name && flag(r, "done"))?;
        if let Some(e) = rec["error"].as_str().filter(|e| !e.is_empty()) {
            return Some(e.to_string());
        }
        let roles = rec["roles"].as_array().cloned().unwrap_or_default();
        let bad: Vec<String> = roles
            .iter()
            .filter(|r| matches!(r["state"].as_str(), Some("exited" | "not-reporting" | "needs-you")))
            .map(|r| match r["detail"].as_str().map(str::trim).filter(|d| !d.is_empty()) {
                Some(d) => format!("{} {}: {d}", text(r, "role"), text(r, "state")),
                None => format!("{} {}", text(r, "role"), text(r, "state")),
            })
            .collect();
        (!bad.is_empty() && bad.len() == roles.len()).then(|| bad.join("; ").chars().take(300).collect())
    }

    pub fn tick(&self) {
        let (now, tz) = (Timestamp::now(), TimeZone::system());
        for s in load() {
            let (id, team) = (text(&s, "id"), text(&s, "team"));
            let mut going = self.going(team);
            if flag(&s, "running") && flag(&s, "done") {
                going = !self.finish(&s, "finished"); // a core that doesn't answer leaves the run going: tried again next tick
            } else if flag(&s, "running") && !going {
                match self.start_failure(team) {
                    Some(why) => self.failed(&s, s["last"]["at"].as_str().map(String::from), &why),
                    None => {
                        // its agents ended some other way
                        update(
                            id,
                            json!({"running": false, "done": false, "skipped": null, "last": {"at": s["last"]["at"], "result": "ended"}}),
                        );
                        let reason = "Its agents ended before its top agent said the work was done. What it committed waits in Review.";
                        self.log(
                            &s,
                            "schedule_ended",
                            &format!("{} stopped early", text(&s, "name")),
                            reason,
                            true,
                            self.notify_finished(),
                        );
                    }
                }
            }
            if flag(&s, "removed") {
                if !going {
                    let _lock = lock();
                    save_all(&load().into_iter().filter(|x| x["id"] != s["id"]).collect::<Vec<_>>());
                }
                continue;
            }
            let Some(created) = parse(&s["created"]) else { continue };
            let Some(slot) = latest(&s["every"], created, now, &tz)
                .filter(|sl| !flag(&s, "paused") && *sl > parse(&s["handled"]).unwrap_or(created))
            else {
                continue;
            };
            update(id, json!({"handled": iso(slot)}));
            let (at, name) = (slot.to_zoned_datetime(tz.clone()).strftime("%H:%M").to_string(), text(&s, "name"));
            if going {
                update(id, json!({"skipped": at}));
                let reason = "The last run is still going. Open its team to see what it is waiting for.";
                self.log(&s, "schedule_skip", &format!("Skipped {name} at {at}"), reason, true, false);
            } else {
                self.run(&s, now);
            }
        }
    }

    fn failed(&self, s: &Value, at: Option<String>, why: &str) {
        update(
            text(s, "id"),
            json!({"running": false, "done": false, "skipped": null, "last": {"at": at, "result": "failed", "detail": why}}),
        );
        let reason = format!(
            "{}. It tries again at its next time; fix this in Settings, Scheduled runs.",
            why.trim_end_matches('.')
        );
        self.log(s, "schedule_failed", &format!("Couldn't start {}", text(s, "name")), &reason, false, true);
    }

    /// The preset's roles, each named after the schedule's team: a role named plain `builder` would share the worktree of
    /// the person's own team's builder in the same project (worktrees are per role name).
    fn roles(&self, preset: &str, team: &str) -> Result<(Vec<Value>, Value), ScheduleError> {
        let folder = folder(&self.teams, preset)?;
        let tm = crate::preset::load(&folder, false)
            .map_err(|e| ScheduleError::new("preset", &format!("This team can no longer be loaded: {}", e.0)))?;
        let roles = tm.roles.iter().map(|r| {
            let mut d = crate::newteam::role_dict(r);
            d["name"] = format!("{team}-{}", r.name).into();
            d["parent"] = r.parent.as_ref().map_or(Value::Null, |p| format!("{team}-{p}").into());
            d
        });
        let practices = if preset.starts_with("saved:") {
            Value::Object(tm.practices)
        } else {
            crate::settings::load(None)["practices"].clone()
        };
        Ok((roles.collect(), practices))
    }

    fn run(&self, s: &Value, now: Timestamp) {
        let (id, team, preset) = (text(s, "id"), text(s, "team"), text(s, "preset"));
        let started = self
            .roles(preset, team)
            .map_err(|e| e.0.split_once(": ").map_or(e.0.clone(), |(_, m)| m.into()))
            .and_then(|(roles, practices)| {
                let task = format!("{}\n\n{DONE_NOTE}", text(s, "task").trim());
                (self.start)(text(s, "project"), &task, &roles, &practices, Some(team))
                    .map_err(|e| e.message)
                    .map(|_| roles)
            });
        let roles = match started {
            Ok(roles) => roles,
            Err(why) => return self.failed(s, Some(iso(now)), &why),
        };
        crate::tasks::fresh(team, text(s, "task"));
        let review =
            roles.iter().find(|r| r["checkout"].as_str().unwrap_or("branch") == "branch").map(|r| r["name"].clone());
        update(
            id,
            json!({"running": true, "done": false, "skipped": null, "review_role": review, "last": {"at": iso(now), "result": "started"}}),
        );
        update(id, described(&self.teams, preset));
        let reason = format!(
            "{}. Its agents stop when they say they are done; their work waits in Review.",
            when_words(&s["every"])
        );
        self.log(s, "schedule_start", &format!("Started {}", text(s, "name")), &reason, true, false);
    }

    fn finish(&self, s: &Value, result: &str) -> bool {
        let (team, name) = (text(s, "team"), text(s, "name"));
        let Ok(n) = crate::team::stop(&self.rt, team) else { return false }; // tried again at the next tick
        update(
            text(s, "id"),
            json!({"running": false, "done": false, "skipped": null, "last": {"at": s["last"]["at"], "result": result}}),
        );
        if result == "finished" {
            let agents = if n == 1 { "its agent was".to_string() } else { format!("its {n} agents were") };
            let reason = format!(
                "Its top agent said the work is done, so {agents} stopped. Its work waits in Review; nothing was merged."
            );
            self.log(s, "schedule_done", &format!("{name} finished"), &reason, true, self.notify_finished());
        } else {
            self.log(
                s,
                "schedule_stop",
                &format!("Stopped the run of {name}"),
                "You stopped the run. What it committed waits in Review.",
                true,
                false,
            );
        }
        true
    }

    /// An agent reported done. Some when that ends a scheduled run: only the team's top agent can.
    pub fn done(&self, team: &str, role: &str) -> Option<String> {
        let roster = crate::newteam::roster(team, Some(&self.teams));
        if roster.get(role)?.get("parent").is_some_and(crate::pyrules::truthy) {
            return None;
        }
        let _lock = lock();
        let mut items = load();
        let mine: Vec<&mut Value> = items.iter_mut().filter(|s| s["team"] == team && flag(s, "running")).collect();
        let hit = !mine.is_empty();
        mine.into_iter().for_each(|s| put(s, json!({"done": true})));
        hit.then(|| {
            save_all(&items);
            ENDS_NOW.to_string()
        })
    }

    // --- what the app asks -----------------------------------------------------------------------------------------

    pub fn save(
        &self,
        name: &str,
        project: &str,
        preset: &str,
        task: &str,
        every: &Value,
        id: Option<&str>,
    ) -> Result<Value, ScheduleError> {
        let name = name.split_whitespace().collect::<Vec<_>>().join(" ");
        if name.is_empty() {
            return Err(ScheduleError::new("name", "Give it a name."));
        }
        if task.trim().is_empty() {
            return Err(ScheduleError::new("task", "Say what the team should do."));
        }
        let every = parse_every(every)?;
        let project = abspath(project);
        let check = crate::newteam::project_check(&project);
        if !flag(&check, "ok") {
            return Err(ScheduleError::new("project", text(&check, "reason")));
        }
        folder(&self.teams, preset)?;
        let (team, old) = {
            let _lock = lock();
            let mut items = load();
            let old = id.and_then(|id| items.iter().position(|x| x["id"] == id));
            if id.is_some() && old.is_none() {
                return Err(ScheduleError::new("name", "This schedule was removed."));
            }
            let team = match old {
                Some(i) => text(&items[i], "team").to_string(),
                None => {
                    let mut taken: BTreeSet<String> = std::fs::read_dir(&self.teams)
                        .map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect())
                        .unwrap_or_default();
                    taken.extend(items.iter().map(|x| text(x, "team").to_string()));
                    crate::newteam::team_name(&name, &taken)
                }
            };
            self.roles(preset, &team)?; // refuses a saved team that no longer loads
            let mut fields = json!({"name": name.chars().take(60).collect::<String>(), "project": project, "preset": preset, "task": task.trim(), "every": every});
            put(&mut fields, described(&self.teams, preset));
            match old {
                Some(i) => {
                    if items[i]["every"] != every {
                        // counted afresh, so a new time doesn't fire at once for a past one
                        put(&mut fields, json!({"created": iso(Timestamp::now()), "handled": iso(Timestamp::now())}));
                    }
                    put(&mut items[i], fields);
                }
                None => {
                    let mut item = json!({"id": uuid::Uuid::new_v4().simple().to_string()[..12], "team": team, "created": iso(Timestamp::now()),
                                          "handled": null, "paused": false, "running": false, "done": false, "last": null});
                    put(&mut item, fields);
                    items.push(item);
                }
            }
            save_all(&items);
            (team, old.is_some())
        };
        let extra = json!({"title": format!("{} the schedule {name}", if old { "Changed" } else { "Added" })})
            .as_object()
            .cloned()
            .unwrap_or_default();
        let reason = format!("{}, in {}.", when_words(&every), base(&project));
        crate::live::record(Record {
            action: "schedule_set",
            agent: &name,
            reason: &reason,
            team: Some(&team),
            extra,
            ..Record::default()
        });
        Ok(Value::Array(self.rows()))
    }

    /// Records a change of one schedule that the person made.
    fn said(&self, s: Option<Value>, action: &str, title: &str, reason: &str) {
        let Some(s) = s else { return };
        let extra = json!({"title": format!("{title} {}", text(&s, "name"))}).as_object().cloned().unwrap_or_default();
        crate::live::record(Record {
            action,
            agent: text(&s, "name"),
            reason,
            team: s["team"].as_str(),
            extra,
            ..Record::default()
        });
    }

    pub fn pause(&self, id: &str, paused: bool) -> Result<Value, ScheduleError> {
        // resuming never runs the times it was paused through
        let s = update(
            id,
            if paused { json!({"paused": true}) } else { json!({"paused": false, "handled": iso(Timestamp::now())}) },
        );
        let (title, reason) = if paused {
            ("Paused the schedule", "It won’t start until you resume it.")
        } else {
            ("Resumed the schedule", "It runs again at its next time.")
        };
        self.said(s, "schedule_pause", title, reason);
        Ok(Value::Array(self.rows()))
    }

    /// Stop button: ends the run going now. The schedule carries on at its next time.
    pub fn stop(&self, id: &str) -> Result<Value, ScheduleError> {
        let s = load().into_iter().find(|x| x["id"] == id && flag(x, "running"));
        let s = s.ok_or_else(|| ScheduleError::new("run", "This run has already ended."))?;
        if !self.finish(&s, "stopped") {
            return Err(ScheduleError::new("run", "Couldn't reach its agents. Try again in a moment."));
        }
        Ok(Value::Array(self.rows()))
    }

    /// Try again now, after a run that couldn't start.
    pub fn run_now(&self, id: &str) -> Result<Value, ScheduleError> {
        let s = load().into_iter().find(|x| x["id"] == id && !flag(x, "removed"));
        let s = s.ok_or_else(|| ScheduleError::new("run", "This schedule was removed."))?;
        if self.going(text(&s, "team")) {
            return Err(ScheduleError::new("run", "It is running now."));
        }
        self.run(&s, Timestamp::now());
        Ok(Value::Array(self.rows()))
    }

    /// Its saved team and branches stay, so its work stays in Review. A run going now carries on to its end.
    pub fn remove(&self, id: &str) -> Result<Value, ScheduleError> {
        self.said(
            update(id, json!({"removed": true})),
            "schedule_remove",
            "Removed the schedule",
            "Its branches stay in Review.",
        );
        Ok(Value::Array(self.rows()))
    }

    /// Teams of schedules that have run: the app keeps them in its team picker, so a run's Activity and board stay
    /// reachable after its agents stop.
    pub fn run_teams(&self) -> Vec<String> {
        let live = load().into_iter().filter(|s| !flag(s, "removed"));
        live.map(|s| text(&s, "team").to_string()).filter(|t| self.teams.join(t).is_dir()).collect()
    }

    pub fn rows(&self) -> Vec<Value> {
        let (now, tz) = (Timestamp::now(), TimeZone::system());
        let hhmm = |t: Timestamp| t.to_zoned_datetime(tz.clone()).strftime("%H:%M").to_string();
        let mut out = vec![];
        for s in load().iter().filter(|s| !flag(s, "removed")) {
            let Some(created) = parse(&s["created"]) else { continue };
            let (last, running, paused) = (&s["last"], flag(s, "running"), flag(s, "paused"));
            let at = parse(&last["at"]);
            let nxt = slot(&s["every"], created, parse(&s["handled"]).unwrap_or(created).max(now), &tz).1;
            let result = if running { json!("running") } else { last["result"].clone() };
            let mut note = Value::Null;
            let line = if running {
                if let Some(sk) = s["skipped"].as_str().filter(|s| !s.is_empty()) {
                    note = format!("Skipped {sk}: the last run was still going.").into();
                }
                format!(
                    "Running now, started {}. Its agents stop when they say they are done.",
                    at.map_or("just now".into(), hhmm)
                )
            } else if paused {
                "Paused. It won’t start until you resume it.".to_string()
            } else {
                let said = at.zip(result.as_str()).and_then(|(a, r)| {
                    let day = day_words(a, now, &tz);
                    Some(match r {
                        "finished" => format!("Last run {day} finished; its work waits in Review."),
                        "ended" => format!(
                            "Last run {day} stopped before it said it was done. What it committed waits in Review."
                        ),
                        "stopped" => format!("Last run {day} was stopped by you. What it committed waits in Review."),
                        "failed" => format!("Couldn’t start {day}: {}.", text(last, "detail").trim_end_matches('.')),
                        _ => return None,
                    })
                });
                format!("{}Next: {}.", said.map_or(String::new(), |s| s + " "), day_words(nxt, now, &tz))
            };
            let (project, preset) = (text(s, "project"), text(s, "preset"));
            let label = Some(text(s, "preset_label"))
                .filter(|l| !l.is_empty())
                .unwrap_or(preset.strip_prefix("saved:").unwrap_or(preset));
            out.push(json!({
                "id": s["id"], "name": s["name"], "team": s["team"], "project": project, "project_name": base(project), "preset": preset,
                "preset_label": label, "who": text(s, "who"), "task": s["task"], "every": s["every"], "when": when_words(&s["every"]),
                "paused": paused, "state": if running { "running" } else if paused { "paused" } else { "waiting" },
                "result": result, "line": line, "note": note, "next": iso(nxt), "review_role": s["review_role"],
            }));
        }
        out
    }

    pub fn handlers(this: Arc<Scheduler>) -> Handlers {
        type F = fn(&Scheduler, &Value) -> Result<Value, BrainError>;
        let table: [(&'static str, F); 6] = [
            ("schedule.list", |s, p| {
                let mut out = json!({"schedules": s.rows()});
                if crate::pyrules::truthy(&p["teams"]) {
                    out["teams"] = Value::Array(options(&s.teams));
                }
                Ok(out)
            }),
            ("schedule.save", |s, p| {
                let t =
                    |k: &str| p[k].as_str().map(String::from).ok_or_else(|| BrainError::new(format!("Missing {k}.")));
                Ok(s.save(&t("name")?, &t("project")?, &t("preset")?, &t("task")?, &p["every"], p["id"].as_str())?)
            }),
            // `paused` defaults to true.
            ("schedule.pause", |s, p| {
                Ok(s.pause(p["id"].as_str().unwrap_or(""), p.get("paused").is_none_or(crate::pyrules::truthy))?)
            }),
            ("schedule.remove", |s, p| Ok(s.remove(p["id"].as_str().unwrap_or(""))?)),
            ("schedule.stop", |s, p| Ok(s.stop(p["id"].as_str().unwrap_or(""))?)),
            ("schedule.run_now", |s, p| Ok(s.run_now(p["id"].as_str().unwrap_or(""))?)),
        ];
        table
            .into_iter()
            .map(|(name, f)| {
                let this = this.clone();
                let h: crate::server::Handler = Box::new(move |p, _| f(&this, &p));
                (name, h)
            })
            .collect()
    }
}

#[cfg(test)]
mod tests;
