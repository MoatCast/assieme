//! The brain's socket (newline JSON-RPC, Python's `serve.py` error form) and its 15-second loop.
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;

/// A handler's failure: the app shows `message`, and `detail` under Details.
#[derive(Debug, Clone, PartialEq)]
pub struct BrainError {
    pub message: String,
    pub detail: Option<String>,
}

impl BrainError {
    pub fn new(message: impl Into<String>) -> BrainError {
        BrainError { message: message.into(), detail: None }
    }

    /// The reply's message: `bridge.ts` `splitDetail` reads the detail back out.
    fn text(&self) -> String {
        match self.detail.as_deref().filter(|d| !d.is_empty()) {
            Some(d) => format!("{}\n\nDetails:\n{d}", self.message),
            None => self.message.clone(),
        }
    }
}

impl std::fmt::Display for BrainError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

/// Who asked: an assistant through the MCP server (step 18a), or the person.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum By {
    You,
    Assistant,
}

pub struct Call {
    pub by: By,
}

pub type Handler = Box<dyn Fn(Value, &Call) -> Result<Value, BrainError> + Send + Sync>;

/// The method table.
#[derive(Default)]
pub struct Methods(BTreeMap<&'static str, Handler>);

impl Methods {
    pub fn insert(&mut self, name: &'static str, h: Handler) {
        self.0.insert(name, h);
    }

    /// Sorted, as Python's `cli.handler_names()`.
    pub fn names(&self) -> Vec<&'static str> {
        self.0.keys().copied().collect()
    }

    /// The reply to one request line.
    pub fn reply(&self, line: &[u8]) -> Value {
        let request = match crate::pyrules::json_loads_bytes(line) {
            Ok(Value::Object(o)) => o,
            _ => return json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": "not JSON"}}),
        };
        let id = request.get("id").cloned().unwrap_or(Value::Null);
        let method = request.get("method").cloned().unwrap_or(Value::Null);
        let Some(handler) = method.as_str().and_then(|m| self.0.get(m)) else {
            let message = format!("no method {}", crate::pyrules::repr(&method));
            return json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": message}});
        };
        let by = if request.get("by").and_then(Value::as_str) == Some("assistant") { By::Assistant } else { By::You };
        // Python's `request.get('params') or {}`.
        let params = match request.get("params") {
            Some(p) if crate::pyrules::truthy(p) => p.clone(),
            _ => json!({}),
        };
        let result = catch_unwind(AssertUnwindSafe(|| handler(params, &Call { by })))
            .unwrap_or_else(|panic| Err(BrainError::new(panic_text(&*panic))));
        match result {
            Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
            Err(e) => json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32000, "message": e.text()}}),
        }
    }
}

fn panic_text(panic: &(dyn std::any::Any + Send)) -> String {
    let what = panic.downcast_ref::<&str>().map(|s| s.to_string()).or_else(|| panic.downcast_ref::<String>().cloned());
    what.unwrap_or_else(|| "the brain failed".into())
}

/// A method no session has ported yet.
pub fn not_ported(name: &'static str) -> Handler {
    Box::new(move |_, _| Err(BrainError::new(format!("Not ported yet: {name}."))))
}

/// One task per connection; each request runs on a worker thread, replies in order.
pub async fn serve(listener: UnixListener, methods: Arc<Methods>) {
    loop {
        let Ok((stream, _)) = listener.accept().await else { continue };
        let methods = methods.clone();
        tokio::spawn(async move {
            let (read, mut write) = stream.into_split();
            let mut read = BufReader::new(read);
            let mut line = Vec::new();
            loop {
                line.clear();
                match read.read_until(b'\n', &mut line).await {
                    Ok(0) | Err(_) => return,
                    Ok(_) => {}
                }
                let methods = methods.clone();
                let request = std::mem::take(&mut line);
                let Ok(reply) = tokio::task::spawn_blocking(move || methods.reply(&request)).await else { return };
                let mut out = reply.to_string().into_bytes();
                out.push(b'\n');
                if write.write_all(&out).await.is_err() {
                    return; // the client hung up before its reply
                }
            }
        });
    }
}

type Step = (&'static str, Box<dyn Fn() + Send + Sync>);

/// The brain's loop: every step in order, then a pause.
#[derive(Default)]
pub struct Loop(Vec<Step>);

impl Loop {
    pub fn step(&mut self, name: &'static str, f: Box<dyn Fn() + Send + Sync>) {
        self.0.push((name, f));
    }

    /// One failing step must not stop notifications, delivery or the others.
    pub fn start(self, every: Duration) {
        std::thread::spawn(move || {
            loop {
                for (name, step) in &self.0 {
                    if let Err(panic) = catch_unwind(AssertUnwindSafe(step)) {
                        eprintln!("brain: step {name} failed: {}", panic_text(&*panic));
                    }
                }
                std::thread::sleep(every);
            }
        });
    }
}
