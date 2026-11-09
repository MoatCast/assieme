// Changed by Assieme: new file. See NOTICE.
//! Delivery control, added by Assieme: routine notes wait in the core for the
//! receiver instead of waking it. Held notes go in as one batch while it works
//! (they join its current turn), or after the hold time if it stays idle: one
//! wake instead of many. The batch stays held until its turn is confirmed, so
//! a lost one is re-sent and a restarted core still has it.
//! Which notes are routine is the caller's policy, not the core's.

use crate::rpc::RpcError;
use crate::runtime::{Core, State, now_ms};
use crate::terminals::Terminal;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::Duration;

/// Tries before a batch is given up, with an event saying so.
const MAX_ATTEMPTS: u32 = 5;

#[derive(Serialize, Deserialize, JsonSchema, Clone, Debug, PartialEq)]
pub struct Held {
    pub id: u64,
    pub text: String,
    pub since_ms: u64,
    #[serde(default)]
    pub attempts: u32,
}

fn env_ms(var: &str, default: u64) -> Duration {
    Duration::from_millis(std::env::var(var).ok().and_then(|v| v.parse().ok()).unwrap_or(default))
}

/// How long a note waits for an idle receiver to start working by itself.
fn hold_for() -> Duration {
    env_ms("ASM_HOLD_MS", 20 * 60 * 1000)
}

/// How long after an unconfirmed batch it is tried again.
fn retry_after() -> Duration {
    env_ms("ASM_RETRY_MS", 30_000)
}

/// Can the batch go in now? A dialog never takes one (typing could answer it).
fn due(t: &Terminal, now: u64) -> bool {
    let state = t.status.as_ref().map(|s| s.state.as_str());
    let oldest = t.held.first().map_or(now, |h| h.since_ms);
    let expired = now.saturating_sub(oldest) >= hold_for().as_millis() as u64;
    state != Some("blocked") && (state == Some("working") || !t.reported || expired)
}

impl Core {
    /// Holds `text` for terminal `id`: the note's id, for its delivery events.
    pub fn hold(&self, id: u32, text: String) -> Result<u64, RpcError> {
        let mut st = self.lock();
        let t = st.reg.terminals.get_mut(&id).filter(|t| t.is_live()).ok_or_else(|| {
            RpcError::new(
                "finished",
                format!("{} has finished", crate::terminals::address(id)),
                format!("asm read {}", crate::terminals::address(id)),
            )
        })?;
        t.held_seq += 1;
        let note = t.held_seq;
        t.held.push(Held { id: note, text, since_ms: now_ms(), attempts: 0 });
        self.save(&st);
        drop(st);
        let _ = self.jobs.send(crate::notes::Job::Held(id));
        self.changed.notify_waiters();
        Ok(note)
    }

    fn delivery_event(&self, st: &mut State, id: u32, result: &str, notes: &[u64]) {
        self.emit(st, "delivery", id, serde_json::json!({"result": result, "notes": notes}));
    }

    /// Ends the running delivery if nothing is left to do. The lock makes this
    /// and `hold` exclusive, so a note held right now starts a new delivery.
    fn delivery_done(&self, st: &mut State, id: u32) -> bool {
        match st.reg.terminals.get_mut(&id) {
            Some(t) if t.is_live() && !t.held.is_empty() => false,
            Some(t) if !t.held.is_empty() => {
                let notes: Vec<u64> = std::mem::take(&mut t.held).iter().map(|h| h.id).collect();
                self.delivery_event(st, id, "gave_up", &notes);
                self.save(st);
                st.held_tasks.remove(&id);
                true
            }
            _ => {
                st.held_tasks.remove(&id);
                true
            }
        }
    }

    /// Delivers what `id` holds, whenever it can take it, until nothing is left.
    pub(crate) async fn deliver_held(self: Arc<Self>, id: u32) {
        loop {
            let ready = |st: &State| {
                st.reg.terminals.get(&id).is_none_or(|t| !t.is_live() || t.held.is_empty() || due(t, now_ms()))
            };
            // The hold running out changes no state, so look again every second.
            if !self.wait_for(Duration::from_secs(1), ready).await {
                continue;
            }
            let batch: Vec<u64>;
            let text;
            {
                let mut st = self.lock();
                if self.delivery_done(&mut st, id) {
                    return;
                }
                let held = &st.reg.terminals[&id].held;
                batch = held.iter().map(|h| h.id).collect();
                text = held.iter().map(|h| h.text.as_str()).collect::<Vec<_>>().join("\n");
            }
            let sent = self.send(id, &text, false, None).await;
            // A dialog that opened meanwhile is not a failed try: it waits like before.
            if matches!(&sent, Err(e) if e.kind == "blocked") {
                continue;
            }
            let retry = {
                let mut st = self.lock();
                let result = match &sent {
                    Ok(s) if s.started == Some(true) => Some("confirmed"),
                    Ok(s) if s.started.is_none() => Some("delivered"), // it never reports status: nobody can tell
                    _ => None,
                };
                let Some(t) = st.reg.terminals.get_mut(&id) else {
                    st.held_tasks.remove(&id);
                    return;
                };
                let retry = result.is_none();
                let result = result.or_else(|| {
                    t.held.iter_mut().filter(|h| batch.contains(&h.id)).for_each(|h| h.attempts += 1);
                    t.held.iter().any(|h| h.attempts >= MAX_ATTEMPTS).then_some("gave_up")
                });
                if let Some(result) = result {
                    t.held.retain(|h| !batch.contains(&h.id));
                    self.delivery_event(&mut st, id, result, &batch);
                }
                self.save(&st);
                retry
            };
            if retry {
                tokio::time::sleep(retry_after()).await;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::status::Status;
    use crate::terminals::{NewTerminal, Registry};

    #[test]
    fn a_note_waits_for_work_or_the_hold_and_never_goes_into_a_dialog() {
        let mut r = Registry::default();
        let id = r.create(NewTerminal { argv: vec!["x".into()], ..Default::default() }, 0).unwrap();
        let t = r.terminals.get_mut(&id).unwrap();
        t.held.push(Held { id: 1, text: "n".into(), since_ms: 1000, attempts: 0 });
        assert!(due(t, 1000), "it never reported status, so waiting can't help");
        t.reported = true;
        let say = |state: &str| Status {
            state: state.into(),
            kind: None,
            progress: None,
            app: String::new(),
            title: String::new(),
            msg: String::new(),
            source: None,
        };
        t.status = Some(say("idle"));
        assert!(!due(t, 1000), "idle: held");
        assert!(due(t, 1000 + hold_for().as_millis() as u64), "idle too long: one wake");
        t.status = Some(say("working"));
        assert!(due(t, 1000), "joins the current turn");
        t.status = Some(say("blocked"));
        assert!(!due(t, u64::MAX), "typing could answer its dialog");
    }
}
