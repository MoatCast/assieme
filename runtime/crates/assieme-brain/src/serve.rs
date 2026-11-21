//! Port of `assieme/serve.py`, owned by session G (see OWNERS.md).
//!
//! `assieme serve`: the brain as a background service.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// Who asked for the request this thread is serving.
pub fn caller() -> By {
    todo!("serve.caller")
}

/// One request's reply.
pub fn handle(request: &Value, handlers: &Methods) -> Value {
    todo!("serve.handle")
}
