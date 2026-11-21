//! Port of `assieme/quiet.py`, owned by session C (see OWNERS.md).
//!
//! A3 quiet send: routine notes between agents wait for the receiver's next turn instead of waking it.
use crate::prelude::*;
use serde_json::json;
use std::process::{Command, Stdio};

const MAX_HOLD: f64 = 20.0 * 60.0; // deliver anyway after this, even if the receiver stays idle
const GIVE_UP_AFTER: f64 = 24.0 * 3600.0; // notes for an agent that has gone wait this long, in case it comes back
const ALIVE_FOR: f64 = 60.0; // a loop that touched the heartbeat this recently is running
const BLOCKER: &str = "git-remote-assieme-push-blocked";

fn epoch(now: Option<Timestamp>) -> f64 {
    now.unwrap_or_else(Timestamp::now).as_millisecond() as f64 / 1000.0
}

/// The array in a state file; anything else reads as empty.
fn list(name: &str) -> Vec<Value> {
    match read_json(&path(&[name]), json!([])) {
        Value::Array(a) => a,
        _ => vec![],
    }
}

fn write_list(name: &str, items: Vec<Value>) {
    let _ = write_json(&path(&[name]), &Value::Array(items)); // a toggle that can't be saved is retried by the next press
}

/// Remove exactly the notes that were sent; one that arrived meanwhile stays for the next round.
pub fn forget_delivered(delivered: &[Value]) {
    let Ok(_lock) = locked() else { return };
    write_list("held.json", list("held.json").into_iter().filter(|n| !delivered.contains(n)).collect());
}

fn hold(note: Value) -> std::io::Result<()> {
    let _lock = locked()?;
    let mut all = list("held.json");
    all.push(note);
    write_json(&path(&["held.json"]), &Value::Array(all))
}

/// A file in the state folder.
pub fn path(parts: &[&str]) -> PathBuf {
    parts.iter().fold(state_dir(), |p, x| p.join(x))
}

/// The JSON in `path`, or `default` when it is missing or damaged.
pub fn read_json(path: &Path, default: Value) -> Value {
    let Ok(bytes) = std::fs::read(path) else { return default };
    let Ok(text) = String::from_utf8(bytes) else { return default };
    crate::pyrules::json_loads(&text).unwrap_or(default)
}

/// Folders are compared resolved: /var and /private/var, or a symlinked checkout, are the same place.
pub fn real(path: &str) -> String {
    if path.is_empty() {
        return String::new();
    }
    let mut abs = std::path::absolute(path).unwrap_or_else(|_| path.into());
    let mut rest = vec![];
    // Python's realpath resolves what exists and keeps the rest as written.
    loop {
        if let Ok(done) = abs.canonicalize() {
            return rest.iter().rev().fold(done, |p, x| p.join(x)).to_string_lossy().into_owned();
        }
        let Some(name) = abs.file_name().map(|n| n.to_owned()) else { return path.into() };
        rest.push(name);
        abs.pop();
    }
}

/// `%Y-%m-%dT%H:%M:%SZ`, the time as the logs write it.
pub fn stamp(t: Timestamp) -> String {
    t.strftime("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// Python's `os.path.expanduser`: a leading `~` becomes $HOME.
pub fn expanduser(p: &str) -> String {
    match p.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => {
            format!("{}{rest}", std::env::var("HOME").unwrap_or_default())
        }
        _ => p.to_string(),
    }
}

/// `real` of a folder that may be missing or empty.
pub fn real_opt(path: Option<&str>) -> Option<String> {
    path.filter(|p| !p.is_empty()).map(real)
}

/// Written whole or not at all: a reader never sees half a file.
pub fn write_json(path: &Path, data: &Value) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    assieme_core::paths::write_atomic(path, &serde_json::to_vec(data)?)
}

