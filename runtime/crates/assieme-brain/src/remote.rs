//! Port of `assieme/remote.py`, owned by session E (see OWNERS.md).
//!
//! Remote machines (step 15): another machine's agents and brain, reached only through the core's machine line.
//!
//! Each machine runs its own brain beside its agents, in a terminal of its own `assieme` core, so a remote team keeps
//! being coordinated while this Mac sleeps. In the Mac's brain this module lists hosts, tests and sets up machines,
//! forwards the app's calls to the brain where the team lives (`machine.exec` running `assieme call` there), and adds
//! remote teams to the calls that list every team. Assieme never runs ssh itself: its core does, with the person's own
//! ssh, SSH config and agent, and nothing here reads a key or a password.
//!
//! Machines there run `asm`: `machine add` copies it, and the setup's `assieme` step writes the launcher for it.
use crate::prelude::*;
use crate::pyrules::truthy;
use crate::server::Handler;
use assieme_core::put;
use regex::Regex;
use serde_json::json;
use std::io::{BufRead, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::time::{Duration, Instant};

const SESSION: &str = "assieme"; // the core Assieme uses there, as here
const SERVICE: &str = "assieme.service";
const BRAIN: &str = "brain";
const BRAIN_NAME: &str = "assieme-brain";
const ASSIEME: &str = "\"$HOME/.local/bin/assieme\""; // the launcher `install_launcher` writes
const CALL_S: f64 = 18.0; // under the app's own 20 s, so its error is ours, not a timeout
const KEPT: [&str; 5] = ["team.map", "team.view", "agent.detail", "board.get", "brief.get"]; // shown from their last answer while away
const LIST_S: f64 = 8.0; // a remote that lists slower than this is shown from its last answer
const BRAIN_WAIT: Duration = Duration::from_secs(30);
const REVIVE: Duration = Duration::from_secs(120); // after a brain there wouldn't start, wait this long before the next try
const POLL: Duration = Duration::from_secs(300); // with the app closed, a remote's news is asked for at least this often
const WOKE: Duration = Duration::from_secs(60); // a longer gap between ticks: the Mac slept
const TAILSCALE_APP: &str = "/Applications/Tailscale.app/Contents/MacOS/Tailscale";
const HOST_HELP: &str = "Type a host from your SSH config, or user@address.";

/// Under the app's own 20 s, except what is slow by nature.
fn call_seconds(method: &str) -> f64 {
    match method {
        "team.start" | "team.add_agent" => 115.0,
        "team.suggest" => 175.0,
        "review.get" | "review.merge" | "review.undo" | "review.discard" => 55.0,
        "review.pull_request" => 255.0,
        "review.ask" | "review.note" => 85.0,
        _ => CALL_S,
    }
}

/// One ssh argument: never an option, never two words.
fn target_ok(t: &str) -> bool {
    static TARGET: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^[A-Za-z0-9_.@:\[\]%-]+$").expect("a fixed pattern"));
    TARGET.is_match(t) && !t.starts_with('-')
}

/// A program run to its end, or None when it can't start or takes longer than `seconds`.
fn run(program: &str, args: &[&str], seconds: u64) -> Option<std::process::Output> {
    let mut cmd = std::process::Command::new(program);
    cmd.args(args);
    crate::setup::run(cmd, None, Duration::from_secs(seconds)).ok().flatten()
}

fn quote(s: &str) -> String {
    shlex::try_quote(s).map_or_else(|_| s.to_string(), |q| q.into_owned())
}

#[derive(Debug, Clone, PartialEq)]
pub struct RemoteError {
    pub message: String,
    pub detail: String,
}

impl From<RemoteError> for BrainError {
    fn from(e: RemoteError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

fn err(message: impl Into<String>) -> RemoteError {
    RemoteError { message: message.into(), detail: String::new() }
}

// --- hosts the person already has ---

/// Hosts named in ~/.ssh/config, with where each goes. Only Host, HostName, User and Port are read.
fn ssh_hosts() -> Vec<Value> {
    parse_ssh(&std::fs::read_to_string(format!("{}/.ssh/config", crate::rules::home())).unwrap_or_default())
}

fn parse_ssh(text: &str) -> Vec<Value> {
    let split = Regex::new(r"\s*=\s*|\s+").expect("a fixed pattern");
    let mut hosts: Vec<(String, Map<String, Value>)> = vec![];
    let mut current: Vec<usize> = vec![];
    for line in text.lines() {
        let mut words = split.splitn(line.trim(), 2);
        let key = words.next().unwrap_or_default().to_lowercase();
        let value = words.next().unwrap_or_default().trim();
        match key.as_str() {
            "host" => {
                current = value
                    .split_whitespace()
                    .filter(|n| !n.contains(['*', '?', '!']))
                    .map(|n| match hosts.iter().position(|h| h.0 == n) {
                        Some(i) => i,
                        None => {
                            hosts.push((n.into(), Map::new()));
                            hosts.len() - 1
                        }
                    })
                    .collect();
            }
            "match" => current.clear(),
            "hostname" | "user" | "port" => {
                for &i in &current {
                    hosts[i].1.entry(key.clone()).or_insert(json!(value)); // as ssh does: the first value wins
                }
            }
            _ => {}
        }
    }
    let get =
        |h: &Map<String, Value>, k: &str| h.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(String::from);
    hosts
        .into_iter()
        .map(|(target, h)| {
            let user = get(&h, "user").map_or(String::new(), |u| format!("{u}@"));
            let port = get(&h, "port").map_or(String::new(), |p| format!(":{p}"));
            let name = get(&h, "hostname").unwrap_or_else(|| target.clone());
            json!({"target": target, "address": format!("{user}{name}{port}"), "from": "ssh"})
        })
        .collect()
}

/// Machines on the person's tailnet, online first, by their MagicDNS name. Nothing when Tailscale isn't here.
fn tailscale_hosts() -> Vec<Value> {
    // The Mac app's binary can start Tailscale when it isn't running: ask it only when it is.
    let app_running = || {
        Path::new(TAILSCALE_APP).exists()
            && run("pgrep", &["-x", "IPNExtension"], 2).is_some_and(|o| o.status.success())
    };
    let Some(exe) = crate::pyrules::which("tailscale")
        .map(|p| p.to_string_lossy().into_owned())
        .or_else(|| app_running().then(|| TAILSCALE_APP.into()))
    else {
        return vec![];
    };
    let Some(out) = run(&exe, &["status", "--json"], 3) else { return vec![] };
    let Ok(data) = crate::pyrules::json_loads(&String::from_utf8_lossy(&out.stdout)) else { return vec![] };
    let mut hosts: Vec<(bool, String, Value)> = data["Peer"]
        .as_object()
        .into_iter()
        .flat_map(|peers| peers.values())
        .filter_map(|p| {
            let dns = p["DNSName"].as_str().unwrap_or_default().trim_end_matches('.');
            let first = dns.split('.').next().unwrap_or_default();
            let name = if first.is_empty() { p["HostName"].as_str().unwrap_or_default() } else { first };
            let target = if dns.is_empty() { name } else { dns };
            let online = p["Online"].as_bool().unwrap_or(false);
            (!name.is_empty() && target_ok(target)).then(|| {
                (
                    !online,
                    name.to_string(),
                    json!({"target": target, "name": name, "address": target, "online": online, "from": "tailscale"}),
                )
            })
        })
        .collect();
    hosts.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
    hosts.into_iter().map(|h| h.2).collect()
}

// --- addresses and ids in replies from there ---

/// A reply from there, as seen from here: `t7` is `gpu-box/t7`, and so is a merge's undo id.
fn prefix(v: &Value, m: &str) -> Value {
    match v {
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, x)| {
                    let plain = x.as_str().filter(|s| !s.is_empty() && !s.contains('/') && !s.starts_with("log:"));
                    match plain {
                        Some(s) if k == "address" || k == "undo" => (k.clone(), json!(format!("{m}/{s}"))),
                        _ => (k.clone(), prefix(x, m)),
                    }
                })
                .collect(),
        ),
        Value::Array(a) => Value::Array(a.iter().map(|x| prefix(x, m)).collect()),
        _ => v.clone(),
    }
}

