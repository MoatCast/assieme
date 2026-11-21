//! Money and limits: a daily token limit pauses once, the person's extra raises today's allowance, and a bad limit is
//! refused. One test: it sets the process environment.
#![cfg(unix)]
use assieme_brain::{budget, runtime::Runtime};
use jiff::Timestamp;
use serde_json::json;
use std::collections::BTreeMap;

#[test]
fn limits_pause_once_and_extras_raise_the_allowance() {
    let dir = std::path::PathBuf::from(format!("/var/tmp/brain-budget-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    unsafe { std::env::set_var("XDG_STATE_HOME", &dir) };
    let (log, now) = (dir.join("actions.jsonl"), Timestamp::now());
    let rt = Runtime::new("t", Some(dir.join("no-core.sock"))); // every send fails: the state must still be right
    let set =
        |limits, at_limit| budget::set_limits("t", &limits, at_limit, &rt, &BTreeMap::new(), 0, Some(now), Some(&log));

    for bad in [json!({"*": -1}), json!({"*": true}), json!({"*": "9"})] {
        assert_eq!(set(bad, "pause").unwrap_err().message, "A limit is a number of tokens above zero.");
    }
    assert!(set(json!({}), "later").is_err());
    set(json!({"*": 1000, "dev": 400, "qa": 0, "gone": null}), "pause").unwrap();
    assert_eq!(budget::teams().into_iter().collect::<Vec<_>>(), ["t"]);

    let built = json!({"budget": {"used": 1200, "agents": [{"role": "dev", "used": 500}, {"role": "qa", "used": 700}]},
                       "panes": [{"role": "dev", "state": "idle", "address": "a1"}, {"role": "qa", "state": "asking", "address": "a2"}]});
    assert_eq!(budget::check("t", &built, &rt, now, None, Some(&log)).len(), 2, "the team and dev");
    assert!(budget::check("t", &built, &rt, now, None, Some(&log)).is_empty(), "a pause is made once");
    assert!(budget::hold("t", "dev", "qa", "hello") && !budget::hold("t", "nobody", "qa", "hello"));

    let used = BTreeMap::from([("dev".to_string(), 500), ("qa".to_string(), 700)]);
    let view = budget::view("t", &used, 1200, now);
    let team = view["asks"].as_array().unwrap().iter().find(|a| a["scope"] == "*").unwrap().clone();
    assert_eq!((team["add"].as_i64(), team["label"].as_str()), (Some(1200 - 1000 + 250), Some("Add 250 and carry on")));

    budget::resume("t", "*", 450, &rt, Some(now), "you", Some(&log));
    let view = budget::view("t", &used, 1200, now);
    assert_eq!(
        (view["allowed"].as_i64(), view["asks"].as_array().unwrap().len()),
        (Some(1450), 1),
        "dev's own limit still holds it"
    );
    assert_eq!(budget::resume("t", "*", 0, &rt, Some(now), "you", Some(&log))["ok"], false, "already carried on");

    set(json!({"*": 1000, "dev": 400}), "tell").unwrap(); // "tell" lets every pause go
    assert!(budget::view("t", &used, 1200, now)["asks"].as_array().unwrap().is_empty());
    assert_eq!(budget::check("t", &built, &rt, now, None, Some(&log)).len(), 1, "dev only: the team has today's extra");
    assert!(budget::check("t", &built, &rt, now, None, Some(&log)).is_empty());
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn words_round_as_the_app_writes_them() {
    let got: Vec<String> = [999, 1500, 2500, 4_200_000, 1_000_000, 999_999].map(budget::words).into();
    assert_eq!(got, ["999", "2K", "2K", "4.2M", "1M", "1000K"]);
}
