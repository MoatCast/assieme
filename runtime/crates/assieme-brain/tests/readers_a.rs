//! Session A's readers against Python's answers in `tests/data/readers_a.json` (`record/readers_a.py`): plan
//! limits, the log adapters, status's log reading, recent actions and detect's command readers. Each case's folder
//! tree is rebuilt in a scratch folder with the same file times, and every call uses paths relative to it, as the
//! recording did. One test: it changes the process's folder and environment.
#![cfg(unix)]
use assieme_brain::model::Session;
use assieme_brain::status::Agent;
use assieme_brain::{adapters, detect, limits, live, pyrules, status};
use jiff::Timestamp;
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

fn t(x: Timestamp) -> Value {
    json!(x.as_nanosecond() as f64 / 1e9)
}

fn ot(x: Option<Timestamp>) -> Value {
    x.map_or(Value::Null, t)
}

fn at(seconds: f64) -> Timestamp {
    Timestamp::from_nanosecond((seconds * 1e9).round() as i128).unwrap()
}

fn session(s: &Session) -> Value {
    json!({"agent": s.agent, "source": s.source, "calls": s.calls, "tokens": s.tokens, "output": s.output,
           "peak": s.peak, "cwd": s.cwd,
           "turns": s.turns.iter().map(|u| json!([u.sender, u.channel, u.calls, u.tokens, u.text, ot(u.ts), u.changed, u.branch])).collect::<Vec<_>>(),
           "refusals": s.refusals.0.iter().map(|((l, w), n)| json!([l, w, n])).collect::<Vec<_>>(),
           "rule_notes": s.rule_notes,
           "samples": s.samples.iter().map(|(a, n)| json!([t(*a), n])).collect::<Vec<_>>(),
           "jobs": s.jobs.iter().map(|j| json!([j.id, t(j.start), ot(j.end), j.runs.iter().map(|r| json!([r.0, r.1, r.2])).collect::<Vec<_>>(), j.branch])).collect::<Vec<_>>(),
           "failures": s.failures.iter().map(|(a, sig, n, l)| json!([t(*a), sig, n, l])).collect::<Vec<_>>(),
           "cycles": s.cycles.0.iter().map(|(k, n)| json!([k, n])).collect::<Vec<_>>(),
           "refusal_seconds": s.refusal_seconds.0.iter().map(|(k, n)| json!([k, n])).collect::<Vec<_>>()})
}

fn agent(a: &Agent) -> Value {
    json!({"name": a.name, "source": a.source, "path": a.path, "state": a.state, "last": ot(a.last),
           "turn_start": ot(a.turn_start), "detail": a.detail,
           "pending": a.pending.iter().map(|(k, (n, l, c))| json!([k, n, l, c])).collect::<Vec<_>>(), "cmds": a.cmds,
           "sends": a.sends.iter().map(|(x, y)| json!([t(*x), y])).collect::<Vec<_>>(), "sent_at": ot(a.sent_at),
           "wakes": a.wakes.iter().map(|(x, y)| json!([t(*x), y])).collect::<Vec<_>>(), "waits_on": a.waits_on,
           "peers": a.peers, "thread": a.thread, "parent_thread": a.parent_thread,
           "children": a.children.iter().map(agent).collect::<Vec<_>>(), "cwd": a.cwd, "asked": a.asked,
           "turn_by": a.turn_by, "ask_kind": a.ask_kind, "ask_since": ot(a.ask_since), "context": a.context,
           "refused": a.refused.iter().map(|(x, l, w, ad)| json!([t(*x), l, w, ad])).collect::<Vec<_>>(),
           "context_limit": a.context_limit,
           "heavy": a.heavy.iter().map(|(x, k, f, l, c)| json!([t(*x), k, f, l, c])).collect::<Vec<_>>(),
           "heavy_end": a.heavy_end.iter().map(|(k, v)| json!([k, t(*v)])).collect::<Vec<_>>(),
           "failures": a.failures.iter().map(|(x, s, turn, l)| json!([t(*x), s, ot(*turn), l])).collect::<Vec<_>>(),
           "samples": a.samples.iter().map(|(x, n)| json!([t(*x), n])).collect::<Vec<_>>()})
}

/// Python's form -> the form the Rust keeps: a missing id or tool name is "", a missing label "None"; dict-ordered
/// collections that Rust keeps sorted are sorted on both sides.
fn python(v: &Value, key: &str) -> Value {
    match (key, v) {
        ("pending", Value::Array(rows)) => Value::Array(
            rows.iter()
                .map(|r| {
                    let s = |i: usize, none: &str| r[i].as_str().unwrap_or(none).to_string();
                    json!([s(0, ""), s(1, ""), s(2, "None"), s(3, "")])
                })
                .collect(),
        ),
        ("heavy_end", Value::Array(rows)) => {
            let mut rows: Vec<Value> =
                rows.iter().map(|r| json!([r[0].as_str().unwrap_or(""), r[1].clone()])).collect();
            rows.sort_by_key(|r| r[0].as_str().unwrap_or("").to_string());
            rows.dedup_by(|a, b| a[0] == b[0]);
            Value::Array(rows)
        }
        ("heavy", Value::Array(rows)) => Value::Array(
            rows.iter()
                .map(|r| {
                    let mut r = r.clone();
                    if r[4].is_null() {
                        r[4] = json!("");
                    }
                    r
                })
                .collect(),
        ),
        (_, Value::Object(o)) => Value::Object(o.iter().map(|(k, x)| (k.clone(), python(x, k))).collect()),
        (_, Value::Array(a)) => Value::Array(a.iter().map(|x| python(x, key)).collect()),
        _ => v.clone(),
    }
}

