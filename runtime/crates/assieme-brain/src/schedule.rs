//! Port of `assieme/schedule.py`, owned by session F (see OWNERS.md).
//!
//! Scheduled runs: a small team that starts on its own at set times, works on its own branches and stops when done.
//!
//! The brain's tick fires runs; there is no launchd job per schedule. A time missed while the Mac slept runs once when
//! it wakes, never once per missed time. A run still going at the next time is skipped, and Activity says so. A run ends
//! when its top agent reports done: the brain stops its agents and their branches wait in Review. Nothing is merged.
//! Schedules are kept in schedules.json in the state folder.
use crate::live::Record;
use crate::prelude::*;
use jiff::Span;
use jiff::civil::Date;
use jiff::tz::TimeZone;
use serde_json::json;

pub const ENDS_NOW: &str =
    "Reported. This scheduled run ends now: its agents stop and the person reviews the branches later.";

const KINDS: [&str; 4] = ["day", "week", "hours", "minutes"];
const DAYS: [&str; 7] = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/// The form offers hours; minutes are for a short test.
const MIN_MINUTES: i64 = 5;
const DONE_NOTE: &str = "This is a scheduled run that nobody watches live. Work on your own branch and commit there; \
never merge or push. When the whole job is finished, call report with state \"done\": that ends the run and stops \
every agent of the team. The person reviews the branches later.";

/// The brain serves each call on its own thread, and the tick runs on another.
static LOCK: Mutex<()> = Mutex::new(());

/// A problem the form shows next to its field: `name`, `project`, `preset`, `every`, `task` or `run`.
/// The text is `field: message`, as the app reads it.
#[derive(Debug, Clone, PartialEq)]
pub struct ScheduleError(pub String);

impl ScheduleError {
    fn new(field: &str, message: &str) -> ScheduleError {
        ScheduleError(format!("{field}: {message}"))
    }

    fn message(&self) -> &str {
        self.0.split_once(": ").map_or(&self.0, |(_, m)| m)
    }
}

impl From<ScheduleError> for BrainError {
    fn from(e: ScheduleError) -> BrainError {
        BrainError::new(e.0)
    }
}

/// Starts a saved team: `(project, task, roles, practices, name)` as `Starter::start`.
pub type StartTeam = dyn Fn(&str, &str, &[Value], &Value, Option<&str>) -> Result<Value, BrainError> + Send + Sync;

/// Everything a schedule asks of the rest of the brain, so tests can stand in for it.
trait Deps: Send + Sync {
    fn status(&self, team: &str) -> Result<Vec<Value>, RuntimeError>;
    fn stop(&self, team: &str) -> Result<usize, RuntimeError>;
    fn record(&self, row: Record<'_>);
    fn check_preset(&self, id: &str) -> Result<(), ScheduleError>;
    /// The preset's roles, each named after the schedule's team, and its practices.
    fn roles(&self, id: &str, team: &str) -> Result<(Vec<Value>, Value), ScheduleError>;
    /// What the row says about its team: `{preset_label, who}`.
    fn described(&self, id: &str) -> Value;
    fn project_check(&self, path: &str) -> Value;
    fn team_name(&self, name: &str, taken: &BTreeSet<String>) -> String;
    fn saved_teams(&self) -> BTreeSet<String>;
    fn team_exists(&self, team: &str) -> bool;
    fn roster(&self, team: &str) -> BTreeMap<String, Value>;
    fn fresh(&self, team: &str, task: &str);
    fn notify_finished(&self) -> bool;
    /// The teams a schedule can start, leaving out `own`.
    fn options(&self, own: &BTreeSet<String>) -> Vec<Value>;
}

struct Real {
    rt: Runtime,
    teams: PathBuf,
}

impl Real {
    fn folder(&self, id: &str) -> Result<PathBuf, ScheduleError> {
        if crate::newteam::BUILTIN.contains(&id) {
            return Ok(crate::newteam::PRESETS_DIR.join(id));
        }
        match id.strip_prefix("saved:") {
            Some(d) if !d.is_empty() && Path::new(d).file_name().is_some_and(|f| f == d) => Ok(self.teams.join(d)),
            _ => Err(ScheduleError::new("preset", "Choose a team.")),
        }
    }
}

fn join_words(xs: &[String]) -> String {
    match xs {
        [] => String::new(),
        [x] => x.clone(),
        [init @ .., last] => format!("{} and {last}", init.join(", ")),
    }
}

/// The agents and models a team runs unattended, as the app shows them: "Claude Code, Sonnet and Opus".
fn who(roles: &[crate::preset::Role]) -> String {
    let unique = |xs: Vec<String>| {
        let mut out: Vec<String> = vec![];
        for x in xs {
            if !out.contains(&x) {
                out.push(x);
            }
        }
        out
    };
    let name = |table: &[(&str, &str)], k: &str| {
        table.iter().find(|(i, _)| *i == k).map_or(k.to_string(), |(_, n)| n.to_string())
    };
    let agents = unique(roles.iter().map(|r| name(crate::launch::NAMES, &r.agent)).collect());
    let models = unique(
        roles
            .iter()
            .map(|r| r.model.as_deref().map_or("its default model".into(), |m| name(crate::newteam::CLAUDE_MODELS, m)))
            .collect(),
    );
    format!("{}, {}", join_words(&agents), join_words(&models))
}

impl Deps for Real {
    fn status(&self, team: &str) -> Result<Vec<Value>, RuntimeError> {
        crate::team::status(&self.rt, Some(team))
    }

