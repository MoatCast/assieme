//! Port of `assieme/bridge.py`, owned by session E (see OWNERS.md).
//!
//! The team tools' way in for agents in containers. A container on a Mac can't open the brain's Unix socket, so the
//! brain also listens on 127.0.0.1 for the eight calls an agent's tools make. Each container agent gets its own ticket,
//! which stands for its terminal: the call is made as that agent, whatever address it sends.
use crate::prelude::*;
use serde_json::json;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::OpenOptionsExt;

const METHODS: [&str; 8] = [
    "mcp.teammates",
    "mcp.message",
    "mcp.report",
    "mcp.brief",
    "mcp.record_decision",
    "mcp.tasks",
    "mcp.task_add",
    "mcp.task_set",
];
static LOCK: Mutex<()> = Mutex::new(());

fn tickets_path() -> PathBuf {
    crate::quiet::state_dir().join("bridge-tickets.json")
}

fn load() -> BTreeMap<String, String> {
    let read = || serde_json::from_slice(&std::fs::read(tickets_path()).ok()?).ok();
    read().unwrap_or_default()
}

fn save(tickets: &BTreeMap<String, String>) -> std::io::Result<()> {
    let path = tickets_path();
    std::fs::create_dir_all(path.parent().unwrap_or(Path::new(".")))?;
    let tmp = path.with_extension("json.tmp");
    let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp)?;
    f.write_all(&serde_json::to_vec(tickets)?)?;
    std::fs::rename(tmp, path)
}

/// From now on `ticket` speaks for the agent in terminal `address`.
pub fn bind(ticket: &str, address: &str) {
    let _lock = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut tickets = load();
    tickets.insert(ticket.into(), address.into());
    let _ = save(&tickets);
}

/// A new ticket.
pub fn issue() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// The port the bridge listens on, or None.
pub fn port() -> Option<u16> {
    std::fs::read_to_string(crate::quiet::state_dir().join("bridge.port")).ok()?.trim().parse().ok()
}

fn keep_if(keep: impl Fn(&str) -> bool) {
    let _lock = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let tickets = load();
    let left: BTreeMap<_, _> = tickets.iter().filter(|(_, a)| keep(a)).map(|(t, a)| (t.clone(), a.clone())).collect();
    if left != tickets {
        let _ = save(&left);
    }
}

/// Forget the tickets of terminals that are gone.
pub fn prune(live: &BTreeSet<String>) {
    keep_if(|a| live.contains(a));
}

pub fn revoke(addresses: &[String]) {
    keep_if(|a| !addresses.iter().any(|x| x == a));
}

fn handle(line: &[u8], methods: &Methods) -> Value {
    let Ok(Value::Object(request)) = crate::pyrules::json_loads_bytes(line) else {
        return json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": "not JSON"}});
    };
    let address = request.get("ticket").and_then(Value::as_str).and_then(|t| load().remove(t));
    let method = request.get("method").and_then(Value::as_str).filter(|m| METHODS.contains(m));
    let (Some(address), Some(method)) = (address, method) else {
        let id = request.get("id").cloned().unwrap_or(Value::Null);
        return json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32001, "message": "Not allowed."}});
    };
    let mut params = match request.get("params") {
        Some(Value::Object(p)) => p.clone(),
        _ => Map::new(),
    };
    params.insert("address".into(), json!(address));
    methods.reply(json!({"id": request.get("id"), "method": method, "params": params}).to_string().as_bytes())
}

/// Listen on a free port in the background and write it down for `launch`. Returns the port.
pub fn start(handlers: Arc<Methods>, host: &str) -> u16 {
    let listener = std::net::TcpListener::bind((host, 0)).expect("a free port on the loopback address");
    let port = listener.local_addr().map_or(0, |a| a.port());
    let _ = std::fs::create_dir_all(crate::quiet::state_dir());
    let _ = std::fs::write(crate::quiet::state_dir().join("bridge.port"), port.to_string());
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let methods = handlers.clone();
            std::thread::spawn(move || {
                let mut out = &stream;
                for line in BufReader::new(&stream).split(b'\n').map_while(Result::ok) {
                    if writeln!(out, "{}", handle(&line, &methods)).is_err() {
                        return; // the client hung up
                    }
                }
            });
        }
    });
    port
}
