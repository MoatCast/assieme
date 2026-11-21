//! Port of `assieme/container.py`, owned by session D (see OWNERS.md).
//!
//! An agent in a container that sees only its own copy of the project, with Docker.
//!
//! Assieme never installs either. The copy and the agent's settings file are mounted at the same paths they have on
//! the Mac, so the agent's command line is the one `launch` builds. Its home folder is a volume per kind and project,
//! so it signs in once per project; with share_signin, that kind's sign-in files from the Mac are mounted read-only.
use crate::prelude::*;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::process::Command;
use std::time::Duration;

#[derive(Debug, Clone, PartialEq)]
pub struct ContainerError(pub String);

impl From<ContainerError> for BrainError {
    fn from(e: ContainerError) -> BrainError {
        BrainError::new(e.0)
    }
}

// Apple's `container` isn't offered until it has been tried: its containers reach the Mac on an address the bridge
// doesn't listen on.
const HOST: &str = "host.docker.internal"; // how a container reaches this Mac
const HOME: &str = "/home/node";
const DOCKERFILE: &str = include_str!("../../../../assieme/container/Dockerfile");
// The sign-in files that can be shared, each read-only. Claude Code and Cursor keep theirs in the Mac's Keychain:
// they sign in inside. Read-only: a sign-in that needs renewing asks again inside the container.
const SIGNIN: [(&str, &[&str]); 3] = [
    ("codex", &[".codex/auth.json"]),
    ("gemini", &[".gemini/oauth_creds.json", ".gemini/google_accounts.json"]),
    ("opencode", &[".local/share/opencode/auth.json"]),
];
const NOT_RUNNING: &str = "Docker is on this Mac but not running.";
const NONE: &str = "Containers need Docker, and it isn't on this Mac.";

fn image(kind: &str) -> String {
    format!("assieme-agent-{kind}:1")
}

fn docker() -> Option<PathBuf> {
    crate::setup::agent_path("docker")
}

fn ok(docker: &Path, args: &[&str]) -> bool {
    let mut cmd = Command::new(docker);
    cmd.args(args);
    crate::setup::run(cmd, None, Duration::from_secs(5)).ok().flatten().is_some_and(|o| o.status.success())
}

/// `{tool, running, image_ready}`.
pub fn state(kind: &str) -> Value {
    let Some(d) = docker() else { return json!({"tool": null, "running": false, "image_ready": false}) };
    let running = ok(&d, &["info"]);
    json!({"tool": "docker", "running": running, "image_ready": running && ok(&d, &["image", "inspect", &image(kind)])})
}

/// Why an agent of this kind can't start in a container now, in Paper's words, or None.
pub fn problem(kind: &str) -> Option<String> {
    let s = state(kind);
    if s["tool"].is_null() {
        return Some(NONE.into());
    }
    if s["running"] != true {
        return Some(NOT_RUNNING.into());
    }
    (s["image_ready"] != true).then(|| {
        format!(
            "The container for {} is not built yet. Choose Download and build in Add agent.",
            crate::launch::name_of(kind)
        )
    })
}

/// The shell command that builds this kind's image (shown and run in a setup terminal).
pub fn build_command(kind: &str) -> Result<String, ContainerError> {
    let docker = docker().ok_or_else(|| ContainerError(NONE.into()))?;
    let recipe = crate::quiet::state_dir().join("container");
    // The Linux asm that the app carries beside the core, for the image's CPU (Docker here runs this Mac's own).
    let linux = std::env::current_exe()
        .ok()
        .and_then(|exe| {
            Some(exe.parent()?.parent()?.join("Resources/linux").join(std::env::consts::ARCH).join("assieme-core"))
        })
        .filter(|p| p.is_file())
        .ok_or_else(|| ContainerError("Assieme's program for containers is missing; update Assieme.".into()))?;
    std::fs::create_dir_all(&recipe)
        .and_then(|()| std::fs::write(recipe.join("Dockerfile"), DOCKERFILE))
        .and_then(|()| std::fs::copy(&linux, recipe.join("asm")).map(drop))
        .map_err(|e| ContainerError(format!("Couldn't write the container recipe: {e}.")))?;
    let kind_arg = format!("KIND={kind}");
    let words = [
        docker.to_string_lossy().as_ref(),
        "build",
        "-t",
        &image(kind),
        "--build-arg",
        &kind_arg,
        &recipe.to_string_lossy(),
    ]
    .map(|w| shlex::try_quote(w).map_or_else(|_| w.to_string(), |q| q.into_owned()));
    Ok(words.join(" "))
}

/// The project an agent's copy belongs to (<project>/.assieme/worktrees/<role>), as a short name for its volume.
fn project_key(path: &str) -> String {
    let project = path.split("/.assieme/worktrees/").next().unwrap_or(path);
    let real = std::fs::canonicalize(project).unwrap_or_else(|_| project.into());
    format!("{:x}", Sha256::digest(real.to_string_lossy().as_bytes()))[..12].to_string()
}

/// `docker run` around the agent's own command line. `inner` and `env` are what `launch` built for it.
pub fn run_argv(
    role: &Role,
    inner: &[String],
    env: &BTreeMap<String, String>,
    path: &str,
    terminal: &str,
    bridge: Option<&str>,
) -> Vec<String> {
    let docker = docker().map_or_else(|| "docker".into(), |d| d.to_string_lossy().into_owned());
    let mut a: Vec<String> = [&docker[..], "run", "--rm", "-it", "--init", "--name"].map(String::from).into();
    a.extend([format!("assieme-{terminal}"), "-v".into(), format!("{path}:{path}"), "-w".into(), path.into()]);
    // one per project: no agent reads another's
    a.extend(["-v".into(), format!("assieme-home-{}-{}:{HOME}", role.agent, project_key(path))]);
    if role.share_signin {
        for rel in SIGNIN.iter().filter(|(k, _)| *k == role.agent).flat_map(|(_, files)| *files) {
            let mine = Path::new(&crate::rules::home()).join(rel);
            if mine.is_file() {
                a.extend(["-v".into(), format!("{}:{HOME}/{rel}:ro", mine.display())]);
            }
        }
    }
    let mut env = env.clone();
    if let Some(b) = bridge {
        env.insert("ASSIEME_BRIDGE".into(), format!("{HOST}:{b}")); // 'port/ticket'
    }
    a.extend(env.iter().flat_map(|(k, v)| ["-e".to_string(), format!("{k}={v}")]));
    a.push(image(&role.agent));
    a.extend(inner.iter().cloned());
    a
}

#[cfg(test)]
mod tests {
    #[test]
    fn dockerfile_has_asm_and_no_python() {
        let recipe = super::DOCKERFILE.to_lowercase();
        assert!(recipe.contains("copy asm /usr/local/bin/asm"));
        assert!(!recipe.contains("pip") && !recipe.contains("python"));
    }
}
