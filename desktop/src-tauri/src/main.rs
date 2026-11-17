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
use std::path::{Path, PathBuf};
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

/// Python installs of the framework kind (python.org's installer), newest first. Every version
/// folder is listed rather than trusting `Current`, which points at whichever was installed last,
/// not at the one the project's packages were installed for.
fn framework_pythons_in(versions_dir: &Path) -> Vec<PathBuf> {
    let mut found: Vec<((u32, u32), PathBuf)> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(versions_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            let mut parts = name.split('.');
            if let (Some(Ok(major)), Some(Ok(minor))) =
                (parts.next().map(str::parse::<u32>), parts.next().map(str::parse::<u32>))
            {
                found.push(((major, minor), entry.path().join("bin").join("python3")));
            }
        }
    }
    found.sort_by(|a, b| b.0.cmp(&a.0));
    found.into_iter().map(|(_, path)| path).collect()
}

/// The path a shell printed, from output that may have other lines (a profile that echoes
/// something): the last line that is an absolute path.
fn last_absolute_path(output: &str) -> Option<PathBuf> {
    output.lines().rev().map(str::trim).find(|line| line.starts_with('/')).map(PathBuf::from)
}

/// The `python3` the user's own shell would run. An app opened from Finder gets a minimal PATH, where
/// `python3` is the macOS stub rather than the one the packages were installed for, so ask a shell
/// that has read the user's profile. `interactive` also reads ~/.zshrc, where pyenv and conda tend to
/// live; it is slower, so it is only tried after the cheaper places.
fn shell_python(interactive: bool) -> Vec<PathBuf> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
    let flags: &[&str] = if interactive { &["-l", "-i", "-c"] } else { &["-l", "-c"] };
    Command::new(shell)
        .args(flags)
        .arg("command -v python3")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()
        .and_then(|out| last_absolute_path(&String::from_utf8_lossy(&out.stdout)))
        .into_iter()
        .collect()
}

/// The first candidate, in the order the stages give them, that is complete. A bare name (like
/// `python3`) is looked up on PATH, so it is simply tried; a full path that doesn't exist is skipped.
/// Stages are functions so the slower ones only run when the earlier ones found nothing.
fn pick_python(stages: &[Box<dyn Fn() -> Vec<PathBuf>>], is_complete: impl Fn(&PathBuf) -> bool) -> Option<PathBuf> {
    stages.iter().find_map(|stage| {
        stage()
            .into_iter()
            .find(|candidate| (candidate.components().count() == 1 || candidate.exists()) && is_complete(candidate))
    })
}

/// Whether a Python can be used for the backend: it has every package already, or it is the project's own
/// virtual environment and installing what it lacks (`repair`) completes it. Any other Python is never
/// touched: a system Python is not Athena's to change.
fn usable(
    candidate: &PathBuf,
    venv: &PathBuf,
    is_complete: &dyn Fn(&PathBuf) -> bool,
    repair: &dyn Fn(&PathBuf) -> bool,
) -> bool {
    is_complete(candidate) || (candidate == venv && venv.exists() && repair(candidate) && is_complete(candidate))
}