    fn stop(&self, team: &str) -> Result<usize, RuntimeError> {
        crate::team::stop(&self.rt, team)
    }

    fn record(&self, row: Record<'_>) {
        crate::live::record(row);
    }

    fn check_preset(&self, id: &str) -> Result<(), ScheduleError> {
        self.folder(id).map(|_| ())
    }

    fn roles(&self, id: &str, team: &str) -> Result<(Vec<Value>, Value), ScheduleError> {
        let load = |e: String| ScheduleError::new("preset", &format!("This team can no longer be loaded: {e}"));
        let tm = crate::preset::load(&self.folder(id)?, false).map_err(|e| load(e.0))?;
        let roles = tm
            .roles
            .iter()
            .map(|r| {
                let mut d = crate::newteam::role_dict(r);
                d["name"] = Value::from(format!("{team}-{}", r.name));
                d["parent"] = r.parent.as_ref().map_or(Value::Null, |p| Value::from(format!("{team}-{p}")));
                d
            })
            .collect();
        let practices = if id.starts_with("saved:") {
            Value::Object(tm.practices)
        } else {
            crate::settings::load(None)["practices"].clone()
        };
        Ok((roles, practices))
    }

    fn described(&self, id: &str) -> Value {
        let loaded = self.folder(id).ok().and_then(|f| crate::preset::load(&f, false).ok().map(|tm| (f, tm)));
        let Some((folder, tm)) = loaded else {
            return json!({"preset_label": id.strip_prefix("saved:").unwrap_or(id), "who": ""});
        };
        let label = if crate::newteam::BUILTIN.contains(&id) {
            crate::newteam::as_preset(&folder, id, None).ok().and_then(|p| p["label"].as_str().map(String::from))
        } else {
            None
        };
        let label = label.unwrap_or_else(|| format!("{} (saved)", id.strip_prefix("saved:").unwrap_or(id)));
        json!({"preset_label": label, "who": who(&tm.roles)})
    }

    fn project_check(&self, path: &str) -> Value {
        crate::newteam::project_check(path)
    }

    fn team_name(&self, name: &str, taken: &BTreeSet<String>) -> String {
        crate::newteam::team_name(name, taken)
    }

    fn saved_teams(&self) -> BTreeSet<String> {
        std::fs::read_dir(&self.teams)
            .map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default()
    }

    fn team_exists(&self, team: &str) -> bool {
        self.teams.join(team).is_dir()
    }

    fn roster(&self, team: &str) -> BTreeMap<String, Value> {
        crate::newteam::roster(team, Some(&self.teams))
    }

    fn fresh(&self, team: &str, task: &str) {
        crate::tasks::fresh(team, task);
    }

    fn notify_finished(&self) -> bool {
        crate::settings::load(None)["notify"]["finished"].as_bool().unwrap_or(false)
    }

