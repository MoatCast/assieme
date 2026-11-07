mod bridge;
mod frame;
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

#[tauri::command]
async fn brain_call(method: String, params: Value) -> Result<Value, String> {
    bridge::call_at(&bridge::brain_socket(), &method, params)
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
    bridge::read_text_at(&bridge::hc_socket(&session), &address)
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Streams::default())
        .invoke_handler(tauri::generate_handler![
            brain_call,
            terminal_open,
            terminal_input,
            terminal_resize,
            terminal_close,
            terminal_read,
            service_running,
            service_install
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
