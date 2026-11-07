//! The bridge between the page and the two local services: the brain (`assieme serve`) and herdr-core's
//! `assieme` session. Plain functions over Unix sockets, so they are tested without Tauri; `lib.rs` wraps them
//! as commands. Terminals in any other hc session are only ever read, never attached.
use crate::frame::{self, CLOSE, CREDIT, DATA, RESIZE};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

fn connect(sock: &Path) -> Result<UnixStream, String> {
    UnixStream::connect(sock).map_err(|e| format!("cannot reach {}: {e}", sock.display()))
}

fn reply_result(v: Value) -> Result<Value, String> {
    if let Some(e) = v.get("error") {
        return Err(e.get("message").and_then(Value::as_str).unwrap_or("error").to_string());
    }
    Ok(v.get("result").cloned().unwrap_or(Value::Null))
}

fn state_home() -> PathBuf {
    std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".local/state"))
}

pub fn brain_socket() -> PathBuf {
    state_home().join("assieme/brain.sock")
}

/// The `assieme` session's socket (fixed by Assieme), or another session's default hc socket.
pub fn hc_socket(session: &str) -> PathBuf {
    if session == "assieme" {
        state_home().join("assieme/hc.sock")
    } else {
        state_home().join(format!("herdr-core/{session}/{session}.sock"))
    }
}

/// One JSON-RPC call, one line each way. An error reply becomes `Err(message)`.
pub fn call_at(sock: &Path, method: &str, params: Value) -> Result<Value, String> {
    let mut s = connect(sock)?;
    let req = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
    s.write_all(format!("{req}\n").as_bytes()).map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(&s).read_line(&mut line).map_err(|e| e.to_string())?;
    reply_result(serde_json::from_str(&line).map_err(|e| format!("bad reply: {e}"))?)
}

/// A terminal's screen and history as text: the only access to sessions other than `assieme`.
pub fn read_text_at(sock: &Path, address: &str) -> Result<String, String> {
    let r = call_at(sock, "terminal.read", json!({"terminal": address, "lines": 2000}))?;
    Ok(r.get("text").and_then(Value::as_str).unwrap_or_default().to_string())
}

pub struct Stream {
    writer: Arc<Mutex<UnixStream>>,
    channel: u32,
}

