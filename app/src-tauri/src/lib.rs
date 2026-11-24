//! LitLoop desktop shell: starts the Python backend (litloop.desktop.server) and
//! hands its address and token to the web front end.

use std::io::{BufRead, BufReader};
use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager, RunEvent};

#[derive(Clone, serde::Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

/// The running backend, or why it could not start (shown in the window).
struct Backend {
    ready: Arc<(Mutex<Option<Result<BackendInfo, String>>>, Condvar)>,
    child: Arc<Mutex<Option<Child>>>,
    stopping: Arc<AtomicBool>,
}

#[tauri::command]
async fn backend(state: tauri::State<'_, Backend>) -> Result<BackendInfo, String> {
    let ready = Arc::clone(&state.ready);
    tauri::async_runtime::spawn_blocking(move || {
        let (lock, cvar) = &*ready;
        let mut guard = lock.lock().unwrap();
        while guard.is_none() {
            guard = cvar.wait(guard).unwrap();
        }
        guard.as_ref().unwrap().clone()
    })
    .await
    .map_err(|e| format!("等待 LitLoop 后台就绪失败：{e}"))?
}

/// The interpreter to start the backend with.
///
/// `LITLOOP_PYTHON` wins (the Python environment a developer runs LitLoop
/// from). The installed app ships the official embeddable Python next to its
/// executable, either as a plain `python/` folder beside the .exe or in the
/// bundle's `resources/python/`; both are checked before falling back to
/// whatever `python` PATH has.
fn resolve_python() -> String {
    if let Ok(p) = std::env::var("LITLOOP_PYTHON") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for rel in ["python/python.exe", "resources/python/python.exe"] {
                let cand = dir.join(rel);
                if cand.is_file() {
                    return cand.to_string_lossy().into_owned();
                }
            }
        }
    }
    "python".into()
}

/// Start `python -m litloop.desktop.server` and wait for the line that names its port.
fn start_backend(
    child_slot: Arc<Mutex<Option<Child>>>,
    stopping: Arc<AtomicBool>,
) -> Result<BackendInfo, String> {
    if stopping.load(Ordering::Acquire) {
        return Err("LitLoop 正在退出".into());
    }
    let python = resolve_python();
    let token = uuid::Uuid::new_v4().simple().to_string();
    let mut cmd = Command::new(&python);
    cmd.args(["-m", "litloop.desktop.server", "--token", &token])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window for the backend
    }
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return Err(format!("无法启动 LitLoop 后台（{python}）：{e}")),
    };
    let stdout = child.stdout.take().expect("piped stdout");
    {
        let mut guard = child_slot.lock().unwrap();
        if stopping.load(Ordering::Acquire) {
            let _ = child.kill();
            return Err("LitLoop 正在退出".into());
        }
        *guard = Some(child);
    }
    let mut lines = BufReader::new(stdout).lines();
    let first = lines.next();
    // Keep reading (and dropping) whatever else it prints, so its stdout never fills up or breaks.
    std::thread::spawn(move || for _ in lines {});
    if stopping.load(Ordering::Acquire) {
        if let Some(mut c) = child_slot.lock().unwrap().take() {
            let _ = c.kill();
        }
        return Err("LitLoop 正在退出".into());
    }
    let port = first
        .and_then(|l| l.ok())
        .and_then(|l| serde_json::from_str::<serde_json::Value>(&l).ok())
        .and_then(|v| v.get("port").and_then(|p| p.as_u64()))
        .and_then(|p| u16::try_from(p).ok());
    match port {
        Some(port) => {
            // Confirm uvicorn is actually listening on 127.0.0.1:port before waking the UI.
            let addr = SocketAddr::from(([127, 0, 0, 1], port));
            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                if stopping.load(Ordering::Acquire) {
                    if let Some(mut c) = child_slot.lock().unwrap().take() {
                        let _ = c.kill();
                    }
                    return Err("LitLoop 正在退出".into());
                }
                if let Ok(stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(50)) {
                    drop(stream);
                    break;
                }
                if Instant::now() >= deadline {
                    break;
                }
                std::thread::sleep(Duration::from_millis(15));
            }
            Ok(BackendInfo { port, token })
        }
        None => {
            if let Some(mut c) = child_slot.lock().unwrap().take() {
                let _ = c.kill();
            }
            Err(format!(
                "LitLoop 后台（{python}）没有报告端口；请确认已安装 LitLoop，或用 LITLOOP_PYTHON 指定 Python"
            ))
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let ready = Arc::new((Mutex::new(None), Condvar::new()));
    let child = Arc::new(Mutex::new(None));
    let stopping = Arc::new(AtomicBool::new(false));

    let ready_bg = Arc::clone(&ready);
    let child_bg = Arc::clone(&child);
    let stopping_bg = Arc::clone(&stopping);
    std::thread::spawn(move || {
        let result = start_backend(child_bg, stopping_bg);
        let (lock, cvar) = &*ready_bg;
        *lock.lock().unwrap() = Some(result);
        cvar.notify_all();
    });

    let app = tauri::Builder::default()
        // Registered first: a link clicked while LitLoop is open must reach the
        // running window instead of starting a second copy.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            for arg in argv.iter().filter(|a| a.starts_with("litloop://")) {
                let _ = app.emit("litloop-url", arg.clone());
            }
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_deep_link::init())
        .manage(Backend {
            ready,
            child,
            stopping,
        })
        .invoke_handler(tauri::generate_handler![backend])
        .build(tauri::generate_context!())
        .expect("error while building the LitLoop app");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let backend = handle.state::<Backend>();
            backend.stopping.store(true, Ordering::Release);
            if let Some(mut child) = backend.child.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
