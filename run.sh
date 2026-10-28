#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo "creating venv..."
  "$PY" -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi
if [ -f .env ]; then set -a; . ./.env; set +a; fi
echo "MasteryLoop -> http://localhost:${PORT:-8000}"
echo "demo shortcut -> http://localhost:${PORT:-8000}/?autostart=recursion"
exec ./.venv/bin/python app.py
