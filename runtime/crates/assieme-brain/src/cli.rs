//! The `assieme` command (`asm cli …`).
//!
//! assieme            help
//! assieme status     one snapshot of team health
//! assieme team       start, list and stop a team from a preset
//! assieme quiet      quiet send: the asm stand-in on or off
//! assieme mcp        the MCP server for assistants and for agents in a team (stdio)
//! assieme serve      the brain as a background service
use crate::prelude::*;
use crate::serve::tracked;
use crate::server::{Handler, Loop};
use crate::{
    actions, activity, bridge, bringover, connect, live, machine, menubar, newteam, preset, preview, quiet, remote,
    review,
};
use crate::{schedule, scripts, serve, settings, setup, status, tasks, team, teammap};
use clap::{Parser, Subcommand, ValueEnum};
use serde::Deserialize;
use serde::de::DeserializeOwned;
use serde_json::json;
use std::collections::BTreeMap;

#[derive(Parser)]
#[command(
    name = "assieme",
    about = "Team health, what needs you, and one-key fixes. Commands give snapshots and reports.",
    arg_required_else_help = true
)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Live health of each agent: working, stuck, waiting on you, ...
    Status {
        /// Agents with log activity in this window.
        #[arg(long, default_value_t = 12.0)]
        hours: f64,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        claude_dir: Option<PathBuf>,
        #[arg(long)]
        codex_dir: Option<PathBuf>,
        /// Show command arguments (off: program names only).
        #[arg(long)]
        show_text: bool,
    },
    /// Quiet send: put the asm stand-in on (or off) for your agents.
    Quiet { switch: Switch },
    /// Start, list and stop a team of agents from a preset.
    Team {
        #[command(subcommand)]
        cmd: TeamCmd,
    },
    /// The MCP server for assistants and for agents in a team (stdio).
    Mcp,
    /// The brain as a background service (the app talks to it).
    Serve,
}

#[derive(Clone, ValueEnum)]
enum Switch {
    On,
    Off,
    Status,
}

#[derive(Subcommand)]
enum TeamCmd {
    /// Start every role in a preset folder (team.toml plus briefs).
    Start {
        preset: PathBuf,
        /// Sent to every agent after its brief.
        #[arg(long)]
        task: Option<String>,
        /// Start just this role (for example to retry it).
        #[arg(long, value_name = "ROLE")]
        only: Option<String>,
    },
    /// The agents of one team, or of every team.
    Status {
        name: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Stop every running agent of a team.
    Stop { name: String },
}

pub fn main(args: Vec<String>) -> i32 {
    let cli = match Cli::try_parse_from(std::iter::once("assieme".to_string()).chain(args)) {
        Ok(cli) => cli,
        Err(e) => {
            let _ = e.print();
            return e.exit_code();
        }
    };
    let home = PathBuf::from(crate::rules::home());
    match cli.cmd {
        Cmd::Status { hours, json, claude_dir, codex_dir, show_text } => {
            let (claude, codex) = (
                claude_dir.unwrap_or(home.join(".claude/projects")),
                codex_dir.unwrap_or(home.join(".codex/sessions")),
            );
            let now = status::now_utc();
            let agents = status::collect(&claude, &codex, now, hours, show_text, status::running_cwds().as_ref(), None);
            print!("{}", if json { status::to_json(&agents, now) + "\n" } else { status::to_text(&agents, now) });
            0
        }
        Cmd::Quiet { switch } => quiet_command(&switch),
        Cmd::Team { cmd } => match team_command(cmd) {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("assieme: {}", e.message);
                e.detail.iter().for_each(|d| eprintln!("{d}"));
                1
            }
        },
        Cmd::Mcp => crate::mcpserver::main(),
        Cmd::Serve => crate::run(vec!["--socket".into(), quiet::state_dir().join("brain.sock").into()]),
    }
}

