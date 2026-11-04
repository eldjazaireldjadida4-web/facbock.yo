from __future__ import annotations

import configparser
import errno
import os
import shutil
import tempfile
from pathlib import Path


class ConfigRecoveryError(RuntimeError):
    pass


def _secure_mode(path: Path) -> None:
    if os.name == "posix":
        os.chmod(path, 0o600)


def _parse(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8") as source:
        parser.read_file(source)
    return parser


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
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        _secure_mode(temp_path)
        os.replace(temp_path, destination)
    except Exception:
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
            parser = _parse(candidate)
            _secure_mode(candidate)
            if candidate != config_path:
                write_config_atomically(parser, config_path, backup=False)
            return parser, candidate
        except (OSError, UnicodeError, configparser.Error) as error:
            errors.append(error)
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
            parser.write(output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        _secure_mode(temp_path)
        os.replace(temp_path, config_path)
        _sync_directory(config_path.parent)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
