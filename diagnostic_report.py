from __future__ import annotations

import configparser
import errno
import json
import os
import platform
import re
import shutil
import socket
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from content_safety import (
    DEFAULT_OPERATOR_RIGHTS_STATEMENT,
    migrate_legacy_rights_gate_config,
    validate_automatic_rights_settings,
)
from facebook_auth import facebook_cookie_setup_status
from facebook_scraper import DEFAULT_NETWORK_PREFLIGHT_TIMEOUT

APP_VERSION = "4.48.11"
REPORT_DIRECTORY = "reports"
NETWORK_ERROR_MARKERS = (
    "network",
    "timed out",
    "timeout",
    "connection refused",
    "connection reset",
    "network is unreachable",
    "تعذر الاتصال",
    "تعذر الوصول",
    "ipv4 وipv6",
)
ERROR_MARKERS = (
    "error",
    "exception",
    "critical",
    "failed",
    "failure",
    "timeout",
    "timed out",
    "connection refused",
    "connection reset",
    "network unreachable",
    "network unavailable",
    "فشل",
    "خطأ",
    "تعذر",
    "رفض",
    "أوقف التشغيل",
    "تحذير",
)

SECRET_HEADER_PATTERN = re.compile(r"(?i)\b(authorization|cookie|cookies|set-cookie)\s*[:=]\s*[^\r\n]+")
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)\b(c_user|xs|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|client[_ -]?secret|api[_ -]?key|password|passwd|secret|token)\s*[:=]\s*(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
PRIVATE_FIELD_PATTERN = re.compile(
    r"(?i)\b(title|page[_ -]?(?:name|id|url)|video[_ -]?(?:title|id)|source[_ -]?url|post[_ -]?id|email|username)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
URL_PATTERN = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)
LOCAL_PATH_PATTERN = re.compile(r"(?i)(?:[A-Z]:\\Users\\|/Users/|/home/)[^\s\"']+")
CATEGORY_LABELS = {
    "rights": "بوابة الحقوق",
    "facebook_network": "اتصال Facebook",
    "facebook_auth": "جلسة Facebook",
    "youtube_quota": "حصة YouTube",
    "youtube_auth": "تفويض YouTube",
    "disk": "مساحة التخزين",
    "download": "تنزيل الفيديو",
    "upload": "رفع الفيديو",
    "runtime": "خطأ تشغيل عام",
    "configuration": "إعدادات التشغيل",
    "pages": "قائمة صفحات Facebook",
    "queue_file": "ملف قائمة الانتظار",
    "pending_queue": "عناصر قيد الانتظار",
    "youtube_network": "اتصال YouTube API",
    "proxy_configuration": "إعداد الوكيل",
    "network_preflight_disabled": "فحص الشبكة",
    "facebook_proxy_unverified": "اتصال الوكيل",
    "proxy_not_configured": "لا يوجد وكيل مضبوط",
    "proxy_unreachable": "الوكيل لا يستجيب",
}
CATEGORY_GUIDANCE = {
    "rights": "إذا كان الفيديو أصلياً وتملك حقوقه كاملة، اختر أساس owned في الاستوديو > حقوق النشر؛ لا يلزم مرجع إثبات في هذه الحالة. إذا كان الحق بترخيص أو إذن، اختر الأساس المطابق وأدخل مرجعاً حقيقياً. لا تستخدم إقراراً غير صحيح.",
    "facebook_network": "اختبر الاتصال من تبويب Facebook. افحص DNS وجدار الحماية؛ إذا كنت تستخدم وكيلاً موثوقاً فتحقق من عنوانه ومنفذه. العناصر المعلّقة لم تُرفع.",
    "facebook_auth": "أعد تسجيل الدخول إلى Facebook وصدّر cookies.txt جديداً، ثم اختبر الصفحة يدوياً.",
    "youtube_quota": "تحقق من حصة YouTube اليومية وانتظر إعادة تعيينها قبل تكرار الرفع.",
    "youtube_auth": "افتح تبويب الحسابات وأكمل تفويض YouTube؛ التقرير لا يقرأ محتوى رموز OAuth.",
    "disk": "حرّر مساحة في مجلد الفيديو المؤقت أو غيّر مجلده من الإعدادات.",
    "download": "افحص اتصال Facebook والكوكيز وحدّث yt-dlp من داخل التطبيق.",
    "upload": "افحص سجل الرفع وبيانات YouTube؛ لا تعاود رفع عنصر نتيجته غير محسومة قبل المطابقة.",
    "runtime": "افتح errors.log محلياً للتفاصيل التقنية، ثم أعد التشغيل بعد معالجة السبب.",
    "configuration": "افحص config.ini وأعد ضبط القيم غير الصالحة من الواجهة.",
    "pages": "فعّل صفحة Facebook واحدة على الأقل في تبويب الصفحات.",
    "queue_file": "احتفظ بنسخة من قائمة الانتظار قبل محاولة إصلاح ملفها؛ التقرير لم يغيّره.",
    "pending_queue": "تعامل مع سبب الانتظار ثم أعد فحص العناصر من الواجهة؛ لا يحذفها التقرير.",
    "youtube_network": "اختبر اتصال الإنترنت وDNS والمنفذ 443 إلى Google API قبل محاولة الرفع.",
    "proxy_configuration": "صحّح عنوان الوكيل من تبويب Facebook، ولا تضع اسم مستخدم أو كلمة مرور فيه.",
    "proxy_not_configured": "إذا كان الاتصال المباشر محجوباً، استخدم فقط وكيلاً موثوقاً تملكه؛ راجع عنوان الوكيل والمنفذ من تبويب Facebook ولا تشارك بيانات الدخول.",
    "proxy_unreachable": "تحقق من تشغيل خدمة الوكيل ومن عنوانها ومنفذها؛ لا تشارك اسم المستخدم أو كلمة المرور.",
    "network_preflight_disabled": "فعّل فحص الشبكة قبل التشغيل لتجنّب دورات بلا اتصال.",
    "facebook_proxy_unverified": "المنفذ يستجيب، لكن يلزم اختبار صفحة Facebook للتأكد من أن الوكيل يصل إلى الوجهة.",
}


def _error_code(error: BaseException) -> str:
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "timeout"
    error_number = getattr(error, "errno", None)
    if error_number in {errno.ECONNREFUSED, 10061}:
        return "connection_refused"
    if error_number in {errno.ENETUNREACH, 10051, errno.EHOSTUNREACH, 10065}:
        return "network_unreachable"
    if isinstance(error, socket.gaierror):
        if getattr(error, "errno", None) == getattr(socket, "EAI_AGAIN", object()):
            return "temporary_dns_failure"
        return "dns_failure"
    return "connection_failed"


def probe_service(host: str, port: int = 443, timeout: float = 10.0) -> dict:
    timeout = max(1.0, min(float(timeout), 15.0))
    results = {}
    for label, family in (("IPv4", socket.AF_INET), ("IPv6", socket.AF_INET6)):
        try:
            addresses = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except OSError as error:
            results[label] = {
                "dns": _error_code(error),
                "addresses": 0,
                "tcp": "not_tested",
            }
            continue
        unique = []
        seen = set()
        for address_family, socktype, protocol, _, address in addresses:
            key = (address_family, tuple(address))
            if key not in seen:
                seen.add(key)
                unique.append((address_family, socktype, protocol, address))
        if not unique:
            results[label] = {"dns": "no_address", "addresses": 0, "tcp": "not_tested"}
            continue
        failures = []
        connected = False
        candidates = unique[:4]
        per_address_timeout = max(0.5, timeout / len(candidates))
        for address_family, socktype, protocol, address in candidates:
            try:
                with socket.socket(address_family, socktype, protocol) as connection:
                    connection.settimeout(per_address_timeout)
                    connection.connect(address)
                connected = True
                break
            except OSError as error:
                failures.append(_error_code(error))
        results[label] = {
            "dns": "ok",
            "addresses": len(unique),
            "tcp": "reachable" if connected else (failures[0] if failures else "connection_failed"),
        }
    return results


def _load_config(root: Path):
    parser = configparser.ConfigParser(interpolation=None)
    path = root / "config.ini"
    try:
        if not path.is_file():
            return parser, "missing"
        with path.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
        migrate_legacy_rights_gate_config(parser)
        return parser, "ok"
    except (OSError, UnicodeError, configparser.Error):
        return parser, "invalid"


def _cfg(config, section, key, default=""):
    return config.get(section, key, fallback=default)


def _cfg_bool(config, section, key, default=False):
    try:
        return config.getboolean(section, key, fallback=default)
    except (ValueError, configparser.Error):
        return default


def _project_path(root: Path, value: str) -> Path:
    candidate = Path(os.path.expandvars(os.path.expanduser(str(value or "").strip())))
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def _proxy_endpoint(proxy_value: str):
    value = str(proxy_value or "").strip()
    if not value:
        return "not_configured", None, None
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
            return "invalid", None, None
        port = parsed.port or (443 if scheme == "https" else 1080 if scheme.startswith("socks5") else 80)
        return "configured", parsed.hostname, port
    except ValueError:
        return "invalid", None, None


def _file_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source)
        return value, "ok"
    except FileNotFoundError:
        return default, "missing"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return default, "invalid"


