use super::*;

fn git(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git").args(args).current_dir(dir).output().unwrap();
    assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn put(root: &Path, files: &[&str]) {
    for f in files {
        std::fs::create_dir_all(root.join(f).parent().unwrap()).unwrap();
        std::fs::write(root.join(f), format!("{f}\n")).unwrap();
    }
}

/// A save commits the listed files only, leaves secrets out, and a repeat or a merge in progress changes nothing.
#[test]
fn saving_a_folder_as_a_project() {
    let dir = tempfile::tempdir().unwrap();
    let (root, p) = (dir.path().join("proj"), dir.path().join("proj").to_string_lossy().into_owned());
    put(&root, &["a.txt", "sub/b.txt", ".env", "node_modules/x.js", ".assieme/team.toml"]);
    let preview = folder_preview(&p).unwrap();
    assert_eq!(
        (&preview["files"], &preview["first"], &preview["left_out"]),
        (&json!(2), &json!(true), &json!([".env", "node_modules"]))
    );
    assert!(!root.join(".git").exists(), "a preview must not create a repository");
    // (what changes before the next save, the commit message, the files tracked, the commits so far)
    let rounds = [
        ("", "First version, saved by Assieme", "a.txt\nsub/b.txt", "1"),
        ("", "First version, saved by Assieme", "a.txt\nsub/b.txt", "1"),
        ("new.txt", "Saved by Assieme before starting a team", "a.txt\nnew.txt\nsub/b.txt", "2"),
    ];
    for (new, message, tracked, commits) in rounds {
        put(&root, &[new].map(|n| if n.is_empty() { "a.txt" } else { n }));
        if new.is_empty() {
            std::fs::write(root.join("a.txt"), "a.txt\n").unwrap();
        }
        assert_eq!(folder_save(&p).unwrap(), json!({"ok": true}));
        assert_eq!(git(&root, &["ls-files"]), tracked);
        assert_eq!(
            (git(&root, &["log", "-1", "--format=%s"]).as_str(), git(&root, &["rev-list", "--count", "HEAD"]).as_str()),
            (message, commits)
        );
    }
    std::fs::write(root.join(".git/MERGE_HEAD"), "0\n").unwrap();
    assert!(matches!(folder_save(&p), Err(SetupError::Unfinished(_))));
    assert!(matches!(folder_save("/"), Err(SetupError::TooLarge(_))));
}

/// A command that hangs is killed with its children at its timeout.
#[test]
fn a_hanging_command_is_killed() {
    let started = Instant::now();
    let mut sh = Command::new("sh");
    sh.args(["-c", "sleep 30 & wait"]);
    assert!(run(sh, None, Duration::from_millis(150)).unwrap().is_none());
    assert!(started.elapsed() < Duration::from_secs(10));
}
