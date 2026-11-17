#!/bin/bash

# Run Athena as one process: build the web app if it is missing or out of date,
# then serve it and the API together on http://127.0.0.1:8000 (this machine only).
# For working on the interface, use start_backend.sh and start_frontend.sh instead.

cd "$(dirname "$0")"
PORT="${PORT:-8000}"

if [ ! -f frontend/build/index.html ] || [ -n "$(find frontend/src frontend/public frontend/package.json -newer frontend/build/index.html -print -quit)" ]; then
    echo "building the web app..."
    (cd frontend && { [ -d node_modules ] || npm install; } && npm run build) || exit 1
fi

# Use the project's virtual environment when it has every package the backend imports (see
# backend/env_check.py), else the system python. A virtual environment that predates a dependency
# can import fastapi and still fail on the first message that needs the missing package.
PYTHON=python3
if [ -x "$PWD/backend_env/bin/python" ]; then
    if ! "$PWD/backend_env/bin/python" -c pass 2>/dev/null; then
        echo "note: backend_env's Python doesn't run (the Python it was made from was moved or removed), so it is being skipped."
        echo "      rebuild it with: python3 -m venv --clear backend_env && backend_env/bin/pip install -r backend/requirements.txt"
    else
        # An environment that predates a dependency is brought up to date: only what is missing is installed,
        # and only into this folder's own virtual environment, never into the system Python
        if ! "$PWD/backend_env/bin/python" backend/env_check.py --quiet 2>/dev/null; then
            echo "backend_env is missing packages; installing them..."
            "$PWD/backend_env/bin/python" backend/env_check.py --repair --quiet 2>&1 | sed 's/^/      /'
        fi
        if "$PWD/backend_env/bin/python" backend/env_check.py --quiet 2>/dev/null; then
            PYTHON="$PWD/backend_env/bin/python"
        else
            echo "note: backend_env is still missing packages, so it is being skipped:"
            "$PWD/backend_env/bin/python" backend/env_check.py 2>&1 | sed 's/^/      /'
        fi
    fi
fi
if ! "$PYTHON" backend/env_check.py; then
    echo "install what is missing with: $PYTHON -m pip install -r backend/requirements.txt"
    exit 1
fi

echo "athena is running at http://127.0.0.1:${PORT}"
cd backend
exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
