"""Starting Ollama from the AI provider screen. Nothing real is started: processes and the server are faked."""

import threading

import pytest
from fastapi.testclient import TestClient

import main
import ollama_runner as runner


class Clock:
    """Time that only moves when something sleeps, so waiting costs nothing."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Process:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


def start(running, launcher=lambda: ["ollama", "serve"], url="http://localhost:11434", spawn=None, wait=10, process=None):
    clock = Clock()
    spawned = []

    def fake_spawn(command):
        spawned.append(command)
        return process or Process()

    result = runner.start(wait, 0.5, running=running, launcher=launcher, url=lambda: url,
                          spawn=spawn or fake_spawn, sleep=clock.sleep, clock=clock)
    return result, spawned, clock


def comes_up_after(polls):
    """A server that answers on the n-th check."""
    calls = {"n": 0}

    def running():
        calls["n"] += 1
        return calls["n"] > polls

    return running


class TestStarting:
    def test_when_it_is_already_running_nothing_is_started(self):
        result, spawned, _ = start(lambda: True)
        assert result == {"running": True, "started": False, "code": "already_running", "message": "ollama is already running"}
        assert spawned == []

    def test_it_starts_the_launcher_and_waits_until_ollama_answers(self):
        result, spawned, clock = start(comes_up_after(4))
        assert spawned == [["ollama", "serve"]]
        assert (result["running"], result["started"], result["code"]) == (True, True, "started")
        assert 0 < clock.now < 10                  # it polled rather than waiting the whole time

    def test_the_mac_app_launcher_is_used_as_given(self):
        _, spawned, _ = start(comes_up_after(1), launcher=lambda: ["open", "-a", "Ollama"])
        assert spawned == [["open", "-a", "Ollama"]]

    def test_it_gives_up_after_the_wait_and_says_where_to_look(self):
        result, _, clock = start(lambda: False, wait=6)
        assert (result["running"], result["code"]) == (False, "timeout")
        assert "6 seconds" in result["message"] and "Athena-ollama.log" in result["message"]
        assert clock.now >= 6

    def test_a_server_that_exits_at_once_fails_immediately_not_after_the_wait(self):
        result, _, clock = start(lambda: False, process=Process(exit_code=1), wait=20)
        assert result["code"] == "failed" and "exit 1" in result["message"]
        assert clock.now < 1

    def test_open_exiting_with_zero_is_not_a_failure_the_app_is_still_launching(self):
        # `open -a Ollama` returns straight away; Ollama itself takes a few seconds
        result, _, _ = start(comes_up_after(3), process=Process(exit_code=0), launcher=lambda: ["open", "-a", "Ollama"])
        assert result["code"] == "started"

    def test_not_installed_says_where_to_get_it(self):
        result, spawned, _ = start(lambda: False, launcher=lambda: None)
        assert (result["running"], result["code"]) == (False, "not_installed")
        assert "ollama.com" in result["message"] and spawned == []

    def test_a_command_that_cannot_run_is_reported_not_raised(self):
        def refuse(command):
            raise PermissionError("not executable")

        result, _, _ = start(lambda: False, spawn=refuse)
        assert result["code"] == "failed" and "not executable" in result["message"]

    @pytest.mark.parametrize("url", ["http://ollama.example.com:11434", "http://192.168.1.20:11434", "https://gpu-box:11434"])
    def test_it_never_tries_to_start_an_ollama_on_another_machine(self, url):
        result, spawned, _ = start(lambda: False, url=url)
        assert (result["running"], result["code"]) == (False, "remote_host")
        assert url in result["message"] and spawned == []

    @pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.0.0.1:11434", "http://0.0.0.0:11434", "http://[::1]:11434"])
    def test_local_addresses_are_started(self, url):
        _, spawned, _ = start(comes_up_after(1), url=url)
        assert len(spawned) == 1

    def test_the_lock_is_released_after_a_failure_so_it_can_be_tried_again(self):
        start(lambda: False, wait=1)
        result, spawned, _ = start(comes_up_after(1))
        assert result["code"] == "started" and len(spawned) == 1


class TestPressingTwice:
    def test_a_second_press_while_starting_waits_instead_of_starting_it_again(self):
        inside = threading.Event()
        release = threading.Event()
        spawned = []

        def slow_spawn(command):
            spawned.append(command)
            inside.set()
            release.wait(5)
            return Process()

        state = {"up": False}
        first_result = {}

        def first():
            first_result["r"] = runner.start(5, 0.01, running=lambda: state["up"], launcher=lambda: ["ollama", "serve"],
                                              url=lambda: "http://localhost:11434", spawn=slow_spawn)

        thread = threading.Thread(target=first)
        thread.start()
        assert inside.wait(5)                      # the first press is now mid-start
        second = runner.start(0.3, 0.01, running=lambda: state["up"], launcher=lambda: ["ollama", "serve"],
                              url=lambda: "http://localhost:11434", spawn=slow_spawn)
        assert second["code"] == "timeout"         # it only waited
        assert spawned == [["ollama", "serve"]]    # and did not launch a second Ollama
        state["up"] = True
        release.set()
        thread.join(5)
        assert first_result["r"]["code"] == "started"


class TestFindingOllama:
    def test_the_mac_app_is_preferred_when_installed(self, monkeypatch):
        monkeypatch.setattr(runner.sys, "platform", "darwin")
        monkeypatch.setattr(runner.os.path, "isdir", lambda p: p == "/Applications/Ollama.app")
        assert runner.find_launcher() == ["open", "-a", "Ollama"]

    def test_otherwise_the_binary_on_the_path_is_served(self, monkeypatch):
        monkeypatch.setattr(runner.sys, "platform", "linux")
        monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/ollama")
        assert runner.find_launcher() == ["/usr/bin/ollama", "serve"]

    def test_a_minimal_path_still_finds_the_usual_install_locations(self, monkeypatch):
        monkeypatch.setattr(runner.sys, "platform", "linux")
        monkeypatch.setattr(runner.shutil, "which", lambda name: None)
        monkeypatch.setattr(runner.os.path, "isfile", lambda p: p == "/opt/homebrew/bin/ollama")
        assert runner.find_launcher() == ["/opt/homebrew/bin/ollama", "serve"]

    def test_nothing_installed_gives_none(self, monkeypatch):
        monkeypatch.setattr(runner.sys, "platform", "linux")
        monkeypatch.setattr(runner.shutil, "which", lambda name: None)
        monkeypatch.setattr(runner.os.path, "isfile", lambda p: False)
        monkeypatch.setattr(runner.os.path, "isdir", lambda p: False)
        assert runner.find_launcher() is None

    def test_the_command_never_contains_anything_from_a_request(self):
        # find_launcher takes no arguments at all: there is nothing a caller could put in the command
        import inspect
        assert inspect.signature(runner.find_launcher).parameters == {}


class TestTheEndpoint:
    def test_it_returns_the_result_even_when_starting_fails(self, monkeypatch):
        monkeypatch.setattr(main.ollama_runner, "start", lambda: {"running": False, "started": False, "code": "not_installed",
                                                                   "message": "Ollama isn't installed."})
        reply = TestClient(main.app).post("/settings/ollama/start")
        assert reply.status_code == 200 and reply.json()["code"] == "not_installed"

    def test_the_endpoint_takes_no_body_and_no_parameters(self, monkeypatch):
        calls = []
        monkeypatch.setattr(main.ollama_runner, "start", lambda: calls.append(1) or {"running": True, "started": False,
                                                                                      "code": "already_running", "message": "x"})
        reply = TestClient(main.app).post("/settings/ollama/start", json={"command": "rm -rf /", "extra": 1})
        assert reply.status_code == 200 and calls == [1]

    def test_the_unreachable_message_points_at_the_button(self):
        code, message = main.classify_llm_error(RuntimeError("failed to connect to ollama"))
        assert code == "ollama_unreachable" and "run ollama" in message
