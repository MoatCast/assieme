//! Port of `assieme/actions.py`, owned by session C (see OWNERS.md).
//!
//! Step 18a: the brain's actions, described for assistants (Claude Code, Codex, the Claude app), so a session can do
//! what the app does. `assistant.actions` lists them and `assistant.do` runs one; the MCP server asks first when one
//! changes something.
use crate::prelude::*;
use crate::server::{By, Call, Handler};
use serde_json::json;

/// One action an assistant may run (step 18a).
pub struct Action {
    pub description: &'static str,
    pub params: Vec<(&'static str, &'static str)>,
    /// Python's default is False.
    pub changes: bool,
}

/// (name, description, params, whether it changes something). The brain serves actions that are neither here nor
/// among the app-only ones (`mcp.*`, `assistants.*` and a few more) only by mistake.
#[rustfmt::skip]
#[allow(clippy::type_complexity)]
const TABLE: &[(&str, &str, &[(&str, &str)], bool)] = &[
    ("teams.list", "Lists the teams running now and the saved ones that have stopped.", &[], false),
    ("team.view", "Shows a team: its agents, their states and what needs the person.", &[("team", "team name")], false),
    ("team.map", "Shows who wakes whom in a team, with the wake-ups between agents.", &[("team", "team name")], false),
    ("team.presets", "Lists the team presets New team offers.", &[], false),
    ("project.check", "Checks a project folder before starting a team in it.", &[("path", "full path of the folder")], false),
    ("team.suggest", "Suggests a team for a project, with sizes, a reviewer and a budget.", &[("project", "full path"), ("size", "small, medium or large"), ("review", "true or false"), ("budget", "daily limit in dollars, or null"), ("task", "optional: what the team works on; then size may be empty")], false),
    ("team.start", "Starts a team: its agents, each in its own terminal and branch.", &[("project", "full path"), ("task", "what the team works on"), ("roles", "list of roles as in New team"), ("practices", "team practices, {} for the defaults"), ("name", "optional team name")], true),
    ("team.stop", "Stops every agent of a team. Their branches and files stay.", &[("team", "team name")], true),
    ("team.add_agent", "Adds an agent to a running team.", &[("team", "team name"), ("role", "the role, as in New team")], true),
    ("team.allow_script", "Allows one setup or test script a team brought over may run.", &[("team", "team name"), ("which", "setup or test"), ("line", "the exact command line")], true),
    ("team.run_found", "Lets Assieme apply automatic fixes on a team found in herdr, or takes that back.", &[("team", "team name"), ("on", "true or false")], true),
    ("team.band", "Shows the instrument each agent plays.", &[("team", "team name")], false),
    ("team.improve", "Checks a running team and lists improvements to its setup.", &[("team", "team name")], false),
    ("team.improve_apply", "Applies chosen improvements to a team.", &[("team", "team name"), ("ids", "list of improvement ids")], true),
    ("team.improve_brief", "Drafts a better brief for one role.", &[("project", "full path"), ("role", "the role")], false),
    ("team.rule_draft", "Turns plain words into a permission rule for a team.", &[("project", "full path"), ("words", "the rule in plain words")], false),
    ("groups.set", "Saves how a large team groups its agents on the map.", &[("groups", "groups by team")], true),
    ("agent.detail", "Shows one agent: what is wrong, how full its memory is and who wakes it.", &[("team", "team name"), ("role", "agent name in the team")], false),
    ("agent.remove", "Removes an agent that couldn't start. Its branch and files stay.", &[("team", "team name"), ("role", "agent name in the team")], true),
    ("agent.restart", "Restarts an agent with its saved choice. Whatever it is doing stops.", &[("team", "team name"), ("role", "agent name in the team")], true),
    ("agent.tools", "Lists the skills and MCP servers an agent has on and off.", &[("team", "team name"), ("role", "agent name in the team")], false),
    ("agent.set_tools", "Turns an agent’s skills and MCP servers on or off.", &[("team", "team name"), ("role", "agent name in the team"), ("tools_off", "list of ids turned off"), ("tools_new", "true to add newly installed ones")], true),
    ("agent.set_musician", "Changes the instrument an agent plays. Only its look changes.", &[("team", "team name"), ("role", "agent name in the team"), ("musician", "one of the nine instruments")], true),
    ("tools.find", "Lists the skills and MCP servers installed for a kind of agent.", &[("kind", "agent kind"), ("project", "optional full path"), ("own_copy", "optional, true or false")], false),
    ("tools.suggest", "Suggests which skills and MCP servers a role needs.", &[("role_kind", "planner, builder, reviewer or tester"), ("kind", "agent kind"), ("project", "optional full path"), ("own_copy", "optional, true or false")], false),
    ("review.waiting", "Lists the agents whose work is ready to review.", &[], false),
    ("review.get", "Shows an agent’s changes and its checks: tests, conflicts with main, files touched.", &[("team", "team name"), ("role", "agent name in the team"), ("retest", "optional, true to run the tests again")], false),
    ("review.merge", "Merges an agent’s work into main.", &[("team", "team name"), ("role", "agent name in the team"), ("tip", "the commit shown by review.get"), ("force", "optional, true to merge despite failing checks")], true),
    ("review.ask", "Sends an agent a request for changes to its work.", &[("team", "team name"), ("role", "agent name in the team"), ("text", "what to change")], true),
    ("review.discard", "Throws away an agent’s work on its branch.", &[("team", "team name"), ("role", "agent name in the team"), ("tip", "the commit shown by review.get")], true),
    ("review.note", "Acts on a reviewer’s note: asks the builder to fix it, or ignores it.", &[("team", "team name"), ("role", "agent name in the team"), ("note", "note id"), ("action", "fix or ignore")], true),
    ("review.pull_request", "Opens a pull request for an agent’s work.", &[("team", "team name"), ("role", "agent name in the team")], true),
    ("review.undo", "Undoes a merge made in the last day.", &[("id", "the merge id")], true),
    ("board.get", "Shows a team’s task board: to do, doing, review and done.", &[("team", "team name")], false),
    ("board.add", "Adds a card to a team’s board.", &[("team", "team name"), ("title", "the task"), ("role", "optional agent to give it to")], true),
    ("board.move", "Moves a card to another column.", &[("team", "team name"), ("id", "card id"), ("status", "todo, doing, review or done")], true),
    ("board.remove", "Removes a card from a team’s board.", &[("team", "team name"), ("id", "card id")], true),
    ("brief.get", "Shows a team’s shared brief: goal, decisions and rules.", &[("team", "team name")], false),
    ("brief.save", "Changes a team’s shared brief.", &[("team", "team name"), ("goal", "optional new goal"), ("rules", "optional list of rules")], true),
    ("brief.remove", "Removes one decision from a team’s shared brief.", &[("team", "team name"), ("id", "decision id")], true),
    ("budget.set", "Sets a team’s daily spend limits.", &[("team", "team name"), ("limits", "limits per team and agent"), ("at_limit", "optional, pause or warn")], true),
    ("tip.apply", "Applies a suggested fix to a team.", &[("team", "team name"), ("tip", "tip id")], true),
    ("quiet.pairs", "Lists the pairs of agents whose routine notes wait until the receiver is free.", &[("team", "optional team name")], false),
    ("quiet.set", "Holds or stops holding routine notes between two agents.", &[("from_cwd", "sender folder"), ("to_cwd", "receiver folder"), ("label", "optional"), ("on", "optional, true or false")], true),
    ("activity.list", "Lists what Assieme did, with its reason, newest first.", &[("team", "optional team name")], false),
    ("activity.undo", "Undoes an action from Activity, where it can be undone.", &[("id", "activity id")], true),
    ("settings.get", "Shows Assieme’s settings.", &[], false),
    ("settings.set", "Changes one Assieme setting.", &[("key", "setting name, for example notify.needs_you"), ("value", "its new value")], true),
    ("notifications.pause", "Pauses Assieme’s Mac notifications for one hour.", &[], true),
    ("notifications.resume", "Turns Assieme’s Mac notifications back on.", &[], true),
    ("schedule.list", "Lists scheduled runs.", &[("teams", "optional, true to include their teams")], false),
    ("schedule.save", "Creates or changes a scheduled run.", &[("name", "run name"), ("project", "full path"), ("preset", "preset name"), ("task", "what to do"), ("every", "when it runs"), ("id", "optional, to change an existing run")], true),
    ("schedule.pause", "Pauses or resumes a scheduled run.", &[("id", "run id"), ("paused", "optional, true or false")], true),
    ("schedule.run_now", "Starts a scheduled run now.", &[("id", "run id")], true),
    ("schedule.stop", "Stops a scheduled run that is going.", &[("id", "run id")], true),
    ("schedule.remove", "Deletes a scheduled run.", &[("id", "run id")], true),
    ("preview.list", "Lists the live previews running.", &[], false),
    ("preview.start", "Starts an agent’s dev server, to see its changes.", &[("team", "team name"), ("role", "agent name in the team"), ("command", "optional command"), ("install", "optional, true to install first"), ("confirm", "optional")], true),
    ("preview.stop", "Stops an agent’s live preview.", &[("team", "team name"), ("role", "agent name in the team")], true),
    ("bringover.find", "Finds teams from other agent tools in a folder.", &[("folder", "optional full path")], false),
    ("bringover.workspaces", "Lists the workspaces of a team found in another tool.", &[("id", "id from bringover.find")], false),
    ("bringover.import", "Turns a team from another tool into a New team draft.", &[("id", "id from bringover.find"), ("ticked", "optional list")], true),
    ("herdr.find", "Finds herdr teams and launch scripts on this Mac.", &[], false),
    ("herdr.import", "Turns a herdr team into a New team draft.", &[("source", "what herdr.find returned")], true),
    ("remote.machines", "Lists the machines Assieme is set up on.", &[], false),
    ("remote.hosts", "Lists the SSH hosts this Mac knows.", &[], false),
    ("remote.check", "Checks that a machine is reachable and ready.", &[("machine", "machine name")], false),
    ("remote.test", "Tests an SSH connection and adds the machine to Assieme.", &[("target", "SSH host or user@address")], true),
    ("remote.connect", "Connects to a machine Assieme is set up on.", &[("machine", "machine name")], true),
    ("remote.setup", "Sets Assieme up on a machine over SSH.", &[("target", "SSH host or user@address")], true),
    ("remote.forget", "Removes a machine from Assieme. Its files stay.", &[("machine", "machine name")], true),
    ("remote.repos", "Lists the git projects in the home folder.", &[], false),
    ("setup.status", "Shows which agents are installed and signed in on this Mac.", &[], false),
    ("setup.commands", "Lists the install and sign-in commands for each kind of agent.", &[], false),
    ("setup.folder_preview", "Shows what turning a folder into a project would create.", &[("project", "full path")], false),
    ("setup.folder_save", "Turns a folder into a git project for a team.", &[("project", "full path")], true),
    ("setup.folder_create", "Creates a new project folder.", &[("path", "full path")], true),
    ("container.status", "Shows whether an agent kind’s container is built.", &[("agent", "agent kind")], false),
    ("container.build", "Builds the container an agent kind runs in.", &[("agent", "agent kind")], true),
];

