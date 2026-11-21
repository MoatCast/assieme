//! What the MCP server asks the brain. `asm mcp` is a thin process over these `mcp.*` methods; the rules on who
//! may do what without a question live with it (a question needs the client), and the actions themselves live here.
use crate::prelude::*;
use crate::server::Handler;
use crate::team::{BRIEFED, ROLE, TEAM};
use crate::{brief, bringover, budget, fixes, herdr, live, musicians, newteam, preset, quiet, reports, settings};
use serde_json::json;

const READ_MAX_LINES: i64 = 2000;
const READ_MAX_CHARS: usize = 50_000;
const KEEP_S: u64 = 24 * 3600;

type R = Result<Value, BrainError>;

fn fail<T>(m: impl Into<String>) -> Result<T, BrainError> {
    Err(BrainError::new(m))
}

fn label<'a>(row: &'a Value, key: &str) -> &'a str {
    row["labels"][key].as_str().unwrap_or("")
}

fn pane(p: &Value) -> Value {
    let mut out = json!({"agent": p["role"], "cli": p["agent"], "state": p["state"], "since_seconds": p["since_s"], "detail": p["detail"]});
    if let Some(fix) = p["fix"].as_object() {
        let buttons = fix["buttons"].as_array().cloned().unwrap_or_default();
        let labels = |kinds: &[&str], key: &str| -> Vec<Value> {
            buttons
                .iter()
                .filter(|b| kinds.contains(&b["kind"].as_str().unwrap_or("")))
                .map(|b| b[key].clone())
                .collect()
        };
        out["about"] = fix["text"].clone();
        out["can_answer_with"] = labels(&["menu", "answer"], "label").into();
        out["fixes"] = labels(&["action"], "action").into();
    }
    out
}

/// A copy the assistant can still start after the app took the draft into New team (step 18a, start_draft).
fn keep(drafts: &Path, draft: &Value) {
    let kept = drafts.join("kept");
    let _ = std::fs::create_dir_all(&kept);
    for entry in std::fs::read_dir(&kept).into_iter().flatten().flatten() {
        let age = entry.metadata().and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok());
        if age.is_some_and(|age| age.as_secs() > KEEP_S) {
            let _ = std::fs::remove_file(entry.path());
        }
    }
    let _ = quiet::write_json(&kept.join(format!("{}.json", draft["id"].as_str().unwrap_or(""))), draft);
}

pub struct Tools {
    rt: Runtime,
    brain: Arc<Brain>,
    starter: Arc<Starter>,
    scheduler: Option<Arc<crate::schedule::Scheduler>>,
    teams: PathBuf,
}

impl Tools {
    pub fn new(
        rt: Runtime,
        brain: Arc<Brain>,
        starter: Arc<Starter>,
        scheduler: Option<Arc<crate::schedule::Scheduler>>,
    ) -> Tools {
        Tools { rt, brain, starter, scheduler, teams: newteam::teams_dir() }
    }

    // --- agents inside Assieme ---------------------------------------------------------------------------

    /// (team, role, the terminal's row) of a terminal that is an agent of an Assieme team.
    pub fn me(&self, address: &str) -> Result<(String, String, Value), BrainError> {
        let info = self.rt.get(address)?;
        let team = label(&info, TEAM);
        if team.is_empty() {
            return fail("This terminal is not an agent of an Assieme team.");
        }
        let role = Some(label(&info, ROLE)).filter(|r| !r.is_empty()).unwrap_or(address);
        Ok((team.into(), role.into(), info))
    }

    /// The live terminals of a team.
    fn live(&self, team: &str) -> Vec<Value> {
        let labels = BTreeMap::from([(TEAM.to_string(), team.to_string())]);
        let rows = self.rt.list(&labels, true).unwrap_or_default();
        rows.into_iter().filter(|r| label(r, TEAM) == team && r["state"] == "live").collect()
    }

    fn teammates(&self, address: &str) -> R {
        let (name, me, _) = self.me(address)?;
        let roster = newteam::roster(&name, Some(&self.teams));
        let out: Vec<Value> = self
            .live(&name)
            .iter()
            .filter(|r| label(r, ROLE) != me)
            .map(|r| {
                let mate = roster.get(label(r, ROLE)).cloned().unwrap_or_default();
                json!({"name": label(r, ROLE), "job": mate["job"].as_str().unwrap_or(""), "reports_to": mate["parent"], "status": r["status"]["state"]})
            })
            .collect();
        Ok(json!({"team": name, "you": me, "teammates": out}))
    }

