//! `asm cli check-configs`: agent settings that still run the Python brain, which the app no longer carries.
use crate::prelude::*;

/// What the Python brain's entries contain: `python3 -P -m assieme …`, a path inside the app's `Resources/python`, or
/// the `uv tool install` of the command line (whose arguments a JSON list keeps apart, so `-m assieme` never matches).
const OLD: [&str; 3] = ["-m assieme", "Resources/python", "uv/tools/assieme"];

/// The files among `files` (team, path) that hold an old entry.
pub fn stale(files: impl IntoIterator<Item = (String, PathBuf)>) -> Vec<(String, PathBuf)> {
    let old = |(_, path): &(String, PathBuf)| {
        std::fs::read_to_string(path).is_ok_and(|text| OLD.iter().any(|needle| text.contains(needle)))
    };
    files.into_iter().filter(old).collect()
}

/// Every settings file Assieme or the agents keep: the person's own, the assistants' plugins, and each team's copies.
pub fn candidates(home: &Path, state: &Path, teams: &Path) -> Vec<(String, PathBuf)> {
    let mut files: Vec<(String, PathBuf)> = [
        ".claude/settings.json",
        ".claude.json",
        ".codex/config.toml",
        ".codex/hooks.json",
        "Library/Application Support/Claude/claude_desktop_config.json",
    ]
    .iter()
    .map(|f| ("(all teams)".to_string(), home.join(f)))
    .collect();
    for dir in ["assistants-plugin", "agy-plugin", "gemini"] {
        let pattern = state.join(dir).join("**/*").to_string_lossy().into_owned();
        files.extend(glob::glob(&pattern).into_iter().flatten().flatten().map(|p| ("(all teams)".to_string(), p)));
    }
    for entry in std::fs::read_dir(teams).into_iter().flatten().flatten() {
        let team = entry.file_name().to_string_lossy().into_owned();
        let toml = std::fs::read_to_string(entry.path().join("team.toml")).unwrap_or_default();
        let Some(project) = toml.parse::<toml::Table>().ok().and_then(|t| t.get("project")?.as_str().map(String::from))
        else {
            continue;
        };
        let copies = Path::new(&project).join(".assieme/worktrees");
        for copy in std::fs::read_dir(copies).into_iter().flatten().flatten() {
            for f in [
                ".claude/settings.local.json",
                ".codex/config.toml",
                ".gemini/settings.json",
                ".cursor/hooks.json",
                ".cursor/mcp.json",
                ".agents/hooks.json",
            ] {
                files.push((team.clone(), copy.path().join(f)));
            }
        }
    }
    files
}

/// Prints each team and file with an old entry; 1 when there is any.
pub fn main() -> i32 {
    let (home, state) = (PathBuf::from(crate::rules::home()), crate::quiet::state_dir());
    let found = stale(candidates(&home, &state, &state.join("teams")));
    for (team, path) in &found {
        println!("{team}\t{}", path.display());
    }
    i32::from(!found.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_check_finds_python_entries() {
        let dir = std::env::temp_dir().join(format!("asm-configcheck-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (clean, old) = (dir.join("clean.json"), dir.join("old.json"));
        std::fs::write(&clean, r#"{"hooks": {"Stop": [{"command": "/A/asm hook idle"}]}}"#).unwrap();
        std::fs::write(
            &old,
            r#"{"hooks": {"Stop": [{"command": "/A/Resources/python/bin/python3 -P -m assieme.hook idle"}]}}"#,
        )
        .unwrap();
        // The Claude app keeps the command and its arguments apart, as a list
        let app = dir.join("claude_desktop_config.json");
        std::fs::write(
            &app,
            r#"{"mcpServers": {"assieme": {"command": "/U/.local/share/uv/tools/assieme/bin/python", "args": ["-P", "-m", "assieme", "mcp"]}}}"#,
        )
        .unwrap();
        let files = [
            ("a".to_string(), clean),
            ("b".to_string(), old.clone()),
            ("c".to_string(), dir.join("missing")),
            ("d".to_string(), app.clone()),
        ];
        assert_eq!(stale(files), [("b".to_string(), old), ("d".to_string(), app)]);
        let _ = std::fs::remove_dir_all(dir);
    }
}
