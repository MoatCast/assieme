//! Assieme's brain, ported from the Python package `assieme/`.
#![cfg(unix)]

pub mod method_names;
mod prelude;
pub mod pyrules;
pub mod server;

pub mod actions;
pub mod activity;
pub mod adapters;
pub mod agenttools;
pub mod answers;
pub mod antigravity;
pub mod ask;
pub mod askhook;
pub mod bridge;
pub mod brief;
pub mod bringover;
pub mod budget;
pub mod cli;
pub mod connect;
pub mod container;
pub mod detect;
pub mod fixes;
pub mod herdr;
pub mod hook;
pub mod improve;
pub mod launch;
pub mod limits;
pub mod live;
pub mod machine;
pub mod mcpserver;
pub mod mcptools;
pub mod menubar;
pub mod model;
pub mod musicians;
pub mod newteam;
pub mod panes;
pub mod permissions;
pub mod plugin;
pub mod preset;
pub mod preview;
pub mod process;
pub mod prompts;
pub mod quiet;
pub mod remote;
pub mod report;
pub mod reports;
pub mod review;
pub mod rules;
pub mod runtime;
pub mod schedule;
pub mod scripts;
pub mod serve;
pub mod service;
pub mod settings;
pub mod setup;
pub mod status;
pub mod tasks;
pub mod team;
pub mod teammap;
pub mod tools;
pub mod worktree;

use std::ffi::OsString;
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

/// Every socket method; each session replaces its `not_ported` entries.
pub fn methods() -> server::Methods {
    let mut methods = server::Methods::default();
    for &name in method_names::METHOD_NAMES {
        methods.insert(name, server::not_ported(name));
    }
    methods
}

/// The steps of the 15-second loop; each session adds its own.
pub fn steps() -> server::Loop {
    server::Loop::default()
}

/// `asm brain <args>`: the exit code.
pub fn run(args: Vec<OsString>) -> i32 {
    let mut socket = None;
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        match arg.to_str() {
            Some("--check") => return 0,
            Some("--socket") => socket = args.next().map(PathBuf::from),
            _ => {
                eprintln!("asm brain: unknown argument {}", arg.to_string_lossy());
                return 2;
            }
        }
    }
    let Some(socket) = socket else {
        eprintln!("asm brain needs --socket until the cutover.");
        return 2;
    };
    let dir = socket.parent().map(PathBuf::from).unwrap_or_default();
    if let Err(e) = assieme_core::platform::private_dir(&dir) {
        eprintln!("asm brain: can't create {}: {e}", dir.display());
        return 1;
    }
    // Held for the whole run: dropped only when the process ends.
    let lock = match std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(dir.join("brain.lock")) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("asm brain: can't open brain.lock: {e}");
            return 1;
        }
    };
    if !assieme_core::platform::try_lock(&lock) {
        eprintln!("Another brain is already running here ({}).", dir.display());
        return 3;
    }
    if UnixStream::connect(&socket).is_ok() {
        eprintln!("Another program answers on {}; it is left as it is.", socket.display());
        return 4;
    }
    let _ = std::fs::remove_file(&socket); // a stale file from a brain that died
    let old = unsafe { libc::umask(0o177) }; // created 0600: only this user can talk to the brain
    let bound = std::os::unix::net::UnixListener::bind(&socket);
    unsafe { libc::umask(old) };
    let listener = match bound.and_then(|l| l.set_nonblocking(true).map(|()| l)) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("asm brain: can't listen on {}: {e}", socket.display());
            return 1;
        }
    };
    // The core holds our stdin: when it exits, so does its brain.
    std::thread::spawn(|| {
        let _ = std::io::copy(&mut std::io::stdin(), &mut std::io::sink());
        std::process::exit(0);
    });
    steps().start(Duration::from_secs(15));
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("tokio runtime");
    runtime.block_on(async move {
        let listener = tokio::net::UnixListener::from_std(listener).expect("socket");
        server::serve(listener, Arc::new(methods())).await;
    });
    drop(lock);
    0
}
