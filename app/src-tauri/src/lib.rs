//! LitLoop desktop shell: starts the Python backend (litloop.app_server) and
//! hands its address and token to the web front end.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

#[derive(Clone, serde::Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

/// The running backend, or why it could not start (shown in the window).
struct Backend {
    info: Result<BackendInfo, String>,
    child: Mutex<Option<Child>>,
}

#[tauri::command]
fn backend(state: tauri::State<Backend>) -> Result<BackendInfo, String> {
    state.info.clone()
}

/// Start `python -m litloop.app_server` and wait for the line that names its port.
///
/// The interpreter is `LITLOOP_PYTHON` when set (the Python environment
/// LitLoop is installed in), otherwise `python` on PATH. The installed app
/// will ship its own backend instead (TODO: PyInstaller sidecar).
fn start_backend() -> (Result<BackendInfo, String>, Option<Child>) {
    let python = std::env::var("LITLOOP_PYTHON").unwrap_or_else(|_| "python".into());
    let token = uuid::Uuid::new_v4().simple().to_string();
    let mut cmd = Command::new(&python);
    cmd.args(["-m", "litloop.app_server", "--token", &token])
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
        Err(e) => return (Err(format!("无法启动 LitLoop 后台（{python}）：{e}")), None),
    };
    let mut lines = BufReader::new(child.stdout.take().expect("piped stdout")).lines();
    let first = lines.next();
    // Keep reading (and dropping) whatever else it prints, so its stdout never fills up or breaks.
    std::thread::spawn(move || for _ in lines {});
    let port = first
        .and_then(|l| l.ok())
        .and_then(|l| serde_json::from_str::<serde_json::Value>(&l).ok())
        .and_then(|v| v.get("port").and_then(|p| p.as_u64()))
        .and_then(|p| u16::try_from(p).ok());
    match port {
        Some(port) => (Ok(BackendInfo { port, token }), Some(child)),
        None => {
            let _ = child.kill();
            (
                Err(format!(
                    "LitLoop 后台（{python}）没有报告端口；请确认已安装 LitLoop，或用 LITLOOP_PYTHON 指定 Python"
                )),
                None,
            )
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let (info, child) = start_backend();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Backend {
            info,
            child: Mutex::new(child),
        })
        .invoke_handler(tauri::generate_handler![backend])
        .build(tauri::generate_context!())
        .expect("error while building the LitLoop app");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Backend>().child.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
