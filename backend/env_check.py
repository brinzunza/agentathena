"""
Does this Python have every package the backend imports?

The launchers (start_app.sh and the desktop app) run this to choose which Python to start the
backend with. The project's virtual environment is preferred, but only if it is complete: one that
predates a dependency (it can import fastapi yet lack langchain_anthropic) would otherwise start
fine and then fail on the first message that needs the missing package.

    python env_check.py            exit 0 if complete; otherwise exit 1 and name what is missing
    python env_check.py --quiet    the same, printing nothing
    python env_check.py --repair   install what is missing, if this is the project's virtual environment
                                   (never into a system Python); exit 0 once it is complete

Presence is checked with find_spec, which doesn't import anything, so this takes a fraction of a second.
"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile

# Every third-party package imported by app/ and backend/main.py (the Streamlit front end in
# app/main.py is separate). tests/test_env_check.py fails if one is added without being listed here.
REQUIRED = (
    "chromadb", "ddgs", "dotenv", "fastapi", "httpx", "langchain_anthropic", "langchain_chroma",
    "langchain_community", "langchain_core", "langchain_nomic", "langchain_ollama", "langchain_openai",
    "langchain_text_splitters", "langgraph", "ollama", "pydantic", "pymupdf", "starlette", "uvicorn",
)


# The name each import is installed under (they differ: dotenv is python-dotenv, pymupdf is PyMuPDF)
DISTRIBUTIONS = {
    "chromadb": "chromadb", "ddgs": "ddgs", "dotenv": "python-dotenv", "fastapi": "fastapi", "httpx": "httpx",
    "langchain_anthropic": "langchain-anthropic", "langchain_chroma": "langchain-chroma",
    "langchain_community": "langchain-community", "langchain_core": "langchain-core",
    "langchain_nomic": "langchain-nomic", "langchain_ollama": "langchain-ollama", "langchain_openai": "langchain-openai",
    "langchain_text_splitters": "langchain-text-splitters", "langgraph": "langgraph", "ollama": "ollama",
    "pydantic": "pydantic", "pymupdf": "PyMuPDF", "starlette": "starlette", "uvicorn": "uvicorn",
}
REQUIREMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")


def missing_packages(required=None):
    """The names in `required` (default: REQUIRED) this interpreter cannot import."""
    absent = []
    for name in (REQUIRED if required is None else required):
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            absent.append(name)
    return absent


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def install_specs(absent, requirements_path=REQUIREMENTS):
    """
    What to ask pip for to supply the missing imports: the line from requirements.txt when it lists the package
    (so its pinned version is honoured), else the bare name. Only what is missing: installing the whole file
    could move packages that work to other versions.
    """
    listed = {}
    try:
        with open(requirements_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip()
                name = re.split(r"[=<>!~\[; ]", line, maxsplit=1)[0]
                if name:
                    listed[_normal(name)] = line
    except OSError:
        pass
    specs = []
    for module in absent:
        distribution = DISTRIBUTIONS.get(module, module)
        spec = listed.get(_normal(distribution), distribution)
        if spec not in specs:
            specs.append(spec)
    return specs


def pinned_versions(run) -> "list[str] | None":
    """
    Every package installed here, as `name==version` lines, or None if that can't be listed. Used as a ceiling and
    a floor in one: with these as constraints pip may add packages but may not change one that is installed.
    """
    result = run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return [line.strip() for line in (result.stdout or "").splitlines() if re.fullmatch(r"[A-Za-z0-9_.\-]+==[^\s]+", line.strip())]


def repair(run=None, out=None, requirements_path=REQUIREMENTS) -> bool:
    """
    Install the packages this Python is missing, and say whether it is now complete.

    Only inside a virtual environment: the project's own backend_env is Athena's to fix, a system Python is not.
    And only by adding: a missing package often wants newer versions of ones that already work (langchain-anthropic
    wanted langchain-core 1.x, which breaks the langchain packages that were installed), so everything installed
    is held at its version. If the missing package can't be had on those terms, nothing is installed.
    """
    run = run or subprocess.run         # looked up now, not when the function was defined
    out = out or sys.stderr
    absent = missing_packages()
    if not absent:
        return True
    if not in_virtualenv():
        print(f"{sys.executable} is not a virtual environment, so nothing was installed into it.", file=out)
        return False
    specs = install_specs(absent, requirements_path)
    pins = pinned_versions(run)
    if pins is None and run([sys.executable, "-m", "ensurepip", "--upgrade"]).returncode == 0:
        pins = pinned_versions(run)         # an environment can lack pip itself; put it back once
    if pins is None:
        print("couldn't list what is installed, so nothing was changed.", file=out)
        return False
    print(f"installing into {sys.prefix}, leaving everything already installed as it is: {', '.join(specs)}", file=out)
    with tempfile.TemporaryDirectory() as folder:
        constraints = os.path.join(folder, "installed.txt")
        with open(constraints, "w", encoding="utf-8") as f:
            f.write("\n".join(pins) + "\n")
        result = run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--retries", "1",
                      "--timeout", "20", "--constraint", constraints, *specs])
    if result.returncode != 0:
        print("installing failed: no internet connection, or what is missing can't be added without changing "
              "packages that are installed. nothing was changed.", file=out)
        return False
    return not missing_packages()


def main(argv):
    absent = missing_packages()
    if absent and "--repair" in argv:
        repair()
        absent = missing_packages()
    if absent and "--quiet" not in argv:
        print(f"{sys.executable} is missing: {', '.join(absent)}", file=sys.stderr)
    return 1 if absent else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