/// Every action an assistant may run, by name.
pub static CATALOGUE: LazyLock<BTreeMap<&'static str, Action>> = LazyLock::new(|| {
    TABLE
        .iter()
        .map(|&(name, description, params, changes)| (name, Action { description, params: params.to_vec(), changes }))
        .collect()
});

// Set in the server entry of the plugin, the Claude app extension and the Codex plugin: the client asks before each
// change tool, so its own prompt is the person's yes. Here so the brain needn't load the SDK.
pub const CLIENT_ASKS: &str = "ASSIEME_CLIENT_ASKS";

/// The actions an assistant may use, optionally only those whose name or description holds `filter`.
fn listing(filter: &str) -> Value {
    let f = filter.to_lowercase();
    CATALOGUE.iter().filter(|(n, a)| n.contains(&f) || a.description.to_lowercase().contains(&f)).map(|(n, a)| {
        json!({"name": n, "description": a.description, "params": a.params.iter().copied().collect::<BTreeMap<_, _>>(), "changes": a.changes})
    }).collect()
}

/// Carry out one catalogued action through the brain's own handler. Any failure comes back as its plain message.
fn run(table: &Methods, name: &str, params: &Value, call: &Call) -> Result<Value, BrainError> {
    let Some(action) = CATALOGUE.get(name).filter(|_| table.names().contains(&name)) else {
        return Err(BrainError::new(format!("No action named {name}. app_actions lists them.")));
    };
    if params.as_object().is_some_and(|p| p.keys().any(|k| !action.params.iter().any(|(n, _)| n == k))) {
        let names: Vec<&str> = action.params.iter().map(|p| p.0).collect();
        return Err(BrainError::new(format!(
            "{name} takes: {}.",
            if names.is_empty() { "nothing".into() } else { names.join(", ") }
        )));
    }
    let by = if call.by == By::Assistant { "assistant" } else { "you" };
    let request = json!({"jsonrpc": "2.0", "id": 0, "method": name, "params": params, "by": by});
    let reply = table.reply(request.to_string().as_bytes());
    match reply["error"]["message"].as_str() {
        Some(message) => Err(BrainError::new(message)),
        None => Ok(reply["result"].clone()),
    }
}

/// `assistant.actions` and `assistant.do`, routed through `table`.
pub fn handlers(table: Arc<Methods>) -> Handlers {
    vec![
        (
            "assistant.actions",
            Box::new(|p: Value, _: &Call| Ok(listing(p["filter"].as_str().unwrap_or("")))) as Handler,
        ),
        (
            "assistant.do",
            Box::new(move |p: Value, call: &Call| run(&table, p["name"].as_str().unwrap_or(""), &p["params"], call)),
        ),
    ]
}
