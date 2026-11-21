//! Session E2: the bridge's tickets, the sampler's spacing and cap, and a call routed to another machine's brain.
#![cfg(unix)]
mod testkit;
use assieme_brain::runtime::Runtime;
use assieme_brain::server::{By, Call, Methods};
use assieme_brain::{bridge, machine, remote::Remotes};
use assieme_core::put;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::os::unix::fs::PermissionsExt;
use std::sync::{Arc, LazyLock};

/// One state folder for the file: tests that need their own paths take them under it.
static HOME: LazyLock<testkit::Temp> = LazyLock::new(|| {
    let t = testkit::Temp::new();
    t.set_env();
    t
});

#[test]
fn a_ticket_speaks_for_its_terminal_and_for_eight_calls_only() {
    LazyLock::force(&HOME);
    let mut methods = Methods::default();
    for name in ["mcp.teammates", "team.stop"] {
        methods.insert(name, Box::new(|p, _| Ok(p)));
    }
    let port = bridge::start(Arc::new(methods), "127.0.0.1");
    assert_eq!(bridge::port(), Some(port));
    let (ticket, other) = (bridge::issue(), bridge::issue());
    bridge::bind(&ticket, "t7");
    bridge::bind(&other, "t9");
    let tickets = HOME.state().join("assieme/bridge-tickets.json");
    assert_eq!(std::fs::metadata(&tickets).unwrap().permissions().mode() & 0o777, 0o600);
    let mut conn = TcpStream::connect(("127.0.0.1", port)).unwrap();
    let mut ask = |request: Value| {
        writeln!(conn, "{request}").unwrap();
        let mut line = String::new();
        BufReader::new(&conn).read_line(&mut line).unwrap();
        serde_json::from_str::<Value>(&line).unwrap()
    };
    let call =
        |ticket: &str, method: &str| json!({"id": 1, "ticket": ticket, "method": method, "params": {"address": "t1"}});
    assert_eq!(
        ask(call(&ticket, "mcp.teammates"))["result"]["address"],
        "t7",
        "the ticket's terminal, not the sent one"
    );
    for refused in
        [call("no-such", "mcp.teammates"), call(&ticket, "team.stop"), json!({"id": 2, "method": "mcp.tasks"})]
    {
        assert_eq!(ask(refused)["error"]["code"], -32001);
    }
    bridge::revoke(&["t7".into()]);
    assert_eq!(ask(call(&ticket, "mcp.teammates"))["error"]["code"], -32001);
    bridge::prune(&["t1".to_string()].into_iter().collect());
    assert_eq!(ask(call(&other, "mcp.teammates"))["error"]["code"], -32001, "a gone terminal's ticket is forgotten");
}

#[test]
fn the_sampler_writes_once_a_minute_and_stays_small() {
    let file = HOME.0.join("machine.jsonl");
    let at = |s: i64| jiff::Timestamp::from_second(1_800_000_000 + s).unwrap();
    assert!([(0, true), (30, false), (61, true)].iter().all(|&(s, wrote)| machine::tick(at(s), Some(&file)) == wrote));
    assert_eq!(std::fs::read_to_string(&file).unwrap().lines().count(), 2);
    let old = "{\"at\":\"2020-01-01T00:00:00Z\",\"free_pct\":1,\"load\":1,\"cpus\":1}\n";
    std::fs::write(&file, old.repeat(5000)).unwrap();
    assert!(machine::tick(at(0), Some(&file)));
    let text = std::fs::read_to_string(&file).unwrap();
    assert!(
        text.len() < 256 * 1024 && text.lines().all(|l| serde_json::from_str::<Value>(l).is_ok()),
        "{}",
        text.len()
    );
    assert!(text.lines().last().unwrap().contains("2027"));
}

#[test]
fn a_call_goes_where_its_address_says_and_comes_back_as_seen_from_here() {
    LazyLock::force(&HOME);
    let sock = HOME.0.join("core.sock");
    let reply =
        format!("{}\n", json!({"jsonrpc": "2.0", "id": 1, "result": {"address": "t7", "rows": [{"undo": "abc"}]}}));
    let core = testkit::FakeCore::start(
        &sock,
        HashMap::from([
            (
                "machine.list",
                json!({"machines": [{"name": "gpu-box", "target": "gpu", "session": "assieme", "state": "connected"}]}),
            ),
            ("terminal.list", json!({"terminals": []})),
            ("machine.exec", json!({"code": 0, "stdout": put::encode(reply.as_bytes()), "stderr": ""})),
        ]),
    );
    let remotes = Arc::new(Remotes::new(Runtime::new("assieme", Some(sock))));
    let local: assieme_brain::server::Handlers = vec![("team.status", Box::new(|_, _| Ok(json!({"local": true}))))];
    let table = Remotes::wrap(remotes, local);
    let ask = |address: &str| {
        let handler = &table.iter().find(|(n, _)| *n == "team.status").unwrap().1;
        handler(json!({"address": address}), &Call { by: By::You }).unwrap()
    };
    assert_eq!(ask("t1"), json!({"local": true}));
    assert_eq!(ask("gpu-box/t7"), json!({"address": "gpu-box/t7", "rows": [{"undo": "gpu-box/abc"}]}));
    let sent = core.calls("machine.exec").pop().unwrap();
    assert_eq!(sent["on"], "gpu-box");
    assert!(sent["line"].as_str().unwrap().ends_with("call team.status"), "{}", sent["line"]);
    let input = put::decode(sent["input"].as_str().unwrap()).unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&input).unwrap(),
        json!({"address": "t7"}),
        "the machine's own name is dropped"
    );
}

#[test]
fn remote_setup_installs_the_asm_launcher() {
    LazyLock::force(&HOME);
    let sock = HOME.0.join("core-launcher.sock");
    let core = testkit::FakeCore::start(
        &sock,
        HashMap::from([("machine.exec", json!({"code": 0, "stdout": "", "stderr": ""}))]),
    );
    Remotes::new(Runtime::new("assieme", Some(sock))).install_launcher("gpu-box").unwrap();
    let line = core.calls("machine.exec").pop().unwrap()["line"].as_str().unwrap().to_string();
    assert!(line.contains(r#"exec "$HOME/.local/bin/assieme-core" cli "$@""#), "{line}");
    assert!(line.contains(r#"> "$HOME/.local/bin/assieme""#) && line.contains(r#"chmod +x "$HOME/.local/bin/assieme""#), "{line}");
}
