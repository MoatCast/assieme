use super::*;
use std::os::unix::fs::PermissionsExt;

fn put(root: &Path, files: &[&str]) {
    for f in files {
        let p = root.join(f);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, format!("{f}\n")).unwrap();
    }
}

fn git_out(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git").args(args).current_dir(dir).output().unwrap();
    assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn project() -> (tempfile::TempDir, String) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("proj");
    std::fs::create_dir(&path).unwrap();
    let s = path.to_string_lossy().into_owned();
    (dir, s)
}

fn strings(v: &Value) -> Vec<&str> {
    v.as_array().unwrap().iter().map(|s| s.as_str().unwrap()).collect()
}

#[test]
fn the_preview_lists_what_a_save_would_commit_and_writes_nothing() {
    let (_dir, p) = project();
    let root = Path::new(&p);
    put(
        root,
        &["a.txt", "sub/b.txt", ".env", ".env.local", "node_modules/x.js", ".assieme/team.toml", "key.pem", "keep.md"],
    );
    let preview = folder_preview(&p).unwrap();
    assert_eq!(strings(&preview["sample"]), ["a.txt", "keep.md", "sub/b.txt"]);
    assert_eq!(
        (preview["files"].clone(), preview["empty"].clone(), preview["first"].clone()),
        (json!(3), json!(false), json!(true))
    );
    assert_eq!(strings(&preview["left_out"]), [".env", ".env.local", "key.pem", "node_modules"]);
    assert!(!root.join(".git").exists(), "a preview must not create a repository");
    assert_eq!(uncommitted(&p).unwrap(), 3);
}

#[test]
fn the_first_save_makes_one_commit_without_the_secrets_and_a_repeat_changes_nothing() {
    let (_dir, p) = project();
    let root = Path::new(&p);
    put(root, &["a.txt", "sub/b.txt", ".env", "node_modules/x.js", ".assieme/team.toml"]);
    assert_eq!(folder_save(&p).unwrap(), json!({"ok": true}));
    assert_eq!(git_out(root, &["rev-list", "--count", "HEAD"]), "1");
    assert_eq!(git_out(root, &["log", "-1", "--format=%s"]), "First version, saved by Assieme");
    assert_eq!(git_out(root, &["ls-files"]), "a.txt\nsub/b.txt");
    let exclude = std::fs::read_to_string(root.join(".git/info/exclude")).unwrap();
    assert!(exclude.lines().any(|l| l == ".env") && exclude.lines().any(|l| l == ".assieme/"), "{exclude}");
    // saved again with nothing new: no second commit
    assert_eq!(folder_save(&p).unwrap(), json!({"ok": true}));
    assert_eq!(git_out(root, &["rev-list", "--count", "HEAD"]), "1");
    assert_eq!(folder_preview(&p).unwrap()["first"], false);
}

#[test]
fn an_empty_folder_gets_an_empty_first_commit() {
    let (_dir, p) = project();
    assert_eq!(folder_preview(&p).unwrap()["empty"], true);
    folder_save(&p).unwrap();
    assert_eq!(git_out(Path::new(&p), &["rev-list", "--count", "HEAD"]), "1");
}

#[test]
fn a_later_save_commits_only_the_listed_files_and_counts_deletions() {
    let (_dir, p) = project();
    let root = Path::new(&p);
    put(root, &["a.txt", "gone.txt", "other/x.txt"]);
    folder_save(&p).unwrap();
    std::fs::write(root.join("a.txt"), "changed\n").unwrap();
    std::fs::remove_file(root.join("gone.txt")).unwrap();
    put(root, &["new.txt", ".env"]);
    assert_eq!(uncommitted(&p).unwrap(), 3);
    folder_save(&p).unwrap();
    assert_eq!(git_out(root, &["log", "-1", "--format=%s"]), "Saved by Assieme before starting a team");
    assert_eq!(git_out(root, &["status", "--porcelain"]), "");
    assert_eq!(git_out(root, &["ls-files"]), "a.txt\nnew.txt\nother/x.txt");
}

#[test]
fn what_the_person_staged_elsewhere_stays_out_of_the_commit() {
    let (_dir, p) = project();
    let root = Path::new(&p);
    put(root, &["a.txt", "b.txt"]);
    folder_save(&p).unwrap();
    // `staged.txt` is staged by the person; `a.txt` changed and is the one a save commits.
    put(root, &["staged.txt"]);
    git_out(root, &["add", "staged.txt"]);
    std::fs::write(root.join("a.txt"), "changed\n").unwrap();
    // the status of a staged new file is `A `, so it counts as a changed tracked file: it is listed too
    let files = uncommitted(&p).unwrap();
    assert_eq!(files, 2);
    folder_save(&p).unwrap();
    assert_eq!(git_out(root, &["status", "--porcelain"]), "");
}