/// Held while changing held.json: shims in several agents and the deliverer take turns.
pub fn locked() -> std::io::Result<Locked> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let file = std::fs::File::create(dir.join("held.lock"))?;
    // flock, as Python's fcntl.flock: the Python shims and the Rust brain take the same lock.
    if unsafe { libc::flock(std::os::fd::AsRawFd::as_raw_fd(&file), libc::LOCK_EX) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(Locked(file))
}

/// The lock on `held.lock`, released when dropped (closing the file releases it).
pub struct Locked(pub std::fs::File);

/// Called with a delivered batch of notes and why it went now.
pub type OnBatch = dyn Fn(&[Value], &str);
/// Called with notes that waited a day for an agent that stopped.
pub type OnGiveUp = dyn Fn(&[Value]);

fn at(n: &Value) -> f64 {
    n["at"].as_f64().unwrap_or(0.0)
}

/// The core a note must go back through. A plain `asm send` would reach the default session.
fn env(note: &Value) -> Vec<(&'static str, String)> {
    let Some(session) = note["session"].as_str().filter(|s| !s.is_empty()) else { return vec![] };
    let mut e = vec![("ASM_SESSION", session.to_string())];
    e.extend(note["socket"].as_str().map(|s| ("ASM_SOCKET", s.to_string())));
    e
}

/// What `asm` printed, when it exited 0 within `secs`.
fn capture(asm: &Path, args: &[&str], env: &[(&str, String)], secs: u64) -> Option<String> {
    use std::io::Read;
    let mut child = Command::new(asm)
        .args(args)
        .envs(env.iter().map(|(k, v)| (k, v)))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let mut out = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        out.read_to_string(&mut s).ok().map(|_| s)
    });
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(secs);
    let status = loop {
        if let Some(s) = child.try_wait().ok()? {
            break s;
        }
        if std::time::Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    };
    reader.join().ok()?.filter(|_| status.success())
}

/// The first `asm` on PATH that is not our stand-in. Never an `hc`: that is the person's own herdr.
fn real_asm() -> Option<PathBuf> {
    let shim = real(&shim_dir().to_string_lossy());
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .filter(|d| !d.as_os_str().is_empty() && real(&d.to_string_lossy()) != shim)
        .map(|d| d.join("asm"))
        .find(|p| p.is_file() && crate::pyrules::executable(p))
}

/// Terminal addresses get reused: the note goes only if this address is still the agent it was held for.
fn still_there(asm: &Path, note: &Value) -> bool {
    capture(asm, &["get", note["to"].as_str().unwrap_or(""), "--json"], &env(note), 10)
        .and_then(|s| crate::pyrules::json_loads(&s).ok())
        .is_some_and(|v| real(v["cwd"].as_str().unwrap_or("")) == note["to_cwd"].as_str().unwrap_or("-"))
}

/// Where an agent restarted in the same folder now is: the one live terminal there that no other held note is for.
/// None when there is none, or several (never guess which agent a note was for).
fn moved_to(asm: &Path, note: &Value, others: &BTreeSet<String>) -> Option<String> {
    let got = crate::pyrules::json_loads(&capture(asm, &["ls", "--json"], &env(note), 10)?).ok()?;
    let here: Vec<&str> = got["terminals"]
        .as_array()?
        .iter()
        .filter(|t| {
            t["state"].as_str().unwrap_or("live") == "live" && real(t["cwd"].as_str().unwrap_or("")) == note["to_cwd"]
        })
        .filter_map(|t| t["address"].as_str())
        .filter(|a| !others.contains(*a))
        .collect();
    (here.len() == 1).then(|| here[0].to_string())
}

fn batch_text(notes: &[Value]) -> String {
    let local = jiff::tz::TimeZone::system();
    let mut text = format!(
        "assieme: {} quiet note{} held while you were busy:",
        notes.len(),
        if notes.len() > 1 { "s" } else { "" }
    );
    for n in notes {
        let time = Timestamp::from_millisecond((at(n) * 1000.0) as i64)
            .map_or_else(|_| String::new(), |t| t.to_zoned_datetime(local.clone()).strftime("%H:%M").to_string());
        text +=
            &format!("\n- from {} at {time}: {}", n["from"].as_str().unwrap_or(""), n["text"].as_str().unwrap_or(""));
    }
    text
}

