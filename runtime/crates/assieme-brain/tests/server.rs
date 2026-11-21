#![cfg(unix)]
use assieme_brain::server::{BrainError, By, Loop, Methods, serve};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

/// Serves `methods` on a fresh socket in its own thread; returns the socket path.
fn start(methods: Methods) -> std::path::PathBuf {
    static N: AtomicU32 = AtomicU32::new(0);
    let dir = std::path::PathBuf::from("/var/tmp").join(format!(
        "brain-srv-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("b.sock");
    let listener = std::os::unix::net::UnixListener::bind(&path).unwrap();
    listener.set_nonblocking(true).unwrap();
    std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(async move {
            let listener = tokio::net::UnixListener::from_std(listener).unwrap();
            serve(listener, Arc::new(methods)).await;
        });
    });
    path
}

struct Client(BufReader<UnixStream>);

impl Client {
    fn new(path: &std::path::Path) -> Client {
        Client(BufReader::new(UnixStream::connect(path).unwrap()))
    }
    fn raw(&mut self, line: &str) -> Value {
        self.0.get_mut().write_all(format!("{line}\n").as_bytes()).unwrap();
        let mut reply = String::new();
        self.0.read_line(&mut reply).unwrap();
        serde_json::from_str(&reply).unwrap()
    }
    fn call(&mut self, request: Value) -> Value {
        self.raw(&request.to_string())
    }
}

fn echo() -> Methods {
    let mut m = Methods::default();
    m.insert("echo", Box::new(|params, _| Ok(params)));
    m.insert("who", Box::new(|_, call| Ok(json!(call.by == By::Assistant))));
    m.insert("fail", Box::new(|_, _| Err(BrainError { message: "It broke.".into(), detail: Some("line 1".into()) })));
    m.insert("plain", Box::new(|_, _| Err(BrainError::new("Just this."))));
    m.insert("panic", Box::new(|_, _| panic!("boom")));
    m.insert(
        "slow",
        Box::new(|_, _| {
            std::thread::sleep(Duration::from_secs(2));
            Ok(json!(1))
        }),
    );
    m
}

#[test]
fn replies_keep_the_request_id() {
    let mut c = Client::new(&start(echo()));
    let r = c.call(json!({"jsonrpc": "2.0", "id": 7, "method": "echo", "params": {"a": 1}}));
    assert_eq!(r, json!({"jsonrpc": "2.0", "id": 7, "result": {"a": 1}}));
    let r = c.call(json!({"id": "x", "method": "echo"}));
    assert_eq!(r, json!({"jsonrpc": "2.0", "id": "x", "result": {}}), "missing params are an empty object");
}

#[test]
fn by_assistant_reaches_the_handler() {
    let mut c = Client::new(&start(echo()));
    assert_eq!(c.call(json!({"id": 1, "method": "who", "by": "assistant"}))["result"], json!(true));
    assert_eq!(c.call(json!({"id": 2, "method": "who", "by": "other"}))["result"], json!(false));
    assert_eq!(c.call(json!({"id": 3, "method": "who"}))["result"], json!(false));
}

#[test]
fn detail_is_appended_as_python_does() {
    let mut c = Client::new(&start(echo()));
    let r = c.call(json!({"id": 1, "method": "fail"}));
    assert_eq!(r["error"], json!({"code": -32000, "message": "It broke.\n\nDetails:\nline 1"}));
    let r = c.call(json!({"id": 2, "method": "plain"}));
    assert_eq!(r["error"], json!({"code": -32000, "message": "Just this."}));
}

#[test]
fn unknown_method_is_32601() {
    let mut c = Client::new(&start(echo()));
    let r = c.call(json!({"id": 1, "method": "nope"}));
    assert_eq!(r, json!({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no method 'nope'"}}));
    let r = c.call(json!({"id": 2, "method": "it's"}));
    assert_eq!(r["error"]["message"], "no method \"it's\"");
    let r = c.call(json!({"id": 3}));
    assert_eq!(r["error"]["message"], "no method None");
}

#[test]
fn not_an_object_is_not_json() {
    let mut c = Client::new(&start(echo()));
    let not_json = json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": "not JSON"}});
    assert_eq!(c.raw("{oops"), not_json);
    assert_eq!(c.raw("[1, 2]"), not_json);
    assert_eq!(c.raw("\u{fffd}\u{0}"), not_json);
    // The connection still serves.
    assert_eq!(c.call(json!({"id": 1, "method": "echo"}))["result"], json!({}));
}

#[test]
fn a_panic_is_an_error_reply_and_the_connection_lives() {
    let mut c = Client::new(&start(echo()));
    let r = c.call(json!({"id": 1, "method": "panic"}));
    assert_eq!(r["error"]["code"], -32000);
    assert!(r["error"]["message"].as_str().unwrap().contains("boom"), "{r}");
    assert_eq!(c.call(json!({"id": 2, "method": "echo"}))["result"], json!({}));
}

#[test]
fn a_slow_handler_does_not_block_another_connection() {
    let path = start(echo());
    let p = path.clone();
    let slow = std::thread::spawn(move || Client::new(&p).call(json!({"id": 1, "method": "slow"})));
    std::thread::sleep(Duration::from_millis(100));
    let at = Instant::now();
    Client::new(&path).call(json!({"id": 2, "method": "echo"}));
    assert!(at.elapsed() < Duration::from_millis(200), "{:?}", at.elapsed());
    assert_eq!(slow.join().unwrap()["result"], json!(1));
}

#[test]
fn a_failing_step_does_not_stop_the_loop() {
    let ran = Arc::new(AtomicU32::new(0));
    let mut every = Loop::default();
    every.step("broken", Box::new(|| panic!("step failed")));
    let r = ran.clone();
    every.step("counts", Box::new(move || drop(r.fetch_add(1, Ordering::SeqCst))));
    every.start(Duration::from_millis(20));
    let at = Instant::now();
    while ran.load(Ordering::SeqCst) < 3 {
        assert!(at.elapsed() < Duration::from_secs(5), "the loop stopped");
        std::thread::sleep(Duration::from_millis(10));
    }
}
