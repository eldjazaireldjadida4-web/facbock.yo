from __future__ import annotations

import os
from pathlib import Path


def restrict_file_permissions(path: str | os.PathLike[str], mode: int = 0o600) -> bool:
    if os.name == "nt":
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