/// Equal, numbers within a millionth (Python keeps some as floats, some as ints); Python's NaN is Rust's null.
fn same(want: &Value, got: &Value) -> bool {
    match (want, got) {
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len() && a.iter().all(|(k, v)| b.get(k).is_some_and(|w| same(v, w)))
        }
        (Value::Array(a), Value::Array(b)) => a.len() == b.len() && a.iter().zip(b).all(|(x, y)| same(x, y)),
        _ => match (pyrules::float(want), pyrules::float(got)) {
            (Some(x), _) if x.is_nan() => got.is_null(),
            (Some(x), Some(y)) => x == y || (x - y).abs() <= 1e-6 * x.abs().max(1.0),
            _ => want == got,
        },
    }
}

/// Order-free compare for lists whose order Python took from the folder listing.
fn sorted(v: Value) -> Value {
    match v {
        Value::Array(mut a) => {
            a.sort_by_key(|x| x.to_string());
            Value::Array(a)
        }
        other => other,
    }
}

fn build(root: &Path, case: &Value) {
    let set_time = |p: &Path, mtime: f64| {
        let f = std::fs::File::open(p).unwrap();
        let time = std::time::UNIX_EPOCH + std::time::Duration::from_secs_f64(mtime);
        f.set_times(std::fs::FileTimes::new().set_modified(time).set_accessed(time)).unwrap();
    };
    for f in case["files"].as_array().unwrap() {
        let path = root.join(f["path"].as_str().unwrap());
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut bytes = vec![];
        for run in f["lines"].as_array().unwrap() {
            let line: Vec<u8> = run[0].as_str().unwrap().chars().map(|c| c as u32 as u8).collect();
            for _ in 0..run[1].as_u64().unwrap() {
                for _ in 0..run[2].as_u64().unwrap() {
                    bytes.extend(&line);
                }
                bytes.push(b'\n');
            }
        }
        std::fs::write(&path, bytes).unwrap();
        set_time(&path, f["mtime"].as_f64().unwrap());
    }
    for db in case["dbs"].as_array().unwrap() {
        let path = root.join(db["path"].as_str().unwrap());
        let sql: Vec<&str> = db["sql"].as_array().unwrap().iter().map(|s| s.as_str().unwrap()).collect();
        rusqlite::Connection::open(&path).unwrap().execute_batch(&sql.join(";\n")).unwrap();
    }
    for d in case["dirs"].as_array().unwrap() {
        let path = root.join(d["path"].as_str().unwrap());
        std::fs::create_dir_all(&path).unwrap();
    }
    for d in case["dirs"].as_array().unwrap() {
        set_time(&root.join(d["path"].as_str().unwrap()), d["mtime"].as_f64().unwrap());
    }
}

fn call(root: &Path, f: &str, a: &Value) -> Value {
    let p = |x: &str| root.join(x);
    let since = || at(a["since"].as_f64().unwrap());
    let show = a["show_text"].as_bool().unwrap_or(false);
    let sessions = |s: Vec<Session>| sorted(Value::Array(s.iter().map(session).collect()));
    let no_rules = |_: &str| -> Vec<(String, String)> { vec![] };
    let now = || at(a["now"].as_f64().unwrap());
    match f {
        "limits.read" => limits::read(&p("claude"), &p("codex"), now()).unwrap(),
        "adapters.claude_code" => {
            sessions(adapters::claude_code(&p("claude"), since(), show, Some(&p("none.json")), None, &[]))
        }
        "adapters.codex" => sessions(adapters::codex(&p("codex"), since(), show)),
        "adapters.gemini" => sessions(adapters::gemini(&p("gemini"), since(), show)),
        "adapters.opencode" => sessions(adapters::opencode(&p("opencode.db"), since(), show)),
        "adapters.box_metrics" => Value::Array(adapters::box_metrics(&p("boxes.jsonl"), since())),
        "status.collect" => {
            let running: Option<BTreeSet<String>> =
                a["running"].as_array().map(|r| r.iter().map(|x| x.as_str().unwrap().into()).collect());
            let hours = a["hours"].as_f64().unwrap_or(12.0);
            let agents =
                status::collect(&p("claude"), &p("codex"), now(), hours, show, running.as_ref(), Some(&no_rules));
            Value::Array(agents.iter().map(agent).collect())
        }
        "status.stalls" => {
            let agents = status::collect(&p("claude"), &p("codex"), now(), 12.0, false, None, Some(&no_rules));
            json!({"stalls": status::stalls(&agents, now()), "queue": status::owner_queue(&agents, now()),
                   "warnings": detect::live_findings(&agents, now())})
        }
        "status.recent_actions" => {
            let n = a["n"].as_u64().unwrap_or(4) as usize;
            let got = status::recent_actions(&p(a["path"].as_str().unwrap()), a["source"].as_str().unwrap(), show, n);
            Value::Array(got.into_iter().map(|(x, y)| json!([t(x), y])).collect())
        }
        "live.recent_actions" => {
            Value::Array(live::recent_actions(Some(&p(a["path"].as_str().unwrap())), a["n"].as_u64().unwrap() as usize))
        }
        other => panic!("no call {other}"),
    }
}

