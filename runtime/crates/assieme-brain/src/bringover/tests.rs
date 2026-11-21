use super::*;
use std::process::Command;

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
    serde_json::from_value::<serde_json::Map<String, Value>>(v.clone()).map(|m| Found {
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
    }).unwrap()
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
        write(&path, &f.get("hex").map_or_else(|| f["content"].as_str().unwrap().as_bytes().to_vec(), |x| hex(x.as_str().unwrap())));
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
        assert_eq!(json!({"found": items, "tasks": tasks, "scripts": scripts, "problem": problem}), c["out"], "{}", c["name"]);
    }

    for c in data["summaries"].as_array().unwrap() {
        let items: Vec<Found> = c["items"].as_array().unwrap().iter().map(found).collect();
        assert_eq!(summary(&items), c["out"].as_str().unwrap());
    }
    for c in data["becomes"].as_array().unwrap() {
        assert_eq!(becomes(&found(&c["item"]), c["n"].as_u64().unwrap() as usize), c["out"].as_str().unwrap());
    }
    for c in data["tools"].as_array().unwrap() {
        assert_eq!(tool_of(c["folder"].as_str().unwrap(), c["home"].as_str().unwrap()), c["out"].as_str().unwrap(), "{c}");
    }
    for c in data["kinds"].as_array().unwrap() {
        assert_eq!(kind_of(c["program"].as_str()), c["out"].as_str(), "{c}");
    }
}

// --- real git repositories -------------------------------------------------------------------------------------------

fn git(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"])
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// A repository `repo` with one commit on `main`.
fn repo(root: &Path, name: &str) -> PathBuf {
    let dir = root.join(name);
    std::fs::create_dir_all(&dir).unwrap();
    git(&dir, &["init", "-q", "-b", "main"]);
    std::fs::write(dir.join("a.txt"), "a\n").unwrap();
    git(&dir, &["add", "."]);
    git(&dir, &["commit", "-qm", "first"]);
    dir
}

/// A worktree on a new branch, with one commit when `work` says so.
fn worktree(repo: &Path, at: &Path, branch: &str, work: bool) {
    std::fs::create_dir_all(at.parent().unwrap()).unwrap();
    git(repo, &["worktree", "add", "-q", "-b", branch, &at.to_string_lossy()]);
    if work {
        std::fs::write(at.join("new.txt"), branch).unwrap();
        git(at, &["add", "."]);
        git(at, &["commit", "-qm", "work"]);
    }
}

/// Stands in for `scripts::rewrite`: the line as it is, and the variables it names that Assieme doesn't set.
fn rewrite(line: &str) -> (String, Vec<String>) {
    (line.replace("$CONDUCTOR_PORT", "$PORT"), if line.contains("$CONDUCTOR_ROOT") { vec!["$CONDUCTOR_ROOT".into()] } else { vec![] })
}

fn ts() -> Timestamp {
    Timestamp::from_second(1_800_000_000).unwrap()
}

/// A home with a Conductor workspace that has work, one that has none, and the person's own worktree.
struct Home {
    _dir: tempfile::TempDir,
    home: String,
    teams: PathBuf,
    repo: String,
}

fn home() -> Home {
    let dir = tempfile::tempdir().unwrap();
    let root = std::fs::canonicalize(dir.path()).unwrap();
    let repo = repo(&root, "repos/app");
    let h = root.join("home");
    worktree(&repo, &h.join("conductor/workspaces/app/tokyo"), "tokyo", true);
    worktree(&repo, &h.join("conductor/workspaces/app/oslo"), "oslo", false);
    worktree(&repo, &root.join("mine/side"), "side", true);
    write(&repo.join("conductor.json"), br#"{"scripts": {"setup": "npm i", "run": "npm run dev --port $CONDUCTOR_PORT", "archive": "", "other": "x"}}"#);
    Home { home: h.to_string_lossy().into_owned(), teams: root.join("teams"), repo: repo.to_string_lossy().into_owned(), _dir: dir }
}

fn inputs<'a>(h: &'a Home, sessions: Vec<Sess>, procs: Vec<Proc>) -> Inputs<'a> {
    Inputs { home: &h.home, folder: None, teams: &h.teams, now: ts(), sessions: Some(sessions), procs: Some(procs), herdr_teams: Some(vec![]) }
}

