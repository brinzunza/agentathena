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

# Use the project's virtual environment when it has the backend's packages, else the system python
PYTHON=python3
if [ -x "$PWD/backend_env/bin/python" ] && "$PWD/backend_env/bin/python" -c "import fastapi" 2>/dev/null; then
    PYTHON="$PWD/backend_env/bin/python"
fi

echo "athena is running at http://127.0.0.1:${PORT}"
cd backend
exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
