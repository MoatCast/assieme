//! Port of `assieme/tasks.py`, owned by session C (see OWNERS.md).
//!
//! The task board: what a team is doing, as cards in To do, Doing, Review and Done.
//!
//! A card's status is said, never guessed: an agent says it through the MCP server, the person moves the card, or the
//! brain acts on a fact it has about the branch (merged, discarded, not merged yet). Kept in one small file, like
//! reports, so it survives a brain restart.
use crate::fixes::obj;
use crate::live::{self, Record};
use crate::prelude::*;
use crate::pyrules::{split, splitlines};
use crate::quiet;
use crate::server::{Call, Handler};
use serde_json::json;

const STATUSES: [&str; 4] = ["todo", "doing", "review", "done"];
const MAX_TITLE: usize = 120;
const MAX_NOTE: usize = 200;
const MAX_CARDS: usize = 200;
static WRITE: Mutex<()> = Mutex::new(()); // the brain serves each call on its own thread

/// An agent's terminal address -> (team, role, its row).
pub type Me = dyn Fn(&str) -> Option<(String, String, Value)> + Send + Sync;

fn all() -> Map<String, Value> {
    obj(quiet::read_json(&quiet::path(&["tasks.json"]), json!({})))
}

fn of(all: &Map<String, Value>, team: &str) -> Value {
    match all.get(team) {
        Some(b) if b["cards"].is_array() && b["next"].is_i64() => b.clone(),
        _ => json!({"title": "", "next": 1, "cards": []}),
    }
}

pub fn board(team: &str) -> Value {
    of(&all(), team)
}

/// Change one team's board. Nothing is written when the change fails, or changes nothing.
fn edit<T>(team: &str, change: impl FnOnce(&mut Value) -> Result<T, BrainError>) -> Result<T, BrainError> {
    let _one = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    let mut everything = all();
    let mut b = of(&everything, team);
    let got = change(&mut b)?;
    if everything.get(team) != Some(&b) {
        everything.insert(team.into(), b);
        quiet::write_json(&quiet::path(&["tasks.json"]), &Value::Object(everything))
            .map_err(|e| BrainError::new(e.to_string()))?;
    }
    Ok(got)
}

fn ms(now: Option<Timestamp>) -> i64 {
    now.unwrap_or_else(Timestamp::now).as_millisecond()
}

fn card<'a>(b: &'a mut Value, id: &str) -> Result<&'a mut Value, BrainError> {
    let want = id.trim().trim_start_matches('#');
    let cards = b["cards"].as_array_mut().into_iter().flatten();
    cards.into_iter().find(|c| c["id"] == want).ok_or_else(|| BrainError::new(format!("No task {id} on this board.")))
}

fn clip(text: &str, n: usize) -> String {
    split(text).join(" ").chars().take(n).collect()
}

/// A new team's board: empty, titled with the first sentence of its task.
pub fn fresh(team: &str, task: &str) {
    let line = splitlines(task.trim()).first().map_or("", |l| l.trim());
    // up to the first space that follows . ! or ?
    let end = line
        .char_indices()
        .zip(line.chars().skip(1))
        .find(|((_, a), b)| ".!?".contains(*a) && b.is_whitespace())
        .map_or(line.len(), |((i, a), _)| i + a.len_utf8());
    let title: String = line[..end].trim_end_matches('.').chars().take(MAX_TITLE).collect();
    let _ = edit(team, |b| {
        *b = json!({"title": title, "next": 1, "cards": []});
        Ok(())
    });
}

/// A new card.
pub fn add(team: &str, title: &str, role: Option<&str>, by: &str, now: Option<Timestamp>) -> Result<Value, BrainError> {
    let title = clip(title, MAX_TITLE);
    if title.is_empty() {
        return Err(BrainError::new("Say what needs doing."));
    }
    edit(team, |b| {
        if b["cards"].as_array().map_or(0, Vec::len) >= MAX_CARDS {
            return Err(BrainError::new("This board is full. Remove some finished cards first."));
        }
        let c = json!({"id": b["next"].to_string(), "title": title, "role": role.filter(|r| !r.is_empty()), "status": "todo", "by": by,
                       "note": "", "needs_you": false, "ms": ms(now), "moved_ms": ms(now)});
        b["next"] = json!(b["next"].as_i64().unwrap_or(1) + 1);
        b["cards"].as_array_mut().into_iter().for_each(|cards| cards.push(c.clone()));
        Ok(c)
    })
}

/// Set a card's status. `note` None keeps the note it has.
pub fn r#move(
    team: &str,
    id: &str,
    status: &str,
    note: Option<&str>,
    needs_you: bool,
    now: Option<Timestamp>,
) -> Result<Value, BrainError> {
    if !STATUSES.contains(&status) {
        return Err(BrainError::new(format!("status is one of: {}.", STATUSES.join(", "))));
    }
    edit(team, |b| {
        let c = card(b, id)?;
        if c["status"] != status {
            c["moved_ms"] = json!(ms(now));
        }
        (c["status"], c["needs_you"]) = (json!(status), json!(needs_you));
        if let Some(n) = note {
            c["note"] = json!(clip(n, MAX_NOTE));
        }
        // said again since the merge: an undo no longer moves it
        c.as_object_mut().into_iter().for_each(|o| ["was", "merge"].iter().for_each(|k| drop(o.remove(*k))));
        Ok(c.clone())
    })
}