/// `find` keeps its latest answer in one place, so tests that read it take turns.
static TURN: Mutex<()> = Mutex::new(());

fn session(cwd: &str, kind: &str, id: &str) -> Sess {
    Sess { kind: kind.into(), cwd: cwd.into(), session: id.into(), model: Some(json!("opus")), at: 1.0 }
}

#[test]
fn finds_a_conductor_workspace_with_work_and_leaves_out_empty_and_own_worktrees() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    let h = home();
    let tokyo = format!("{}/conductor/workspaces/app/tokyo", h.home);
    let found = find_with(&inputs(&h, vec![session(&tokyo, "claude", "sess-1")], vec![]));
    let projects = found["projects"].as_array().unwrap();
    assert_eq!(projects.len(), 1, "{found}");
    let p = &projects[0];
    assert_eq!((p["id"].as_str(), p["tool"].as_str(), p["label"].as_str(), p["count"].as_u64()), (Some(format!("conductor:{}", h.repo).as_str()), Some("conductor"), Some("app"), Some(2)));
    // Oslo has nothing new, so it is listed but not ticked; Tokyo has a commit.
    assert_eq!(p["summary"], "2 workspaces · Claude Code · 1 with unmerged work");
    assert_eq!(p["running"], Value::Null);
    assert_eq!(found["problems"], json!([]));

    let w = workspaces_with(p["id"].as_str().unwrap(), &rewrite).unwrap();
    let rows = w["rows"].as_array().unwrap();
    assert_eq!(rows[0]["name"], "tokyo");
    assert_eq!((rows[0]["ticked"].clone(), rows[0]["becomes"].clone()), (json!(true), json!("Becomes builder-1 · Claude Code, opus · 1 commit not merged")));
    assert_eq!((rows[1]["name"].clone(), rows[1]["ticked"].clone()), (json!("oslo"), json!(false)));
    assert_eq!(rows[1]["note"], "Left out: nothing new on its branch. Tick it to bring it anyway.");
    assert_eq!(w["tool"], "Conductor");
    assert_eq!(w["scripts"].as_array().unwrap().len(), 2);
    assert_eq!(w["scripts"][0], json!({"which": "setup", "line": "npm i", "becomes": "runs in each new agent’s copy"}));
}

#[test]
fn a_worktree_of_another_tool_is_listed_only_when_an_agent_worked_in_it() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    let h = home();
    let side = format!("{}/mine/side", Path::new(&h.repo).parent().unwrap().parent().unwrap().display());
    let none = find_with(&inputs(&h, vec![], vec![]));
    assert!(none["projects"].as_array().unwrap().iter().all(|p| p["tool"] != "other"), "{none}");
    let found = find_with(&inputs(&h, vec![session(&side, "codex", "c-1")], vec![]));
    let other = found["projects"].as_array().unwrap().iter().find(|p| p["tool"] == "other").unwrap();
    assert_eq!(other["summary"], "1 workspace · Codex · 1 with unmerged work");
}

#[test]
fn an_agent_still_running_in_a_folder_is_said_and_skipped_at_start() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    let h = home();
    let tokyo = format!("{}/conductor/workspaces/app/tokyo", h.home);
    let proc = Proc { kind: "claude".into(), cwd: format!("{tokyo}/src") };
    let found = find_with(&inputs(&h, vec![], vec![proc.clone()]));
    let p = &found["projects"][0];
    assert_eq!(p["running"], "Still running in Conductor: stop it there first.");
    let id = p["id"].as_str().unwrap();
    let draft = draft_with(id, &["tokyo".into()], &Deps { kind_brief: &|k| format!("standard {k}"), model_problem: &|_, _| false, rewrite: &rewrite }).unwrap();
    assert_eq!(draft["running"], "Still running in Conductor: stop it there first.");
    let (kept, skipped) = not_running_with(draft["roles"].as_array().unwrap(), "Conductor", &[proc]);
    assert!(kept.is_empty());
    assert_eq!(skipped[0]["reason"], "Still running in Conductor: stop it there first.");
    let (kept, skipped) = not_running_with(draft["roles"].as_array().unwrap(), "Conductor", &[]);
    assert_eq!((kept.len(), skipped.len()), (1, 0));
}

