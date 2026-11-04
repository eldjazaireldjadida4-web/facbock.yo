from __future__ import annotations

import configparser
import math
from urllib.parse import urlsplit

from content_safety import RIGHTS_BASIS_LABELS, validate_automatic_rights_settings
from control_plane import validate_publication_controls


BOOLEAN_SETTINGS = (
    ("SETTINGS", "HEADLESS"),
    ("SETTINGS", "DELETE_AFTER_UPLOAD"),
    ("SETTINGS", "REMOVE_TAGS"),
    ("SETTINGS", "DRY_RUN"),
    ("WATCH", "AUTO_START"),
    ("WATCH", "FAST_SCRAPE"),
    ("WATCH", "NETWORK_PREFLIGHT"),
    ("WATCH", "FORCE_IPV4"),
    ("STUDIO", "TITLE_OPTIMIZE"),
    ("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE"),
    ("STUDIO", "AUTO_SHORTS"),
    ("STUDIO", "AUTO_HASHTAGS"),
    ("STUDIO", "DEDUPE_CONTENT"),
    ("STUDIO", "RIGHTS_AUTO_DRAFT"),
    ("STUDIO", "LOGO_ENABLED"),
    ("STUDIO", "LOGO_COVER_OLD"),
    ("STUDIO", "LOGO_PLATE"),
    ("STUDIO", "LOGO_SECONDARY"),
    ("STUDIO", "LOGO_SECONDARY_COVER_OLD"),
    ("STUDIO", "REVIEW_BEFORE_UPLOAD"),
    ("STUDIO", "THUMBNAIL_ENABLED"),
    ("STUDIO", "SHORTS_CUT"),
    ("STUDIO", "SCHEDULE"),
    ("STUDIO", "COOKIES_ENABLED"),
    ("STUDIO", "TELEGRAM_ERRORS"),
    ("AI", "ENABLED"),
    ("SAFETY", "ENABLED"),
    ("SAFETY", "ALLOW_PUBLIC_AUTOMATION"),
)

INTEGER_RANGES = {
    ("SETTINGS", "BETWEEN_VIDEOS"): (0, 86400),
    ("SETTINGS", "DAILY_UPLOAD_LIMIT"): (1, 1000),
    ("SETTINGS", "MAX_ATTEMPTS"): (1, 10),
    ("SETTINGS", "FAILED_MAX_AGE_DAYS"): (1, 3650),
    ("WATCH", "INTERVAL_MINUTES"): (1, 1440),
    ("WATCH", "DOWNLOAD_SOCKET_TIMEOUT"): (5, 300),
    ("WATCH", "DOWNLOAD_RETRIES"): (1, 10),
    ("WATCH", "SCRAPE_SOCKET_TIMEOUT"): (5, 120),
    ("WATCH", "SCRAPE_RETRIES"): (1, 10),
    ("WATCH", "PAGE_LOAD_TIMEOUT"): (5, 120),
    ("WATCH", "NETWORK_PREFLIGHT_TIMEOUT"): (5, 120),
    ("STUDIO", "CATEGORY"): (1, 99),
    ("STUDIO", "PG_SCROLL"): (0, 100),
    ("STUDIO", "PG_LIMIT"): (1, 100),
    ("STUDIO", "LOGO_SCALE"): (1, 100),
    ("STUDIO", "LOGO_OPACITY"): (0, 100),
    ("STUDIO", "LOGO_COVER_WIDTH"): (1, 100),
    ("STUDIO", "SCHEDULE_MIN"): (1, 10080),
    ("STUDIO", "SCHEDULE_MAX"): (1, 10080),
    ("STUDIO", "MIN_DURATION"): (0, 43200),
    ("STUDIO", "MAX_DURATION"): (0, 43200),
}

PATH_SETTINGS = (
    ("SETTINGS", "VIDEO_DIRECTORY"),
    ("SETTINGS", "CLIENT_SECRETS_FILE"),
    ("STUDIO", "COOKIES_FILE"),
    ("STUDIO", "RIGHTS_MANIFEST_FILE"),
    ("STUDIO", "LOGO_PATH"),
    ("SAFETY", "CUTTER_ROOT"),
    ("SAFETY", "CUTTER_PYTHON"),
)


def _get(cfg: configparser.ConfigParser, section: str, key: str, fallback: str = "") -> str:
    try:
        return cfg.get(section, key, fallback=fallback).strip()
    except (configparser.Error, ValueError):
        return fallback


def _boolean(cfg: configparser.ConfigParser, section: str, key: str, fallback: bool = False) -> bool:
    try:
        return cfg.getboolean(section, key, fallback=fallback)
    except (configparser.Error, ValueError):
        return fallback


