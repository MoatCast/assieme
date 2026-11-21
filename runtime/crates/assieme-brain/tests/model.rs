//! `src/model.rs` against Python's answers in `tests/data/model.json` (`record/model_cases.py`).
#![cfg(unix)]
use assieme_brain::model::{Session, parse_ts};
use serde_json::{Value, json};

fn data() -> Value {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/model.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn ns(t: Option<jiff::Timestamp>) -> Value {
    json!(t.map(|t| t.as_nanosecond().to_string()))
}

#[test]
fn parse_ts_as_python() {
    for c in data()["parse_ts"].as_array().unwrap() {
        assert_eq!(ns(parse_ts(&c["in"])), c["out"], "{}", c["in"]);
    }
}

#[test]
fn session_wake_and_call_as_python() {
    for c in data()["session"].as_array().unwrap() {
        let mut s = Session { agent: "agent".into(), source: "claude".into(), ..Session::default() };
        for op in c["in"].as_array().unwrap() {
            let ts = |i: usize| parse_ts(&op[i]);
            if op[0] == "wake" {
                s.wake(
                    op[1].as_str().unwrap(),
                    op[2].as_str().unwrap(),
                    op[3].as_str().unwrap(),
                    op[4].as_bool().unwrap(),
                    ts(5),
                    op[6].as_str(),
                );
            } else {
                s.call(op[1].as_i64().unwrap(), op[2].as_i64().unwrap(), ts(3));
            }
        }
        let got = json!({
            "calls": s.calls, "tokens": s.tokens, "output": s.output, "peak": s.peak,
            "samples": s.samples.iter().map(|(t, n)| json!([ns(Some(*t)), n])).collect::<Vec<_>>(),
            "turns": s.turns.iter().map(|t| json!({
                "sender": t.sender, "channel": t.channel, "calls": t.calls, "tokens": t.tokens, "text": t.text,
                "ts": ns(t.ts), "changed": t.changed, "branch": t.branch,
            })).collect::<Vec<_>>(),
        });
        assert_eq!(got, c["out"], "{}", c["in"]);
    }
}

#[test]
fn counter_keeps_first_counted_order_on_ties() {
    let mut c = assieme_brain::model::Counter::default();
    for k in ["b", "a", "b", "c", "a"] {
        c.add(k, 1);
    }
    assert_eq!(c.get(&"b"), 2);
    assert_eq!(c.most_common().iter().map(|(k, _)| *k).collect::<Vec<_>>(), ["b", "a", "c"]);
}