#[test]
fn the_draft_makes_one_builder_per_ticked_workspace_in_the_order_ticked() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    let h = home();
    let tokyo = format!("{}/conductor/workspaces/app/tokyo", h.home);
    let found = find_with(&inputs(&h, vec![session(&tokyo, "codex", "thread-9")], vec![]));
    let id = found["projects"][0]["id"].as_str().unwrap().to_string();
    let deps = Deps { kind_brief: &|k| format!("standard {k} brief"), model_problem: &|_, m| m == "opus", rewrite: &rewrite };
    let d = draft_with(&id, &["tokyo".into(), "oslo".into(), "tokyo".into()], &deps).unwrap();
    let roles = d["roles"].as_array().unwrap();
    assert_eq!(roles.iter().map(|r| r["job"].as_str().unwrap()).collect::<Vec<_>>(), ["tokyo", "oslo"], "each once, in the order ticked");
    assert_eq!((roles[0]["name"].clone(), roles[0]["agent"].clone(), roles[0]["branch"].clone(), roles[0]["level"].clone()), (json!("builder-1"), json!("codex"), json!("tokyo"), json!("safe")));
    assert_eq!(roles[0]["resume"], "thread-9");
    assert_eq!(roles[0]["worktree"], format!("{}/conductor/workspaces/app/tokyo", std::fs::canonicalize(&h.home).unwrap().display()));
    assert!(roles[0].get("model").is_none(), "a model the agent can't use is left out");
    assert_eq!(roles[1]["agent"], "claude");
    assert!(roles[1].get("resume").is_none());
    assert_eq!((d["from"].clone(), d["tool"].clone(), d["label"].clone(), d["task"].clone()), (json!("bringover"), json!("Conductor"), json!("app"), json!("")));
    assert_eq!(d["scripts"], json!({"setup": "npm i", "run": "npm run dev --port $PORT"}));
    let notes: Vec<&str> = d["notes"].as_array().unwrap().iter().map(|n| n["text"].as_str().unwrap()).collect();
    assert!(notes.contains(&"Which agent worked here isn't recorded, so they start with Claude Code. Change it if needed."), "{notes:?}");
    assert!(notes.contains(&"No conversation was found to pick up, so they start a new one with their brief."), "{notes:?}");
    assert!(notes.last().unwrap().starts_with("They start with Assieme's safe permissions"));
    assert_eq!(draft_with(&id, &[], &deps).unwrap_err().message, "Tick at least one workspace to bring over.");
    assert_eq!(draft_with("conductor:/gone", &["x".into()], &deps).unwrap_err().message, "That project isn't in the list any more. Look again.");
}

#[test]
fn a_workspace_already_in_a_saved_team_is_not_listed_again() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    let h = home();
    let tokyo = std::fs::canonicalize(format!("{}/conductor/workspaces/app/tokyo", h.home)).unwrap();
    write(&h.teams.join("mine/team.toml"), format!("name = \"mine\"\n[[role]]\nname = \"b\"\nworktree = \"{}\"\n[[role]]\nname = \"c\"\n", tokyo.display()).as_bytes());
    write(&h.teams.join("broken/team.toml"), b"= not toml");
    let found = find_with(&inputs(&h, vec![], vec![]));
    let p = &found["projects"][0];
    assert_eq!((p["count"].clone(), p["summary"].clone()), (json!(1), json!("1 workspace")));
}

