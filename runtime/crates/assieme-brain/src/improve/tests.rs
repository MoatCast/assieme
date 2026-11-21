use super::*;
use std::cell::RefCell;

fn row(role: &str, cwd: &str, address: &str) -> Value {
    json!({"address": address, "state": "live", "cwd": cwd, "labels": {"assieme.role": role}})
}

fn on() -> Vec<Group> {
    vec![
        ("messages".into(), "Fewer messages".into(), vec!["Send less.".into(), "Batch them.".into()]),
        ("stopping".into(), "Stop rules".into(), vec!["Two attempts.".into()]),
        ("machine".into(), "Light on the Mac".into(), vec!["One suite at a time.".into()]),
    ]
}

struct World {
    report: Value,
    live: Vec<Value>,
    entries: Vec<Value>,
    undone: BTreeSet<String>,
    on: Vec<Group>,
}

impl Default for World {
    fn default() -> World {
        World { report: json!({"edges": [], "send_less": [], "findings": [], "refusals": []}), live: vec![], entries: vec![], undone: BTreeSet::new(), on: vec![] }
    }
}

fn env() -> Env {
    Env {
        known: BTreeMap::new(),
        jobs: [("planner", "Plans."), ("builder", "Builds."), ("reviewer", "Reviews.")].map(|(k, v)| (k.to_string(), v.to_string())).into(),
        kind_brief: Box::new(|k| format!("standard {k}")),
        validate: Box::new(|_| Ok(())),
        effort_problem: Box::new(|_, _| None),
        checkout: Box::new(|c| (c.to_string(), None)),
    }
}

fn run<T>(w: &World, f: impl FnOnce(&Ports) -> T) -> T {
    let (report, live) = (w.report.clone(), w.live.clone());
    let ports = Ports {
        on: w.on.clone(),
        entries: w.entries.clone(),
        undone: w.undone.clone(),
        report: &move |_| report.clone(),
        live_findings: &move |_| live.clone(),
        env: &env,
    };
    f(&ports)
}

fn got(rows: Vec<Value>, terminals: &[Value]) -> Got {
    team_of(rows, vec![], true, terminals).unwrap()
}

fn ids(found: &Value) -> Vec<&str> {
    found["findings"].as_array().unwrap().iter().map(|f| f["id"].as_str().unwrap()).collect()
}

#[test]
fn a_team_started_in_assieme_or_with_no_live_agent_is_not_improved() {
    let rows = vec![row("a", "/w/a", "log:a")];
    assert!(team_of(rows.clone(), vec![], false, &[]).is_none());
    assert!(team_of(vec![json!({"address": "x", "state": "stopped", "labels": {"assieme.role": "a"}})], vec![], true, &[]).is_none());
    let out = run(&World::default(), |p| findings(p, "t", None));
    assert_eq!(out, json!({"team": "t", "found": false, "findings": [], "in_place": []}));
}

#[test]
fn agents_in_one_folder_are_a_review_finding_that_needs_a_restart() {
    let w = World::default();
    let g = got(vec![row("a", "/w/x", "log:a"), row("b", "/w/x", "log:b"), row("c", "/w/y", "log:c")], &[]);
    let out = run(&w, |p| findings(p, "t", Some(&g)));
    let f = &out["findings"][0];
    assert_eq!((f["id"].as_str(), f["kind"].as_str(), f["restart"].as_bool()), (Some("folder:a"), Some("review"), Some(true)));
    assert_eq!(f["detail"], "a and b work in the same folder, so they can edit the same files. Give each its own branch.");
    assert_eq!(f["draft"], Value::Null);
    assert_eq!(out["in_place"], json!([]));
    let apart = got(vec![row("a", "/w/x", "log:a"), row("c", "/w/y", "log:c")], &[]);
    assert_eq!(run(&w, |p| findings(p, "t", Some(&apart)))["in_place"], json!(["each agent has its own folder"]));
}

#[test]
fn terminals_show_wide_permissions_and_reviewers_that_can_edit() {
    let w = World::default();
    let terminals = [
        json!({"address": "t1", "name": "builder-1", "cwd": "/w/a", "argv": ["claude", "--dangerously-skip-permissions"]}),
        json!({"address": "t2", "name": "reviewer", "cwd": "/w/b", "argv": ["codex", "--yolo"]}),
        json!({"address": "t3", "name": "planner", "cwd": "/w/c", "argv": ["claude", "--permission-mode", "plan"]}),
        json!({"address": "t4", "name": "shell", "cwd": "/w/d", "argv": ["bash"]}),
    ];
    let g = got(vec![row("builder-1", "/w/a", "t1"), row("reviewer", "/w/b", "t2"), row("planner", "/w/c", "t3"), row("shell", "/w/d", "t4")], &terminals);
    let out = run(&w, |p| findings(p, "t", Some(&g)));
    assert_eq!(ids(&out), ["permissions:builder-1", "permissions:reviewer", "reviewer:reviewer"]);
    assert_eq!(out["findings"][0]["detail"], "builder-1 runs with permission checks skipped. Start it again with safe permissions: its own copy, and no pushes.");
    assert_eq!(out["findings"][2]["detail"], "reviewer reviews or plans but can edit files. Start it again read-only.");
    assert_eq!(out["in_place"], json!(["each agent has its own folder"]));
    // the fix is a draft of the same agents, which only a team with an import source has
    let mut with_source = got(vec![row("builder-1", "/w/a", "t1")], &terminals[..1]);
    with_source.source = Some("saved:default:1".into());
    assert_eq!(run(&w, |p| findings(p, "t", Some(&with_source)))["findings"][0]["draft"], "saved:default:1");
    // safe command lines, and a planner that is read-only, are said to be in place
    let safe = [json!({"address": "t3", "name": "planner", "cwd": "/w/c", "argv": ["claude", "--permission-mode", "plan"]})];
    let ok = got(vec![row("planner", "/w/c", "t3")], &safe);
    assert_eq!(run(&w, |p| findings(p, "t", Some(&ok)))["in_place"], json!(["safe permissions", "read-only reviewers"]));
}

