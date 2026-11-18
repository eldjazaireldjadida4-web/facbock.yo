# -*- mode: python ; coding: utf-8 -*-
# ruff: noqa: F821  (Analysis/PYZ/EXE/COLLECT/SPECPATH are PyInstaller globals)
"""PyInstaller spec for facbock.yo (FB → YouTube Uploader Pro).

Build with ``build_windows.bat`` / ``build_linux.sh`` or directly::

    python -m PyInstaller --clean --noconfirm facbock-yo.spec

Design (see docs/packaging.md for the full rationale):

* One-directory bundle (``onedir``), NOT one-file: a Tkinter app of this
  size starts far faster when nothing is unpacked to a temp dir on every
  launch, and ``APP_DIRECTORY`` (``dirname(abspath(__file__))``) resolves
  to the real install directory, so config/state/logs live beside the
  program exactly as in source mode. A one-file build would point
  ``APP_DIRECTORY`` at a throwaway ``_MEIxxxx`` temp dir and silently
  lose all state on exit.
* ``console=True`` for the main executable: the CLI modes (``--help``,
  ``--preflight``, ``--cli``, ``--repair-state``) need a console, exactly
  like the ``.bat`` launchers today. The GUI opens on top of that console.
* Three executables share one COLLECT: the main app, the readiness
  checker (``doctor.py``) and the automation setup tool
  (``enable_automation.py``).
* The faster-whisper stack is EXCLUDED on purpose: local transcription is
  an optional feature, imported lazily, and its absence degrades cleanly
  (``LocalTranscriptionError`` with a plain message). Bundling it would
  add onnxruntime + ctranslate2 + tokenizers + model weights — hundreds
  of megabytes — for a feature that defaults to off. The build venv
  installs ``requirements-lock.txt`` (which does contain faster-whisper),
  so the excludes below are what keep it out of the bundle.
* The doctor executable must be able to import every module its library
  check verifies (``dependency_check.RUNTIME_IMPORTS``), even though its
  own code never touches most of them — otherwise the frozen doctor
  falsely reports them missing. ``DOCTOR_HIDDEN_IMPORTS`` below carries
  that list (``tests/test_packaging.py`` enforces it stays in sync).
* FFmpeg/ffprobe are NOT bundled: they are external binaries resolved
  via ``PATH`` (see ``media_processing.ffmpeg_available``). The installer
  and docs tell the user to install FFmpeg once (``winget install
  Gyan.FFmpeg``).
"""

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

try:
    SPEC_DIR = Path(SPECPATH)
except NameError:  # pragma: no cover - spec is always run by PyInstaller
    SPEC_DIR = Path.cwd()

_VERSION_RE = re.compile(r'APP_VERSION\s*=\s*"([^"]+)"')
APP_VERSION = _VERSION_RE.search(
    (SPEC_DIR / "version.py").read_text(encoding="utf-8")
).group(1)

# Optional local-transcription stack: imported lazily by
# local_transcription._load_model and cleanly reported as missing.
TRANSCRIBE_EXCLUDES = [
    "faster_whisper",
    "onnxruntime",
    "ctranslate2",
    "tokenizers",
    "huggingface_hub",
    "hf_xet",
    "av",
]

HIDDEN_IMPORTS = [
    # google-api-python-client resolves these dynamically at discovery time.
    "googleapiclient.discovery_cache",
    "google.auth.transport.requests",
    "google_auth_oauthlib.flow",
]

# Modules the readiness checker verifies (dependency_check.RUNTIME_IMPORTS).
# Keep in sync — tests/test_packaging.py enforces it.
DOCTOR_HIDDEN_IMPORTS = [
    "tkinter",
    "PIL",
    "arabic_reshaper",
    "bidi.algorithm",
    "yt_dlp",
    "selenium",
    "googleapiclient",
    "google_auth_oauthlib",
    "google.auth",
    "google.auth.transport.requests",
    "google_auth_httplib2",
    "portalocker",
    "fontTools",
]

# Read-only examples shipped beside the program (the app never writes
# these). NEVER add config.ini, *_token.json or client_secrets.json here:
# user secrets must never be baked into a distributed bundle.
DATAS = [
    (str(SPEC_DIR / "config.ini.example"), "."),
    (str(SPEC_DIR / "rights_manifest.example.json"), "."),
]
# Data files PyInstaller does not always pick up on its own: the tz
# database (no system tzdata on Windows), arabic-reshaper's tables and
# certifi's CA bundle (used by the Google/selenium stacks).
DATAS += collect_data_files("tzdata")
DATAS += collect_data_files("arabic_reshaper")
DATAS += collect_data_files("certifi")

COMMON = dict(
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=TRANSCRIBE_EXCLUDES,
    noarchive=False,
)

a_main = Analysis(["fb_youtube_uploader_v49_pro.py"], **COMMON)
a_doctor = Analysis(
    ["doctor.py"], **{**COMMON, "hiddenimports": HIDDEN_IMPORTS + DOCTOR_HIDDEN_IMPORTS}
)
a_automation = Analysis(["enable_automation.py"], **COMMON)

pyz_main = PYZ(a_main.pure, a_main.zipped_data, cipher=None)
pyz_doctor = PYZ(a_doctor.pure, a_doctor.zipped_data, cipher=None)
pyz_automation = PYZ(a_automation.pure, a_automation.zipped_data, cipher=None)

exe_main = EXE(
    pyz_main,
    a_main.scripts,
    [],
    exclude_binaries=True,
    name="facbock-yo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_doctor = EXE(
    pyz_doctor,
    a_doctor.scripts,
    [],
    exclude_binaries=True,
    name="facbock-yo-doctor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_automation = EXE(
    pyz_automation,
    a_automation.scripts,
    [],
    exclude_binaries=True,
    name="facbock-yo-automation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe_main,
    exe_doctor,
    exe_automation,
    a_main.binaries,
    a_main.datas,
    a_doctor.binaries,
    a_doctor.datas,
    a_automation.binaries,
    a_automation.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="facbock-yo",
)
