#!/usr/bin/env bash
# Build the facbock.yo one-dir bundle on Linux with PyInstaller.
# Run from the repo root:  ./build_linux.sh
# Output: dist/facbock-yo/  (facbock-yo, facbock-yo-doctor,
# facbock-yo-automation) plus a tarball dist/facbock-yo-<version>-linux-x86_64.tar.gz
# and a first-run launcher dist/facbock-yo/facbock-yo.sh.
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  printf '%s\n' "Requires Python 3.11 or newer."
  exit 2
fi
if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  printf '%s\n' "Tkinter is missing from this Python. On Ubuntu/Debian: sudo apt install python3-tk"
  exit 2
fi

if [ ! -x ".venv-build/bin/python" ]; then
  python3 -m venv .venv-build
fi
.venv-build/bin/python -m pip install --upgrade pip
.venv-build/bin/python -m pip install -r requirements-lock.txt
.venv-build/bin/python -m pip install pyinstaller

.venv-build/bin/python -m PyInstaller --clean --noconfirm facbock-yo.spec

# PyInstaller 6 puts bundled data files under dist/facbock-yo/_internal/.
# The launcher and the user need the example configs beside the binary,
# so copy them to the bundle root as well.
cp config.ini.example dist/facbock-yo/config.ini.example
cp rights_manifest.example.json dist/facbock-yo/rights_manifest.example.json

# First-run launcher: copy the shipped example config on a fresh install,
# then run the frozen binary (so --help/--preflight/--cli keep working).
cat > dist/facbock-yo/facbock-yo.sh <<'LAUNCHER'
#!/usr/bin/env bash
# First-run helper for the facbock.yo Linux bundle.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f config.ini ] && [ -f config.ini.example ]; then
  cp config.ini.example config.ini
  chmod 600 config.ini
fi
exec ./facbock-yo "$@"
LAUNCHER
chmod +x dist/facbock-yo/facbock-yo.sh

VERSION="$(.venv-build/bin/python -c "from version import APP_VERSION; print(APP_VERSION)")"
tar -czf "dist/facbock-yo-${VERSION}-linux-x86_64.tar.gz" -C dist facbock-yo

printf '\nBundle built: dist/facbock-yo/\n'
du -sh dist/facbock-yo "dist/facbock-yo-${VERSION}-linux-x86_64.tar.gz"
printf '\nSmoke test:\n'
dist/facbock-yo/facbock-yo --version
dist/facbock-yo/facbock-yo --help >/dev/null && printf -- '--help OK\n'
printf 'Run the GUI with: dist/facbock-yo/facbock-yo.sh\n'