/// Send held notes: into a busy receiver's current turn, or after MAX_HOLD anyway. Returns what was sent, as
/// `[to, how many notes, why]`.
pub fn deliver(
    agents: &[Agent],
    now: Option<Timestamp>,
    on_batch: Option<&OnBatch>,
    on_give_up: Option<&OnGiveUp>,
) -> Vec<Value> {
    let now = epoch(now);
    let _ = std::fs::create_dir_all(state_dir());
    let _ = std::fs::write(path(&["deliverer.alive"]), now.to_string());
    let by_cwd: BTreeMap<String, &Agent> =
        agents.iter().filter_map(|a| a.cwd.as_deref().filter(|c| !c.is_empty()).map(|c| (real(c), a))).collect();
    let asm = real_asm();
    let mut sent = vec![];
    for (to_cwd, all_notes) in held() {
        let a = by_cwd.get(&to_cwd);
        let busy = a.is_some_and(|a| a.state == "working" || a.state == "waiting on subagents");
        let gone = a.is_some_and(|a| a.state == "exited");
        let mut batches: BTreeMap<(Option<String>, String), Vec<Value>> = BTreeMap::new(); // agents sharing a folder each get theirs
        for n in all_notes {
            batches
                .entry((n["session"].as_str().map(String::from), n["to"].as_str().unwrap_or("").into()))
                .or_default()
                .push(n);
        }
        let targets: BTreeSet<String> = batches.keys().map(|k| k.1.clone()).collect();
        for notes in batches.values() {
            let waited = now - notes.iter().map(at).fold(f64::INFINITY, f64::min);
            let Some(asm) = asm.as_deref().filter(|_| busy || waited >= MAX_HOLD) else { continue };
            let last = &notes[notes.len() - 1];
            let mut to = Some(last["to"].as_str().unwrap_or("").to_string());
            if !gone && !still_there(asm, last) {
                let mut others = targets.clone();
                others.remove(last["to"].as_str().unwrap_or(""));
                to = moved_to(asm, last, &others);
            }
            let Some(to) = to.filter(|_| !gone) else {
                if waited >= GIVE_UP_AFTER {
                    forget_delivered(notes);
                    on_give_up.iter().for_each(|f| f(notes));
                }
                continue;
            };
            if capture(asm, &["send", &to, &batch_text(notes)], &env(last), 60).is_some() {
                forget_delivered(notes);
                let why = if busy { "busy" } else { "overdue" };
                sent.push(json!([to, notes.len(), why]));
                on_batch.iter().for_each(|f| f(notes, why));
            }
        }
    }
    sent
}

pub fn deliverer_alive(now: Option<Timestamp>) -> bool {
    let Ok(modified) = std::fs::metadata(path(&["deliverer.alive"])).and_then(|m| m.modified()) else { return false };
    let modified = modified.duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64());
    epoch(now) - modified <= ALIVE_FOR
}

/// {to_cwd: [note, ...]} of notes waiting for delivery.
pub fn held() -> BTreeMap<String, Vec<Value>> {
    let mut out: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for n in list("held.json") {
        out.entry(n["to_cwd"].as_str().unwrap_or("").into()).or_default().push(n);
    }
    out
}

/// A note from Assieme itself or an agent's message tool, delivered with the agent's next turn.
#[allow(clippy::too_many_arguments)]
pub fn hold_note(
    to: &str,
    to_cwd: &str,
    text: &str,
    session: Option<&str>,
    socket: Option<&Path>,
    now: Option<Timestamp>,
    sender: &str,
    from_cwd: &str,
) {
    let _ = hold(json!({"at": epoch(now), "from_cwd": from_cwd, "from": sender, "to": to, "to_cwd": real(to_cwd),
                        "text": text, "session": session, "socket": socket}));
}

