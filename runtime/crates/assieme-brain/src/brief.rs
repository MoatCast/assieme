//! Port of `assieme/brief.py`, owned by session D (see OWNERS.md).
//!
//! The shared team brief: one small file per team with the goal, the person's rules and the decisions they made.
//!
//! Every agent gets it in its first message and can read it again with the `team_brief` tool, so a question the person
//! has answered once isn't asked twice. Kept by team name, so it outlives a restart of the team and of the brain.
use crate::prelude::*;
use serde_json::json;
use sha2::{Digest, Sha256};

pub const READ_FIRST: &str = "Before you ask the person anything, call team_brief: the answer may be there already.";
const KEEP: usize = 50; // decisions; the oldest go first
const SAME_ANSWER_MS: i64 = 300_000;

/// The brain serves each call on its own thread.
static WRITE: Mutex<()> = Mutex::new(());

fn file(team: &str) -> PathBuf {
    let safe: String = team.chars().map(|c| if c.is_alphanumeric() || "_.-".contains(c) { c } else { '-' }).collect();
    crate::quiet::state_dir().join("briefs").join(format!("{safe}.json"))
}

fn s(v: &Value, k: &str) -> String {
    v[k].as_str().unwrap_or_default().to_string()
}

fn clip(text: &str, max: usize) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(max).collect()
}

/// `{goal, rules, decisions}`.
pub fn load(team: &str) -> Value {
    let got = crate::quiet::read_json(&file(team), json!({}));
    let decisions: Vec<&Value> = got["decisions"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|d| !s(d, "q").is_empty() && !s(d, "a").is_empty())
        .collect();
    json!({"goal": s(&got, "goal"), "rules": s(&got, "rules"), "decisions": decisions})
}

/// Reads the brief, lets `edit` change it (false: it did not), and writes it back.
fn change(team: &str, edit: impl FnOnce(&mut Value) -> bool) -> Result<Option<Value>, BrainError> {
    let _one = WRITE.lock().unwrap_or_else(|p| p.into_inner());
    let mut b = load(team);
    if !edit(&mut b) {
        return Ok(None);
    }
    crate::quiet::write_json(&file(team), &b).map_err(|e| BrainError::new(e.to_string()))?;
    Ok(Some(b))
}

/// Set the goal, the rules or both; None leaves that part as it is.
pub fn save(team: &str, goal: Option<&str>, rules: Option<&str>) -> Result<Value, BrainError> {
    change(team, |b| {
        for (k, v, max) in [("goal", goal, 2000), ("rules", rules, 4000)] {
            if let Some(v) = v {
                b[k] = json!(v.trim().chars().take(max).collect::<String>());
            }
        }
        true
    })
    .map(|b| b.unwrap_or_default())
}

/// One decision. `by` is who wrote it down: "you" when the brain saw the person's answer, "agent" when the agent
/// reported it. The same question again replaces its earlier answer. None when an agent's record repeats an answer
/// the person gave a moment ago.
pub fn add(
    team: &str,
    role: &str,
    question: &str,
    answer: &str,
    by: &str,
    now: Option<Timestamp>,
) -> Result<Option<Value>, BrainError> {
    let (q, a) = (clip(question, 300), clip(answer, 500));
    if q.is_empty() || a.is_empty() {
        return Err(BrainError::new("A decision needs the question and the answer."));
    }
    let ms = now.unwrap_or_else(Timestamp::now).as_millisecond();
    change(team, |b| {
        let mut decisions: Vec<Value> = b["decisions"].as_array().cloned().unwrap_or_default();
        // shortcut: an agent's record soon after ours for the same agent is taken as the same answer; compare the
        // words instead if agents start losing real second decisions.
        if by == "agent"
            && decisions
                .iter()
                .any(|d| d["role"] == role && d["by"] == "you" && ms - d["ms"].as_i64().unwrap_or(0) < SAME_ANSWER_MS)
        {
            return false;
        }
        decisions.retain(|d| s(d, "q").to_lowercase() != q.to_lowercase());
        let id: String = Sha256::digest(format!("{q}{ms}")).iter().take(5).map(|x| format!("{x:02x}")).collect();
        decisions.push(json!({"id": id, "q": q, "a": a, "role": role, "by": by, "ms": ms}));
        let drop = decisions.len().saturating_sub(KEEP);
        b["decisions"] = Value::Array(decisions.split_off(drop));
        true
    })
}

pub fn remove(team: &str, id: &str) -> Result<Value, BrainError> {
    change(team, |b| {
        b["decisions"] = b["decisions"].as_array().into_iter().flatten().filter(|d| d["id"] != id).cloned().collect();
        true
    })
    .map(|b| b.unwrap_or_default())
}

/// What agents read, or "" while the brief is empty. `goal` false for a first message (see `team::send_brief`).
pub fn text(team: &str, goal: bool) -> String {
    let b = load(team);
    let lines: Vec<String> =
        b["decisions"].as_array().into_iter().flatten().map(|d| format!("- {} -> {}", s(d, "q"), s(d, "a"))).collect();
    let parts: Vec<String> = [
        (goal && !s(&b, "goal").is_empty()).then(|| format!("Goal: {}", s(&b, "goal"))),
        (!s(&b, "rules").is_empty()).then(|| format!("Rules from the person:\n{}", s(&b, "rules"))),
        (!lines.is_empty()).then(|| format!("Decisions the person already made:\n{}", lines.join("\n"))),
    ]
    .into_iter()
    .flatten()
    .collect();
    if parts.is_empty() {
        String::new()
    } else {
        format!("Team brief, shared by every agent in this team:\n{}", parts.join("\n"))
    }
}
