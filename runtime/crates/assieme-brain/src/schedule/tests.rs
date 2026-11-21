use super::*;
use std::sync::atomic::{AtomicBool, Ordering};

fn fixtures() -> Value {
    let path = format!("{}/tests/data/schedule.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn ts(s: &str) -> Timestamp {
    s.parse().unwrap()
}

/// Python's answers for every time case: the same slots, words and refusals.
#[test]
fn matches_the_recorded_python_answers() {
    let data = fixtures();
    for c in data["slots"].as_array().unwrap() {
        let tz = TimeZone::get(c["zone"].as_str().unwrap()).unwrap();
        let (created, now) = (ts(c["created"].as_str().unwrap()), ts(c["now"].as_str().unwrap()));
        let got = latest(&c["every"], created, now, &tz).map(iso);
        assert_eq!(got.as_deref(), c["latest"].as_str(), "latest: {c}");
        assert_eq!(iso(next_time(&c["every"], created, now, &tz)), c["next"].as_str().unwrap(), "next: {c}");
    }
    for c in data["words"].as_array().unwrap() {
        assert_eq!(when_words(&c["every"]), c["when"].as_str().unwrap(), "{c}");
    }
    for c in data["days"].as_array().unwrap() {
        let tz = TimeZone::get(c["zone"].as_str().unwrap()).unwrap();
        let got = day_words(ts(c["time"].as_str().unwrap()), ts(c["now"].as_str().unwrap()), &tz);
        assert_eq!(got, c["words"].as_str().unwrap(), "{c}");
    }
    for c in data["checks"].as_array().unwrap() {
        match (parse_every(&c["raw"]), c.get("ok"), c.get("error")) {
            (Ok(got), Some(want), _) => assert_eq!(&got, want, "{c}"),
            (Err(got), _, Some(want)) => assert_eq!(got.0, want.as_str().unwrap(), "{c}"),
            (got, ..) => panic!("{c}: got {got:?}"),
        }
    }
}

#[derive(Default)]
struct State {
    live: BTreeSet<String>,
    stops: Vec<String>,
    stop_fails: bool,
    records: Vec<(String, String, bool, String)>,
    started: Vec<(String, String, Vec<Value>, Option<String>)>,
    roster: BTreeMap<String, Value>,
    fresh: Vec<String>,
    start_log: Vec<Value>,
}

struct Fake(Arc<Mutex<State>>);

impl Deps for Fake {
    fn status(&self, team: &str) -> Result<Vec<Value>, RuntimeError> {
        let live = self.0.lock().unwrap().live.contains(team);
        Ok(if live { vec![json!({"state": "live"})] } else { vec![] })
    }
    fn stop(&self, team: &str) -> Result<usize, RuntimeError> {
        let mut s = self.0.lock().unwrap();
        if s.stop_fails {
            return Err(RuntimeError { kind: "x".into(), message: "down".into(), hint: String::new() });
        }
        s.stops.push(team.into());
        Ok(2)
    }
    fn record(&self, row: Record<'_>) {
        let title = row.extra.get("title").and_then(Value::as_str).unwrap_or("").to_string();
        self.0.lock().unwrap().records.push((row.action.into(), row.agent.into(), row.failed, title));
    }
    fn check_preset(&self, id: &str) -> Result<(), ScheduleError> {
        if id == "full" || id.starts_with("saved:") {
            Ok(())
        } else {
            Err(ScheduleError::new("preset", "Choose a team."))
        }
    }
    fn roles(&self, id: &str, team: &str) -> Result<(Vec<Value>, Value), ScheduleError> {
        if id == "saved:broken" {
            return Err(ScheduleError::new("preset", "This team can no longer be loaded: bad"));
        }
        Ok((
            vec![
                json!({"name": format!("{team}-lead"), "checkout": "read-only", "parent": null}),
                json!({"name": format!("{team}-builder"), "parent": format!("{team}-lead")}),
            ],
            json!({}),
        ))
    }
    fn described(&self, id: &str) -> Value {
        json!({"preset_label": id, "who": "Claude Code, Sonnet"})
    }
    fn project_check(&self, path: &str) -> Value {
        if path.ends_with("/nope") { json!({"ok": false, "reason": "Not a project."}) } else { json!({"ok": true}) }
    }
    fn team_name(&self, name: &str, taken: &BTreeSet<String>) -> String {
        let base = name.to_lowercase().replace(' ', "-");
        let mut n = base.clone();
        let mut k = 2;
        while taken.contains(&n) {
            n = format!("{base}-{k}");
            k += 1;
        }
        n
    }
    fn saved_teams(&self) -> BTreeSet<String> {
        BTreeSet::new()
    }
    fn team_exists(&self, team: &str) -> bool {
        team != "ghost"
    }
    fn roster(&self, _team: &str) -> BTreeMap<String, Value> {
        self.0.lock().unwrap().roster.clone()
    }
    fn fresh(&self, _team: &str, task: &str) {
        self.0.lock().unwrap().fresh.push(task.into());
    }
    fn notify_finished(&self) -> bool {
        true
    }
    fn options(&self, _own: &BTreeSet<String>) -> Vec<Value> {
        vec![json!({"id": "full"})]
    }
}

struct Rig {
    s: Scheduler,
    state: Arc<Mutex<State>>,
    clock: Arc<Mutex<Timestamp>>,
    alerts: Arc<Mutex<Vec<(String, String)>>>,
    fail_start: Arc<AtomicBool>,
    _dir: tempfile::TempDir,
}

fn rig(now: &str) -> Rig {
    let dir = tempfile::tempdir().unwrap();
    let state = Arc::new(Mutex::new(State::default()));
    state.lock().unwrap().roster = BTreeMap::from([
        ("sched-lead".to_string(), json!({"parent": null})),
        ("sched-builder".to_string(), json!({"parent": "sched-lead"})),
    ]);
    let (alerts, fail_start) = (Arc::new(Mutex::new(vec![])), Arc::new(AtomicBool::new(false)));
    let (st, fail) = (state.clone(), fail_start.clone());
    let start: Box<StartTeam> = Box::new(move |project, task, roles, _practices, name| {
        if fail.load(Ordering::SeqCst) {
            return Err(BrainError::new("The project folder is gone"));
        }
        st.lock().unwrap().started.push((project.into(), task.into(), roles.to_vec(), name.map(String::from)));
        Ok(json!({}))
    });
    let st = state.clone();
    let starting: Box<dyn Fn() -> Vec<Value> + Send + Sync> = Box::new(move || st.lock().unwrap().start_log.clone());
    let al = alerts.clone();
    let announce: Box<Notify> = Box::new(move |t, b| al.lock().unwrap().push((t.into(), b.into())));
    let mut s = Scheduler::build(
        Box::new(Fake(state.clone())),
        start,
        starting,
        Some(announce),
        dir.path().join("schedules.json"),
    );
    let clock = Arc::new(Mutex::new(ts(now)));
    let c = clock.clone();
    s.now = Box::new(move || *c.lock().unwrap());
    s.tz = TimeZone::UTC;
    Rig { s, state, clock, alerts, fail_start, _dir: dir }
}

impl Rig {
    fn at(&self, t: &str) {
        *self.clock.lock().unwrap() = ts(t);
    }
    fn add(&self) -> Value {
        let rows = self.s.save(
            "Night run",
            "/work/app",
            "full",
            "  Fix the bugs ",
            &json!({"kind": "day", "at": "02:00"}),
            None,
        );
        rows.unwrap()[0].clone()
    }
    fn item(&self) -> Value {
        load_from(&self.s.file).remove(0)
    }
    fn actions(&self) -> Vec<String> {
        self.state.lock().unwrap().records.iter().map(|r| r.0.clone()).collect()
    }
}

#[test]
fn saving_validates_each_field_and_names_the_team_once() {
    let r = rig("2026-11-21T10:00:00Z");
    let every = json!({"kind": "day", "at": "02:00"});
    let err = |name: &str, project: &str, preset: &str, task: &str| {
        r.s.save(name, project, preset, task, &every, None).unwrap_err().0
    };
    assert_eq!(err("  ", "/p", "full", "t"), "name: Give it a name.");
    assert_eq!(err("n", "/p", "full", " "), "task: Say what the team should do.");
    assert_eq!(err("n", "/nope", "full", "t"), "project: Not a project.");
    assert_eq!(err("n", "/p", "weird", "t"), "preset: Choose a team.");
    assert_eq!(err("n", "/p", "saved:broken", "t"), "preset: This team can no longer be loaded: bad");
    assert_eq!(
        r.s.save("n", "/p", "full", "t", &json!({"kind": "day"}), None).unwrap_err().0,
        "every: Give a time such as 02:00."
    );
    assert!(load_from(&r.s.file).is_empty());

    let row = r.add();
    assert_eq!(
        (row["team"].as_str(), row["task"].as_str(), row["project_name"].as_str()),
        (Some("night-run"), Some("Fix the bugs"), Some("app"))
    );
    assert_eq!(row["state"], "waiting");
    assert_eq!(row["line"], "Next: tomorrow at 02:00.");
    assert_eq!(row["who"], "Claude Code, Sonnet");
    r.s.save("Night   run", "/work/app", "full", "again", &every, None).unwrap();
    let teams: Vec<String> = load_from(&r.s.file).iter().map(|s| s["team"].as_str().unwrap().into()).collect();
    assert_eq!(teams, ["night-run", "night-run-2"]);
    assert_eq!(r.actions(), ["schedule_set", "schedule_set"]);
}

#[test]
fn editing_keeps_the_team_and_restarts_the_count_only_for_a_new_time() {
    let r = rig("2026-11-21T10:00:00Z");
    let row = r.add();
    let id = row["id"].as_str().unwrap();
    r.at("2026-11-21T12:00:00Z");
    r.s.save("Night run", "/work/app", "full", "new task", &json!({"kind": "day", "at": "02:00"}), Some(id)).unwrap();
    assert_eq!(r.item()["created"], "2026-11-21T10:00:00Z");
    assert_eq!(r.item()["task"], "new task");
    r.s.save("Night run", "/work/app", "full", "new task", &json!({"kind": "day", "at": "05:00"}), Some(id)).unwrap();
    assert_eq!(
        (r.item()["created"].as_str(), r.item()["handled"].as_str()),
        (Some("2026-11-21T12:00:00Z"), Some("2026-11-21T12:00:00Z"))
    );
    assert_eq!(r.item()["team"], "night-run");
    assert_eq!(
        r.s.save("x", "/work/app", "full", "t", &json!({"kind": "day", "at": "05:00"}), Some("gone")).unwrap_err().0,
        "name: This schedule was removed."
    );
}

#[test]
fn a_time_missed_while_asleep_runs_once() {
    let r = rig("2026-11-21T10:00:00Z");
    r.add();
    r.at("2026-11-21T01:59:00Z"); // before the first time: nothing
    r.s.tick();
    assert!(r.state.lock().unwrap().started.is_empty());
    r.at("2026-11-25T09:00:00Z"); // asleep for four nights
    r.s.tick();
    let state = r.state.lock().unwrap();
    assert_eq!(state.started.len(), 1);
    let (project, task, roles, name) = &state.started[0];
    assert_eq!((project.as_str(), name.as_deref()), ("/work/app", Some("night-run")));
    assert!(task.starts_with("Fix the bugs\n\nThis is a scheduled run that nobody watches live."));
    assert_eq!(roles[0]["name"], "night-run-lead");
    assert_eq!(state.fresh, ["Fix the bugs"]);
    drop(state);
    let s = r.item();
    assert_eq!(
        (s["running"].clone(), s["handled"].clone(), s["last"]["result"].clone()),
        (json!(true), json!("2026-11-25T02:00:00Z"), json!("started"))
    );
    assert_eq!(s["review_role"], "night-run-builder");
    r.s.tick(); // the same slot is not handled twice
    assert_eq!(r.state.lock().unwrap().started.len(), 1);
}

#[test]
fn a_run_still_going_at_the_next_time_is_skipped_and_said_so() {
    let r = rig("2026-11-21T10:00:00Z");
    r.add();
    r.at("2026-11-22T02:00:30Z");
    r.s.tick();
    r.state.lock().unwrap().live.insert("night-run".into());
    r.at("2026-11-23T02:00:30Z");
    r.s.tick();
    assert_eq!(r.state.lock().unwrap().started.len(), 1);
    assert_eq!(r.actions(), ["schedule_set", "schedule_start", "schedule_skip"]);
    let rows = r.s.rows();
    assert_eq!(rows[0]["state"], "running");
    assert_eq!(rows[0]["note"], "Skipped 02:00: the last run was still going.");
    assert_eq!(rows[0]["line"], "Running now, started 02:00. Its agents stop when they say they are done.");
}

#[test]
fn only_the_top_agent_ends_a_run_and_the_next_tick_stops_the_team() {
    let r = rig("2026-11-21T10:00:00Z");
    r.add();
    assert_eq!(r.s.done("night-run", "sched-lead"), None); // nothing running yet
    r.at("2026-11-22T02:00:30Z");
    r.s.tick();
    r.state.lock().unwrap().live.insert("night-run".into());
    assert_eq!(r.s.done("night-run", "sched-builder"), None);
    assert_eq!(r.s.done("night-run", "stranger"), None);
    assert_eq!(r.s.done("night-run", "sched-lead").as_deref(), Some(ENDS_NOW));
    r.state.lock().unwrap().stop_fails = true;
    r.s.tick(); // a core that doesn't answer leaves the run going
    assert_eq!(r.item()["running"], true);
    r.state.lock().unwrap().stop_fails = false;
    r.s.tick();
    assert_eq!(r.state.lock().unwrap().stops, ["night-run"]);
    assert_eq!(r.item()["running"], false);
    assert_eq!(r.item()["last"]["result"], "finished");
    assert!(
        r.alerts.lock().unwrap()[0].1.starts_with("Its top agent said the work is done, so its 2 agents were stopped.")
    );
    assert!(r.s.rows()[0]["line"].as_str().unwrap().starts_with("Last run today at 02:00 finished"));
}

#[test]
fn a_start_that_fails_is_recorded_and_tried_again_at_the_next_time() {
    let r = rig("2026-11-21T10:00:00Z");
    r.add();
    r.fail_start.store(true, Ordering::SeqCst);
    r.at("2026-11-22T02:00:30Z");
    r.s.tick();
    let s = r.item();
    assert_eq!((s["running"].clone(), s["last"]["result"].clone()), (json!(false), json!("failed")));
    assert_eq!(s["last"]["detail"], "The project folder is gone");
    assert!(r.state.lock().unwrap().records.last().unwrap().2);
    assert_eq!(r.alerts.lock().unwrap()[0].0, "Couldn't start Night run");
    assert_eq!(
        r.s.rows()[0]["line"],
        "Couldn’t start today at 02:00: The project folder is gone. Next: tomorrow at 02:00."
    );
    let id = s["id"].as_str().unwrap().to_string();
    r.fail_start.store(false, Ordering::SeqCst);
    r.s.run_now(&id).unwrap();
    assert_eq!(r.item()["running"], true);
    r.state.lock().unwrap().live.insert("night-run".into());
    assert_eq!(r.s.run_now(&id).unwrap_err().0, "run: It is running now.");
}

#[test]
fn agents_that_never_start_or_end_early_are_told() {
    let r = rig("2026-11-21T10:00:00Z");
    r.add();
    r.at("2026-11-22T02:00:30Z");
    r.s.tick();
    // Its agents ended before the top one said done.
    r.s.tick();
    assert_eq!(r.item()["last"]["result"], "ended");
    assert_eq!(r.actions().last().unwrap(), "schedule_ended");
    // Agents that never got going: New team's start says why.
    let id = r.item()["id"].as_str().unwrap().to_string();
    r.state.lock().unwrap().start_log = vec![json!({"team": "night-run", "done": true,
        "roles": [{"role": "lead", "state": "exited", "detail": " no login "}, {"role": "builder", "state": "needs-you"}]})];
    r.s.run_now(&id).unwrap();
    r.s.tick();
    assert_eq!(r.item()["last"]["detail"], "lead exited: no login; builder needs-you");
}

#[test]
fn pausing_removing_and_stopping() {
    let r = rig("2026-11-21T10:00:00Z");
    let id = r.add()["id"].as_str().unwrap().to_string();
    r.s.pause(&id, true).unwrap();
    r.at("2026-11-23T03:00:00Z");
    r.s.tick();
    assert!(r.state.lock().unwrap().started.is_empty());
    assert_eq!(r.s.rows()[0]["line"], "Paused. It won’t start until you resume it.");
    r.s.pause(&id, false).unwrap(); // the times it was paused through never run
    r.s.tick();
    assert!(r.state.lock().unwrap().started.is_empty());
    assert_eq!(r.s.stop(&id).unwrap_err().0, "run: This run has already ended.");
    r.at("2026-11-24T02:00:10Z");
    r.s.tick();
    assert_eq!(r.item()["running"], true);
    r.state.lock().unwrap().stop_fails = true;
    assert_eq!(r.s.stop(&id).unwrap_err().0, "run: Couldn't reach its agents. Try again in a moment.");
    r.state.lock().unwrap().stop_fails = false;
    assert_eq!(r.s.stop(&id).unwrap()[0]["result"], "stopped");
    assert_eq!(r.actions().last().unwrap(), "schedule_stop");
    assert_eq!(r.s.run_teams(), ["night-run"]);
    r.s.remove(&id).unwrap();
    assert!(r.s.rows().is_empty() && r.s.run_teams().is_empty());
    assert_eq!(r.s.run_now(&id).unwrap_err().0, "run: This schedule was removed.");
    r.s.tick(); // nothing of it is going, so the removed schedule leaves the file
    assert!(load_from(&r.s.file).is_empty());
}

#[test]
fn the_socket_methods() {
    let r = rig("2026-11-21T10:00:00Z");
    let this = Arc::new(r.s);
    let methods: BTreeMap<_, _> = Scheduler::handlers(this).into_iter().collect();
    assert_eq!(
        methods.keys().copied().collect::<Vec<_>>(),
        ["schedule.list", "schedule.pause", "schedule.remove", "schedule.run_now", "schedule.save", "schedule.stop"]
    );
    let call = |m: &str, p: Value| methods[m](p, &crate::server::Call { by: By::You });
    let saved = call(
        "schedule.save",
        json!({"name": "N", "project": "/p", "preset": "full", "task": "t", "every": {"kind": "hours", "n": 2}}),
    )
    .unwrap();
    assert_eq!(saved[0]["when"], "Every 2 hours");
    let listed = call("schedule.list", json!({"teams": true})).unwrap();
    assert_eq!((listed["schedules"].as_array().unwrap().len(), listed["teams"][0]["id"].clone()), (1, json!("full")));
    assert!(call("schedule.list", json!({})).unwrap().get("teams").is_none());
    let id = saved[0]["id"].as_str().unwrap();
    assert_eq!(call("schedule.pause", json!({"id": id})).unwrap()[0]["paused"], true); // `paused` defaults to true
    assert_eq!(call("schedule.pause", json!({"id": id, "paused": false})).unwrap()[0]["paused"], false);
    assert_eq!(
        call("schedule.save", json!({"name": "", "project": "/p", "preset": "full", "task": "t", "every": {}}))
            .unwrap_err()
            .message,
        "name: Give it a name."
    );
    assert_eq!(call("schedule.remove", json!({"id": id})).unwrap(), json!([]));
}