def _queue_summary(root: Path):
    data, state = _file_json(root / "failed_queue.json", [])
    if not isinstance(data, list):
        return {"state": "invalid", "total": 0, "by_status": {}}
    statuses = Counter()
    for item in data:
        if not isinstance(item, dict):
            statuses["invalid_item"] += 1
            continue
        status = str(item.get("status") or "pending").strip().lower()
        if status in {
            "rights_review_required",
            "content_safety_review_required",
            "publication_approval_required",
            "duplicate_review_required",
            "upload_result_unknown",
            "upload_in_progress",
            "failed",
            "pending",
        }:
            statuses[status] += 1
        else:
            statuses["other"] += 1
    return {"state": state, "total": len(data), "by_status": dict(statuses)}


def _classify_error(line: str):
    value = str(line or "").lower()
    if any(marker in value for marker in ("rights_operator", "rights_gate", "بوابة الحقوق", "مرجع إثبات", "الإقرار")):
        return "rights"
    if any(marker in value for marker in ("quota", "الحصة", "الحصة اليومية", "نفدت الحصة")):
        return "youtube_quota"
    if any(marker in value for marker in ("facebook", "فيسبوك", "cookie", "cookies", "checkpoint", "تسجيل الدخول")):
        if any(marker in value for marker in NETWORK_ERROR_MARKERS):
            return "facebook_network"
        if any(marker in value for marker in ("cookie", "cookies", "login", "checkpoint", "جلسة", "تسجيل الدخول")):
            return "facebook_auth"
    if any(marker in value for marker in ("youtube", "google oauth", "client_secrets", "رمز يوتيوب", "تفويض يوتيوب")):
        return "youtube_auth"
    if any(marker in value for marker in ("disk", "مساحة التخزين", "مساحة كافية", "no space")):
        return "disk"
    if any(marker in value for marker in ("download", "تنزيل", "السحب", "فشل السحب")):
        return "download"
    if any(marker in value for marker in ("upload", "رفع الفيديو", "فشل رفع", "فشل الرفع")):
        return "upload"
    return "runtime"