fn team_command(cmd: TeamCmd) -> Result<(), BrainError> {
    let rt = Runtime::new("assieme", None);
    match cmd {
        TeamCmd::Stop { name } => println!("stopped {} agents", team::stop(&rt, &name)?),
        TeamCmd::Status { name, json } => {
            let rows = team::status(&rt, name.as_deref())?;
            if json {
                println!("{}", serde_json::to_string_pretty(&rows).unwrap_or_default());
            } else if rows.is_empty() {
                println!("no Assieme teams running{}", name.map(|n| format!(" named {n}")).unwrap_or_default());
            }
            let s = |r: &Value, k: &str, or: &str| r[k].as_str().filter(|x| !x.is_empty()).unwrap_or(or).to_string();
            for r in rows.iter().filter(|_| !json) {
                println!(
                    "{:<20} {:<16} {:<6} {:<9} {}",
                    s(r, "team", ""),
                    s(r, "role", "?"),
                    s(r, "address", ""),
                    s(r, "state", ""),
                    s(r, "status", "-")
                );
            }
        }
        TeamCmd::Start { preset, task, only } => {
            let tm = preset::load(&preset, true)?;
            let ask = |question: &str| {
                eprint!("{question} [y/N] ");
                let mut line = String::new();
                std::io::stdin().read_line(&mut line).is_ok()
                    && matches!(line.trim().to_lowercase().as_str(), "y" | "yes")
            };
            let shown = preset.display();
            for s in team::start(&tm, task.as_deref(), &rt, &ask, only.as_deref())? {
                let address = s.address.as_deref().map(|a| format!("  {a}")).unwrap_or_default();
                println!("{:<16} {}{address}", s.role, s.state);
                s.note.lines().for_each(|l| println!("    {l}"));
                match s.state.as_str() {
                    "needs-you" => println!(
                        "    open it: ASM_SESSION={} ASM_SOCKET={} asm attach {}\n    then: assieme team start {shown}",
                        rt.session,
                        rt.sock.display(),
                        s.address.as_deref().unwrap_or("")
                    ),
                    "exited" | "not-reporting" => println!("    retry: assieme team start {shown} --only {}", s.role),
                    _ => {}
                }
            }
        }
    }
    Ok(())
}

fn quiet_command(switch: &Switch) -> i32 {
    match switch {
        Switch::On => match quiet::install() {
            Ok(dir) => println!(
                "Quiet send is installed.\n\nAgents pick it up when their PATH starts with this folder. Add this line to the script that starts your\nagents, before it starts them, then restart them:\n\n    export PATH=\"{}:$PATH\"\n\nNothing changes until you also mark a pair quiet in the app.\nUndo with `assieme quiet off`.",
                dir.display()
            ),
            Err(e) => {
                eprintln!("assieme: couldn't install quiet send: {e}");
                return 1;
            }
        },
        Switch::Off => {
            quiet::uninstall();
            println!(
                "Quiet send is off: the asm stand-in is removed. Held notes are still delivered while assieme runs."
            );
        }
        Switch::Status => {
            let pairs: Vec<String> =
                quiet::pairs().iter().filter_map(|p| p["label"].as_str().map(String::from)).collect();
            let yes = |b: bool| if b { "yes" } else { "no" };
            println!("stand-in installed: {}", yes(quiet::shim_dir().join("asm").exists()));
            println!("deliverer running: {}", yes(quiet::deliverer_alive(None)));
            println!("quiet pairs: {}", if pairs.is_empty() { "none".into() } else { pairs.join(", ") });
            println!("notes held: {}", quiet::held().values().map(Vec::len).sum::<usize>());
        }
    }
    0
}

/// A handler that reads its params as `P`; a missing one is the reply's error.
fn typed<P: DeserializeOwned>(f: impl Fn(P) -> Result<Value, BrainError> + Send + Sync + 'static) -> Handler {
    Box::new(move |p, _| f(serde_json::from_value(p).map_err(|e| BrainError::new(e.to_string()))?))
}

#[derive(Deserialize)]
struct Origin {
    tool: String,
    label: String,
}

/// `team.start` and what comes with a team brought over (step 30): `scripts`, `allow_scripts`, `cards` and `origin`.
#[derive(Deserialize)]
struct StartTeam {
    project: String,
    task: String,
    roles: Vec<Value>,
    practices: Value,
    name: Option<String>,
    scripts: Option<BTreeMap<String, String>>,
    #[serde(default)]
    allow_scripts: Vec<String>,
    #[serde(default)]
    cards: Vec<Value>,
    origin: Option<Origin>,
}

