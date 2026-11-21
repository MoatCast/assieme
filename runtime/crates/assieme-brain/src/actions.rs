//! Port of `assieme/actions.py`, owned by session C (see OWNERS.md).
//!
//! Step 18a: the brain's actions, described for assistants (Claude Code, Codex, the Claude app), so a session can do
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// One action an assistant may run (step 18a).
pub struct Action {
    pub description: &'static str,
    pub params: Vec<(&'static str, &'static str)>,
    /// Python's default is False.
    pub changes: bool,
}

/// Every action an assistant may run, by name.
pub static CATALOGUE: LazyLock<BTreeMap<&'static str, Action>> = LazyLock::new(|| todo!("actions.CATALOGUE"));

pub const CLIENT_ASKS: &str = "ASSIEME_CLIENT_ASKS";

/// `assistant.actions` and `assistant.do`, routed through `table`.
pub fn handlers(table: Arc<Methods>) -> Handlers {
    todo!("actions.handlers")
}