#[test]
fn what_todays_sessions_show_becomes_rules_to_give_and_things_to_review() {
    let g = got(vec![row("a", "/w/a", "log:a"), row("b", "/w/b", "log:b")], &[]);
    let w = World {
        on: on(),
        report: json!({"edges": [{"from": "a", "to": "b"}], "findings": [{"kind": "loop", "agent": "a", "what": "a repeated one command"}, {"kind": "review", "agent": "a", "what": "again"}],
                       "send_less": [{"from": "a", "to": "b", "wakes": 8, "empty": 6, "share": 0.125}],
                       "refusals": [{"agent": "b", "command": "rm -rf x", "count": 3, "why": "the rules"}, {"agent": "b", "command": "ls", "count": 2, "why": "x"},
                                    {"agent": "z", "command": "ls", "count": 9, "why": "x"}]}),
        live: vec![json!({"kind": "repeats", "agent": "b", "what": "b ran the whole suite twice"}), json!({"kind": "other", "agent": "b", "what": "x"})],
        ..World::default()
    };
    let out = run(&w, |p| findings(p, "t", Some(&g)));
    // None of these agents can be reached (they are log rows), so the rules to give are for the person to paste.
    assert_eq!(ids(&out), ["messages:a", "stopping:a", "machine:b", "refused:b:rm -rf x", "practices"]);
    let messages = &out["findings"][0];
    assert_eq!(messages["kind"], "review");
    assert_eq!(messages["detail"], "a woke b 8 times today and 6 of those did no work (12% of today’s tokens). Give a the rules for fewer, cheaper messages. Assieme can’t send it messages, because it runs in a terminal Assieme doesn’t control, so paste these rules into the next message.");
    assert_eq!(messages["copy"], "assieme: your owner added these team practices. From your next task, follow them:\n- Send less.\n- Batch them.");
    assert!(out["findings"][1]["detail"].as_str().unwrap().starts_with("a repeated one command. Add the two-attempt limit and a notification when it stops. Assieme can’t send it"));
    assert!(out["findings"][2]["detail"].as_str().unwrap().starts_with("b ran the whole suite twice. Give it the rules that keep your Mac usable. Assieme can’t send it"));
    assert_eq!(out["findings"][3]["detail"], "b tried `rm -rf x` 3 times and the rules refused it each time. Check whether it should be allowed, or change the brief so it stops trying.");
    let practices = &out["findings"][4];
    assert!(practices["detail"].as_str().unwrap().starts_with("a and b started outside Assieme, so they don’t follow the 3 practice groups you have on. Assieme can’t send them messages, because they run in"));
    assert_eq!(practices["to"], json!(["a", "b"]));
    assert_eq!(out["in_place"], json!(["each agent has its own folder"]));
}

#[test]
fn what_was_already_given_is_not_offered_again_and_an_undo_offers_it_back() {
    let g = got(vec![row("a", "/w/a", "t1")], &[json!({"address": "t1", "name": "a", "cwd": "/w/a", "argv": ["claude"]})]);
    let given = |id: &str| json!({"id": id, "action": "improve", "team": "t", "ok": true, "sent": [["a", "t1", "/w/a", "messages"], ["a", "t1", "/w/a", "stopping"], ["a", "t1", "/w/a", "machine"]]});
    let mut w = World { on: on(), entries: vec![given("e1")], ..World::default() };
    let out = run(&w, |p| findings(p, "t", Some(&g)));
    assert_eq!(out["findings"], json!([]));
    assert_eq!(out["in_place"], json!(["safe permissions", "team practices"]));
    w.undone.insert("e1".into());
    assert_eq!(ids(&run(&w, |p| findings(p, "t", Some(&g)))), ["practices"]);
    // another team's row, and one that failed, give nothing
    w.undone.clear();
    w.entries = vec![json!({"id": "e2", "action": "improve", "team": "other", "sent": [["a", "t1", "/w/a", "messages"]]}),
                     json!({"id": "e3", "action": "improve", "team": "t", "ok": false, "sent": [["a", "t1", "/w/a", "messages"]]})];
    assert_eq!(delivered("t", &w.entries, &w.undone), BTreeSet::new());
}

