//! Port of `assieme/report.py`, owned by session A (see OWNERS.md).
//!
//! Sessions -> per-agent totals, the wake graph, refusals and flags; rendered as Markdown or JSON.
// Rendering (`to_md`, `to_json`) belongs to `assieme report`, which is not ported.
#![allow(clippy::todo, unused_variables)]
use crate::model::HUMAN;
use crate::prelude::*;
use serde_json::json;

pub const EMPTY_CALLS: i64 = 2; // a wake the receiver answers in at most this many calls did no work
pub const EMPTY_WAKES_PER_EDGE: i64 = 20; // flag a sender -> receiver pair with more empty wakes than this
pub const REPEATED_REFUSALS: i64 = 3; // flag a command refused at least this often for one agent
pub const PEAK_CONTEXT: i64 = 250_000; // flag a context above this: compaction is not keeping up
pub const NOISY_LAST_HOUR: i64 = 10; // flag an edge with at least this many wakes in the last hour...
pub const NOISY_FACTOR: f64 = 3.0; // ...and this many times its average hourly rate
pub const SEND_LESS: usize = 3; // how many edges the "send less" list names
pub const SEND_LESS_MIN_SHARE: f64 = 0.01; // ...each worth at least this share of all input tokens

#[derive(Default, serde::Serialize)]
struct Totals {
    sessions: i64,
    calls: i64,
    tokens: i64,
    output: i64,
    peak: i64,
    sources: BTreeSet<String>,
    #[serde(flatten)]
    found: Map<String, Value>, // detect's columns
}

#[derive(Default, serde::Serialize)]
struct Edge {
    from: String,
    to: String,
    wakes: i64,
    empty: i64,
    tokens: i64,
    empty_tokens: i64,
    calls: i64,
    last_hour: i64,
    channels: BTreeSet<String>,
    sample: String,
    per_hour: Option<f64>,
}

