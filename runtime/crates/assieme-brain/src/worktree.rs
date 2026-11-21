//! Port of `assieme/worktree.py`, owned by session D (see OWNERS.md).
//!
//! One git worktree per role, under <project>/.assieme/worktrees/<role>. Uses the git command.
//!
//! A role that runs in a container gets a clone at that path instead: a worktree's `.git` points into the project's
//! own, and the container could write hooks there that later run on the Mac. Its commits come back with `sync`.
use crate::prelude::*;
use std::process::Output;

/// Git options that run no hook or watcher a container agent could have planted.
pub const SAFE: &[&str] = &["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"];

#[derive(Debug, Clone, PartialEq)]
pub struct WorktreeError {
    pub message: String,
    pub detail: String,
}

impl WorktreeError {
    fn new(message: impl Into<String>, detail: impl Into<String>) -> WorktreeError {
        WorktreeError { message: message.into(), detail: detail.into() }
    }
}

impl std::fmt::Display for WorktreeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl From<WorktreeError> for BrainError {
    fn from(e: WorktreeError) -> BrainError {
        BrainError { message: e.message, detail: Some(e.detail).filter(|d| !d.is_empty()) }
    }
}

fn real(path: &str) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| PathBuf::from(path))
}

/// A container agent's copy: a repository of its own at <project>/.assieme/worktrees/<role>.
pub fn is_clone(path: &str) -> bool {
    let real = real(path);
    let Some(parent) = real.parent() else { return false };
    let name = |p: Option<&Path>| p.and_then(Path::file_name).map(|n| n.to_os_string());
    name(Some(parent)).is_some_and(|n| n == "worktrees")
        && name(parent.parent()).is_some_and(|n| n == ".assieme")
        && Path::new(path).join(".git").is_dir()
}

/// `git -C cwd args`, with SAFE in a container agent's clone.
pub fn git(cwd: &str, args: &[&str]) -> std::io::Result<Output> {
    let mut cmd = std::process::Command::new("git");
    if is_clone(cwd) {
        cmd.args(SAFE);
    }
    cmd.arg("-C").arg(cwd).args(args).stdin(std::process::Stdio::null()).output()
}

/// A git call's outcome: did it succeed, what it printed (trimmed) and what it said on stderr (trimmed).
pub struct Git {
    pub ok: bool,
    pub out: String,
    pub err: String,
}

/// `git` with its answer read; a git that cannot start is a failed call.
pub fn run(cwd: &str, args: &[&str]) -> Git {
    match git(cwd, args) {
        Ok(o) => Git {
            ok: o.status.success(),
            out: String::from_utf8_lossy(&o.stdout).trim().to_string(),
            err: String::from_utf8_lossy(&o.stderr).trim().to_string(),
        },
        Err(e) => Git { ok: false, out: String::new(), err: e.to_string() },
    }
}

fn name_of(project: &str) -> String {
    Path::new(project.trim_end_matches('/')).file_name().map_or_else(String::new, |n| n.to_string_lossy().into_owned())
}

pub fn check_repo(project: &str) -> Result<(), WorktreeError> {
    if !run(project, &["rev-parse", "--show-toplevel"]).ok {
        return Err(WorktreeError::new(
            format!("{} isn't set up to keep versions yet. Choose it in New team to set it up.", name_of(project)),
            "",
        ));
    }
    if !run(project, &["rev-parse", "--verify", "--quiet", "HEAD"]).ok {
        return Err(WorktreeError::new(
            format!("{} has no saved version yet. Choose it in New team to save a first version.", name_of(project)),
            "",
        ));
    }
    Ok(())
}

/// Keep a file Assieme writes out of every commit: one line in the repository's own info/exclude.
pub fn exclude(project: &str, line: &str) {
    let rel = run(project, &["rev-parse", "--git-path", "info/exclude"]).out;
    if rel.is_empty() {
        return;
    }
    let path = Path::new(project).join(&rel); // an absolute path stays as it is
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let lines: Vec<&str> = text.lines().collect();
    if lines.contains(&line) {
        return;
    }
    let lead = if lines.last().is_some_and(|l| !l.is_empty()) { "\n" } else { "" };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{lead}{line}");
    }
}

