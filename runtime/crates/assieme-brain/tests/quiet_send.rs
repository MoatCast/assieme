//! Quiet send end to end with a fake `asm`: a note on a quiet pair is held, an urgent one and a `--now` one go through,
//! and the deliverer sends the held notes as one batch once the receiver is busy. One test: it sets the environment.
#![cfg(unix)]
use assieme_brain::{quiet, status::Agent};

#[test]
fn quiet_notes_are_held_then_delivered_as_one_batch() {
    let dir = std::fs::canonicalize("/var/tmp").unwrap().join(format!("brain-quiet-send-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let (bin, from, to, calls) = (dir.join("bin"), dir.join("from"), dir.join("to"), dir.join("calls"));
    [&bin, &from, &to].iter().for_each(|d| std::fs::create_dir_all(d).unwrap());
    let asm = bin.join("asm");
    std::fs::write(
        &asm,
        format!(
            "#!/bin/sh\necho \"$@\" >> {}\n[ \"$1\" = get ] && echo '{{\"cwd\": \"{}\"}}'\nexit 0\n",
            calls.display(),
            to.display()
        ),
    )
    .unwrap();
    std::fs::set_permissions(&asm, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    unsafe {
        std::env::set_var("PATH", format!("{}:/usr/bin:/bin", bin.display()));
        std::env::set_var("XDG_STATE_HOME", dir.join("state"));
    }
    let shim = |words: &[&str]| {
        quiet::shim_main(&words.iter().map(|w| w.to_string()).collect::<Vec<_>>(), from.to_str(), None)
    };
    let log = || std::fs::read_to_string(&calls).unwrap_or_default();
    let agent = |state: &str| Agent { cwd: to.to_str().map(String::from), state: state.into(), ..Agent::default() };
    quiet::set_pair(from.to_str().unwrap(), to.to_str().unwrap(), "pair", true);

    assert_eq!(shim(&["send", "peer", "no loop running"]), 0);
    assert!(log().contains("send peer no loop running"), "with nobody to deliver, a note goes now");
    assert!(quiet::deliver(&[agent("idle")], None, None, None).is_empty()); // starts the heartbeat

    for note in ["first note", "second note"] {
        assert_eq!(shim(&["send", "peer", note]), 0);
    }
    assert_eq!(quiet::held().values().map(Vec::len).sum::<usize>(), 2);
    assert!(!log().contains("first note"), "held, not sent");
    shim(&["send", "peer", "is it done?"]);
    shim(&["send", "--now", "peer", "right away"]);
    assert!(log().contains("send peer is it done?") && log().contains("send peer right away"));

    assert!(quiet::deliver(&[agent("idle")], None, None, None).is_empty(), "an idle receiver waits");
    let sent = quiet::deliver(&[agent("working")], None, None, None);
    assert_eq!(sent, [serde_json::json!(["peer", 2, "busy"])]);
    assert!(log().contains("2 quiet notes held") && log().contains("second note") && quiet::held().is_empty());
    let _ = std::fs::remove_dir_all(&dir);
}
