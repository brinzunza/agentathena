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
    reply.starts_with("HTTP/1.") && reply.contains(" 200") && reply.contains("healthy")
}

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
                    if started.elapsed() > START_TIMEOUT {
                        return fail("the backend didn't start. see ~/Library/Logs/Athena-backend.log");
                    }
                    thread::sleep(Duration::from_millis(250));
                }
                let url = format!("http://127.0.0.1:{port}");
                let _ = window.navigate(url.parse().expect("a valid local address"));
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
