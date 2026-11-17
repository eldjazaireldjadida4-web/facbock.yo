from __future__ import annotations

import logging
import os
from pathlib import Path

_WINDOWS_PERMISSION_WARNING_EMITTED = False


def _warn_windows_permissions_once():
    """يحذّر مرة واحدة أن chmod/0600 غير مطبق على Windows — لا عطل إطلاقاً."""
    global _WINDOWS_PERMISSION_WARNING_EMITTED
    if _WINDOWS_PERMISSION_WARNING_EMITTED:
        return
    _WINDOWS_PERMISSION_WARNING_EMITTED = True
    logging.warning(
        "حماية الملفات (0600) غير مطبقة على Windows؛ "
        "اعتمد صلاحيات NTFS للمستخدم الحالي لإخفاء الرموز محلياً."
    )


def restrict_file_permissions(path: str | os.PathLike[str], mode: int = 0o600,
                              _force_windows: bool = False) -> bool:
    global _WINDOWS_PERMISSION_WARNING_EMITTED
    if _force_windows or os.name == "nt":
        first = not _WINDOWS_PERMISSION_WARNING_EMITTED
        _WINDOWS_PERMISSION_WARNING_EMITTED = True
        if first:
            _warn_windows_permissions_once()
        return False
    target = Path(path)
    try:
        if target.is_symlink() or not target.is_file():
            return False
        os.chmod(target, mode)
        return True
    except OSError:
        return False


def open_private_append(path: str | os.PathLike[str], *, binary: bool = False):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if binary:
        flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(target, flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        if binary:
            return os.fdopen(descriptor, "ab")
        return os.fdopen(descriptor, "a", encoding="utf-8", newline="\n")
    except Exception:
        os.close(descriptor)
        raise
