from __future__ import annotations

from importlib import import_module
from collections.abc import Callable


RUNTIME_IMPORTS = (
    ("tkinter", "Tkinter (Linux: python3-tk; Windows: reinstall Python with Tcl/Tk)"),
    ("PIL", "Pillow"),
    ("arabic_reshaper", "arabic-reshaper"),
    ("bidi.algorithm", "python-bidi"),
    ("yt_dlp", "yt-dlp"),
    ("selenium", "selenium"),
    ("googleapiclient", "google-api-python-client"),
    ("google_auth_oauthlib", "google-auth-oauthlib"),
    ("google.auth", "google-auth"),
    ("google.auth.transport.requests", "google-auth"),
    ("google_auth_httplib2", "google-auth-httplib2"),
    ("portalocker", "portalocker"),
    ("fontTools", "fonttools"),
)


def missing_runtime_dependencies(importer: Callable[[str], object] = import_module) -> list[str]:
    missing = []
    for module_name, package_name in RUNTIME_IMPORTS:
        try:
            importer(module_name)
        except Exception:
            missing.append(package_name)
    return list(dict.fromkeys(missing))
