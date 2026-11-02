#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  printf '%s\n' "يتطلب المشروع Python 3.11 أو أحدث."
  exit 2
fi
if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  printf '%s\n' "مكوّن Tkinter غير متوفر في Python هذا. على Ubuntu/Debian نفّذ: sudo apt install python3-tk ثم أعد ./setup.sh."
  exit 2
fi
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-lock.txt
if [ ! -f config.ini ]; then
  cp config.ini.example config.ini
fi
chmod 600 config.ini
printf '%s\n' "اكتمل التثبيت. أضف ملف Google OAuth، ثم شغّل ./run_gui.sh مرة واحدة لتسجيل الدخول. بعده شغّل ./run_automation.sh --preflight للتحقق قبل التشغيل الآلي."