    fn message(&self, address: &str, to: &str, text: &str) -> R {
        let (name, me, info) = self.me(address)?;
        let rows = self.live(&name);
        let Some(target) = rows.iter().find(|r| label(r, ROLE) == to) else {
            let names: Vec<&str> = rows.iter().map(|r| label(r, ROLE)).filter(|r| *r != me).collect();
            let names = if names.is_empty() { "none".into() } else { names.join(", ") };
            return fail(format!("No running agent named {to}. Your teammates: {names}."));
        };
        if label(target, BRIEFED) != "1" {
            return fail(format!("{to} is still starting. Send this when it has started.")); // a message to a booting agent is dropped
        }
        let (target_address, held) =
            (target["address"].as_str().unwrap_or(""), |detail: String| json!({"sent": "held", "detail": detail}));
        if budget::hold(&name, to, &me, text) {
            return Ok(held(format!(
                "{to} is paused at a daily spend limit. It gets this when the person lets it carry on."
            )));
        }
        if fixes::hold(&name, to, &me, text) {
            return Ok(held(format!(
                "{to} is paused until the person resumes it. It gets this message when it carries on."
            )));
        }
        let (from_cwd, to_cwd) = (info["cwd"].as_str().unwrap_or(""), target["cwd"].as_str().unwrap_or(""));
        if !from_cwd.is_empty()
            && !to_cwd.is_empty()
            && quiet::is_quiet(from_cwd, to_cwd)
            && !quiet::urgent(text)
            && quiet::deliverer_alive(None)
        {
            let (session, sock) = (&self.rt.session, &self.rt.sock);
            quiet::hold_note(
                target_address,
                to_cwd,
                text,
                Some(session),
                Some(sock),
                None,
                &me,
                &quiet::real(from_cwd),
            );
            return Ok(held(format!("{to} gets this with its next turn. It was not woken.")));
        }
        reports::noted(&name, to);
        // The marker the logs already read, so the map still shows who woke whom.
        self.rt.send(target_address, &format!("<asm from=\"{address} {me}\">{text}</asm>"), 30.0)?;
        Ok(json!({"sent": "now", "detail": format!("Sent to {to}.")}))
    }

    fn report(&self, address: &str, state: &str, note: &str) -> R {
        let (name, me, info) = self.me(address)?;
        reports::save(&name, &me, state, note, None, reports::running_turn(&info))?;
        if state == "need_decision" && settings::load(None)["notify"]["needs_you"] == true {
            live::notify(&format!("{me} needs a decision"), &note.chars().take(200).collect::<String>());
        }
        if state == "done"
            && let Some(ends) = self.scheduler.as_ref().and_then(|s| s.done(&name, &me))
        {
            return Ok(json!({"ok": true, "detail": ends}));
        }
        let shown = reports::SHOWN.contains(&state);
        Ok(
            json!({"ok": true, "detail": if shown { "Reported. End your turn now if you cannot go on." } else { "Reported." }}),
        )
    }

    fn team_brief(&self, address: &str) -> R {
        let (name, ..) = self.me(address)?;
        let text = Some(brief::text(&name, true)).filter(|t| !t.is_empty());
        Ok(json!({"team": name, "brief": text.unwrap_or_else(|| "Nothing in the team brief yet.".into())}))
    }

    fn record_decision(&self, address: &str, question: &str, answer: &str) -> R {
        let (name, me, _) = self.me(address)?;
        brief::add(&name, &me, question, answer, "agent", None)?;
        Ok(json!({"ok": true, "detail": "Saved in the team brief."}))
    }

    // --- reading -----------------------------------------------------------------------------------------

    fn list_teams(&self) -> R {
        let running = self.brain.teams();
        let here: BTreeSet<&str> =
            running["teams"].as_array().into_iter().flatten().filter_map(|t| t["name"].as_str()).collect();
        let mut saved: Vec<String> = std::fs::read_dir(&self.teams)
            .into_iter()
            .flatten()
            .flatten()
            .filter(|d| d.path().join("team.toml").is_file())
            .map(|d| d.file_name().to_string_lossy().into_owned())
            .collect();
        saved.sort();
        saved.retain(|n| !here.contains(n.as_str()));
        Ok(json!({"running": running["teams"], "saved_and_stopped": saved}))
    }

