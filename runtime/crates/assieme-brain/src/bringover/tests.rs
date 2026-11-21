use super::*;

fn fixtures() -> Value {
    let path = format!("{}/tests/data/bringover.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn write(path: &Path, bytes: &[u8]) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, bytes).unwrap();
}

fn hex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

fn found(v: &Value) -> Found {
    serde_json::from_value::<serde_json::Map<String, Value>>(v.clone())
        .map(|m| Found {
            tool: m["tool"].as_str().unwrap().into(),
            project: m["project"].as_str().unwrap().into(),
            name: m["name"].as_str().unwrap().into(),
            folder: m["folder"].as_str().map(String::from),
            branch: m["branch"].as_str().unwrap().into(),
            ahead: m["ahead"].as_i64().unwrap(),
            dirty: m["dirty"].as_bool().unwrap(),
            kind: m["kind"].as_str().map(String::from),
            model: m["model"].as_str().map(String::from),
            session: m["session"].as_str().map(String::from),
            running_elsewhere: m["running_elsewhere"].as_bool().unwrap(),
            notes: m["notes"].as_array().unwrap().iter().map(|n| n.as_str().unwrap().into()).collect(),
        })
        .unwrap()
}

/// What Python's readers gave for each of the other tools' files and for the sheet's words.
#[test]
fn matches_the_recorded_python_answers() {
    let data = fixtures();
    // agent history
    let dir = tempfile::tempdir().unwrap();
    let h = &data["history"];
    let now_s = h["now"].as_i64().unwrap();
    for f in h["files"].as_array().unwrap() {
        let path = dir.path().join(f["path"].as_str().unwrap());
        write(
            &path,
            &f.get("hex")
                .map_or_else(|| f["content"].as_str().unwrap().as_bytes().to_vec(), |x| hex(x.as_str().unwrap())),
        );
        let at = std::time::UNIX_EPOCH + Duration::from_secs((now_s - f["age_days"].as_i64().unwrap() * 86400) as u64);
        std::fs::File::options().write(true).open(&path).unwrap().set_modified(at).unwrap();
    }
    let now = Timestamp::from_second(now_s).unwrap();
    let mut got = history(&dir.path().join("claude"), &dir.path().join("codex"), now, DAYS);
    got.sort_by(|a, b| (&a.kind, &a.session, &a.cwd).cmp(&(&b.kind, &b.session, &b.cwd)));
    assert_eq!(serde_json::to_value(&got).unwrap(), h["out"]);

    for c in data["squad"].as_array().unwrap() {
        let home = tempfile::tempdir().unwrap();
        if let Some(state) = c["state"].as_str() {
            write(&home.path().join(".claude-squad/state.json"), state.as_bytes());
        }
        let (items, problem) = read_claude_squad(home.path());
        assert_eq!(json!({"found": items, "problem": problem}), c["out"], "{}", c["state"]);
    }

    for c in data["vibe"].as_array().unwrap() {
        let home = tempfile::tempdir().unwrap();
        let db = home.path().join("Library/Application Support/ai.bloop.vibe-kanban/db.v2.sqlite");
        match &c["sql"] {
            Value::String(sql) => {
                std::fs::create_dir_all(db.parent().unwrap()).unwrap();
                rusqlite::Connection::open(&db).unwrap().execute_batch(sql).unwrap();
            }
            Value::Null => write(&db, &b"this is not a database".repeat(50)),
            _ => {}
        }
        let (items, tasks, scripts, problem) = read_vibe_kanban(home.path());
        assert_eq!(
            json!({"found": items, "tasks": tasks, "scripts": scripts, "problem": problem}),
            c["out"],
            "{}",
            c["name"]
        );
    }

    for c in data["summaries"].as_array().unwrap() {
        let items: Vec<Found> = c["items"].as_array().unwrap().iter().map(found).collect();
        assert_eq!(summary(&items), c["out"].as_str().unwrap());
    }
    for c in data["becomes"].as_array().unwrap() {
        assert_eq!(becomes(&found(&c["item"]), c["n"].as_u64().unwrap() as usize), c["out"].as_str().unwrap());
    }
    for c in data["tools"].as_array().unwrap() {
        assert_eq!(
            tool_of(c["folder"].as_str().unwrap(), c["home"].as_str().unwrap()),
            c["out"].as_str().unwrap(),
            "{c}"
        );
    }
    for c in data["kinds"].as_array().unwrap() {
        assert_eq!(kind_of(c["program"].as_str()), c["out"].as_str(), "{c}");
    }
}
