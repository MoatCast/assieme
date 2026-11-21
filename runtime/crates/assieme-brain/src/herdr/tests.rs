use super::*;

fn fixtures() -> Value {
    let path = format!("{}/tests/data/herdr.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// The answers `record/herdr_fixtures.py` gave New team's side with: a fixed list of models, jobs and refusals.
fn env() -> Env {
    let ids = |l: &[&str]| l.iter().map(|s| s.to_string()).collect();
    Env {
        known: BTreeMap::from([
            ("claude".into(), ids(&["fable", "opus", "sonnet", "haiku"])),
            ("codex".into(), ids(&["gpt-5.5", "gpt-5.4"])),
        ]),
        jobs: [("planner", "Plans."), ("builder", "Builds."), ("reviewer", "Reviews."), ("tester", "Tests.")]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect(),
        kind_brief: Box::new(|k| format!("standard {k} brief")),
        validate: Box::new(|r| {
            if r["name"].as_str().unwrap_or("").contains("refuse") { Err("No refusing here.".into()) } else { Ok(()) }
        }),
        effort_problem: Box::new(|_, e| {
            if ["low", "medium", "high", "xhigh"].contains(&e) { None } else { Some("bad effort".into()) }
        }),
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
        let got =
            from_script(&env, c["text"].as_str().unwrap(), &format!("/scripts/{}.sh", c["name"].as_str().unwrap()));
        assert_eq!(got, c["draft"], "script {}", c["name"]);
    }
    for c in data["workspaces"].as_array().unwrap() {
        assert_eq!(agent_panes(&c["workspace"]).len() as u64, c["panes"].as_u64().unwrap());
        assert_eq!(from_workspace(&env, &c["workspace"]), c["draft"], "{c}");
    }
}