    fn status(&self, team: &str) -> R {
        let built = self.brain.map(team)?;
        let panes = built["panes"].as_array().cloned().unwrap_or_default();
        if panes.is_empty() {
            return fail(format!("No team named {team} is running."));
        }
        Ok(json!({"team": team, "title": built["title"], "summary": built["subtitle"], "stopped": built["stopped"],
                  "started_outside_assieme": built["found"], "agents": panes.iter().map(pane).collect::<Vec<_>>(),
                  "tokens_today": built["spend"]["tokens"]}))
    }

    fn needs_you(&self) -> R {
        let mut out = vec![];
        for t in self.brain.teams()["teams"].as_array().into_iter().flatten() {
            let name = t["name"].as_str().unwrap_or("");
            for p in self.brain.map(name)?["panes"].as_array().into_iter().flatten() {
                if crate::teammap::needs_you(p) || p["fix"]["budget"].as_bool().unwrap_or(false) {
                    let mut row = pane(p);
                    row["team"] = name.into();
                    out.push(row);
                }
            }
        }
        Ok(json!({"needs_you": out}))
    }

    fn pane_of(&self, team: &str, role: &str) -> R {
        let built = self.brain.map(team)?;
        match built["panes"].as_array().and_then(|ps| ps.iter().find(|p| p["role"] == role)) {
            Some(p) => Ok(p.clone()),
            None => fail(format!("No agent named {role} in {team}.")),
        }
    }

    /// The agent's pane, when Assieme can reach its terminal.
    fn reach(&self, team: &str, role: &str) -> R {
        let p = self.pane_of(team, role)?;
        if p["reachable"] == false {
            return fail(format!("{role} wasn't started in a terminal Assieme can reach, so it can only be watched."));
        }
        Ok(p)
    }

    fn read(&self, team: &str, role: &str, lines: i64) -> R {
        let p = self.reach(team, role)?;
        let n = lines.clamp(1, READ_MAX_LINES);
        let screen = self.rt.read(p["address"].as_str().unwrap_or(""), n)?;
        let all: Vec<&str> = screen.lines().collect();
        let text = all[all.len().saturating_sub(n as usize)..].join("\n");
        let from = text.char_indices().rev().nth(READ_MAX_CHARS - 1).map_or(0, |(i, _)| i);
        Ok(json!({"text": &text[from..]}))
    }

    // --- acting (the MCP process has already got the person's yes, or the setting says Automatic) --------

    fn send(&self, team: &str, role: &str, text: &str) -> R {
        let p = self.reach(team, role)?;
        let address = p["address"].as_str().unwrap_or("");
        self.rt.send(address, text, 30.0)?;
        live::record(live::Record {
            action: "send",
            agent: role,
            reason: "You approved this from your assistant.",
            team: Some(team),
            address: Some(address),
            ..Default::default()
        });
        Ok(json!({"ok": true, "detail": format!("Sent to {role}.")}))
    }

    fn answer(&self, team: &str, role: &str, answer: &str) -> R {
        let p = self.reach(team, role)?;
        let fix = &p["fix"];
        if p["state"] != "asking" || fix.is_null() {
            return Ok(json!({"ok": false, "detail": format!("{role} isn't asking anything right now.")}));
        }
        let is = |b: &&Value, kinds: &[&str]| kinds.contains(&b["kind"].as_str().unwrap_or(""));
        let buttons = fix["buttons"].as_array().cloned().unwrap_or_default();
        let choices: Vec<&Value> = buttons.iter().filter(|b| is(b, &["menu", "answer"])).collect();
        let wanted = answer.trim().to_lowercase();
        let lower = |v: &Value| v.as_str().unwrap_or("").to_lowercase();
        if let Some(b) = choices.iter().find(|b| wanted == lower(&b["label"]) || wanted == lower(&b["send"])) {
            return self.brain.act(team, role, fix["id"].as_str().unwrap_or(""), b["id"].as_str().unwrap_or(""), "you");
        }
        if choices.iter().any(|b| b["kind"] == "menu") {
            let labels: Vec<&str> = choices.iter().filter_map(|b| b["label"].as_str()).collect();
            return Ok(json!({"ok": false, "detail": format!("Choose one of: {}", labels.join(", "))}));
        }
        let address = p["address"].as_str().unwrap_or("");
        self.rt.send(address, answer, 30.0)?;
        live::record(live::Record {
            action: "answer",
            agent: role,
            reason: crate::panes::ANSWERED,
            team: Some(team),
            address: Some(address),
            ..Default::default()
        });
        if let Some(q) = fix["question"].as_str().filter(|q| !q.is_empty()) {
            brief::add(team, role, q, answer, "you", None)?;
        }
        Ok(json!({"ok": true, "detail": "sent"}))
    }

