use super::*;

fn fixtures() -> Value {
    let path = format!("{}/tests/data/herdr.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// The answers `record/herdr_fixtures.py` gave New team's side with: a fixed list of models, jobs and refusals.
fn env() -> Env {
    let ids = |l: &[&str]| l.iter().map(|s| s.to_string()).collect();
    Env {
        known: BTreeMap::from([("claude".into(), ids(&["fable", "opus", "sonnet", "haiku"])), ("codex".into(), ids(&["gpt-5.5", "gpt-5.4"]))]),
        jobs: [("planner", "Plans."), ("builder", "Builds."), ("reviewer", "Reviews."), ("tester", "Tests.")]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect(),
        kind_brief: Box::new(|k| format!("standard {k} brief")),
        validate: Box::new(|r| if r["name"].as_str().unwrap_or("").contains("refuse") { Err("No refusing here.".into()) } else { Ok(()) }),
        effort_problem: Box::new(|_, e| if ["low", "medium", "high", "xhigh"].contains(&e) { None } else { Some("bad effort".into()) }),
        checkout: Box::new(|cwd| match cwd.split_once("/wt/") {
            Some((main, _)) => (main.to_string(), Some(format!("branch-{}", py_basename(cwd)))),
            None => (cwd.to_string(), None),
        }),
    }
}

fn words_of(v: &Value) -> Vec<Word> {
    v.as_array().unwrap().iter().map(|w| w.as_str().map(String::from)).collect()
}

fn vars_of(v: &Value) -> Vars {
    v.as_object().unwrap().iter().map(|(k, v)| (k.clone(), v.as_str().map(String::from))).collect()
}

/// Python's answers for each command line, shell word, script and saved workspace.
#[test]
fn matches_the_recorded_python_answers() {
    let (data, env) = (fixtures(), env());
    for c in data["agent_roles"].as_array().unwrap() {
        let names: Vec<String> = c["env"].as_array().unwrap().iter().map(|n| n.as_str().unwrap().into()).collect();
        let (role, notes) = agent_role(
            &env,
            c["name"].as_str().unwrap(),
            c["kind"].as_str().unwrap(),
            &words_of(&c["args"]),
            &names,
            c["holds"].as_bool().unwrap(),
            c["base"].as_str().unwrap_or(""),
        );
        assert_eq!(Value::Object(role), c["role"], "role: {c}");
        assert_eq!(json!(notes.iter().map(|(k, a, d)| json!([k, a, d])).collect::<Vec<_>>()), c["notes"], "notes: {c}");
    }
    for c in data["splits"].as_array().unwrap() {
        let (prog, args, env) = split(&words_of(&c["argv"]));
        let got = json!([prog, args, env]);
        assert_eq!(got, c["out"], "{c}");
    }
    for c in data["words"].as_array().unwrap() {
        assert_eq!(json!(words(c["text"].as_str().unwrap(), &vars_of(&c["env"]))), c["out"], "{c}");
    }
    for c in data["scripts"].as_array().unwrap() {
        let got = from_script(&env, c["text"].as_str().unwrap(), &format!("/scripts/{}.sh", c["name"].as_str().unwrap()));
        assert_eq!(got, c["draft"], "script {}", c["name"]);
    }
    for c in data["workspaces"].as_array().unwrap() {
        assert_eq!(agent_panes(&c["workspace"]).len() as u64, c["panes"].as_u64().unwrap());
        assert_eq!(from_workspace(&env, &c["workspace"]), c["draft"], "{c}");
    }
}

fn write(path: &Path, text: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

#[test]
fn finds_saved_workspaces_in_every_session_and_drafts_them() {
    let dir = tempfile::tempdir().unwrap();
    let ws = |id: i64, name: &str| {
        json!({"id": id, "custom_name": name, "identity_cwd": "/w/proj/", "tabs": [{"panes": {"a": {"managed_agent_kind": "claude", "agent_name": "lead", "cwd": "/w/proj"}}}]})
    };
    write(&dir.path().join("session.json"), &json!({"workspaces": [ws(1, "main team"), {"id": 2, "tabs": []}]}).to_string());
    write(&dir.path().join("sessions/work/session.json"), &json!({"workspaces": [ws(7, "")]}).to_string());
    write(&dir.path().join("sessions/broken/session.json"), "{nope");
    write(&dir.path().join("sessions/list/session.json"), "[1]");
    let found = find_in(dir.path());
    assert_eq!(found["unreadable"], Value::Null);
    let teams = found["teams"].as_array().unwrap();
    let rows: Vec<(&str, &str, &str)> = teams.iter().map(|t| (t["id"].as_str().unwrap(), t["label"].as_str().unwrap(), t["where"].as_str().unwrap())).collect();
    assert_eq!(rows, [("saved:default:1", "main team", "Saved in herdr"), ("saved:work:7", "proj", "Saved in herdr, session work")]);
    assert!(teams.iter().all(|t| t["agents"] == 1 && t["running"] == false));

    let draft = draft_with(&env(), "saved:work:7", dir.path()).unwrap();
    assert_eq!(draft["roles"][0]["name"], "lead");
    assert_eq!(draft["project"], "/w/proj");
    assert_eq!(draft_with(&env(), "saved:work:8", dir.path()).unwrap_err().message, "That herdr workspace isn't saved any more.");
    assert_eq!(draft_with(&env(), "saved:nope:7", dir.path()).unwrap_err().message, "That herdr workspace isn't saved any more.");
    assert_eq!(draft_with(&env(), "running:x", dir.path()).unwrap_err().message, "Unknown herdr source 'running:x'.");
}

#[test]
fn drafts_a_script_file_and_reads_briefs_relative_to_the_agents_folder() {
    let dir = tempfile::tempdir().unwrap();
    let work = dir.path().join("work");
    write(&work.join("brief.md"), "  Be careful.\n");
    let script = dir.path().join("start.sh");
    let text = format!("hc run --name lead --cwd {w} -- claude --append-system-prompt-file brief.md --system-prompt-file gone.md \"Go\"\n", w = work.display());
    write(&script, &text);
    let source = format!("script:{}", script.display());
    let got = draft_with(&env(), &source, dir.path()).unwrap();
    assert_eq!(got["roles"][0]["brief"], "Be careful.");
    assert_eq!(got["task"], "Go");
    let notes = got["notes"].as_array().unwrap();
    assert!(notes.iter().any(|n| n["text"] == "Part of the brief of 1 agent couldn't be read, so it is missing here."), "{notes:?}");
    assert_eq!(
        draft_with(&env(), &format!("script:{}/missing.sh", dir.path().display()), dir.path()).unwrap_err().message,
        "That launch script couldn't be read."
    );
    write(&dir.path().join("binary.sh"), "\u{0}");
    std::fs::write(dir.path().join("bad.sh"), [0xff, 0xfe]).unwrap();
    assert!(draft_with(&env(), &format!("script:{}/bad.sh", dir.path().display()), dir.path()).is_err());
}

#[test]
fn python_shell_words_and_lines() {
    assert_eq!(lex("a 'b c' \"d\\\"e\" f\\ g", false, false), (vec!["a", "b c", "d\"e", "f g"].into_iter().map(String::from).collect(), false));
    assert_eq!(lex("a #c\nb", true, false).0, ["a", "b"]);
    assert_eq!(lex("a;b && c", false, true).0, ["a", ";", "b", "&&", "c"]);
    assert_eq!(lex("x \"open", false, false), (vec!["x".to_string()], true));
    assert_eq!(lex("\"\" y", false, false).0, ["", "y"]);
    assert_eq!(splitlines("a\r\nb\u{2028}c\n"), ["a", "b", "c"]);
    assert_eq!((py_dirname("/a/b/"), py_dirname("/a"), py_dirname("a"), py_basename("/a/b/")), ("/a/b".into(), "/".into(), "".into(), "".into()));
    assert_eq!(py_normpath("/a/b/../c/./d//"), "/a/c/d");
    assert_eq!(py_normpath("../x"), "../x");
}