/// Write the asm stand-in. Returns its folder, which goes first on the agents' PATH.
pub fn install() -> std::io::Result<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    let dir = shim_dir();
    std::fs::create_dir_all(&dir)?;
    let _ = std::fs::remove_file(dir.join("hc")); // the stand-in from before the switch would shadow the person's own herdr
    let exe = std::env::current_exe()?;
    // git runs the blocker for assieme-push-blocked:// remotes (see launch.BLOCK_PUSH)
    // Only `asm send` can be held: everything else goes from the shell straight to the real asm, which is the faster start.
    let stand_in = format!(
        "#!/bin/sh\n# assieme quiet send: holds notes on quiet pairs, passes everything else to the real asm.\n\
         [ \"$1\" = send ] && exec \"{exe}\" shim \"$@\"\n\
         IFS=:\nfor d in $PATH; do\n  [ -n \"$d\" ] && ! [ \"$d/asm\" -ef \"$0\" ] && [ -x \"$d/asm\" ] && exec \"$d/asm\" \"$@\"\ndone\n\
         echo \"assieme: real asm not found on PATH\" >&2\nexit 127\n",
        exe = exe.display()
    );
    let files = [
        ("asm", stand_in),
        (BLOCKER, "#!/bin/sh\necho \"Assieme blocks pushes for this team. Ask the person to push, or turn the safety practice off.\" >&2\nexit 1\n".into()),
    ];
    for (name, body) in files {
        std::fs::write(dir.join(name), body)?;
        std::fs::set_permissions(dir.join(name), std::fs::Permissions::from_mode(0o755))?;
    }
    Ok(dir)
}

pub fn is_quiet(from_cwd: &str, to_cwd: &str) -> bool {
    let (from, to) = (real(from_cwd), real(to_cwd)); // the sender may run from a subfolder of its agent's folder
    pairs().iter().any(|p| {
        let pf = p["from_cwd"].as_str().unwrap_or("");
        (from == pf || from.starts_with(&format!("{pf}/"))) && p["to_cwd"] == to
    })
}

/// [from, to] pairs the person un-held.
fn opted_list() -> Vec<Value> {
    list("no_auto_hold.json").into_iter().filter(|p| p.as_array().is_some_and(|a| a.len() == 2)).collect()
}

/// True once a person un-held this pair: Assieme never holds it again by itself.
pub fn opted_out(from_cwd: &str, to_cwd: &str) -> bool {
    opted_list().contains(&json!([real(from_cwd), real(to_cwd)]))
}

/// Quiet pairs as [{from_cwd, to_cwd, label}].
pub fn pairs() -> Vec<Value> {
    list("quiet.json")
}

/// Share of the team's tokens a quiet pair saved: wake-ups avoided (notes minus batches) x that edge's average.
pub fn savings(pair: &Value, entries: &[Value], edge: &Value, total_tokens: i64) -> f64 {
    let wakes = edge["wakes"].as_f64().unwrap_or(0.0);
    if wakes == 0.0 || total_tokens == 0 {
        return 0.0;
    }
    let counts: Vec<f64> = entries
        .iter()
        .filter(|e| e["action"] == "deliver" && e["to_cwd"] == pair["to_cwd"])
        .filter_map(|e| e["from"][pair["from_cwd"].as_str()?].as_f64())
        .collect();
    (counts.iter().sum::<f64>() - counts.len() as f64).max(0.0) * edge["tokens"].as_f64().unwrap_or(0.0)
        / wakes
        / total_tokens as f64
}