#[test]
fn a_folder_inside_a_repository_counts_only_its_own_files() {
    let (dir, _) = project();
    let root = dir.path().join("repo");
    std::fs::create_dir(&root).unwrap();
    put(&root, &["top.txt", "app/one.txt", "lib/two.txt"]);
    git_out(&root, &["init", "-q"]);
    git_out(&root, &["add", "."]);
    git_out(&root, &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start"]);
    put(&root, &["top.txt", "app/one.txt", "app/new.txt", "lib/two.txt"]);
    std::fs::write(root.join("app/one.txt"), "edited\n").unwrap();
    std::fs::write(root.join("lib/two.txt"), "edited\n").unwrap();
    let app = root.join("app").to_string_lossy().into_owned();
    assert_eq!(strings(&folder_preview(&app).unwrap()["sample"]), ["new.txt", "one.txt"]);
    // the repository's top folder: the two edited files and the new one (`top.txt` is unchanged)
    assert_eq!(uncommitted(&root.to_string_lossy()).unwrap(), 3);
}

#[test]
fn a_merge_in_progress_stops_the_save() {
    let (_dir, p) = project();
    let root = Path::new(&p);
    put(root, &["a.txt"]);
    folder_save(&p).unwrap();
    std::fs::write(root.join(".git/MERGE_HEAD"), "0\n").unwrap();
    let err = folder_save(&p).unwrap_err();
    assert_eq!(
        err,
        SetupError::Unfinished(Unfinished(
            "A git merge or similar is in progress in this folder. Finish it first, then come back.".into()
        ))
    );
    assert!(folder_preview(&p).is_err());
}

#[test]
fn the_home_folder_and_root_are_too_broad() {
    assert!(too_broad("/") && too_broad("/./") && too_broad("~") && too_broad("~/"));
    assert!(!too_broad("/tmp"));
    assert_eq!(folder_save("/").unwrap_err(), SetupError::TooLarge(TooLarge("This folder is too large.".into())));
}

#[test]
fn creating_a_folder_refuses_one_that_exists() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a/b").to_string_lossy().into_owned();
    assert_eq!(folder_create(&path).unwrap(), json!({"path": path}));
    assert!(Path::new(&path).is_dir());
    assert_eq!(folder_create(&path).unwrap_err(), SetupError::Failed("That folder already exists.".into()));
    let blocked = dir.path().join("a/b/c/d");
    std::fs::write(dir.path().join("a/b/c"), "file").unwrap();
    assert!(matches!(folder_create(&blocked.to_string_lossy()), Err(SetupError::Failed(m)) if !m.contains("os error")));
}

fn script(dir: &Path, name: &str, body: &str) -> PathBuf {
    let p = dir.join(name);
    std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    p
}

#[test]
fn signed_in_is_read_from_each_tools_own_words_and_unclear_counts_as_in() {
    let dir = tempfile::tempdir().unwrap();
    let d = dir.path();
    let paths: BTreeMap<&str, Option<PathBuf>> = BTreeMap::from([
        ("claude", Some(script(d, "claude", "exit 1"))),
        ("codex", Some(script(d, "codex", "exit 0"))),
        ("gemini", None),
        ("opencode", Some(script(d, "opencode", "echo 'You have 0 credentials'"))),
        ("cursor", Some(script(d, "cursor-agent", "echo 'Not logged in'"))),
        ("antigravity", None),
    ]);
    let got = status_with(&paths);
    let agents = &got["agents"];
    assert_eq!(agents["claude"], json!({"installed": true, "signed_in": false}));
    assert_eq!(agents["codex"], json!({"installed": true, "signed_in": true}));
    assert_eq!(agents["opencode"], json!({"installed": true, "signed_in": false}));
    assert_eq!(agents["cursor"], json!({"installed": true, "signed_in": false}));
    assert_eq!(agents["gemini"], json!({"installed": false, "signed_in": false}));
    assert_eq!(agents["antigravity"], json!({"installed": false, "signed_in": false}));
    // a program that crashes, or that the system can't start, is not "signed out"
    let paths = BTreeMap::from([("claude", Some(script(d, "c2", "exit 2"))), ("codex", Some(d.join("missing")))]);
    assert!(
        signed_in(paths["claude"].as_ref().unwrap(), "claude") && signed_in(paths["codex"].as_ref().unwrap(), "codex")
    );
    assert!(signed_in(Path::new("/nowhere"), "antigravity"));
}

#[test]
fn a_command_that_hangs_is_killed_at_its_timeout() {
    let dir = tempfile::tempdir().unwrap();
    let hang = script(dir.path(), "hang", "sleep 30");
    let started = Instant::now();
    assert!(run(Command::new(hang), None, Duration::from_millis(150)).unwrap().is_none());
    assert!(started.elapsed() < Duration::from_secs(10));
    let mut cat = Command::new("cat");
    cat.arg("-");
    let out = run(cat, Some(b"fed".to_vec()), Duration::from_secs(5)).unwrap().unwrap();
    assert_eq!(out.stdout, b"fed");
}

#[test]
fn git_counts_as_ready_unless_it_is_apples_stub() {
    assert!(git_ready_with(Some(PathBuf::from("/opt/homebrew/bin/git")), true));
    assert!(git_ready_with(Some(PathBuf::from("/usr/bin/git")), false));
}

#[test]
fn the_commands_shown_are_the_commands_that_run() {
    let a = agents();
    assert_eq!(a.as_object().unwrap().len(), 6);
    assert_eq!(a["claude"]["signin"], "claude auth login");
    assert_eq!(known("nope").unwrap_err(), SetupError::Failed("Unknown agent.".into()));
    assert_eq!(quote("/a b/c"), "'/a b/c'");
    assert_eq!(quote("/a/b-c"), "/a/b-c");
}
