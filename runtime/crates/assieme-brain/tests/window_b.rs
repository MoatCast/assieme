//! Session B's readers against Python's answers in `tests/data/window_b.json` (`record/window_b.py`): the screen
//! readers of `prompts` and the Activity rows. The HH:MM columns follow the machine's time zone and are left out.
#![cfg(unix)]
use assieme_brain::{activity, prompts, pyrules};
use serde_json::{Value, json};

#[test]
fn screens_match_python() {
    let data = load();
    for case in data["screens"].as_array().unwrap() {
        let screen = case["in"].as_str().unwrap();
        let menu = prompts::menu(screen);
        let labels: serde_json::Map<String, Value> =
            prompts::permission_labels(&menu).into_iter().map(|(n, l)| (n.to_string(), json!(l))).collect();
        let got = json!({"in": screen, "trust": prompts::trust_keys(screen), "first_run": prompts::first_run_keys(screen),
                         "hook_review": prompts::hook_review(screen), "menu": menu, "labels": labels,
                         "context": prompts::menu_context(screen), "question": prompts::question(screen)});
        assert_eq!(&got, case, "screen {screen:?}");
    }
}

#[test]
fn activity_rows_match_python() {
    let data = &load()["activity"];
    let log = std::env::temp_dir().join(format!("window-b-{}.jsonl", std::process::id()));
    let lines: Vec<String> = data["entries"].as_array().unwrap().iter().map(Value::to_string).collect();
    std::fs::write(&log, lines.join("\n")).unwrap();
    let now = data["now"].as_str().unwrap().parse().unwrap();
    for case in data["cases"].as_array().unwrap() {
        let got = activity::list_rows(case["team"].as_str(), case["filter"].as_str().unwrap(), Some(&log), Some(now));
        let (got, want) = (got["rows"].as_array().unwrap(), case["rows"].as_array().unwrap());
        assert_eq!(got.len(), want.len(), "{case}");
        for (g, w) in got.iter().zip(want) {
            let (mut g, mut w) = (g.clone(), w.clone());
            for row in [&mut g, &mut w] {
                for k in ["at", "day", "outcome_at", "check_until"] {
                    row[k] = Value::Null;
                }
                row["ok"] = json!(pyrules::truthy(&row["ok"])); // Python passes a stored 0 through
            }
            assert_eq!(g, w, "team {} filter {}", case["team"], case["filter"]);
        }
    }
    let _ = std::fs::remove_file(log);
}

fn load() -> Value {
    pyrules::json_loads(
        &std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/window_b.json")).unwrap(),
    )
    .unwrap()
}