impl Stream {
    /// Attach to a live terminal. Every DATA payload goes to `on_data` and is credited back at once;
    /// `on_close` gets the reason once when the stream ends.
    pub fn open(
        sock: &Path,
        address: &str,
        cols: u16,
        rows: u16,
        observe: bool,
        on_data: impl Fn(Vec<u8>) + Send + 'static,
        on_close: impl Fn(String) + Send + 'static,
    ) -> Result<Stream, String> {
        let s = connect(sock)?;
        let mut w = s.try_clone().map_err(|e| e.to_string())?;
        let mut r = BufReader::new(s);
        w.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"hello\",\"params\":{\"framed\":true}}\n")
            .map_err(|e| e.to_string())?;
        let mut line = String::new();
        r.read_line(&mut line).map_err(|e| e.to_string())?;
        reply_result(serde_json::from_str(&line).map_err(|e| format!("bad hello reply: {e}"))?)?;
        let attach = json!({"jsonrpc": "2.0", "id": 2, "method": "terminal.attach",
            "params": {"terminal": address, "cols": cols, "rows": rows, "observe": observe}});
        w.write_all(&frame::encode(0, DATA, attach.to_string().as_bytes())).map_err(|e| e.to_string())?;
        let channel = loop {
            match frame::read(&mut r).map_err(|e| e.to_string())? {
                Some((0, DATA, p)) => {
                    let v: Value = serde_json::from_slice(&p).map_err(|e| format!("bad attach reply: {e}"))?;
                    if v.get("id") == Some(&json!(2)) {
                        let res = reply_result(v)?;
                        break res.get("channel").and_then(Value::as_u64).ok_or("attach gave no channel")? as u32;
                    }
                }
                Some(_) => {}
                None => return Err("hc closed the connection during attach".into()),
            }
        };
        let writer = Arc::new(Mutex::new(w));
        let credit = writer.clone();
        std::thread::spawn(move || {
            let why = loop {
                match frame::read(&mut r) {
                    Ok(Some((ch, DATA, p))) if ch == channel => {
                        let n = p.len() as u32;
                        on_data(p);
                        // Return the credit at once, so hc keeps streaming past its 256 KiB window.
                        if credit.lock().unwrap().write_all(&frame::encode(channel, CREDIT, &n.to_le_bytes())).is_err() {
                            break "lost".to_string();
                        }
                    }
                    Ok(Some((ch, CLOSE, p))) if ch == channel => {
                        break if p.is_empty() { "closed".into() } else { String::from_utf8_lossy(&p).into_owned() };
                    }
                    Ok(Some(_)) => {}
                    Ok(None) | Err(_) => break "lost".to_string(),
                }
            };
            on_close(why);
        });
        Ok(Stream { writer, channel })
    }

    fn send(&self, kind: u8, payload: &[u8]) -> Result<(), String> {
        self.writer.lock().unwrap().write_all(&frame::encode(self.channel, kind, payload)).map_err(|e| e.to_string())
    }

    pub fn input(&self, data: &[u8]) -> Result<(), String> {
        self.send(DATA, data)
    }

    pub fn resize(&self, cols: u16, rows: u16) -> Result<(), String> {
        let mut p = cols.to_le_bytes().to_vec();
        p.extend(rows.to_le_bytes());
        self.send(RESIZE, &p)
    }

    pub fn close(&self) {
        let _ = self.send(CLOSE, b"");
        let _ = self.writer.lock().unwrap().shutdown(std::net::Shutdown::Both);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frame::{self, CLOSE, CREDIT, DATA, RESIZE};
    use std::io::{BufRead, BufReader, Write};
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::sync::{Arc, Mutex, mpsc};
    use std::time::Duration;

    fn temp_sock(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("assieme-bridge-{}-{name}", std::process::id()));
        let _ = std::fs::create_dir_all(&d);
        let p = d.join("s.sock");
        let _ = std::fs::remove_file(&p);
        p
    }

    /// Fake hc: answers `hello` and `terminal.attach`, sends `total` bytes on channel 1 in 16 KiB frames,
    /// then reports every frame the client sent until it closes.
    fn fake_hc(sock: &Path, total: usize) -> mpsc::Receiver<(u32, u8, Vec<u8>)> {
        let listener = UnixListener::bind(sock).unwrap();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let (s, _) = listener.accept().unwrap();
            let mut r = BufReader::new(s.try_clone().unwrap());
            let mut w = s;
            let mut line = String::new();
            r.read_line(&mut line).unwrap();
            assert!(line.contains("\"framed\":true"), "hello first: {line}");
            w.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"framed\":true}}\n").unwrap();
            let (ch, _, attach) = frame::read(&mut r).unwrap().unwrap();
            assert_eq!(ch, 0);
            tx.send((0, DATA, attach)).unwrap();
            w.write_all(&frame::encode(0, DATA, br#"{"jsonrpc":"2.0","id":2,"result":{"channel":1}}"#)).unwrap();
            let mut sent = 0;
            while sent < total {
                let n = (total - sent).min(16 * 1024);
                w.write_all(&frame::encode(1, DATA, &vec![b'x'; n])).unwrap();
                sent += n;
            }
            while let Ok(Some(f)) = frame::read(&mut r) {
                if tx.send(f).is_err() {
                    break;
                }
            }
        });
        rx
    }

    fn open(sock: &Path, observe: bool) -> (Stream, Arc<Mutex<usize>>, mpsc::Receiver<String>) {
        let got = Arc::new(Mutex::new(0));
        let g = got.clone();
        let (ctx, crx) = mpsc::channel();
        let s = Stream::open(sock, "t1", 120, 40, observe, move |b| *g.lock().unwrap() += b.len(), move |why| {
            let _ = ctx.send(why);
        })
        .unwrap();
        (s, got, crx)
    }

    #[test]
    fn data_frames_return_credit() {
        let sock = temp_sock("credit");
        let rx = fake_hc(&sock, 300 * 1024);
        let (s, got, _) = open(&sock, true);
        let (_, _, attach) = rx.recv_timeout(Duration::from_secs(5)).unwrap();
        let attach: Value = serde_json::from_slice(&attach).unwrap();
        assert_eq!(attach["method"], "terminal.attach");
        assert_eq!((attach["params"]["terminal"].as_str(), attach["params"]["observe"].as_bool()), (Some("t1"), Some(true)));
        let mut credited = 0;
        while credited < 300 * 1024 {
            let (ch, kind, p) = rx.recv_timeout(Duration::from_secs(5)).unwrap();
            assert_eq!((ch, kind), (1, CREDIT));
            credited += u32::from_le_bytes(p[..4].try_into().unwrap()) as usize;
        }
        assert_eq!(credited, 300 * 1024);
        assert_eq!(*got.lock().unwrap(), 300 * 1024);
        s.close();
    }

    #[test]
    fn resize_frame_is_cols_then_rows_le() {
        let sock = temp_sock("resize");
        let rx = fake_hc(&sock, 0);
        let (s, _, _) = open(&sock, false);
        rx.recv_timeout(Duration::from_secs(5)).unwrap();
        s.resize(120, 40).unwrap();
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap(), (1, RESIZE, vec![120, 0, 40, 0]));
        s.input(b"hi").unwrap();
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap(), (1, DATA, b"hi".to_vec()));
        s.close();
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap().1, CLOSE);
    }

    #[test]
    fn lost_stream_reports_close() {
        let sock = temp_sock("lost");
        let listener = UnixListener::bind(&sock).unwrap();
        std::thread::spawn(move || {
            let (s, _) = listener.accept().unwrap();
            let mut r = BufReader::new(s.try_clone().unwrap());
            let mut w = s;
            let mut line = String::new();
            r.read_line(&mut line).unwrap();
            w.write_all(b"{\"id\":1,\"result\":{}}\n").unwrap();
            frame::read(&mut r).unwrap();
            w.write_all(&frame::encode(0, DATA, br#"{"id":2,"result":{"channel":1}}"#)).unwrap();
            // then the core goes away
        });
        let (_s, _, closed) = open(&sock, true);
        assert_eq!(closed.recv_timeout(Duration::from_secs(5)).unwrap(), "lost");
    }

    #[test]
    fn attach_error_is_returned() {
        let sock = temp_sock("attach-err");
        let listener = UnixListener::bind(&sock).unwrap();
        std::thread::spawn(move || {
            let (s, _) = listener.accept().unwrap();
            let mut r = BufReader::new(s.try_clone().unwrap());
            let mut w = s;
            let mut line = String::new();
            r.read_line(&mut line).unwrap();
            w.write_all(b"{\"id\":1,\"result\":{}}\n").unwrap();
            frame::read(&mut r).unwrap();
            w.write_all(&frame::encode(0, DATA, br#"{"id":2,"error":{"message":"t1 has finished"}}"#)).unwrap();
        });
        let r = Stream::open(&sock, "t1", 80, 24, true, |_| {}, |_| {});
        assert_eq!(r.err().unwrap(), "t1 has finished");
    }

    fn fake_line_server(sock: &Path, reply: &'static str) -> mpsc::Receiver<String> {
        let listener = UnixListener::bind(sock).unwrap();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let (s, _) = listener.accept().unwrap();
            let mut r = BufReader::new(s.try_clone().unwrap());
            let mut line = String::new();
            r.read_line(&mut line).unwrap();
            tx.send(line).unwrap();
            let mut w: UnixStream = s;
            w.write_all(reply.as_bytes()).unwrap();
        });
        rx
    }

    #[test]
    fn brain_call_error_is_string() {
        let sock = temp_sock("brain-err");
        let _rx = fake_line_server(&sock, "{\"id\":1,\"error\":{\"message\":\"nope\"}}\n");
        assert_eq!(call_at(&sock, "brain.snapshot", serde_json::json!({})).unwrap_err(), "nope");
    }

    #[test]
    fn brain_call_result() {
        let sock = temp_sock("brain-ok");
        let rx = fake_line_server(&sock, "{\"id\":1,\"result\":{\"ok\":true}}\n");
        assert_eq!(call_at(&sock, "brain.snapshot", serde_json::json!({})).unwrap()["ok"], true);
        assert!(rx.recv().unwrap().contains("\"method\":\"brain.snapshot\""));
    }

    #[test]
    fn read_only_for_other_sessions() {
        assert!(hc_socket("default").ends_with("herdr-core/default/default.sock"));
        assert!(hc_socket("assieme").ends_with("assieme/hc.sock"));
        let sock = temp_sock("read");
        let rx = fake_line_server(&sock, "{\"id\":1,\"result\":{\"text\":\"hello\"}}\n");
        assert_eq!(read_text_at(&sock, "t4").unwrap(), "hello");
        let req: Value = serde_json::from_str(&rx.recv().unwrap()).unwrap();
        assert_eq!((req["method"].as_str(), req["params"]["terminal"].as_str()), (Some("terminal.read"), Some("t4")));
    }
}
