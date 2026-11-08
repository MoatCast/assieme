//! The menu bar icon, its panel window and Mac notifications. The brain decides everything (`menu.poll`, see
//! `assieme/menubar.py`); this file only draws it and carries presses back.
use crate::bridge;
use serde_json::{Value, json};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, Rect, RunEvent, Runtime, WebviewWindow, Window, WindowEvent};
use tauri_plugin_notification::{Action, ActionPerformed, ActionType, NotificationExt};

const POLL: Duration = Duration::from_secs(10);
const CLICK_THAT_CLOSED: Duration = Duration::from_millis(300);
const PANEL_WIDTH: f64 = 360.0;
const PANEL_MAX_HEIGHT: f64 = 640.0;
const GAP: f64 = 6.0;

/// When the panel last hid itself. Clicking the icon while it's open first takes its focus away, which hides it; that
/// same click must then not open it again.
#[derive(Default)]
pub struct LastHidden(Mutex<Option<Instant>>);

#[derive(Debug, PartialEq)]
enum Press {
    Open,
    Button(String),
}

/// What a press on a notification means. macOS reports a button by its title, and "tap" for the notification itself.
fn press_for(action: &str, event: &Value) -> Option<Press> {
    if action == "tap" {
        return Some(Press::Open);
    }
    let buttons = event["buttons"].as_array()?;
    let b = buttons.iter().find(|b| b["label"] == action || b["id"] == action)?;
    Some(match b["id"].as_str()? {
        "open" => Press::Open,
        id => Press::Button(id.to_string()),
    })
}

fn title_for(count: u64) -> Option<String> {
    (count > 0).then(|| count.to_string())
}

/// Bring the main window forward, on an agent if there's one to show.
pub fn open_window<R: Runtime>(app: &AppHandle<R>, team: Option<&str>, role: Option<&str>, view: Option<&str>) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
    if team.is_some() {
        let _ = app.emit_to("main", "assieme-open", json!({"team": team, "role": role, "view": view}));
    }
}

fn on_press<R: Runtime>(app: AppHandle<R>, performed: &ActionPerformed) {
    let Some(event) = performed.notification().and_then(|n| n.extra().get("event")).cloned() else {
        return open_window(&app, None, None, None); // can't tell which: the app is the safe answer
    };
    let (team, role, view) = (event["team"].as_str(), event["role"].as_str(), event["view"].as_str());
    match press_for(performed.action_id(), &event) {
        Some(Press::Open) => open_window(&app, team, role, view),
        Some(Press::Button(button)) => {
            let reply = bridge::call_at(&bridge::brain_socket(), "pane.act",
                json!({"team": team, "role": role, "fix": event["fix"], "button": button}));
            // The question changed, or the press failed: show it rather than leave them believing it was answered.
            if !reply.is_ok_and(|r| r["ok"] == true) {
                open_window(&app, team, role, view);
            }
        }
        None => {}
    }
}

fn show<R: Runtime>(app: &AppHandle<R>, event: &Value) {
    let n = app.notification();
    let key = event["key"].as_str().unwrap_or_default();
    let actions: Vec<Action> = event["buttons"].as_array().into_iter().flatten()
        .filter_map(|b| Some(Action::builder(b["label"].as_str()?, b["label"].as_str()?).build()))
        .collect();
    let _ = n.register_action_types(vec![ActionType::builder(key).actions(actions).build()]);
    let _ = n.builder()
        .title(event["title"].as_str().unwrap_or("Assieme"))
        .body(event["body"].as_str().unwrap_or_default())
        .action_type_id(key)
        .extra("event", event)
        .show();
}

fn poll_forever<R: Runtime>(app: AppHandle<R>) {
    // First, so nothing is sent before the person has answered macOS's question. Later runs return at once.
    let asked = notify_rust::request_auth_blocking();
    eprintln!("notifications: asked {asked:?}, now {:?}", notify_rust::get_notification_settings_blocking());
    let mut shown = None;
    loop {
        // A brain that isn't running shows no count; First run deals with that.
        let reply = bridge::call_at(&bridge::brain_socket(), "menu.poll", json!({})).unwrap_or(Value::Null);
        let title = title_for(reply["count"].as_u64().unwrap_or(0));
        if title != shown {
            if let Some(tray) = app.tray_by_id("assieme") {
                let _ = tray.set_title(title.as_deref());
            }
            shown = title;
        }
        // Kept fresh while hidden: a panel call takes a moment, and opening must not paint old news first.
        let _ = app.emit_to("menu", "menu-state", &reply["panel"]);
        for event in reply["events"].as_array().into_iter().flatten() {
            show(&app, event);
        }
        std::thread::sleep(POLL);
    }
}

/// Centred under the icon, kept on the icon's screen.
fn place<R: Runtime>(w: &WebviewWindow<R>, rect: &Rect) -> tauri::Result<()> {
    let scale = w.scale_factor()?;
    let (at, size) = (rect.position.to_physical::<f64>(scale), rect.size.to_physical::<f64>(scale));
    let width = w.outer_size()?.width as f64;
    let mut x = at.x + size.width / 2.0 - width / 2.0;
    if let Some(m) = w.monitor_from_point(at.x, at.y)? {
        let right = m.position().x as f64 + m.size().width as f64;
        x = x.min(right - width - GAP * scale).max(m.position().x as f64);
    }
    w.set_position(PhysicalPosition::new(x, at.y + size.height + GAP * scale))
}

