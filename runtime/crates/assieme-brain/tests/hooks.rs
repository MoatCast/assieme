//! What may happen without the person: the ask hook's answers, and the one-off question run read-only.
#![cfg(unix)]
use assieme_brain::{ask, askhook};
use serde_json::json;

#[test]
fn ask_hook_asks_for_changes_and_refuses_where_nobody_can_say_yes() {
    let ask = |t: &str| {
        json!({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": t,
        "permissionDecisionReason": if t == "ask" { "Assieme asks before changing anything." } else {
            "Assieme changes need your own yes, and this session’s permission mode can approve without asking you. Switch to the default mode to make it." }}})
    };
    for (input, expected) in [
        (r#"{"tool_name":"mcp__assieme__answer"}"#, Some("ask")),
        (r#"{"tool_name":"mcp__plugin_assieme_assieme__app_action","permission_mode":"default"}"#, Some("ask")),
        (r#"{"tool_name":"mcp__assieme__stop_team","permission_mode":"bypassPermissions"}"#, Some("deny")),
        (r#"{"tool_name":"mcp__assieme__send_message","permission_mode":"dontAsk"}"#, Some("deny")),
        (r#"{"tool_name":"mcp__assieme__app_action","permission_mode":"auto"}"#, Some("deny")),
        (r#"{"tool_name":"mcp__assieme__list_teams"}"#, None),
        (r#"{"tool_name":"mcp__assieme__app_read","permission_mode":"auto"}"#, None),
        (r#"{}"#, None),
        ("not json", Some("ask")),
        ("[]", Some("ask")),
        (r#"{"tool_name":5}"#, Some("ask")),
    ] {
        assert_eq!(askhook::answer(input.as_bytes()), expected.map(ask), "{input}");
    }
}

/// A fake `claude` on the PATH: it records its arguments and its input, and answers as the real one wraps its text.
#[test]
fn ask_agent_runs_claude_read_only_and_unwraps_its_answer() {
    let dir = tempfile::tempdir().unwrap();
    let (claude, seen) = (dir.path().join("claude"), dir.path().join("seen"));
    let script = format!(
        "#!/bin/sh\necho \"$@\" > {0}\n/bin/cat >> {0}\nif [ \"$FAIL\" = 1 ]; then echo first >&2; echo 'the last line' >&2; exit 3; fi\necho '{{\"result\":\"yes\"}}'\n",
        seen.display()
    );
    std::fs::write(&claude, script).unwrap();
    std::fs::set_permissions(&claude, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    unsafe { std::env::set_var("PATH", dir.path()) };
    assert!(ask::installed().contains(&"claude".to_string()));
    for (tools, args) in [
        (true, "-p --output-format json --disallowedTools Edit Write NotebookEdit Bash\nthe question"),
        (
            false,
            "-p --output-format json --strict-mcp-config --disallowedTools Edit Write NotebookEdit Bash Read Grep Glob WebFetch WebSearch Task\nthe question",
        ),
    ] {
        assert_eq!(ask::ask_agent("the question", "/", 10.0, None, tools).unwrap(), "yes");
        assert_eq!(std::fs::read_to_string(&seen).unwrap().trim_end(), args);
    }
    unsafe { std::env::set_var("FAIL", "1") };
    let e = ask::ask_agent("q", "/", 10.0, None, true).unwrap_err();
    assert_eq!((e.message.as_str(), e.missing), ("the last line", false));
}