fn remove(team: &str, id: &str) -> Result<Value, BrainError> {
    edit(team, |b| {
        let id = card(b, id)?["id"].clone();
        b["cards"].as_array_mut().into_iter().for_each(|cards| cards.retain(|c| c["id"] != id));
        Ok(json!({"ok": true}))
    })
}

/// What the brain knows about a branch. The merge or discard stands even if the card can't be saved.
fn fact(team: &str, change: impl Fn(&mut Value)) {
    let _ = edit(team, |b| {
        b["cards"].as_array_mut().into_iter().flatten().for_each(change);
        Ok(())
    });
}

/// Review merged this agent's branch: its cards in Doing and Review are done. `undo` names the merge.
pub fn merged(team: &str, role: &str, base: &str, undo: &str, now: Option<Timestamp>) {
    fact(team, |c| {
        if c["role"] == role && (c["status"] == "doing" || c["status"] == "review") {
            c["was"] = json!([c["status"], c["note"]]);
            (c["merge"], c["status"], c["note"]) = (json!(undo), json!("done"), json!(format!("merged into {base}")));
            (c["needs_you"], c["moved_ms"]) = (json!(false), json!(ms(now)));
        }
    });
}

pub fn unmerged(team: &str, undo: &str, now: Option<Timestamp>) {
    fact(team, |c| {
        if c["merge"] == undo && c["was"].is_array() {
            (c["status"], c["note"]) = (c["was"][0].clone(), c["was"][1].clone());
            c.as_object_mut().into_iter().for_each(|o| ["was", "merge"].iter().for_each(|k| drop(o.remove(*k))));
            c["moved_ms"] = json!(ms(now));
        }
    });
}

pub fn discarded(team: &str, role: &str, now: Option<Timestamp>) {
    fact(team, |c| {
        if c["role"] == role && (c["status"] == "doing" || c["status"] == "review") {
            (c["status"], c["note"], c["needs_you"], c["moved_ms"]) =
                (json!("todo"), json!("Its work was discarded."), json!(false), json!(ms(now)));
        }
    });
}

fn shown(c: &Value) -> Value {
    let mut out: Map<String, Value> = ["id", "title", "role", "status", "by", "note", "needs_you"]
        .iter()
        .map(|k| (k.to_string(), c[*k].clone()))
        .collect();
    out.insert("moved_s".into(), json!(((ms(None) - c["moved_ms"].as_i64().unwrap_or(0)) / 1000).max(0)));
    Value::Object(out)
}

const TOLD: &str = "New task from the person, card {id} on the board: {title}\n\nCall set_task when you start it (doing) and when it is committed and ready to look at (review).";

/// The team boards: each card joined with what is true right now about its agent and branch.
pub struct Board {
    brain: Arc<Brain>,
    reviews: Arc<Review>,
    me: Box<Me>,
    rt: Runtime,
}

impl Board {
    pub fn new(brain: Arc<Brain>, reviews: Arc<Review>, me: Box<Me>) -> Board {
        Board { brain, reviews, me, rt: Runtime::new("assieme", None) }
    }

    fn who(&self, address: &str) -> Result<(String, String), BrainError> {
        (self.me)(address)
            .map(|(team, role, _)| (team, role))
            .ok_or_else(|| BrainError::new("This terminal is not an agent of an Assieme team."))
    }

    pub fn get(&self, team: &str) -> Result<Value, BrainError> {
        let built = self.brain.map(team)?;
        let panes = built["panes"].as_array().cloned().unwrap_or_default();
        let b = board(team);
        let cards: Vec<Value> = b["cards"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|c| {
                let mut c = shown(c);
                let pane = panes.iter().find(|p| !c["role"].is_null() && p["role"] == c["role"]);
                if let Some(p) = pane.filter(|_| c["status"] == "doing") {
                    c["agent"] = json!(
                        ["role", "state", "since_s", "detail", "fix", "reachable"]
                            .iter()
                            .map(|k| (*k, p[*k].clone()))
                            .collect::<BTreeMap<_, _>>()
                    );
                }
                if c["status"] == "review" && c["role"].as_str().is_some_and(|r| !r.is_empty()) {
                    c["branch"] = json!(self.reviews.card(team, c["role"].as_str().unwrap_or("")));
                }
                c
            })
            .collect();
        Ok(json!({"team": team, "title": b["title"], "cards": cards, "spend": built["spend"],
                  "spend_ready": built.get("spend_ready").unwrap_or(&json!(true))}))
    }