    fn fix_button(p: &Value, action: &str) -> Option<Value> {
        p["fix"]["buttons"].as_array()?.iter().find(|b| b["action"] == action).cloned()
    }

    /// Whether this fix is on offer right now, and whether its setting lets it run without asking.
    fn fix_check(&self, team: &str, role: &str, action: &str) -> R {
        let built = self.brain.map(team)?;
        let p = built["panes"].as_array().and_then(|ps| ps.iter().find(|p| p["role"] == role));
        let Some(p) = p.filter(|p| Self::fix_button(p, action).is_some()) else {
            return Ok(json!({"available": false, "why": format!("{role} doesn't need this right now.")}));
        };
        // The same limits as teammap's automatic fixes.
        let auto = settings::load(None)["fixes"][action] == "auto"
            && built["found"] != true
            && (action != "compact" || p["memory_full"] == true);
        Ok(json!({"available": true, "auto": auto, "text": p["fix"]["text"]}))
    }

    fn fix(&self, team: &str, role: &str, action: &str, by: &str) -> R {
        let p = self.pane_of(team, role)?;
        match Self::fix_button(&p, action) {
            Some(b) => {
                self.brain.act(team, role, p["fix"]["id"].as_str().unwrap_or(""), b["id"].as_str().unwrap_or(""), by)
            }
            None => Ok(json!({"ok": false, "detail": format!("{role} doesn't need this right now.")})),
        }
    }

    fn stop_agent(&self, team: &str, role: &str) -> R {
        let rows = self.live(team);
        let Some(row) = rows.iter().find(|r| label(r, ROLE) == role) else {
            return fail(format!("No running agent named {role} in {team}."));
        };
        let address = row["address"].as_str().unwrap_or("");
        self.rt.kill(address)?;
        let _ = newteam::forget_role(&self.teams.join(team), role); // no saved folder to tidy: the agent itself has stopped
        let why = "You approved this from your assistant. Its branch and files stay.";
        live::record(live::Record {
            action: "stop_agent",
            agent: role,
            reason: why,
            team: Some(team),
            address: Some(address),
            ..Default::default()
        });
        Ok(json!({"ok": true, "detail": format!("Stopped {role}. Its branch and files stay.")}))
    }

    /// Start a stopped team again. A new team only ever starts from New team, with the person's click.
    fn start_saved(&self, team: &str, task: &str) -> R {
        let Ok(tm) = preset::load(&self.teams.join(team), false) else {
            return fail(format!("No saved team named {team}."));
        };
        let roles: Vec<Value> = tm.roles.iter().map(newteam::role_dict).collect();
        self.starter.start(&tm.project, task, &roles, &Value::Object(tm.practices), None, None, &[])
    }

    // --- drafts ------------------------------------------------------------------------------------------

    fn drafts() -> PathBuf {
        quiet::state_dir().join("drafts")
    }