fn strip(v: &Value, m: &str) -> Value {
    match v {
        Value::String(s) => json!(s.strip_prefix(&format!("{m}/")).unwrap_or(s)),
        Value::Object(o) => Value::Object(o.iter().map(|(k, x)| (k.clone(), strip(x, m))).collect()),
        Value::Array(a) => Value::Array(a.iter().map(|x| strip(x, m)).collect()),
        _ => v.clone(),
    }
}

/// What Test connection says when the machine can't be added. `result` is what the app shows.
fn failure(target: &str, e: &RuntimeError) -> Value {
    let result = match e.kind.as_str() {
        "asm_missing" | "incompatible" | "needs_update" => "not_set_up",
        "needs_login" => "needs_login",
        _ if e.message.contains("IDENTIFICATION HAS CHANGED") => "host_key_changed",
        _ if e.message.contains("Host key verification failed") => "new_host_key",
        _ => {
            return json!({"result": "unreachable", "target": target, "why": e.message.split_once(": ").map_or(&*e.message, |x| x.1)});
        }
    };
    json!({"result": result, "target": target})
}

fn merged(a: &Value, b: Value) -> Value {
    let mut out = a.clone();
    if let (Some(o), Value::Object(b)) = (out.as_object_mut(), b) {
        o.extend(b);
    }
    out
}

#[derive(Default)]
struct Inner {
    jobs: BTreeMap<String, Value>,
    last_tick: Option<Instant>,
    raw_at: Option<(Instant, Value)>,
    ms_at: Option<(Instant, Vec<Value>)>, // read at most once a second
    reviving: BTreeSet<String>,           // brains being started there
    revive_after: BTreeMap<String, Instant>,
    polled: BTreeMap<String, (Instant, String)>, // machine -> the last menu.poll with the app closed
    last: BTreeMap<String, Value>,               // machine, method and params -> its last answer
    starts: BTreeSet<String>,
    rows: BTreeMap<String, Vec<Value>>, // machine -> its team rows
    set_up: BTreeSet<String>,
}

/// Other machines: their brains, their teams, and calls routed to them.
#[derive(Clone)]
pub struct Remotes {
    rt: Runtime,
    store: PathBuf,
    inner: Arc<Mutex<Inner>>,
}

type Rows = Vec<(String, Vec<Value>, bool)>;

impl Remotes {
    pub fn new(rt: Runtime) -> Remotes {
        let store = crate::quiet::state_dir().join("remote.json");
        let saved = crate::quiet::read_json(&store, Value::Null);
        let rows = saved["rows"].as_object().into_iter().flatten();
        let rows = rows.map(|(m, r)| (m.clone(), r.as_array().cloned().unwrap_or_default())).collect();
        let set_up =
            saved["set_up"].as_array().into_iter().flatten().filter_map(|m| m.as_str().map(String::from)).collect();
        Remotes { rt, store, inner: Arc::new(Mutex::new(Inner { rows, set_up, ..Inner::default() })) }
    }

