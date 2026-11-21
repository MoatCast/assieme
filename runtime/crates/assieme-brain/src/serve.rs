//! `asm brain`'s table of methods: `brain.snapshot` and who asked.
//!
//! The socket itself, the 15-second loop and the error form are `server`. This is what the app reads.
use crate::prelude::*;
use crate::server::{Call, Handler};
use crate::{launch, limits, settings, setup, status, team, teammap};
use serde_json::json;
use std::cell::Cell;

thread_local! {
    static BY: Cell<By> = const { Cell::new(By::You) };
}

/// Who asked for the request this thread is serving: the assistant (step 18a, through the MCP server) or the person.
pub fn caller() -> By {
    BY.get()
}

/// A handler that says who asked while it runs, for `caller`.
pub fn tracked(h: Handler) -> Handler {
    Box::new(move |params: Value, call: &Call| {
        BY.set(call.by);
        let out = h(params, call);
        BY.set(By::You);
        out
    })
}

/// One request's reply.
pub fn handle(request: &Value, handlers: &Methods) -> Value {
    handlers.reply(request.to_string().as_bytes())
}

/// What the app reads: every agent, the teams, the queue and the plan limits.
pub fn snapshot(
    claude_dir: &Path,
    codex_dir: &Path,
    now: Timestamp,
    rt: &Runtime,
    starting: Vec<Value>,
) -> Result<Value, BrainError> {
    let all = status::collect(claude_dir, codex_dir, now, 12.0, false, None, status::running_cwds().as_ref(), None);
    let agents: Vec<Agent> = all.into_iter().filter(|a| !teammap::is_own_session(a)).collect();
    let (_, alerts) = status::summary(&agents);
    let teams = team::status(rt, None).unwrap_or_default(); // the assieme core isn't running: no Assieme teams yet
    let st = settings::load(None);
    let groups = st["groups"].as_object().cloned().unwrap_or_default();
    let live_cwds =
        teams.iter().filter(|t| t["state"] == "live").filter_map(|t| t["cwd"].as_str().map(String::from)).collect();
    // sorted names; a group may come as `{name, …}` or as its name
    let mut found: Vec<Value> = teammap::found_groups(&agents, &groups, &live_cwds)
        .iter()
        .map(|g| g.get("name").unwrap_or(g).clone())
        .collect();
    found.sort_by_key(Value::to_string);
    let clis: Map<String, Value> = launch::CLIS
        .iter()
        .chain(&[("asm", "asm")])
        .map(|(k, v)| (k.to_string(), setup::agent_path(v).is_some().into()))
        .collect();
    let rows = agents.iter().map(|a| {
        let mut row = a.as_dict(now);
        row["cwd"] = json!(a.cwd);
        row["herdr_address"] = json!(a.herdr_address);
        row["group"] = teammap::group_of(a, &groups).into();
        row
    });
    Ok(json!({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ").to_string(), "clis": clis, "agents": rows.collect::<Vec<_>>(),
              "found_teams": found, "first_run_done": st["first_run_done"], "alerts": alerts,
              "queue": status::owner_queue(&agents, now), "stalls": status::stalls(&agents, now), "teams": teams,
              "starting": starting, "limits": limits::read(claude_dir, codex_dir, now)?}))
}
