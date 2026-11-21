//! Port of `assieme/permissions.py`, owned by session D (see OWNERS.md).
//!
//! What each agent may do: three levels and the person's own rules, written as each agent's own settings.
use crate::prelude::*;
use regex::Regex;
use serde_json::json;

pub const GEMINI_POLICY: &str = ".gemini/policies/assieme.toml";
pub const RULES_FILE: &str = ".codex/rules/assieme.rules";

type Program = Vec<String>;

fn prog(words: &[&str]) -> Program {
    words.iter().map(|w| w.to_string()).collect()
}

fn progs(list: &[&[&str]]) -> Vec<Program> {
    list.iter().map(|p| prog(p)).collect()
}

// Where git usually lives on a Mac, so `/usr/bin/git push` is denied as well as `git push`.
const GIT_PATHS: [&str; 5] = [
    "/usr/bin/git",
    "/opt/homebrew/bin/git",
    "/usr/local/bin/git",
    "/Library/Developer/CommandLineTools/usr/bin/git",
    "/Applications/Xcode.app/Contents/Developer/usr/bin/git",
];
const GIT_PUSH: [&[&str]; 3] = [&["git", "push"], &["git", "tag"], &["git", "merge"]];
const SECRET_PATHS: [&str; 7] = [
    "~/.ssh/**",
    "~/.aws/**",
    "~/.config/gcloud/**",
    "~/.azure/**",
    "~/.gnupg/**",
    "~/.netrc",
    "~/Library/Keychains/**",
];
// Allowing one of these would allow everything run through it.
const LAUNCHERS: [&str; 12] =
    ["bash", "sh", "zsh", "fish", "env", "sudo", "xargs", "eval", "exec", "nohup", "time", "command"];
// These run whatever they are given: allowed only with what they run (`python3 scripts/check.py`, `make lint`).
const RUNNERS: [&str; 16] = [
    "python",
    "python3",
    "node",
    "npx",
    "uvx",
    "pnpm",
    "bunx",
    "bun",
    "deno",
    "perl",
    "ruby",
    "osascript",
    "make",
    "npm",
    "yarn",
    "uv",
];
const RUNS_ANYTHING: [&str; 4] = ["dlx", "exec", "x", "run"];

/// Each item: its key, the words the editors show, its note, and where an agent kind covers less than the note says.
const ITEMS: [(&str, &str, &str); 11] = [
    ("edit_owned", "Edit files it owns", ""),
    ("tests", "Run tests, 2 CPU cores kept free", ""),
    ("commit", "Commit to its own branch", ""),
    ("message", "Message its team", ""),
    ("read", "Read the project and its history", ""),
    ("edit", "Edit files or commit", ""),
    ("push", "Push, tags, merge", "you merge in Review"),
    ("install", "Install packages or new dependencies", ""),
    ("download", "Download models or datasets", ""),
    ("secrets", "Read secrets (~/.ssh, cloud keys, keychain)", "its file tools and commands that name them"),
    ("network", "Direct network calls", ""),
];
const SECRETS_NOTES: [(&str, &str); 5] = [
    ("codex", "only the keychain command, for now"),
    ("gemini", "its file tools only, for now"),
    ("opencode", "its file tools only, for now"),
    ("cursor", "its file tools only, for now"),
    ("antigravity", "its file tools only, for now"),
];

/// id, title, about, summary, allowed, blocked.
type Level = (&'static str, &'static str, &'static str, &'static str, &'static [&'static str], &'static [&'static str]);
const LEVEL_INFO: [Level; 3] = [
    (
        "safe",
        "Safe",
        "Edit its files, test, commit locally",
        "Safe: edit its files, run tests, commit locally.",
        &["edit_owned", "tests", "commit", "message"],
        &["push", "install", "download", "secrets", "network"],
    ),
    (
        "read-only",
        "Read only",
        "For reviewers and researchers",
        "Read only: read and run tests, never edit.",
        &["read", "tests", "message"],
        &["push", "edit", "install", "download", "secrets", "network"],
    ),
    (
        "trusted",
        "Trusted",
        "Also install packages and go online",
        "Trusted: also install packages and go online. Never push.",
        &["edit_owned", "tests", "commit", "message", "install", "download", "network"],
        &["push", "secrets"],
    ),
];