def validate_configuration(cfg: configparser.ConfigParser) -> list[str]:
    errors: list[str] = []
    for section, key in BOOLEAN_SETTINGS:
        if not cfg.has_option(section, key):
            continue
        try:
            cfg.getboolean(section, key)
        except (configparser.Error, ValueError):
            errors.append(f"{section}.{key} يجب أن تكون true أو false.")
    for (section, key), (minimum, maximum) in INTEGER_RANGES.items():
        if not cfg.has_option(section, key):
            continue
        try:
            value = cfg.getint(section, key)
        except (configparser.Error, ValueError):
            errors.append(f"{section}.{key} يجب أن تكون عدداً صحيحاً.")
            continue
        if not minimum <= value <= maximum:
            errors.append(f"{section}.{key} خارج النطاق المسموح ({minimum}–{maximum}).")
    if cfg.has_option("SETTINGS", "MIN_DISK_GB"):
        try:
            disk_gb = cfg.getfloat("SETTINGS", "MIN_DISK_GB")
            if not math.isfinite(disk_gb) or not 0 <= disk_gb <= 100000:
                raise ValueError
        except (configparser.Error, ValueError, OverflowError):
            errors.append("SETTINGS.MIN_DISK_GB يجب أن تكون عدداً موجباً محدوداً.")
    for section, key in PATH_SETTINGS:
        value = _get(cfg, section, key)
        if value and ("\x00" in value or len(value) > 4096):
            errors.append(f"{section}.{key} يحتوي مساراً غير صالح.")
    for section, key in (("SETTINGS", "VIDEO_DIRECTORY"), ("SETTINGS", "CLIENT_SECRETS_FILE")):
        if not _get(cfg, section, key):
            errors.append(f"{section}.{key} مطلوب.")
    privacy = _get(cfg, "SETTINGS", "PRIVACY", "private").lower()
    if privacy not in {"private", "unlisted", "public"}:
        errors.append("SETTINGS.PRIVACY يجب أن تكون private أو unlisted أو public.")
    rights_mode = _get(cfg, "STUDIO", "RIGHTS_GATE_MODE", "strict").lower()
    if rights_mode not in {"strict", "operator_confirmed"}:
        errors.append("STUDIO.RIGHTS_GATE_MODE يجب أن تكون strict أو operator_confirmed.")
    rights_basis = _get(cfg, "STUDIO", "RIGHTS_OPERATOR_BASIS", "owned").lower()
    if rights_basis not in RIGHTS_BASIS_LABELS:
        errors.append("STUDIO.RIGHTS_OPERATOR_BASIS غير صالح.")
    tags = _get(cfg, "STUDIO", "TAGS", "")
    if len(tags) > 500:
        errors.append("STUDIO.TAGS يتجاوز حد YouTube البالغ 500 حرف.")
    proxy = _get(cfg, "WATCH", "PROXY", "")
    if proxy:
        try:
            parsed = urlsplit(proxy)
            port = parsed.port
            valid_proxy = (
                parsed.scheme.lower() in {"http", "https", "socks4", "socks5", "socks5h"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and (port is None or 1 <= port <= 65535)
            )
        except ValueError:
            valid_proxy = False
        if not valid_proxy:
            errors.append("WATCH.PROXY يجب أن يكون عنوان بروكسي صالحاً بلا اسم مستخدم أو كلمة مرور أو مسار.")
    min_duration = _safe_int(cfg, "STUDIO", "MIN_DURATION", 0)
    max_duration = _safe_int(cfg, "STUDIO", "MAX_DURATION", 0)
    if max_duration and min_duration >= max_duration:
        errors.append("STUDIO.MAX_DURATION يجب أن تكون أكبر من MIN_DURATION أو تساوي 0 بلا حد أقصى.")
    min_schedule = _safe_int(cfg, "STUDIO", "SCHEDULE_MIN", 20)
    max_schedule = _safe_int(cfg, "STUDIO", "SCHEDULE_MAX", 60)
    if max_schedule < min_schedule:
        errors.append("STUDIO.SCHEDULE_MAX يجب ألا تقل عن SCHEDULE_MIN.")
    review_before_upload = _boolean(cfg, "STUDIO", "REVIEW_BEFORE_UPLOAD", True)
    schedule_enabled = _boolean(cfg, "STUDIO", "SCHEDULE", False)
    allow_public = _boolean(cfg, "SAFETY", "ALLOW_PUBLIC_AUTOMATION", False)
    dry_run = _boolean(cfg, "SETTINGS", "DRY_RUN", False)
    errors.extend(validate_publication_controls(
        privacy,
        schedule_enabled,
        review_before_upload,
        allow_public,
        dry_run=dry_run,
    ))
    errors.extend(validate_automatic_rights_settings({
        "rights_gate_mode": rights_mode,
        "review_before_upload": review_before_upload,
        "dry_run": dry_run,
        "rights_operator_basis": rights_basis,
        "rights_operator_statement": _get(cfg, "STUDIO", "RIGHTS_OPERATOR_STATEMENT"),
        "rights_operator_evidence": _get(cfg, "STUDIO", "RIGHTS_OPERATOR_EVIDENCE"),
    }))
    return list(dict.fromkeys(errors))


def _safe_int(cfg: configparser.ConfigParser, section: str, key: str, fallback: int) -> int:
    try:
        return cfg.getint(section, key, fallback=fallback)
    except (configparser.Error, ValueError):
        return fallback