/// Order-free where Python's order came from the folder listing.
fn expected(f: &str, out: &Value) -> Value {
    let out = python(out, "");
    if f.starts_with("adapters.") && f != "adapters.box_metrics" { sorted(out) } else { out }
}

fn strings(data: &Value) -> Vec<String> {
    let s = &data["strings"];
    let text = |c: &Value| c["in"].as_str().unwrap_or("").to_string();
    let mut wrong = vec![];
    let mut check = |what: &str, c: &Value, got: Value| {
        if !same(&c["out"], &got) {
            wrong.push(format!("{what}({:?}): got {got}, Python {}", c["in"], c["out"]));
        }
    };
    for c in s["heavy_runs"].as_array().unwrap() {
        let extra: Vec<String> = c["extra"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().into()).collect();
        let got = detect::heavy_runs(&text(c), &extra).into_iter().map(|r| json!([r.0, r.1, r.2])).collect();
        check("heavy_runs", c, Value::Array(got));
    }
    for c in s["changes_something"].as_array().unwrap() {
        let got = detect::changes_something(c["tool"].as_str(), c["in"].as_str());
        check("changes_something", c, json!(got));
    }
    for c in s["commands_of"].as_array().unwrap() {
        check("commands_of", c, json!(detect::commands_of(c["in"].as_str())));
    }
    for c in s["is_fix_commit"].as_array().unwrap() {
        check("is_fix_commit", c, json!(detect::is_fix_commit(&text(c))));
    }
    for c in s["failure_sig"].as_array().unwrap() {
        check("failure_sig", c, json!(detect::failure_sig(&text(c))));
    }
    for c in s["codex_failed"].as_array().unwrap() {
        check("codex_failed", c, json!(detect::codex_failed(&text(c))));
    }
    for c in s["program"].as_array().unwrap() {
        check("program", c, json!(adapters::program(&text(c))));
    }
    for c in s["command_label"].as_array().unwrap() {
        check("command_label", c, json!(adapters::command_label("Bash", &text(c), c["show_text"].as_bool().unwrap())));
    }
    for c in s["wake_sender"].as_array().unwrap() {
        let (a, b) = adapters::wake_sender(&text(c));
        check("wake_sender", c, json!([a, b]));
    }
    for c in s["text_of"].as_array().unwrap() {
        check("text_of", c, json!(adapters::text_of(&c["in"])));
    }
    wrong
}

#[test]
fn readers_match_python() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/readers_a.json");
    let data = pyrules::json_loads(&std::fs::read_to_string(path).unwrap()).unwrap();
    let scratch = std::env::temp_dir().join(format!("readers-a-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    std::fs::create_dir_all(scratch.join("state")).unwrap();
    // SAFETY: this test binary has one test, so nothing else reads the environment meanwhile.
    unsafe {
        std::env::set_var("HOME", "/home/fx");
        std::env::set_var("XDG_STATE_HOME", scratch.join("state"));
    }
    std::env::set_current_dir(&scratch).unwrap();

    let mut wrong = strings(&data);
    let (mut calls, mut cases) = (0, 0);
    for case in data["cases"].as_array().unwrap() {
        let root = PathBuf::from("fx").join(case["name"].as_str().unwrap());
        build(&root, case);
        cases += 1;
        for c in case["calls"].as_array().unwrap() {
            calls += 1;
            let f = c["fn"].as_str().unwrap();
            let want = &c["out"];
            let Some(ok) = want.get("ok") else {
                wrong.push(format!("{} {f}: Python raised {}", case["name"], want["error"]));
                continue;
            };
            let got = call(&root, f, &c["args"]);
            let ok = expected(f, ok);
            if !same(&ok, &got) {
                wrong.push(format!("{} {f} {}:\n  got    {got}\n  Python {ok}", case["name"], c["args"]));
            }
        }
    }
    let _ = std::fs::remove_dir_all(&scratch);
    assert!(wrong.is_empty(), "{} differ ({cases} cases, {calls} calls):\n{}", wrong.len(), wrong.join("\n"));
}
