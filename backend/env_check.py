"""
Does this Python have every package the backend imports?

The launchers (start_app.sh and the desktop app) run this to choose which Python to start the
backend with. The project's virtual environment is preferred, but only if it is complete: one that
predates a dependency (it can import fastapi yet lack langchain_anthropic) would otherwise start
fine and then fail on the first message that needs the missing package.

    python env_check.py            exit 0 if complete; otherwise exit 1 and name what is missing
    python env_check.py --quiet    the same, printing nothing

Presence is checked with find_spec, which doesn't import anything, so this takes a fraction of a second.
"""

import importlib.util
import sys

# Every third-party package imported by app/ and backend/main.py (the Streamlit front end in
# app/main.py is separate). tests/test_env_check.py fails if one is added without being listed here.
REQUIRED = (
    "chromadb", "ddgs", "dotenv", "fastapi", "httpx", "langchain_anthropic", "langchain_chroma",
    "langchain_community", "langchain_core", "langchain_nomic", "langchain_ollama", "langchain_openai",
    "langchain_text_splitters", "langgraph", "ollama", "pydantic", "pymupdf", "uvicorn",
)


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


def main(argv):
    absent = missing_packages()
    if absent and "--quiet" not in argv:
        print(f"{sys.executable} is missing: {', '.join(absent)}", file=sys.stderr)
    return 1 if absent else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
