//! Port of `assieme/bridge.py`, owned by session E (see OWNERS.md).
//!
//! The team tools' way in for agents in containers. A container on a Mac can't open the brain's Unix socket, so the
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// From now on `ticket` speaks for the agent in terminal `address`.
pub fn bind(ticket: &str, address: &str) {
    todo!("bridge.bind")
}

/// A new ticket.
pub fn issue() -> String {
    todo!("bridge.issue")
}

/// The port the bridge listens on, or None.
pub fn port() -> Option<u16> {
    todo!("bridge.port")
}

/// Forget the tickets of terminals that are gone.
pub fn prune(live: &BTreeSet<String>) {
    todo!("bridge.prune")
}

pub fn revoke(addresses: &[String]) {
    todo!("bridge.revoke")
}

/// Listen on a free port in the background and write it down for `launch`. Returns the port.
pub fn start(handlers: Arc<Methods>, host: &str) -> u16 {
    todo!("bridge.start")
}
