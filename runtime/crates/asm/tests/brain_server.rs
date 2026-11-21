#![cfg(unix)]
mod common;
use common::{Env, isolated};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::process::{Child, Stdio};
use std::time::{Duration, Instant};

fn brain(env: &Env, sock: &std::path::Path) -> Child {
    env.cmd()
        .args(["brain", "--socket"])
        .arg(sock)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
}

fn answers(sock: &std::path::Path) -> bool {
    let Ok(stream) = UnixStream::connect(sock) else { return false };
    let mut r = BufReader::new(stream);
    r.get_mut().write_all(b"{\"id\":1,\"method\":\"no.such\"}\n").unwrap();
    let mut line = String::new();
    r.read_line(&mut line).is_ok() && line.contains("-32601")
}

fn wait_answers(sock: &std::path::Path) {
    let at = Instant::now();
    while !answers(sock) {
        assert!(at.elapsed() < Duration::from_secs(10), "the brain never answered");
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn exit_within(child: &mut Child, limit: Duration) -> Option<i32> {
    let at = Instant::now();
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            return status.code();
        }
        if at.elapsed() > limit {
            let _ = child.kill();
            panic!("still running after {limit:?}");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn exits_when_stdin_closes() {
    let env = isolated();
    let sock = env.root.join("run/brain.sock");
    let mut child = brain(&env, &sock);
    wait_answers(&sock);
    drop(child.stdin.take());
    assert_eq!(exit_within(&mut child, Duration::from_secs(5)), Some(0));
}

#[test]
fn a_second_brain_exits_three() {
    let env = isolated();
    let sock = env.root.join("run/brain.sock");
    let mut first = brain(&env, &sock);
    wait_answers(&sock);
    let second = env.cmd().args(["brain", "--socket"]).arg(&sock).stdin(Stdio::piped()).output().unwrap();
    assert_eq!(second.status.code(), Some(3), "{}", String::from_utf8_lossy(&second.stderr));
    assert!(answers(&sock), "the first brain still serves");
    drop(first.stdin.take());
    exit_within(&mut first, Duration::from_secs(5));
}

#[test]
fn never_removes_a_socket_that_answers() {
    let env = isolated();
    let sock = env.root.join("run/brain.sock");
    let _other = UnixListener::bind(&sock).unwrap();
    let out = env.cmd().args(["brain", "--socket"]).arg(&sock).stdin(Stdio::piped()).output().unwrap();
    assert_eq!(out.status.code(), Some(4), "{}", String::from_utf8_lossy(&out.stderr));
    assert!(sock.exists());
    assert!(UnixStream::connect(&sock).is_ok(), "the other listener's socket is untouched");
}

#[test]
fn a_stale_socket_file_is_replaced() {
    let env = isolated();
    let sock = env.root.join("run/brain.sock");
    drop(UnixListener::bind(&sock).unwrap());
    // A child another test thread forks keeps a copy of the listener until it execs: wait that out.
    let at = Instant::now();
    while UnixStream::connect(&sock).is_ok() {
        assert!(at.elapsed() < Duration::from_secs(5), "the dropped listener still answers");
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(sock.exists());
    let mut child = brain(&env, &sock);
    wait_answers(&sock);
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(std::fs::metadata(&sock).unwrap().permissions().mode() & 0o777, 0o600);
    drop(child.stdin.take());
    assert_eq!(exit_within(&mut child, Duration::from_secs(5)), Some(0));
}

#[test]
fn no_socket_exits_two() {
    let env = isolated();
    let out = env.cmd().args(["brain"]).stdin(Stdio::null()).output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert_eq!(String::from_utf8_lossy(&out.stderr).trim(), "asm brain needs --socket until the cutover.");
}