    /// The person's New task. A card given to a running agent is also sent to it: otherwise nobody would know.
    pub fn add(&self, team: &str, title: &str, role: Option<&str>) -> Result<Value, BrainError> {
        let role = role.filter(|r| !r.is_empty());
        let c = add(team, title, role, "you", None)?;
        let built = self.brain.map(team)?;
        let pane = role.and_then(|r| built["panes"].as_array()?.iter().find(|p| p["role"] == r));
        let told = pane.is_some_and(|p| p["live"] == true && p["reachable"] != false);
        if let (true, Some(p), Some(role)) = (told, pane, role) {
            let text = TOLD
                .replace("{id}", c["id"].as_str().unwrap_or(""))
                .replace("{title}", c["title"].as_str().unwrap_or(""));
            self.rt.send(p["address"].as_str().unwrap_or(""), &text, 30.0)?;
            live::record(Record {
                action: "send",
                agent: role,
                reason: "You gave it a task on the board.",
                team: Some(team),
                address: p["address"].as_str(),
                ..Default::default()
            });
        }
        Ok(json!({"card": shown(&c), "told": told}))
    }

    pub fn r#move(&self, team: &str, id: &str, status: &str) -> Result<Value, BrainError> {
        Ok(shown(&r#move(team, id, status, None, false, None)?))
    }

    // --- agents, each for its own team only ---

    pub fn tasks(&self, address: &str) -> Result<Value, BrainError> {
        let (team, me) = self.who(address)?;
        Ok(
            json!({"team": team, "you": me, "tasks": board(&team)["cards"].as_array().into_iter().flatten().map(shown).collect::<Vec<_>>()}),
        )
    }

    pub fn task_add(&self, address: &str, title: &str, assignee: &str) -> Result<Value, BrainError> {
        let (team, me) = self.who(address)?;
        let rows =
            self.rt.list(&BTreeMap::from([(crate::team::TEAM.to_string(), team.clone())]), true).unwrap_or_default();
        let roles: Vec<&str> = rows
            .iter()
            .filter(|r| r["state"] == "live")
            .filter_map(|r| r["labels"][crate::team::ROLE].as_str())
            .collect();
        let assignee =
            if ["me", "myself", "self"].contains(&assignee.trim().to_lowercase().as_str()) { &me } else { assignee };
        if !assignee.is_empty() && !roles.contains(&assignee) {
            return Err(BrainError::new(format!(
                "No running agent named {assignee}. You are {me}. Your team: {}.",
                roles.join(", ")
            )));
        }
        Ok(json!({"task": shown(&add(&team, title, Some(assignee), &me, None)?)}))
    }

    pub fn task_set(
        &self,
        address: &str,
        task: &str,
        status: &str,
        note: &str,
        waiting_for_person: bool,
    ) -> Result<Value, BrainError> {
        let (team, me) = self.who(address)?;
        let mut detail = "Done.".to_string();
        let mut role = card(&mut board(&team), task)?["role"].as_str().unwrap_or("").to_string();
        if role.is_empty() && (status == "doing" || status == "review") {
            // the agent working on it: its branch is the card's
            edit(&team, |b| {
                card(b, task)?["role"] = json!(me);
                Ok(())
            })?;
            role = me;
        }
        let mut status = status;
        if status == "done"
            && !role.is_empty()
            && self.reviews.card(&team, &role).is_some_and(|c| crate::pyrules::truthy(&c["ahead"]))
        {
            status = "review"; // a fact about the branch beats the agent's word
            detail = format!("{role}'s branch isn't merged yet, so the card is in Review. The person merges it there.");
        }
        Ok(
            json!({"task": shown(&r#move(&team, task, status, Some(note), waiting_for_person, None)?), "detail": detail}),
        )
    }

    pub fn handlers(this: Arc<Board>) -> Handlers {
        type F = fn(&Board, &Value) -> Result<Value, BrainError>;
        fn s<'a>(p: &'a Value, k: &str) -> &'a str {
            p[k].as_str().unwrap_or("")
        }
        let table: [(&'static str, F); 7] = [
            ("board.get", |b, p| b.get(s(p, "team"))),
            ("board.add", |b, p| b.add(s(p, "team"), s(p, "title"), p["role"].as_str())),
            ("board.move", |b, p| b.r#move(s(p, "team"), s(p, "id"), s(p, "status"))),
            ("board.remove", |_, p| remove(s(p, "team"), s(p, "id"))),
            ("mcp.tasks", |b, p| b.tasks(s(p, "address"))),
            ("mcp.task_add", |b, p| b.task_add(s(p, "address"), s(p, "title"), s(p, "assignee"))),
            ("mcp.task_set", |b, p| {
                b.task_set(
                    s(p, "address"),
                    s(p, "task"),
                    s(p, "status"),
                    s(p, "note"),
                    crate::pyrules::truthy(&p["waiting_for_person"]),
                )
            }),
        ];
        table
            .into_iter()
            .map(|(name, f)| {
                let board = this.clone();
                (name, Box::new(move |p: Value, _: &Call| f(&board, &p)) as Handler)
            })
            .collect()
    }
}
