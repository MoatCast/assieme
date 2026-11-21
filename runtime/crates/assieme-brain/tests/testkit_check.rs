//! The testkit itself: the fake core logs sends, kills and spawns; the repository has its commit.
#![cfg(unix)]
mod testkit;
use assieme_brain::runtime::Runtime;
use serde_json::json;

#[test]
fn fake_core_logs_and_answers() {
    let tmp = testkit::Temp::new();
    let sock = tmp.0.join("asm.sock");
    let core = testkit::FakeCore::start(
        &sock,
        [("terminal.run", json!({"address": "t1"})), ("terminal.send", json!({})), ("terminal.kill", json!(null))]
            .into(),
    );
    let rt = Runtime::new("test", Some(sock));
    assert_eq!(rt.start(&["claude".into()], "/p", &Default::default(), None, None).unwrap(), "t1");
    rt.send("t1", "hi", 30.0).unwrap();
    rt.kill("t1").unwrap();
    assert_eq!(rt.get("t1").unwrap_err().message, "no method");
    assert_eq!(core.calls("terminal.send"), [json!({"terminal": "t1", "text": "hi", "timeout": 30})]);
    assert_eq!(core.calls("terminal.kill").len(), 1);
    assert_eq!(core.calls("terminal.run")[0]["argv"], json!(["claude"]));
    let repo = tmp.git_repo("repo");
    let log = std::process::Command::new("git").arg("-C").arg(&repo).args(["log", "--oneline"]).output().unwrap();
    assert_eq!(String::from_utf8_lossy(&log.stdout).lines().count(), 1);
}
