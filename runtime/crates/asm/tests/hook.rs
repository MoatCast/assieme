//! `asm hook` writes the agent's status to its terminal, and starts in under 20 ms (the spec's budget).
#![cfg(unix)]
use std::io::Read;
use std::os::fd::FromRawFd;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// Runs `sh -c "asm hook <state>"` with a pseudo-terminal of its own as the terminal, so the hook finds it one step
/// up and never writes to the terminal running the tests. Returns what reached the terminal and how long it took.
fn hook(state: &str) -> (String, Duration) {
    let (mut master, mut slave) = (0, 0);
    // SAFETY: openpty fills the two descriptors, which are then owned by the files below.
    assert_eq!(
        unsafe {
            libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut())
        },
        0
    );
    let (mut master, slave) = unsafe { (std::fs::File::from_raw_fd(master), std::fs::File::from_raw_fd(slave)) };
    let mut sh = Command::new("sh");
    sh.arg("-c").arg(format!("{} hook {state} </dev/null", env!("CARGO_BIN_EXE_asm")));
    sh.stdin(slave.try_clone().unwrap()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: setsid and ioctl are async-signal-safe; the slave is the child's standard input.
    unsafe {
        sh.pre_exec(|| {
            libc::setsid();
            libc::ioctl(0, libc::TIOCSCTTY as _, 0);
            Ok(())
        })
    };
    // closing a terminal waits for its output to be read, as a terminal window does
    let reader = std::thread::spawn(move || {
        let mut out = String::new();
        let _ = master.read_to_string(&mut out); // ends with an error once every slave is closed
        out
    });
    let started = Instant::now();
    assert!(sh.status().unwrap().success());
    let took = started.elapsed();
    drop((sh, slave));
    (reader.join().unwrap(), took)
}

#[test]
fn reports_the_state_to_the_terminal() {
    for (state, expected) in [
        ("working", "\x1b]7501;state=working:app=agent\x1b\\"),
        ("blocked", "\x1b]7501;state=blocked:kind=permission:app=agent\x1b\\"),
        ("nonsense", ""),
    ] {
        assert_eq!(hook(state).0, expected, "{state}");
    }
}

#[test]
fn starts_in_under_20_ms() {
    let best = (0..20).map(|_| hook("idle").1).min().unwrap();
    eprintln!("hook best of 20, with its shell: {best:?}");
    assert!(best < Duration::from_millis(20), "{best:?}");
}