def _safe_error_excerpt(line: str) -> str:
    text = " ".join(str(line or "").replace("\x00", " ").split())
    text = SECRET_HEADER_PATTERN.sub(lambda match: f"{match.group(1)}=[مخفي]", text)
    text = SECRET_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}=[مخفي]", text)
    text = PRIVATE_FIELD_PATTERN.sub(lambda match: f"{match.group(1)}=[محتوى محذوف]", text)
    text = JWT_PATTERN.sub("[رمز مخفي]", text)

    def hide_url(match):
        try:
            parsed = urlsplit(match.group(0))
            return f"{parsed.scheme}://{parsed.hostname}" if parsed.hostname else "[رابط مخفي]"
        except ValueError:
            return "[رابط مخفي]"

    text = URL_PATTERN.sub(hide_url, text)
    text = LOCAL_PATH_PATTERN.sub("[مسار محلي مخفي]", text)
    return text[:240]


def _problem_lines(root: Path):
    records = []
    log_sources = []
    for name, backup_count in (("automation.log", 5), ("errors.log", 3)):
        log_sources.extend(root / f"{name}.{index}" for index in range(backup_count, 0, -1))
        log_sources.append(root / name)
    for path in log_sources:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        offset = max(0, len(lines) - 500)
        for line_number, line in enumerate(lines[-500:], start=offset + 1):
            if any(marker in line.lower() for marker in ERROR_MARKERS):
                records.append({"source_file": path.name, "line_number": line_number, "message": line})
    return records


