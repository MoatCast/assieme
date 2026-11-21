#![cfg(unix)]
mod common;
use common::{asm, isolated};

#[test]
fn help_is_the_core_help() {
    let out = asm(&isolated(), &["--help"]);
    assert!(out.status.success());
    assert_eq!(String::from_utf8(out.stdout).unwrap(), include_str!("../../assieme-core/src/help.txt"));
}

#[test]
fn version_is_the_workspace_version() {
    let workspace = include_str!("../../../Cargo.toml");
    let version = workspace.lines().find_map(|l| l.strip_prefix("version = \"")).unwrap().trim_end_matches('"');
    let out = asm(&isolated(), &["--version"]);
    assert!(out.status.success());
    assert!(String::from_utf8(out.stdout).unwrap().contains(version), "{version}");
}

#[test]
fn brain_check_runs_the_brain_library() {
    let out = asm(&isolated(), &["brain", "--check"]);
    assert_eq!(out.status.code(), Some(0), "{}", String::from_utf8_lossy(&out.stderr));
}

#[test]
fn tests_never_see_the_real_setup() {
    let env = isolated();
    let cmd = env.cmd();
    let var = |name: &str| cmd.get_envs().find(|(k, _)| *k == name).and_then(|(_, v)| v).map(|v| v.to_owned());
    assert_eq!(var("ASM_SESSION").unwrap(), "test");
    let home = var("HOME").unwrap();
    assert_ne!(Some(&home), std::env::var_os("HOME").as_ref());
    assert!(std::path::Path::new(&home).starts_with(&env.root));
    // Nothing else leaks in from the test runner's environment.
    let names: Vec<_> = cmd.get_envs().map(|(k, _)| k.to_owned()).collect();
    assert!(
        names
            .iter()
            .all(|k| k == "PATH" || k == "HOME" || k == "ASM_SESSION" || k.to_str().unwrap().starts_with("XDG_"))
    );
}
