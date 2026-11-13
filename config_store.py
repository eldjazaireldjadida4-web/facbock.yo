from __future__ import annotations

import configparser
import errno
import os
import shutil
import stat
import tempfile
from pathlib import Path


class ConfigRecoveryError(RuntimeError):
    pass


def _secure_mode(path: Path) -> None:
    if os.name == "posix":
        os.chmod(path, 0o600)


def _parse_text(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return parser


def _read_private_text(path: Path) -> str:
    """يقرأ ملف إعدادات عادياً دون متابعة رابط رمزي، ويقيّد صلاحياته على نفس الواصف.

    فحص ``is_symlink()`` ثم الفتح بالاسم يترك فجوة TOCTOU: قد يُستبدل المسار
    برابط بين الفحص والفتح. هنا الفتح نفسه يرفض الروابط (O_NOFOLLOW) ويقفل
    الملف عبر ``os.fchmod`` لا عبر مسار يمكن استبداله.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "configuration file is not a regular file", str(path))
        if os.name == "posix":
            try:
                os.fchmod(descriptor, 0o600)
            except OSError as error:
                if error.errno not in {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            descriptor = -1
            return source.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as error:
        if error.errno in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise
    finally:
        os.close(descriptor)


def _copy_private(source: Path, destination: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temp_path = Path(temporary)
    try:
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
            descriptor = -1
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        _secure_mode(temp_path)
        os.replace(temp_path, destination)
        _sync_directory(destination.parent)
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _rotate_backups(path: Path, count: int = 3) -> None:
    backup_temp_descriptor, backup_temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.backup.", suffix=".tmp", dir=path.parent
    )
    backup_temp = Path(backup_temp_name)
    try:
        with path.open("rb") as source, os.fdopen(backup_temp_descriptor, "wb") as destination:
            backup_temp_descriptor = -1
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        _secure_mode(backup_temp)
        for index in range(count, 1, -1):
            previous = path.with_name(f"{path.name}.bak{index - 1}")
            current = path.with_name(f"{path.name}.bak{index}")
            if previous.exists():
                os.replace(previous, current)
        os.replace(backup_temp, path.with_name(f"{path.name}.bak1"))
        _sync_directory(path.parent)
    except Exception:
        if backup_temp_descriptor >= 0:
            try:
                os.close(backup_temp_descriptor)
            except OSError:
                pass
        try:
            backup_temp.unlink()
        except OSError:
            pass
        raise


def read_config(path: str | os.PathLike[str]) -> tuple[configparser.ConfigParser, Path | None]:
    config_path = Path(path).expanduser()
    candidates = [config_path] + [
        config_path.with_name(f"{config_path.name}.bak{index}") for index in range(1, 4)
    ]
    existing = False
    errors: list[Exception] = []
    for candidate in candidates:
        if candidate.is_symlink():
            errors.append(OSError("symbolic-link configuration files are not accepted"))
            existing = True
            continue
        if not candidate.is_file():
            continue
        existing = True
        try:
            parser = _parse_text(_read_private_text(candidate))
        except (OSError, UnicodeError, configparser.Error) as error:
            errors.append(error)
            continue
        if candidate != config_path:
            try:
                write_config_atomically(parser, config_path, backup=False)
            except ConfigRecoveryError as error:
                # المسار الأساسي رابط رمزي أو غير قابل للاستبدال: النسخة
                # الاحتياطية صالحة، فلا نُسقط الاسترداد بسبب إعادة الكتابة.
                errors.append(error)
        return parser, candidate
    if existing:
        raise ConfigRecoveryError(
            f"{config_path.name} cannot be read and has no valid backup."
        ) from (errors[-1] if errors else None)
    return configparser.ConfigParser(interpolation=None), None


def write_config_atomically(
    parser: configparser.ConfigParser,
    path: str | os.PathLike[str],
    *,
    backup: bool = True,
) -> None:
    config_path = Path(path).expanduser()
    if config_path.is_symlink():
        raise ConfigRecoveryError("Symbolic-link configuration files are not accepted.")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if backup and config_path.exists():
        _rotate_backups(config_path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output_file:
            descriptor = -1
            parser.write(output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        _secure_mode(temp_path)
        os.replace(temp_path, config_path)
        _sync_directory(config_path.parent)
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