    fn st(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn save(&self, st: &Inner) {
        let _ = crate::quiet::write_json(&self.store, &json!({"rows": st.rows, "set_up": st.set_up}));
    }

    fn spawn(&self, f: impl FnOnce(Remotes) + Send + 'static) {
        let me = self.clone();
        std::thread::spawn(move || f(me));
    }

    // --- what the core knows ---

    /// The core's list with every machine's terminals, read at most once a second.
    fn raw(&self) -> Value {
        if let Some((at, raw)) = &self.st().raw_at
            && at.elapsed() < Duration::from_secs(1)
        {
            return raw.clone();
        }
        let raw = self
            .rt
            .call("terminal.list", json!({"labels": {}, "all": true}))
            .unwrap_or_else(|_| json!({"terminals": [], "machines": []}));
        self.st().raw_at = Some((Instant::now(), raw.clone()));
        raw
    }

    /// Forget the cached lists: the next read asks the core.
    fn fresh(&self) {
        let mut st = self.st();
        (st.raw_at, st.ms_at) = (None, None);
    }

    /// The machines Assieme added, with their line: connecting, connected, needs_login or unreachable. Read at most
    /// once a second: every brain call asks, to route it.
    pub fn machines(&self) -> Vec<Value> {
        if let Some((at, ms)) = &self.st().ms_at
            && at.elapsed() < Duration::from_secs(1)
        {
            return ms.clone();
        }
        let listed = self.rt.call("machine.list", json!({})).ok();
        let out: Vec<Value> = listed
            .iter()
            .flat_map(|l| l["machines"].as_array().into_iter().flatten())
            .filter(|m| m["session"] == SESSION)
            .map(|m| {
                let state = m["state"].as_str().unwrap_or("connecting");
                json!({"name": m["name"], "target": m["target"], "state": state, "since_ms": m["since_ms"], "why": m["why"]})
            })
            .collect();
        self.st().ms_at = Some((Instant::now(), out.clone()));
        out
    }

    /// (machine, its team rows, reachable): live rows from the core, the last seen ones while a line is down.
    pub fn remote_rows(&self) -> Rows {
        let ms = self.machines();
        if ms.is_empty() {
            return vec![];
        }
        let raw = self.raw();
        let terminals: Vec<&Value> = raw["terminals"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|t| t["address"].as_str().is_some_and(|a| a.contains('/')))
            .collect();
        let mut st = self.st(); // the server's threads and the tick both get here
        let mut out = vec![];
        let mut changed = false;
        for m in &ms {
            let name = m["name"].as_str().unwrap_or_default().to_string();
            if m["state"] != "connected" {
                out.push((name.clone(), st.rows.get(&name).cloned().unwrap_or_default(), false));
                continue;
            }
            let mine: Vec<&Value> = terminals
                .iter()
                .copied()
                .filter(|t| t["address"].as_str().is_some_and(|a| a.split('/').next() == Some(&name)))
                .collect();
            let rows: Vec<Value> =
                mine.iter().filter_map(|t| crate::team::row(t)).map(|r| merged(&r, json!({"machine": name}))).collect();
            if st.rows.get(&name) != Some(&rows) {
                st.rows.insert(name.clone(), rows.clone());
                changed = true;
            }
            out.push((name, rows, true));
        }
        if changed {
            self.save(&st);
        }
        out
    }

    /// Remote teams with agents, as `teams.list` lists them, each with its machine.
    fn running(&self) -> Vec<Value> {
        let mut out = vec![];
        for (m, rows, ok) in self.remote_rows() {
            let mut names: Vec<&str> = vec![];
            for n in rows.iter().filter_map(|r| r["team"].as_str()) {
                if !names.contains(&n) {
                    names.push(n);
                }
            }
            for name in names {
                let n = rows.iter().filter(|r| r["team"] == name && r["state"] == "live").count();
                if n > 0 {
                    out.push(json!({"name": name, "kind": "assieme", "agents": n, "machine": m, "reachable": ok}));
                }
            }
        }
        out
    }

    /// Team name -> the machine it runs on.
    pub fn teams(&self) -> BTreeMap<String, String> {
        let mut out = BTreeMap::new();
        for (m, rows, _) in self.remote_rows() {
            out.extend(rows.iter().filter_map(|r| Some((r["team"].as_str()?.to_string(), m.clone()))));
        }
        out
    }

    // --- calls there ---

    /// (exit code, stdout, stderr) of a shell line there.
    pub fn exec(
        &self,
        machine: &str,
        line: &str,
        timeout_s: f64,
        stdin: &[u8],
    ) -> Result<(i32, String, String), RemoteError> {
        let mut p = json!({"on": machine, "line": line, "timeout": timeout_s});
        if !stdin.is_empty() {
            p["input"] = json!(put::encode(stdin));
        }
        let r = self.rt.call("machine.exec", p).map_err(|e| err(format!("Can't reach {machine}: {e}")))?;
        let text = |k: &str| {
            String::from_utf8_lossy(&put::decode(r[k].as_str().unwrap_or_default()).unwrap_or_default()).into_owned()
        };
        if truthy(&r["timed_out"]) {
            return Err(err(format!("{machine} took too long to answer.")));
        }
        Ok((r["code"].as_i64().unwrap_or(-1) as i32, text("stdout"), text("stderr")))
    }

    /// A brain method there, its reply as seen from here.
    pub fn call(
        &self,
        machine: &str,
        method: &str,
        params: Option<&Value>,
        timeout_s: Option<f64>,
    ) -> Result<Value, RemoteError> {
        let timeout = timeout_s.filter(|t| *t != 0.0).unwrap_or_else(|| call_seconds(method));
        let params = strip(params.unwrap_or(&json!({})), machine).to_string();
        let line = format!("exec {ASSIEME} call {}", quote(method));
        let (_, out, stderr) = self.exec(machine, &line, timeout, params.as_bytes())?;
        let Ok(reply) = out.trim().lines().next_back().map_or(Err(String::new()), crate::pyrules::json_loads) else {
            let last = stderr.trim().lines().next_back().unwrap_or_default();
            return Err(err(format!(
                "Assieme on {machine} didn't answer{}",
                if last.is_empty() { ".".into() } else { format!(": {last}") }
            )));
        };
        if let Some(e) = reply.get("error") {
            return Err(err(e["message"].as_str().filter(|m| !m.is_empty()).unwrap_or("failed")));
        }
        Ok(prefix(&reply["result"], machine))
    }

    /// (machine, answer, reachable) from every machine with a team, side by side; one that can't answer gives its last.
    fn each(&self, method: &str, params: Option<&Value>) -> Vec<(String, Value, bool)> {
        let names: Vec<String> = self.remote_rows().into_iter().filter(|r| !r.1.is_empty()).map(|r| r.0).collect();
        let one = |m: &String| match self.call(m, method, params, Some(LIST_S)) {
            Ok(got) => {
                self.st().last.insert(format!("{m}|{method}"), got.clone());
                Some((m.clone(), got, true))
            }
            Err(_) => self.st().last.get(&format!("{m}|{method}")).map(|got| (m.clone(), got.clone(), false)),
        };
        std::thread::scope(|s| {
            let jobs: Vec<_> = names.iter().map(|m| s.spawn(move || one(m))).collect();
            jobs.into_iter().filter_map(|j| j.join().ok().flatten()).filter(|r| !r.1.is_null()).collect()
        })
    }

    // --- routing the app's calls ---

    /// The machine a call is for, or None for this Mac. `machine` says so; a team or an address shows it.
    fn where_(&self, params: &mut Map<String, Value>) -> Option<String> {
        if let Some(m) = params.remove("machine").as_ref().and_then(Value::as_str).filter(|m| !m.is_empty()) {
            return Some(m.into());
        }
        let known: Vec<Value> = self.machines();
        if known.is_empty() {
            return None; // no machine added: everything is here
        }
        if let Some(t) = params.get("team").and_then(Value::as_str).filter(|t| !t.is_empty())
            && let Some(w) = self.teams().get(t)
            && !self.here(t)
        {
            return Some(w.clone());
        }
        let known: BTreeSet<&str> = known.iter().filter_map(|m| m["name"].as_str()).collect();
        let heads = params.values().filter_map(Value::as_str).filter(|v| v.contains('/') && !v.starts_with('/'));
        heads.filter_map(|v| v.split('/').next()).find(|h| known.contains(h)).map(String::from)
    }

    /// A team of that name runs on this Mac: it wins over one elsewhere.
    fn here(&self, name: &str) -> bool {
        crate::team::status(&self.rt, Some(name)).is_ok_and(|rows| rows.iter().any(|r| r["team"] == name))
    }

    /// One name across every machine: a team is found by its name.
    fn team_name(&self, params: &Map<String, Value>) -> String {
        let live = crate::team::status(&self.rt, None).unwrap_or_default();
        let mut taken: BTreeSet<String> = self.teams().into_keys().collect();
        taken.extend(live.iter().filter(|r| r["state"] == "live").filter_map(|r| r["team"].as_str().map(String::from)));
        crate::newteam::team_name(
            params.get("project").and_then(Value::as_str).filter(|p| !p.is_empty()).unwrap_or("team"),
            &taken,
        )
    }

    fn routed(&self, name: &'static str, inner: Handler) -> Handler {
        let me = self.clone();
        Box::new(move |params, call| {
            let mut p = params.as_object().cloned().unwrap_or_default();
            let machine = me.where_(&mut p);
            let unnamed = name == "team.start" && !p.get("name").is_some_and(truthy);
            let Some(m) = machine else {
                if unnamed && !me.teams().is_empty() {
                    p.insert("name".into(), json!(me.team_name(&p)));
                }
                return inner(Value::Object(p), call);
            };
            if name == "team.start" {
                if unnamed {
                    p.insert("name".into(), json!(me.team_name(&p)));
                }
                me.st().starts.insert(m.clone()); // its progress shows from that brain's records
            }
            let params = Value::Object(p);
            let key = format!("{m}|{name}|{params}");
            match me.call(&m, name, Some(&params), None) {
                Ok(got) => {
                    if KEPT.contains(&name) && got.is_object() {
                        me.st().last.insert(key, got.clone());
                    }
                    Ok(got)
                }
                Err(e) => {
                    let Some(last) = me.st().last.get(&key).cloned() else { return Err(e.into()) };
                    let line = me.machines().into_iter().find(|x| x["name"] == m.as_str());
                    // What the team was doing when it was last seen.
                    Ok(merged(
                        &last,
                        json!({"unreachable": line.unwrap_or(json!({"name": m, "state": "unreachable"}))}),
                    ))
                }
            }
        })
    }

    /// Adds every machine's panel to this one's: its teams (most needing the person first), count and spend.
    fn panel(&self, r: &mut Value, got: &[(String, Value, bool)]) {
        for (m, x, ok) in got {
            let x = if x["panel"].is_null() { x } else { &x["panel"] };
            let teams =
                x["teams"].as_array().into_iter().flatten().map(|t| merged(t, json!({"where": m, "reachable": ok})));
            if let Some(all) = r["teams"].as_array_mut() {
                all.extend(teams);
            }
            r["count"] =
                json!(r["count"].as_i64().unwrap_or(0) + if *ok { x["count"].as_i64().unwrap_or(0) } else { 0 });
            if let (Some(a), Some(b)) = (r["spend_tokens"].as_i64(), x["spend_tokens"].as_i64()) {
                r["spend_tokens"] = json!(a + b);
            }
        }
        let need = |t: &Value| t["need"].as_f64().unwrap_or(0.0);
        if let Some(all) = r["teams"].as_array_mut() {
            all.sort_by(|a, b| need(b).total_cmp(&need(a)));
        }
    }

    /// A list across teams: each remote's rows added, marked with their machine. An id gets the machine in front, so
    /// the button that sends it back (Undo) goes there.
    fn listed(&self, method: &str, key: Option<&str>, p: &Value, r: &mut Value, sort: Option<fn(&Value) -> String>) {
        if truthy(&p["team"]) {
            return;
        }
        let mut more = vec![];
        for (m, x, _) in self.each(method, Some(p)) {
            let rows = key.map_or(&x, |k| &x[k]);
            for y in rows.as_array().into_iter().flatten() {
                let mut y = merged(y, json!({"machine": m}));
                if let Some(id) = y["id"].as_str().filter(|i| !i.is_empty()) {
                    y["id"] = json!(format!("{m}/{id}"));
                }
                more.push(y);
            }
        }
        let rows = match key {
            Some(k) => r[k].as_array_mut(),
            None => r.as_array_mut(),
        };
        if let Some(rows) = rows {
            rows.extend(more);
            match (sort, method) {
                (Some(f), "activity.list") => rows.sort_by_key(|y| std::cmp::Reverse(f(y))),
                (Some(f), _) => rows.sort_by_key(f),
                _ => {}
            }
        }
    }

    /// What a remote adds to the answers that list every team.
    fn merge(&self, name: &str, mut r: Value, p: &Value) -> Value {
        let text = |y: &Value, k: &str| y[k].as_str().unwrap_or_default().to_string();
        match name {
            "brain.snapshot" => {
                let mut teams = r["teams"].as_array().cloned().unwrap_or_default();
                for (_, rows, ok) in self.remote_rows() {
                    teams.extend(rows.iter().map(|x| merged(x, json!({"reachable": ok}))));
                }
                let mut starting = r["starting"].as_array().cloned().unwrap_or_default();
                starting.extend(self.starting());
                (r["teams"], r["machines"], r["starting"]) = (json!(teams), json!(self.machines()), json!(starting));
            }
            "teams.list" => {
                let mut teams = r["teams"].as_array().cloned().unwrap_or_default();
                teams.extend(self.running());
                r["teams"] = json!(teams);
            }
            "mcp.list_teams" => {
                let mut running = r["running"].as_array().cloned().unwrap_or_default();
                running.extend(self.running());
                r["running"] = json!(running);
            }
            "menu.state" => self.panel(&mut r, &self.each("menu.state", None)),
            "menu.poll" => {
                let got = self.each("menu.poll", None);
                self.panel(&mut r["panel"], &got);
                r["count"] = r["panel"]["count"].clone();
                for (m, x, _) in got.iter().filter(|g| g.2) {
                    let events = x["events"].as_array().into_iter().flatten();
                    let events =
                        events.map(|e| merged(e, json!({"key": format!("{m}|{}", text(e, "key")), "machine": m})));
                    if let Some(all) = r["events"].as_array_mut() {
                        all.extend(events);
                    }
                }
            }
            "review.waiting" => self.listed(
                "review.waiting",
                None,
                p,
                &mut r,
                Some(|w| {
                    format!("{}\0{}", w["team"].as_str().unwrap_or_default(), w["role"].as_str().unwrap_or_default())
                }),
            ),
            "activity.list" => self.listed(
                "activity.list",
                Some("rows"),
                p,
                &mut r,
                Some(|y| {
                    format!("{}\0{}", y["day"].as_str().unwrap_or_default(), y["at"].as_str().unwrap_or_default())
                }),
            ),
            "mcp.needs_you" => self.listed("mcp.needs_you", Some("needs_you"), p, &mut r, None),
            _ => {}
        }
        r
    }

    /// The brain's handlers, each going where its team lives, plus the machine handlers. Lists add remote rows.
    pub fn wrap(this: Arc<Remotes>, handlers: Handlers) -> Handlers {
        let mut out: Handlers = vec![];
        for (name, h) in handlers {
            let (routed, me) = (this.routed(name, h), (*this).clone());
            let merges = [
                "brain.snapshot",
                "teams.list",
                "menu.state",
                "menu.poll",
                "mcp.list_teams",
                "review.waiting",
                "activity.list",
                "mcp.needs_you",
            ];
            out.push((
                name,
                if merges.contains(&name) {
                    Box::new(move |p, call| routed(p.clone(), call).map(|r| me.merge(name, r, &p)))
                } else {
                    routed
                },
            ));
        }
        out.extend(Remotes::handlers(this));
        out
    }

    // --- starting a team there: its progress comes from that brain ---

    /// Teams starting on a machine, from that brain, until they have all finished starting.
    fn starting(&self) -> Vec<Value> {
        let pending: Vec<String> = self.st().starts.iter().cloned().collect();
        let mut out = vec![];
        for m in pending {
            let Ok(got) = self.call(&m, "brain.snapshot", None, Some(LIST_S)) else { continue };
            let rows = got["starting"].as_array().cloned().unwrap_or_default();
            if rows.iter().all(|r| truthy(&r["done"])) {
                self.st().starts.remove(&m);
            }
            out.extend(rows.iter().map(|r| merged(r, json!({"machine": m}))));
        }
        out
    }

    // --- the machine handlers (here only) ---

    /// A machine's line, and when it answers: whether Assieme there is current, and its Claude Code and Codex.
    pub fn check(&self, machine: &str) -> Result<Value, RemoteError> {
        let m = self
            .machines()
            .into_iter()
            .find(|x| x["name"] == machine)
            .ok_or_else(|| err(format!("No machine named {machine}.")))?;
        let out = merged(&m, json!({"job": self.job(m["target"].as_str().unwrap_or_default())}));
        if m["state"] != "connected" {
            return Ok(merged(&out, json!({"ready": false})));
        }
        let asked = self
            .call(machine, "remote.version", None, Some(15.0))
            .and_then(|v| Ok((v, self.call(machine, "setup.status", None, Some(CALL_S))?)));
        Ok(match asked {
            Err(e) => merged(&out, json!({"ready": false, "problem": e.message})),
            Ok((version, st)) => merged(
                &out,
                json!({"ready": true, "outdated": version != env!("CARGO_PKG_VERSION"), "git": st["git"], "agents": st["agents"]}),
            ),
        })
    }

    /// Test connection: add the machine (the core dials it) and say what is there, in one of the `result`s.
    pub fn test(&self, target: &str) -> Result<Value, RemoteError> {
        let target = target.trim();
        if !target_ok(target) {
            return Err(err(HOST_HELP));
        }
        let name = match self.machines().into_iter().find(|m| m["target"] == target) {
            Some(known) => {
                let name = known["name"].as_str().unwrap_or_default().to_string();
                if known["state"] != "connected"
                    && let Err(e) = self.rt.call("machine.connect", json!({"machine": name}))
                {
                    return Ok(merged(&failure(target, &e), json!({"name": name})));
                }
                name
            }
            None => match self.rt.call("machine.add", json!({"target": target, "session": SESSION})) {
                Ok(added) => {
                    self.fresh();
                    added["name"].as_str().unwrap_or_default().to_string()
                }
                Err(e) => return Ok(failure(target, &e)),
            },
        };
        let c = self.check(&name)?;
        if c["ready"] == true {
            let mut st = self.st(); // found ready (set up from another Mac): its brain is looked after too
            if st.set_up.insert(name) {
                self.save(&st);
            }
        }
        let result = if c["ready"] == true {
            "ready"
        } else if c["state"] == "connected" {
            "not_set_up"
        } else {
            c["state"].as_str().unwrap_or("unreachable")
        };
        Ok(merged(&c, json!({"result": result})))
    }

    fn job(&self, target: &str) -> Value {
        self.st().jobs.get(target).cloned().unwrap_or(Value::Null)
    }

    fn job_edit(&self, target: &str, f: impl FnOnce(&mut Value)) {
        self.st().jobs.get_mut(target).map(f);
    }

    /// Set up Assieme there in the background: the runtime, Assieme itself, then its brain. Progress in `job`.
    pub fn setup(&self, target: &str) -> Result<Value, RemoteError> {
        let target = target.trim().to_string();
        if !target_ok(&target) {
            return Err(err(HOST_HELP));
        }
        let steps: Vec<Value> =
            ["runtime", "assieme", "brain"].iter().map(|id| json!({"id": id, "state": "waiting"})).collect();
        {
            let mut st = self.st();
            if st.jobs.get(&target).is_some_and(|j| j["done"] == false) {
                return Ok(json!({"job": st.jobs[&target]}));
            }
            let job = json!({"target": target, "machine": null, "done": false, "error": null, "steps": steps});
            st.jobs.insert(target.clone(), job);
        }
        let t = target.clone();
        self.spawn(move |me| me.run_setup(&t));
        Ok(json!({"job": self.job(&target)}))
    }

    fn run_setup(&self, target: &str) {
        let result = (|| -> Result<(), RemoteError> {
            for i in 0..3 {
                self.job_edit(target, |j| j["steps"][i]["state"] = json!("doing"));
                let machine = self.job(target)["machine"].as_str().unwrap_or_default().to_string();
                match i {
                    0 => self.add_installing(target)?,
                    1 => self.install_launcher(&machine)?,
                    _ => self.start_brain(&machine, true)?,
                }
                self.job_edit(target, |j| j["steps"][i]["state"] = json!("done"));
            }
            let machine = self.job(target)["machine"].as_str().unwrap_or_default().to_string();
            let mut st = self.st();
            st.set_up.insert(machine);
            self.save(&st);
            Ok(())
        })();
        self.job_edit(target, |j| {
            if let Err(e) = result {
                // shown on the sheet, with Try again
                for s in j["steps"].as_array_mut().into_iter().flatten().filter(|s| s["state"] == "doing") {
                    s["state"] = json!("failed");
                }
                j["error"] = json!(e.message);
            }
            j["done"] = json!(true);
        });
    }

    /// `assieme` there: a launcher for the `asm` that `machine add` copied, so `call` and `serve` reach `asm cli`.
    pub fn install_launcher(&self, machine: &str) -> Result<(), RemoteError> {
        let line = concat!(
            r#"printf '%s\n' '#!/bin/sh' 'exec "$HOME/.local/bin/assieme-core" cli "$@"' > "$HOME/.local/bin/assieme" "#,
            r#"&& chmod +x "$HOME/.local/bin/assieme""#
        );
        if self.exec(machine, line, CALL_S, b"")?.0 != 0 {
            return Err(err(format!("Assieme isn't installed on {machine}.")));
        }
        Ok(())
    }

    fn asm_cli(&self, args: &[&str]) -> Result<Value, RemoteError> {
        let mut cmd = std::process::Command::new(crate::pyrules::which("asm").unwrap_or_else(|| "asm".into()));
        cmd.args(args).arg("--json").env_clear().envs(crate::runtime::clean_env(&std::env::vars().collect()));
        cmd.env("ASM_SOCKET", &self.rt.sock).env("ASM_SESSION", &self.rt.session);
        let Some(out) = crate::setup::run(cmd, None, Duration::from_secs(600)).ok().flatten() else {
            return Err(err("the core failed"));
        };
        let (stdout, stderr) = (String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
        let said = if stdout.trim().is_empty() { stderr.trim() } else { stdout.trim() };
        let Ok(reply) = said.lines().next_back().map_or(Err(String::new()), crate::pyrules::json_loads) else {
            let said = if stderr.trim().is_empty() { stdout.trim() } else { stderr.trim() };
            return Err(err(if said.is_empty() { "the core failed" } else { said }));
        };
        if !out.status.success() || reply.get("error").is_some() {
            return Err(err(reply["error"]["message"].as_str().filter(|m| !m.is_empty()).unwrap_or("the core failed")));
        }
        Ok(reply)
    }

    /// The machine, added with asm on it: the core copies itself there, or the Linux build the app carries.
    fn add_installing(&self, target: &str) -> Result<(), RemoteError> {
        let mut known = self.machines().into_iter().find(|m| m["target"] == target);
        match &known {
            Some(m) if m["state"] == "needs_update" => {
                self.asm_cli(&["machine", "update", m["name"].as_str().unwrap_or_default()])?;
            }
            None => known = Some(self.asm_cli(&["machine", "add", target, "--session", SESSION, "--install"])?),
            _ => {}
        }
        self.fresh();
        let name = known.map(|m| m["name"].clone());
        self.job_edit(target, |j| j["machine"] = name.unwrap_or(Value::Null));
        Ok(())
    }

    fn brains(&self, machine: &str) -> Vec<Value> {
        self.fresh();
        let raw = self.raw();
        let at = format!("{machine}/");
        let brain = |t: &&Value| {
            t["address"].as_str().is_some_and(|a| a.starts_with(&at))
                && t["labels"][SERVICE] == BRAIN
                && t["state"] == "live"
        };
        raw["terminals"].as_array().into_iter().flatten().filter(brain).cloned().collect()
    }

    /// Assieme's brain there, in a terminal of that machine's core, answering before this returns.
    pub fn start_brain(&self, machine: &str, restart: bool) -> Result<(), RemoteError> {
        let running = self.brains(machine);
        if !running.is_empty() && !restart {
            return Ok(());
        }
        for t in &running {
            self.rt.kill(t["address"].as_str().unwrap_or_default()).map_err(|e| err(e.message))?;
        }
        // It finds its core through the variable the core gives every program in its terminals.
        let line = format!("ASSIEME_ASM_SOCKET=\"$ASM_TERMINAL_SOCKET\" exec {ASSIEME} serve");
        let run = json!({"on": machine, "name": BRAIN_NAME, "labels": {SERVICE: BRAIN}, "wait": false, "no_parent": true, "line": line});
        self.rt.call("terminal.run", run).map_err(|e| err(e.message))?;
        let deadline = Instant::now() + BRAIN_WAIT;
        loop {
            match self.call(machine, "settings.get", None, Some(5.0)) {
                Ok(_) => return Ok(()),
                Err(e) if Instant::now() >= deadline => {
                    return Err(err(format!("Assieme started on {machine} but doesn't answer: {}", e.message)));
                }
                Err(_) => std::thread::sleep(Duration::from_secs(1)),
            }
        }
    }

    pub fn connect(&self, machine: &str) -> Result<Value, RemoteError> {
        let got = self.rt.call("machine.connect", json!({"machine": machine})).map_err(|e| err(e.message));
        self.fresh(); // its line just changed
        got
    }

    /// This Mac stops showing it. Its agents keep running there.
    pub fn forget(&self, machine: &str) -> Value {
        let _ = self.rt.call("machine.rm", json!({"machine": machine}));
        self.fresh();
        let mut st = self.st();
        st.rows.remove(machine);
        st.set_up.remove(machine);
        self.save(&st);
        json!({"ok": true})
    }

    /// Terminal, on `ssh <target>` (a password, a new host key) or on the core's login for a machine already added.
    /// The person answers ssh there; Assieme sees none of it.
    pub fn open_terminal(&self, target: Option<&str>, machine: Option<&str>) -> Result<Value, RemoteError> {
        let line = if let Some(machine) = machine.filter(|m| !m.is_empty()) {
            if !self.machines().iter().any(|x| x["name"] == machine) {
                return Err(err(format!("No machine named {machine}.")));
            }
            let asm = crate::pyrules::which("asm").map_or("asm".into(), |p| p.to_string_lossy().into_owned());
            let (sock, session) = (self.rt.sock.to_string_lossy().into_owned(), &self.rt.session);
            format!(
                "ASM_SOCKET={} ASM_SESSION={} exec {} machine login {}",
                quote(&sock),
                quote(session),
                quote(&asm),
                quote(machine)
            )
        } else {
            let target = target.unwrap_or_default().trim();
            if !target_ok(target) {
                return Err(err(HOST_HELP));
            }
            format!(
                "ssh -- {} exit && echo \"Logged in. You can close this window and go back to Assieme.\"",
                quote(target)
            )
        };
        let path = crate::quiet::state_dir().join("ssh-login.command");
        let fail = |e: std::io::Error| err(e.to_string());
        std::fs::create_dir_all(path.parent().unwrap_or(Path::new("."))).map_err(fail)?;
        std::fs::write(&path, format!("#!/bin/sh\n{line}\n")).map_err(fail)?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).map_err(fail)?;
        let _ = std::process::Command::new("open").args(["-a", "Terminal"]).arg(&path).output();
        Ok(json!({"ok": true}))
    }

    pub fn machines_view(&self) -> Vec<Value> {
        let set_up = self.st().set_up.clone();
        let view = |m: Value| {
            let job = self.job(m["target"].as_str().unwrap_or_default());
            let set = set_up.contains(m["name"].as_str().unwrap_or_default());
            merged(&m, json!({"job": job, "set_up": set}))
        };
        self.machines().into_iter().map(view).collect()
    }

    pub fn handlers(this: Arc<Remotes>) -> Handlers {
        let on = |f: fn(&Remotes, &Value) -> Result<Value, RemoteError>| -> Handler {
            let me = this.clone();
            Box::new(move |p, _| Ok(f(&me, &p)?))
        };
        vec![
            ("remote.hosts", {
                let me = this.clone();
                Box::new(move |_, _| {
                    Ok(json!({"ssh": ssh_hosts(), "tailscale": tailscale_hosts(), "machines": me.machines_view()}))
                })
            }),
            ("remote.machines", {
                let me = this.clone();
                Box::new(move |_, _| Ok(json!(me.machines_view())))
            }),
            ("remote.check", on(|me, p| me.check(p["machine"].as_str().unwrap_or_default()))),
            ("remote.test", on(|me, p| me.test(p["target"].as_str().unwrap_or_default()))),
            ("remote.setup", on(|me, p| me.setup(p["target"].as_str().unwrap_or_default()))),
            ("remote.job", on(|me, p| Ok(json!({"job": me.job(p["target"].as_str().unwrap_or_default())})))),
            ("remote.connect", on(|me, p| me.connect(p["machine"].as_str().unwrap_or_default()))),
            ("remote.forget", on(|me, p| Ok(me.forget(p["machine"].as_str().unwrap_or_default())))),
            ("remote.terminal", on(|me, p| me.open_terminal(p["target"].as_str(), p["machine"].as_str()))),
        ]
    }

    // --- the brain's tick ---

    /// After the Mac slept, dial every machine at once instead of at the core's next retry. A machine Assieme set up
    /// whose brain is gone (the machine restarted) gets it back. With the app closed, remote news notifies here.
    pub fn tick(&self, watching: bool) {
        let now = Instant::now();
        let slept = self.st().last_tick.is_some_and(|t| now - t > WOKE);
        self.st().last_tick = Some(now);
        self.forget_unknown();
        for m in self.machines() {
            let name = m["name"].as_str().unwrap_or_default().to_string();
            if m["state"] != "connected" {
                if slept {
                    self.spawn(move |me| drop(me.connect(&name))); // the next tick tries again
                }
                continue;
            }
            let busy = self.job(m["target"].as_str().unwrap_or_default())["done"] == false;
            let due = {
                let st = self.st();
                !st.reviving.contains(&name)
                    && st.revive_after.get(&name).is_none_or(|t| now >= *t)
                    && st.set_up.contains(&name)
            };
            if due && !busy && self.brains(&name).is_empty() {
                self.st().reviving.insert(name.clone());
                self.spawn(move |me| me.revive(&name));
            }
        }
        if !watching {
            self.notify_news(now);
        }
    }

    /// Machines set up on a core that no longer knows them (added before the switch to asm) are dropped from set_up:
    /// the person adds them again. Only on a list the core gave: an unreachable core proves nothing.
    fn forget_unknown(&self) {
        let Ok(listed) = self.rt.call("machine.list", json!({})) else { return };
        let known: BTreeSet<&str> = listed["machines"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|m| m["session"] == SESSION)
            .filter_map(|m| m["name"].as_str())
            .collect();
        let mut st = self.st();
        let before = st.set_up.len();
        st.set_up.retain(|m| known.contains(m.as_str()));
        if st.set_up.len() != before {
            self.save(&st);
        }
    }

    /// Start the brain there; after a failure the next try waits. Never on the tick's own thread.
    fn revive(&self, name: &str) {
        let failed = self.start_brain(name, false).is_err();
        let mut st = self.st();
        if failed {
            st.revive_after.insert(name.into(), Instant::now() + REVIVE);
        }
        st.reviving.remove(name);
    }

    /// With the app closed, ask a machine for its news only when its teams changed, or every POLL: each ask starts a
    /// process there.
    fn notify_news(&self, now: Instant) {
        for (m, rows, ok) in self.remote_rows() {
            let sig =
                json!(rows.iter().map(|r| [&r["address"], &r["state"], &r["status"]]).collect::<Vec<_>>()).to_string();
            let seen = self.st().polled.get(&m).cloned();
            if !ok || rows.is_empty() || seen.is_some_and(|(at, s)| s == sig && now - at < POLL) {
                continue;
            }
            self.st().polled.insert(m.clone(), (now, sig));
            let Ok(got) = self.call(&m, "menu.poll", None, Some(LIST_S)) else { continue };
            for e in got["events"].as_array().into_iter().flatten() {
                crate::live::notify(e["title"].as_str().unwrap_or_default(), e["body"].as_str().unwrap_or_default());
            }
        }
    }
}

// --- there: what the Mac's brain asks for ---

/// Git repositories in the home folder here, for New team's project field when this is the remote.
fn repos() -> Vec<String> {
    const DEPTH: usize = 3;
    fn walk(dir: &Path, level: usize, out: &mut Vec<PathBuf>) {
        if dir.join(".git").is_dir() {
            return out.push(dir.into());
        }
        if level + 1 >= DEPTH {
            return;
        }
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for e in entries.flatten().filter(|e| e.file_type().is_ok_and(|t| t.is_dir())) {
            let name = e.file_name().to_string_lossy().into_owned();
            if !name.starts_with('.') && name != "node_modules" && name != "Library" {
                walk(&e.path(), level + 1, out);
            }
        }
    }
    let home = crate::rules::home();
    let mut found = vec![];
    walk(Path::new(&home), 0, &mut found);
    let mut out: Vec<String> = found
        .iter()
        .map(|r| r.to_string_lossy().into_owned())
        .map(|r| r.strip_prefix(&home).map_or(r.clone(), |rest| format!("~{rest}")))
        .collect();
    out.sort();
    out
}

/// `remote.version` and `remote.repos`.
pub fn remote_handlers() -> Handlers {
    vec![
        ("remote.version", Box::new(|_, _| Ok(json!(env!("CARGO_PKG_VERSION"))))),
        ("remote.repos", Box::new(|_, _| Ok(json!(repos())))),
    ]
}

/// `assieme call <method>`: one request to this machine's brain, the parameters as JSON on stdin, the reply on
/// stdout. The Mac's brain reaches a remote brain this way, over the core's line. Returns the exit code.
pub fn call_main(method: &str, sock: Option<&Path>) -> i32 {
    let ask = || -> Result<String, &'static str> {
        let mut raw = String::new();
        std::io::stdin().read_to_string(&mut raw).map_err(|_| "Assieme isn't running on this machine.")?;
        let params = if raw.trim().is_empty() { Ok(json!({})) } else { crate::pyrules::json_loads(&raw) };
        let params = params.map_err(|_| "The parameters were not JSON.")?;
        let down = |_| "Assieme isn't running on this machine.";
        let mut stream = std::os::unix::net::UnixStream::connect(
            sock.map_or_else(|| crate::quiet::state_dir().join("brain.sock"), Path::to_path_buf),
        )
        .map_err(down)?;
        writeln!(stream, "{}", json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})).map_err(down)?;
        let mut line = String::new();
        std::io::BufReader::new(stream).read_line(&mut line).map_err(down)?;
        Ok(line)
    };
    let line = ask().unwrap_or_else(|message| json!({"error": {"message": message}}).to_string());
    println!("{}", line.trim_end_matches('\n'));
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `parse_ssh` against Python's `ssh_hosts` (`record/ssh_hosts_cases.py`).
    #[test]
    fn ssh_hosts_as_python_reads_them() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/ssh_hosts.json");
        let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        for c in cases {
            assert_eq!(json!(parse_ssh(c["config"].as_str().unwrap())), c["hosts"], "{}", c["config"]);
        }
    }
}