    fn options(&self, own: &BTreeSet<String>) -> Vec<Value> {
        let saved: Vec<String> = {
            let mut s: Vec<String> =
                self.saved_teams().into_iter().filter(|d| self.teams.join(d).join("team.toml").is_file()).collect();
            s.sort();
            s
        };
        let builtin = crate::newteam::BUILTIN.iter().map(|i| (i.to_string(), None));
        let saved = saved
            .into_iter()
            .filter(|d| !own.contains(d))
            .map(|d| (format!("saved:{d}"), Some(format!("{d} (saved)"))));
        let mut out = vec![];
        for (id, label) in builtin.chain(saved) {
            let Ok(folder) = self.folder(&id) else { continue };
            let Ok(tm) = crate::preset::load(&folder, false) else { continue }; // half-written or hand-edited: not offered
            let label = label
                .or_else(|| {
                    crate::newteam::as_preset(&folder, &id, None)
                        .ok()
                        .and_then(|p| p["label"].as_str().map(String::from))
                })
                .unwrap_or_else(|| id.clone());
            out.push(json!({"id": id, "label": label, "who": who(&tm.roles)}));
        }
        out
    }
}

// --- the file ------------------------------------------------------------------------------------------------------

fn file_path() -> PathBuf {
    crate::quiet::path(&["schedules.json"])
}

fn load_from(file: &Path) -> Vec<Value> {
    match crate::quiet::read_json(file, json!([])) {
        Value::Array(items) => {
            items.into_iter().filter(|s| s.is_object() && s.get("id").is_some_and(crate::pyrules::truthy)).collect()
        }
        _ => vec![],
    }
}

fn save_to(file: &Path, items: &[Value]) {
    // A write that fails leaves the old file whole; the next tick tries again.
    let _ = crate::quiet::write_json(file, &Value::Array(items.to_vec()));
}

/// The saved schedules.
pub fn load() -> Vec<Value> {
    load_from(&file_path())
}

fn iso(t: Timestamp) -> String {
    t.strftime("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// Our own `iso` strings only: a hand-edited file with another form reads as no time.
fn parse(v: &Value) -> Option<Timestamp> {
    v.as_str()?.parse().ok()
}

// --- time ----------------------------------------------------------------------------------------------------------

fn every_n(every: &Value) -> i64 {
    every.get("n").and_then(Value::as_i64).unwrap_or(1)
}

fn at_of(every: &Value) -> (i8, i8) {
    let at = every["at"].as_str().unwrap_or("00:00");
    let (h, m) = at.split_once(':').unwrap_or(("0", "0"));
    (h.parse().unwrap_or(0), m.parse().unwrap_or(0))
}

/// The planned time at or before `t` (it may be before the schedule was made), and the one after it.
fn slot(every: &Value, created: Timestamp, t: Timestamp, tz: &TimeZone) -> (Timestamp, Timestamp) {
    let kind = every["kind"].as_str().unwrap_or("");
    if kind == "hours" || kind == "minutes" {
        let unit: i128 = if kind == "minutes" { 60 } else { 3600 } * 1_000_000_000;
        let step = unit * every_n(every) as i128;
        let ns = |t: Timestamp| t.as_nanosecond();
        let slot = ns(created) + (ns(t) - ns(created)).div_euclid(step) * step;
        let make = |n: i128| Timestamp::from_nanosecond(n).unwrap_or(t);
        return (make(slot), make(slot + step));
    }
    let (h, m) = at_of(every);
    let days: i64 = if kind == "week" { 7 } else { 1 };
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

fn next_time(every: &Value, created: Timestamp, after: Timestamp, tz: &TimeZone) -> Timestamp {
    slot(every, created, after, tz).1
}

fn when_words(every: &Value) -> String {
    let at = every["at"].as_str().unwrap_or("");
    match every["kind"].as_str().unwrap_or("") {
        "day" => format!("Every day at {at}"),
        "week" => format!("{}s at {at}", DAYS[every["day"].as_u64().unwrap_or(0) as usize % 7]),
        k => {
            let n = every_n(every);
            let unit = if k == "hours" { "hour" } else { "minute" };
            if n == 1 { format!("Every {unit}") } else { format!("Every {n} {unit}s") }
        }
    }
}

fn day_words(t: Timestamp, now: Timestamp, tz: &TimeZone) -> String {
    let (t, now) = (t.to_zoned_datetime(tz.clone()), now.to_zoned_datetime(tz.clone()));
    let days = (t.date() - now.date()).get_days() as i64;
    let day = match days {
        0 => "today".to_string(),
        1 => "tomorrow".to_string(),
        -1 => "yesterday".to_string(),
        2..=6 => DAYS[t.weekday().to_monday_zero_offset() as usize].to_string(),
        _ => format!("{} {}", t.day(), MONTHS[t.month() as usize - 1]),
    };
    format!("{day} at {:02}:{:02}", t.hour(), t.minute())
}

// --- checking a schedule -------------------------------------------------------------------------------------------

fn parse_every(raw: &Value) -> Result<Value, ScheduleError> {
    let bad = |m: &str| ScheduleError::new("every", m);
    let kind = raw
        .get("kind")
        .and_then(Value::as_str)
        .filter(|k| KINDS.contains(k))
        .ok_or_else(|| bad("Choose when it runs."))?;
    if kind == "day" || kind == "week" {
        let at = raw.get("at").map_or(String::new(), |a| a.as_str().map_or(a.to_string(), String::from));
        let ok = at.len() == 5
            && at.as_bytes()[2] == b':'
            && at[..2].parse::<u8>().is_ok_and(|h| h < 24 && at.as_bytes()[0].is_ascii_digit())
            && at[3..].parse::<u8>().is_ok_and(|m| m < 60 && at.as_bytes()[3].is_ascii_digit());
        if !ok {
            return Err(bad("Give a time such as 02:00."));
        }
        let mut out = json!({"kind": kind, "at": at});
        if kind == "week" {
            let day = raw.get("day").and_then(Value::as_i64).filter(|d| (0..7).contains(d));
            out["day"] = day.ok_or_else(|| bad("Choose a day of the week."))?.into();
        }
        return Ok(out);
    }
    let (low, high) = if kind == "hours" { (1, 24 * 7) } else { (MIN_MINUTES, 24 * 60) };
    match raw.get("n").and_then(Value::as_i64).filter(|n| (low..=high).contains(n)) {
        Some(n) => Ok(json!({"kind": kind, "n": n})),
        None => Err(bad(&format!("Choose between {low} and {high} {kind}."))),
    }
}

fn agents_were(n: usize) -> String {
    if n == 1 { "its agent was".into() } else { format!("its {n} agents were") }
}

/// Python's `os.path.abspath(os.path.expanduser(path))`.
fn abspath(path: &str) -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    let expanded = match path.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => format!("{home}{rest}"),
        _ => path.to_string(),
    };
    let full = if expanded.starts_with('/') {
        expanded
    } else {
        format!("{}/{expanded}", std::env::current_dir().map(|d| d.to_string_lossy().into_owned()).unwrap_or_default())
    };
    let mut parts: Vec<&str> = vec![];
    for p in full.split('/') {
        match p {
            "" | "." => {}
            ".." => {
                parts.pop();
            }
            p => parts.push(p),
        }
    }
    format!("/{}", parts.join("/"))
}

// --- the scheduler -------------------------------------------------------------------------------------------------

/// Scheduled runs.
pub struct Scheduler {
    deps: Box<dyn Deps>,
    start: Box<StartTeam>,
    starting: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
    announce: Option<Box<Notify>>,
    file: PathBuf,
    now: Box<dyn Fn() -> Timestamp + Send + Sync>,
    tz: TimeZone,
}

impl Scheduler {
    pub fn new(
        rt: Runtime,
        start: Box<StartTeam>,
        starting: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
        announce: Option<Box<Notify>>,
    ) -> Scheduler {
        let teams = crate::newteam::teams_dir();
        Scheduler::build(Box::new(Real { rt, teams }), start, starting, announce, file_path())
    }

    fn build(
        deps: Box<dyn Deps>,
        start: Box<StartTeam>,
        starting: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
        announce: Option<Box<Notify>>,
        file: PathBuf,
    ) -> Scheduler {
        Scheduler { deps, start, starting, announce, file, now: Box::new(Timestamp::now), tz: TimeZone::system() }
    }

    fn going(&self, name: &str) -> bool {
        // The core isn't running: nothing of this team is.
        if self.deps.status(name).is_ok_and(|rows| rows.iter().any(|r| r["state"] == "live")) {
            return true;
        }
        (self.starting)().iter().any(|r| r["team"] == name && !r["done"].as_bool().unwrap_or(false))
    }

    /// Change one schedule under the lock, so an edit from the app and the tick never undo each other.
    fn update(&self, id: &str, change: impl FnOnce(&mut Map<String, Value>)) -> Option<Value> {
        let _lock = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let mut items = load_from(&self.file);
        let s = items.iter_mut().find(|x| x["id"] == id)?;
        change(s.as_object_mut()?);
        let s = s.clone();
        save_to(&self.file, &items);
        Some(s)
    }

    fn log(&self, s: &Value, action: &str, title: &str, reason: &str, ok: bool, alert: bool) {
        let name = s["name"].as_str().unwrap_or("");
        let mut extra = Map::new();
        extra.insert("title".into(), title.into());
        // A row that didn't work shows its detail ("It didn't work: ..."), so the why goes there too.
        self.deps.record(Record {
            action,
            agent: name,
            reason,
            failed: !ok,
            detail: if ok { "" } else { reason },
            team: s["team"].as_str(),
            by: "auto",
            now: Some((self.now)()),
            extra,
            ..Record::default()
        });
        if alert && let Some(announce) = &self.announce {
            announce(title, reason);
        }
    }

    /// Why the background start of this team failed, or None: New team's start records an error, or agents that
    /// never got going.
    fn start_failure(&self, name: &str) -> Option<String> {
        let records = (self.starting)();
        let rec = records.iter().rev().find(|r| r["team"] == name && r["done"].as_bool().unwrap_or(false))?;
        if let Some(e) = rec["error"].as_str().filter(|e| !e.is_empty()) {
            return Some(e.to_string());
        }
        let roles = rec["roles"].as_array().cloned().unwrap_or_default();
        let bad: Vec<&Value> = roles
            .iter()
            .filter(|r| matches!(r["state"].as_str(), Some("exited" | "not-reporting" | "needs-you")))
            .collect();
        if !bad.is_empty() && bad.len() == roles.len() {
            let text: Vec<String> = bad
                .iter()
                .map(|r| {
                    let detail = r["detail"].as_str().map(str::trim).filter(|d| !d.is_empty());
                    format!(
                        "{} {}{}",
                        r["role"].as_str().unwrap_or(""),
                        r["state"].as_str().unwrap_or(""),
                        detail.map_or(String::new(), |d| format!(": {d}"))
                    )
                })
                .collect();
            return Some(text.join("; ").chars().take(300).collect());
        }
        None
    }

    pub fn tick(&self) {
        let now = (self.now)();
        for s in load_from(&self.file) {
            let id = s["id"].as_str().unwrap_or("").to_string();
            let team = s["team"].as_str().unwrap_or("");
            let mut going = self.going(team);
            let running = s["running"].as_bool().unwrap_or(false);
            if running && s["done"].as_bool().unwrap_or(false) {
                going = !self.finish(&s, "finished"); // a core that doesn't answer leaves the run going: tried again next tick
            } else if running && !going {
                if let Some(why) = self.start_failure(team) {
                    self.failed(&s, s["last"]["at"].as_str().map(String::from), &why);
                } else {
                    // its agents ended some other way
                    let at = s["last"]["at"].clone();
                    self.update(&id, |x| {
                        x.insert("running".into(), false.into());
                        x.insert("done".into(), false.into());
                        x.insert("skipped".into(), Value::Null);
                        x.insert("last".into(), json!({"at": at, "result": "ended"}));
                    });
                    let name = s["name"].as_str().unwrap_or("");
                    self.log(
                        &s,
                        "schedule_ended",
                        &format!("{name} stopped early"),
                        "Its agents ended before its top agent said the work was done. What it committed waits in Review.",
                        true,
                        self.deps.notify_finished(),
                    );
                }
            }
            if s["removed"].as_bool().unwrap_or(false) {
                if !going {
                    let _lock = LOCK.lock().unwrap_or_else(|e| e.into_inner());
                    let rest: Vec<Value> = load_from(&self.file).into_iter().filter(|x| x["id"] != s["id"]).collect();
                    save_to(&self.file, &rest);
                }
                continue;
            }
            if s["paused"].as_bool().unwrap_or(false) {
                continue;
            }
            let Some(created) = parse(&s["created"]) else { continue };
            let Some(slot) = latest(&s["every"], created, now, &self.tz) else { continue };
            if slot <= parse(&s["handled"]).unwrap_or(created) {
                continue;
            }
            self.update(&id, |x| {
                x.insert("handled".into(), iso(slot).into());
            });
            let at = slot.to_zoned_datetime(self.tz.clone()).strftime("%H:%M").to_string();
            let name = s["name"].as_str().unwrap_or("");
            if going {
                self.update(&id, |x| {
                    x.insert("skipped".into(), at.clone().into());
                });
                self.log(
                    &s,
                    "schedule_skip",
                    &format!("Skipped {name} at {at}"),
                    "The last run is still going. Open its team to see what it is waiting for.",
                    true,
                    false,
                );
            } else {
                self.run(&s, now);
            }
        }
    }

    fn failed(&self, s: &Value, at: Option<String>, why: &str) {
        let id = s["id"].as_str().unwrap_or("");
        self.update(id, |x| {
            x.insert("running".into(), false.into());
            x.insert("done".into(), false.into());
            x.insert("skipped".into(), Value::Null);
            x.insert("last".into(), json!({"at": at, "result": "failed", "detail": why}));
        });
        let name = s["name"].as_str().unwrap_or("");
        self.log(
            s,
            "schedule_failed",
            &format!("Couldn't start {name}"),
            &format!(
                "{}. It tries again at its next time; fix this in Settings, Scheduled runs.",
                why.trim_end_matches('.')
            ),
            false,
            true,
        );
    }

    fn run(&self, s: &Value, now: Timestamp) {
        let text = |k: &str| s[k].as_str().unwrap_or("").to_string();
        let (id, team, preset) = (text("id"), text("team"), text("preset"));
        let started =
            self.deps.roles(&preset, &team).map_err(|e| e.message().to_string()).and_then(|(roles, practices)| {
                let task = format!("{}\n\n{DONE_NOTE}", text("task").trim());
                (self.start)(&text("project"), &task, &roles, &practices, Some(&team))
                    .map_err(|e| e.message)
                    .map(|_| roles)
            });
        let roles = match started {
            Ok(roles) => roles,
            Err(why) => return self.failed(s, Some(iso(now)), &why),
        };
        self.deps.fresh(&team, &text("task"));
        let review =
            roles.iter().find(|r| r["checkout"].as_str().unwrap_or("branch") == "branch").map(|r| r["name"].clone());
        let described = self.deps.described(&preset);
        self.update(&id, |x| {
            x.insert("running".into(), true.into());
            x.insert("done".into(), false.into());
            x.insert("skipped".into(), Value::Null);
            x.insert("review_role".into(), review.unwrap_or(Value::Null));
            x.insert("last".into(), json!({"at": iso(now), "result": "started"}));
            if let Value::Object(d) = described {
                x.extend(d);
            }
        });
        self.log(
            s,
            "schedule_start",
            &format!("Started {}", text("name")),
            &format!(
                "{}. Its agents stop when they say they are done; their work waits in Review.",
                when_words(&s["every"])
            ),
            true,
            false,
        );
    }

    fn finish(&self, s: &Value, result: &str) -> bool {
        let team = s["team"].as_str().unwrap_or("");
        let Ok(n) = self.deps.stop(team) else { return false }; // tried again at the next tick
        let id = s["id"].as_str().unwrap_or("");
        self.update(id, |x| {
            let at = x.get("last").and_then(|l| l.get("at")).cloned().unwrap_or(Value::Null);
            x.insert("running".into(), false.into());
            x.insert("done".into(), false.into());
            x.insert("skipped".into(), Value::Null);
            x.insert("last".into(), json!({"at": at, "result": result}));
        });
        let name = s["name"].as_str().unwrap_or("");
        if result == "finished" {
            self.log(
                s,
                "schedule_done",
                &format!("{name} finished"),
                &format!(
                    "Its top agent said the work is done, so {} stopped. Its work waits in Review; nothing was merged.",
                    agents_were(n)
                ),
                true,
                self.deps.notify_finished(),
            );
        } else {
            let mut extra = Map::new();
            extra.insert("title".into(), format!("Stopped the run of {name}").into());
            self.deps.record(Record {
                action: "schedule_stop",
                agent: name,
                reason: "You stopped the run. What it committed waits in Review.",
                team: Some(team),
                now: Some((self.now)()),
                extra,
                ..Record::default()
            });
        }
        true
    }

    /// An agent reported done. True when that ends a scheduled run: only the team's top agent can.
    pub fn done(&self, team: &str, role: &str) -> Option<String> {
        let roster = self.deps.roster(team);
        let found = roster.get(role)?;
        if found.get("parent").is_some_and(crate::pyrules::truthy) {
            return None;
        }
        let _lock = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let mut items = load_from(&self.file);
        let mut hit = None;
        for s in items.iter_mut().filter(|s| s["team"] == team && s["running"].as_bool().unwrap_or(false)) {
            s["done"] = true.into();
            hit = Some(ENDS_NOW.to_string());
        }
        if hit.is_some() {
            save_to(&self.file, &items);
        }
        hit
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
        let check = self.deps.project_check(&project);
        if !check["ok"].as_bool().unwrap_or(false) {
            return Err(ScheduleError::new("project", check["reason"].as_str().unwrap_or("")));
        }
        self.deps.check_preset(preset)?;
        let (team, old) = {
            let _lock = LOCK.lock().unwrap_or_else(|e| e.into_inner());
            let mut items = load_from(&self.file);
            let old = id.and_then(|id| items.iter().position(|x| x["id"] == id));
            if id.is_some() && old.is_none() {
                return Err(ScheduleError::new("name", "This schedule was removed."));
            }
            let team = match old {
                Some(i) => items[i]["team"].as_str().unwrap_or("").to_string(),
                None => {
                    let mut taken = self.deps.saved_teams();
                    taken.extend(items.iter().filter_map(|x| x["team"].as_str().map(String::from)));
                    self.deps.team_name(&name, &taken)
                }
            };
            self.deps.roles(preset, &team)?; // refuses a saved team that no longer loads
            let mut fields = json!({"name": name.chars().take(60).collect::<String>(), "project": project, "preset": preset,
                                    "task": task.trim(), "every": every});
            if let (Value::Object(d), Some(f)) = (self.deps.described(preset), fields.as_object_mut()) {
                f.extend(d);
            }
            let Value::Object(mut fields) = fields else { unreachable!() };
            match old {
                Some(i) => {
                    if items[i]["every"] != every {
                        // counted afresh, so a new time doesn't fire at once for a past one
                        let now = iso((self.now)());
                        fields.insert("created".into(), now.clone().into());
                        fields.insert("handled".into(), now.into());
                    }
                    if let Value::Object(o) = &mut items[i] {
                        o.extend(fields);
                    }
                }
                None => {
                    let mut item = json!({"id": uuid::Uuid::new_v4().simple().to_string()[..12], "team": team,
                                          "created": iso((self.now)()), "handled": null, "paused": false, "running": false,
                                          "done": false, "last": null});
                    if let Some(o) = item.as_object_mut() {
                        o.extend(fields);
                    }
                    items.push(item);
                }
            }
            save_to(&self.file, &items);
            (team, old.is_some())
        };
        let base = Path::new(&project).file_name().map_or(String::new(), |f| f.to_string_lossy().into_owned());
        let mut extra = Map::new();
        extra.insert("title".into(), format!("{} the schedule {name}", if old { "Changed" } else { "Added" }).into());
        self.deps.record(Record {
            action: "schedule_set",
            agent: &name,
            reason: &format!("{}, in {base}.", when_words(&every)),
            team: Some(&team),
            extra,
            ..Record::default()
        });
        Ok(Value::Array(self.rows()))
    }

    pub fn pause(&self, id: &str, paused: bool) -> Result<Value, ScheduleError> {
        let now = iso((self.now)());
        let s = self.update(id, |s| {
            s.insert("paused".into(), paused.into());
            if !paused {
                s.insert("handled".into(), now.into()); // resuming never runs the times it was paused through
            }
        });
        if let Some(s) = s {
            let name = s["name"].as_str().unwrap_or("");
            let mut extra = Map::new();
            extra.insert(
                "title".into(),
                format!("{} the schedule {name}", if paused { "Paused" } else { "Resumed" }).into(),
            );
            self.deps.record(Record {
                action: "schedule_pause",
                agent: name,
                reason: if paused {
                    "It won’t start until you resume it."
                } else {
                    "It runs again at its next time."
                },
                team: s["team"].as_str(),
                extra,
                ..Record::default()
            });
        }
        Ok(Value::Array(self.rows()))
    }

    /// Stop button: ends the run going now. The schedule carries on at its next time.
    pub fn stop(&self, id: &str) -> Result<Value, ScheduleError> {
        let s = load_from(&self.file)
            .into_iter()
            .find(|x| x["id"] == id)
            .filter(|s| s["running"].as_bool().unwrap_or(false));
        let Some(s) = s else { return Err(ScheduleError::new("run", "This run has already ended.")) };
        if !self.finish(&s, "stopped") {
            return Err(ScheduleError::new("run", "Couldn't reach its agents. Try again in a moment."));
        }
        Ok(Value::Array(self.rows()))
    }

    /// Try again now, after a run that couldn't start.
    pub fn run_now(&self, id: &str) -> Result<Value, ScheduleError> {
        let s = load_from(&self.file).into_iter().find(|x| x["id"] == id && !x["removed"].as_bool().unwrap_or(false));
        let Some(s) = s else { return Err(ScheduleError::new("run", "This schedule was removed.")) };
        if self.going(s["team"].as_str().unwrap_or("")) {
            return Err(ScheduleError::new("run", "It is running now."));
        }
        self.run(&s, (self.now)());
        Ok(Value::Array(self.rows()))
    }

    /// Its saved team and branches stay, so its work stays in Review. A run going now carries on to its end.
    pub fn remove(&self, id: &str) -> Result<Value, ScheduleError> {
        let s = self.update(id, |x| {
            x.insert("removed".into(), true.into());
        });
        if let Some(s) = s {
            let name = s["name"].as_str().unwrap_or("");
            let mut extra = Map::new();
            extra.insert("title".into(), format!("Removed the schedule {name}").into());
            self.deps.record(Record {
                action: "schedule_remove",
                agent: name,
                reason: "Its branches stay in Review.",
                team: s["team"].as_str(),
                extra,
                ..Record::default()
            });
        }
        Ok(Value::Array(self.rows()))
    }

    /// Teams of schedules that have run: the app keeps them in its team picker, so a run's Activity and board
    /// stay reachable after its agents stop.
    pub fn run_teams(&self) -> Vec<String> {
        load_from(&self.file)
            .iter()
            .filter(|s| !s["removed"].as_bool().unwrap_or(false))
            .filter_map(|s| s["team"].as_str())
            .filter(|t| self.deps.team_exists(t))
            .map(String::from)
            .collect()
    }

    pub fn rows(&self) -> Vec<Value> {
        let (now, tz) = ((self.now)(), &self.tz);
        let mut out = vec![];
        for s in load_from(&self.file) {
            if s["removed"].as_bool().unwrap_or(false) {
                continue;
            }
            let Some(created) = parse(&s["created"]) else { continue };
            let last = if s["last"].is_object() { s["last"].clone() } else { json!({}) };
            let at = parse(&last["at"]);
            let after = parse(&s["handled"]).unwrap_or(created).max(now);
            let nxt = next_time(&s["every"], created, after, tz);
            let running = s["running"].as_bool().unwrap_or(false);
            let paused = s["paused"].as_bool().unwrap_or(false);
            let result = if running { Value::from("running") } else { last["result"].clone() };
            let mut note = Value::Null;
            let line = if running {
                if let Some(sk) = s["skipped"].as_str().filter(|s| !s.is_empty()) {
                    note = format!("Skipped {sk}: the last run was still going.").into();
                }
                let when = at
                    .map_or("just now".to_string(), |a| a.to_zoned_datetime(tz.clone()).strftime("%H:%M").to_string());
                format!("Running now, started {when}. Its agents stop when they say they are done.")
            } else if paused {
                "Paused. It won’t start until you resume it.".to_string()
            } else {
                let said = at.and_then(|a| {
                    let day = day_words(a, now, tz);
                    Some(match result.as_str()? {
                        "finished" => format!("Last run {day} finished; its work waits in Review."),
                        "ended" => format!(
                            "Last run {day} stopped before it said it was done. What it committed waits in Review."
                        ),
                        "stopped" => format!("Last run {day} was stopped by you. What it committed waits in Review."),
                        "failed" => format!(
                            "Couldn’t start {day}: {}.",
                            last["detail"].as_str().unwrap_or("").trim_end_matches('.')
                        ),
                        _ => return None,
                    })
                });
                format!("{}Next: {}.", said.map_or(String::new(), |s| s + " "), day_words(nxt, now, tz))
            };
            let project = s["project"].as_str().unwrap_or("");
            let preset = s["preset"].as_str().unwrap_or("");
            out.push(json!({
                "id": s["id"], "name": s["name"], "team": s["team"], "project": project,
                "project_name": Path::new(project).file_name().map_or(String::new(), |f| f.to_string_lossy().into_owned()),
                "preset": preset,
                "preset_label": s["preset_label"].as_str().filter(|l| !l.is_empty()).unwrap_or(preset.strip_prefix("saved:").unwrap_or(preset)),
                "who": s["who"].as_str().unwrap_or(""), "task": s["task"], "every": s["every"], "when": when_words(&s["every"]),
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
                    let own: BTreeSet<String> =
                        load_from(&s.file).iter().filter_map(|x| x["team"].as_str().map(String::from)).collect();
                    out["teams"] = Value::Array(s.deps.options(&own));
                }
                Ok(out)
            }),
            ("schedule.save", |s, p| {
                let text =
                    |k: &str| p[k].as_str().map(String::from).ok_or_else(|| BrainError::new(format!("Missing {k}.")));
                Ok(s.save(
                    &text("name")?,
                    &text("project")?,
                    &text("preset")?,
                    &text("task")?,
                    &p["every"],
                    p["id"].as_str(),
                )?)
            }),
            ("schedule.pause", |s, p| {
                let id = p["id"].as_str().ok_or_else(|| BrainError::new("Missing id."))?;
                // `paused` defaults to true.
                Ok(s.pause(id, p.get("paused").is_none_or(crate::pyrules::truthy))?)
            }),
            ("schedule.remove", |s, p| Ok(s.remove(p["id"].as_str().ok_or_else(|| BrainError::new("Missing id."))?)?)),
            ("schedule.stop", |s, p| Ok(s.stop(p["id"].as_str().ok_or_else(|| BrainError::new("Missing id."))?)?)),
            (
                "schedule.run_now",
                |s, p| Ok(s.run_now(p["id"].as_str().ok_or_else(|| BrainError::new("Missing id."))?)?),
            ),
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
