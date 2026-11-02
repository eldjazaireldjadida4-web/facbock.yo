#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

if [ ! -f requirements-dev.txt ]; then
    echo "Error: requirements-dev.txt is missing; run this from the project directory." >&2
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    if ! python3 -m venv .venv; then
        echo "Error: could not create .venv. Install Python venv support and retry." >&2
        exit 1
    fi
fi

.venv/bin/python -m pip install --quiet --disable-pip-version-check --upgrade pip
if [ $? -ne 0 ]; then
    echo "Error: pip upgrade failed." >&2
    exit 1
fi

if ! .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt; then
    echo "Error: dependency installation failed." >&2
    exit 1
fi

echo "Running project tests..."
if .venv/bin/python -m pytest "$@"; then
    echo "All tests passed."
else
    status=$?
    echo "Some tests failed; see the output above." >&2
    exit "$status"
fi
