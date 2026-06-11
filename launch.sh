#!/usr/bin/env bash
# MGS1 Undub Studio launcher (Linux / macOS).
# Creates a virtualenv on first run, installs/updates dependencies only when
# they change, then starts the GUI. All arguments are passed through, e.g.:
#   ./launch.sh --host 0.0.0.0 --port 9000
set -e
cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: Python not found. Install Python 3.10+ first." >&2
    exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERROR: Python 3.10+ is required (found: $("$PY" --version 2>&1))." >&2
    exit 1
fi

VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
    echo "First run — creating virtualenv in $VENV ..."
    "$PY" -m venv "$VENV"
fi

REQS=gui/requirements.txt
STAMP="$VENV/.requirements-stamp"
if [ ! -f "$STAMP" ] || ! cmp -s "$REQS" "$STAMP"; then
    echo "Installing dependencies ..."
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install -r "$REQS"
    cp "$REQS" "$STAMP"
fi

exec "$VENV/bin/python" gui/app.py "$@"
