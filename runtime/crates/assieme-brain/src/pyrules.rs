//! Python's reading rules, where the brain's behaviour depends on them: `datetime.fromisoformat`, `json.loads`,
//! `str.splitlines`, `glob.glob`, `repr()` of a string and truthiness. Proven on `tests/data/pyrules.json`, recorded
//! from Python by `record/pyrules_cases.py`. After the cutover each rule is kept on purpose or replaced by Rust's own.
use jiff::civil::{Date, ISOWeekDate, Weekday};
use jiff::{SignedDuration, Timestamp, tz::Offset};
use serde_json::Value;
use std::path::{Path, PathBuf};

/// Python's `bool(value)` for a JSON value.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

// --- datetime.fromisoformat ---------------------------------------------------------------------------------------

/// `datetime.fromisoformat(s)` (Python 3.11 and later), a time with no zone read as UTC; None where Python raises.
pub fn fromisoformat(s: &str) -> Option<Timestamp> {
    let c: Vec<char> = s.chars().collect();
    if c.len() < 7 {
        return None;
    }
    let sep = separator(&c)?;
    let (mut y, mut m, mut d) = parse_date(&c[..sep.min(c.len())])?;
    // A separator, even last, means a time follows (CPython's C parser).
    let (h, mi, sec, us, offset) = if sep >= c.len() {
        (0, 0, 0, 0, 0)
    } else {
        let ([h, mi, sec, us], next_day, offset) = parse_time(&c[sep + 1..])?;
        // Midnight at the end of a valid day is the next day's start.
        if next_day && (1..=12).contains(&m) && d <= days_in_month(y, m) {
            d += 1;
            if d > days_in_month(y, m) {
                (d, m) = (1, m + 1);
                if m > 12 {
                    (m, y) = (1, y + 1);
                }
            }
        }
        (h, mi, sec, us, offset)
    };
    if !(1..=9999).contains(&y) || h > 23 || mi > 59 || sec > 59 {
        return None;
    }
    let date = Date::new(y as i16, m as i8, d as i8).ok()?;
    let local = Offset::UTC.to_timestamp(date.at(h as i8, mi as i8, sec as i8, us as i32 * 1000)).ok()?;
    local.checked_sub(SignedDuration::from_micros(offset)).ok()
}

fn digit(c: Option<&char>) -> bool {
    c.is_some_and(char::is_ascii_digit)
}

/// Where the date ends: CPython's `_find_isoformat_datetime_separator`.
fn separator(c: &[char]) -> Option<usize> {
    let n = c.len();
    if n == 7 {
        return Some(7);
    }
    if c[4] == '-' {
        if c[5] != 'W' {
            return Some(10);
        }
        if n > 8 && c[8] == '-' {
            if n == 9 {
                return None;
            }
            return Some(if n > 10 && digit(c.get(10)) { 8 } else { 10 });
        }
        return Some(8);
    }
    if c[4] != 'W' {
        return Some(8);
    }
    let mut idx = 7;
    while idx < n && digit(c.get(idx)) {
        idx += 1;
    }
    Some(if idx < 9 {
        idx
    } else if idx % 2 == 0 {
        7
    } else {
        8
    })
}

/// Exactly `n` ASCII digits.
fn number(c: &[char], from: usize, n: usize) -> Option<u32> {
    let part = c.get(from..from + n)?;
    part.iter().all(char::is_ascii_digit).then(|| part.iter().fold(0, |acc, d| acc * 10 + d.to_digit(10).unwrap()))
}

fn days_in_month(y: u32, m: u32) -> u32 {
    let leap = y.is_multiple_of(4) && (!y.is_multiple_of(100) || y.is_multiple_of(400));
    [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m as usize - 1]
}

fn parse_date(c: &[char]) -> Option<(u32, u32, u32)> {
    if ![7, 8, 10].contains(&c.len()) || !c.iter().all(char::is_ascii) {
        return None;
    }
    let year = number(c, 0, 4)?;
    let has_sep = c[4] == '-';
    let mut pos = 4 + has_sep as usize;
    if c.get(pos) == Some(&'W') {
        pos += 1;
        let week = number(c, pos, 2)?;
        pos += 2;
        let mut day = 1;
        if c.len() > pos {
            if (c[pos] == '-') != has_sep {
                return None;
            }
            pos += has_sep as usize;
            day = number(c, pos, 1)?;
        }
        if !(1..=9999).contains(&year) || !(1..=7).contains(&day) {
            return None;
        }
        let weekday = Weekday::from_monday_one_offset(day as i8).ok()?;
        let date = ISOWeekDate::new(year as i16, week as i8, weekday).ok()?.date();
        return Some((date.year() as u32, date.month() as u32, date.day() as u32)).filter(|d| d.0 >= 1);
    }
    let month = number(c, pos, 2)?;
    pos += 2;
    if (c.get(pos) == Some(&'-')) != has_sep {
        return None;
    }
    pos += has_sep as usize;
    Some((year, month, number(c, pos, 2)?))
}

