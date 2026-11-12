mod bridge;
mod frame;
mod menubar;
mod service;

use bridge::Stream;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU32, Ordering};
use tauri::{Manager, State};
use tauri::ipc::{Channel, InvokeResponseBody};

/// Live terminal streams by id. Only `bridge.ts` calls these commands.
#[derive(Default)]
struct Streams {
    next: AtomicU32,
    open: Mutex<HashMap<u32, Stream>>,
}

/// How long a brain call may take: 20 s, unless the page asks for longer (Help me choose waits for an agent).
fn timeout_for(timeout_s: Option<u64>) -> std::time::Duration {
    std::time::Duration::from_secs(timeout_s.unwrap_or(20))
}

#[tauri::command]
async fn brain_call(method: String, params: Value, timeout_s: Option<u64>) -> Result<Value, String> {
    // Socket I/O blocks: keep it off the async workers so a slow brain can't stall terminal commands.
    let timeout = timeout_for(timeout_s);
    tauri::async_runtime::spawn_blocking(move || bridge::call_at_with(&bridge::brain_socket(), &method, params, timeout))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn terminal_open(
    streams: State<'_, Streams>,
    address: String,
    cols: u16,
    rows: u16,
    observe: bool,
    on_data: Channel<InvokeResponseBody>,
    on_close: Channel<String>,
) -> Result<u32, String> {
    let s = Stream::open(
        &bridge::hc_socket("assieme"),
        &address,
        cols,
        rows,
        observe,
        move |b| {
            let _ = on_data.send(InvokeResponseBody::Raw(b));
        },
        move |why| {
            let _ = on_close.send(why);
        },
    )?;
    let id = streams.next.fetch_add(1, Ordering::Relaxed);
    streams.open.lock().unwrap().insert(id, s);
    Ok(id)
}

#[tauri::command]
async fn terminal_input(streams: State<'_, Streams>, id: u32, data: Vec<u8>) -> Result<(), String> {
    match streams.open.lock().unwrap().get(&id) {
        Some(s) => s.input(&data),
        None => Err(format!("no terminal stream {id}")),
    }
}

#[tauri::command]
async fn terminal_resize(streams: State<'_, Streams>, id: u32, cols: u16, rows: u16) -> Result<(), String> {
    match streams.open.lock().unwrap().get(&id) {
        Some(s) => s.resize(cols, rows),
        None => Err(format!("no terminal stream {id}")),
    }
}

#[tauri::command]
async fn terminal_close(streams: State<'_, Streams>, id: u32) -> Result<(), String> {
    if let Some(s) = streams.open.lock().unwrap().remove(&id) {
        s.close();
    }
    Ok(())
}

/// Read-only text of a terminal in any session: the only access to sessions other than `assieme`.
#[tauri::command]
async fn terminal_read(session: String, address: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || bridge::read_text_at(&bridge::hc_socket(&session), &address))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn service_running() -> bool {
    service::running("launchctl")
}

/// Install the background brain with the Python and hc bundled inside the app.
#[tauri::command]
async fn service_install(app: tauri::AppHandle) -> Result<(), String> {
    let res = app.path().resource_dir().map_err(|e| e.to_string())?;
    service::install(&res.join("python/bin/python3"), &res.join("hc-bin"))
}

#[tauri::command]
async fn window_open(app: tauri::AppHandle, team: Option<String>, role: Option<String>, view: Option<String>) {
    menubar::hide_panel(&app);
    menubar::open_window(&app, team.as_deref(), role.as_deref(), view.as_deref());
}

#[tauri::command]
async fn menu_fit(app: tauri::AppHandle, height: f64) -> Result<(), String> {
    menubar::fit_panel(&app, height)
}

#[tauri::command]
async fn notifications_off() -> bool {
    menubar::notifications_off().await
}

/// The one fix macOS leaves to the person: its own page for notifications.
#[tauri::command]
async fn notification_settings_open() -> Result<(), String> {
    std::process::Command::new("open").arg("x-apple.systempreferences:com.apple.Notifications-Settings.extension")
        .status().map(|_| ()).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Streams::default())
        .manage(menubar::LastHidden::default())
        .setup(|app| Ok(menubar::setup(app.handle())?))
        .on_window_event(menubar::window_event)
        // The main window starts hidden (tauri.conf.json) so it never shows a frame in the wrong theme. Once its page
        // has loaded, its CSS already has the theme: show it here, so a failing script can never leave no window.
        .on_page_load(|webview, payload| {
            if webview.label() == "main" && payload.event() == tauri::webview::PageLoadEvent::Finished {
                let _ = webview.window().show();
            }
        })
        .invoke_handler(tauri::generate_handler![
            brain_call,
            terminal_open,
            terminal_input,
            terminal_resize,
            terminal_close,
            terminal_read,
            service_running,
            service_install,
            window_open,
            menu_fit,
            notifications_off,
            notification_settings_open
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(menubar::run_event);
}

#[cfg(test)]
mod tests {
    use super::timeout_for;
    use std::time::Duration;

    #[test]
    fn brain_calls_wait_20s_unless_told() {
        assert_eq!(timeout_for(None), Duration::from_secs(20));
        assert_eq!(timeout_for(Some(180)), Duration::from_secs(180));
    }
}
