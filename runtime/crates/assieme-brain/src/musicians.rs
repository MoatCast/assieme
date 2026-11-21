//! Port of `assieme/musicians.py`, owned by session D (see OWNERS.md).
//!
//! Step 31: the instrument each agent plays. Chosen by role when the team is made, then saved with the role, so it never
//! changes when other agents come or go. The pictures are drawn by the app (art/musicians.py).
use crate::prelude::*;

pub const INSTRUMENTS: &[&str] =
    &["piano", "reviewer", "violin", "cello", "bass", "timpani", "flute", "clarinet", "trumpet"];

const STRINGS: [&str; 3] = ["violin", "cello", "bass"]; // builders, in turn
const WINDS: [&str; 3] = ["flute", "clarinet", "trumpet"]; // a role of the person's own, in turn
const ORDINALS: [&str; 12] = [
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh",
    "twelfth",
];

fn by_kind(kind: &str) -> Option<&'static str> {
    match kind {
        "planner" => Some("piano"),
        "reviewer" => Some("reviewer"),
        "tester" => Some("timpani"),
        _ => None,
    }
}

/// What the rule picks for each role by its kind and place in the team, and why, as the app's caption says it
/// ("Bass, chosen for the third builder.").
pub fn rules(kinds: &[Option<&str>]) -> Vec<(&'static str, String)> {
    let (mut builders, mut custom) = (0, 0);
    kinds
        .iter()
        .map(|kind| match *kind {
            Some("builder") => {
                let why = ORDINALS
                    .get(builders)
                    .map_or_else(|| format!("builder {}", builders + 1), |o| format!("the {o} builder"));
                builders += 1;
                (STRINGS[(builders - 1) % 3], why)
            }
            Some(k) if by_kind(k).is_some() => (by_kind(k).unwrap_or("piano"), format!("the {k}")),
            _ => {
                custom += 1;
                (WINDS[(custom - 1) % 3], "a role of your own".to_string())
            }
        })
        .collect()
}

/// Each role's instrument from its saved one (when it is a known one) and its kind.
pub fn pick(roles: &[(Option<&str>, Option<&str>)]) -> Vec<String> {
    let kinds: Vec<_> = roles.iter().map(|(k, _)| *k).collect();
    roles
        .iter()
        .zip(rules(&kinds))
        .map(|((_, saved), (rule, _))| match saved {
            Some(s) if INSTRUMENTS.contains(s) => s.to_string(),
            _ => rule.to_string(),
        })
        .collect()
}

/// Each role's instrument: its saved one when it has a known one, else the rule by its place in the team.
pub fn assign(roles: &[Role]) -> Vec<String> {
    let items: Vec<_> = roles.iter().map(|r| (r.kind.as_deref(), r.musician.as_deref())).collect();
    pick(&items)
}

/// 0, or 1 for the fourth builder on: the strings come round again in the second colours.
pub fn palette(kinds: &[Option<&str>], i: usize) -> u8 {
    if kinds[i] != Some("builder") {
        return 0;
    }
    u8::from(kinds[..i].iter().filter(|k| **k == Some("builder")).count() >= 3)
}