/// The brain's methods and its 15-second loop, as `serve` ran them. `asm brain` takes these at the cutover.
pub fn brain() -> (Arc<Methods>, Loop) {
    let home = PathBuf::from(crate::rules::home());
    let (claude, codex) = (home.join(".claude/projects"), home.join(".codex/sessions"));
    let (rt, teams) = (Runtime::new("assieme", None), newteam::teams_dir());
    let scheduled = || schedule::load().iter().filter_map(|x| x["team"].as_str().map(String::from)).collect();
    let starter = Arc::new(Starter::new(rt.clone(), teams.clone(), Box::new(scheduled)));
    let brain =
        Arc::new(Brain::new(claude.clone(), codex.clone(), rt.clone(), starter.clone(), Arc::new(Answers::new())));
    let reviews = Arc::new(review::Review::new(rt.clone(), teams));
    activity::register_merge(reviews.clone());
    let previews = Arc::new(preview::Previews::new(reviews.clone()));
    previews.stop_left_behind();
    let stopper = previews.clone();
    reviews.set_on_archive(Box::new(move |team, role| stopper.stop(team, role))); // archiving a review stops its preview
    let menu = Arc::new(menubar::Menubar::new(brain.clone(), reviews.clone()));
    let watching = {
        let menu = menu.clone();
        move || menu.app_watching(None)
    };
    let announce = {
        let watching = watching.clone();
        move |title: &str, body: &str| {
            if !watching() {
                live::notify(title, body)
            }
        }
    };
    let (start, records) = (starter.clone(), starter.clone());
    let sched = Arc::new(schedule::Scheduler::new(
        rt.clone(),
        Box::new(move |p, t, r, pr, n| start.start(p, t, r, pr, n, None, &[])),
        Box::new(move || records.records()),
        Some(Box::new(announce)),
    ));
    let tools = Arc::new(crate::mcptools::Tools::new(rt.clone(), brain.clone(), starter.clone(), Some(sched.clone())));
    let me = tools.clone();
    let board = Arc::new(tasks::Board::new(brain.clone(), reviews.clone(), Box::new(move |a| me.me(a).ok())));
    let remotes = Arc::new(remote::Remotes::new(rt.clone()));

    let mut table: Handlers = vec![];
    let snapshot = {
        let (rt, starter, tools, sched) = (rt.clone(), starter.clone(), tools.clone(), sched.clone());
        let (claude, codex) = (claude.clone(), codex.clone());
        move |_: Value, _: &_| {
            let now = status::now_utc();
            let mut out = serve::snapshot(&claude, &codex, now, &rt, starter.records())?;
            out["draft"] = json!(tools.draft_next());
            out["scheduled_teams"] = json!(sched.run_teams());
            Ok(out)
        }
    };
    table.push(("brain.snapshot", Box::new(snapshot)));
    table.push((
        "project.check",
        typed(|p: BTreeMap<String, String>| Ok(newteam::project_check(p.get("path").map_or("", String::as_str)))),
    ));
    table.push(("team.presets", Box::new(|_, _| Ok(newteam::presets(None)))));
    let start_team = {
        let remember = previews.clone();
        typed(move |p: StartTeam| {
            let (mut roles, mut skipped) = (p.roles, vec![]);
            if let Some(o) = &p.origin {
                (roles, skipped) = bringover::not_running(&roles, &o.tool);
            }
            let scripts = p.scripts.unwrap_or_default();
            let mut got = starter.start(
                &p.project,
                &p.task,
                &roles,
                &p.practices,
                p.name.as_deref(),
                Some(&scripts),
                &p.allow_scripts,
            )?;
            tasks::fresh(got["team"].as_str().unwrap_or(""), &p.task); // a new team, a new board; restarting a saved team keeps its cards
            if let Some(o) = &p.origin {
                let origin = json!({"tool": o.tool, "label": o.label});
                bringover::started(
                    got["team"].as_str().unwrap_or(""),
                    &p.project,
                    &roles,
                    &p.cards,
                    &scripts,
                    &origin,
                    &|a, b| remember.remember(a, b),
                );
            }
            if !skipped.is_empty() {
                got["skipped"] = skipped.into();
            }
            Ok(got)
        })
    };
    table.push(("team.start", start_team));
    table.push((
        "team.allow_script",
        typed(|p: BTreeMap<String, String>| {
            let get = |k: &str| p.get(k).map_or("", String::as_str);
            scripts::allow(get("team"), get("which"), get("line")).map(|()| Value::Null)
        }),
    ));
    table.push((
        "bringover.find",
        typed(|p: BTreeMap<String, Option<String>>| Ok(bringover::find(p.get("folder").and_then(|f| f.as_deref())))),
    ));
    table.push((
        "bringover.workspaces",
        typed(|p: BTreeMap<String, String>| bringover::workspaces(p.get("id").map_or("", String::as_str))),
    ));
    #[derive(Deserialize)]
    struct Suggest {
        project: String,
        size: Value,
        review: Value,
        budget: Value,
        #[serde(default)]
        task: String,
        models: Option<Value>,
    }
    table.push((
        "team.suggest",
        typed(|p: Suggest| {
            Ok(newteam::suggest(&p.project, &p.size, &p.review, &p.budget, &p.task, p.models.as_ref())?)
        }),
    ));
    table.push((
        "team.improve_brief",
        typed(|p: BTreeMap<String, Value>| {
            Ok(newteam::improve_brief(p["project"].as_str().unwrap_or(""), &p["role"])?)
        }),
    ));
    table.push((
        "team.rule_draft",
        typed(|p: BTreeMap<String, String>| {
            Ok(newteam::rule_draft(
                p.get("project").map_or("", String::as_str),
                p.get("words").map_or("", String::as_str),
            )?)
        }),
    ));
    table.extend(teammap::Brain::handlers(brain.clone()));
    table.extend(review::Review::handlers(reviews));
    table.extend(crate::mcptools::Tools::handlers(tools));
    table.extend(tasks::Board::handlers(board));
    table.extend(schedule::Scheduler::handlers(sched.clone()));
    table.extend(connect::handlers());
    table.extend(menubar::Menubar::handlers(menu));
    table.extend(setup::handlers(rt.clone()));
    table.extend(preview::Previews::handlers(previews.clone()));
    table.extend(remote::Remotes::handlers(remotes.clone()));
    let table = remote::Remotes::wrap(remotes.clone(), table);

    // step 18a: assistants reach the same actions, routed the same way. The actions see the table without themselves.
    let shared: Vec<(&'static str, Arc<Handler>)> = table.into_iter().map(|(n, h)| (n, Arc::new(tracked(h)))).collect();
    let fill = |extra: Handlers| {
        let mut methods = crate::methods();
        for (name, h) in &shared {
            let h = h.clone();
            methods.insert(name, Box::new(move |p, c| h(p, c)));
        }
        extra.into_iter().for_each(|(name, h)| methods.insert(name, tracked(h)));
        Arc::new(methods)
    };
    let methods = fill(actions::handlers(fill(vec![])));
    bridge::start(methods.clone(), "127.0.0.1"); // container agents' team tools
    let _ = settings::migrate(None); // a settings problem must not stop the brain

    let mut steps = Loop::default();
    let (c, x, w) = (claude, codex, watching.clone());
    steps.step("watch", Box::new(move || notify_and_deliver(&c, &x, !w())));
    let (b, w) = (brain, watching.clone());
    steps.step("brain", Box::new(move || drop(b.tick(None, (!w()).then_some(&live::notify as &Notify)))));
    steps.step(
        "machine",
        Box::new(|| {
            let _ = machine::tick(status::now_utc(), None);
        }),
    );
    let s = sched;
    steps.step("schedule", Box::new(move || s.tick()));
    steps.step("previews", Box::new(move || previews.sweep()));
    let w = watching;
    steps.step("remotes", Box::new(move || remotes.tick(w())));
    steps.step(
        "tickets",
        Box::new(move || {
            // no core: nothing is running, and nothing is pruned until one answers
            if let Ok(rows) = team::status(&rt, None) {
                bridge::prune(
                    &rows
                        .iter()
                        .filter(|r| r["state"] == "live")
                        .filter_map(|r| r["address"].as_str().map(String::from))
                        .collect(),
                );
            }
        }),
    );
    (methods, steps)
}

/// One round of the serve tick: plain notifications (only when the app isn't showing its own) and held quiet notes.
fn notify_and_deliver(claude: &Path, codex: &Path, announcing: bool) {
    let now = status::now_utc();
    let agents = status::collect(claude, codex, now, 12.0, false, status::running_cwds().as_ref(), None);
    let st = settings::load(None);
    let paused = crate::model::parse_ts(&st["notify_paused_until"]).is_some_and(|p| p > now);
    if announcing && !paused {
        live::announce(
            &status::owner_queue(&agents, now),
            &status::stalls(&agents, now),
            now,
            None,
            Some(&st["notify"]),
        );
    }
    quiet::deliver(
        &agents,
        None,
        Some(&move |notes: &[Value], why: &str| drop(live::record_delivery(notes, why, Some(now)))),
        Some(&move |notes: &[Value]| drop(live::record_given_up(notes, Some(now)))),
    );
}
