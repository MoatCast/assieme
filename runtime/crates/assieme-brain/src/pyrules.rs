//! Python's reading rules, where the brain's behaviour depends on them.
use serde_json::Value;

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

/// `json.loads` of a request line given as bytes.
pub fn json_loads_bytes(line: &[u8]) -> Result<Value, String> {
    serde_json::from_slice(line).map_err(|e| e.to_string())
}

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

/// Python's `str.isprintable` for one character: not a control, separator other than space, or format character.
fn printable(c: char) -> bool {
    c == ' '
        || !(c.is_control()
            || c.is_whitespace()
            || matches!(c as u32, 0xad | 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x180e | 0x200b..=0x200f
                | 0x202a..=0x202e | 0x2060..=0x2064 | 0x2066..=0x206f | 0xfeff | 0xfff9..=0xfffb
                | 0xd800..=0xdfff | 0xe000..=0xf8ff | 0xf0000..=0x10ffff))
}
