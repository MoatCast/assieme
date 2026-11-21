//! Status hook for Claude Code and Codex: `asm hook <idle|working|blocked>`.
//!
//! Writes an OSC 7501 status report to the agent's own terminal, which the core reads as the agent's status.
//! Hooks run without a terminal of their own, so the agent's is found by walking up the process tree.
//! A hook must never break the agent: every failure is ignored and the exit code is always 0. It must also start fast,
//! so nothing here is async and no program is run.
use std::io::Write;

const STATES: [&str; 3] = ["idle", "working", "blocked"];

fn osc(state: &str) -> Vec<u8> {
    let kind = if state == "blocked" { ":kind=permission" } else { "" };
    format!("\x1b]7501;state={state}{kind}:app=agent\x1b\\").into_bytes()
}

/// (the terminal's device path if it has one, the parent) of a process.
#[cfg(target_os = "macos")]
fn tty_and_parent(pid: i32) -> Option<(Option<String>, i32)> {
    unsafe extern "C" {
        fn devname(dev: libc::dev_t, kind: libc::mode_t) -> *const libc::c_char;
    }
    // SAFETY: proc_pidinfo fills the struct it is given; devname returns a static string or null.
    unsafe {
        let mut info: libc::proc_bsdinfo = std::mem::zeroed();
        let size = std::mem::size_of_val(&info) as libc::c_int;
        if libc::proc_pidinfo(pid, libc::PROC_PIDTBSDINFO, 0, (&raw mut info).cast(), size) != size {
            return None;
        }
        let name = (info.e_tdev != u32::MAX).then(|| devname(info.e_tdev as libc::dev_t, libc::S_IFCHR));
        let tty =
            name.filter(|n| !n.is_null()).map(|n| format!("/dev/{}", std::ffi::CStr::from_ptr(n).to_string_lossy()));
        Some((tty, info.pbi_ppid as i32))
    }
}

#[cfg(not(target_os = "macos"))]
fn tty_and_parent(pid: i32) -> Option<(Option<String>, i32)> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    // after the command, which may hold spaces and parentheses: state, ppid, pgrp, session, tty_nr
    let mut fields = stat.get(stat.rfind(')')? + 1..)?.split_whitespace().skip(1);
    let ppid = fields.next()?.parse().ok()?;
    let tty_nr: u32 = fields.nth(2)?.parse().ok()?;
    // only a pseudo-terminal (major 136), which is what an agent in a terminal window has
    let tty = (tty_nr >> 8 & 0xfff == 136).then(|| format!("/dev/pts/{}", tty_nr & 0xff | tty_nr >> 12 & 0xfff00));
    Some((tty, ppid))
}

fn find_tty(mut pid: i32) -> Option<String> {
    while pid > 1 {
        let (tty, parent) = tty_and_parent(pid)?;
        if tty.is_some() {
            return tty;
        }
        pid = parent;
    }
    None
}

pub fn main(args: &[String]) -> i32 {
    let _ = std::io::copy(&mut std::io::stdin().lock(), &mut std::io::sink());
    let report = args.first().filter(|s| STATES.contains(&s.as_str()));
    let tty = report.and_then(|_| find_tty(unsafe { libc::getppid() }));
    if let (Some(state), Some(tty)) = (report, tty)
        && let Ok(mut f) = std::fs::OpenOptions::new().write(true).open(tty)
    {
        let _ = f.write_all(&osc(state));
    }
    0
}