/// Install what the project's virtual environment is missing. The installer's output goes to the
/// log, since a windowed app has no terminal, and it only ever acts inside a virtual environment
/// (backend/env_check.py refuses anything else).
fn repair_venv(python: &PathBuf, check: &Path) -> bool {
    let log = log_path();
    let Ok(file) = OpenOptions::new().create(true).append(true).open(&log) else { return false };
    let Ok(errors) = file.try_clone() else { return false };
    Command::new(python)
        .arg(check)
        .args(["--repair", "--quiet"])
        .stdin(Stdio::null())
        .stdout(file)
        .stderr(errors)
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn log_path() -> PathBuf {
    std::env::var("HOME")
        .map(|home| PathBuf::from(home).join("Library").join("Logs").join("Athena-backend.log"))
        .unwrap_or_else(|_| std::env::temp_dir().join("Athena-backend.log"))
}

/// A Python that has every package the backend imports (backend/env_check.py is the list). The
/// project's virtual environment comes first. One that predates a dependency (it can import
/// fastapi and still lack langchain_anthropic, so it starts fine and fails on the first message
/// that needs it) is brought up to date in place; if that can't be done it is skipped.
fn python(root: &PathBuf, notify: &dyn Fn(&str)) -> Option<PathBuf> {
    let check = root.join("backend").join("env_check.py");
    let venv = root.join("backend_env").join("bin").join("python");
    let venv_stage = venv.clone();
    let stages: Vec<Box<dyn Fn() -> Vec<PathBuf>>> = vec![
        Box::new(move || vec![venv_stage.clone()]),
        Box::new(|| shell_python(false)),
        Box::new(|| framework_pythons_in(Path::new("/Library/Frameworks/Python.framework/Versions"))),
        Box::new(|| vec![PathBuf::from("/opt/homebrew/bin/python3"), PathBuf::from("/usr/local/bin/python3")]),
        Box::new(|| shell_python(true)),
        Box::new(|| vec![PathBuf::from("python3")]),
    ];
    let is_complete = |python: &PathBuf| {
        Command::new(python)
            .arg(&check)
            .arg("--quiet")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    };
    let repair = |python: &PathBuf| {
        notify("updating the packages Athena needs. the first start after an update can take a few minutes…");
        repair_venv(python, &check)
    };
    pick_python(&stages, |python| usable(python, &venv, &is_complete, &repair))
}

fn spawn_backend(root: &PathBuf, port: u16, notify: &dyn Fn(&str)) -> std::io::Result<Child> {
    // Output goes to a log file, since a windowed app has no terminal to show it in
    let log = OpenOptions::new().create(true).append(true).open(log_path())?;
    let python = python(root, notify).ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "no Python with the backend's packages was found. run backend/env_check.py with your Python to see what is missing",
        )
    })?;
    Command::new(python)
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

                // A progress line on the "starting" page, without marking it as failed
                let say = |message: &str| {
                    let _ = window.eval(&format!("document.getElementById('message').textContent={:?}", message));
                };

                if !is_healthy(port) {
                    if !root.join("frontend").join("build").join("index.html").exists() {
                        return fail("the web app hasn't been built. run: cd frontend && npm run build");
                    }
                    match spawn_backend(&root, port, &say) {
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

    fn stage(paths: &[&str]) -> Box<dyn Fn() -> Vec<PathBuf>> {
        let owned: Vec<PathBuf> = paths.iter().map(PathBuf::from).collect();
        Box::new(move || owned.clone())
    }

    #[test]
    fn the_first_complete_python_wins_and_an_incomplete_one_is_skipped() {
        let stages = vec![stage(&["python-in-the-venv"]), stage(&["python3"])];
        assert_eq!(pick_python(&stages, |_| true), Some(PathBuf::from("python-in-the-venv")));
        // the venv predates a dependency: the next one is used instead
        let skip_venv = |python: &PathBuf| python != &PathBuf::from("python-in-the-venv");
        assert_eq!(pick_python(&stages, skip_venv), Some(PathBuf::from("python3")));
    }

    fn venv_path() -> PathBuf {
        // any path that exists: the check for the venv's own existence is part of the rule
        std::env::current_exe().expect("the test binary has a path")
    }

    #[test]
    fn a_complete_python_is_used_without_repairing_anything() {
        let venv = venv_path();
        assert!(usable(&venv, &venv, &|_| true, &|_| panic!("nothing needed repairing")));
    }

    #[test]
    fn an_incomplete_venv_is_repaired_and_then_used() {
        use std::cell::Cell;
        let venv = venv_path();
        let repaired = Cell::new(false);
        // complete only once the repair has run
        let complete = |_: &PathBuf| repaired.get();
        let repair = |_: &PathBuf| { repaired.set(true); true };
        assert!(usable(&venv, &venv, &complete, &repair));
        assert!(repaired.get());
    }

    #[test]
    fn a_repair_that_fails_leaves_the_venv_skipped() {
        let venv = venv_path();
        assert!(!usable(&venv, &venv, &|_| false, &|_| false));
    }

    #[test]
    fn a_repair_that_does_not_complete_it_leaves_the_venv_skipped() {
        let venv = venv_path();
        assert!(!usable(&venv, &venv, &|_| false, &|_| true));
    }

    #[test]
    fn a_python_that_is_not_the_venv_is_never_repaired() {
        let venv = venv_path();
        let other = PathBuf::from("/usr/bin/python3");
        assert!(!usable(&other, &venv, &|_| false, &|_| panic!("a system python must not be changed")));
    }

    #[test]
    fn a_venv_that_is_not_there_is_not_repaired_into_existence() {
        let venv = PathBuf::from("/no/such/dir/backend_env/bin/python");
        assert!(!usable(&venv, &venv, &|_| false, &|_| panic!("there is nothing to repair")));
    }

    #[test]
    fn nothing_is_chosen_when_no_python_is_complete() {
        assert_eq!(pick_python(&[stage(&["python3", "python"])], |_| false), None);
    }

    #[test]
    fn a_full_path_that_does_not_exist_is_not_even_tried() {
        assert_eq!(pick_python(&[stage(&["/no/such/dir/python3"])], |_| true), None);
    }

    #[test]
    fn a_later_stage_only_runs_when_the_earlier_ones_found_nothing() {
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::sync::Arc;
        let slow_stage_ran = Arc::new(AtomicBool::new(false));
        let flag = slow_stage_ran.clone();
        let stages: Vec<Box<dyn Fn() -> Vec<PathBuf>>> = vec![
            stage(&["python3"]),
            Box::new(move || { flag.store(true, Ordering::SeqCst); vec![PathBuf::from("python3")] }),
        ];
        assert!(pick_python(&stages, |_| true).is_some());
        assert!(!slow_stage_ran.load(Ordering::SeqCst), "the slow stage ran although the first one was enough");
    }

    #[test]
    fn framework_versions_are_found_newest_first_and_current_is_not_trusted() {
        let dir = std::env::temp_dir().join(format!("athena-frameworks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        for name in ["3.9", "3.13", "3.12", "Current", "notes.txt"] {
            std::fs::create_dir_all(dir.join(name)).unwrap();
        }
        let found = framework_pythons_in(&dir);
        let names: Vec<String> = found
            .iter()
            .map(|p| p.parent().unwrap().parent().unwrap().file_name().unwrap().to_string_lossy().to_string())
            .collect();
        assert_eq!(names, vec!["3.13", "3.12", "3.9"]);
        assert!(found.iter().all(|p| p.ends_with("bin/python3")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_missing_frameworks_folder_gives_no_candidates() {
        assert!(framework_pythons_in(Path::new("/no/such/frameworks")).is_empty());
    }

    #[test]
    fn the_shells_answer_is_the_last_path_it_printed() {
        let noisy = "welcome back!\n/Library/Frameworks/Python.framework/Versions/3.12/bin/python3\n";
        assert_eq!(
            last_absolute_path(noisy),
            Some(PathBuf::from("/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"))
        );
        assert_eq!(last_absolute_path("/a/python3\n/b/python3\n"), Some(PathBuf::from("/b/python3")));
        assert_eq!(last_absolute_path("python3 not found\n"), None);
        assert_eq!(last_absolute_path(""), None);
    }

    /// Not part of the normal run, since it depends on what is installed here. To check a machine the
    /// way Finder would start the app (no shell PATH): build the tests, then run the binary under
    /// `env -i HOME=$HOME PATH=/usr/bin:/bin SHELL=/bin/zsh <binary> --ignored --nocapture`.
    #[test]
    #[ignore = "depends on the Pythons installed on this machine"]
    fn this_machine_has_a_complete_python_even_with_a_minimal_path() {
        let chosen = python(&project_root(), &|_| {});
        println!("chosen python: {:?}", chosen);
        assert!(chosen.is_some(), "no complete Python found: run backend/env_check.py with yours");
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