def _error_summaries(root: Path):
    counts = Counter()
    latest = {}
    for record in _problem_lines(root):
        line = record["message"]
        category = _classify_error(line)
        counts[category] += 1
        stamp = re.search(r"(?:\[(\d{2}:\d{2}:\d{2})\]|(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}))", line)
        value = (stamp.group(1) or stamp.group(2)) if stamp else "وقت غير متاح"
        latest[category] = {
            "category": category,
            "last_seen": value,
            "source_file": record["source_file"],
            "line_number": record["line_number"],
            "detail": _safe_error_excerpt(line),
        }
    return [
        {**latest[category], "count": counts[category]}
        for category in sorted(counts, key=lambda key: (-counts[key], key))
    ]


def _rights_settings(config):
    return {
        "rights_gate_mode": _cfg(config, "STUDIO", "RIGHTS_GATE_MODE", "strict"),
        "rights_operator_basis": _cfg(config, "STUDIO", "RIGHTS_OPERATOR_BASIS", "owned"),
        "rights_operator_statement": _cfg(
            config, "STUDIO", "RIGHTS_OPERATOR_STATEMENT", DEFAULT_OPERATOR_RIGHTS_STATEMENT
        ),
        "rights_operator_evidence": _cfg(config, "STUDIO", "RIGHTS_OPERATOR_EVIDENCE", ""),
        "review_before_upload": _cfg_bool(config, "STUDIO", "REVIEW_BEFORE_UPLOAD", True),
        "dry_run": _cfg_bool(config, "SETTINGS", "DRY_RUN", False),
    }