fn toggle<R: Runtime>(app: &AppHandle<R>, rect: &Rect) {
    let Some(w) = app.get_webview_window("menu") else { return };
    if w.is_visible().unwrap_or(false) {
        let _ = w.hide();
        return;
    }
    let closed_it = app.state::<LastHidden>().0.lock().unwrap().is_some_and(|t| t.elapsed() < CLICK_THAT_CLOSED);
    if closed_it {
        return;
    }
    let _ = place(&w, rect);
    let _ = w.show();
    let _ = w.set_focus();
    let _ = w.emit("menu-opened", ());
}

pub fn setup<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    TrayIconBuilder::with_id("assieme")
        .icon(tauri::include_image!("icons/tray.png"))
        .icon_as_template(true)
        .tooltip("Assieme")
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, rect, .. } = event {
                toggle(tray.app_handle(), &rect);
            }
        })
        .build(app)?;
    let handle = app.clone();
    // Registered before anything is shown: the plugin only reports presses on notifications shown after this.
    let _ = app.notification().on_action(move |performed| {
        let (app, performed) = (handle.clone(), performed.clone());
        std::thread::spawn(move || on_press(app, &performed)); // a brain call: the plugin asks for a quick return
    });
    let handle = app.clone();
    std::thread::spawn(move || poll_forever(handle));
    Ok(())
}

/// Closing the main window keeps the app, so the icon and notifications stay. Clicking away closes the panel.
pub fn window_event<R: Runtime>(w: &Window<R>, event: &WindowEvent) {
    match (w.label(), event) {
        ("main", WindowEvent::CloseRequested { api, .. }) => {
            api.prevent_close();
            let _ = w.hide();
        }
        ("menu", WindowEvent::Focused(false)) => {
            *w.state::<LastHidden>().0.lock().unwrap() = Some(Instant::now());
            let _ = w.hide();
        }
        _ => {}
    }
}

/// The Dock icon brings the hidden window back.
pub fn run_event<R: Runtime>(app: &AppHandle<R>, event: RunEvent) {
    if let RunEvent::Reopen { has_visible_windows: false, .. } = event {
        open_window(app, None, None, None);
    }
}

/// The panel asks to fit what it's showing, up to a limit past which it scrolls.
pub fn fit_panel<R: Runtime>(app: &AppHandle<R>, height: f64) -> Result<(), String> {
    let w = app.get_webview_window("menu").ok_or("no panel")?;
    w.set_size(tauri::LogicalSize::new(PANEL_WIDTH, height.clamp(60.0, PANEL_MAX_HEIGHT))).map_err(|e| e.to_string())
}

/// Whether banners can't reach the person. notify-rust doesn't re-export the status types, so they're told apart by
/// name. A failed read counts as on: no false alarm.
fn off_for(status: &str, alerts: &str) -> bool {
    !matches!(status, "Authorized" | "Provisional" | "Ephemeral") || alerts == "Disabled"
}

pub fn notifications_off() -> bool {
    notify_rust::get_notification_settings_blocking().is_ok_and(|s| {
        off_for(&format!("{:?}", s.authorization_status), &format!("{:?}", s.alert_enabled))
    })
}

pub fn hide_panel<R: Runtime>(app: &AppHandle<R>) {
    if let Some(w) = app.get_webview_window("menu") {
        let _ = w.hide();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event() -> Value {
        json!({"buttons": [{"id": "open", "label": "Open"}, {"id": "b1", "label": "Behind a flag"}]})
    }

    #[test]
    fn a_click_on_the_notification_opens() {
        assert_eq!(press_for("tap", &event()), Some(Press::Open));
    }

    #[test]
    fn buttons_are_told_apart_by_title() {
        assert_eq!(press_for("Open", &event()), Some(Press::Open));
        assert_eq!(press_for("Behind a flag", &event()), Some(Press::Button("b1".into())));
    }

    #[test]
    fn an_unknown_press_does_nothing() {
        assert_eq!(press_for("To main", &event()), None);
        assert_eq!(press_for("To main", &json!({})), None);
    }

    #[test]
    fn the_icon_shows_a_number_only_when_something_needs_you() {
        assert_eq!(title_for(0), None);
        assert_eq!(title_for(2), Some("2".to_string()));
    }

    #[test]
    fn notifications_count_as_off_unless_clearly_on() {
        assert!(!off_for("Authorized", "Enabled"));
        assert!(!off_for("Provisional", "NotSupported"));
        assert!(off_for("Denied", "Disabled"));
        assert!(off_for("NotDetermined", "NotSupported"));
        assert!(off_for("Authorized", "Disabled"));
    }

    /// The plugin's own path from what's shown to what's reported: the brain's event must survive it.
    #[test]
    fn the_event_comes_back_with_the_press() {
        let performed: ActionPerformed = serde_json::from_value(json!({
            "actionId": "Open", "inputValue": null,
            "notification": {"id": 1, "groupSummary": false, "extra": {"event": event()}}})).unwrap();
        assert_eq!(performed.notification().unwrap().extra()["event"], event());
    }
}