    /// Checked like New team's form. It starts nothing: the app opens it in New team for the person to Start.
    fn draft_save(
        &self,
        project: &str,
        roles: Vec<Value>,
        task: &str,
        practices: Option<Map<String, Value>>,
        why: &str,
    ) -> R {
        let check = newteam::project_check(project);
        if check["ok"] != true {
            let message = check["reason"].as_str().unwrap_or("").to_string();
            return Err(newteam::NewTeamError { field: "project".into(), message, detail: String::new() }.into());
        }
        let mut roles: Vec<Map<String, Value>> = roles.into_iter().filter_map(|r| r.as_object().cloned()).collect();
        for r in &mut roles {
            if !r.get("musician").and_then(Value::as_str).is_some_and(|m| musicians::INSTRUMENTS.contains(&m)) {
                r.remove("musician");
            }
        }
        let picked = musicians::pick(
            &roles
                .iter()
                .map(|r| (r.get("kind").and_then(Value::as_str), r.get("musician").and_then(Value::as_str)))
                .collect::<Vec<_>>(),
        );
        let tool = regex::Regex::new(newteam::TOOL_ID).expect("a fixed pattern");
        let mut filled = vec![];
        for (mut r, m) in roles.into_iter().zip(picked) {
            r.insert("musician".into(), m.into());
            for key in ["worktree", "branch", "resume"] {
                r.remove(key); // only a bring-over sets them
            }
            for key in ["tools_off", "tools_known"] {
                // an assistant's ids are untrusted: keep only well-formed ones
                if let Some(v) = r.get_mut(key) {
                    *v = v.as_array().map_or(json!([]), |a| {
                        a.iter().filter(|x| x.as_str().is_some_and(|s| tool.is_match(s))).cloned().collect()
                    });
                }
            }
            if r.get("tools_new").is_some_and(|v| *v != false) {
                r.insert("tools_new".into(), true.into());
            }
            let kind = r.remove("kind").and_then(|k| k.as_str().map(String::from));
            if let Some((_, (_, checkout))) = newteam::KINDS.iter().find(|(k, _)| Some(*k) == kind.as_deref()) {
                r.entry("checkout").or_insert((*checkout).into());
                if r.get("brief").and_then(Value::as_str).is_none_or(|b| b.trim().is_empty()) {
                    r.insert("brief".into(), newteam::kind_brief(kind.as_deref().unwrap_or("")).into());
                }
            }
            r.entry("checkout").or_insert("branch".into());
            r.entry("job").or_insert("".into());
            r.entry("parent").or_insert(Value::Null);
            filled.push(Value::Object(r));
        }
        newteam::validate(&filled)?;
        let mut chosen = settings::load(None)["practices"].as_object().cloned().unwrap_or_default();
        chosen.extend(practices.unwrap_or_default());
        let known = preset::keys();
        let unknown: Vec<&str> = chosen.keys().filter(|k| !known.contains(*k)).map(String::as_str).collect();
        if !unknown.is_empty() {
            return fail(format!("Unknown practice: {}.", unknown.join(", ")));
        }
        let id = uuid::Uuid::new_v4().simple().to_string()[..12].to_string();
        let project = preset::absolute(Path::new(project)).to_string_lossy().into_owned();
        let draft =
            json!({"id": id, "project": project, "task": task, "roles": filled, "practices": chosen, "why": why});
        let _ = quiet::write_json(&Self::drafts().join(format!("{id}.json")), &draft);
        keep(&Self::drafts(), &draft);
        let opened =
            std::process::Command::new("open").args(["-b", "dev.assieme.app"]).status().is_ok_and(|s| s.success());
        Ok(json!({"id": id, "opened": opened}))
    }

    /// A project from another tool (step 30) as a New team draft for the app to open. It starts nothing and
    /// changes nothing of the other tool's.
    fn bringover_import(&self, id: &str, ticked: &[String]) -> R {
        let mut draft = bringover::draft(id, ticked)?;
        let roles: Vec<Value> = draft["roles"].as_array().cloned().unwrap_or_default();
        if id.starts_with("herdr:") && roles.is_empty() {
            let notes: Vec<&str> =
                draft["notes"].as_array().into_iter().flatten().filter_map(|n| n["text"].as_str()).collect();
            return fail(format!("No Claude Code or Codex agents were found in it. {}", notes.join(" ")));
        }
        newteam::validate(&roles)?;
        draft["id"] = uuid::Uuid::new_v4().simple().to_string()[..12].into();
        draft["why"] = "".into();
        draft["practices"] = settings::load(None)["practices"].clone();
        Ok(draft)
    }