def collect_diagnostics(app_dir: str, version: str = APP_VERSION, now: datetime | None = None) -> dict:
    root = Path(app_dir).resolve()
    created = now or datetime.now().astimezone()
    config, config_state = _load_config(root)
    network_timeout = DEFAULT_NETWORK_PREFLIGHT_TIMEOUT
    try:
        network_timeout = max(
            10,
            min(
                config.getint(
                    "WATCH",
                    "NETWORK_PREFLIGHT_TIMEOUT",
                    fallback=DEFAULT_NETWORK_PREFLIGHT_TIMEOUT,
                ),
                15,
            ),
        )
    except (ValueError, configparser.Error):
        network_timeout = DEFAULT_NETWORK_PREFLIGHT_TIMEOUT

    facebook = probe_service("www.facebook.com", 443, timeout=network_timeout)
    youtube = probe_service("www.googleapis.com", 443, timeout=network_timeout)
    proxy_state, proxy_host, proxy_port = _proxy_endpoint(_cfg(config, "WATCH", "PROXY", ""))
    proxy = None
    if proxy_host:
        proxy = probe_service(proxy_host, proxy_port, timeout=network_timeout)

    cookies_enabled = _cfg_bool(config, "STUDIO", "COOKIES_ENABLED", False)
    cookies_path_value = _cfg(config, "STUDIO", "COOKIES_FILE", "cookies.txt")
    cookies_path = _project_path(root, cookies_path_value)
    cookies = facebook_cookie_setup_status(cookies_enabled, str(cookies_path))

    pages, pages_state = _file_json(root / "pages.json", [])
    if isinstance(pages, list):
        valid_pages = [item for item in pages if isinstance(item, dict)]
        page_counts = {"total": len(valid_pages), "enabled": sum(bool(item.get("enabled", True)) for item in valid_pages)}
    else:
        page_counts = {"total": 0, "enabled": 0}
        pages_state = "invalid"

    queue = _queue_summary(root)
    rights_settings = _rights_settings(config)
    rights_errors = validate_automatic_rights_settings(rights_settings)

    video_dir = _project_path(root, _cfg(config, "SETTINGS", "VIDEO_DIRECTORY", "videos"))
    try:
        free_gb = round(shutil.disk_usage(video_dir if video_dir.exists() else root).free / (1024 ** 3), 2)
    except OSError:
        free_gb = None
    try:
        minimum_disk_gb = max(0.0, float(_cfg(config, "SETTINGS", "MIN_DISK_GB", "2")))
    except ValueError:
        minimum_disk_gb = 2.0

    token_files = list(root.glob("*_token.json"))
    client_secrets = _project_path(root, _cfg(config, "SETTINGS", "CLIENT_SECRETS_FILE", "client_secrets.json"))
    proxy_route = {
        "state": proxy_state,
        "reachable": any(item.get("tcp") == "reachable" for item in (proxy or {}).values()) if proxy else None,
    }

    issues = []
    if config_state != "ok":
        issues.append({"category": "configuration", "severity": "error" if config_state == "invalid" else "warning"})
    if rights_errors:
        issues.append({"category": "rights", "severity": "error"})
    direct_facebook_reachable = any(item.get("tcp") == "reachable" for item in facebook.values())
    proxy_reachable = bool(proxy and any(item.get("tcp") == "reachable" for item in proxy.values()))
    if not direct_facebook_reachable and not proxy_reachable:
        issues.append({"category": "facebook_network", "severity": "error"})
    if not direct_facebook_reachable and proxy_state == "not_configured":
        issues.append({"category": "proxy_not_configured", "severity": "warning"})
    if proxy_state == "configured" and proxy is not None and not proxy_reachable:
        issues.append({"category": "proxy_unreachable", "severity": "warning"})
    if cookies_enabled and cookies.get("status") != "ready":
        issues.append({"category": "facebook_auth", "severity": "warning"})
    if not client_secrets.is_file() or not token_files:
        issues.append({"category": "youtube_auth", "severity": "warning"})
    if free_gb is not None and free_gb < minimum_disk_gb:
        issues.append({"category": "disk", "severity": "error"})
    if not page_counts["enabled"]:
        issues.append({"category": "pages", "severity": "error"})
    if queue["state"] == "invalid":
        issues.append({"category": "queue_file", "severity": "error"})
    elif queue["total"]:
        issues.append({"category": "pending_queue", "severity": "warning"})
    if not any(item.get("tcp") == "reachable" for item in youtube.values()):
        issues.append({"category": "youtube_network", "severity": "error"})
    if proxy_state == "invalid":
        issues.append({"category": "proxy_configuration", "severity": "error"})
    if not _cfg_bool(config, "WATCH", "NETWORK_PREFLIGHT", True):
        issues.append({"category": "network_preflight_disabled", "severity": "warning"})
    if not direct_facebook_reachable and proxy_reachable:
        issues.append({"category": "facebook_proxy_unverified", "severity": "warning"})

    return {
        "created": created.isoformat(timespec="seconds"),
        "version": str(version),
        "platform": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "config_state": config_state,
        "network_timeout_seconds_per_family": network_timeout,
        "force_ipv4": _cfg_bool(config, "WATCH", "FORCE_IPV4", True),
        "network_preflight_enabled": _cfg_bool(config, "WATCH", "NETWORK_PREFLIGHT", True),
        "facebook_network": facebook,
        "youtube_network": youtube,
        "proxy": proxy_route,
        "cookies": {
            "enabled": cookies_enabled,
            "status": cookies.get("status"),
            "active_cookie_count": cookies.get("active_cookie_count", 0),
            "expired_cookie_count": cookies.get("expired_cookie_count", 0),
            "has_session_pair": cookies.get("status") == "ready",
        },
        "youtube_auth": {"client_secrets_file_present": client_secrets.is_file(), "token_file_count": len(token_files)},
        "rights": {"mode": rights_settings["rights_gate_mode"], "configuration_errors": rights_errors},
        "pages": {**page_counts, "state": pages_state},
        "queue": queue,
        "disk": {"free_gb": free_gb, "minimum_gb": minimum_disk_gb},
        "errors": _error_summaries(root),
        "issues": issues,
    }


