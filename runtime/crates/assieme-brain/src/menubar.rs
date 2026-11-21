//! Port of `assieme/menubar.py`, owned by session B (see OWNERS.md).
//!
//! The menu bar panel and Mac notifications: what the icon counts, what its panel shows, what should pop up.
// Declared by the foundation; the owner fills the bodies and removes this allow.
#![allow(clippy::todo, unused_variables)]
#[allow(unused_imports)]
use crate::prelude::*;

/// The menu bar's data and the app's notifications.
pub struct Menubar {}

impl Menubar {
    pub fn new(brain: Arc<Brain>, reviews: Arc<Review>) -> Menubar {
        todo!("menubar.Menubar.new")
    }

    pub fn state(&self) -> Value {
        todo!("menubar.Menubar.state")
    }

    pub fn poll(&self) -> Value {
        todo!("menubar.Menubar.poll")
    }

    /// The app polled recently, so it shows notifications itself.
    pub fn app_watching(&self, now: Option<Timestamp>) -> bool {
        todo!("menubar.Menubar.app_watching")
    }

    pub fn pause(&self) -> Value {
        todo!("menubar.Menubar.pause")
    }

    pub fn resume(&self) -> Value {
        todo!("menubar.Menubar.resume")
    }

    pub fn handlers(this: Arc<Menubar>) -> Handlers {
        todo!("menubar.Menubar.handlers")
    }
}
