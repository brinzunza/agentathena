"""backend/env_check.py: the launchers use it to pick a Python that has everything the backend imports."""

import ast
import glob
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

import env_check  # noqa: E402
import main  # noqa: E402


def third_party_imports():
    """Top-level names imported by the backend that are neither standard library nor the project's own."""
    local = {os.path.basename(f)[:-3] for f in glob.glob(os.path.join(ROOT, "app", "*.py")) + glob.glob(os.path.join(ROOT, "backend", "*.py"))}
    found = set()
    sources = [f for f in glob.glob(os.path.join(ROOT, "app", "*.py")) if os.path.basename(f) != "main.py"]   # app/main.py is Streamlit
    sources.append(os.path.join(ROOT, "backend", "main.py"))
    for path in sources:
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            found.update(name.split(".")[0] for name in names)
    return {name for name in found if name not in sys.stdlib_module_names and name not in local}


def test_every_package_the_backend_imports_is_on_the_list():
    # Otherwise an environment missing it would be chosen, and fail only when the code path runs
    assert third_party_imports() - set(env_check.REQUIRED) == set()


def test_nothing_on_the_list_is_unused():
    assert set(env_check.REQUIRED) - third_party_imports() == set()


def test_this_environment_is_complete():
    assert env_check.missing_packages() == []


def test_a_package_that_does_not_exist_is_reported():
    assert env_check.missing_packages(["json", "surely_not_a_real_package_xyz"]) == ["surely_not_a_real_package_xyz"]


def test_a_dotted_or_empty_name_is_reported_missing_rather_than_crashing():
    assert env_check.missing_packages(["os.not_a_submodule.deeper", ""]) == ["os.not_a_submodule.deeper", ""]
    assert env_check.missing_packages(["json"]) == []


def test_the_default_list_is_read_when_called_not_when_defined(monkeypatch):
    monkeypatch.setattr(env_check, "REQUIRED", ("surely_not_a_real_package_xyz",))
    assert env_check.missing_packages() == ["surely_not_a_real_package_xyz"]


def run_check(*args, required=None):
    code = "import sys, env_check; " + (f"env_check.REQUIRED = {tuple(required)!r}; " if required is not None else "") \
        + "sys.exit(env_check.main(sys.argv[1:]))"
    return subprocess.run([sys.executable, "-c", code, *args], cwd=os.path.join(ROOT, "backend"),
                          capture_output=True, text=True)


def test_exit_code_is_zero_when_complete_and_one_when_not():
    assert run_check().returncode == 0
    assert run_check(required=["json", "surely_not_a_real_package_xyz"]).returncode == 1


def test_it_names_what_is_missing_and_which_python_is_missing_it():
    result = run_check(required=["surely_not_a_real_package_xyz"])
    assert "surely_not_a_real_package_xyz" in result.stderr and sys.executable in result.stderr


def test_quiet_prints_nothing():
    result = run_check("--quiet", required=["surely_not_a_real_package_xyz"])
    assert result.returncode == 1 and result.stdout == "" and result.stderr == ""


def test_running_it_as_a_script_exits_cleanly():
    result = subprocess.run([sys.executable, os.path.join(ROOT, "backend", "env_check.py"), "--quiet"], capture_output=True)
    assert result.returncode == 0


def test_the_launcher_script_uses_it():
    script = open(os.path.join(ROOT, "start_app.sh"), encoding="utf-8").read()
    assert "backend/env_check.py" in script
    assert '-c "import fastapi"' not in script       # the old check, which let an incomplete environment through


class TestTheErrorYouSee:
    """What the chat shows when the backend's Python lacks a package."""

    def test_a_missing_module_is_not_reported_as_a_provider_problem(self):
        code, message = main.classify_llm_error(ModuleNotFoundError("No module named 'langchain_anthropic'", name="langchain_anthropic"))
        assert code == "missing_package"
        assert "langchain_anthropic" in message
        assert sys.executable in message and "pip install" in message and "restart" in message

    def test_it_works_from_the_text_when_the_exception_was_re_wrapped(self):
        code, message = main.classify_llm_error(RuntimeError("No module named 'ddgs'"))
        assert code == "missing_package" and "ddgs" in message

    def test_other_errors_are_unaffected(self):
        assert main.classify_llm_error(RuntimeError("connection refused"))[0] == "ollama_unreachable"
        assert main.classify_llm_error(RuntimeError("something odd")) == ("error", "something odd")

    def test_it_reaches_the_chat_as_an_error_event(self, monkeypatch):
        monkeypatch.setattr(main, "run_agent", lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'x'", name="x")))
        client = TestClient(main.app)
        chat = client.post("/chats", json={"name": "env"}).json()["id"]
        try:
            body = client.post(f"/chats/{chat}/qa/chat/stream", json={"content": "hello"}).text
            assert '"code": "missing_package"' in body or '"code":"missing_package"' in body
        finally:
            client.delete(f"/chats/{chat}")
