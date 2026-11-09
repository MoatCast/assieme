//! The menu bar icon, its panel window and Mac notifications. The brain decides everything (`menu.poll`, see
//! `assieme/menubar.py`); this file only draws it and carries presses back.
use crate::bridge;
use serde_json::{Value, json};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::image::Image;
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, Rect, RunEvent, Runtime, WebviewWindow, Window, WindowEvent};
use mac_usernotifications::{Action, AuthorizationStatus, Notification, NotificationSettingStatus, get_notification_settings, request_auth};

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

/// What a press on a notification means: "tap" is the notification itself, anything else a button's id.
fn press_for(action: &str, event: &Value) -> Option<Press> {
    if action == "tap" {
        return Some(Press::Open);
    }
    let buttons = event["buttons"].as_array()?;
    let b = buttons.iter().find(|b| b["id"] == action)?;
    Some(match b["id"].as_str()? {
        "open" => Press::Open,
        id => Press::Button(id.to_string()),
    })
}

/// The red badge for this count, drawn by `icons/badges/render.py`. None: nothing needs you, so the plain icon.
fn badge_for(count: u64) -> Option<Image<'static>> {
    Some(match count {
        0 => return None,
        1 => tauri::include_image!("icons/badges/1.png"),
        2 => tauri::include_image!("icons/badges/2.png"),
        3 => tauri::include_image!("icons/badges/3.png"),
        4 => tauri::include_image!("icons/badges/4.png"),
        5 => tauri::include_image!("icons/badges/5.png"),
        6 => tauri::include_image!("icons/badges/6.png"),
        7 => tauri::include_image!("icons/badges/7.png"),
        8 => tauri::include_image!("icons/badges/8.png"),
        9 => tauri::include_image!("icons/badges/9.png"),
        _ => tauri::include_image!("icons/badges/more.png"),
    })
}

/// A badge stays red in any menu bar; the plain icon is a template, which macOS tints to suit the bar.
fn show_count<R: Runtime>(app: &AppHandle<R>, count: u64) {
    let Some(tray) = app.tray_by_id("assieme") else { return };
    let badge = badge_for(count);
    let template = badge.is_none();
    let _ = tray.set_icon(Some(badge.unwrap_or(tauri::include_image!("icons/tray.png"))));
    let _ = tray.set_icon_as_template(template);
    // The image has no words for VoiceOver.
    let _ = tray.set_tooltip(Some(match count {
        0 => "Assieme".to_string(),
        1 => "Assieme: 1 needs you".to_string(),
        n => format!("Assieme: {n} need you"),
    }));
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

fn on_press<R: Runtime>(app: &AppHandle<R>, action: &str, event: &Value) {
    let (team, role, view) = (event["team"].as_str(), event["role"].as_str(), event["view"].as_str());
    match press_for(action, event) {
        Some(Press::Open) => open_window(app, team, role, view),
        Some(Press::Button(button)) => {
            let reply = bridge::call_at(&bridge::brain_socket(), "pane.act",
                json!({"team": team, "role": role, "fix": event["fix"], "button": button}));
            // The question changed, or the press failed: show it rather than leave them believing it was answered.
            if !reply.is_ok_and(|r| r["ok"] == true) {
                open_window(app, team, role, view);
            }
        }
        None => {}
    }
}

/// A small task per notification, waiting for what's done with it, so ones nobody answers cost no thread. It ends once
/// it's pressed or dismissed.
fn show<R: Runtime>(app: &AppHandle<R>, event: &Value) {
    let (app, event) = (app.clone(), event.clone());
    tauri::async_runtime::spawn(async move {
        let mut n = Notification::new()
            .title(event["title"].as_str().unwrap_or("Assieme"))
            .message(event["body"].as_str().unwrap_or_default());
        for b in event["buttons"].as_array().into_iter().flatten() {
            n = n.action(Action::button(b["id"].as_str().unwrap_or_default(), b["label"].as_str().unwrap_or_default()));
        }
        let response = async { n.send().await?.response().await }.await;
        match response {
            Ok(r) if r.is_dismiss_action() || r.is_timed_out() => {}
            Ok(r) => {
                let action = if r.is_default_action() { "tap".to_string() } else { r.action_identifier.clone() };
                // A press calls the brain over its socket: that must not hold up the other notifications' tasks.
                let _ = tauri::async_runtime::spawn_blocking(move || on_press(&app, &action, &event)).await;
            }
            Err(e) => eprintln!("notification not shown: {e}"), // for example, notifications are off: the panel says so
        }
    });
}

fn poll_forever<R: Runtime>(app: AppHandle<R>) {
    // First, so nothing is sent before the person has answered macOS's question. Later runs return at once.
    let _ = tauri::async_runtime::block_on(request_auth());
    let mut shown = 0;
    loop {
        // A brain that isn't running shows no count; First run deals with that.
        let reply = bridge::call_at(&bridge::brain_socket(), "menu.poll", json!({})).unwrap_or(Value::Null);
        let count = reply["count"].as_u64().unwrap_or(0);
        if count != shown {
            show_count(&app, count);
            shown = count;
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

/// Whether banners can't reach the person. A failed read counts as on: no false alarm.
pub async fn notifications_off() -> bool {
    get_notification_settings().await.is_ok_and(|s| off_for(s.authorization_status, s.alert_enabled))
}

fn off_for(status: AuthorizationStatus, alerts: NotificationSettingStatus) -> bool {
    !matches!(status, AuthorizationStatus::Authorized | AuthorizationStatus::Provisional | AuthorizationStatus::Ephemeral)
        || matches!(alerts, NotificationSettingStatus::Disabled)
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
    fn buttons_are_told_apart_by_id() {
        assert_eq!(press_for("open", &event()), Some(Press::Open));
        assert_eq!(press_for("b1", &event()), Some(Press::Button("b1".into())));
    }

    #[test]
    fn an_unknown_press_does_nothing() {
        assert_eq!(press_for("b2", &event()), None);
        assert_eq!(press_for("b2", &json!({})), None);
    }

    #[test]
    fn the_icon_has_a_badge_only_when_something_needs_you() {
        assert!(badge_for(0).is_none());
        // One digit is a round badge; past 9 they all share the wider "9+".
        assert_eq!(badge_for(1).unwrap().width(), 72);
        assert_eq!(badge_for(9).unwrap().width(), 72);
        assert_eq!(badge_for(10).unwrap().width(), 104);
        assert_eq!(badge_for(250).unwrap().width(), 104);
        assert_eq!(badge_for(1).unwrap().height(), 72);
        assert_ne!(badge_for(1).unwrap().rgba(), badge_for(2).unwrap().rgba());
    }

    #[test]
    fn notifications_count_as_off_unless_clearly_on() {
        use AuthorizationStatus::*;
        use NotificationSettingStatus::*;
        assert!(!off_for(Authorized, Enabled));
        assert!(!off_for(Provisional, NotSupported));
        assert!(off_for(Denied, Disabled));
        assert!(off_for(NotDetermined, NotSupported));
        assert!(off_for(Authorized, Disabled));
    }
}