/// Where the role works.
pub fn folder(team: &Team, role: &Role) -> String {
    format!("{}/.assieme/worktrees/{}", team.project, role.name)
}

/// A brought-over role's own folder (step 30), while it is still there.
fn kept(role: &Role) -> Option<&str> {
    role.worktree.as_deref().filter(|w| !w.is_empty() && Path::new(w).is_dir())
}

const TOOL_PATHS: &[(&str, &str)] = &[
    ("/conductor/", "Conductor"),
    ("/.conductor/", "Conductor"),
    ("/.claude-squad/", "Claude Squad"),
    ("/vibe-kanban/", "Vibe Kanban"),
    ("/.vibe-kanban-workspaces/", "Vibe Kanban"),
];

/// The tool a brought-over folder belongs to, from where it is; None when the path doesn't say.
pub fn tool_name(folder: &str) -> Option<&'static str> {
    let with_end = format!("{folder}/");
    TOOL_PATHS.iter().find(|(part, _)| with_end.contains(part)).map(|(_, name)| *name)
}

/// The branch the role works on: the one it was brought over with while its folder is there, else Assieme's.
pub fn branch_of(team: &Team, role: &Role) -> String {
    match &role.branch {
        Some(b) if kept(role).is_some() && !b.is_empty() => b.clone(),
        _ => format!("assieme/{}/{}", team.name, role.name),
    }
}

fn same_repo(project: &str, path: &str) -> bool {
    let common = |p: &str| run(p, &["rev-parse", "--path-format=absolute", "--git-common-dir"]);
    let (a, b) = (common(project), common(path));
    a.ok && b.ok && real(&a.out) == real(&b.out)
}

fn failed(name: &str, args: &[&str], r: &Git) -> WorktreeError {
    WorktreeError::new(format!("Assieme couldn't make {name}'s copy."), format!("git {}\n{}", args.join(" "), r.err))
}

/// The role's worktree path: created on first use, reused while clean, never reset. A brought-over role works in
/// its own folder as it is, uncommitted work included; when that folder has gone, it gets an Assieme copy that
/// starts from its branch.
pub fn ensure(team: &Team, role: &Role) -> Result<String, WorktreeError> {
    check_repo(&team.project)?;
    if let Some(kept) = kept(role) {
        if !same_repo(&team.project, kept) {
            return Err(WorktreeError::new(
                format!("{kept} isn't a copy of this project. Bring it over again from its own project."),
                "",
            ));
        }
        let now = run(kept, &["branch", "--show-current"]).out;
        if let Some(want) = role.branch.as_deref().filter(|b| !b.is_empty() && **b != now) {
            // Review would follow the old branch and miss the agent's commits
            let there = if now.is_empty() { "no branch" } else { &now };
            return Err(WorktreeError::new(
                format!("{} is on {there} now, not {want}. Switch it back, or bring it over again.", name_of(kept)),
                "",
            ));
        }
        return Ok(kept.to_string());
    }
    let path = folder(team, role);
    let branch = format!("assieme/{}/{}", team.name, role.name);
    let exists = |b: &str| run(&team.project, &["rev-parse", "--verify", "--quiet", &format!("refs/heads/{b}")]).ok;
    let start = match &role.branch {
        Some(b) if !b.is_empty() && !Path::new(&path).is_dir() && exists(b) => b.as_str(),
        _ => "HEAD",
    };
    exclude(&team.project, ".assieme/");
    if Path::new(&path).is_dir() {
        let top = run(&path, &["rev-parse", "--show-toplevel"]);
        if !top.ok || real(&top.out) != real(&path) {
            return Err(WorktreeError::new(
                format!("A folder is in the way of {}'s copy. Move it, then start again.", role.name),
                path,
            ));
        }
        let st = run(&path, &["status", "--porcelain"]);
        if !st.ok || !st.out.is_empty() {
            return Err(WorktreeError::new(
                format!(
                    "{}'s copy has changes that aren't saved yet. Save or remove them, then start again.",
                    role.name
                ),
                path,
            ));
        }
        return Ok(path);
    }
    let read_only = role.checkout == "read-only";
    if role.container {
        return clone(&team.project, &path, &branch, read_only, &role.name);
    }
    let args: Vec<&str> = if read_only {
        vec!["worktree", "add", "--detach", &path, "HEAD"]
    } else {
        if exists(&branch) {
            return Err(WorktreeError::new(
                format!(
                    "A copy named {branch} is left over from an earlier team. Rename this team, or remove the old copy."
                ),
                format!("git branch -D {branch}"),
            ));
        }
        vec!["worktree", "add", "-b", &branch, &path, start]
    };
    let r = run(&team.project, &args);
    if !r.ok {
        return Err(failed(&role.name, &args, &r));
    }
    Ok(path)
}