def format_report(data: dict) -> str:
    def fmt_state(value):
        mapping = {
            "ok": "سليم",
            "missing": "غير موجود",
            "invalid": "غير صالح",
            "not_configured": "غير مضبوط",
            "configured": "مضبوط",
            "reachable": "متاح",
            "not_tested": "لم يُختبر",
            "no_address": "لا توجد عناوين DNS",
            "timeout": "انتهت المهلة",
            "connection_refused": "رفض الاتصال",
            "network_unreachable": "لا يوجد مسار شبكي",
            "dns_failure": "فشل DNS",
            "temporary_dns_failure": "تعذر DNS مؤقتاً",
            "connection_failed": "فشل اتصال TCP",
        }
        return mapping.get(str(value), str(value))

    lines = [
        "# تقرير تشخيصي — FB → YouTube Uploader",
        "",
        f"- الإصدار: `{data['version']}`",
        f"- وقت الإنشاء: `{data['created']}`",
        f"- النظام: `{data['platform']}`",
        f"- Python: `{data['python']}`",
        "",
        "> الخصوصية: لا يعرض التقرير قيم الكوكيز أو محتوى رموز OAuth؛ يذكر حالة ملف الكوكيز وعددها فقط. يحجب الروابط وأنماط الأسرار المعروفة؛ قد تبقى تفاصيل في رسائل الخطأ الحرة، لذلك راجعه قبل مشاركته.",
    ]
    lines.extend(["", "## حالة الجاهزية"])
    if data["issues"]:
        for issue in data["issues"]:
            severity = "مانع" if issue["severity"] == "error" else "تنبيه"
            label = CATEGORY_LABELS.get(issue["category"], issue["category"])
            guidance = CATEGORY_GUIDANCE.get(issue["category"], "")
            lines.append(f"- **{severity} — {label}**: {guidance}")
    else:
        lines.append("- لم يظهر مانع إعدادات أو اتصال واضح في الفحوص المحلية.")
    lines.extend(["", "## ملخص الأعطال"])
    if data["errors"]:
        for error in data["errors"]:
            detail = str(error.get("detail", "")).replace("`", "'")
            location = f"{error.get('source_file', 'سجل محلي')}:{error.get('line_number', '?')}"
            lines.append(
                f"- **{CATEGORY_LABELS.get(error['category'], error['category'])}**: "
                f"{error['count']} رسائل؛ آخر ظهور `{error['last_seen']}` في `{location}`. "
                f"آخر تفاصيل آمنة: `{detail}`. "
                f"{CATEGORY_GUIDANCE.get(error['category'], '')}"
            )
    else:
        lines.append("- لم تُرصد رسائل خطأ أو تحذير محفوظة في السجلات المحلية.")
    lines.extend([
        "",
        "## Facebook",
        f"- اختبار IPv4: DNS `{fmt_state(data['facebook_network']['IPv4']['dns'])}`, "
        f"TCP/443 `{fmt_state(data['facebook_network']['IPv4']['tcp'])}` "
        f"({data['facebook_network']['IPv4']['addresses']} عناوين).",
        f"- اختبار IPv6: DNS `{fmt_state(data['facebook_network']['IPv6']['dns'])}`, "
        f"TCP/443 `{fmt_state(data['facebook_network']['IPv6']['tcp'])}` "
        f"({data['facebook_network']['IPv6']['addresses']} عناوين).",
        f"- مهلة التشغيل الآلي: `{data['network_timeout_seconds_per_family']}` ثانية لكل مسار؛ IPv4 وIPv6 يُختبران على حدة.",
        f"- فحص الشبكة قبل التشغيل التلقائي: `{data['network_preflight_enabled']}`.",
        f"- ملف `cookies.txt` مفعّل: `{data['cookies']['enabled']}`؛ الحالة: `{data['cookies']['status']}`؛ "
        f"الكوكيز النشطة/المنتهية: `{data['cookies']['active_cookie_count']}/{data['cookies']['expired_cookie_count']}`.",
        "- وجود زوج c_user/xs لا يثبت أن Facebook قبل الجلسة؛ الاختبار الفعلي أو التحقق الأمني يحتاج إلى المتصفح.",
        "",
        "## YouTube والإعدادات",
        f"- اتصال Google API عبر TCP/443: IPv4 `{fmt_state(data['youtube_network']['IPv4']['tcp'])}`, "
        f"IPv6 `{fmt_state(data['youtube_network']['IPv6']['tcp'])}`.",
        f"- ملف OAuth الأساسي موجود: `{data['youtube_auth']['client_secrets_file_present']}`؛ "
        f"عدد ملفات التفويض المحلي: `{data['youtube_auth']['token_file_count']}` (لم تُقرأ محتوياتها).",
        f"- وضع بوابة الحقوق: `{data['rights']['mode']}`؛ إعدادات ناقصة: `{len(data['rights']['configuration_errors'])}`.",
    ])
    for error in data["rights"]["configuration_errors"]:
        lines.append(f"  - {error}")
    lines.extend([
        "",
        "## الصفحات والانتظار والتخزين",
        f"- الصفحات: `{data['pages']['enabled']}` مفعّلة من `{data['pages']['total']}`؛ حالة الملف: `{fmt_state(data['pages']['state'])}`.",
        f"- قائمة العناصر المعلّقة: `{data['queue']['total']}`؛ حالة الملف: `{fmt_state(data['queue']['state'])}`.",
    ])
    for status, count in sorted(data["queue"]["by_status"].items()):
        lines.append(f"  - `{status}`: {count}")
    free_gb = data["disk"]["free_gb"]
    lines.append(
        f"- مساحة القرص المتاحة: `{free_gb if free_gb is not None else 'غير متاحة'} GB`; "
        f"الحد الأدنى المضبوط: `{data['disk']['minimum_gb']} GB`."
    )
    lines.extend([
        "",
        "## تشخيص الوكيل",
        f"- الوكيل: `{fmt_state(data['proxy']['state'])}`؛ وصول TCP إلى نقطة الوكيل: "
        f"`{fmt_state('reachable' if data['proxy']['reachable'] else 'connection_failed') if data['proxy']['reachable'] is not None else 'لم يُختبر'}`.",
        "- فحص منفذ الوكيل لا يثبت أن الوكيل ينشئ نفقاً إلى Facebook؛ لا يتضمن التقرير عنوان الوكيل أو بياناته.",
        "",
        "## خطوات المعالجة",
    ])
    steps = []
    for issue in data["issues"]:
        guidance = CATEGORY_GUIDANCE.get(issue["category"])
        if guidance and guidance not in steps:
            steps.append(guidance)
    for error in data["errors"]:
        guidance = CATEGORY_GUIDANCE.get(error["category"])
        if guidance and guidance not in steps:
            steps.append(guidance)
    if steps:
        lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    else:
        lines.append("- لم يُكتشف مانع إعدادات واضح؛ صلاحية جلسة Facebook والرفع الحقيقي يظلان بحاجة إلى اختبار داخل التطبيق.")
    lines.extend([
        "",
        "أنشأ التطبيق هذا التقرير محلياً فقط. لم يُرسل أي شيء إلى خدمة خارجية.",
        "",
    ])
    return "\n".join(lines)


def write_diagnostic_report(app_dir: str, version: str = APP_VERSION) -> Path:
    root = Path(app_dir).resolve()
    data = collect_diagnostics(str(root), version=version)
    report = format_report(data)
    report_dir = root / REPORT_DIRECTORY
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    destination = report_dir / f"diagnostic-report-{stamp}.md"
    fd, temp_name = tempfile.mkstemp(prefix=".diagnostic-", suffix=".tmp", dir=report_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as target:
            target.write(report)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return destination


if __name__ == "__main__":
    report_path = write_diagnostic_report(str(Path(__file__).resolve().parent))
    print(f"تقرير التشخيص المحلي: {report_path}")
