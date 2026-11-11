"""
Start Ollama for the user, from the AI provider screen.

Ollama is a separate program that this app only talks to; it isn't started with the app. This
starts it when asked, and waits until it answers, so the screen can show its models straight away.

It uses the Ollama Mac app if that is installed (the same as opening it from Applications, so it
also appears in the menu bar), and otherwise runs `ollama serve`. Either way Ollama is left running
afterwards, like any other background service: quitting Athena doesn't stop it.

The command is fixed. Nothing the browser sends is ever part of it, and it only ever starts a
local Ollama: if OLLAMA_HOST points at another machine there is nothing here to start.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, List, Optional
from urllib.parse import urlparse

import llm_config

WAIT_SECONDS = 20.0
POLL_SECONDS = 0.5

# Where Ollama may be installed. A backend started from Finder has a minimal PATH, so `which` alone isn't enough.
MAC_APPS = ("/Applications/Ollama.app", os.path.expanduser("~/Applications/Ollama.app"))
BINARIES = (
    "/usr/local/bin/ollama",
    "/opt/homebrew/bin/ollama",
    "/Applications/Ollama.app/Contents/Resources/ollama",
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", ""}
_starting = threading.Lock()


def log_path() -> str:
    """Where `ollama serve` writes its output when this app starts it."""
    logs = os.path.expanduser("~/Library/Logs")
    directory = logs if sys.platform == "darwin" and os.path.isdir(logs) else tempfile.gettempdir()
    return os.path.join(directory, "Athena-ollama.log")


def is_local(url: str) -> bool:
    """Whether an Ollama address is on this machine, i.e. something this app could start."""
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


def is_running() -> bool:
    return llm_config.list_ollama_models() is not None


def find_launcher() -> Optional[List[str]]:
    """The command that starts Ollama here, or None if it isn't installed."""
    if sys.platform == "darwin" and any(os.path.isdir(app) for app in MAC_APPS):
        return ["open", "-a", "Ollama"]
    binary = shutil.which("ollama") or next((path for path in BINARIES if os.path.isfile(path)), None)
    return [binary, "serve"] if binary else None


def _result(running: bool, code: str, message: str, started: bool = False) -> dict:
    return {"running": running, "started": started, "code": code, "message": message}


def start(
    wait_seconds: float = WAIT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    *,
    running: Callable[[], bool] = None,
    launcher: Callable[[], Optional[List[str]]] = None,
    url: Callable[[], str] = None,
    spawn: Callable = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """
    Start Ollama if it isn't running, and wait for it to answer.

    Returns {running, started, code, message}. `code` is one of: already_running, started,
    remote_host, not_installed, failed (the process stopped at once), timeout (it didn't answer in
    time). Failing is a normal answer, not an exception: the screen shows `message`.
    The keyword arguments are for tests.
    """
    running = running or is_running
    launcher = launcher or find_launcher
    url = url or llm_config._ollama_url
    spawn = spawn or _spawn

    if running():
        return _result(True, "already_running", "ollama is already running")
    if not is_local(url()):
        return _result(False, "remote_host",
                       f"OLLAMA_HOST points at another machine ({url()}), so there is nothing here to start. "
                       "start Ollama on that machine.")

    # A second press while one start is under way waits for it instead of starting Ollama twice
    first = _starting.acquire(blocking=False)
    process = None
    try:
        if first:
            command = launcher()
            if not command:
                return _result(False, "not_installed",
                               "Ollama isn't installed. get it from ollama.com, then press this again.")
            try:
                process = spawn(command)
            except OSError as e:
                return _result(False, "failed", f"couldn't start Ollama: {e}")

        deadline = clock() + wait_seconds
        while clock() < deadline:
            if running():
                return _result(True, "started", "ollama is running", started=True)
            if process is not None and process.poll() not in (None, 0):
                # `open -a` exits straight away with 0; a real server that stops is a failure
                return _result(False, "failed",
                               f"Ollama stopped as soon as it started (exit {process.poll()}). see {log_path()}")
            sleep(poll_seconds)
        return _result(False, "timeout", f"Ollama didn't answer within {int(wait_seconds)} seconds. see {log_path()}")
    finally:
        if first:
            _starting.release()


def _spawn(command: List[str]):
    """Run the command, detached so Ollama outlives this process, with its output in a log file."""
    log = open(log_path(), "ab")
    try:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    finally:
        log.close()