/// HH[:?MM[:?SS[{.,}fff[fff]]]] -> [hour, minute, second, microsecond]; digits past six are dropped.
fn hh_mm_ss_ff(c: &[char]) -> Option<[u32; 4]> {
    let mut comps = [0; 4];
    let mut pos = 0;
    let mut has_sep = false;
    for (i, comp) in comps.iter_mut().take(3).enumerate() {
        *comp = number(c, pos, 2)?;
        pos += 2;
        let next = c.get(pos);
        if i == 0 {
            has_sep = next == Some(&':');
        }
        if next.is_none() || i >= 2 {
            break;
        }
        if has_sep && next != Some(&':') {
            return None;
        }
        pos += has_sep as usize;
    }
    if pos < c.len() {
        if c[pos] != '.' && c[pos] != ',' {
            return None;
        }
        let rest = &c[pos + 1..];
        if rest.is_empty() || !rest.iter().all(char::is_ascii_digit) {
            return None;
        }
        let take = rest.len().min(6);
        comps[3] = number(rest, 0, take)? * 10u32.pow(6 - take as u32);
    }
    Some(comps)
}

/// (components, became the next day, offset in microseconds east of UTC).
fn parse_time(t: &[char]) -> Option<([u32; 4], bool, i64)> {
    if t.len() < 2 || !t.iter().all(char::is_ascii) {
        return None;
    }
    let find = |ch| t.iter().position(|&x| x == ch).map_or(0, |i| i + 1);
    let tz_pos = [find('-'), find('+'), find('Z')].into_iter().find(|&p| p > 0).unwrap_or(0);
    let mut comps = hh_mm_ss_ff(if tz_pos > 0 { &t[..tz_pos - 1] } else { t })?;
    let mut next_day = false;
    if comps[0] == 24 {
        if comps[1..].iter().any(|&x| x != 0) {
            return None;
        }
        (comps[0], next_day) = (0, true);
    }
    let mut offset = 0;
    if tz_pos == t.len() && t[t.len() - 1] == 'Z' {
    } else if tz_pos > 0 {
        let tz = &t[tz_pos..];
        if [0, 1, 3].contains(&tz.len()) || t[tz_pos - 1] == 'Z' {
            return None;
        }
        let [h, m, s, us] = hh_mm_ss_ff(tz)?;
        let total = ((h as i64 * 60 + m as i64) * 60 + s as i64) * 1_000_000 + us as i64;
        if total >= 86_400_000_000 {
            return None; // Python's timezone() takes less than a day
        }
        offset = if t[tz_pos - 1] == '-' { -total } else { total };
    }
    Some((comps, next_day, offset))
}

// --- json.loads ---------------------------------------------------------------------------------------------------

/// A JSON string holding a value Python's `json.loads` gives but `serde_json` cannot hold: `NaN`, `Infinity`,
/// `-Infinity` (as `<MARK>nan`, `<MARK>inf`, `<MARK>-inf`) and integers past 64 bits (`<MARK>int:<digits>`).
/// U+FDD0 is a noncharacter, never in real text. Read them with [`float`] and [`is_number`].
pub const MARK: char = '\u{FDD0}';

/// `json.loads`: `NaN`, `Infinity` and integers past 64 bits are kept (see [`MARK`]); a float past f64 is infinity.
pub fn json_loads(s: &str) -> Result<Value, String> {
    serde_json::from_str(&mark_python_numbers(s)).map_err(|e| e.to_string())
}

/// `json.loads` of bytes: UTF-8 only.
pub fn json_loads_bytes(line: &[u8]) -> Result<Value, String> {
    json_loads(std::str::from_utf8(line).map_err(|e| e.to_string())?)
}

