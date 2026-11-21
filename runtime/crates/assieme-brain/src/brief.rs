//! Port of `assieme/brief.py`, owned by session D (see OWNERS.md).
//!
//! The shared team brief: one small file per team with the goal, the person's rules and the decisions they made.
//!
//! Every agent gets it in its first message and can read it again with the `team_brief` tool, so a question the person
//! has answered once isn't asked twice. Kept by team name, so it outlives a restart of the team and of the brain.
use crate::prelude::*;
use serde_json::json;
use sha2::{Digest, Sha256};

const GOAL_MAX: usize = 2000;
const RULES_MAX: usize = 4000;
const QUESTION_MAX: usize = 300;
const ANSWER_MAX: usize = 500;
/// Decisions kept; the oldest go first.
const KEEP: usize = 50;
const SAME_ANSWER_MS: i64 = 300 * 1000;
pub const READ_FIRST: &str = "Before you ask the person anything, call team_brief: the answer may be there already.";

/// The brain serves each call on its own thread.
static WRITE: Mutex<()> = Mutex::new(());

fn file(team: &str) -> PathBuf {
    let safe: String =
        team.chars().map(|c| if c.is_alphanumeric() || matches!(c, '_' | '.' | '-') { c } else { '-' }).collect();
    crate::quiet::state_dir().join("briefs").join(format!("{safe}.json"))
}

fn clip(s: &str, max: usize) -> String {
    s.trim().chars().take(max).collect()
}

fn text_of(v: Option<&Value>) -> String {
    v.and_then(Value::as_str).unwrap_or_default().to_string()
}

fn load_from(path: &Path) -> Value {
    let got = crate::quiet::read_json(path, json!({}));
    let decisions: Vec<Value> = got
        .get("decisions")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter(|d| d.is_object() && !text_of(d.get("q")).is_empty() && !text_of(d.get("a")).is_empty())
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    json!({"goal": text_of(got.get("goal")), "rules": text_of(got.get("rules")), "decisions": decisions})
}

fn change(path: &Path, edit: impl FnOnce(&mut Value) -> bool) -> Result<(Value, bool), BrainError> {
    let _one = WRITE.lock().unwrap_or_else(|p| p.into_inner());
    let mut b = load_from(path);
    let changed = edit(&mut b);
    if changed {
        crate::quiet::write_json(path, &b).map_err(|e| BrainError::new(e.to_string()))?;
    }
    Ok((b, changed))
}

/// `{goal, rules, decisions}`.
pub fn load(team: &str) -> Value {
    load_from(&file(team))
}

fn save_in(path: &Path, goal: Option<&str>, rules: Option<&str>) -> Result<Value, BrainError> {
    change(path, |b| {
        if let Some(g) = goal {
            b["goal"] = json!(clip(g, GOAL_MAX));
        }
        if let Some(r) = rules {
            b["rules"] = json!(clip(r, RULES_MAX));
        }
        true
    })
    .map(|(b, _)| b)
}

/// Set the goal, the rules or both; None leaves that part as it is.
pub fn save(team: &str, goal: Option<&str>, rules: Option<&str>) -> Result<Value, BrainError> {
    save_in(&file(team), goal, rules)
}

fn squash(s: &str, max: usize) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(max).collect()
}

fn add_in(
    path: &Path,
    role: &str,
    question: &str,
    answer: &str,
    by: &str,
    ms: i64,
) -> Result<Option<Value>, BrainError> {
    let (q, a) = (squash(question, QUESTION_MAX), squash(answer, ANSWER_MAX));
    if q.is_empty() || a.is_empty() {
        return Err(BrainError::new("A decision needs the question and the answer."));
    }
    let (b, changed) = change(path, |b| {
        let mut decisions: Vec<Value> = b["decisions"].as_array().cloned().unwrap_or_default();
        // shortcut: an agent's record soon after ours for the same agent is taken as the same answer; compare the
        // words instead if agents start losing real second decisions.
        let echo =
            |d: &Value| d["role"] == role && d["by"] == "you" && ms - d["ms"].as_i64().unwrap_or(0) < SAME_ANSWER_MS;
        if by == "agent" && decisions.iter().any(echo) {
            return false;
        }
        decisions.retain(|d| text_of(d.get("q")).to_lowercase() != q.to_lowercase());
        let digest = Sha256::digest(format!("{q}{ms}"));
        let id: String = digest.iter().take(5).map(|x| format!("{x:02x}")).collect();
        decisions.push(json!({"id": id, "q": q, "a": a, "role": role, "by": by, "ms": ms}));
        let drop = decisions.len().saturating_sub(KEEP);
        b["decisions"] = Value::Array(decisions.split_off(drop));
        true
    })?;
    Ok(changed.then_some(b))
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
    let ms = now.unwrap_or_else(Timestamp::now).as_millisecond();
    add_in(&file(team), role, question, answer, by, ms)
}

fn remove_in(path: &Path, id: &str) -> Result<Value, BrainError> {
    change(path, |b| {
        let kept: Vec<Value> =
            b["decisions"].as_array().into_iter().flatten().filter(|d| d["id"] != id).cloned().collect();
        b["decisions"] = Value::Array(kept);
        true
    })
    .map(|(b, _)| b)
}

pub fn remove(team: &str, id: &str) -> Result<Value, BrainError> {
    remove_in(&file(team), id)
}

