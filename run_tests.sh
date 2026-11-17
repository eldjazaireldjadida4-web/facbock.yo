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
pytest_out=$(mktemp)
status=0
.venv/bin/python -m pytest "$@" 2>&1 | tee "$pytest_out" || status=${PIPESTATUS[0]}
# حارس الانقطاع الصامت: يجب أن يصل pytest إلى سطر الملخص (مثل "690 passed").
# إن لم يصل فالتشغيل انقطع (مثلاً عبر os._exit من اختبار) ويُعد فشلاً
# حتى لو كان رمز خروج pytest صفراً.
if ! grep -qE '[0-9]+ (passed|failed|error)' "$pytest_out"; then
    echo "CRITICAL: pytest did not reach a summary line; the run was truncated." >&2
    rm -f "$pytest_out"
    exit 3
fi
rm -f "$pytest_out"
if [ "$status" -ne 0 ]; then
    echo "Some tests failed; see the output above." >&2
    exit "$status"
fi
echo "All tests passed."
