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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rule_by_place() {
        let kinds = [Some("planner"), Some("builder"), Some("builder"), Some("builder"), Some("builder"), Some("reviewer"), Some("tester"), None, Some("odd")];
        let got: Vec<_> = rules(&kinds).into_iter().map(|(i, _)| i).collect();
        assert_eq!(got, ["piano", "violin", "cello", "bass", "violin", "reviewer", "timpani", "flute", "clarinet"]);
        assert_eq!(rules(&kinds)[3].1, "the third builder");
        assert_eq!(rules(&kinds)[7].1, "a role of your own");
    }

    #[test]
    fn saved_instrument_wins_only_when_known() {
        let got = pick(&[(Some("builder"), Some("trumpet")), (Some("builder"), Some("kazoo")), (None, None)]);
        assert_eq!(got, ["trumpet", "cello", "flute"]);
    }

    #[test]
    fn many_builders_get_a_numbered_reason() {
        let kinds = vec![Some("builder"); 14];
        assert_eq!(rules(&kinds)[12].1, "builder 13");
    }

    #[test]
    fn second_colours_from_the_fourth_builder() {
        let kinds = [Some("builder"), Some("builder"), Some("builder"), Some("planner"), Some("builder")];
        assert_eq!([0, 3, 4].map(|i| palette(&kinds, i)), [0, 0, 1]);
    }
}