fn text_of_brief(b: &Value, goal: bool) -> String {
    let mut parts = vec![];
    if goal && !text_of(b.get("goal")).is_empty() {
        parts.push(format!("Goal: {}", text_of(b.get("goal"))));
    }
    if !text_of(b.get("rules")).is_empty() {
        parts.push(format!("Rules from the person:\n{}", text_of(b.get("rules"))));
    }
    let lines: Vec<String> = b["decisions"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|d| format!("- {} -> {}", text_of(d.get("q")), text_of(d.get("a"))))
        .collect();
    if !lines.is_empty() {
        parts.push(format!("Decisions the person already made:\n{}", lines.join("\n")));
    }
    if parts.is_empty() {
        String::new()
    } else {
        format!("Team brief, shared by every agent in this team:\n{}", parts.join("\n"))
    }
}

/// What agents read, or "" while the brief is empty. `goal` false for a first message (see `team::brief`).
pub fn text(team: &str, goal: bool) -> String {
    text_of_brief(&load(team), goal)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(dir: &tempfile::TempDir) -> PathBuf {
        dir.path().join("t.json")
    }

    #[test]
    fn a_missing_or_damaged_file_is_an_empty_brief() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(load_from(&at(&dir)), json!({"goal": "", "rules": "", "decisions": []}));
        std::fs::write(at(&dir), "not json").unwrap();
        assert_eq!(load_from(&at(&dir))["decisions"], json!([]));
    }

    #[test]
    fn goal_and_rules_are_trimmed_cut_and_kept_apart() {
        let dir = tempfile::tempdir().unwrap();
        let p = at(&dir);
        save_in(&p, Some("  ship it \n"), None).unwrap();
        let b = save_in(&p, None, Some(&"r".repeat(5000))).unwrap();
        assert_eq!(b["goal"], "ship it");
        assert_eq!(b["rules"].as_str().unwrap().len(), 4000);
        let b = save_in(&p, Some(&"g".repeat(2500)), None).unwrap();
        assert_eq!(b["goal"].as_str().unwrap().len(), 2000);
        assert_eq!(b["rules"].as_str().unwrap().len(), 4000);
    }

    #[test]
    fn the_same_question_replaces_its_answer() {
        let dir = tempfile::tempdir().unwrap();
        let p = at(&dir);
        add_in(&p, "b", "Which   database?", "sqlite", "you", 1000).unwrap();
        let b = add_in(&p, "b", "which database?", "postgres", "you", 2000).unwrap().unwrap();
        let d = b["decisions"].as_array().unwrap();
        assert_eq!(d.len(), 1);
        assert_eq!((d[0]["q"].as_str(), d[0]["a"].as_str()), (Some("which database?"), Some("postgres")));
        assert_eq!(d[0]["id"].as_str().unwrap().len(), 10);
    }

    #[test]
    fn an_empty_question_or_answer_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        assert!(add_in(&at(&dir), "b", "  ", "x", "you", 1).is_err());
        assert!(add_in(&at(&dir), "b", "x", "", "you", 1).is_err());
    }

    #[test]
    fn an_agents_echo_of_the_persons_answer_is_not_a_second_decision() {
        let dir = tempfile::tempdir().unwrap();
        let p = at(&dir);
        add_in(&p, "b", "Use tabs?", "yes", "you", 1_000_000).unwrap();
        assert!(add_in(&p, "b", "Tabs or spaces?", "tabs", "agent", 1_100_000).unwrap().is_none());
        assert!(
            add_in(&p, "c", "Tabs or spaces?", "tabs", "agent", 1_100_000).unwrap().is_some(),
            "another agent's word counts"
        );
        assert!(
            add_in(&p, "b", "Line length?", "100", "agent", 1_000_000 + 300_000).unwrap().is_some(),
            "after five minutes it counts"
        );
    }

    #[test]
    fn only_the_newest_fifty_decisions_stay() {
        let dir = tempfile::tempdir().unwrap();
        let p = at(&dir);
        for i in 0..55 {
            add_in(&p, "b", &format!("q{i}"), "a", "you", i).unwrap();
        }
        let b = load_from(&p);
        let d = b["decisions"].as_array().unwrap();
        assert_eq!((d.len(), d[0]["q"].as_str(), d[49]["q"].as_str()), (50, Some("q5"), Some("q54")));
    }

    #[test]
    fn a_decision_can_be_removed_by_its_id() {
        let dir = tempfile::tempdir().unwrap();
        let p = at(&dir);
        let b = add_in(&p, "b", "one", "1", "you", 1).unwrap().unwrap();
        add_in(&p, "b", "two", "2", "you", 2).unwrap();
        let id = b["decisions"][0]["id"].as_str().unwrap();
        let b = remove_in(&p, id).unwrap();
        assert_eq!(b["decisions"].as_array().unwrap().len(), 1);
        assert_eq!(remove_in(&p, "nope").unwrap()["decisions"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn what_agents_read() {
        let dir = tempfile::tempdir().unwrap();
        let p = at(&dir);
        assert_eq!(text_of_brief(&load_from(&p), true), "");
        save_in(&p, Some("Ship"), Some("No pushes")).unwrap();
        add_in(&p, "b", "Tabs?", "yes", "you", 1).unwrap();
        let b = load_from(&p);
        assert_eq!(
            text_of_brief(&b, true),
            "Team brief, shared by every agent in this team:\nGoal: Ship\nRules from the person:\nNo pushes\n\
             Decisions the person already made:\n- Tabs? -> yes"
        );
        assert!(!text_of_brief(&b, false).contains("Goal:"));
    }
}