#[test]
fn herdr_teams_are_listed_after_the_tools_and_drafted_by_herdr() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    let h = home();
    let team = json!({"id": "saved:default:1", "label": "main team", "where": "Saved in herdr", "agents": 2, "running": false});
    let mut i = inputs(&h, vec![], vec![]);
    i.herdr_teams = Some(vec![team]);
    let found = find_with(&i);
    let last = found["projects"].as_array().unwrap().last().unwrap().clone();
    assert_eq!(last, json!({"id": "herdr:saved:default:1", "tool": "herdr", "label": "main team", "count": 2,
                            "summary": "Saved in herdr · 2 agents", "running": null}));
}

#[test]
fn a_repository_that_cannot_be_read_is_listed_as_unreadable() {
    let (found, unreadable) = scan(&["/definitely/not/a/repo".to_string()], &[], &[], "/h", Duration::from_secs(5));
    assert!(found.is_empty());
    assert_eq!(unreadable, ["/definitely/not/a/repo"]);
}

#[test]
fn the_running_agents_are_read_from_the_systems_process_list() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = std::fs::canonicalize(dir.path()).unwrap();
    let program = cwd.join("claude");
    if std::os::unix::fs::symlink("/bin/sleep", &program).is_err() {
        return;
    }
    let Ok(mut child) = Command::new(&program).arg("30").current_dir(&cwd).spawn() else { return };
    std::thread::sleep(Duration::from_millis(300));
    let seen = running_agents();
    let _ = child.kill();
    let _ = child.wait();
    assert!(seen.iter().any(|p| p.kind == "claude" && Path::new(&p.cwd) == cwd), "{seen:?}");
}

#[test]
fn start_puts_the_cards_on_the_board_the_run_script_in_the_preview_and_one_row_in_activity() {
    use std::cell::RefCell;
    let (added, moved, remembered, recorded) = (RefCell::new(vec![]), RefCell::new(vec![]), RefCell::new(vec![]), RefCell::new(vec![]));
    let cards = [
        json!({"title": "Fix login", "status": "doing", "role": "builder-1"}),
        json!({"title": "Add docs", "status": "todo", "role": null}),
        json!({"title": "Lost", "status": "review", "role": "builder-2"}),
    ];
    let roles = [json!({"name": "builder-1", "worktree": "/w/a"}), json!({"name": "builder-2"})];
    let scripts = BTreeMap::from([("run".to_string(), "npm run dev".to_string())]);
    started_with(
        "team",
        "/p",
        &roles,
        &cards,
        &scripts,
        &json!({"tool": "Conductor", "label": "app"}),
        &|p, r| remembered.borrow_mut().push((p.to_string(), r.to_string())),
        &|title, role| {
            added.borrow_mut().push((title.to_string(), role.map(String::from)));
            if title == "Lost" { Err(BrainError::new("no")) } else { Ok(json!({"id": format!("id-{}", added.borrow().len())})) }
        },
        &|id, status| {
            moved.borrow_mut().push((id.to_string(), status.to_string()));
            Ok(())
        },
        &|row| recorded.borrow_mut().push((row.action.to_string(), row.team.map(String::from), row.reason.to_string(), row.extra.clone())),
    );
    assert_eq!(*added.borrow(), [("Fix login".to_string(), Some("builder-1".to_string())), ("Add docs".to_string(), None), ("Lost".to_string(), Some("builder-2".to_string()))]);
    assert_eq!(*moved.borrow(), [("id-1".to_string(), "doing".to_string())], "a todo card stays where it starts, and a card that failed is not moved");
    assert_eq!(*remembered.borrow(), [("/p".to_string(), "npm run dev".to_string())]);
    let rows = recorded.borrow();
    assert_eq!((rows[0].0.as_str(), rows[0].1.as_deref(), rows[0].2.as_str()), ("bringover", Some("team"), "Its agents work in the folders they had in Conductor."));
    assert_eq!(rows[0].3["title"], "Brought over app from Conductor");
    assert_eq!(rows[0].3["adopted"], json!(["/w/a"]));
}
