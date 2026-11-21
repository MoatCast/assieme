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

#[derive(Default)]
struct Totals {
    sessions: i64,
    calls: i64,
    tokens: i64,
    output: i64,
    peak: i64,
    sources: BTreeSet<String>,
}

#[derive(Default)]
struct Edge {
    wakes: i64,
    empty: i64,
    tokens: i64,
    empty_tokens: i64,
    calls: i64,
    last_hour: i64,
    channels: BTreeSet<String>,
    sample: String,
}

/// Python's `round(x, n)`, near enough for rates and shares.
fn round_to(x: f64, places: i32) -> f64 {
    let k = 10f64.powi(places);
    (x * k).round() / k
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
    let mut agents: Vec<(String, Totals)> = vec![];
    let mut edges: Vec<((String, String), Edge)> = vec![];
    let hour_ago = now.map(|n| n - jiff::SignedDuration::from_hours(1));
    let mut refusals: crate::model::Counter<(String, String, String)> = Default::default();
    let mut notes: BTreeMap<String, String> = BTreeMap::new();
    for s in sessions {
        let i = match agents.iter().position(|(n, _)| *n == s.agent) {
            Some(i) => i,
            None => {
                agents.push((s.agent.clone(), Totals::default()));
                agents.len() - 1
            }
        };
        let a = &mut agents[i].1;
        a.sessions += 1;
        a.sources.insert(s.source.clone());
        a.calls += s.calls;
        a.tokens += s.tokens;
        a.output += s.output;
        a.peak = a.peak.max(s.peak);
        for t in &s.turns {
            let key = (t.sender.clone(), s.agent.clone());
            let i = match edges.iter().position(|(k, _)| *k == key) {
                Some(i) => i,
                None => {
                    edges.push((key, Edge::default()));
                    edges.len() - 1
                }
            };
            let e = &mut edges[i].1;
            let empty = t.calls <= EMPTY_CALLS;
            e.wakes += 1;
            e.empty += empty as i64;
            e.tokens += t.tokens;
            e.empty_tokens += if empty { t.tokens } else { 0 }; // what the receiver re-read for nothing
            e.last_hour += matches!((hour_ago, t.ts, now), (Some(h), Some(ts), Some(n)) if h <= ts && ts <= n) as i64;
            e.calls += t.calls;
            e.channels.insert(t.channel.clone());
            if !t.text.is_empty() {
                e.sample = t.text.clone();
            }
        }
        for ((cmd, why), n) in &s.refusals.0 {
            refusals.add((s.agent.clone(), cmd.clone(), why.clone()), *n);
        }
        notes.extend(s.rule_notes.clone());
    }

    let mut flags = vec![];
    for (name, a) in &agents {
        if a.peak > PEAK_CONTEXT {
            flags.push(format!("{name}: context peaked at {}K tokens; compact or restart it", a.peak / 1000));
        }
    }
    for ((sender, receiver), e) in &edges {
        if sender != HUMAN && e.empty > EMPTY_WAKES_PER_EDGE {
            flags.push(format!(
                "{sender} -> {receiver}: {} of {} wakes needed <= {EMPTY_CALLS} calls ({:.1}M tokens on all of them); \
                 stop the routine message or batch it",
                e.empty,
                e.wakes,
                e.tokens as f64 / 1e6
            ));
        }
    }
    for ((agent, cmd, why), n) in &refusals.0 {
        if *n >= REPEATED_REFUSALS {
            let fix =
                notes.get(why).filter(|f| !f.is_empty()).map_or("fix the rule or the brief instead of retrying", |f| f);
            flags.push(format!("{agent}: '{cmd}' refused {n} times by {why}; {fix}"));
        }
    }
    let hours = hours.filter(|h| *h != 0.0);
    let per_hour = |e: &Edge| hours.map(|h| round_to(e.wakes as f64 / h, 2));
    for ((sender, receiver), e) in &edges {
        if let Some(rate) = per_hour(e)
            && sender != HUMAN
            && e.last_hour >= NOISY_LAST_HOUR
            && e.last_hour as f64 >= NOISY_FACTOR * rate
        {
            flags.push(format!(
                "{sender} -> {receiver}: {} wakes in the last hour, {:.0}x its usual rate; it just got noisy",
                e.last_hour,
                e.last_hour as f64 / rate.max(0.01)
            ));
        }
    }
    let total_tokens: i64 = agents.iter().map(|(_, a)| a.tokens).sum();
    let mut send_less: Vec<&((String, String), Edge)> = edges
        .iter()
        .filter(|((f, _), e)| {
            f != HUMAN && e.empty_tokens as f64 >= SEND_LESS_MIN_SHARE * total_tokens as f64 && e.empty_tokens != 0
        })
        .collect();
    send_less.sort_by_key(|(_, e)| std::cmp::Reverse(e.empty_tokens));
    send_less.truncate(SEND_LESS);
    let found = crate::detect::analyse(sessions, now, boxes, machine);

    let agents_out: Map<String, Value> = agents
        .iter()
        .map(|(name, a)| {
            let mut v = json!({"sessions": a.sessions, "calls": a.calls, "tokens": a.tokens, "output": a.output,
                               "peak": a.peak, "sources": a.sources});
            if let (Some(o), Some(Value::Object(more))) = (v.as_object_mut(), found["agents"].get(name)) {
                o.extend(more.clone());
            }
            (name.clone(), v)
        })
        .collect();
    let edges_out: Vec<Value> = edges
        .iter()
        .map(|((f, t), e)| {
            json!({"from": f, "to": t, "wakes": e.wakes, "empty": e.empty, "tokens": e.tokens,
                   "empty_tokens": e.empty_tokens, "calls": e.calls, "last_hour": e.last_hour,
                   "channels": e.channels, "sample": e.sample, "per_hour": per_hour(e)})
        })
        .collect();
    let send_less: Vec<Value> = send_less
        .iter()
        .map(|((f, t), e)| {
            let share = if total_tokens > 0 { round_to(e.empty_tokens as f64 / total_tokens as f64, 4) } else { 0.0 };
            let text = format!(
                "{f} -> {t}: {} of {} wakes did no work and cost {:.1}M tokens ({:.0}% of all input); batch these \
                 notes or stop sending them",
                e.empty,
                e.wakes,
                e.empty_tokens as f64 / 1e6,
                100.0 * e.empty_tokens as f64 / total_tokens.max(1) as f64
            );
            json!({"from": f, "to": t, "empty": e.empty, "wakes": e.wakes, "empty_tokens": e.empty_tokens,
                   "share": share, "text": text})
        })
        .collect();
    let empty_tokens: i64 = edges.iter().filter(|((f, _), _)| f != HUMAN).map(|(_, e)| e.empty_tokens).sum();
    let refusals: Vec<Value> = refusals
        .most_common()
        .into_iter()
        .map(|((a, c, w), n)| {
            json!({"agent": a, "command": c, "why": w, "advice": notes.get(w).cloned().unwrap_or_default(), "count": n})
        })
        .collect();
    json!({"agents": agents_out, "findings": found["findings"], "detectors": found["detectors"],
           "edges": edges_out, "send_less": send_less, "empty_tokens": empty_tokens, "refusals": refusals,
           "flags": flags})
}

pub fn to_json(r: &Value, meta: &Value) -> String {
    todo!("report.to_json")
}

pub fn to_md(r: &Value, meta: &Value, show_text: bool) -> String {
    todo!("report.to_md")
}
