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


# --- repairing the project's virtual environment ---

class Result:
    def __init__(self, code=0, stdout=""):
        self.returncode, self.stdout = code, stdout


class Pip:
    """Stands in for subprocess.run: records the commands, answers `pip freeze`, and 'installs' by making the package importable."""

    def __init__(self, env_check, codes=(0,), fixes=True, freeze="anyio==3.7.1\nlangchain-core==0.3.86\n-e git+x#egg=y\nweird @ file:///x\n",
                 freeze_code=0):
        self.env_check, self.commands, self.codes, self.fixes = env_check, [], list(codes), fixes
        self.freeze, self.freeze_code, self.constraints = freeze, freeze_code, None

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if "freeze" in command:
            return Result(self.freeze_code, self.freeze)
        code = self.codes.pop(0) if self.codes else 0
        if "--constraint" in command:
            self.constraints = open(command[command.index("--constraint") + 1], encoding="utf-8").read()
        if code == 0 and self.fixes and "install" in command and "pip" in command:
            self.env_check.REQUIRED = ()
        return Result(code)

    def installs(self):
        return [c for c in self.commands if "install" in c and "pip" in c]


@pytest.fixture
def env(monkeypatch):
    import env_check
    monkeypatch.setattr(env_check, "REQUIRED", ("surely_not_a_real_package_xyz",))
    monkeypatch.setattr(env_check, "DISTRIBUTIONS", {"surely_not_a_real_package_xyz": "not-real-xyz"})
    monkeypatch.setattr(env_check, "in_virtualenv", lambda: True)
    return env_check


class TestRepair:
    def test_every_required_import_has_a_distribution_name(self):
        import env_check
        assert set(env_check.REQUIRED) <= set(env_check.DISTRIBUTIONS)

    def test_the_names_that_differ_from_the_import_are_right(self):
        import env_check
        assert env_check.DISTRIBUTIONS["dotenv"] == "python-dotenv" and env_check.DISTRIBUTIONS["pymupdf"] == "PyMuPDF"

    def test_it_installs_what_is_missing_and_says_it_is_now_complete(self, env):
        pip = Pip(env)
        assert env.repair(run=pip, out=open(os.devnull, "w")) is True
        assert pip.installs()[0][1:4] == ["-m", "pip", "install"] and "not-real-xyz" in pip.installs()[0]

    def test_it_installs_only_what_is_missing_never_the_whole_file(self, env):
        pip = Pip(env)
        env.repair(run=pip, out=open(os.devnull, "w"))
        assert "-r" not in pip.installs()[0]

    def test_everything_already_installed_is_held_at_its_version(self, env):
        # A missing package once wanted langchain-core 1.x, which broke the langchain packages that were working
        pip = Pip(env)
        env.repair(run=pip, out=open(os.devnull, "w"))
        assert "--constraint" in pip.installs()[0]
        assert pip.constraints.split() == ["anyio==3.7.1", "langchain-core==0.3.86"]      # only name==version lines are usable

    def test_if_the_installed_packages_cannot_be_listed_nothing_is_changed(self, env):
        pip = Pip(env, freeze_code=1)
        assert env.repair(run=pip, out=open(os.devnull, "w")) is False and pip.installs() == []

    def test_a_missing_pip_is_put_back_once_so_the_installed_can_be_listed(self, env):
        class NoPipAtFirst(Pip):
            def __call__(self, command, **kwargs):
                if "freeze" in command and not any("ensurepip" in c for c in self.commands):
                    self.commands.append(command)
                    return Result(1)
                return super().__call__(command, **kwargs)

        pip = NoPipAtFirst(env)
        assert env.repair(run=pip, out=open(os.devnull, "w")) is True
        assert any("ensurepip" in c for c in pip.commands)

    def test_a_package_requirements_does_not_list_is_installed_by_name(self, env, tmp_path):
        reqs = tmp_path / "r.txt"
        reqs.write_text("other==1\n")
        assert env.install_specs(["surely_not_a_real_package_xyz"], str(reqs)) == ["not-real-xyz"]

    def test_a_pinned_version_in_requirements_is_honoured_however_the_name_is_spelled(self, env, tmp_path):
        reqs = tmp_path / "r.txt"
        reqs.write_text("Not_Real.XYZ==3.1  # why\n")
        assert env.install_specs(["surely_not_a_real_package_xyz"], str(reqs)) == ["Not_Real.XYZ==3.1"]

    def test_extras_in_requirements_lines_do_not_hide_the_package(self, tmp_path):
        import env_check
        reqs = tmp_path / "r.txt"
        reqs.write_text("uvicorn[standard]==0.24.0\n")
        assert env_check.install_specs(["uvicorn"], str(reqs)) == ["uvicorn[standard]==0.24.0"]

    def test_nothing_missing_means_nothing_is_run(self, env):
        env.REQUIRED = ()
        pip = Pip(env)
        assert env.repair(run=pip) is True and pip.commands == []

    def test_a_system_python_is_never_touched(self, env, monkeypatch):
        monkeypatch.setattr(env, "in_virtualenv", lambda: False)
        pip = Pip(env)
        assert env.repair(run=pip, out=open(os.devnull, "w")) is False and pip.commands == []

    def test_a_failed_install_is_reported_not_raised(self, env):
        pip = Pip(env, codes=(1,))
        assert env.repair(run=pip, out=open(os.devnull, "w")) is False

    def test_what_cannot_be_added_without_changing_other_packages_is_not_installed_at_all(self, env, capsys):
        env.repair(run=Pip(env, codes=(1,)))
        assert "nothing was changed" in capsys.readouterr().err

    def test_an_install_that_leaves_it_incomplete_is_not_called_complete(self, env):
        assert env.repair(run=Pip(env, fixes=False), out=open(os.devnull, "w")) is False

    def test_the_command_line_flag_repairs_then_reports(self, env, monkeypatch):
        monkeypatch.setattr(env.subprocess, "run", Pip(env))
        assert env.main(["--repair", "--quiet"]) == 0

    def test_without_the_flag_nothing_is_installed(self, env, monkeypatch):
        pip = Pip(env)
        monkeypatch.setattr(env.subprocess, "run", pip)
        assert env.main(["--quiet"]) == 1 and pip.commands == []

    def test_both_launchers_use_it(self):
        script = open(os.path.join(ROOT, "start_app.sh"), encoding="utf-8").read()
        assert "--repair" in script
        launcher = open(os.path.join(ROOT, "desktop", "src-tauri", "src", "main.rs"), encoding="utf-8").read()
        assert '"--repair"' in launcher
