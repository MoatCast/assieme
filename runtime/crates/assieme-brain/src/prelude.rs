//! What every module's declarations use.
pub use crate::answers::Answers;
pub use crate::model::Session;
pub use crate::newteam::Starter;
pub use crate::preset::{Role, Team};
pub use crate::review::Review;
pub use crate::rules::RulesFor;
pub use crate::runtime::{Runtime, RuntimeError};
pub use crate::server::{BrainError, By, Handlers, Methods};
pub use crate::status::Agent;
pub use crate::teammap::Brain;
pub use jiff::Timestamp;
pub use serde_json::{Map, Value};
pub use std::collections::{BTreeMap, BTreeSet};
pub use std::path::{Path, PathBuf};
pub use std::sync::{Arc, LazyLock, Mutex};

/// A desktop notification: (title, body).
pub type Notify = dyn Fn(&str, &str) + Send + Sync;
