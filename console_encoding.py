# -*- coding: utf-8 -*-
"""تهيئة ترميز إخراج الطرفية إلى UTF-8 (حماية من أعطال Windows).

On a Windows console ``stdout``/``stderr`` default to the legacy code
page (cp1252): printing the Arabic text and arrows (``→``) this app uses
raises ``UnicodeEncodeError`` and kills the frozen app before it does
any work (release run 44340376582: ``--preflight`` crashed with exit 1
instead of the expected 2; ``--version``/``--help`` crashed the same
way). Reconfigure both streams to UTF-8 with ``errors="replace"`` at
every entry point, before any output. The call is guarded so it can
never break startup (redirected, replaced, or already-UTF-8 streams).
"""

import sys


def reconfigure_std_streams_utf8() -> None:
    """Reconfigure stdout/stderr to UTF-8 (errors="replace") if possible.

    Never raises: a missing stream, a stream without ``reconfigure``
    (test doubles, pipes, ``io.StringIO``) and a stream whose
    reconfiguration fails are all left alone.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue
