//! Port of `assieme/agenttools.py`, owned by session D (see OWNERS.md).
//!
//! Step 32: the brain methods for one agent's skills, plugins and MCP servers.
use crate::live::Record;
use crate::prelude::*;
use crate::{newteam, preset, team, tools};
use serde_json::json;

pub fn find(kind: &str, project: Option<&str>, own_copy: bool) -> Value {
    tools::find(kind, project, own_copy)
}

pub fn suggest(role_kind: &str, kind: &str, project: Option<&str>, own_copy: bool) -> Value {
    json!(tools::suggest(role_kind, find(kind, project, own_copy)["items"].as_array().map_or(&[], Vec::as_slice)))
}

/// The team's folder, the team as loaded (`practices`: as it runs, not as its file saves it) and the role.
fn role_of(
    teams: &Path,
    team_name: &str,
    role_name: &str,
    practices: bool,
) -> Result<(PathBuf, preset::Team, Role), BrainError> {
    let folder = teams.join(team_name);
    let tm = preset::load(&folder, practices)?;
    let role = tm.roles.iter().find(|r| r.name == role_name).cloned();
    role.map(|r| (folder, tm, r)).ok_or_else(|| BrainError::new(format!("No agent named {role_name} in {team_name}.")))
}

/// What Agent details' picker needs: this agent's kind's items, its saved choice and its role's suggestion.
pub fn for_agent(teams: &Path, team_name: &str, role_name: &str) -> Result<Value, BrainError> {
    let (_, tm, r) = role_of(teams, team_name, role_name, true)?;
    let mut found = tools::find(&r.agent, Some(&tm.project), r.checkout == "branch" && r.worktree.is_none());
    let suggested =
        tools::suggest(r.kind.as_deref().unwrap_or(""), found["items"].as_array().map_or(&[], Vec::as_slice));
    found["value"] = json!({"tools_off": r.tools_off, "tools_new": r.tools_new});
    found["suggested"] = json!(suggested);
    found["role_kind"] = json!(r.kind);
    found["agent"] = json!(r.agent);
    Ok(found)
}

/// Save one agent's choice. It applies the next time the agent starts. Logged with an Undo.
pub fn set_tools(
    teams: &Path,
    team_name: &str,
    role_name: &str,
    tools_off: &[String],
    tools_new: bool,
) -> Result<Value, BrainError> {
    let (folder, tm, r) = role_of(teams, team_name, role_name, false)?;
    let change = json!({"tools_off": tools_off, "tools_new": tools_new});
    let roles: Vec<Value> = tm
        .roles
        .iter()
        .map(|x| {
            let mut d = newteam::role_dict(x);
            if x.name == role_name {
                d.as_object_mut().into_iter().for_each(|m| m.extend(change.as_object().cloned().unwrap_or_default()));
            }
            d
        })
        .collect();
    newteam::validate(&roles)?; // parents too
    // What was there before stays known, so an item uninstalled meanwhile comes back as it was, not as new.
    let known: BTreeSet<String> = if tools_new {
        BTreeSet::new()
    } else {
        let now = tools::find(&r.agent, Some(&tm.project), true);
        let ids = now["items"].as_array().into_iter().flatten().filter_map(|i| i["id"].as_str().map(String::from));
        r.tools_known.iter().cloned().chain(ids).collect()
    };
    let old = json!({"tools_off": r.tools_off, "tools_new": r.tools_new, "tools_known": r.tools_known});
    let new = json!({"tools_off": tools_off, "tools_new": tools_new, "tools_known": known});
    newteam::rewrite_role(&folder, role_name, new.as_object().unwrap_or(&Map::new()))?;
    let extra =
        json!({"title": format!("Changed the skills and servers of {role_name}"), "old": old, "folder": folder});
    Ok(crate::live::record(Record {
        action: "tools",
        agent: role_name,
        reason: "You changed its skills and servers. They apply when it next starts.",
        team: Some(team_name),
        extra: extra.as_object().cloned().unwrap_or_default(),
        ..Record::default()
    }))
}

/// Undo of a `tools` Activity row.
pub fn undo(e: &Value) -> Result<(), BrainError> {
    let folder =
        e["folder"].as_str().map_or_else(|| newteam::teams_dir().join(e["team"].as_str().unwrap_or("")), PathBuf::from);
    newteam::rewrite_role(&folder, e["agent"].as_str().unwrap_or(""), e["old"].as_object().unwrap_or(&Map::new()))
}

/// Stop the agent and start it again with its saved choice, picking up its conversation where the kind allows it
/// (Claude Code and Codex). The app asks the person first: anything it is doing right now stops.
pub fn restart(teams: &Path, rt: &Runtime, team_name: &str, role_name: &str) -> Result<Value, BrainError> {
    let (_, mut tm, _) = role_of(teams, team_name, role_name, true)?; // its brief keeps the team practices
    let labels = BTreeMap::from([(team::TEAM.to_string(), team_name.to_string())]);
    let rows = rt.list(&labels, true).unwrap_or_default(); // no core running: nothing of this team is
    let mine: Vec<&Value> = rows.iter().filter(|r| r["labels"][team::ROLE] == role_name).collect();
    for row in mine.iter().filter(|r| r["state"] == "live") {
        let _ = rt.kill(row["address"].as_str().unwrap_or(""));
    }
    // Only a role that ran has a conversation to pick up: Try again after a start that failed before it ran starts it
    // fresh, as `--continue` with nothing to continue ends the agent at once.
    // shortcut: "ran" means the core has a terminal for it, so one that ended before its first prompt (killed at
    // Claude Code's folder-trust question) still resumes; ask the agent's own session folder if that shows up.
    let ran = !mine.is_empty();
    tm.roles.iter_mut().filter(|x| x.name == role_name).for_each(|x| x.cont = ran);
    let title = json!({"title": format!("Restarted {role_name}")});
    crate::live::record(Record {
        action: "restart",
        agent: role_name,
        reason: "You restarted it with its skills and servers.",
        team: Some(team_name),
        extra: title.as_object().cloned().unwrap_or_default(),
        ..Record::default()
    });
    let (rt, team_name, role_name) = (rt.clone(), team_name.to_string(), role_name.to_string());
    std::thread::spawn(move || {
        // a failed start is a row in Activity, not a crash of the brain
        if let Err(err) = team::start(&tm, None, &rt, &|_| true, Some(&role_name)) {
            let reason = format!("It didn't start again: {}", err.message);
            crate::live::record(Record {
                action: "restart",
                agent: &role_name,
                reason: &reason,
                failed: true,
                team: Some(&team_name),
                ..Record::default()
            });
        }
    });
    Ok(json!({"ok": true, "detail": ""}))
}