#[test]
fn applying_holds_one_note_per_agent_and_records_one_row_per_finding() {
    let terminals = [json!({"address": "t1", "name": "a", "cwd": "/w/a", "argv": ["claude"]}), json!({"address": "t2", "name": "b", "cwd": "/w/b", "argv": ["claude"]})];
    let g = got(vec![row("a", "/w/a", "t1"), row("b", "/w/b", "t2")], &terminals);
    let w = World {
        on: on(),
        report: json!({"edges": [], "send_less": [{"from": "a", "to": "b", "wakes": 3, "empty": 3, "share": 0.5}], "findings": [], "refusals": []}),
        entries: vec![json!({"id": "old", "action": "improve", "team": "t", "ok": true, "sent": [["b", "t2", "/w/b", "messages"]]})],
        ..World::default()
    };
    let (held, recorded) = (RefCell::new(vec![]), RefCell::new(vec![]));
    let out = run(&w, |p| {
        apply_with(
            p,
            "t",
            &["practices".to_string(), "messages:a".to_string(), "not-a-finding".to_string()],
            Some(&g),
            Timestamp::from_second(1_800_000_000).unwrap(),
            None,
            &|to, cwd, text| held.borrow_mut().push((to.to_string(), cwd.to_string(), text.to_string())),
            &|r| {
                recorded.borrow_mut().push((r.agent.to_string(), r.reason.to_string(), r.extra.clone()));
                json!({"id": format!("row{}", recorded.borrow().len())})
            },
        )
    });
    assert_eq!(out, json!({"ok": true, "applied": [{"id": "messages:a", "entry": "row1"}, {"id": "practices", "entry": "row2"}]}));
    let held = held.borrow();
    // "messages:a" gives a the messaging rules; "practices" then gives a only the stopping rules, and b both it still lacks
    assert_eq!(held.len(), 3);
    assert!(held[0].2.contains("- Send less.\n- Batch them.") && !held[0].2.contains("Two attempts."), "{}", held[0].2);
    assert!(held[1].2.contains("Two attempts.") && !held[1].2.contains("Send less."), "{}", held[1].2);
    assert_eq!((held[1].0.as_str(), held[2].0.as_str()), ("t1", "t2"));
    assert!(held[2].2.contains("Two attempts."), "b was given the messaging rules before");
    let rows = recorded.borrow();
    assert_eq!(rows[0].0, "a");
    assert_eq!(rows[0].2["title"], "Gave a “Fewer messages”");
    assert_eq!(rows[0].2["sent"], json!([["a", "t1", quiet_real("/w/a"), "messages"]]));
    assert_eq!(rows[1].0, "a and b");
    assert_eq!(rows[1].2["title"], "Gave a and b the team practices");
}

fn quiet_real(p: &str) -> String {
    crate::quiet::real(p)
}

#[test]
fn log_only_agents_cannot_be_sent_anything() {
    let g = got(vec![row("a", "/w/a", "log:a")], &[]);
    let w = World { on: on(), ..World::default() };
    let held = RefCell::new(0);
    let out = run(&w, |p| apply_with(p, "t", &["practices".to_string()], Some(&g), Timestamp::UNIX_EPOCH, None, &|_, _, _| *held.borrow_mut() += 1, &|_| json!({"id": "x"})));
    assert_eq!((out, *held.borrow()), (json!({"ok": true, "applied": []}), 0));
}

#[test]
fn undoing_takes_a_held_note_back_and_tells_an_agent_that_already_has_it() {
    let practices = json!({"groups": [{"id": "messages", "title": "Fewer messages"}, {"id": "stopping", "title": "Stop rules"}]});
    let e = json!({"notes": [["t1", "/w/a", "note one"], ["t2", "/w/b", "note two"]],
                   "sent": [["a", "t1", "/w/a", "messages"], ["a", "t1", "/w/a", "stopping"], ["b", "t2", "/w/b", "messages"], ["b", "t2", "/w/b", "unknown"]]});
    // note one is still held; note two was delivered
    let held = [json!({"to": "t1", "to_cwd": "/w/a", "text": "note one"}), json!({"to": "t1", "to_cwd": "/w/a", "text": "another"})];
    let (dropped, said) = (RefCell::new(vec![]), RefCell::new(vec![]));
    undo_with(&e, &practices, &held, &|n| dropped.borrow_mut().extend(n.iter().cloned()), &|to, cwd, text| said.borrow_mut().push((to.to_string(), cwd.to_string(), text.to_string())));
    assert_eq!(*dropped.borrow(), [held[0].clone()]);
    assert_eq!(
        *said.borrow(),
        [("t2".to_string(), "/w/b".to_string(), "assieme: your owner took back these team practices, so you no longer need to follow them: “Fewer messages”, “unknown”.".to_string())]
    );
}