/// Sessions -> per-agent totals, the wake graph, refusals and flags. now/hours describe the window; with them,
/// edges also get hourly rates and a last-hour count. `boxes` and `machine` are the F10 metrics and F6 samples.
pub fn aggregate(
    sessions: &[Session],
    now: Option<Timestamp>,
    hours: Option<f64>,
    boxes: &[Value],
    machine: &[Value],
) -> Value {
    use crate::detect::slot;
    let mut agents: Vec<(&str, Totals)> = vec![];
    let mut edges: Vec<((&str, &str), Edge)> = vec![];
    let hour_ago = now.map(|n| n - jiff::SignedDuration::from_hours(1));
    let mut refusals: crate::model::Counter<(&str, &str, &str)> = Default::default();
    let mut notes: BTreeMap<&str, &str> = BTreeMap::new();
    let hours = hours.filter(|h| *h != 0.0);
    for s in sessions {
        let a = slot(&mut agents, &s.agent);
        a.sessions += 1;
        a.sources.insert(s.source.clone());
        (a.calls, a.tokens, a.output, a.peak) =
            (a.calls + s.calls, a.tokens + s.tokens, a.output + s.output, a.peak.max(s.peak));
        for t in &s.turns {
            let e = slot(&mut edges, (&t.sender, &s.agent));
            let empty = t.calls <= EMPTY_CALLS;
            (e.from, e.to, e.wakes, e.calls) = (t.sender.clone(), s.agent.clone(), e.wakes + 1, e.calls + t.calls);
            (e.empty, e.tokens) = (e.empty + empty as i64, e.tokens + t.tokens);
            e.empty_tokens += if empty { t.tokens } else { 0 }; // what the receiver re-read for nothing
            e.last_hour += matches!((hour_ago, t.ts, now), (Some(h), Some(ts), Some(n)) if h <= ts && ts <= n) as i64;
            e.channels.insert(t.channel.clone());
            if !t.text.is_empty() {
                e.sample = t.text.clone();
            }
            e.per_hour = hours.map(|h| (e.wakes as f64 / h * 100.0).round() / 100.0);
        }
        for ((cmd, why), n) in &s.refusals.0 {
            refusals.add((&s.agent, cmd, why), *n);
        }
        notes.extend(s.rule_notes.iter().map(|(k, v)| (k.as_str(), v.as_str())));
    }

    let mut flags = vec![];
    for (name, a) in agents.iter().filter(|(_, a)| a.peak > PEAK_CONTEXT) {
        flags.push(format!("{name}: context peaked at {}K tokens; compact or restart it", a.peak / 1000));
    }
    let routine = |e: &&Edge| e.from != HUMAN;
    for e in edges.iter().map(|x| &x.1).filter(routine).filter(|e| e.empty > EMPTY_WAKES_PER_EDGE) {
        flags.push(format!(
            "{} -> {}: {} of {} wakes needed <= {EMPTY_CALLS} calls ({:.1}M tokens on all of them); \
            stop the routine message or batch it",
            e.from,
            e.to,
            e.empty,
            e.wakes,
            e.tokens as f64 / 1e6
        ));
    }
    for ((agent, cmd, why), n) in refusals.0.iter().filter(|r| r.1 >= REPEATED_REFUSALS) {
        let fix = notes.get(why).filter(|f| !f.is_empty()).unwrap_or(&"fix the rule or the brief instead of retrying");
        flags.push(format!("{agent}: '{cmd}' refused {n} times by {why}; {fix}"));
    }
    for e in edges.iter().map(|x| &x.1).filter(routine) {
        if let Some(rate) =
            e.per_hour.filter(|r| e.last_hour >= NOISY_LAST_HOUR && e.last_hour as f64 >= NOISY_FACTOR * r)
        {
            flags.push(format!(
                "{} -> {}: {} wakes in the last hour, {:.0}x its usual rate; it just got noisy",
                e.from,
                e.to,
                e.last_hour,
                e.last_hour as f64 / rate.max(0.01)
            ));
        }
    }
    let total = agents.iter().map(|(_, a)| a.tokens).sum::<i64>() as f64;
    let mut send_less: Vec<&Edge> = edges
        .iter()
        .map(|x| &x.1)
        .filter(routine)
        .filter(|e| e.empty_tokens as f64 >= SEND_LESS_MIN_SHARE * total && e.empty_tokens != 0)
        .collect();
    send_less.sort_by_key(|e| std::cmp::Reverse(e.empty_tokens));
    let send_less: Vec<Value> = send_less
        .iter()
        .take(SEND_LESS)
        .map(|e| {
            let share = if total > 0.0 { (e.empty_tokens as f64 / total * 1e4).round() / 1e4 } else { 0.0 };
            let text = format!(
                "{} -> {}: {} of {} wakes did no work and cost {:.1}M tokens ({:.0}% of all input); batch \
            these notes or stop sending them",
                e.from,
                e.to,
                e.empty,
                e.wakes,
                e.empty_tokens as f64 / 1e6,
                100.0 * e.empty_tokens as f64 / total.max(1.0)
            );
            json!({"from": e.from, "to": e.to, "empty": e.empty, "wakes": e.wakes, "empty_tokens": e.empty_tokens,
               "share": share, "text": text})
        })
        .collect();
    let mut found = crate::detect::analyse(sessions, now, boxes, machine);
    for (name, a) in agents.iter_mut() {
        a.found = found["agents"][*name].as_object().cloned().unwrap_or_default();
    }
    let refusals: Vec<Value> = refusals.most_common().into_iter().map(|((a, c, w), n)| {
        json!({"agent": a, "command": c, "why": w, "advice": notes.get(w).unwrap_or(&""), "count": n})
    }).collect();
    json!({"agents": agents.into_iter().collect::<BTreeMap<_, _>>(), "findings": found["findings"].take(),
           "detectors": found["detectors"].take(), "edges": edges.iter().map(|x| &x.1).collect::<Vec<_>>(),
           "send_less": send_less, "empty_tokens": edges.iter().map(|x| &x.1).filter(routine).map(|e| e.empty_tokens).sum::<i64>(),
           "refusals": refusals, "flags": flags})
}

pub fn to_json(r: &Value, meta: &Value) -> String {
    todo!("report.to_json")
}

pub fn to_md(r: &Value, meta: &Value, show_text: bool) -> String {
    todo!("report.to_md")
}
