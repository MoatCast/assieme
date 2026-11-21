//! Asking the person's own agent a one-off question: Claude Code first, else Codex, read-only, in a folder.
//!
//! Used by Help me choose and by suggested answers. The prompt goes on stdin (claude's --disallowedTools takes every
//! argument after it); the environment is cleaned of agent-session markers.
use crate::prelude::*;
use std::process::Command;
use std::time::Duration;

/// The agent could not answer, in words; `missing` when no agent is installed.
#[derive(Debug, Clone, PartialEq)]
pub struct AskError {
    pub message: String,
    pub missing: bool,
}

impl From<AskError> for BrainError {
    fn from(e: AskError) -> BrainError {
        BrainError::new(e.message)
    }
}

/// The agents installed on this machine, from `preset::AGENTS`.
pub fn installed() -> Vec<String> {
    let found = |a: &str| crate::launch::cli_of(a).and_then(crate::setup::agent_path).is_some();
    crate::preset::AGENTS.iter().filter(|a| found(a)).map(|a| a.to_string()).collect()
}

/// Claude's arguments with no tools at all: with Edit/Write/NotebookEdit/Bash, nothing is left to act with.
const NO_TOOLS: [&str; 6] = ["Read", "Grep", "Glob", "WebFetch", "WebSearch", "Task"];

/// The agent's answer text (Claude's --output-format json wrapper removed).
///
/// `tools: false` is a pure text answer. Use it whenever the prompt quotes text an agent printed, which may carry
/// instructions from what that agent read (Codex's read-only sandbox already has no network).
pub fn ask_agent(
    prompt: &str,
    cwd: &str,
    timeout_s: f64,
    timeout_label: Option<&str>,
    tools: bool,
) -> Result<String, AskError> {
    let err = |message: String| AskError { message, missing: false };
    // the only two that answer one-off questions here
    let Some(agent) = installed().into_iter().find(|a| a == "claude" || a == "codex") else {
        return Err(AskError { message: "Install Claude Code or Codex, then reopen New team.".into(), missing: true });
    };
    let mut args: Vec<&str> = if agent == "claude" {
        let mut a = vec!["-p", "--output-format", "json"];
        a.extend(if tools { None } else { Some("--strict-mcp-config") });
        a.extend(["--disallowedTools", "Edit", "Write", "NotebookEdit", "Bash"]);
        a.extend(if tools { &[][..] } else { &NO_TOOLS[..] });
        a
    } else {
        vec!["exec", "--sandbox", "read-only", "--skip-git-repo-check", "-"]
    };
    // by full path: the PATH this process has may not include where it was installed
    let program = crate::setup::agent_path(&agent).ok_or_else(|| err(format!("{agent} is not installed.")))?;
    let mut cmd = Command::new(program);
    let env = crate::runtime::clean_env(&std::env::vars().collect());
    cmd.args(args.drain(..)).current_dir(cwd).env_clear().envs(env);
    let out = crate::setup::run(cmd, Some(prompt.as_bytes().to_vec()), Duration::from_secs_f64(timeout_s))
        .map_err(|e| err(e.to_string()))?
        .ok_or_else(|| {
            err(format!("no answer within {}.", timeout_label.map_or(format!("{timeout_s} seconds"), String::from)))
        })?;
    let (stdout, stderr) = (String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
    if !out.status.success() {
        let from = if stderr.is_empty() { &stdout } else { &stderr };
        let last = from.lines().rfind(|l| !l.trim().is_empty()).map(str::trim);
        return Err(err(last.map_or(format!("exit {}", out.status.code().unwrap_or(-1)), String::from)));
    }
    Ok(match serde_json::from_str::<Value>(&stdout) {
        Ok(Value::Object(o)) if o.get("result").is_some_and(Value::is_string) => {
            o["result"].as_str().unwrap_or("").into()
        }
        _ => stdout.into_owned(),
    })
}

/// The JSON object from the first `{` to the last `}` of a reply (prose or a code fence around it is fine).
pub fn first_json(text: &str) -> Option<Map<String, Value>> {
    let (start, end) = (text.find('{')?, text.rfind('}')?);
    match serde_json::from_str(text.get(start..=end)?) {
        Ok(Value::Object(o)) => Some(o),
        _ => None,
    }
}