fn mark_python_numbers(s: &str) -> String {
    let number = regex::Regex::new(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?").unwrap();
    let mut out = String::with_capacity(s.len());
    let (mut i, mut in_string, mut escaped) = (0, false, false);
    let marked = |what: &str| format!("\"{MARK}{what}\"");
    while i < s.len() {
        let rest = &s[i..];
        let ch = rest.chars().next().unwrap();
        if in_string {
            (in_string, escaped) = (escaped || ch != '"', !escaped && ch == '\\');
        } else if ch == '"' {
            in_string = true;
        } else if let Some(word) = ["NaN", "Infinity", "-Infinity"].into_iter().find(|w| rest.starts_with(w)) {
            out.push_str(&marked(match word {
                "NaN" => "nan",
                "Infinity" => "inf",
                _ => "-inf",
            }));
            i += word.len();
            continue;
        } else if let Some(m) = number.find(rest).filter(|m| !m.is_empty() && (ch == '-' || ch.is_ascii_digit())) {
            let token = m.as_str();
            let integer = !token.contains(['.', 'e', 'E']);
            let big = integer && token.parse::<i64>().is_err() && token.parse::<u64>().is_err();
            let float = (!integer).then(|| token.parse::<f64>().unwrap_or(0.0)).filter(|f| f.is_infinite());
            match (big, float) {
                _ if token == "-0" => out.push('0'), // Python's int, not a negative zero
                (true, _) => out.push_str(&marked(&format!("int:{token}"))),
                (_, Some(f)) => out.push_str(&marked(if f > 0.0 { "inf" } else { "-inf" })),
                _ => out.push_str(token),
            }
            i += token.len();
            continue;
        }
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

/// Python's `isinstance(v, (int, float)) and not isinstance(v, bool)` for a value from [`json_loads`].
pub fn is_number(v: &Value) -> bool {
    v.is_number() || float(v).is_some()
}

/// The number a value from [`json_loads`] holds, as a float (NaN and the infinities included).
pub fn float(v: &Value) -> Option<f64> {
    if let Value::Number(n) = v {
        return n.as_f64();
    }
    match v.as_str()?.strip_prefix(MARK)? {
        "nan" => Some(f64::NAN),
        "inf" => Some(f64::INFINITY),
        "-inf" => Some(f64::NEG_INFINITY),
        other => other.strip_prefix("int:")?.parse().ok(),
    }
}

// --- str.splitlines -----------------------------------------------------------------------------------------------

/// `str.splitlines()`: lines end at \n, \r, \r\n, \v, \f, \x1c, \x1d, \x1e, \x85, U+2028 and U+2029.
pub fn splitlines(s: &str) -> Vec<&str> {
    let mut out = vec![];
    let (mut start, mut chars) = (0, s.char_indices().peekable());
    while let Some((i, c)) = chars.next() {
        if matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            out.push(&s[start..i]);
            start = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|&(_, n)| n == '\n') {
                chars.next();
                start += 1;
            }
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

// --- str.split ----------------------------------------------------------------------------------------------------

/// `str.isspace` for one character: Unicode whitespace, and the separators \x1c to \x1f.
pub fn isspace(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// `str.split()` with no separator.
pub fn split(s: &str) -> Vec<&str> {
    s.split(isspace).filter(|w| !w.is_empty()).collect()
}

// --- glob.glob ----------------------------------------------------------------------------------------------------

/// `glob.glob(pattern)`: a wildcard never matches a name starting with a dot, `**` is `*`. Sorted: Python's own
/// order is the folder's, which the brain never relies on (it sorts, or takes the newest by time).
pub fn glob(pattern: &Path) -> Vec<PathBuf> {
    let Some(text) = pattern.to_str() else { return vec![] };
    let text = text.replace("**", "*");
    // The crate's require_literal_leading_dot also hides `.x` from a `.*` pattern, so the dot rule is applied below.
    let options = glob::MatchOptions {
        case_sensitive: true,
        require_literal_separator: true,
        require_literal_leading_dot: false,
    };
    let trailing = text.ends_with('/') && text.len() > 1;
    let Ok(paths) = glob::glob_with(text.trim_end_matches('/'), options) else { return vec![] };
    let parts: Vec<&str> = text.trim_end_matches('/').split('/').collect();
    // Python lists a name starting with a dot only for a pattern part starting with one, and never `.` or `..`.
    let listed = |p: &PathBuf| {
        let got = p.to_str().unwrap_or_default();
        got.split('/')
            .zip(&parts)
            .all(|(g, want)| g == *want || !(g == "." || g == ".." || (g.starts_with('.') && !want.starts_with('.'))))
    };
    let mut out: Vec<PathBuf> = paths
        .flatten()
        .filter(listed)
        .filter(|p| !trailing || p.is_dir())
        .map(|p| if trailing { PathBuf::from(format!("{}/", p.display())) } else { p })
        .collect();
    out.sort();
    out
}

// --- fnmatch.fnmatchcase -----------------------------------------------------------------------------------------

/// `fnmatch.fnmatchcase(name, pattern)`: `*` crosses `/`, no escapes, no braces, `[!x]` negates.
pub fn fnmatchcase(name: &str, pattern: &str) -> bool {
    regex::Regex::new(&fnmatch_regex(pattern)).is_ok_and(|re| re.is_match(name))
}

/// CPython's `fnmatch._translate`, written for Rust's regex syntax.
fn fnmatch_regex(pat: &str) -> String {
    let p: Vec<char> = pat.chars().collect();
    let n = p.len();
    let mut out = String::from("(?s)^");
    let lit = |c: char| if c.is_ascii_punctuation() { format!("\\{c}") } else { c.to_string() };
    let mut i = 0;
    while i < n {
        let c = p[i];
        i += 1;
        match c {
            '*' => {
                while i < n && p[i] == '*' {
                    i += 1;
                }
                out.push_str(".*");
            }
            '?' => out.push('.'),
            '[' => {
                let mut j = i;
                if j < n && p[j] == '!' {
                    j += 1;
                }
                if j < n && p[j] == ']' {
                    j += 1;
                }
                while j < n && p[j] != ']' {
                    j += 1;
                }
                if j >= n {
                    out.push_str("\\[");
                    continue;
                }
                // Python's chunks: the class split at range hyphens, with empty ranges removed.
                let mut chunks: Vec<Vec<char>> = vec![];
                if !p[i..j].contains(&'-') {
                    chunks.push(p[i..j].to_vec());
                } else {
                    let mut k = if p[i] == '!' { i + 2 } else { i + 1 };
                    while let Some(at) = (k < j).then(|| p[k..j].iter().position(|&x| x == '-')).flatten() {
                        let at = k + at;
                        chunks.push(p[i..at].to_vec());
                        i = at + 1;
                        k = at + 3;
                    }
                    let chunk = p[i..j].to_vec();
                    if chunk.is_empty() {
                        chunks.last_mut().unwrap().push('-');
                    } else {
                        chunks.push(chunk);
                    }
                    for k in (1..chunks.len()).rev() {
                        if chunks[k - 1].last() > chunks[k].first() {
                            let rest = chunks.remove(k);
                            chunks[k - 1].pop();
                            chunks[k - 1].extend(&rest[1..]);
                        }
                    }
                }
                i = j + 1;
                let body: Vec<char> = chunks.join(&'\u{0}');
                if body.is_empty() {
                    out.push_str("[^\\s\\S]"); // never matches
                } else if body == ['!'] {
                    out.push('.');
                } else {
                    let negate = body[0] == '!';
                    out.push('[');
                    if negate {
                        out.push('^');
                    }
                    for &c in &body[negate as usize..] {
                        if c == '\u{0}' { out.push('-') } else { out.push_str(&lit(c)) }
                    }
                    out.push(']');
                }
            }
            c => out.push_str(&lit(c)),
        }
    }
    out.push('$');
    out
}

// --- repr ---------------------------------------------------------------------------------------------------------

/// Python's `repr()` of a scalar JSON value (containers fall back to JSON).
pub fn repr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => repr_str(s),
        other => other.to_string(),
    }
}

/// Python's `repr()` of a `str`.
pub fn repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if printable(c) => out.push(c),
            c if (c as u32) < 0x100 => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) < 0x10000 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push_str(&format!("\\U{:08x}", c as u32)),
        }
    }
    out.push(quote);
    out
}

/// `str.isprintable` for one character: not a control, a separator other than space, a format or private-use
/// character. shortcut: unassigned code points count as printable (no Unicode tables here); upgrade if ids or
/// messages ever carry them.
fn printable(c: char) -> bool {
    c == ' '
        || !(c.is_control()
            || c.is_whitespace()
            || matches!(c as u32, 0xad | 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x180e | 0x200b..=0x200f
                | 0x202a..=0x202e | 0x2060..=0x2064 | 0x2066..=0x206f | 0xfeff | 0xfff9..=0xfffb
                | 0xe000..=0xf8ff | 0xf0000..=0x10ffff))
}
