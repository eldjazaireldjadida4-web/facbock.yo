#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python=".venv/bin/python"
if [ ! -x "$python" ]; then
  printf '%s\n' "شغّل ./setup.sh أولاً."
  exit 2
fi
if [ "${1:-}" = "--preflight" ]; then
  shift
  exec "$python" fb_youtube_uploader_v49_pro.py --preflight "$@"
fi
exec "$python" fb_youtube_uploader_v49_pro.py --cli "$@"