    /// The oldest draft waiting to open, or None. The app asks often, so this only lists a folder.
    pub fn draft_next(&self) -> Option<String> {
        let ages = std::fs::read_dir(Self::drafts())
            .ok()?
            .flatten()
            .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
            .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path()))); // gone: the app took it just now
        ages.min().and_then(|(_, p)| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
    }

    fn draft_get(&self, id: &str) -> Value {
        let path = Self::drafts().join("kept").join(format!("{}.json", base(id)));
        std::fs::read_to_string(path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .filter(Value::is_object)
            .unwrap_or(Value::Null)
    }

    fn draft_take(&self, id: &str) -> Value {
        let path = Self::drafts().join(format!("{}.json", base(id)));
        let Ok(text) = std::fs::read_to_string(&path) else { return Value::Null };
        match serde_json::from_str::<Value>(&text) {
            Ok(draft) if draft.is_object() => {
                let _ = std::fs::remove_file(&path);
                draft
            }
            _ => {
                // kept to look at, but no longer in the way of the next draft
                let _ = std::fs::rename(&path, path.with_extension("json.damaged"));
                Value::Null
            }
        }
    }

    pub fn handlers(this: Arc<Tools>) -> Handlers {
        type F = fn(&Tools, &Value) -> R;
        fn s(p: &Value, k: &str) -> String {
            p[k].as_str().unwrap_or("").to_string()
        }
        let table: [(&'static str, F); 22] = [
            ("mcp.teammates", |t, p| t.teammates(&s(p, "address"))),
            ("mcp.message", |t, p| t.message(&s(p, "address"), &s(p, "to"), &s(p, "text"))),
            ("mcp.report", |t, p| t.report(&s(p, "address"), &s(p, "state"), &s(p, "note"))),
            ("mcp.list_teams", |t, _| t.list_teams()),
            ("mcp.status", |t, p| t.status(&s(p, "team"))),
            ("mcp.needs_you", |t, _| t.needs_you()),
            ("mcp.read", |t, p| t.read(&s(p, "team"), &s(p, "role"), p["lines"].as_i64().unwrap_or(200))),
            ("mcp.send", |t, p| t.send(&s(p, "team"), &s(p, "role"), &s(p, "text"))),
            ("mcp.answer", |t, p| t.answer(&s(p, "team"), &s(p, "role"), &s(p, "answer"))),
            ("mcp.fix_check", |t, p| t.fix_check(&s(p, "team"), &s(p, "role"), &s(p, "action"))),
            ("mcp.fix", |t, p| {
                let by = p["by"].as_str().unwrap_or("you");
                t.fix(&s(p, "team"), &s(p, "role"), &s(p, "action"), by)
            }),
            ("mcp.stop_agent", |t, p| t.stop_agent(&s(p, "team"), &s(p, "role"))),
            ("mcp.start_saved", |t, p| t.start_saved(&s(p, "team"), &s(p, "task"))),
            ("mcp.draft_save", |t, p| {
                let roles = p["roles"].as_array().cloned().unwrap_or_default();
                let practices = p["practices"].as_object().cloned();
                t.draft_save(&s(p, "project"), roles, &s(p, "task"), practices, &s(p, "why"))
            }),
            ("draft.take", |t, p| Ok(t.draft_take(&s(p, "id")))),
            ("mcp.draft_get", |t, p| Ok(t.draft_get(&s(p, "id")))),
            ("herdr.find", |_, _| Ok(herdr::find())),
            ("herdr.import", |t, p| t.bringover_import(&format!("herdr:{}", s(p, "source")), &[])),
            ("mcp.brief", |t, p| t.team_brief(&s(p, "address"))),
            ("mcp.record_decision", |t, p| t.record_decision(&s(p, "address"), &s(p, "question"), &s(p, "answer"))),
            ("brief.get", |_, p| Ok(brief::load(&s(p, "team")))),
            ("brief.save", |_, p| brief::save(&s(p, "team"), p["goal"].as_str(), p["rules"].as_str())),
        ];
        let mut out: Handlers = table
            .into_iter()
            .map(|(name, f)| {
                let this = this.clone();
                let h: Handler = Box::new(move |p, _| f(&this, &p));
                (name, h)
            })
            .collect();
        out.push((
            "brief.remove",
            Box::new(|p, _| brief::remove(p["team"].as_str().unwrap_or(""), p["id"].as_str().unwrap_or(""))),
        ));
        out.push((
            "bringover.import",
            Box::new(move |p, _| {
                let ticked: Vec<String> = serde_json::from_value(p["ticked"].clone()).unwrap_or_default();
                this.bringover_import(p["id"].as_str().unwrap_or(""), &ticked)
            }),
        ));
        out
    }
}

fn base(id: &str) -> &str {
    id.rsplit('/').next().unwrap_or(id)
}
