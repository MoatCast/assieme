//! Port of `assieme/prompts.py`, owned by session B (see OWNERS.md).
//!
//! Prompts and questions an agent shows, recognised from its screen text.
//!
//! Claude Code's folder-trust prompt defaults to "No, exit", so it is never answered with a bare Enter.
//! Codex's hook review is never answered by Assieme: the user reviews hooks themselves.
use crate::prelude::*;
use crate::pyrules::{isspace, splitlines};
use regex::Regex;

const META: [&str; 2] = ["Type something.", "Chat about this"]; // Claude's own extras: Reply covers them
const LABEL_MAX: usize = 40;
const ALWAYS_LABEL: &str = "Always allow for this agent";

static ITEM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\s*([❯›>]\s*)?(\d+)\.\s+(\S.*?)\s*$").unwrap());
static ALWAYS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^Yes, (and )?(don.t ask again|allow all|always)").unwrap());
static AGENT_TEXT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[⏺•]\s?(.*)$").unwrap());
static BOX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\s*([❯›]|─{3,})").unwrap());

fn strip(s: &str) -> &str {
    s.trim_matches(isspace)
}

/// The keys that answer "yes" to a folder-trust prompt on screen, or None.
pub fn trust_keys(screen: &str) -> Option<Vec<&'static str>> {
    if screen.contains("Yes, I trust this folder") {
        Some(vec!["down", "enter"])
    } else if screen.contains("Trust and continue") {
        Some(vec!["enter"])
    } else {
        None
    }
}

/// ["enter"] (keep the default) on one of Claude Code's and Antigravity's first-run screens that only choose a look
/// or say what to be careful about, else None. Never the sign-in, which stays the person's. Compared without
/// spaces, since a screen read can lose them.
pub fn first_run_keys(screen: &str) -> Option<Vec<&'static str>> {
    let flat: String = screen.chars().filter(|c| !isspace(*c)).collect();
    let has = |s: &str| flat.contains(s);
    (has("Choosethetextstylethatlooksbestwithyourterminal")
        || has("Chooseyourcolorscheme:")
        || (has("Securitynotes:") && has("PressEntertocontinue")))
    .then(|| vec!["enter"])
}

pub fn hook_review(screen: &str) -> bool {
    screen.contains("Hooks need review")
}

/// The last 40 lines that matter: runs of blank lines squeezed to one (a tall screen is mostly blank).
fn tail(screen: &str) -> Vec<&str> {
    let mut out: Vec<&str> = vec![];
    for line in splitlines(screen) {
        if !strip(line).is_empty() || out.last().is_some_and(|l| !strip(l).is_empty()) {
            out.push(line);
        }
    }
    out.split_off(out.len().saturating_sub(40))
}

/// (items, index of item 1, lines) of the last numbered block on screen; no items when there is none.
fn block(screen: &str) -> (Vec<(u32, String)>, usize, Vec<&str>) {
    let lines = tail(screen);
    let (mut found, mut pointer, mut first): (Vec<(u32, String)>, bool, Option<usize>) = (vec![], false, None);
    for i in (0..lines.len()).rev() {
        let Some(m) = ITEM.captures(lines[i]) else { continue };
        let n: u32 = m[2].parse().unwrap_or(u32::MAX);
        if found.last().is_some_and(|l| n + 1 != l.0) {
            break;
        }
        found.push((n, m[3].to_string()));
        pointer |= m.get(1).is_some();
        if n == 1 {
            first = Some(i);
            break;
        }
    }
    // A real menu draws its selection pointer; an agent's own numbered list in its reply has none.
    match first {
        Some(first) if pointer && (2..=9).contains(&found.len()) => {
            found.reverse();
            (found, first, lines)
        }
        _ => (vec![], 0, lines),
    }
}

/// The numbered menu at the bottom of the screen as [(n, label)], or [] when there is none: only the last block
/// counts, it must show the selection pointer, and there are 2 to 9 items numbered from 1.
pub fn menu(screen: &str) -> Vec<(u32, String)> {
    let items: Vec<_> = block(screen)
        .0
        .into_iter()
        .filter(|(_, l)| !META.contains(&l.as_str()))
        .map(|(n, l)| match l.chars().count() > LABEL_MAX {
            true => (n, l.chars().take(LABEL_MAX - 1).chain(['…']).collect()),
            false => (n, l),
        })
        .collect();
    if items.len() >= 2 { items } else { vec![] }
}

/// An agent's permission menu in Assieme's words: Allow once, Always allow for this agent, Block; {} for other menus.
pub fn permission_labels(items: &[(u32, String)]) -> BTreeMap<u32, &'static str> {
    let yes_first = items.first().is_some_and(|i| i.1.starts_with("Yes"));
    if !yes_first || !items[1..].iter().any(|i| i.1.starts_with("No")) {
        return BTreeMap::new();
    }
    let mut out = BTreeMap::from([(items[0].0, "Allow once")]);
    for (n, label) in &items[1..] {
        if ALWAYS.is_match(label) && !out.values().any(|v| *v == ALWAYS_LABEL) {
            out.insert(*n, ALWAYS_LABEL);
        } else if label.starts_with("No") && !out.values().any(|v| *v == "Block") {
            out.insert(*n, "Block");
        }
    }
    out
}

/// The text just above the menu (the question, the tool line): what the menu is about.
pub fn menu_context(screen: &str) -> String {
    let (found, first, lines) = block(screen);
    if found.is_empty() {
        return String::new();
    }
    let above: Vec<&str> = lines[..first]
        .iter()
        .map(|l| strip(l))
        .filter(|l| !l.is_empty() && !l.chars().all(|c| "─╌-".contains(c)))
        .collect();
    above[above.len().saturating_sub(8)..].join("\n")
}

/// The agent's last message, when its last paragraph is a question, as one line.
pub fn question(screen: &str) -> Option<String> {
    let lines = tail(screen);
    let start = lines.iter().rposition(|l| AGENT_TEXT.is_match(l))?;
    let mut body = vec![AGENT_TEXT.captures(lines[start])?[1].to_string()];
    for l in &lines[start + 1..] {
        // The message's own lines are indented; the CLI's status lines ("✻ Brewed for 3s") and the input box aren't.
        if BOX.is_match(l) || (!strip(l).is_empty() && !l.starts_with(isspace)) {
            break;
        }
        body.push(strip(l).to_string());
    }
    let body = body.join("\n");
    let last =
        body.split("\n\n").filter(|p| !strip(p).is_empty()).last()?.split_whitespace().collect::<Vec<_>>().join(" ");
    last.ends_with('?').then(|| last.chars().take(300).collect())
}