/// The programs an item stands for (none: handled by the agent's own switches).
fn programs(key: &str) -> Vec<Program> {
    let git_push = |paths: &[&str]| -> Vec<Program> {
        paths.iter().flat_map(|g| GIT_PUSH.iter().map(move |p| [&[*g], &p[1..]].concat())).map(|p| prog(&p)).collect()
    };
    match key {
        "commit" | "edit" => progs(&[&["git", "add"], &["git", "commit"]]),
        "read" => progs(&[&["git", "status"], &["git", "diff"], &["git", "log"], &["git", "show"]]),
        "push" => GIT_PUSH.iter().map(|p| prog(p)).chain(git_push(&GIT_PATHS)).collect(),
        "install" => progs(&[
            &["npm", "install"],
            &["npm", "i"],
            &["npm", "add"],
            &["pnpm", "add"],
            &["pnpm", "install"],
            &["yarn", "add"],
            &["pip", "install"],
            &["pip3", "install"],
            &["uv", "add"],
            &["uv", "pip", "install"],
            &["poetry", "add"],
            &["cargo", "add"],
            &["cargo", "install"],
            &["brew", "install"],
            &["gem", "install"],
            &["go", "get"],
        ]),
        "download" => progs(&[
            &["wget"],
            &["git", "clone"],
            &["git", "lfs"],
            &["huggingface-cli", "download"],
            &["hf", "download"],
        ]),
        "secrets" => progs(&[&["security"]]),
        "network" => progs(&[&["curl"], &["nc"], &["ssh"], &["scp"], &["telnet"], &["ftp"]]),
        _ => vec![],
    }
}

pub fn levels() -> Vec<String> {
    LEVEL_INFO.iter().map(|l| l.0.to_string()).collect()
}

/// For the editors: the levels in order and the items' words.
pub fn table() -> Value {
    let level =
        |l: &Level| json!({"id": l.0, "title": l.1, "about": l.2, "summary": l.3, "allowed": l.4, "blocked": l.5});
    let item = |(k, words, note): &(&str, &str, &str)| {
        let notes = if *k == "secrets" { SECRETS_NOTES.iter().copied().collect() } else { BTreeMap::new() };
        (k.to_string(), json!({"words": words, "note": note, "notes": notes}))
    };
    json!({"levels": LEVEL_INFO.iter().map(level).collect::<Vec<_>>(), "items": ITEMS.iter().map(item).collect::<Map<_, _>>()})
}

