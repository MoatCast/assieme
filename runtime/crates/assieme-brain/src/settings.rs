//! Port of `assieme/settings.py`, owned by the foundation (see OWNERS.md).
//!
//! The app's settings: a small JSON file in the state folder. A damaged file gives the defaults, which are Automatic:
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// (top, sub) of a key that may be set to `value`.
pub fn check(key: &str, value: &Value) -> Result<(String, String), BrainError> {
    todo!("settings._check")
}

/// The settings, cleaned: a damaged file gives the defaults.
pub fn load(file: Option<&Path>) -> Value {
    todo!("settings.load")
}

/// Once, for an install from before fixes ran themselves.
pub fn migrate(file: Option<&Path>) -> bool {
    todo!("settings.migrate")
}

pub fn set_value(key: &str, value: &Value, file: Option<&Path>) -> Result<Value, BrainError> {
    todo!("settings.set_value")
}

/// The saved value of a fix threshold, or its default.
pub fn threshold(name: &str) -> f64 {
    todo!("settings.threshold")
}
