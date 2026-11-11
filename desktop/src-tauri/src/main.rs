// Athena as a desktop window.
//
// The window is only a shell around the local backend, which already serves
// the web app (frontend/build) and the API on one port. This starts that
// backend if it isn't already running, shows a small "starting" page while it
// loads, then points the window at it, and stops the backend again on quit.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

const START_TIMEOUT: Duration = Duration::from_secs(90);
/// How often the running backend is checked, and how many checks in a row may fail before the
/// window says it has stopped (one slow answer isn't a dead backend).
const WATCH_INTERVAL: Duration = Duration::from_secs(3);
const WATCH_MISSES: u32 = 3;
const LOG_HINT: &str = "see ~/Library/Logs/Athena-backend.log";

/// The backend this app started, if it had to. A backend that was already
/// running is left alone, here and on quit.
struct Backend(Mutex<Option<Child>>);

fn port() -> u16 {
    std::env::var("ATHENA_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8000)
}

/// The project folder: ATHENA_HOME, else the repository this app was built from.
fn project_root() -> PathBuf {
    std::env::var("ATHENA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join(".."))
}

/// Whether a reply to GET /health is a healthy one.
fn reply_is_healthy(reply: &str) -> bool {
    reply.starts_with("HTTP/1.") && reply.contains(" 200") && reply.contains("healthy")
}

/// What the window says when the backend exited while it was being waited for.
fn exited_message(status: &str) -> String {
    format!("the backend stopped while starting ({status}). {LOG_HINT}")
}

/// Whether the backend answers /health. A bare TCP request, so no HTTP library is needed.
fn is_healthy(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(800)));
    if stream.write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").is_err() {
        return false;
    }
    let mut reply = String::new();
    let _ = stream.read_to_string(&mut reply);
    reply_is_healthy(&reply)
}

/// How the backend this app started has exited, if it has. None while it runs, or when this app
/// didn't start it (one that was already running is left alone, so there is nothing to ask).
fn exit_of_started_backend<R: tauri::Runtime>(handle: &tauri::AppHandle<R>) -> Option<String> {
    let state = handle.state::<Backend>();
    let mut guard = state.0.lock().unwrap();
    match guard.as_mut()?.try_wait() {
        Ok(Some(status)) => Some(status.to_string()),
        _ => None,
    }
}

/// Covers the app with a notice. Used when the backend goes away after the app has loaded, since
/// otherwise the window would just stop responding.
const STOPPED_NOTICE: &str = r#"(function () {
  if (document.getElementById('athena-stopped')) return;
  var box = document.createElement('div');
  box.id = 'athena-stopped';
  box.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;'
    + 'align-items:center;justify-content:center;gap:10px;padding:24px;text-align:center;'
    + 'background:#3b3b3b;color:#f0f0f0;font:15px Arial,sans-serif';
  var title = document.createElement('strong');
  title.textContent = 'athena has stopped';
  var body = document.createElement('span');
  body.textContent = 'the backend is no longer running. quit and open Athena again. your chats are saved.';
  box.appendChild(title);
  box.appendChild(body);
  document.body.appendChild(box);
})();"#;

/// The project's virtual environment if it has the backend's packages, else the system python.
fn python(root: &PathBuf) -> PathBuf {
    let venv = root.join("backend_env").join("bin").join("python");
    if venv.exists()
        && Command::new(&venv).args(["-c", "import fastapi"]).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false)
    {
        return venv;
    }
    PathBuf::from("python3")
}

fn spawn_backend(root: &PathBuf, port: u16) -> std::io::Result<Child> {
    // Output goes to a log file, since a windowed app has no terminal to show it in
    let log_path = std::env::var("HOME")
        .map(|home| PathBuf::from(home).join("Library").join("Logs").join("Athena-backend.log"))
        .unwrap_or_else(|_| std::env::temp_dir().join("Athena-backend.log"));
    let log = OpenOptions::new().create(true).append(true).open(log_path)?;
    Command::new(python(root))
        .args(["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", &port.to_string()])
        .current_dir(root.join("backend"))
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log)
        .spawn()
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let window = app.get_webview_window("main").expect("the main window is declared in tauri.conf.json");
            let handle = app.handle().clone();
            let port = port();
            let root = project_root();

            thread::spawn(move || {
                let fail = |message: &str| {
                    let _ = window.eval(&format!(
                        "document.body.dataset.state='failed';document.getElementById('message').textContent={:?}",
                        message
                    ));
                };

                if !is_healthy(port) {
                    if !root.join("frontend").join("build").join("index.html").exists() {
                        return fail("the web app hasn't been built. run: cd frontend && npm run build");
                    }
                    match spawn_backend(&root, port) {
                        Ok(child) => *handle.state::<Backend>().0.lock().unwrap() = Some(child),
                        Err(e) => return fail(&format!("couldn't start the backend: {e}")),
                    }
                }

                let started = Instant::now();
                while !is_healthy(port) {
                    // A backend that crashed on the way up (a taken port, a missing package) will
                    // never answer, so don't wait out the timeout for it
                    if let Some(status) = exit_of_started_backend(&handle) {
                        return fail(&exited_message(&status));
                    }
                    if started.elapsed() > START_TIMEOUT {
                        return fail(&format!("the backend didn't start. {LOG_HINT}"));
                    }
                    thread::sleep(Duration::from_millis(250));
                }
                let url = format!("http://127.0.0.1:{port}");
                let _ = window.navigate(url.parse().expect("a valid local address"));

                // From here the window is the app itself. If its backend goes away, say so.
                let mut misses = 0;
                loop {
                    thread::sleep(WATCH_INTERVAL);
                    if is_healthy(port) {
                        misses = 0;
                        continue;
                    }
                    misses += 1;
                    if misses >= WATCH_MISSES {
                        let _ = window.eval(STOPPED_NOTICE);
                        break;
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the app");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Backend>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_healthy_reply_is_recognised() {
        assert!(reply_is_healthy("HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"status\":\"healthy\"}"));
        assert!(reply_is_healthy("HTTP/1.0 200 OK\r\n\r\n{\"status\": \"healthy\"}"));
    }

    #[test]
    fn anything_else_is_not() {
        assert!(!reply_is_healthy(""));
        assert!(!reply_is_healthy("HTTP/1.1 500 Internal Server Error\r\n\r\nhealthy"));
        assert!(!reply_is_healthy("HTTP/1.1 200 OK\r\n\r\n{\"status\":\"starting\"}"));
        assert!(!reply_is_healthy("<html>some other server on this port</html>"));
    }

    #[test]
    fn the_exit_message_says_how_it_exited_and_where_to_look() {
        let message = exited_message("exit status: 1");
        assert!(message.contains("exit status: 1"));
        assert!(message.contains("Athena-backend.log"));
    }

    #[test]
    fn the_notice_is_plain_text_built_with_the_dom_not_html() {
        assert!(STOPPED_NOTICE.contains("textContent"));
        assert!(!STOPPED_NOTICE.contains("innerHTML"));
    }

    #[test]
    fn the_watcher_waits_for_several_misses_before_giving_up() {
        assert!(WATCH_MISSES >= 2);
        assert!(WATCH_INTERVAL >= Duration::from_secs(1));
    }
}