/// Paths inside the project; a problem in words, or None.
pub fn check_owns(owns: &[String]) -> Option<String> {
    static OWNED: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^[A-Za-z0-9._/*@+-][A-Za-z0-9._/*@+ -]*$").expect("a fixed pattern"));
    owns.iter()
        .find(|p| !OWNED.is_match(p) || p.starts_with('/') || p.split('/').any(|s| s == ".."))
        .map(|p| format!("\"{p}\" is not a path inside the project."))
}

/// A rule's program words, cleaned; an error in words when it can't be a rule.
pub fn check_rule(decision: Option<&Value>, program: Option<&Value>) -> Result<Program, String> {
    static WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9._/+@=:,%~-]+$").expect("a fixed pattern")); // no spaces, globs, quotes
    let decision = decision.and_then(Value::as_str);
    if !matches!(decision, Some("block" | "allow")) {
        return Err("A rule either blocks or allows a program.".into());
    }
    let list = program.and_then(Value::as_array).filter(|a| (1..=6).contains(&a.len()));
    let Some(list) = list else {
        return Err("A rule names a program and at most a few of its first words.".into());
    };
    let words: Program = list.iter().map(|w| w.as_str().map_or_else(|| w.to_string(), String::from)).collect();
    if !words.iter().all(|w| WORD.is_match(w)) {
        return Err("A rule names a program and its first words, without spaces, wildcards or shell symbols.".into());
    }
    if decision == Some("allow") {
        let name = words[0].rsplit('/').next().unwrap_or_default();
        let named: Program = std::iter::once(name.to_string()).chain(words[1..].iter().cloned()).collect();
        if GIT_PUSH.iter().any(|f| f.iter().zip(&named).all(|(a, b)| a == b)) {
            return Err("Pushing stays blocked at every level. You merge in Review.".into());
        }
        if LAUNCHERS.contains(&name) {
            return Err(format!("Allowing {} would allow anything run through it. Name the program itself.", words[0]));
        }
        if RUNNERS.contains(&name)
            && words[1..].iter().all(|w| w.starts_with('-') || RUNS_ANYTHING.contains(&w.as_str()))
        {
            return Err(format!(
                "Allowing {} would allow anything it runs. Add what it runs, like {name} lint or {name} scripts/check.py.",
                words.join(" ")
            ));
        }
    }
    Ok(words)
}

pub fn check_rules(rules: &[Value]) -> Option<String> {
    rules.iter().find_map(|r| match r.as_object() {
        None => Some("A rule needs a decision and a program.".into()),
        Some(o) => check_rule(o.get("decision"), o.get("program")).err(),
    })
}

/// The ways a program is run: a script path also as ./path, its full path in the worktree, bash path and sh path.
fn variants(program: &[String], cwd: &str) -> Vec<Program> {
    let first = &program[0];
    if !first.contains('/') || first.starts_with(['/', '~']) {
        return vec![program.to_vec()];
    }
    let p = first.strip_prefix("./").unwrap_or(first);
    let mut paths = vec![p.to_string(), format!("./{p}")];
    if !cwd.is_empty() {
        paths.push(Path::new(cwd).join(p).to_string_lossy().into_owned());
    }
    let run = |lead: &[&str], x: &String| -> Program {
        lead.iter().map(|s| s.to_string()).chain([x.clone()]).chain(program[1..].iter().cloned()).collect()
    };
    paths
        .iter()
        .map(|x| run(&[], x))
        .chain(["bash", "sh"].iter().flat_map(|sh| paths.iter().map(|x| run(&[sh], x))))
        .collect()
}

fn bash(program: &[String]) -> String {
    format!("Bash({}:*)", program.join(" "))
}

fn uniq(v: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = BTreeSet::new();
    v.into_iter().filter(|x| seen.insert(x.clone())).collect()
}

fn items(role: &Role) -> (&'static [&'static str], &'static [&'static str]) {
    LEVEL_INFO.iter().find(|l| Some(l.0) == role.level.as_deref()).map_or((&[], &[]), |l| (l.4, l.5))
}

/// (allow, deny) for Claude Code's settings; empty for a role without a level.
pub fn claude(role: &Role, cwd: &str, test: Option<Vec<String>>) -> (Vec<String>, Vec<String>) {
    if role.level.is_none() {
        return (vec![], vec![]);
    }
    let (allowed, blocked) = items(role);
    let (mut allow, mut deny) = (vec![], vec![]);
    let base = if cwd.is_empty() { ".".to_string() } else { format!("/{}", cwd.trim_end_matches('/')) }; // `//abs`: absolute
    let home = crate::rules::home();
    for key in allowed {
        if *key == "edit_owned" {
            allow.extend(role.owns.iter().map(|p| format!("Edit({base}/{})", p.strip_prefix("./").unwrap_or(p))));
            if role.owns.is_empty() {
                allow.push(format!("Edit({base}/**)"));
            }
        } else if let (&"tests", Some(t)) = (key, &test) {
            allow.push(bash(t));
        }
        allow.extend(programs(key).iter().map(|p| bash(p)));
    }
    for key in blocked {
        deny.extend(programs(key).iter().map(|p| bash(p)));
        if *key == "secrets" {
            deny.extend(SECRET_PATHS.iter().map(|p| format!("Read({p})")));
            // shortcut: a path built another way (a variable, a glob) still gets through; step 16 checks every command.
            for p in SECRET_PATHS {
                let rel = p[2..].trim_end_matches("/**");
                deny.extend(["~", "$HOME", &home].iter().map(|h| format!("Bash(* {h}/{rel}*)")));
            }
        }
    }
    for r in &role.rules {
        let (list, words) =
            if r["decision"] == "allow" { (&mut allow, &r["program"]) } else { (&mut deny, &r["program"]) };
        let program: Program = serde_json::from_value(words.clone()).unwrap_or_default();
        list.extend(variants(&program, cwd).iter().map(|v| bash(v)));
    }
    (allow, deny) // a deny always wins in Claude Code, so a block rule beats the level's allow
}

/// Codex's sandbox for the level; empty for a role without one.
pub fn codex_args(role: &Role) -> Vec<String> {
    let Some(level) = &role.level else { return vec![] };
    if level == "read-only" || role.checkout == "read-only" {
        return prog(&["--sandbox", "read-only"]);
    }
    let mut a = prog(&["--sandbox", "workspace-write"]);
    if level == "trusted" {
        a.extend(prog(&["-c", "sandbox_workspace_write.network_access=true"]));
    }
    a
}

/// A rule's program as JSON text, spaced as Python's `json.dumps` spaces it.
fn dumps(program: &[String]) -> String {
    format!("[{}]", program.iter().map(|w| json!(w).to_string()).collect::<Vec<_>>().join(", "))
}

/// (forbidden, allowed) rule lines for one role.
fn codex_lines(role: &Role, cwd: &str) -> (Vec<String>, Vec<String>) {
    if role.level.is_none() {
        return (vec![], vec![]);
    }
    let (allowed, blocked) = items(role);
    let rule = |p: &[String], decision: &str, why: &str| {
        format!("prefix_rule(pattern={}, decision=\"{decision}\", justification={})", dumps(p), json!(why))
    };
    let words = |key: &str| ITEMS.iter().find(|i| i.0 == key).map_or("", |i| i.1);
    let (mut forbid, mut allow) = (vec![], vec![]);
    for key in blocked {
        forbid.extend(programs(key).iter().map(|p| rule(p, "forbidden", words(key))));
    }
    // Tests and reading run inside the sandbox; commits need .git, which the sandbox keeps read-only.
    for key in allowed.iter().filter(|k| ["commit", "install", "download", "network"].contains(k)) {
        allow.extend(programs(key).iter().map(|p| rule(p, "allow", words(key))));
    }
    for r in &role.rules {
        let program: Program = serde_json::from_value(r["program"].clone()).unwrap_or_default();
        let why = r["words"].as_str().filter(|w| !w.is_empty()).unwrap_or("your rule");
        for v in variants(&program, cwd) {
            if r["decision"] == "block" {
                forbid.push(rule(&v, "forbidden", why));
            } else {
                allow.push(rule(&v, "allow", why));
            }
        }
    }
    (forbid, allow)
}

/// Codex loads every .rules file in the folder, so each agent there gets all of them: one file holds every agent's
/// blocks and only the allows they all have, so no agent is widened by another's rules.
fn codex_rules(roles: &[&Role], cwd: &str) -> Option<String> {
    if roles.iter().all(|r| r.level.is_none()) {
        return None;
    }
    let lines: Vec<_> = roles.iter().map(|r| codex_lines(r, cwd)).collect();
    let forbid = uniq(lines.iter().flat_map(|l| l.0.clone()));
    let allow = uniq(lines[0].1.clone()).into_iter().filter(|x| lines[1..].iter().all(|l| l.1.contains(x)));
    let names = roles.iter().map(|r| r.name.as_str()).collect::<Vec<_>>().join(", ");
    let head = format!("# Written by Assieme for {names} when it starts. Change it in Assieme, not here.");
    Some(format!("{}\n", [head].into_iter().chain(forbid).chain(allow).collect::<Vec<_>>().join("\n")))
}

/// Write (or, when no agent there has a level, remove) a rules file: `path`'s folder is made as needed.
fn write_or_remove(path: &Path, text: Option<String>) -> bool {
    let Some(text) = text else {
        let _ = std::fs::remove_file(path);
        return false;
    };
    let dir = path.parent().unwrap_or(Path::new("."));
    std::fs::create_dir_all(dir).and_then(|()| std::fs::write(path, text)).is_ok()
}

/// Write (or remove) the rules file for the Codex agents in this folder.
pub fn write_codex_rules(cwd: &str, roles: &[&Role]) {
    write_or_remove(&Path::new(cwd).join(RULES_FILE), codex_rules(roles, cwd));
}

/// (allow, deny, allowed items, blocked items): the level's programs, then the person's rules, for the kinds whose
/// rules are plain command prefixes.
type Lists = (Vec<Program>, Vec<Program>, &'static [&'static str], &'static [&'static str]);
fn program_lists(role: &Role, cwd: &str, test: Option<Vec<String>>) -> Lists {
    let (allowed, blocked) = items(role);
    let mut allow: Vec<Program> = allowed.iter().flat_map(|k| programs(k)).collect();
    allow.extend(test.filter(|_| allowed.contains(&"tests")));
    let mut deny: Vec<Program> = blocked.iter().flat_map(|k| programs(k)).collect();
    for r in &role.rules {
        let program: Program = serde_json::from_value(r["program"].clone()).unwrap_or_default();
        let list = if r["decision"] == "allow" { &mut allow } else { &mut deny };
        list.extend(variants(&program, cwd));
    }
    (allow, deny, allowed, blocked)
}

fn secret_dirs() -> Vec<String> {
    SECRET_PATHS.iter().map(|p| format!("{}/{}", crate::rules::home(), p[2..].trim_end_matches("/**"))).collect()
}

/// OpenCode's `permission` rules, or None for a role without a level. Anything not named asks the person.
pub fn opencode(role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value> {
    role.level.as_ref()?;
    let (allow, deny, allowed, blocked) = program_lists(role, cwd, test);
    let mut bash = Map::from_iter([("*".to_string(), json!("ask"))]);
    // OpenCode's last matching pattern wins: the map keeps insertion order (preserve_order), so denies come last.
    for (decision, list) in [("allow", allow), ("deny", deny)] {
        for p in list {
            let line = p.join(" ");
            bash.insert(format!("{line} *"), json!(decision));
            bash.insert(line, json!(decision));
        }
    }
    let mut out = json!({"bash": bash, "edit": if allowed.contains(&"read") { "deny" } else { "allow" },
        "webfetch": if allowed.contains(&"network") { "allow" } else { "deny" }, "external_directory": "ask"});
    if blocked.contains(&"secrets") {
        let denied = SECRET_PATHS.iter().map(|p| (p.to_string(), json!("deny")));
        out["read"] = Value::Object(Map::from_iter([("*".to_string(), json!("allow"))].into_iter().chain(denied)));
    }
    Some(out)
}

/// Gemini CLI's policy rules (TOML), or None for a role without a level. A deny outranks any allow; anything not
/// named asks the person. Read only runs in Gemini's plan mode, which never edits.
fn gemini_policy(role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<String> {
    role.level.as_ref()?;
    let (allow, deny, _, blocked) = program_lists(role, cwd, test);
    let mut out =
        vec![format!("# Written by Assieme for {} when it starts. Change it in Assieme, not here.\n", role.name)];
    let mut rule = |decision: &str, priority: u32, fields: &[(&str, String)]| {
        let lines = fields.iter().map(|(k, v)| format!("{k} = {}", json!(v)));
        out.push(format!(
            "[[rule]]\n{}\ndecision = \"{decision}\"\npriority = {priority}\n",
            lines.collect::<Vec<_>>().join("\n")
        ));
    };
    let shell = |p: &Program| [("toolName", "run_shell_command".to_string()), ("commandPrefix", p.join(" "))];
    for p in &allow {
        rule("allow", 500, &shell(p));
    }
    for p in &deny {
        rule("deny", 900, &shell(p));
    }
    if blocked.contains(&"network") {
        rule("deny", 900, &[("toolName", "web_fetch".into())]);
    }
    if blocked.contains(&"secrets") {
        for d in secret_dirs() {
            rule("deny", 900, &[("toolName", "*".into()), ("argsPattern", regex::escape(&d))]); // its file tools
        }
    }
    Some(out.join("\n"))
}

/// The policy file in the agent's own folder, kept out of git; its path, or None (and no file) without a level.
pub fn write_gemini_policy(cwd: &str, role: &Role, test: Option<Vec<String>>) -> Option<String> {
    let path = Path::new(cwd).join(GEMINI_POLICY);
    let text = gemini_policy(role, cwd, test);
    (write_or_remove(&path, text)).then(|| path.to_string_lossy().into_owned())
}

/// Cursor's `permissions` {allow, deny}, or None for a role without a level. A deny always wins in Cursor.
pub fn cursor(role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value> {
    role.level.as_ref()?;
    let (allow, deny, allowed, blocked) = program_lists(role, cwd, test);
    // Cursor matches a command by its first word, and its arguments after a colon.
    let shell = |p: &Program| match p.len() {
        1 => format!("Shell({})", p[0]),
        _ => format!("Shell({}:{}*)", p[0], p[1..].join(" ")),
    };
    let mut deny: Vec<String> = deny.iter().map(shell).collect();
    if blocked.contains(&"network") {
        deny.push("WebFetch(*)".into());
    }
    if blocked.contains(&"secrets") {
        deny.extend(SECRET_PATHS.iter().map(|p| format!("Read({}/{})", crate::rules::home(), &p[2..])));
    }
    if allowed.contains(&"read") {
        deny.push("Write(**)".into());
    }
    Some(json!({"allow": uniq(allow.iter().map(shell)), "deny": uniq(deny)}))
}

/// Antigravity's `permissions` {allow, deny} for its session settings, or None for a role without a level. A deny
/// always wins; anything not named asks the person.
pub fn antigravity(role: &Role, cwd: &str, test: Option<Vec<String>>) -> Option<Value> {
    role.level.as_ref()?;
    let (allow, deny, _, blocked) = program_lists(role, cwd, test);
    let command = |p: &Program| format!("command({})", p.join(" "));
    let mut deny: Vec<String> = deny.iter().map(command).collect();
    if blocked.contains(&"network") {
        deny.extend(["read_url(*)".into(), "execute_url(*)".into()]);
    }
    if blocked.contains(&"secrets") {
        deny.extend(secret_dirs().iter().map(|d| format!("read_file({d})")));
    }
    Some(json!({"allow": uniq(allow.iter().map(command)), "deny": uniq(deny)}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn role(level: &str) -> Role {
        Role { name: "r".into(), level: Some(level.into()), ..Role::new("r", "claude") }
    }

    #[test]
    fn rules_that_cannot_be_allowed() {
        let ok = |d: &str, p: Value| check_rule(Some(&json!(d)), Some(&p));
        let table: [(&str, Value, Option<&str>); 9] = [
            ("allow", json!(["git", "push"]), Some("Pushing")),
            ("allow", json!(["/usr/bin/git", "merge", "x"]), Some("Pushing")),
            ("allow", json!(["bash"]), Some("anything run through")),
            ("allow", json!(["npm", "run"]), Some("anything it runs")),
            ("allow", json!(["npm", "run", "lint"]), None),
            ("allow", json!(["make", "lint"]), None),
            ("block", json!(["git", "push"]), None),
            ("allow", json!(["rm", "*"]), Some("without spaces")),
            ("allow", json!([]), Some("at most a few")),
        ];
        for (d, p, want) in table {
            match (ok(d, p.clone()), want) {
                (Ok(_), None) => {}
                (Err(e), Some(w)) => assert!(e.contains(w), "{p}: {e}"),
                (got, _) => panic!("{d} {p}: {got:?}"),
            }
        }
        assert!(check_rule(Some(&json!("maybe")), Some(&json!(["ls"]))).is_err());
    }

    #[test]
    fn every_level_blocks_push_and_the_right_things() {
        let rule = json!({"decision": "block", "program": ["rm"], "words": ""});
        let table = [
            ("safe", true, false), // (level, can commit, can install)
            ("read-only", false, false),
            ("trusted", true, true),
        ];
        for (level, commits, installs) in table {
            let mut r = role(level);
            r.rules = vec![rule.clone()];
            let (allow, deny) = claude(&r, "/w/x", Some(prog(&["cargo", "test"])));
            assert!(deny.contains(&"Bash(git push:*)".to_string()), "{level}");
            assert!(deny.contains(&"Bash(/usr/bin/git merge:*)".to_string()), "{level}");
            assert!(deny.contains(&"Bash(rm:*)".to_string()), "{level}");
            assert_eq!(deny.iter().filter(|d| *d == "Read(~/.ssh/**)").count(), 1, "{level}");
            assert!(allow.contains(&"Bash(cargo test:*)".to_string()), "{level}");
            assert_eq!(allow.contains(&"Bash(git commit:*)".to_string()), commits, "{level}");
            assert_eq!(allow.contains(&"Bash(npm install:*)".to_string()), installs, "{level}");
            assert_eq!(deny.contains(&"Bash(npm install:*)".to_string()), !installs, "{level}");
            assert_eq!(allow.iter().any(|a| a.starts_with("Edit(//w/x/")), level != "read-only", "{level}");
        }
        assert_eq!(claude(&Role::new("r", "claude"), "/w", None), (vec![], vec![]));
    }

    #[test]
    fn a_script_rule_covers_the_ways_it_is_run() {
        let v = variants(&prog(&["./scripts/go.sh", "-x"]), "/w");
        let lines: Vec<String> = v.iter().map(|p| p.join(" ")).collect();
        assert!(lines.contains(&"scripts/go.sh -x".into()) && lines.contains(&"/w/scripts/go.sh -x".into()));
        assert!(lines.contains(&"bash ./scripts/go.sh -x".into()) && lines.contains(&"sh /w/scripts/go.sh -x".into()));
        assert_eq!(variants(&prog(&["git", "log"]), "/w"), vec![prog(&["git", "log"])]);
    }

    #[test]
    fn codex_rules_never_widen_another_agent() {
        let (a, b) = (role("trusted"), role("safe"));
        let text = codex_rules(&[&a, &b], "/w").unwrap_or_default();
        assert!(text.contains(r#"pattern=["git", "push"], decision="forbidden""#));
        assert!(text.contains(r#"pattern=["git", "commit"], decision="allow""#)); // both have it
        assert!(!text.contains(r#"pattern=["npm", "install"], decision="allow""#)); // only the trusted one has it
        assert!(codex_rules(&[&Role::new("r", "codex")], "/w").is_none());
        let mut c = role("safe");
        c.rules = vec![
            json!({"decision": "block", "program": ["rm"], "words": "no rm"}),
            json!({"decision": "allow", "program": ["make", "lint"], "words": ""}),
        ];
        let text = codex_rules(&[&c], "/w").unwrap_or_default();
        for line in [
            r#"pattern=["git", "push"], decision="forbidden", justification="Push, tags, merge""#,
            r#"pattern=["rm"], decision="forbidden", justification="no rm""#,
            r#"pattern=["make", "lint"], decision="allow", justification="your rule""#,
        ] {
            assert!(text.contains(line), "{line}");
        }
        assert_eq!(
            codex_args(&a),
            prog(&["--sandbox", "workspace-write", "-c", "sandbox_workspace_write.network_access=true"])
        );
        assert_eq!(codex_args(&role("read-only")), prog(&["--sandbox", "read-only"]));
    }

    /// Each kind's (allow, deny) as program lines, and its whole deny text, for a role; None without a level.
    fn lists(kind: &str, r: &Role, test: Option<Program>) -> Option<(Vec<String>, Vec<String>, String)> {
        let inner =
            |s: &str, pre: &str, post: &str| s.strip_prefix(pre).and_then(|x| x.strip_suffix(post)).map(String::from);
        let strs = |v: &Value| {
            v.as_array().into_iter().flatten().filter_map(Value::as_str).map(String::from).collect::<Vec<_>>()
        };
        let (allow, deny, text) = match kind {
            "claude" => {
                let (a, d) = claude(r, "/w", test);
                if a.is_empty() && d.is_empty() {
                    return None;
                }
                let bash = |v: &[String]| v.iter().filter_map(|x| inner(x, "Bash(", ":*)")).collect();
                (bash(&a), bash(&d), d.join("\n"))
            }
            "opencode" => {
                let o = opencode(r, "/w", test)?;
                let pick = |want: &str| {
                    o["bash"]
                        .as_object()
                        .into_iter()
                        .flatten()
                        .filter(|(k, v)| *v == want && !k.ends_with(" *"))
                        .map(|(k, _)| k.clone())
                        .collect()
                };
                (pick("allow"), pick("deny"), o.to_string())
            }
            "gemini" => {
                let t = gemini_policy(r, "/w", test)?;
                let (mut a, mut d) = (vec![], vec![]);
                for block in t.split("[[rule]]").skip(1) {
                    let Some(line) = block.lines().find_map(|l| l.strip_prefix("commandPrefix = ")) else { continue };
                    let cmd: String = serde_json::from_str(line).unwrap_or_default();
                    if block.contains("decision = \"allow\"") { a.push(cmd) } else { d.push(cmd) }
                }
                (a, d, t)
            }
            "cursor" => {
                let o = cursor(r, "/w", test)?;
                let shell = |v: &Value| {
                    strs(v)
                        .iter()
                        .filter_map(|x| inner(x, "Shell(", ")"))
                        .map(|x| x.trim_end_matches('*').replacen(':', " ", 1))
                        .collect()
                };
                (shell(&o["allow"]), shell(&o["deny"]), o["deny"].to_string())
            }
            _ => {
                let o = antigravity(r, "/w", test)?;
                let cmd = |v: &Value| strs(v).iter().filter_map(|x| inner(x, "command(", ")")).collect();
                (cmd(&o["allow"]), cmd(&o["deny"]), o["deny"].to_string())
            }
        };
        Some((allow, deny, text))
    }

    #[test]
    fn every_agent_kind_allows_and_blocks_what_its_level_says() {
        let rules =
            vec![json!({"decision": "block", "program": ["rm"]}), json!({"decision": "allow", "program": ["./go.sh"]})];
        // a program line, then for safe, read-only and trusted: allowed (A), denied (D) or left to ask (-)
        let table = [
            ("git push", "DDD"),
            ("/usr/bin/git push", "DDD"),
            ("git commit", "ADA"),
            ("git status", "-A-"),
            ("npm install", "DDA"),
            ("wget", "DDA"),
            ("curl", "DDA"),
            ("security", "DDD"),
            ("cargo test", "AAA"),
            ("rm", "DDD"),
            ("go.sh", "AAA"),
        ];
        for kind in ["claude", "opencode", "gemini", "cursor", "antigravity"] {
            assert!(lists(kind, &Role::new("r", kind), None).is_none(), "{kind} without a level");
            for (i, level) in ["safe", "read-only", "trusted"].into_iter().enumerate() {
                let mut r = role(level);
                r.rules = rules.clone();
                let (allow, deny, text) = lists(kind, &r, Some(prog(&["cargo", "test"]))).expect("a level");
                for (line, want) in table {
                    let got = match (allow.iter().any(|a| a == line), deny.iter().any(|d| d == line)) {
                        (_, true) => 'D', // a deny wins in every kind
                        (true, false) => 'A',
                        _ => '-',
                    };
                    assert_eq!(got, want.as_bytes()[i] as char, "{kind} {level}: {line}");
                }
                assert!(text.contains(".ssh"), "{kind} {level}: secrets");
                let net = ["webfetch\":\"deny", "web_fetch", "WebFetch(*)", "read_url(*)"];
                if kind != "claude" {
                    assert_eq!(net.iter().any(|n| text.contains(n)), level != "trusted", "{kind} {level}: network");
                }
            }
        }
        assert_eq!(opencode(&role("read-only"), "/w", None).unwrap()["edit"], "deny");
        assert_eq!(opencode(&role("safe"), "/w", None).unwrap()["edit"], "allow");
        assert!(cursor(&role("read-only"), "/w", None).unwrap()["deny"].to_string().contains("Write(**)"));
        let o = opencode(&role("safe"), "/w", None).unwrap();
        assert_eq!(o["bash"].as_object().and_then(|m| m.keys().next()).map(String::as_str), Some("*")); // asks first
    }

    #[test]
    fn owned_paths_and_rules_are_checked() {
        let owns = |p: &str| check_owns(&[p.to_string()]).is_none();
        for (p, ok) in [
            ("src/**", true),
            ("a b.txt", true),
            ("/etc", false),
            ("../x", false),
            ("a/../b", false),
            ("", false),
            ("$(rm)", false),
        ] {
            assert_eq!(owns(p), ok, "{p}");
        }
        assert!(check_owns(&["src".into(), "/etc".into()]).is_some_and(|e| e.contains("/etc")));
        assert!(check_rules(&[json!({"decision": "allow", "program": ["make", "lint"]})]).is_none());
        assert!(check_rules(&[json!("rm")]).is_some_and(|e| e.contains("needs a decision")));
        assert!(check_rules(&[json!({"decision": "allow", "program": ["bash"]})]).is_some());
        assert_eq!(levels(), ["safe", "read-only", "trusted"]);
        let t = table();
        assert_eq!(t["items"]["secrets"]["notes"]["codex"], "only the keychain command, for now");
        assert_eq!(t["items"]["push"]["notes"], json!({}));
        assert_eq!(t["levels"][1]["blocked"][1], "edit");
    }

    #[test]
    fn rules_files_are_written_and_removed() {
        let dir = tempfile::tempdir().unwrap();
        let cwd = dir.path().to_string_lossy().into_owned();
        let path = write_gemini_policy(&cwd, &role("safe"), None).expect("written");
        assert!(std::fs::read_to_string(&path).unwrap().contains("git push"));
        assert_eq!(write_gemini_policy(&cwd, &Role::new("r", "gemini"), None), None);
        assert!(!Path::new(&path).exists());
        let codex = dir.path().join(RULES_FILE);
        write_codex_rules(&cwd, &[&role("safe")]);
        assert!(std::fs::read_to_string(&codex).unwrap().contains("forbidden"));
        write_codex_rules(&cwd, &[&Role::new("r", "codex")]);
        assert!(!codex.exists());
    }
}