pub fn set_pair(from_cwd: &str, to_cwd: &str, label: &str, on: bool) {
    let pair = json!([real(from_cwd), real(to_cwd)]);
    let mut keep: Vec<Value> = pairs().into_iter().filter(|p| json!([p["from_cwd"], p["to_cwd"]]) != pair).collect();
    let mut out: Vec<Value> = opted_list().into_iter().filter(|p| *p != pair).collect();
    if on {
        keep.push(json!({"from_cwd": pair[0], "to_cwd": pair[1], "label": label}));
    } else {
        out.push(pair); // the person's last word on a pair is remembered
    }
    write_list("no_auto_hold.json", out);
    write_list("quiet.json", keep);
}

pub fn shim_dir() -> PathBuf {
    path(&["bin"])
}

/// `asm ...` as the agents see it. Returns the exit code. Only `asm send` on a quiet pair is ever held.
pub fn shim_main(argv: &[String], cwd: Option<&str>, now: Option<Timestamp>) -> i32 {
    let Some(asm) = real_asm() else {
        eprintln!("assieme: real asm not found on PATH");
        return 127;
    };
    let pass = |args: &[String]| Command::new(&asm).args(args).status().map_or(127, |s| s.code().unwrap_or(1));
    if argv.first().map(String::as_str) != Some("send") {
        return pass(argv);
    }
    let mut args = argv[1..].to_vec();
    let go = |args: &[String]| pass(&[&["send".to_string()], args].concat());
    if let Some(i) = args.iter().position(|a| a == "--now") {
        args.remove(i);
        return go(&args);
    }
    let words: Vec<&String> = args.iter().filter(|a| !a.starts_with('-')).collect();
    if args.iter().any(|a| a == "--if") || words.len() < 2 || !deliverer_alive(now) {
        return go(&args); // conditional sends, odd shapes, or nobody to deliver: go now
    }
    let (target, text) = (words[0], words[1..].iter().map(|w| w.as_str()).collect::<Vec<_>>().join(" "));
    if urgent(&text) {
        return go(&args);
    }
    let info = capture(&asm, &["get", target, "--json"], &[], 10)
        .and_then(|s| crate::pyrules::json_loads(&s).ok())
        .unwrap_or(json!({}));
    let here = std::env::current_dir().unwrap_or_default();
    let sender = real(cwd.unwrap_or(&here.to_string_lossy()));
    if !info["cwd"].as_str().is_some_and(|c| !c.is_empty() && is_quiet(&sender, c)) {
        return go(&args);
    }
    let note = json!({"at": epoch(now), "from_cwd": sender, "from": Path::new(&sender).file_name().and_then(|n| n.to_str()),
        "to": target, "to_cwd": real(info["cwd"].as_str().unwrap_or("")), "text": text,
        "session": std::env::var("ASM_SESSION").ok(), "socket": std::env::var("ASM_SOCKET").ok(),
        "role": info["labels"]["assieme.role"], "team": info["labels"]["assieme.team"]});
    if hold(note).is_err() {
        return go(&args); // a note that can't be kept goes now rather than being lost
    }
    println!(
        "{}",
        json!({"held": true, "for": target, "note": "quiet send: delivered with its next turn, not waking it now"})
    );
    0
}

/// `$XDG_STATE_HOME/assieme`, else `~/.local/state/assieme`.
pub fn state_dir() -> PathBuf {
    match std::env::var_os("XDG_STATE_HOME").filter(|x| !x.is_empty()) {
        Some(base) => PathBuf::from(base).join("assieme"),
        None => {
            // Python's expanduser: HOME without its trailing slashes.
            let home = std::env::home_dir().unwrap_or_default();
            let home = home.to_str().map_or(home.clone(), |h| PathBuf::from(h.trim_end_matches('/')));
            PathBuf::from(format!("{}/.local/state/assieme", home.display()))
        }
    }
}

pub fn uninstall() {
    for name in ["asm", "hc", BLOCKER] {
        let _ = std::fs::remove_file(shim_dir().join(name));
    }
}

pub fn urgent(text: &str) -> bool {
    let t = text.trim();
    t.ends_with('?') || t.to_uppercase().starts_with("URGENT") || t.starts_with('!')
}
