//! Port of `assieme/machine.py`, owned by session E (see OWNERS.md).
//!
//! The optional F6 sampler: free memory and load, once a minute from the brain's tick, in a small capped file.
//! `machine.read` is trimmed: only the writer is ported.
use crate::prelude::*;
use serde_json::json;
use std::io::{Read, Seek, SeekFrom, Write};

const EVERY: f64 = 60.0;
const MAX_BYTES: u64 = 256 * 1024;

fn free_pct() -> Option<f64> {
    if cfg!(target_os = "macos") {
        let out = std::process::Command::new("sysctl").args(["-n", "kern.memorystatus_level"]).output().ok()?;
        return String::from_utf8_lossy(&out.stdout).trim().parse().ok();
    }
    let info = std::fs::read_to_string("/proc/meminfo").ok()?;
    let kb = |key: &str| -> Option<f64> {
        info.lines().find_map(|l| l.strip_prefix(key)?.strip_prefix(':')?.split_whitespace().next()?.parse().ok())
    };
    Some(100.0 * kb("MemAvailable")? / kb("MemTotal")?)
}

fn sample() -> Option<Value> {
    let mut load = [0f64; 1];
    // SAFETY: one writable double for the one average asked.
    if unsafe { libc::getloadavg(load.as_mut_ptr(), 1) } < 1 {
        return None;
    }
    let cpus = std::thread::available_parallelism().map_or(1, usize::from);
    Some(json!({"free_pct": free_pct()?, "load": load[0], "cpus": cpus}))
}

fn last_at(file: &Path) -> Option<Timestamp> {
    let mut f = std::fs::File::open(file).ok()?;
    f.seek(SeekFrom::End(-512.min(f.metadata().ok()?.len() as i64))).ok()?;
    let mut tail = vec![];
    f.read_to_end(&mut tail).ok()?;
    let row = crate::pyrules::json_loads_bytes(tail.split(|&b| b == b'\n').rfind(|l| !l.is_empty())?).ok()?;
    crate::model::parse_ts(&row["at"])
}

/// Record one sample if the last is at least a minute old. Returns whether it did. Never fatal.
pub fn tick(now: Timestamp, file: Option<&Path>) -> bool {
    let file = file.map_or_else(|| crate::quiet::state_dir().join("machine.jsonl"), Path::to_path_buf);
    if last_at(&file).is_some_and(|last| now.as_second() - last.as_second() < EVERY as i64) {
        return false;
    }
    let write = || -> Option<()> {
        let mut row = sample()?;
        row["at"] = json!(now.strftime("%Y-%m-%dT%H:%M:%SZ").to_string());
        std::fs::create_dir_all(file.parent()?).ok()?;
        if std::fs::metadata(&file).is_ok_and(|m| m.len() >= MAX_BYTES) {
            // Keep the newest half, from the next whole line.
            let bytes = std::fs::read(&file).ok()?;
            let half = &bytes[bytes.len() - (MAX_BYTES / 2) as usize..];
            let keep = half.iter().position(|&b| b == b'\n').map_or(&half[half.len()..], |i| &half[i + 1..]);
            assieme_core::paths::write_atomic(&file, keep).ok()?;
        }
        let mut f = std::fs::OpenOptions::new().append(true).create(true).open(&file).ok()?;
        writeln!(f, "{row}").ok()
    };
    write().is_some()
}