/// The container's copy: its own branch, made in the project first so Review knows it.
fn clone(project: &str, path: &str, branch: &str, read_only: bool, name: &str) -> Result<String, WorktreeError> {
    // A branch already there (its work brought back by an undo) is where the copy starts.
    let has = run(project, &["rev-parse", "--verify", "--quiet", &format!("refs/heads/{branch}")]).ok;
    if !read_only && !has {
        let args = ["branch", branch, "HEAD"];
        let r = run(project, &args);
        if !r.ok {
            return Err(failed(name, &args, &r));
        }
    }
    // No hard links: the container must not be able to change the project's own object files.
    let mut args = vec!["clone", "--quiet", "--no-hardlinks"];
    if !read_only {
        args.extend(["--branch", branch]);
    }
    args.extend([project, path]);
    let r = run(project, &args);
    if !r.ok {
        return Err(failed(name, &args, &r));
    }
    if read_only {
        run(path, &["checkout", "--quiet", "--detach"]);
    }
    for key in ["user.name", "user.email"] {
        // who the agent's commits are by, as in a worktree
        let value = run(project, &["config", key]).out;
        if !value.is_empty() {
            run(path, &["config", key, &value]);
        }
    }
    Ok(path.to_string())
}

/// Fetch each container agent's branch from its clone into the project, so Review and the board see its commits.
pub fn sync(project: &str) {
    let base = Path::new(project).join(".assieme").join("worktrees");
    let Ok(names) = std::fs::read_dir(&base) else { return };
    let is_one = |n: &std::ffi::OsString| is_clone(&base.join(n).to_string_lossy());
    if !names.flatten().any(|e| is_one(&e.file_name())) {
        return; // the usual case: no git call at all
    }
    for r in run(project, &["for-each-ref", "--format=%(refname)", "refs/heads/assieme/"]).out.split_whitespace() {
        let path = base.join(r.rsplit('/').next().unwrap_or(""));
        let path = path.to_string_lossy();
        if is_clone(&path) {
            run(project, &["fetch", "--quiet", "--no-tags", &path, &format!("+{r}:{r}")]);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A repository with one commit, in a temporary folder.
    fn repo() -> (tempfile::TempDir, String) {
        let dir = tempfile::tempdir().unwrap();
        let p = std::fs::canonicalize(dir.path()).unwrap().to_string_lossy().into_owned();
        for args in
            [vec!["init", "-q", "-b", "main"], vec!["config", "user.name", "T"], vec!["config", "user.email", "t@t"]]
        {
            assert!(run(&p, &args).ok);
        }
        std::fs::write(format!("{p}/a.txt"), "one\n").unwrap();
        assert!(run(&p, &["add", "."]).ok);
        assert!(run(&p, &["commit", "-q", "-m", "first"]).ok);
        (dir, p)
    }

    fn team(project: &str) -> Team {
        Team { name: "t".into(), project: project.into(), ..Team::default() }
    }

    fn role(name: &str) -> Role {
        Role {
            name: name.into(),
            agent: "claude".into(),
            checkout: "branch".into(),
            tools_new: true,
            ..Role::default()
        }
    }

    #[test]
    fn a_folder_that_is_not_a_repository_is_refused_in_words() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().to_string_lossy().into_owned();
        let e = check_repo(&p).unwrap_err();
        assert!(e.message.ends_with("isn't set up to keep versions yet. Choose it in New team to set it up."), "{e}");
    }

    #[test]
    fn a_repository_with_no_commit_is_refused_in_words() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().to_string_lossy().into_owned();
        assert!(run(&p, &["init", "-q"]).ok);
        let e = check_repo(&p).unwrap_err();
        assert!(e.message.ends_with("has no saved version yet. Choose it in New team to save a first version."), "{e}");
    }

    #[test]
    fn a_role_gets_its_own_branch_worktree_and_keeps_it_out_of_commits() {
        let (_d, p) = repo();
        let (tm, r) = (team(&p), role("builder"));
        let path = ensure(&tm, &r).unwrap();
        assert_eq!(path, format!("{p}/.assieme/worktrees/builder"));
        assert_eq!(run(&path, &["branch", "--show-current"]).out, "assieme/t/builder");
        assert!(Path::new(&path).join("a.txt").is_file());
        assert!(run(&p, &["status", "--porcelain"]).out.is_empty(), "the .assieme folder is excluded");
        assert_eq!(ensure(&tm, &r).unwrap(), path, "a clean copy is reused");
    }

    #[test]
    fn exclude_writes_the_line_once() {
        let (_d, p) = repo();
        exclude(&p, ".assieme/");
        exclude(&p, ".assieme/");
        let text = std::fs::read_to_string(format!("{p}/.git/info/exclude")).unwrap();
        assert_eq!(text.lines().filter(|l| *l == ".assieme/").count(), 1);
    }

    #[test]
    fn a_read_only_role_is_detached_and_has_no_branch() {
        let (_d, p) = repo();
        let r = Role { checkout: "read-only".into(), ..role("reviewer") };
        let path = ensure(&team(&p), &r).unwrap();
        assert_eq!(run(&path, &["branch", "--show-current"]).out, "");
        assert!(!run(&p, &["rev-parse", "--verify", "--quiet", "refs/heads/assieme/t/reviewer"]).ok);
    }

    #[test]
    fn a_copy_with_unsaved_changes_is_never_reset() {
        let (_d, p) = repo();
        let (tm, r) = (team(&p), role("builder"));
        let path = ensure(&tm, &r).unwrap();
        std::fs::write(format!("{path}/new.txt"), "x").unwrap();
        let e = ensure(&tm, &r).unwrap_err();
        assert!(e.message.contains("has changes that aren't saved yet"), "{e}");
        assert_eq!(e.detail, path);
        assert!(Path::new(&path).join("new.txt").exists());
    }

    #[test]
    fn a_plain_folder_in_the_way_is_refused() {
        let (_d, p) = repo();
        std::fs::create_dir_all(format!("{p}/.assieme/worktrees/builder")).unwrap();
        let e = ensure(&team(&p), &role("builder")).unwrap_err();
        assert!(e.message.contains("A folder is in the way of builder's copy"), "{e}");
    }

    #[test]
    fn a_branch_left_over_from_an_earlier_team_is_refused_with_the_fix() {
        let (_d, p) = repo();
        assert!(run(&p, &["branch", "assieme/t/builder"]).ok);
        let e = ensure(&team(&p), &role("builder")).unwrap_err();
        assert!(e.message.contains("is left over from an earlier team"), "{e}");
        assert_eq!(e.detail, "git branch -D assieme/t/builder");
        assert_eq!(BrainError::from(e).detail.as_deref(), Some("git branch -D assieme/t/builder"));
    }

    #[test]
    fn a_brought_over_folder_is_used_as_it_is_while_its_branch_matches() {
        let (_d, p) = repo();
        let other = format!("{p}-other");
        assert!(run(&p, &["worktree", "add", "-q", "-b", "feature", &other]).ok);
        std::fs::write(format!("{other}/wip.txt"), "uncommitted").unwrap();
        let r = Role { worktree: Some(other.clone()), branch: Some("feature".into()), ..role("builder") };
        assert_eq!(ensure(&team(&p), &r).unwrap(), other);
        assert_eq!(branch_of(&team(&p), &r), "feature");
        assert!(run(&other, &["checkout", "-q", "-b", "elsewhere"]).ok);
        let e = ensure(&team(&p), &r).unwrap_err();
        assert!(e.message.contains("is on elsewhere now, not feature"), "{e}");
        let _ = std::fs::remove_dir_all(&other);
    }

    #[test]
    fn a_brought_over_folder_from_another_project_is_refused() {
        let (_d, p) = repo();
        let (_d2, q) = repo();
        let r = Role { worktree: Some(q), ..role("builder") };
        let e = ensure(&team(&p), &r).unwrap_err();
        assert!(e.message.contains("isn't a copy of this project"), "{e}");
    }

    #[test]
    fn a_gone_brought_over_folder_gives_a_new_copy_starting_from_its_branch() {
        let (_d, p) = repo();
        assert!(run(&p, &["branch", "feature"]).ok);
        let r = Role { worktree: Some(format!("{p}-gone")), branch: Some("feature".into()), ..role("builder") };
        let tm = team(&p);
        assert_eq!(branch_of(&tm, &r), "assieme/t/builder");
        let path = ensure(&tm, &r).unwrap();
        assert_eq!(run(&path, &["branch", "--show-current"]).out, "assieme/t/builder");
    }

    #[test]
    fn a_container_role_gets_a_separate_clone_that_sync_brings_back() {
        let (_d, p) = repo();
        let (tm, r) = (team(&p), Role { container: true, ..role("builder") });
        let path = ensure(&tm, &r).unwrap();
        assert!(is_clone(&path));
        assert!(Path::new(&path).join(".git").is_dir(), "its own repository, not a pointer into the project's");
        assert_eq!(run(&path, &["config", "user.name"]).out, "T");
        assert_eq!(run(&path, &["branch", "--show-current"]).out, "assieme/t/builder");
        std::fs::write(format!("{path}/b.txt"), "from the container").unwrap();
        assert!(run(&path, &["add", "."]).ok);
        assert!(run(&path, &["commit", "-q", "-m", "work"]).ok);
        let before = run(&p, &["rev-parse", "assieme/t/builder"]).out;
        sync(&p);
        let after = run(&p, &["rev-parse", "assieme/t/builder"]).out;
        assert_ne!(before, after);
        assert_eq!(after, run(&path, &["rev-parse", "HEAD"]).out);
    }

    #[test]
    fn git_in_a_clone_runs_no_hook_the_container_planted() {
        let (_d, p) = repo();
        let r = Role { container: true, ..role("builder") };
        let path = ensure(&team(&p), &r).unwrap();
        let hook = format!("{path}/.git/hooks/pre-commit");
        std::fs::write(&hook, format!("#!/bin/sh\ntouch {p}/PWNED\n")).unwrap();
        std::fs::set_permissions(&hook, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
        std::fs::write(format!("{path}/c.txt"), "x").unwrap();
        run(&path, &["add", "."]);
        assert!(run(&path, &["commit", "-q", "-m", "x"]).ok);
        assert!(!Path::new(&format!("{p}/PWNED")).exists());
    }

    #[test]
    fn a_worktree_is_not_a_clone() {
        let (_d, p) = repo();
        let path = ensure(&team(&p), &role("builder")).unwrap();
        assert!(!is_clone(&path));
        assert!(!is_clone(&p));
        sync(&p); // nothing to fetch, nothing breaks
    }

    #[test]
    fn names_the_tool_a_folder_came_from() {
        assert_eq!(tool_name("/Users/x/conductor/workspaces/a"), Some("Conductor"));
        assert_eq!(tool_name("/Users/x/.claude-squad/worktrees/a"), Some("Claude Squad"));
        assert_eq!(tool_name("/Users/x/.vibe-kanban-workspaces"), Some("Vibe Kanban"));
        assert_eq!(tool_name("/Users/x/project"), None);
    }
}
