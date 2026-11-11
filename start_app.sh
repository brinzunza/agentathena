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
    if "$PWD/backend_env/bin/python" backend/env_check.py --quiet 2>/dev/null; then
        PYTHON="$PWD/backend_env/bin/python"
    else
        echo "note: backend_env is missing packages, so it is being skipped:"
        "$PWD/backend_env/bin/python" backend/env_check.py 2>&1 | sed 's/^/      /'
    fi
fi
if ! "$PYTHON" backend/env_check.py; then
    echo "install what is missing with: $PYTHON -m pip install -r backend/requirements.txt"
    exit 1
fi

echo "athena is running at http://127.0.0.1:${PORT}"
cd backend
exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
