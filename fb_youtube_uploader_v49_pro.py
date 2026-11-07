"""
╔══════════════════════════════════════════════════════════════╗
║   FB → YouTube Uploader  v4.48.6 PRO — Reliable Core      ║
║   سجل رفع آمن • سحب أوضح • إعدادات اعتماديات موحّدة         ║
╚══════════════════════════════════════════════════════════════╝
"""

from dependency_check import missing_runtime_dependencies


def _check_dependencies():
    missing = missing_runtime_dependencies()
    if missing:
        print("حزم ناقصة أو غير قابلة للاستيراد: " + ", ".join(missing))
        print("الحل: شغّل setup.bat (ويندوز) أو ./setup.sh، ثم أعد التشغيل.")
        print("أو ثبّتها يدوياً داخل البيئة الافتراضية عبر ملف requirements-lock.txt.")
        raise SystemExit(2)


_check_dependencies()

import argparse
import time, os, re, json, logging, random, threading, queue, configparser, shutil, hashlib, unicodedata, math
import subprocess, sys, tempfile
import html as html_module
import urllib.error, urllib.request
from urllib.parse import urlparse
import glob
from datetime import datetime, timedelta, timezone
from config_store import ConfigRecoveryError, read_config, write_config_atomically
from file_security import open_private_append, restrict_file_permissions
from pipeline_errors import PipelineFailure
from safe_logging import SecureRotatingFileHandler, mask_secret, redact_sensitive_text, secure_log_handler
from settings_validation import validate_configuration
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat, features

import arabic_reshaper
from bidi.algorithm import get_display

import yt_dlp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from facebook_auth import (
    facebook_cookie_setup_status,
    inspect_facebook_cookies,
    load_facebook_cookies as load_cookies_to_driver,
)
from facebook_preflight import probe_facebook_page_access, safe_facebook_page_url
from facebook_scraper import (
    FacebookLoginRequired,
    FacebookNetworkUnavailable,
    facebook_auth_block_reason,
    DEFAULT_NETWORK_PREFLIGHT_TIMEOUT,
    canonicalize_facebook_video_url,
    facebook_media_id,
    facebook_network_route,
    is_transient_facebook_error,
)

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

import tkinter as tk
from tkinter import messagebox, ttk, filedialog, simpledialog
from content_safety import (
    DEFAULT_OPERATOR_RIGHTS_STATEMENT,
    RIGHTS_BASIS_LABELS,
    extract_video_fingerprint,
    find_video_fingerprint_matches,
    build_auto_rights_record,
    build_operator_rights_record,
    get_rights_record,
    new_rights_record,
    rights_record_errors,
    save_rights_record,
    migrate_legacy_rights_gate_config,
    validate_automatic_rights_settings,
)
from content_review_bridge import (
    build_review_caption,
    validate_report as validate_content_review,
)
from diagnostic_report import write_diagnostic_report
from control_plane import (
    AutomationAlreadyRunning,
    AutomationRunLock,
    automation_exit_code,
    begin_upload_attempt,
    deduplicate_queue_items,
    mark_upload_result_unknown,
    recover_interrupted_uploads,
    reconcile_upload_result,
    extract_youtube_video_id,
    youtube_video_belongs_to_current_channel,
    remove_queue_item,
    upsert_queue_item,
    UPLOAD_IN_PROGRESS,
    UPLOAD_RESULT_UNKNOWN,
    UPLOAD_REJECTED,
    UPLOAD_NOT_STARTED,

    build_upload_review_warnings,
    release_policy_holds,
    release_rights_holds,
    run_policy_review,
    run_readiness_check,
    run_automation_preflight,
    split_duplicate_review_queue,
    validate_content_review_settings,
    validate_publication_controls,
)

# ════════════════════════════════════════════════════
# CONSTANTS & CONFIG
# ════════════════════════════════════════════════════
BG      = "#0d1117"
CARD    = "#161b22"
PANEL   = "#1c2128"
ACCENT  = "#2f6feb"
BORDER  = "#30363d"
TXT     = "#e6e8eb"
TXT2    = "#9aa4b2"
INP     = "#21262d"
ERR     = "#d1242f"
SUCCESS = "#1a7f37"
WARN    = "#9a6700"
INFO    = "#8b949e"

FB  = ("Segoe UI", 10)
FS  = ("Segoe UI", 12, "bold")
FSM = ("Segoe UI", 9)
FM  = ("Consolas", 10)

__version__ = "4.48.6"
APP_DIRECTORY = os.path.dirname(os.path.abspath(__file__))


def _app_path(path):
    """Resolve relative application files beside the program, not the caller's CWD."""
    value = os.path.expandvars(os.path.expanduser(os.fspath(path)))
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.abspath(os.path.join(APP_DIRECTORY, value))

ERROR_LOG_FILE = _app_path("errors.log")
CONFIG_FILE = _app_path("config.ini")


_FACEBOOK_NETWORK_ROUTE: str | None = None
try:
    config, recovered_config_from = read_config(CONFIG_FILE)
except ConfigRecoveryError as error:
    print(f"توقف التشغيل: {error}")
    raise SystemExit(2) from error
if recovered_config_from and os.path.abspath(recovered_config_from) != os.path.abspath(CONFIG_FILE):
    print(f"استُعيد config.ini من النسخة الاحتياطية {recovered_config_from.name}.")

SCOPES              = [
    "https://www.googleapis.com/auth/youtube.upload",
    # readonly للتحقق من حالة الفيديو/الجدولة بعد الرفع (v4.9)
    "https://www.googleapis.com/auth/youtube.readonly",
]
VIDEO_DIRECTORY     = _app_path(config.get('SETTINGS', 'VIDEO_DIRECTORY', fallback='videos/').strip() or 'videos/')
CLIENT_SECRETS_FILE = _app_path(config.get('SETTINGS', 'CLIENT_SECRETS_FILE', fallback='client_secrets.json').strip() or 'client_secrets.json')
UPLOADED_IDS_FILE   = _app_path("uploaded_ids.txt")
SEEN_IDS_FILE       = _app_path("seen_ids.txt")
PAGES_FILE          = _app_path("pages.json")
ACCOUNTS_FILE       = _app_path("accounts.json")
STATS_FILE          = _app_path("stats.json")
PAGE_STATUS_FILE    = _app_path("page_status.json")
STATE_FILE          = _app_path("state.json")
FAILED_QUEUE_FILE   = _app_path("failed_queue.json")
UPLOAD_RECEIPTS_FILE = _app_path("upload_receipts.json")
HISTORY_FILE        = _app_path("history.jsonl")
HISTORY_MAX_BYTES   = 10_000_000   # تدوير الأرشيف عند 10MB (v4.24)
HISTORY_BACKUPS     = 3
FAILED_QUEUE_MAX_AGE_DAYS = 14    # تقليم عناصر الفشل الأقدم من هذا العمر (v4.24)
CHANNEL_INDEX_FILE  = _app_path("channel_video_index.json")
RIGHTS_MANIFEST_FILE = _app_path("rights_manifest.json")
CHANNEL_INDEX_TTL_SECONDS = 24 * 60 * 60
# بعد فشل تحديث فهرس القناة (مثلاً الشبكة مقطوعة) لا تُعاد المحاولة
# لكل عنصر في الدورة؛ تُستخدم النسخة المحلية بهدوء حتى انقضاء هذه المدة.
CHANNEL_INDEX_RETRY_SECONDS = 10 * 60
_channel_index_fail = {"account": "", "at": 0.0}
MAX_RETRIES         = 3
RETRY_DELAY         = 8
# عدد مرات استئناف الرفع داخل نفس الجلسة عند انقطاع مؤقت (لا يُعيد الملف من الصفر).
MAX_CHUNK_RETRIES   = 5

# الصفحات المحفوظة في المشروع — تُكتب تلقائياً في pages.json عند أول تشغيل
DEFAULT_PAGES = [
    {"url": "https://www.facebook.com/elwataniatvweb/reels/", "name": "الوطنية TV",
     "scroll": 5, "limit": 30, "enabled": True},
    {"url": "https://www.facebook.com/ElwataniaSport/reels/", "name": "الوطنية سبورت",
     "scroll": 5, "limit": 30, "enabled": True},
]

os.makedirs(VIDEO_DIRECTORY, exist_ok=True)

import ai_client
import notify
from texttools import (AR_STOPWORDS, BIDI_OVERRIDE_CHARS, EDGE_SEPARATORS,
                       FILLER_PATTERNS, HASHTAG_RE, INVALID_TITLE_CHARS,
                       JUNK_STATS, MENTION_RE, SYMBOL_RUN_RE, URL_RE,
                       YOUTUBE_DESCRIPTION_MAX, YOUTUBE_TAGS_MAX, YOUTUBE_TITLE_MAX,
                       _arabic_plural, _clean_title_component,
                       _collapse_symbol_runs, _compose_title_parts,
                       _drop_truncated_brand, _is_unicode_word_char,
                       _name_variants, _remove_hashtags, _truncate_smart,
                       _truncate_suffix_component, _unicode_word_tokens,
                       pick_caption_line,
                       _youtube_tag_cost, build_description, build_title,
                       clean_title, ensure_source_attribution,
                       extract_hashtags, is_generic_title, normalize_generated_title,
                       normalize_youtube_tags, normalize_youtube_title,
                       optimize_title, prepare_public_title,
                       sanitize_public_text)

__all__ = [
    "CHANNEL_LIMIT_REASONS", "FACEBOOK_HOSTS", "NON_RETRYABLE_STATUSES",
    "QUOTA_REASONS", "RETRYABLE_REASONS", "_http_reason", "_http_status",
    "_safe_normalize_channel_url", "classify_upload_error",
    "normalize_channel_url", "order_reels", "reel_id_from_url_any",
    "AR_STOPWORDS", "BIDI_OVERRIDE_CHARS", "EDGE_SEPARATORS",
    "FILLER_PATTERNS", "HASHTAG_RE", "INVALID_TITLE_CHARS", "JUNK_STATS",
    "MENTION_RE", "SYMBOL_RUN_RE", "URL_RE", "YOUTUBE_TAGS_MAX",
    "YOUTUBE_TITLE_MAX", "_arabic_plural", "_clean_title_component",
    "_collapse_symbol_runs", "_compose_title_parts",
    "_drop_truncated_brand", "_http_reason", "_http_status",
    "_is_unicode_word_char", "_name_variants", "_remove_hashtags",
    "_safe_normalize_channel_url", "_truncate_smart", "_truncate_suffix_component",
    "_unicode_word_tokens", "_youtube_tag_cost", "build_description",
    "build_title", "classify_upload_error", "clean_title",
    "ensure_source_attribution", "extract_hashtags", "is_generic_title",
    "normalize_channel_url", "normalize_generated_title", "normalize_youtube_tags",
    "normalize_youtube_title", "optimize_title", "order_reels",
    "prepare_public_title", "reel_id_from_url_any",
    "sanitize_public_text",
]
# لا نكتب ملف errors.log عند تشغيل الاختبارات حتى لا يمتلئ بسجلات وهمية.
if "pytest" not in sys.modules:
    _log_handler = SecureRotatingFileHandler(ERROR_LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    _log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(secure_log_handler(_log_handler))
logging.getLogger().setLevel(logging.INFO)

# توقيت المحيط الهادئ — حصة يوتيوب تُصفَّر منتصف الليل بهذا التوقيت
try:
    from zoneinfo import ZoneInfo
    PT_TZ = ZoneInfo("America/Los_Angeles")
except Exception as error:
    PT_TZ = None
    PT_TZ_ERROR = redact_sensitive_text(error)
else:
    PT_TZ_ERROR = ""

def pt_now():
    """الوقت الحالي بتوقيت المحيط الهادئ باستخدام قاعدة مناطق زمنية دقيقة."""
    if PT_TZ is None:
        raise RuntimeError("قاعدة America/Los_Angeles غير متاحة؛ ثبّت tzdata ثم أعد التشغيل.") from None
    return datetime.now(PT_TZ)

# ════════════════════════════════════════════════════
# HELPERS — DATA & UTILS
# ════════════════════════════════════════════════════
def _read_json(path, default, *, expected_type=None, fail_closed=False):
    path = os.fspath(path)
    if not os.path.exists(path):
        return default

    def read_candidate(candidate):
        if os.path.islink(candidate) or not os.path.isfile(candidate):
            raise OSError("state file is not a regular file")
        with open(candidate, "r", encoding="utf-8") as source:
            value = json.load(source)
        if expected_type is not None and not isinstance(value, expected_type):
            raise ValueError("state file has an unexpected structure")
        return value

    try:
        value = read_candidate(path)
        restrict_file_permissions(path)
        return value
    except Exception as primary_error:
        for index in range(1, 4):
            backup = f"{path}.bak{index}"
            try:
                value = read_candidate(backup)
                _write_json(path, value, backup=False)
                logging.warning("استُعيد %s من النسخة الاحتياطية رقم %s", os.path.basename(path), index)
                return value
            except Exception:
                continue
        if fail_closed:
            raise RuntimeError(
                f"تعذرت قراءة {os.path.basename(path)}؛ أُوقف التشغيل لحماية البيانات ومنع إعادة الرفع."
            ) from primary_error
        logging.warning("تعذرت قراءة %s؛ استُخدمت قيمة افتراضية.", os.path.basename(path))
        return default


def _write_json(path, data, *, backup=True):
    path = os.fspath(path)
    if os.path.islink(path):
        raise OSError("refusing to replace a symbolic-link state file")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    critical_names = {
        "failed_queue.json", "upload_receipts.json", "pages.json", "accounts.json",
        "state.json", "channel_video_index.json",
    }
    if backup and os.path.exists(path):
        if not _backup_json(path) and os.path.basename(path) in critical_names:
            raise OSError("required state backup could not be created")
    fd, temporary_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        restrict_file_permissions(path)
        if os.name == "posix":
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def _backup_json(path, keep=3):
    path = os.fspath(path)
    if not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) <= 0:
        return True
    directory = os.path.dirname(os.path.abspath(path))
    fd, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.backup.", suffix=".tmp", dir=directory
    )
    try:
        with open(path, "rb") as source, os.fdopen(fd, "wb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        restrict_file_permissions(temporary_path)
        for index in range(keep - 1, 0, -1):
            src, dst = f"{path}.bak{index}", f"{path}.bak{index + 1}"
            if os.path.islink(src) or os.path.islink(dst):
                raise OSError("state backup is a symbolic link")
            if os.path.exists(src):
                os.replace(src, dst)
                restrict_file_permissions(dst)
        first_backup = f"{path}.bak1"
        if os.path.islink(first_backup):
            raise OSError("state backup is a symbolic link")
        os.replace(temporary_path, first_backup)
        restrict_file_permissions(first_backup)
        return True
    except OSError:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        logging.warning("تعذر إنشاء نسخة احتياطية لملف %s.", os.path.basename(path))
        return False


def _harden_secret_file(path, log_cb=None):
    """يقيّد أذونات الملفات السرية ونسخها الاحتياطية على POSIX."""
    if os.name != "posix":
        return
    base_path = os.fspath(path)
    paths = [base_path, *(f"{base_path}.bak{index}" for index in range(1, 4))]
    for secret_path in paths:
        if not os.path.isfile(secret_path):
            continue
        try:
            os.chmod(secret_path, 0o600)
            if log_cb and secret_path == base_path:
                log_cb(f"ضُبطت أذونات {os.path.basename(secret_path)} (600).")
        except OSError as error:
            logging.debug("تعذر ضبط أذونات %s: %s", secret_path, error)

def load_accounts():
    accounts = _read_json(ACCOUNTS_FILE, ["account1"], expected_type=list, fail_closed=True)
    if any(not isinstance(account, str) or not account.strip() for account in accounts):
        raise RuntimeError("accounts.json contains invalid entries; no upload was started.")
    return accounts or ["account1"]

def save_accounts(accounts):
    if not isinstance(accounts, list) or any(not isinstance(account, str) or not account.strip() for account in accounts):
        raise ValueError("قائمة الحسابات غير صالحة.")
    _write_json(ACCOUNTS_FILE, accounts)

ID_FILE_MAX_ENTRIES = 100_000   # سقف ملفات المعرفات قبل تقليم الأقدم (v4.48.5)
_ID_SET_CACHE = {}


def _trim_id_file(path, max_entries=ID_FILE_MAX_ENTRIES):
    """يقلم ملف المعرفات احتفاظاً بأحدث السجلات عند تجاوز السقف."""
    try:
        # كل سطر سجل يحتاج بايتين على الأقل (حرف + سطر جديد)؛ ما دون هذا الحجم
        # لا يمكن أن يتجاوز السقف أصلًا، فنكتفي بالعودة دون قراءة الملف.
        if not os.path.exists(path) or os.path.getsize(path) < max_entries * 2:
            return
        with open(path, "r", encoding="utf-8") as source:
            lines = [line for line in source if line.strip()]
        if len(lines) <= max_entries:
            return
        kept = lines[-max_entries:]
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                target.writelines(kept)
                target.flush()
                os.fsync(target.fileno())
            restrict_file_permissions(tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        logging.exception("تعذر تقليم ملف المعرفات %s", os.path.basename(path))


def _load_id_set(path):
    if not os.path.exists(path):
        return set()
    if os.path.islink(path):
        raise RuntimeError(f"{os.path.basename(path)} is a symbolic link; no upload was started.")
    restrict_file_permissions(path)
    cached = _ID_SET_CACHE.get(path)
    if cached is not None:
        return set(cached)
    try:
        with open(path, "r", encoding="utf-8") as source:
            values = {line.strip() for line in source if line.strip()}
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"Could not read {os.path.basename(path)}; no upload was started.") from error
    if any("\n" in value or "\r" in value for value in values):
        raise RuntimeError(f"{os.path.basename(path)} contains invalid records; no upload was started.")
    _ID_SET_CACHE[path] = frozenset(values)
    return set(values)

def _append_id(path, vid):
    value = str(vid or "").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("معرّف السجل فارغ أو غير صالح.")
    if value in _load_id_set(path):
        return False
    try:
        with open_private_append(path) as output:
            output.write(f"{value}\n")
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise RuntimeError(f"Could not securely update {os.path.basename(path)}.") from error
    cached = _ID_SET_CACHE.get(path)
    _ID_SET_CACHE[path] = frozenset((cached or set()) | {value})
    _trim_id_file(path)
    return True

def load_uploaded_ids():
    return _load_id_set(UPLOADED_IDS_FILE)

def save_uploaded_id(video_id):
    _append_id(UPLOADED_IDS_FILE, video_id)


def load_upload_receipts(path=None):
    path = UPLOAD_RECEIPTS_FILE if path is None else path
    store = _read_json(
        path,
        {"schema_version": 1, "receipts": {}},
        expected_type=dict,
        fail_closed=True,
    )
    if store.get("schema_version") != 1 or not isinstance(store.get("receipts"), dict):
        raise RuntimeError("upload_receipts.json has an unsupported or invalid structure; no upload was started.")
    receipts = {}
    for source_id, record in store["receipts"].items():
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(record, dict):
            raise RuntimeError("upload_receipts.json contains an invalid receipt; no upload was started.")
        video_id = extract_youtube_video_id(str(record.get("youtube_id") or ""))
        if video_id is None or record.get("source_id") != source_id:
            raise RuntimeError("upload_receipts.json contains an invalid receipt; no upload was started.")
        receipts[source_id] = dict(record)
    return receipts


def save_upload_receipts(receipts, path=None):
    path = UPLOAD_RECEIPTS_FILE if path is None else path
    if not isinstance(receipts, dict):
        raise ValueError("سجل إيصالات YouTube غير صالح.")
    validated = {}
    video_ids = set()
    for source_id, record in receipts.items():
        source = str(source_id or "").strip()
        if not source or not isinstance(record, dict):
            raise ValueError("سجل إيصالات YouTube يحتوي مدخلاً غير صالح.")
        video_id = extract_youtube_video_id(str(record.get("youtube_id") or ""))
        if video_id is None or record.get("source_id") != source or video_id in video_ids:
            raise ValueError("سجل إيصالات YouTube يحتوي معرّفاً غير صالح أو مكرراً.")
        video_ids.add(video_id)
        validated[source] = dict(record)
    _write_json(path, {"schema_version": 1, "receipts": validated})


def persist_upload_receipt(source_id, youtube_video_id, *, account="", url="", page="", title="", path=None):
    path = UPLOAD_RECEIPTS_FILE if path is None else path
    source = str(source_id or "").strip()
    video_id = extract_youtube_video_id(str(youtube_video_id or ""))
    if not source or video_id is None:
        raise ValueError("لا يمكن حفظ إيصال رفع بلا معرّف مصدر ومعرّف YouTube صالح.")
    receipts = load_upload_receipts(path)
    existing = receipts.get(source)
    if existing:
        if existing.get("youtube_id") == video_id:
            return existing, False
        raise RuntimeError("يوجد إيصال مختلف للمصدر نفسه؛ لا تُعد الرفع قبل المطابقة اليدوية.")
    if any(record.get("youtube_id") == video_id for record in receipts.values()):
        raise RuntimeError("معرّف YouTube مرتبط بمصدر آخر؛ أُوقف حفظ الإيصال منعاً للتكرار.")
    record = {
        "source_id": source,
        "youtube_id": video_id,
        "account": str(account or "")[:120],
        "url": str(url or "")[:2048],
        "page": str(page or "")[:240],
        "title": str(title or "")[:100],
        "status": "uploaded",
        "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    receipts[source] = record
    save_upload_receipts(receipts, path)
    return record, True

def load_seen_ids():
    """معرّفات استُنفدت محاولاتها — لا تُعاد معالجتها أبداً."""
    return _load_id_set(SEEN_IDS_FILE)

def save_seen_id(video_id):
    _append_id(SEEN_IDS_FILE, video_id)

def load_stats():
    stats = _read_json(STATS_FILE, {}, expected_type=dict)
    stats.setdefault("total_uploaded", 0)
    stats.setdefault("total_failed", 0)
    stats.setdefault("total_downloaded", 0)
    stats.setdefault("cycles", 0)
    stats.setdefault("daily", {"date": "", "uploaded": 0})
    return stats

def save_stats(stats):
    _write_json(STATS_FILE, stats)


def _is_valid_facebook_status_url(value):
    return safe_facebook_page_url(value) is not None


def _clean_page_status_records(records):
    if not isinstance(records, dict):
        return {}
    cleaned = {}
    for url, record in records.items():
        if not isinstance(url, str) or not _is_valid_facebook_status_url(url) or not isinstance(record, dict):
            continue
        try:
            found = max(0, int(record.get("found", 0)))
        except (TypeError, ValueError):
            found = 0
        try:
            new = max(0, int(record.get("new", 0)))
        except (TypeError, ValueError):
            new = 0
        cleaned[url.strip()[:2048]] = {
            "time": str(record.get("time", ""))[:40],
            "found": found,
            "new": new,
            "status": str(record.get("status", ""))[:500],
        }
    if len(cleaned) > 500:
        cleaned = dict(sorted(cleaned.items(), key=lambda item: item[1]["time"])[-500:])
    return cleaned


def load_page_status(path=PAGE_STATUS_FILE):
    return _clean_page_status_records(_read_json(path, {}, expected_type=dict, fail_closed=True))


def save_page_status(records, path=PAGE_STATUS_FILE):
    _write_json(path, _clean_page_status_records(records))

def bump_daily_uploads(stats):
    today = pt_now().date().isoformat()
    daily = stats.setdefault("daily", {"date": today, "uploaded": 0})
    if daily.get("date") != today:
        daily["date"], daily["uploaded"] = today, 0
    daily["uploaded"] += 1

def load_state():
    return _read_json(STATE_FILE, {}, expected_type=dict, fail_closed=True)

def save_state(state):
    _write_json(STATE_FILE, state)

def load_failed_queue():
    """عناصر فشلت وستُعاد محاولتها في الدورات القادمة."""
    items = _read_json(FAILED_QUEUE_FILE, [], expected_type=list, fail_closed=True)
    if any(
        not isinstance(item, dict)
        or not str(item.get("id") or "").strip()
        or not str(item.get("url") or "").strip()
        for item in items
    ):
        raise RuntimeError("failed_queue.json contains invalid entries; no upload was started.")
    return deduplicate_queue_items(items)

def save_failed_queue(items):
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("قائمة إعادة المحاولة غير صالحة؛ لم تُحفظ.")
    _write_json(FAILED_QUEUE_FILE, deduplicate_queue_items(items))

def _rotate_history_if_needed(max_bytes=10_000_000, backups=3):
    """يدوّر الأرشيف عند تجاوز الحد بدل أن ينمو بلا سقف (مثل errors.log)."""
    try:
        if not os.path.exists(HISTORY_FILE) or os.path.getsize(HISTORY_FILE) < max_bytes:
            return
        for i in range(backups - 1, 0, -1):
            src, dst = f"{HISTORY_FILE}.bak{i}", f"{HISTORY_FILE}.bak{i + 1}"
            if os.path.exists(src):
                shutil.move(src, dst)
        shutil.move(HISTORY_FILE, f"{HISTORY_FILE}.bak1")
    except OSError:
        logging.exception("تعذر تدوير ملف الأرشيف")


def append_history(record):
    """أرشيف دائم لكل عملية (سطر JSON لكل حدث) مع دوران تلقائي."""
    record = dict(record)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    _rotate_history_if_needed()
    try:
        with open_private_append(HISTORY_FILE) as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        return True
    except OSError:
        logging.exception("تعذرت الكتابة في ملف الأرشيف")
        return False


def prune_failed_queue(items, max_age_days=14):
    """يحذف من قائمة الفشل العناصر الأقدم من العمر المسموح؛ عناوين ميتة لا تعود للدوران."""
    if not isinstance(items, list) or max_age_days <= 0:
        return items
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
    kept, dropped = [], []
    for item in items:
        ts = item.get("failed_at")
        try:
            stale = float(ts) < cutoff
        except (TypeError, ValueError):
            stale = False
        if stale and item.get("last_error") not in {
            UPLOAD_IN_PROGRESS, UPLOAD_RESULT_UNKNOWN, UPLOAD_REJECTED, "abandoned",
        }:
            dropped.append(item)
        else:
            kept.append(item)
    for item in dropped:
        archived = append_history({"source_id": item.get("id", ""), "url": item.get("url", ""),
                                   "page": item.get("page", ""), "status": "expired",
                                   "error": f"تجاوز عمر {max_age_days} يوماً في قائمة الفشل"})
        if not archived:
            kept.append(item)
    if dropped and len(kept) < len(items):
        logging.info("أُسقطت %d عنصراً منتهياً بعد حفظها في الأرشيف", len(items) - len(kept))
    return kept

def load_pages():
    """الصفحات المخزنة؛ عند أول تشغيل تُحفظ الصفحات الافتراضية للمشروع."""
    pages = _read_json(PAGES_FILE, None, expected_type=list, fail_closed=True)
    if not pages:
        pages = [dict(p) for p in DEFAULT_PAGES]
        try:
            save_pages(pages)
        except OSError:
            logging.exception("تعذر حفظ pages.json")
    for p in pages:
        p.setdefault("enabled", True)
        p.setdefault("scroll", 5)
        p.setdefault("limit", 30)
        p.setdefault("name", "")
    return pages

def save_pages(pages):
    _write_json(PAGES_FILE, pages)

def disk_free_gb():
    try:
        return round(shutil.disk_usage(VIDEO_DIRECTORY).free / (1024**3), 2)
    except Exception:
        return 0.0

def reel_id_from_url(url):
    return facebook_media_id(url) or str(url or "").strip()

def filter_new_reels(links, uploaded_ids, seen_ids, queued_ids):
    """يوحّد صيغ روابط فيسبوك ويستبعد العناصر التي رُفعت أو دخلت الطابور سابقاً."""
    fresh, seen_here = [], set()
    for link in links:
        raw = link if isinstance(link, dict) else {"url": link}
        canonical_url = canonicalize_facebook_video_url(str(raw.get("url") or ""))
        if not canonical_url:
            continue
        reel_id = reel_id_from_url(canonical_url)
        if not reel_id or reel_id in seen_here:
            continue
        seen_here.add(reel_id)
        if reel_id in uploaded_ids or reel_id in seen_ids or reel_id in queued_ids:
            continue
        fresh.append({
            "url": canonical_url,
            "id": reel_id,
            "title_hint": str(raw.get("title_hint") or "").strip()[:300],
        })
    return fresh

def cookies_arg(settings):
    """يعيد مسار ملف الكوكيز إن كانت الميزة مفعّلة والملف موجوداً، وإلا None."""
    if not settings.get("cookies_enabled"):
        return None
    path = (settings.get("cookies_file") or "").strip()
    if not path:
        return None
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(APP_DIRECTORY, path)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return None
    _harden_secret_file(path)
    return path


def facebook_cookie_fingerprint(path):
    if not path:
        return None
    try:
        stat = os.stat(path)
        status = inspect_facebook_cookies(path)
    except (OSError, UnicodeError, ValueError):
        return None
    return (
        os.path.normcase(os.path.abspath(path)),
        stat.st_mtime_ns,
        stat.st_size,
        bool(status.get("has_active_session_pair")),
    )


def refresh_facebook_cookie_settings(settings, config_path=None):
    latest = configparser.ConfigParser(interpolation=None)
    path = config_path or os.path.join(APP_DIRECTORY, "config.ini")
    try:
        latest.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return settings
    try:
        settings["cookies_enabled"] = latest.getboolean(
            "STUDIO", "COOKIES_ENABLED", fallback=settings.get("cookies_enabled", False)
        )
    except (configparser.Error, ValueError):
        pass
    settings["cookies_file"] = latest.get(
        "STUDIO", "COOKIES_FILE", fallback=settings.get("cookies_file", "cookies.txt")
    )
    return settings

# ════════════════════════════════════════════════════
# CONTENT DEDUP — منع رفع نفس المقطع من صفحتين
# ════════════════════════════════════════════════════
def content_fingerprint(title):
    """بصمة عنوان عربية موحدة تتجاهل التشكيل وعلامات الترقيم."""
    text = unicodedata.normalize("NFKC", (title or "").casefold())
    text = "".join(char for char in text if not unicodedata.category(char).startswith("M"))
    return "".join(char for char in text if char.isalnum())

def is_duplicate_content(title, threshold=0.90, lookback=600):
    """يقارن العنوان بآخر العناوين التي رفعها هذا المشروع."""
    from difflib import SequenceMatcher
    fingerprint = content_fingerprint(title)
    if not fingerprint or is_generic_title(title):
        return False
    state = load_state()
    exact_only = len(fingerprint) < 10
    for previous in state.get("title_fingerprints", [])[-lookback:]:
        if fingerprint == previous:
            return True
        if exact_only:
            continue
        if SequenceMatcher(None, fingerprint, previous, autojunk=False).ratio() >= threshold:
            return True
    return False

def file_sha256(path, chunk_size=1024 * 1024):
    """بصمة محتوى دقيقة للملف، تقرأه على دفعات حتى لا تستهلك الذاكرة."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()

def is_known_media_hash(digest):
    if not digest:
        return False
    return digest in load_state().get("content_sha256", [])

def _youtube_duration_seconds(value):
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds

def fetch_channel_video_index(youtube, account_key="", force=False, log_cb=None):
    """يبني فهرساً مخزناً مؤقتاً لعناوين فيديوهات القناة ومددها عبر YouTube Data API."""
    cached = _read_json(CHANNEL_INDEX_FILE, {})
    if not isinstance(cached, dict):
        cached = {}
    cached_videos = cached.get("videos", [])
    cached_account = str(cached.get("account_key", ""))
    same_cached = bool(cached_videos) and cached_account == str(account_key)
    try:
        age = time.time() - float(cached.get("fetched_at", 0) or 0)
    except (TypeError, ValueError):
        age = CHANNEL_INDEX_TTL_SECONDS + 1
    if not force and same_cached and age < CHANNEL_INDEX_TTL_SECONDS:
        return cached_videos
    if (not force and same_cached
            and _channel_index_fail["account"] == str(account_key)
            and 0 <= time.time() - _channel_index_fail["at"] < CHANNEL_INDEX_RETRY_SECONDS):
        return [dict(video, _stale_index=True) for video in cached_videos]

    try:
        channel_response = youtube.channels().list(
            part="snippet,contentDetails", mine=True, maxResults=1
        ).execute()
        channels = channel_response.get("items", [])
        if not channels:
            raise RuntimeError("لم يعثر YouTube على قناة للحساب الحالي")
        channel = channels[0]
        channel_id = channel.get("id", "")
        uploads_id = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads_id:
            raise RuntimeError("تعذر العثور على قائمة فيديوهات القناة")

        entries = {}
        page_token = None
        while True:
            response = youtube.playlistItems().list(
                part="snippet,contentDetails", playlistId=uploads_id,
                maxResults=50, pageToken=page_token
            ).execute()
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                video_id = (item.get("contentDetails", {}).get("videoId")
                            or snippet.get("resourceId", {}).get("videoId"))
                title = snippet.get("title", "")
                if video_id and title and title.casefold() not in {"deleted video", "private video"}:
                    entries[video_id] = {
                        "video_id": video_id,
                        "title": title,
                        "published_at": item.get("contentDetails", {}).get("videoPublishedAt")
                                        or snippet.get("publishedAt", ""),
                        "duration": None,
                    }
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        video_ids = list(entries)
        for offset in range(0, len(video_ids), 50):
            batch = video_ids[offset:offset + 50]
            details = youtube.videos().list(
                part="contentDetails", id=",".join(batch), maxResults=50
            ).execute()
            for item in details.get("items", []):
                if item.get("id") in entries:
                    entries[item["id"]]["duration"] = _youtube_duration_seconds(
                        item.get("contentDetails", {}).get("duration", "")
                    )

        result = {
            "account_key": str(account_key),
            "channel_id": channel_id,
            "channel_title": channel.get("snippet", {}).get("title", ""),
            "fetched_at": time.time(),
            "videos": list(entries.values()),
        }
        _channel_index_fail.update({"account": "", "at": 0.0})
        _write_json(CHANNEL_INDEX_FILE, result)
        if log_cb:
            log_cb(f"فُحص سجل القناة: {len(entries)} فيديوً للمقارنة مع المحتوى الجديد.")
        return result["videos"]
    except Exception as error:
        if same_cached:
            _channel_index_fail.update({"account": str(account_key), "at": time.time()})
            if log_cb:
                log_cb("تعذر تحديث سجل القناة؛ استُخدمت النسخة المحلية المحفوظة.")
            logging.warning("تعذر تحديث سجل فيديوهات القناة: %s", error)
            return [dict(video, _stale_index=True) for video in cached_videos]
        raise

def find_channel_title_matches(title, duration, channel_videos, threshold=0.90, limit=3):
    """يعيد أقرب العناوين في القناة؛ التطابق عنواني ولا يثبت تطابق ملف الفيديو."""
    from difflib import SequenceMatcher
    fingerprint = content_fingerprint(title)
    if not fingerprint or is_generic_title(title):
        return []
    exact_only = len(fingerprint) < 10
    matches = []
    for video in channel_videos or []:
        previous = content_fingerprint(video.get("title", ""))
        if not previous:
            continue
        score = SequenceMatcher(None, fingerprint, previous, autojunk=False).ratio()
        if exact_only:
            if fingerprint != previous:
                continue
        elif score < threshold and fingerprint != previous:
            continue
        previous_duration = video.get("duration")
        delta = abs(float(duration) - float(previous_duration)) if duration is not None and previous_duration is not None else None
        matches.append({
            "video_id": video.get("video_id", ""),
            "title": video.get("title", ""),
            "published_at": video.get("published_at", ""),
            "duration": previous_duration,
            "similarity": round(score, 3),
            "duration_delta": round(delta, 1) if delta is not None else None,
        })
    return sorted(matches, key=lambda item: (item["similarity"], -(item["duration_delta"] or 0)), reverse=True)[:limit]

def should_pause_for_unverified_channel(dedupe_enabled, review_enabled, index_verified):
    return bool(dedupe_enabled and not review_enabled and not index_verified)

def remember_content(title, media_hash=None, frame_fingerprint=None, video_id=None, duration=None):
    """يحفظ بصمات المحتوى بعد نجاح الرفع."""
    state = load_state()
    fingerprint = "" if is_generic_title(title) else content_fingerprint(title)
    fingerprints = state.get("title_fingerprints", [])
    if len(fingerprint) >= 10:
        fingerprints.append(fingerprint)
        state["title_fingerprints"] = fingerprints[-2000:]
    hashes = state.get("content_sha256", [])
    if media_hash:
        hashes.append(media_hash)
        state["content_sha256"] = list(dict.fromkeys(hashes))[-2000:]
    frames = list(frame_fingerprint or [])
    if frames:
        saved = state.get("video_frame_fingerprints", [])
        if not isinstance(saved, list):
            saved = []
        saved.append({
            "video_id": str(video_id or ""),
            "title": str(title or ""),
            "duration": duration,
            "frames": frames,
        })
        state["video_frame_fingerprints"] = saved[-2000:]
    save_state(state)

DEFAULT_DAILY_UPLOAD_LIMIT = 100
LEGACY_DEFAULT_QUOTA_UNITS = 10000
LEGACY_UPLOAD_COST_UNITS = 1600


def _legacy_quota_to_upload_limit(value):
    try:
        units = max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_UPLOAD_LIMIT
    if units == LEGACY_DEFAULT_QUOTA_UNITS:
        return DEFAULT_DAILY_UPLOAD_LIMIT
    return max(1, units // LEGACY_UPLOAD_COST_UNITS)


def _daily_upload_limit_from_config(cfg):
    try:
        if cfg.has_option("SETTINGS", "DAILY_UPLOAD_LIMIT"):
            value = cfg.getint("SETTINGS", "DAILY_UPLOAD_LIMIT")
        else:
            legacy_units = cfg.getint("SETTINGS", "DAILY_QUOTA", fallback=LEGACY_DEFAULT_QUOTA_UNITS)
            return _legacy_quota_to_upload_limit(legacy_units)
    except (ValueError, configparser.Error):
        return DEFAULT_DAILY_UPLOAD_LIMIT
    return max(1, value)


# ════════════════════════════════════════════════════
# QUOTA MANAGER — حصة الرفع المشتركة لمشروع Google Cloud
# ════════════════════════════════════════════════════
class QuotaManager:
    """يتتبع طلبات videos.insert في عداد واحد مشترك بين حسابات OAuth للمشروع."""

    COST_UPLOAD = 1

    def __init__(self, daily_limit=DEFAULT_DAILY_UPLOAD_LIMIT, log_cb=None, account=""):
        try:
            self.daily_limit = max(1, int(daily_limit))
        except (TypeError, ValueError):
            self.daily_limit = DEFAULT_DAILY_UPLOAD_LIMIT
        self.log_cb = log_cb
        self.account = account
        self._k_used = "youtube_uploads_used"
        self._k_date = "youtube_uploads_date"
        state = load_state()
        today = pt_now().date().isoformat()
        if self._k_used in state and self._k_date in state:
            self.date = state.get(self._k_date, "")
            try:
                self.used = min(self.daily_limit, max(0, int(state.get(self._k_used, 0)))) if self.date == today else 0
            except (TypeError, ValueError):
                self.used = 0
        else:
            self.used = min(self.daily_limit, self._migrate_legacy_usage(state, today))
            if self.used and self.log_cb:
                self.log_cb(f"نُقل عداد الرفع السابق إلى الحصة المشتركة: {self.used} / {self.daily_limit}")
        self.date = today
        self._persist()

    @staticmethod
    def _migrate_legacy_usage(state, today):
        migrated = 0
        for used_key, value in state.items():
            if used_key == "quota_used":
                date_key = "quota_date"
            elif used_key.startswith("quota_used_"):
                date_key = "quota_date_" + used_key[len("quota_used_"):]
            else:
                continue
            if state.get(date_key) != today:
                continue
            try:
                old_units = max(0, int(value))
            except (TypeError, ValueError):
                continue
            if old_units >= LEGACY_DEFAULT_QUOTA_UNITS:
                migrated += old_units // LEGACY_UPLOAD_COST_UNITS
            else:
                migrated += (old_units + LEGACY_UPLOAD_COST_UNITS - 1) // LEGACY_UPLOAD_COST_UNITS
        return migrated

    def _persist(self):
        state = load_state()
        state[self._k_date] = self.date
        state[self._k_used] = self.used
        save_state(state)

    def _refresh(self):
        today = pt_now().date().isoformat()
        if self.date != today:
            self.date, self.used = today, 0
            self._persist()
            if self.log_cb:
                self.log_cb("بدأ يوم جديد بتوقيت المحيط الهادئ — صُفّر عداد الرفع للمشروع")

    def can_afford(self, count=1):
        self._refresh()
        try:
            count = max(1, int(count))
        except (TypeError, ValueError):
            count = 1
        return self.used + count <= self.daily_limit

    def register(self, count=1):
        self._refresh()
        try:
            count = max(1, int(count))
        except (TypeError, ValueError):
            count = 1
        self.used += count
        self._persist()

    def mark_exhausted(self):
        self.used = self.daily_limit
        self._persist()

    def seconds_until_reset(self):
        now = pt_now()
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        return max(60, int((nxt - now).total_seconds()))

# ════════════════════════════════════════════════════
# GEMINI AI — تحسين المحتوى بالذكاء الاصطناعي مع تدوير المفاتيح
# ════════════════════════════════════════════════════
AI_KEYS_FILE = _app_path("ai_keys.json")
# ════════════════════════════════════════════════════
# AI — Gemini (منطق العميل معزول في ai_client.py)
# ════════════════════════════════════════════════════
from ai_client import (AI_STYLES, GEMINI_MODELS, _extract_json, _iso_in_past,
                       ai_ping_key)

def load_ai_keys():
    """كل عنصر: {key, exhausted_until (ISO أو ''), last_error}."""
    return ai_client.load_ai_keys(AI_KEYS_FILE, _read_json)

def save_ai_keys(keys):
    ai_client.save_ai_keys(keys, AI_KEYS_FILE, _write_json,
                           harden=_harden_secret_file)

def ai_mark_exhausted(keys, index, reason=""):
    ai_client.ai_mark_exhausted(keys, index, reason, AI_KEYS_FILE, _write_json,
                                harden=_harden_secret_file)

def gemini_generate(prompt, model, log_cb=None, max_tokens=700):
    """يولّد نصاً عبر Gemini مع تدوير المفاتيح تلقائياً عند نفاد الحصة.

    يعيد (النص، المفاتيح المحدّثة) أو (None، المفاتيح) إذا استُنفدت كلها.
    """
    return ai_client.gemini_generate(
        prompt, model, AI_KEYS_FILE, _read_json, _write_json,
        log_cb=log_cb, max_tokens=max_tokens, harden=_harden_secret_file)

def ai_generate_metadata(raw_title, raw_desc, page_name, duration, model,
                         style_name, log_cb=None, performance_hint=""):
    """يسأل Gemini عن {title, description, tags} احترافية للفيديو. يعيد dict أو None."""
    raw_title = str(raw_title or "")
    raw_desc = str(raw_desc or "")
    page_name = str(page_name or "")
    style = AI_STYLES.get(style_name, AI_STYLES["احترافي متوازن"])
    dur_txt = f"{int(duration)} ثانية" if duration else "غير معروفة"
    title_hint = ("غير متوفر — استنتجه من الوصف واسم الصفحة"
                  if is_generic_title(raw_title) else raw_title[:200])
    prompt = f"""أنت خبير تسويق محتوى يوتيوب عربي. لدي فيديو قصير (Reel) من صفحة فيسبوك "{page_name or 'عامة'}".

العنوان الأصلي: {title_hint}
الوصف الأصلي: {(raw_desc or '')[:400]}
مدة الفيديو: {dur_txt}

{performance_hint}{style}

أعد كتابة بيانات الفيديو ليوتيوب بصيغة JSON فقط بهذا الشكل:
{{"title": "...", "description": "...", "tags": ["...", "..."]}}

القواعد:
- title: عربي فصيح مبسط، 40-80 حرفاً، بلا إيموجي زائد (واحد كحد أقصى)، بلا هاشتاجات.
- description: 2-4 جمل تشوّق للمشاهدة وتلخص المحتوى، بلا روابط.
- tags: من 8 إلى 12 وسمًا (عربي وإنجليزي) مرتبطة فعلاً بموضوع الفيديو.
- لا تخترع أسماء أو أرقاماً غير موجودة في المدخلات.
- إن وُجدت «عناوين القناة الأعلى مشاهدة» استفد منها في بناء العنوان الجديد."""
    text, _keys = gemini_generate(prompt, model, log_cb, max_tokens=700)
    data = _extract_json(text or "")
    if not data or not isinstance(data.get("title"), str):
        return None
    title = normalize_generated_title(data.get("title"))
    if not title:
        if log_cb:
            log_cb("أعاد الذكاء الاصطناعي عنواناً فارغاً أو غير مفيد؛ سيُستخدم عنوان المصدر بدلاً منه.")
        return None
    tags = data.get("tags")
    return {
        "title": title,
        "description": str(data.get("description") or "").strip(),
        "tags": [str(t).strip() for t in tags if str(t).strip()] if isinstance(tags, list) else [],
    }

# ════════════════════════════════════════════════════
# TELEGRAM — إشعارات اختيارية (المنطق معزول في notify.py)
# ════════════════════════════════════════════════════
from notify import send_telegram

def send_cycle_summary(ctx, settings, stats, log_cb=None):
    notify.send_cycle_summary(ctx, settings, stats, log_cb, send=send_telegram)

# ════════════════════════════════════════════════════
# HELPERS — CORE LOGIC
# ════════════════════════════════════════════════════
# (منطق الروابط معزول في fburls.py، وتصنيف أخطاء الرفع في uploaderrors.py)
from fburls import (FACEBOOK_HOSTS, _safe_normalize_channel_url,
                    normalize_channel_url, order_reels, reel_id_from_url_any)
from uploaderrors import (CHANNEL_LIMIT_REASONS, NON_RETRYABLE_STATUSES,
                          QUOTA_REASONS, RETRYABLE_REASONS, _http_reason,
                          _http_status, classify_upload_error)

def sanitize_account_name(name):
    """يمنع تسرب اسم الحساب إلى مسار ملف غير آمن."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).strip("._")
    return safe or "account1"



def _backoff_seconds(attempt):
    """تراجع أسّي مع رجّة عشوائية (8 → 24 → 72… بحد 300 ثانية)."""
    base = min(RETRY_DELAY * (3 ** (attempt - 1)), 300)
    return base + random.uniform(0, RETRY_DELAY / 2)


def verify_upload(youtube, video_id, requested_privacy, sched_time=None, log_cb=None):
    """يتحقق بعد الرفع أن الفيديو ظهر وبالخصوصية/الجدولة المطلوبة (best-effort).

    يكشف الحالة الشهيرة: الفيديو يُرفع لكن يبقى private لأن مشروع Google غير مُدقّق.
    """
    try:
        response = youtube.videos().list(part="status,processingDetails", id=video_id).execute()
    except HttpError as error:
        logging.warning("تعذر التحقق من حالة الفيديو %s: %s", video_id, error)
        if log_cb:
            log_cb("ℹتعذّر التحقق من حالة الفيديو بعد الرفع (قد تحتاج صلاحية youtube.readonly).")
        return None
    except Exception as error:
        logging.warning("تعذر التحقق من حالة الفيديو %s: %s", video_id, error)
        return None

    items = response.get("items") or []
    if not items:
        if log_cb:
            log_cb("لم يظهر الفيديو بعد الرفع؛ قد يكون قيد المعالجة.")
        return None
    status = items[0].get("status") or {}
    actual = status.get("privacyStatus")
    if sched_time:
        if status.get("publishAt"):
            if log_cb:
                log_cb(f"الجدولة مقبولة: سيُنشر في {status['publishAt']}")
        elif log_cb:
            log_cb("YouTube لم يقبل وقت الجدولة (publishAt مفقود)؛ سيظل الفيديو خاصاً.")
    elif requested_privacy == "public" and actual and actual != "public":
        if log_cb:
            log_cb(
                f"رُفع الفيديو لكن حالته «{actual}» وليست «public». السبب الأرجح أن مشروع "
                "Google غير مُدقّق (Unverified) يقفل الرفع خاصاً."
            )
    elif actual == requested_privacy and log_cb:
        log_cb(f"حالة الفيديو بعد الرفع: {actual}")
    return status


def finalize_video(path, log_cb=None):
    """يعيد تغليف المقطع إلى ملف جديد؛ الفشل لا يعيد مسار المصدر كأنه نجاح."""
    source_path = os.path.abspath(os.fspath(path or ""))
    if not os.path.isfile(source_path) or os.path.islink(source_path):
        raise PipelineFailure(
            "تنظيف الفيديو", "source_file_invalid",
            "ملف المصدر غير موجود أو ليس ملفاً عادياً.",
            "تحقق من الملف المحلي ثم أعد المحاولة.",
        )
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise PipelineFailure(
            "تنظيف الفيديو", "ffmpeg_missing",
            "FFmpeg غير مثبت، لذلك لم يُنظّف الفيديو.",
            "ثبّت FFmpeg وأضفه إلى PATH قبل إعادة المحاولة.",
        )
    out_path = os.path.splitext(source_path)[0] + ".clean.mp4"
    if os.path.abspath(out_path) == source_path:
        out_path = os.path.splitext(source_path)[0] + ".finalized.mp4"
    if os.path.lexists(out_path):
        raise PipelineFailure(
            "تنظيف الفيديو", "output_exists",
            "ملف الإخراج المنظف موجود مسبقاً ولن يُستبدل.",
            "اختر اسماً جديداً أو انقل ملف الإخراج السابق يدوياً.",
        )
    output_directory = os.path.dirname(source_path)
    try:
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(source_path)}.", suffix=".mp4", dir=output_directory
        )
        os.close(fd)
    except OSError as error:
        raise PipelineFailure(
            "تنظيف الفيديو", "temporary_file_error",
            redact_sensitive_text(error),
            "تحقق من صلاحيات المجلد ومساحة القرص ثم أعد المحاولة.",
        ) from error
    cmd = [ffmpeg_bin, "-y", "-v", "error", "-i", source_path,
           "-c", "copy", "-movflags", "+faststart", "-map_metadata", "-1",
           "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", temporary_path]
    try:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired as error:
            raise PipelineFailure(
                "تنظيف الفيديو", "ffmpeg_timeout",
                "انتهت مهلة FFmpeg قبل اكتمال التنظيف.",
                "تحقق من الملف والموارد المحلية ثم أعد المحاولة.",
                retryable=True,
            ) from error
        except OSError as error:
            raise PipelineFailure(
                "تنظيف الفيديو", "ffmpeg_unavailable",
                redact_sensitive_text(error),
                "تحقق من تثبيت FFmpeg ومسار PATH ثم أعد المحاولة.",
            ) from error
        if proc.returncode != 0:
            detail = redact_sensitive_text((proc.stderr or "").strip())[:300]
            raise PipelineFailure(
                "تنظيف الفيديو", "ffmpeg_failed",
                detail or "أعاد FFmpeg رمز فشل.",
                "أعد فحص الملف المصدر وتأكد من دعم FFmpeg للحاوية.",
            )
        if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) <= 0:
            raise PipelineFailure(
                "تنظيف الفيديو", "ffmpeg_output_invalid",
                "لم يُنشئ FFmpeg ملف إخراج صالحاً.",
                "أعد فحص الملف المصدر قبل المحاولة مجدداً.",
            )
        try:
            os.link(temporary_path, out_path)
            os.unlink(temporary_path)
        except FileExistsError as error:
            raise PipelineFailure(
                "تنظيف الفيديو", "output_exists",
                "ظهر ملف إخراج بالاسم نفسه أثناء التنظيف؛ لم يُستبدل.",
                "اختر اسماً جديداً ثم أعد المحاولة.",
            ) from error
        except OSError as error:
            raise PipelineFailure(
                "تنظيف الفيديو", "output_commit_failed",
                redact_sensitive_text(error),
                "تحقق من دعم الروابط الصلبة وصلاحيات المجلد ثم أعد المحاولة.",
            ) from error
        if os.name == "posix" and not restrict_file_permissions(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass
            raise PipelineFailure(
                "تنظيف الفيديو", "output_permissions_failed",
                "تعذر تقييد أذونات ملف الإخراج.",
                "تحقق من صلاحيات المجلد ثم أعد المحاولة.",
            )
        if log_cb:
            log_cb("نُظّف المقطع: faststart + إزالة البيانات الوصفية.")
        return out_path
    finally:
        try:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        except OSError as error:
            logging.warning("تعذر حذف ملف FFmpeg المؤقت: %s", redact_sensitive_text(error))


def _selenium_proxy_server(proxy_url):
    try:
        proxy = urlparse(str(proxy_url or "").strip())
        scheme = proxy.scheme.lower()
        host = proxy.hostname or ""
        port = proxy.port
    except ValueError as error:
        raise ValueError("صيغة وكيل المتصفح غير صالحة؛ استخدم عنواناً بلا بيانات دخول.") from error
    if scheme not in {"http", "https", "socks5", "socks5h"} or not host:
        raise ValueError("صيغة وكيل المتصفح غير صالحة؛ استخدم http أو https أو socks5.")
    if proxy.username or proxy.password:
        raise ValueError("وكيل المتصفح لا يقبل بيانات دخول داخل الرابط؛ استخدم وكيلاً محلياً موثوقاً بلا مصادقة.")
    if proxy.path not in {"", "/"} or proxy.query or proxy.fragment:
        raise ValueError("عنوان وكيل المتصفح يجب أن يقتصر على المخطط والمضيف والمنفذ.")
    if not port:
        port = 443 if scheme == "https" else 1080 if scheme.startswith("socks5") else 80
    display_host = f"[{host}]" if ":" in host else host
    display_scheme = "socks5" if scheme == "socks5h" else scheme
    return f"{display_scheme}://{display_host}:{port}"


def init_driver(headless=True, log_cb=None):
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=ar")
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
    proxy_url = config.get("WATCH", "PROXY", fallback="").strip()
    if proxy_url:
        try:
            options.add_argument(f"--proxy-server={_selenium_proxy_server(proxy_url)}")
        except ValueError as error:
            logging.warning("Browser proxy configuration was rejected: %s", error)
            if log_cb:
                log_cb(str(error))
            return None
    try:
        return webdriver.Chrome(options=options)
    except Exception as e:
        logging.error("Driver init error: %s", e)
        if log_cb:
            log_cb(f"فشل تشغيل المتصفح: {e}")
        return None

def _extract_article_hint(driver, el):
    """يصعد من رابط المقطع إلى حاوية المنشور (role=article) ويختار أطول سطر
    معنوى فيها — التسمية التوضيحية الحقيقية. يعيد "" إن لم يجد شيئاً."""
    try:
        article = el.find_element(By.XPATH, "ancestor::div[@role='article'][1]")
        if article is None:
            return ""
        return pick_caption_line(article.text or "")
    except Exception:
        return ""


def get_reels(driver, fb_page, scroll_times, video_limit, log_cb=None):
    """السحب عبر المتصفح، مع إنهاء التنقل المتعذر بدلاً من تعليق الدورة."""
    fb_page = safe_facebook_page_url(fb_page)
    if not fb_page:
        if log_cb:
            log_cb("رابط صفحة Facebook غير صالح؛ لم يُفتح في المتصفح.")
        return None
    page_timeout = _facebook_page_load_timeout()
    try:
        driver.set_page_load_timeout(page_timeout)
    except Exception:
        pass
    network_degraded = False
    try:
        driver.get(fb_page)
    except TimeoutException:
        network_degraded = True
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
        if log_cb:
            log_cb(f"انتهت مهلة تحميل صفحة فيسبوك بعد {page_timeout} ثانية؛ سأفحص المحتوى الذي وصل.")
    except WebDriverException as error:
        if is_transient_facebook_error(error):
            if log_cb:
                log_cb("تعذر الاتصال بفيسبوك؛ تخطّيت الصفحة لهذه الدورة. افحص الإنترنت أو الوكيل.")
            raise FacebookNetworkUnavailable(str(error)) from error
        raise
    try:
        current_url = getattr(driver, "current_url", "")
        page_source = getattr(driver, "page_source", "")
    except Exception:
        current_url, page_source = "", ""
    auth_issue = facebook_auth_block_reason(current_url, page_source)
    if auth_issue:
        messages = {
            "checkpoint": "فيسبوك طلب تحققاً أمنياً للحساب. أكمِل التحقق يدوياً ثم صدّر cookies.txt جديدة؛ لا يتجاوز التطبيق هذا الفحص.",
            "recovery": "فيسبوك يطلب استرداد الحساب. أكمِل الخطوة يدوياً ثم حدّث cookies.txt؛ لا يتجاوز التطبيق هذا الفحص.",
            "login": "فيسبوك أعاد التوجيه إلى تسجيل الدخول. فعّل cookies من الإعدادات، سجّل الدخول وصدّر cookies.txt جديدة.",
        }
        if log_cb:
            log_cb(messages.get(auth_issue, "جلسة Facebook غير صالحة؛ أعد تسجيل الدخول وصدّر cookies.txt جديدة."))
        raise FacebookLoginRequired(auth_issue)
    try:
        readiness_timeout = 2 if network_degraded else min(page_timeout, 15)
        WebDriverWait(driver, readiness_timeout).until(EC.presence_of_element_located((By.TAG_NAME, "a")))
    except Exception:
        pass
    time.sleep(1 if network_degraded else 4)

    links, seen_links, last_count, stall_count = [], set(), 0, 0
    scroll_limit = min(scroll_times, 1) if network_degraded else scroll_times
    scroll_delay = 1 if network_degraded else 3
    for i in range(scroll_limit):
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(scroll_delay)
            elements = driver.find_elements(By.TAG_NAME, "a")
        except WebDriverException as error:
            if is_transient_facebook_error(error):
                network_degraded = True
                if log_cb:
                    log_cb("انقطع المتصفح أثناء سحب الروابط؛ أوقفت هذه الصفحة دون إيقاف بقية الدورة.")
                break
            raise
        for el in elements:
            try:
                href = el.get_attribute("href")
                canonical_url = canonicalize_facebook_video_url(href or "")
                if canonical_url and canonical_url not in seen_links:
                    seen_links.add(canonical_url)
                    hint = ""
                    for attr in (el.get_attribute("aria-label"), el.text):
                        candidate = sanitize_public_text(str(attr or ""), keep_newlines=False)
                        if candidate and not is_generic_title(candidate):
                            hint = candidate.strip()
                            break
                    if not hint:
                        hint = _extract_article_hint(driver, el)
                    links.append({"url": canonical_url, "title_hint": hint})
            except Exception:
                continue
        if log_cb:
            log_cb(f"تمرير {i+1}/{scroll_times} — {len(links)} رابط")
        if len(links) == last_count:
            stall_count += 1
            if stall_count >= 3:
                break
        else:
            stall_count = 0
        last_count = len(links)
        if len(links) >= video_limit:
            break
    if network_degraded and not links:
        raise FacebookNetworkUnavailable("Facebook page load timed out before any reel link was captured.")
    return links[:video_limit]


def _facebook_page_load_timeout():
    try:
        return max(5, min(config.getint("WATCH", "PAGE_LOAD_TIMEOUT", fallback=25), 30))
    except (ValueError, configparser.Error):
        return 25

def _facebook_network_preflight_timeout():
    try:
        timeout = config.getint(
            "WATCH", "NETWORK_PREFLIGHT_TIMEOUT", fallback=DEFAULT_NETWORK_PREFLIGHT_TIMEOUT
        )
    except (ValueError, configparser.Error):
        timeout = DEFAULT_NETWORK_PREFLIGHT_TIMEOUT
    return max(10, min(timeout, 15))


def _facebook_network_preflight(log_cb=None, proxy_override=None):
    global _FACEBOOK_NETWORK_ROUTE
    try:
        proxy = (
            str(proxy_override or "").strip()
            if proxy_override is not None
            else config.get("WATCH", "PROXY", fallback="").strip()
        )
        force_ipv4 = config.getboolean("WATCH", "FORCE_IPV4", fallback=True)
    except (ValueError, configparser.Error):
        proxy, force_ipv4 = "", True
    timeout = _facebook_network_preflight_timeout()
    _FACEBOOK_NETWORK_ROUTE = facebook_network_route(proxy, force_ipv4, timeout=timeout)
    if _FACEBOOK_NETWORK_ROUTE == "ipv6" and force_ipv4 and not proxy and log_cb:
        log_cb("اتصال IPv4 غير متاح؛ تم التحول تلقائياً إلى IPv6، وستستمر دورة السحب.")
    if _FACEBOOK_NETWORK_ROUTE is None:
        logging.warning("Facebook network preflight failed; skipping source scraping this cycle.")
        if log_cb:
            route = "الوكيل" if proxy else "IPv4 وIPv6"
            log_cb(
                f"تعذر الوصول إلى Facebook عبر {route}؛ مُنحت كل عائلة شبكة مهلة {timeout} ثوانٍ. "
                "لم يبدأ السحب أو الرفع؛ تبقى العناصر في قائمة الانتظار."
            )
    return _FACEBOOK_NETWORK_ROUTE is not None


def _network_tweaks(cookies_file=None, socket_timeout=30, retries=3,
                    timeout_key="SOCKET_TIMEOUT", retries_key="RETRIES",
                    legacy_timeout_key=None, legacy_retries_key=None):
    def configured_int(key, default, minimum, maximum, legacy_key=None):
        try:
            if legacy_key and not config.has_option("WATCH", key) and config.has_option("WATCH", legacy_key):
                value = config.getint("WATCH", legacy_key)
            else:
                value = config.getint("WATCH", key, fallback=default)
        except (ValueError, configparser.Error):
            value = default
        return max(minimum, min(value, maximum))

    timeout_ceiling = 20 if timeout_key == "SCRAPE_SOCKET_TIMEOUT" else 30
    opts = {
        "socket_timeout": configured_int(timeout_key, socket_timeout, 5, timeout_ceiling, legacy_timeout_key),
        "retries": configured_int(retries_key, retries, 1, 3, legacy_retries_key),
    }
    proxy = config.get("WATCH", "PROXY", fallback="").strip()
    if proxy:
        opts["proxy"] = proxy
    if config.getboolean("WATCH", "FORCE_IPV4", fallback=False) and _FACEBOOK_NETWORK_ROUTE != "ipv6":
        opts["source_address"] = "0.0.0.0"
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file
    return opts


class _YtDlpQuietLogger:
    def __init__(self):
        self.last_error = ""

    def debug(self, message):
        return None

    def warning(self, message):
        return None

    def error(self, message):
        self.last_error = str(message)


def scrape_links_fast(page_url, limit, log_cb=None, cookies_file=None):
    """سحب سريع عبر yt-dlp من دون متصفح. يعيد None عند الفشل ليُستخدم المتصفح."""
    page_url = safe_facebook_page_url(page_url)
    if not page_url:
        if log_cb:
            log_cb("رابط صفحة Facebook غير صالح؛ لم يُمرر إلى yt-dlp.")
        return None
    # yt-dlp لا يدعم صفحات القوائم (/reels، /videos) — يدعم روابط المقطعات فقط.
    # التحويل المبكر للمتصفح يوفّر 45 ثانية مهلة لكل صفحة بدل فشل مضمون.
    lowered = page_url.lower()
    if re.search(r"facebook\.com/[^/?]+/(reels?|videos)/?(\?|$)", lowered) and "/reel/" not in lowered and "/watch" not in lowered:
        if log_cb:
            log_cb(f"السحب السريع لا يدعم صفحات القوائم — متصفح مباشرة: {page_url}")
        return None
    logger = _YtDlpQuietLogger()
    opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "logger": logger,
        **_network_tweaks(
            cookies_file,
            socket_timeout=15,
            retries=2,
            timeout_key="SCRAPE_SOCKET_TIMEOUT",
            retries_key="SCRAPE_RETRIES",
        ),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(page_url, download=False)
    except Exception as error:
        message = str(error).strip() or logger.last_error
        transient = is_transient_facebook_error(error) or is_transient_facebook_error(
            RuntimeError(logger.last_error)
        )
        if transient:
            if log_cb:
                log_cb("انقطع اتصال Facebook أثناء السحب السريع؛ تخطّيت فتح المتصفح لهذه الصفحة وسأعيد الفحص في الدورة التالية.")
            raise FacebookNetworkUnavailable("Transient Facebook network error during fast scraping.") from error
        if "unsupported url" in message.lower():
            detail = "yt-dlp لا يدعم هذا الرابط؛ سأجرب المتصفح بدلاً منه."
        else:
            detail = f"تعذر السحب السريع ({message[:180]})؛ سأجرب المتصفح."
        if log_cb:
            log_cb(detail)
        return None
    if not isinstance(info, dict):
        message = logger.last_error
        if message and is_transient_facebook_error(RuntimeError(message)):
            if log_cb:
                log_cb("انقطع اتصال Facebook أثناء السحب السريع؛ تخطّيت فتح المتصفح لهذه الصفحة وسأعيد الفحص في الدورة التالية.")
            raise FacebookNetworkUnavailable("Transient Facebook network error during fast scraping.")
        if log_cb:
            if message and "unsupported url" in message.lower():
                log_cb("yt-dlp لا يدعم هذا الرابط؛ سأجرب المتصفح بدلاً منه.")
            else:
                log_cb("لم يستخرج yt-dlp قائمة روابط؛ سأجرب المتصفح.")
        return None
    if logger.last_error and is_transient_facebook_error(RuntimeError(logger.last_error)):
        if log_cb:
            log_cb("انقطع اتصال Facebook أثناء السحب السريع؛ تخطّيت فتح المتصفح لهذه الصفحة وسأعيد الفحص في الدورة التالية.")
        raise FacebookNetworkUnavailable("Transient Facebook network error during fast scraping.")
    links = []
    entries = info.get("entries")
    if entries is None:
        if log_cb:
            log_cb("لم يُرجع مستخرج Facebook قائمة فيديوهات؛ سأجرب المتصفح.")
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source_url = entry.get("webpage_url") or entry.get("url") or ""
        canonical_url = canonicalize_facebook_video_url(source_url)
        if not canonical_url and str(entry.get("id") or "").isdigit():
            canonical_url = canonicalize_facebook_video_url(
                f"https://www.facebook.com/reel/{entry['id']}"
            )
        if canonical_url:
            hint = str(entry.get("title") or entry.get("description") or "").strip()
            links.append({"url": canonical_url, "title_hint": hint[:300]})
    if not links:
        if log_cb:
            log_cb("لم يُرجع السحب السريع روابط؛ سأتحقق عبر المتصفح قبل اعتبار الصفحة فارغة.")
        return None
    ordered = order_reels([item["url"] for item in links], newest_first=False)[:limit]
    by_url = {item["url"]: item for item in links}
    return [by_url[url] for url in ordered if url in by_url]

def get_youtube(account_name, secrets_file=None, allow_interactive=True):
    sf = _app_path(secrets_file or CLIENT_SECRETS_FILE)
    token_file = _app_path(f"{sanitize_account_name(account_name)}_token.json")
    if os.path.islink(sf) or os.path.islink(token_file):
        return None, "مسار OAuth أو ملف token رابط رمزي؛ اختر ملفاً محلياً عادياً في الإعدادات المتقدمة."
    if os.path.isfile(sf):
        _harden_secret_file(sf)
    creds = None
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as token_source:
                token_data = json.load(token_source)
            stored_scopes = set(token_data.get("scopes") or []) if isinstance(token_data, dict) else set()
            if set(SCOPES).issubset(stored_scopes):
                creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            else:
                logging.info("أذونات YouTube تغيّرت؛ سيُطلب تسجيل موافقة جديد للقراءة والرفع.")
        except Exception as error:
            logging.warning("ملف تفويض YouTube غير صالح أو غير قابل للقراءة: %s", redact_sensitive_text(error))
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as error:
                logging.warning("تعذر تحديث تفويض YouTube: %s", redact_sensitive_text(error))
                creds = None
        elif creds and creds.expired:
            creds = None
        if creds is not None and not creds.valid:
            creds = None
        if not creds:
            if not os.path.isfile(sf):
                return None, f"ملف OAuth المحلي {os.path.basename(sf)} مفقود؛ اختره في الإعدادات المتقدمة."
            if not allow_interactive:
                return None, "لا يوجد تفويض YouTube صالح للتشغيل الآلي. افتح الواجهة وسجّل الدخول أولاً."
            try:
                flow = InstalledAppFlow.from_client_secrets_file(sf, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as error:
                detail = redact_sensitive_text(error)[:240]
                return None, f"فشل تفويض YouTube. تحقق من ملف OAuth واتصال المتصفح ثم أعد المحاولة. {detail}"
        if not creds or not creds.valid:
            return None, "لم يُرجع OAuth تفويضاً صالحاً؛ لم يبدأ أي طلب رفع. أعد الموافقة ثم تحقق من الحساب."
        try:
            token_data = json.loads(creds.to_json())
            if not isinstance(token_data, dict) or not token_data.get("token"):
                raise ValueError("OAuth returned incomplete token data")
            _write_json(token_file, token_data)
        except Exception as error:
            logging.error("تعذر حفظ تفويض YouTube محلياً بأمان: %s", redact_sensitive_text(error))
            return None, "تمت الموافقة لكن تعذر حفظ token محلياً بأمان؛ تحقق من صلاحيات مجلد التطبيق."
    try:
        _harden_secret_file(token_file)
        service = build("youtube", "v3", credentials=creds)
    except Exception as error:
        detail = redact_sensitive_text(error)[:240]
        return None, f"تعذر إنشاء اتصال YouTube بعد المصادقة. تحقق من الشبكة والصلاحيات. {detail}"
    return service, None

def _resolve_downloaded_file(info, ydl):
    """يعثر على ملف الفيديو الفعلي بعد الدمج، لا على الاسم المتوقع فقط."""
    candidates = []
    for item in info.get("requested_downloads") or []:
        if isinstance(item, dict):
            candidates.extend([item.get("filepath"), item.get("_filename")])
    candidates.extend([info.get("filepath"), info.get("_filename")])
    try:
        candidates.append(ydl.prepare_filename(info))
    except Exception:
        pass
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
        if candidate:
            merged = os.path.splitext(candidate)[0] + ".mp4"
            if os.path.isfile(merged):
                return merged
    return None

class StopRequested(Exception):
    """يُرفع من داخل خيط العمل عندما يطلب المستخدم الإيقاف."""


def _safe_download_slug(source_id):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(source_id or "video"))[:80] or "video"


def _cleanup_download_attempt(attempt_dir, output_dir):
    if not attempt_dir or os.path.islink(attempt_dir):
        return
    root = os.path.realpath(output_dir)
    candidate = os.path.abspath(attempt_dir)
    if os.path.realpath(os.path.dirname(candidate)) != root:
        return
    if not os.path.basename(candidate).startswith(".fb2yt-"):
        return
    try:
        shutil.rmtree(candidate)
    except OSError as error:
        logging.warning("تعذر تنظيف مجلد محاولة التنزيل المؤقت: %s", redact_sensitive_text(error))


def _promote_downloaded_file(file_path, attempt_dir, output_dir, source_id):
    if not file_path or os.path.islink(file_path) or not os.path.isfile(file_path):
        raise FileNotFoundError("لم يُعثر على ملف تنزيل عادي صالح.")
    root = os.path.realpath(attempt_dir)
    source = os.path.realpath(file_path)
    if os.path.commonpath((root, source)) != root:
        raise OSError("رفض نقل ملف تنزيل خارج مجلد المحاولة المؤقت.")
    extension = os.path.splitext(source)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
        raise OSError("تعذر تحديد امتداد ملف الفيديو الناتج.")
    attempt_name = os.path.basename(attempt_dir).removeprefix(".fb2yt-")
    base = f"fb2yt-{_safe_download_slug(source_id)}-{attempt_name}"
    destination = os.path.join(output_dir, base + extension)
    while os.path.lexists(destination):
        destination = os.path.join(output_dir, f"{base}-{os.urandom(6).hex()}{extension}")
    try:
        os.link(source, destination)
        os.unlink(source)
    except OSError:
        if os.path.lexists(destination):
            raise
        os.rename(source, destination)
    if os.name == "posix" and not restrict_file_permissions(destination):
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise OSError("تعذر تقييد أذونات ملف الفيديو المحلي.")
    return destination


def _cleanup_promoted_download(path, output_dir):
    if not path or os.path.islink(path):
        return
    candidate = os.path.abspath(path)
    root = os.path.realpath(output_dir)
    if not os.path.basename(candidate).startswith("fb2yt-"):
        return
    try:
        if os.path.commonpath((root, os.path.realpath(candidate))) != root:
            return
        os.unlink(candidate)
    except (OSError, ValueError) as error:
        logging.warning("تعذر تنظيف تنزيل مرحلي غير مكتمل: %s", redact_sensitive_text(error))


def _facebook_watch_og_title(url, timeout=20):
    """يجلب og:title من صفحة مشاهدة فيسبوك دون تسجيل.
    صفحات Watch/Reel تقدّم العنوان الحقيقي لزوّار externalhit حتى عندما يعيد
    yt-dlp عنواناً عاماً؛ هذه أضمن مصدر للعنوان الأصلي. يعيد نصاً أو فراغاً."""
    target = (url or "").strip()
    if not target:
        return ""
    try:
        request = urllib.request.Request(
            target,
            headers={"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read(524288).decode("utf-8", "replace")
    except (OSError, ValueError, urllib.error.URLError) as error:
        logging.info("تعذر جلب og:title من صفحة Facebook: %s", redact_sensitive_text(error))
        return ""
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']',
    ):
        match = re.search(pattern, page, re.IGNORECASE)
        if match:
            return html_module.unescape(match.group(1)).strip()
    return ""


def _refetch_facebook_title(download_url, source_names, remove_tags, omit_source_page_name,
                            ydl_opts, log_cb=None):
    """عندما يعيد فيسبوك عنواناً عاماً («Video» أو «Facebook Live») نعيد جلب البيانات فقط
    مرة واحدة بحد مهلة أطول؛ وإن بقي العنوان عاماً بنينا عنواناً وصفياً من اسم الصفحة
    والوقت بدل رفع «Video» حرفياً إلى يوتيوب. يعيد (العنوان، هل هو عنوان بديل)."""
    og_candidate = _facebook_watch_og_title(download_url)
    if og_candidate:
        candidate = prepare_public_title(
            og_candidate, source_names,
            remove_hashtags=remove_tags,
            omit_source_page_name=omit_source_page_name,
        )
        if not is_generic_title(candidate):
            if log_cb:
                log_cb(f"جُلب العنوان الأصلي من بيانات صفحة Facebook: {candidate[:50]}")
            return candidate, False
    try:
        meta_options = dict(ydl_opts)
        meta_options["skip_download"] = True
        meta_options.pop("progress_hooks", None)
        try:
            meta_options["socket_timeout"] = max(int(meta_options.get("socket_timeout") or 0), 60)
        except (TypeError, ValueError):
            meta_options["socket_timeout"] = 60
        with yt_dlp.YoutubeDL(meta_options) as ydl:
            meta = ydl.extract_info(download_url, download=False)
        candidate = prepare_public_title(
            (meta or {}).get("title", ""), source_names,
            remove_hashtags=remove_tags,
            omit_source_page_name=omit_source_page_name,
        )
        if not is_generic_title(candidate):
            if log_cb:
                log_cb(f"عُدّل العنوان العام بعد إعادة جلب بيانات الصفحة: {candidate[:40]}")
            return candidate, False
    except Exception as error:
        logging.warning("تعذر إعادة جلب بيانات العنوان: %s", error)
    page_label = next((str(name).strip() for name in source_names if str(name or "").strip()), "")
    stamp = pt_now().strftime("%Y-%m-%d %H:%M")
    fallback = f"مقطع {page_label} — {stamp}" if page_label else f"مقطع — {stamp}"
    if log_cb:
        log_cb("لم يوفر فيسبوك عنواناً حقيقياً؛ استُخدم عنوان وصفي باسم الصفحة والوقت. فعّل الذكاء الاصطناعي لتحسينه.")
    return fallback, True


def download_video(url, page_name="", log_cb=None, retries=MAX_RETRIES, remove_tags=False,
                   cookies_file=None, stop_event=None, omit_source_page_name=True, output_dir=None,
                   title_hint="", page_title_fetcher=None):
    """ينزّل المقطع إلى مجلد محاولة خاص، ثم ينقله إلى اسم فريد بعد اكتمال التنزيل."""
    original_url = (url or "").strip()
    download_url = canonicalize_facebook_video_url(original_url)
    if not download_url:
        raise PipelineFailure(
            "رابط Facebook", "unsupported_facebook_url",
            "الرابط ليس رابط فيديو Facebook صالحاً أو مدعوماً.",
            "تحقق من رابط Reel أو Watch أو Share، ثم أعد إضافته.",
        )
    output_dir = os.path.abspath(os.fspath(output_dir or VIDEO_DIRECTORY))
    try:
        os.makedirs(output_dir, exist_ok=True)
        if not os.path.isdir(output_dir):
            raise OSError("output path is not a directory")
    except OSError as error:
        raise PipelineFailure(
            "تهيئة مجلد الفيديو", "output_directory_unavailable",
            redact_sensitive_text(error),
            "اختر مجلداً محلياً قابلاً للكتابة في الإعدادات المتقدمة.",
        ) from error
    logger = _YtDlpQuietLogger()
    ffmpeg_available = bool(shutil.which("ffmpeg"))
    format_selector = (
        "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        if ffmpeg_available else "best[ext=mp4]/best"
    )
    if not ffmpeg_available and log_cb:
        log_cb("FFmpeg غير متاح؛ أستخدم صيغة فيديو مدمجة مسبقاً لتفادي فشل دمج الصوت والصورة.")
    ydl_opts = {
        "format": format_selector,
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": logger,
        **_network_tweaks(
            cookies_file,
            socket_timeout=30,
            retries=3,
            timeout_key="DOWNLOAD_SOCKET_TIMEOUT",
            retries_key="DOWNLOAD_RETRIES",
            legacy_timeout_key="SOCKET_TIMEOUT",
            legacy_retries_key="RETRIES",
        ),
    }
    if ffmpeg_available:
        ydl_opts["merge_output_format"] = "mp4"

    def stop_hook(status):
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        if not log_cb or str(status.get("status")) != "downloading":
            return
        now = time.monotonic()
        if now - stop_hook._last_beat < 30:
            return
        stop_hook._last_beat = now
        try:
            done = float(status.get("downloaded_bytes") or 0)
            total = float(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
        except (TypeError, ValueError):
            done, total = 0.0, 0.0
        if total > 0 and done > 0:
            log_cb(f"جاري التنزيل: {min(99, int(done / total * 100))}% ({done / (1024 ** 2):.0f} MB من {total / (1024 ** 2):.0f} MB)")
        elif done > 0:
            log_cb(f"جاري التنزيل: {done / (1024 ** 2):.0f} MB حتى الآن…")

    stop_hook._last_beat = 0.0

    ydl_opts["progress_hooks"] = [stop_hook]

    try:
        attempts = max(1, min(int(retries), 10))
    except (TypeError, ValueError):
        raise PipelineFailure(
            "إعداد التنزيل", "invalid_retry_count",
            "عدد المحاولات غير صالح.",
            "أدخل عدداً صحيحاً بين 1 و10 في الإعدادات.",
        ) from None
    source_id = facebook_media_id(download_url) or hashlib.sha256(download_url.encode("utf-8")).hexdigest()[:16]
    for attempt in range(1, attempts + 1):
        attempt_dir = None
        file_path = None
        logger.last_error = ""
        promoted_path = None
        try:
            attempt_dir = tempfile.mkdtemp(
                prefix=f".fb2yt-{_safe_download_slug(source_id)}-", dir=output_dir
            )
            attempt_options = dict(ydl_opts)
            attempt_options["outtmpl"] = os.path.join(attempt_dir, "%(id)s.%(ext)s")
            with yt_dlp.YoutubeDL(attempt_options) as ydl:
                info = ydl.extract_info(download_url, download=True)
                if not isinstance(info, dict):
                    raise ValueError("لم يُرجع المستخرج بيانات فيديو قابلة للتحقق.")
                file_path = _resolve_downloaded_file(info, ydl)
                if not file_path:
                    raise FileNotFoundError("اكتمل الاستخراج لكن لم يُعثر على ملف الفيديو الناتج.")
                file_path = _promote_downloaded_file(file_path, attempt_dir, output_dir, source_id)
                promoted_path = file_path
            _cleanup_download_attempt(attempt_dir, output_dir)
            size_bytes = os.path.getsize(file_path)
            if size_bytes <= 0:
                raise ValueError("ملف التنزيل الناتج فارغ.")
            source_names = [page_name]
            for extra_name in (info.get("uploader"), info.get("channel"), info.get("uploader_id")):
                if extra_name:
                    source_names.append(extra_name)
            title = prepare_public_title(
                info.get("title", ""),
                source_names,
                remove_hashtags=remove_tags,
                omit_source_page_name=omit_source_page_name,
            )
            title_generic = is_generic_title(title)
            hint_title = ""
            if title_generic:
                hint_title = prepare_public_title(
                    title_hint, source_names,
                    remove_hashtags=remove_tags,
                    omit_source_page_name=omit_source_page_name,
                )
                if not is_generic_title(hint_title):
                    title, title_generic = hint_title, False
                    if log_cb:
                        log_cb(f"استُخدم العنوان الملتقط من صفحة Facebook: {title[:40]}")
            description = sanitize_public_text(info.get("description") or "", keep_newlines=True)
            if title_generic and description:
                caption_title = prepare_public_title(
                    pick_caption_line(description), source_names,
                    remove_hashtags=remove_tags,
                    omit_source_page_name=omit_source_page_name,
                )
                if not is_generic_title(caption_title):
                    title, title_generic = caption_title, False
                    if log_cb:
                        log_cb(f"استُخدِم عنوان من التسمية التوضيحية للمنشور: {title[:40]}")
            if title_generic:
                title, title_generic = _refetch_facebook_title(
                    download_url, source_names, remove_tags, omit_source_page_name,
                    ydl_opts, log_cb)
                if title_generic and hint_title and not is_generic_title(hint_title):
                    title, title_generic = hint_title, False
            if title_generic and page_title_fetcher:
                rescue_raw = str(page_title_fetcher(download_url) or "")
                rescue_title = prepare_public_title(
                    rescue_raw, source_names,
                    remove_hashtags=remove_tags,
                    omit_source_page_name=omit_source_page_name,
                )
                if not is_generic_title(rescue_title):
                    title, title_generic = rescue_title, False
                    if log_cb:
                        log_cb(f"استُخدِم عنوان صفحة Facebook عبر الجلسة المسجلة: {title[:40]}")
            source_label = next((str(name).strip() for name in source_names
                                 if str(name or "").strip()), "")
            description = ensure_source_attribution(description, source_label, download_url, log_cb)
            try:
                duration = float(info.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            size_mb = round(size_bytes / (1024 ** 2), 1)
            if log_cb:
                log_cb(f"اكتمل تنزيل الملف والتحقق من وجوده: {title[:40]}... ({size_mb} MB)")
            logging.info("اكتمل تنزيل ملف فيديو مرحلي إلى المجلد المحلي.")
            promoted_path = None
            return {
                "file": file_path,
                "title": title,
                "title_generic": bool(title_generic),
                "desc": description,
                "source_names": source_names,
                "source_page": source_label,
                "source_url": download_url,
                "size_mb": size_mb,
                "duration": duration,
            }
        except StopRequested:
            _cleanup_promoted_download(promoted_path, output_dir)
            _cleanup_download_attempt(attempt_dir, output_dir)
            raise
        except PipelineFailure:
            _cleanup_promoted_download(promoted_path, output_dir)
            _cleanup_download_attempt(attempt_dir, output_dir)
            raise
        except Exception as error:
            error_text = " ".join(part for part in (str(error), logger.last_error) if part).strip()
            _cleanup_promoted_download(promoted_path, output_dir)
            _cleanup_download_attempt(attempt_dir, output_dir)
            normalized_error = error_text.lower()
            safe_cause = redact_sensitive_text(error_text or type(error).__name__)[:300]
            if "unsupported url" in normalized_error:
                raise PipelineFailure(
                    "استخراج Facebook", "unsupported_facebook_url",
                    "yt-dlp لا يدعم صيغة هذا الرابط.",
                    "استخدم رابط Reel أو Watch أو Share مباشر، ولا تتجاوز قيود المنصة.",
                ) from error
            if any(marker in normalized_error for marker in (
                "checkpoint", "captcha", "verify you are", "security check",
                "login required", "not logged in", "sign in to confirm",
            )):
                raise PipelineFailure(
                    "جلسة Facebook", "facebook_auth_required",
                    "طلب Facebook تسجيل الدخول أو تحققاً أمنياً.",
                    "أكمل التحقق يدوياً في Facebook وحدّث ملف الجلسة بنفسك؛ لن يتجاوز التطبيق الحماية.",
                ) from error
            transient = is_transient_facebook_error(error) or is_transient_facebook_error(
                RuntimeError(logger.last_error)
            )
            if transient and attempt < attempts:
                if log_cb:
                    log_cb(f"انقطع الاتصال أثناء الاستخراج؛ المحاولة {attempt}/{attempts} بعد {RETRY_DELAY} ثوانٍ.")
                if stop_event is not None:
                    if stop_event.wait(RETRY_DELAY):
                        raise StopRequested()
                else:
                    time.sleep(RETRY_DELAY)
                continue
            if transient:
                failure = PipelineFailure(
                    "استخراج Facebook", "facebook_network_unavailable",
                    safe_cause or "تعذر الوصول إلى Facebook بعد المحاولات المحددة.",
                    "تحقق من الإنترنت وDNS والوكيل، ثم أعد المحاولة؛ بقي العنصر في قائمة الانتظار.",
                    retryable=True,
                )
            else:
                failure = PipelineFailure(
                    "استخراج Facebook", "download_failed",
                    safe_cause or "فشل استخراج الملف أو التحقق منه.",
                    "تحقق من صلاحية الرابط والجلسة وتوفر FFmpeg وffprobe، ثم أعد المحاولة.",
                )
            raise failure from error
    raise PipelineFailure(
        "استخراج Facebook", "download_failed",
        "انتهت المحاولات دون الحصول على ملف موثوق.",
        "افحص السجل المحلي وأصلح سبب التنزيل قبل تحرير العنصر.",
    )

# ════════════════════════════════════════════════════
# VIDEO PROCESSOR — تركيب الشعار بـ ffmpeg
# ════════════════════════════════════════════════════
def ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def probe_video(path):
    """يعيد (العرض، الارتفاع، المدة بالثواني) عبر ffprobe."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        ).stdout
        data = json.loads(out)
        stream = (data.get("streams") or [{}])[0]
        duration = float(data.get("format", {}).get("duration") or 0)
        return int(stream.get("width") or 0), int(stream.get("height") or 0), duration
    except Exception:
        return 0, 0, 0.0

LOGO_POSITIONS = {
    "أعلى اليمين": ("main_w-overlay_w-{m}", "{m}"),
    "أعلى اليسار": ("{m}", "{m}"),
    "أسفل اليمين": ("main_w-overlay_w-{m}", "main_h-overlay_h-{m}"),
    "أسفل اليسار": ("{m}", "main_h-overlay_h-{m}"),
}


def build_overlay_filter(position_name, margin, secondary_enabled=False,
                         secondary_position="أسفل اليسار"):
    x_template, y_template = LOGO_POSITIONS.get(
        position_name, LOGO_POSITIONS["أعلى اليمين"])
    x = x_template.format(m=int(margin))
    y = y_template.format(m=int(margin))
    if not secondary_enabled:
        return ("[1:v]format=rgba[badge];"
                f"[0:v][badge]overlay=x='{x}':y='{y}':format=auto[vout]")
    sx_template, sy_template = LOGO_POSITIONS.get(
        secondary_position, LOGO_POSITIONS["أسفل اليسار"])
    sx = sx_template.format(m=int(margin))
    sy = sy_template.format(m=int(margin))
    return ("[1:v]format=rgba[primary_badge];"
            f"[0:v][primary_badge]overlay=x='{x}':y='{y}':format=auto[primary];"
            "[2:v]format=rgba[secondary_badge];"
            f"[primary][secondary_badge]overlay=x='{sx}':y='{sy}':format=auto[vout]")


def create_logo_badge(logo_path, video_width, video_height, scale_pct=8,
                      opacity=100, cover_old=True, cover_width_pct=20,
                      cover_height_pct=19, log_cb=None, plate=True):
    """يبني شارة الشعار.

    plate=True: لوحة مستديرة داكنة خلف الشعار (تغطية كاملة للخلفية).
    plate=False مع cover_old=True: وضع «الشعار فوق الشعار» — شعارك يُكبَّر
    ليغطي منطقة شعار القناة الأصلي مباشرة، بلا لوحة، مع ظل خفيف فقط.
    """
    with Image.open(logo_path) as source:
        has_alpha = "A" in source.getbands() or "transparency" in source.info
        original_size = source.size
        logo = source.convert("RGBA")
    if log_cb and (original_size[0] < 256 or original_size[1] < 128):
        log_cb("دقة الشعار منخفضة؛ استخدم ملفاً أكبر من 512×256 بكسل للحصول على حواف أوضح.")
    if log_cb and not has_alpha:
        log_cb("ملف الشعار بلا شفافية؛ قد يظهر مستطيل خلفه. الأفضل PNG شفاف.")
    alpha_box = logo.getchannel("A").getbbox()
    if not alpha_box:
        raise ValueError("ملف الشعار شفاف بالكامل أو فارغ")
    logo = logo.crop(alpha_box)
    if logo.width < 1 or logo.height < 1:
        raise ValueError("ملف الشعار فارغ")

    padding = max(8, int(min(video_width, video_height) * 0.02))
    factor = 3

    if cover_old and not plate:
        cover_width = max(8, min(int(video_width * min(max(cover_width_pct, 8), 45) / 100),
                                 int(video_width * 0.45)))
        cover_height = max(8, min(int(video_height * min(max(cover_height_pct, 5), 35) / 100),
                                  int(video_height * 0.35)))
        # التوسيع بأسلوب «تغطية»: أكبر بعد يضمن إخفاء الشعار القديم خلف شعارك.
        scale = max(cover_width / logo.width, cover_height / logo.height)
        scale = max(0.05, min(scale, 8.0))
        logo = logo.resize(
            (max(1, round(logo.width * scale)), max(1, round(logo.height * scale))),
            Image.Resampling.LANCZOS)
        logo_alpha = logo.getchannel("A")
        logo_alpha = logo_alpha.point(
            lambda value: round(value * min(max(opacity, 5), 100) / 100))
        logo.putalpha(logo_alpha)

        width, height = logo.width * factor, logo.height * factor
        logo_up = logo.resize((width, height), Image.Resampling.LANCZOS)
        badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_alpha = logo_up.getchannel("A").filter(
            ImageFilter.GaussianBlur(factor * 4))
        shadow_alpha = shadow_alpha.point(lambda value: round(value * 0.55))
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow.putalpha(shadow_alpha)
        badge.alpha_composite(shadow, (0, factor * 2))
        badge.alpha_composite(logo_up, (0, 0))
        return badge.resize((logo.width, logo.height), Image.Resampling.LANCZOS)

    max_logo_width = max(24, int(video_width * min(max(scale_pct, 1), 60) / 100))
    max_logo_height = max(24, int(video_height * 0.16))
    logo.thumbnail((max_logo_width, max_logo_height), Image.Resampling.LANCZOS)

    if cover_old:
        badge_width = max(
            int(video_width * min(max(cover_width_pct, 8), 45) / 100),
            logo.width + padding * 2)
        badge_height = max(
            int(video_height * min(max(cover_height_pct, 5), 35) / 100),
            logo.height + padding * 2)
        badge_width = min(badge_width, max(1, int(video_width * 0.45)))
        badge_height = min(badge_height, max(1, int(video_height * 0.35)))
    else:
        badge_width = logo.width + padding * 2
        badge_height = logo.height + padding * 2

    content_width = max(1, badge_width - padding * 2)
    content_height = max(1, badge_height - padding * 2)
    ratio = min(content_width / logo.width, content_height / logo.height, 1.0)
    if ratio < 1:
        logo = logo.resize((max(1, round(logo.width * ratio)),
                            max(1, round(logo.height * ratio))),
                           Image.Resampling.LANCZOS)
    logo_alpha = logo.getchannel("A")
    logo_alpha = logo_alpha.point(
        lambda value: round(value * min(max(opacity, 5), 100) / 100))
    logo.putalpha(logo_alpha)

    width, height = badge_width * factor, badge_height * factor
    badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    logo = logo.resize((logo.width * factor, logo.height * factor),
                       Image.Resampling.LANCZOS)
    logo_x = (width - logo.width) // 2
    logo_y = (height - logo.height) // 2

    if cover_old:
        inset = max(2, padding // 3) * factor
        bounds = (inset, inset, width - inset - 1, height - inset - 1)
        radius = max(8, int(min(badge_width, badge_height) * 0.22)) * factor
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (bounds[0], bounds[1] + factor * 3, bounds[2], bounds[3] + factor * 3),
            radius=radius, fill=(0, 0, 0, 100))
        badge.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(factor * 3)))
        panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle(
            bounds, radius=radius, fill=(13, 18, 29, 255),
            outline=(255, 255, 255, 62), width=factor)
        badge.alpha_composite(panel)
    else:
        shadow_alpha = logo.getchannel("A").filter(
            ImageFilter.GaussianBlur(factor * 2))
        shadow_alpha = shadow_alpha.point(lambda value: round(value * 0.5))
        shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
        shadow.putalpha(shadow_alpha)
        badge.alpha_composite(shadow, (logo_x + factor, logo_y + factor * 2))

    badge.alpha_composite(logo, (logo_x, logo_y))
    return badge.resize((badge_width, badge_height), Image.Resampling.LANCZOS)


def apply_logo_overlay(input_path, logo_path, position_name="أعلى اليمين",
                       scale_pct=8, opacity=100, cover_old=True,
                       landscape_cover_width_pct=20, landscape_cover_height_pct=19,
                       portrait_cover_width_pct=25, portrait_cover_height_pct=12,
                       secondary_enabled=False, secondary_cover_old=False,
                       plate=True, log_cb=None):
    if not os.path.isfile(logo_path):
        if log_cb:
            log_cb("ملف الشعار غير موجود — أوقف الرفع حتى لا يُنشر الفيديو بلا شعار")
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        if log_cb:
            log_cb("تعذر تركيب الشعار: FFmpeg غير مثبت. ثبّته وأضفه إلى PATH قبل إعادة المحاولة.")
        return None
    try:
        video_width, video_height, _ = probe_video(input_path)
    except Exception as error:
        safe_error = redact_sensitive_text(error)
        if log_cb:
            log_cb(f"تعذر فحص الفيديو قبل تركيب الشعار: {safe_error}. تحقق من سلامة الملف وffprobe.")
        return None
    if video_width <= 0 or video_height <= 0:
        if log_cb:
            log_cb("تعذرت قراءة أبعاد الفيديو — أوقف الرفع")
        return None
    is_landscape = video_width >= video_height
    cover_width = landscape_cover_width_pct if is_landscape else portrait_cover_width_pct
    cover_height = landscape_cover_height_pct if is_landscape else portrait_cover_height_pct
    try:
        badge = create_logo_badge(
            logo_path, video_width, video_height, scale_pct, opacity, cover_old,
            cover_width, cover_height, log_cb, plate=plate)
        secondary_badge = None
        if secondary_enabled:
            secondary_width = min(35, cover_width + (8 if is_landscape else 5))
            secondary_height = max(5, min(15, round(cover_height * 0.45)))
            secondary_badge = create_logo_badge(
                logo_path, video_width, video_height,
                max(4, round(scale_pct * 0.65)), opacity, secondary_cover_old,
                secondary_width, secondary_height, log_cb, plate=plate)
    except Exception as error:
        logging.exception("تعذر تجهيز شارة الشعار")
        if log_cb:
            log_cb(f"تعذر تجهيز الشعار: {error}")
        return None

    margin = max(6, round(min(video_width, video_height) *
                          (0.025 if cover_old else 0.035)))
    secondary_position = "أسفل اليمين" if position_name == "أسفل اليسار" else "أسفل اليسار"
    out_path = None
    try:
        output_directory = os.path.dirname(os.path.abspath(input_path))
        descriptor, out_path = tempfile.mkstemp(
            prefix=f"{os.path.splitext(os.path.basename(input_path))[0]}_branded-",
            suffix=".mp4",
            dir=output_directory,
        )
        os.close(descriptor)
        with tempfile.TemporaryDirectory(prefix="fb-youtube-logo-") as temp_dir:
            badge_path = os.path.join(temp_dir, "logo_badge.png")
            badge.save(badge_path, format="PNG", optimize=True)
            cmd = [ffmpeg_bin, "-y", "-i", input_path, "-i", badge_path]
            if secondary_badge is not None:
                secondary_path = os.path.join(temp_dir, "logo_badge_secondary.png")
                secondary_badge.save(secondary_path, format="PNG", optimize=True)
                cmd.extend(["-i", secondary_path])
            cmd.extend([
                "-filter_complex", build_overlay_filter(
                    position_name, margin, secondary_enabled, secondary_position),
                "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "20", "-c:a", "copy",
                "-movflags", "+faststart", out_path,
            ])
            if log_cb:
                message = "تركيب بطاقة الشعار الاحترافية"
                if secondary_enabled:
                    message += " مع العلامة الثانوية"
                log_cb(message + "…")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            detail = redact_sensitive_text((proc.stderr or "").strip())[:300]
            logging.warning("ffmpeg logo overlay failed: %s", detail or "no output")
            if log_cb:
                log_cb(f"فشل تركيب الشعار: {detail or 'لم يُنشأ ملف صالح'}. تحقق من FFmpeg والملف والشعار.")
            os.unlink(out_path)
            return None
        if os.name == "posix" and not restrict_file_permissions(out_path):
            os.unlink(out_path)
            if log_cb:
                log_cb("تعذر تقييد أذونات فيديو الشعار؛ لم يُستخدم الملف.")
            return None
        return out_path
    except Exception as error:
        if out_path and os.path.isfile(out_path) and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass
        safe_error = redact_sensitive_text(error)
        logging.warning("خطأ في تركيب الشعار: %s", safe_error)
        if log_cb:
            log_cb(f"تعذر تركيب الشعار: {safe_error}. تحقق من FFmpeg وصلاحيات المجلد.")
        return None

# ════════════════════════════════════════════════════
# THUMBNAILS / SHORTS CUT / ANALYTICS (v4.11.2)
# ════════════════════════════════════════════════════
THUMB_WIDTH, THUMB_HEIGHT = 1280, 720
THUMB_BUDGET_UNITS = 50  # thumbnails.set = 50 وحدة من الحصة العامة
THUMB_CANDIDATE_COUNT = 6     # عدد اللقطات المرشحة لاختيار أفضل صورة مصغّرة
THUMB_MIN_SPAN_SECONDS = 8.0  # أقل مدة يستحق الفحص المتعدد للقطات


def _contains_arabic(text):
    return any("\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f" or "\u08a0" <= char <= "\u08ff" for char in text)


def _clean_thumbnail_title(text):
    text = re.sub(r"https?://\S+", " ", str(text or ""), flags=re.IGNORECASE)
    text = re.sub(r"(?<!\S)#[^\s#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _thumbnail_uses_raqm():
    try:
        return features.check("raqm")
    except (AttributeError, ValueError):
        return False


def _thumbnail_display_text(text):
    rtl = _contains_arabic(text)
    if not rtl or _thumbnail_uses_raqm():
        return text, rtl
    return get_display(arabic_reshaper.reshape(text)), True


def _thumbnail_text_width(text, font):
    display, rtl = _thumbnail_display_text(text)
    if rtl and _thumbnail_uses_raqm():
        try:
            return font.getlength(display, direction="rtl", language="ar")
        except (TypeError, ValueError):
            pass
    return font.getlength(display)


def _ellipsize_thumbnail_text(text, max_width, font):
    text = text.rstrip()
    while text and _thumbnail_text_width(text + "…", font) > max_width:
        text = text[:-1].rstrip()
    return text + "…" if text else "…"


def _wrap_text(text, max_width, font=None, max_lines=3):
    text = _clean_thumbnail_title(text)
    if not text or max_width <= 0 or max_lines <= 0:
        return []
    font = font or _load_caption_font(54)
    lines, current = [], ""
    truncated = False
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if _thumbnail_text_width(candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            truncated = True
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []
    if truncated or _thumbnail_text_width(lines[-1], font) > max_width:
        lines[-1] = _ellipsize_thumbnail_text(lines[-1], max_width, font)
    return lines[:max_lines]


_FONT_COVERAGE_CACHE = {}


def _font_covers_text(path, text):
    """يتحقق أن الخط يرسم كل حروف النص؛ يخزّن جدول المحارف لكل خط مرة واحدة."""
    cmap = _FONT_COVERAGE_CACHE.get(path)
    if cmap is None:
        try:
            from fontTools.ttLib import TTFont
            cmap = TTFont(path, fontNumber=0).getBestCmap() or {}
        except Exception:
            cmap = {}
        _FONT_COVERAGE_CACHE[path] = cmap
    if not cmap:
        return False
    return all(ord(char) in cmap or char.isspace() for char in text)


def _load_caption_font(size, text=None):
    windows_fonts = os.path.join(os.environ.get("WINDIR", r"C:\\Windows"), "Fonts")
    candidates = (
        os.path.join(windows_fonts, "arialbd.ttf"),
        os.path.join(windows_fonts, "tahomabd.ttf"),
        os.path.join(windows_fonts, "calibrib.ttf"),
        os.path.join(windows_fonts, "seguisb.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "DejaVuSans-Bold.ttf",
        "Arial Bold.ttf",
        "arial.ttf",
    )
    for name in candidates:
        if text and not _font_covers_text(name, text):
            continue
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    if text:
        for name in candidates:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap_thumbnail_exact(text, max_width, font, max_lines=2):
    """يغلّف النص دون حذف؛ يعيد [] إذا لم يتسع في max_lines أسطر بهذا الخط."""
    text = _clean_thumbnail_title(text)
    if not text or max_width <= 0 or max_lines <= 0:
        return []
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if _thumbnail_text_width(candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            return []
    if current and len(lines) < max_lines:
        lines.append(current)
    if lines and all(_thumbnail_text_width(line, font) <= max_width for line in lines):
        return lines
    return []


def _fit_thumbnail_lines(text, max_width, width, max_lines=2):
    """يختار أكبر حجم خط يُدخل العنوان كاملاً في الأسطر المسموحة، ثم يتراجع للقص بحذف."""
    start = max(36, round(width * 0.052))
    floor = max(30, round(width * 0.030))
    size = start
    while size > floor:
        font = _load_caption_font(size, text=text)
        lines = _wrap_thumbnail_exact(text, max_width, font, max_lines)
        if lines:
            return lines, font
        size = max(floor, round(size * 0.90))
    font = _load_caption_font(floor, text=text)
    lines = _wrap_thumbnail_exact(text, max_width, font, max_lines)
    if lines:
        return lines, font
    return _wrap_text(text, max_width, font=font, max_lines=max_lines), font


def _draw_thumbnail_title(image, title):
    text = _clean_thumbnail_title(title)
    if not text:
        return image
    width, height = image.size
    rtl = _contains_arabic(text)
    margin = max(48, round(width * 0.065))
    accent = max(7, round(width * 0.007))
    text_width = width - margin * 2 - accent - 24
    lines, font = _fit_thumbnail_lines(text, text_width, width, max_lines=2)
    if not lines:
        return image
    font_size = getattr(font, "size", 54)
    ascent, descent = font.getmetrics()
    line_height = max(round((ascent + descent) * 1.04),
                      round(font_size * 1.30), round(height * 0.09))
    top_pad = max(15, round(height * 0.026))
    bottom_pad = max(18, round(height * 0.03))
    band_height = min(height, top_pad + line_height * len(lines) + bottom_pad)
    band_top = height - band_height
    band = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    band_draw = ImageDraw.Draw(band)
    for y in range(band_height):
        progress = y / max(1, band_height - 1)
        alpha = round(22 + 208 * progress ** 1.45)
        band_draw.line((0, y, width, y), fill=(7, 12, 22, alpha))
    result = image.convert("RGBA")
    result.alpha_composite(band, (0, band_top))
    draw = ImageDraw.Draw(result)
    accent_x = width - margin - accent if rtl else margin
    draw.rounded_rectangle(
        (accent_x, band_top + top_pad, accent_x + accent, height - bottom_pad),
        radius=max(3, accent // 2), fill=(239, 73, 78, 245),
    )
    placements = []
    y = band_top + top_pad + max(0, (line_height - (ascent + descent)) // 2)
    right_edge = width - margin - accent - 22
    for line in lines:
        display, line_rtl = _thumbnail_display_text(line)
        text_rtl = rtl or line_rtl
        if text_rtl and _thumbnail_uses_raqm():
            placements.append(((right_edge, y), display,
                               {"anchor": "ra", "direction": "rtl", "language": "ar"}))
        elif text_rtl:
            line_width = font.getlength(display)
            placements.append(((right_edge - line_width, y), display, {"anchor": "la"}))
        else:
            placements.append(((margin + accent + 22, y), display, {"anchor": "la"}))
        y += line_height
    shadow_layer = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    for (pos, display, anchor_kwargs) in placements:
        shadow_draw.text(
            (pos[0] + 2, pos[1] + 4), display, font=font, fill=(0, 0, 0, 215),
            stroke_width=2, stroke_fill=(0, 0, 0, 215), **anchor_kwargs,
        )
    result.alpha_composite(shadow_layer.filter(ImageFilter.GaussianBlur(5)), (0, band_top))
    draw = ImageDraw.Draw(result)
    for (pos, display, anchor_kwargs) in placements:
        draw.text(
            pos, display, font=font, fill=(255, 255, 255, 255),
            stroke_width=2, stroke_fill=(0, 0, 0, 170), **anchor_kwargs,
        )
    return result


def render_thumbnail_title_from_base(base_path, title, out_path=None):
    if not os.path.isfile(base_path) or os.path.islink(base_path):
        return None
    owned_output = False
    if out_path is None:
        out_path = _temporary_thumbnail_path("fb-youtube-preview-title-")
        owned_output = True
    else:
        out_path = os.path.abspath(os.fspath(out_path))
        if (
            os.path.islink(out_path)
            or not os.path.isfile(out_path)
            or not os.path.basename(out_path).startswith("fb-youtube-preview-")
        ):
            return None
    success = False
    try:
        with Image.open(base_path) as source:
            image = source.convert("RGB")
        image = _draw_thumbnail_title(image, title)
        image.convert("RGB").save(out_path, "PNG")
        if os.path.getsize(out_path) <= 0:
            raise OSError("لم يُنشأ ملف معاينة صالح.")
        success = True
        return out_path
    except Exception as error:
        logging.warning("تعذر تحديث عنوان الصورة المصغرة: %s", redact_sensitive_text(error))
        return None
    finally:
        if owned_output and not success and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass


def _temporary_png_path(prefix="fb-youtube-thumbnail-"):
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".png")
    os.close(fd)
    os.remove(path)
    return path


def _temporary_thumbnail_path(prefix):
    descriptor, path = tempfile.mkstemp(prefix=prefix, suffix=".png")
    os.close(descriptor)
    return path


def _thumbnail_frame_score(path):
    """يعيد درجة ملاءمة الإطار (حدة × إضاءة)؛ 0 عند تعذر قراءة الصورة أو إضاءة شديدة الشحوب/الظلمة."""
    try:
        with Image.open(path) as source:
            gray = source.convert("L").resize((160, 90))
            brightness = ImageStat.Stat(gray).mean[0]
            edges = gray.filter(ImageFilter.FIND_EDGES)
            sharpness = ImageStat.Stat(edges).mean[0]
    except Exception:
        return 0.0
    if brightness < 40.0 or brightness > 225.0:
        return 0.0
    closeness = 1.0 - min(1.0, abs(brightness - 118.0) / 118.0)
    return sharpness * (0.35 + 0.65 * closeness)


def _extract_thumbnail_candidates(video_path, duration, tmp_dir):
    """يستخرج لقطات مرشحة موزّعة على معظم مدة الفيديو في تمريرة ffmpeg واحدة."""
    duration = float(duration or 0.0)
    if duration < THUMB_MIN_SPAN_SECONDS:
        return []
    start = duration * 0.10
    span = duration * 0.80
    pattern = os.path.join(tmp_dir, "frame%03d.png")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.2f}", "-i", video_path,
           "-frames:v", str(THUMB_CANDIDATE_COUNT),
           "-vf", (f"fps={THUMB_CANDIDATE_COUNT / span:.5f},"
                   f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={THUMB_WIDTH}:{THUMB_HEIGHT}"),
           pattern]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        logging.warning("تعذر استخراج لقطات الصورة المصغرة: %s", error)
        return []
    if proc.returncode != 0:
        logging.warning("فشل استخراج لقطات الصورة المصغرة: %s", (proc.stderr or "")[-500:])
        return []
    candidates = []
    for path in sorted(glob.glob(os.path.join(tmp_dir, "frame*.png"))):
        try:
            if os.path.getsize(path) > 0:
                candidates.append(path)
        except OSError:
            continue
    return candidates


def _pick_best_frame(video_path, duration, log_cb=None):
    """يختار أوضح وأفضل إضاءة من لقطات مرشحة؛ يعيد مسار PNG مؤقت أو None."""
    tmp_dir = tempfile.mkdtemp(prefix="fb-youtube-frames-")
    try:
        candidates = _extract_thumbnail_candidates(video_path, duration, tmp_dir)
        if not candidates:
            return None
        scored = sorted(candidates, key=_thumbnail_frame_score, reverse=True)
        best = scored[0] if _thumbnail_frame_score(scored[0]) > 0 else None
        if best is None:
            return None
        fd, kept = tempfile.mkstemp(prefix="fb-youtube-frame-best-", suffix=".png")
        os.close(fd)
        shutil.copyfile(best, kept)
        return kept
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _enhance_frame(image):
    """يرفع التباين والتشبع والحدة قليلاً لتظهر الصورة أوضح في شبكة YouTube."""
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Color(image).enhance(1.10)
    image = ImageEnhance.Sharpness(image).enhance(1.25)
    return image


def _capture_fallback_frame(video_path, duration, out_path, log_cb=None):
    """المسار القديم: لقطة واحدة من ربع المدة؛ يعيد True عند النجاح."""
    at = min(max((duration or 0) * 0.25, 1.0), max((duration or 0) - 0.5, 0.0))
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}",
           "-i", video_path, "-frames:v", "1",
           "-vf", f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=increase,crop={THUMB_WIDTH}:{THUMB_HEIGHT}",
           out_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        proc = None
        logging.warning("فشل التقاط الصورة المصغرة: %s", error)
    if proc is None or proc.returncode != 0 or not os.path.isfile(out_path):
        logging.warning("فشل التقاط الصورة المصغرة: %s", (getattr(proc, "stderr", "") or "")[-500:])
        if log_cb:
            log_cb("فشل توليد الصورة المصغرة — سيُرفع الفيديو بدونها")
        return False
    return True


def generate_thumbnail(video_path, title, out_path=None, log_cb=None, *, allow_empty_output=False):
    """يلتقط أفضل لقطة ويكتب العنوان؛ يستخدم ملفاً فريداً ولا يستبدل ملفات موجودة."""
    if not ffmpeg_available():
        if log_cb:
            log_cb("تعذر توليد الصورة المصغرة: FFmpeg غير مثبت. ثبّته وأضفه إلى PATH.")
        return None
    owned_output = False
    if out_path is None:
        out_path = _temporary_thumbnail_path("fb-youtube-thumbnail-")
        owned_output = True
    else:
        out_path = os.path.abspath(os.fspath(out_path))
        if os.path.lexists(out_path):
            if (
                not allow_empty_output
                or os.path.islink(out_path)
                or not os.path.isfile(out_path)
                or os.path.getsize(out_path) != 0
            ):
                if log_cb:
                    log_cb("تعذر حفظ الصورة المصغرة: ملف الإخراج موجود مسبقاً ولم يُستبدل.")
                return None
        else:
            try:
                descriptor = os.open(
                    out_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                os.close(descriptor)
                owned_output = True
            except OSError as error:
                if log_cb:
                    log_cb(f"تعذر إنشاء ملف الصورة المصغرة: {redact_sensitive_text(error)}")
                return None
    success = False
    try:
        _, _, duration = probe_video(video_path)
        frame_path = _pick_best_frame(video_path, duration, log_cb=log_cb)
        if frame_path is not None:
            try:
                with Image.open(frame_path) as source_image:
                    thumbnail = _enhance_frame(source_image.convert("RGB"))
            finally:
                try:
                    os.remove(frame_path)
                except OSError:
                    pass
            thumbnail.save(out_path, "PNG")
        else:
            if not _capture_fallback_frame(video_path, duration, out_path, log_cb=log_cb):
                return None
            with Image.open(out_path) as source_image:
                thumbnail = _enhance_frame(source_image.convert("RGB"))
            thumbnail.save(out_path, "PNG")
        if _clean_thumbnail_title(title):
            thumbnail = _draw_thumbnail_title(thumbnail, title)
            thumbnail.convert("RGB").save(out_path, "PNG")
        if not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            raise OSError("لم يُنشأ ملف صورة مصغّرة صالح.")
        if os.name == "posix" and not restrict_file_permissions(out_path):
            raise OSError("تعذر تقييد أذونات ملف الصورة المصغّرة.")
        success = True
        return out_path
    except Exception as error:
        safe_error = redact_sensitive_text(error)
        logging.warning("خطأ في توليد الصورة المصغرة: %s", safe_error)
        if log_cb:
            log_cb(f"تعذر توليد الصورة المصغرة: {safe_error}. تحقق من الملف وFFmpeg ثم أعد المحاولة.")
        return None
    finally:
        if owned_output and not success and out_path and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                logging.warning("تعذر حذف الصورة المصغّرة الجزئية: %s", redact_sensitive_text(error))


def create_thumbnail_preview(video_path, title, log_cb=None):
    base_path = _temporary_thumbnail_path("fb-youtube-preview-base-")
    preview_path = _temporary_thumbnail_path("fb-youtube-preview-")
    if not generate_thumbnail(
        video_path, "", out_path=base_path, log_cb=log_cb, allow_empty_output=True
    ):
        remove_thumbnail_preview(base_path, preview_path)
        return None, None
    if not render_thumbnail_title_from_base(base_path, title, out_path=preview_path):
        remove_thumbnail_preview(base_path, preview_path)
        return None, None
    return base_path, preview_path


def remove_thumbnail_preview(*paths):
    temp_root = os.path.realpath(tempfile.gettempdir())
    allowed_prefixes = ("fb-youtube-preview-", "fb-youtube-thumbnail-", "fb-youtube-thumb-")
    for path in set(paths):
        if not path:
            continue
        candidate = os.path.abspath(os.fspath(path))
        if os.path.islink(candidate) or not os.path.basename(candidate).startswith(allowed_prefixes):
            continue
        if os.path.realpath(os.path.dirname(candidate)) != temp_root:
            continue
        try:
            os.unlink(candidate)
        except FileNotFoundError:
            pass
        except OSError as error:
            logging.warning("تعذر حذف صورة معاينة مؤقتة: %s", redact_sensitive_text(error))


YOUTUBE_THUMB_MAX_BYTES = 2_000_000  # حد YouTube للصور المصغرة 2MB


def _thumbnail_upload_path(path):
    """يعيد مساراً ضمن حد 2MB؛ يحوّل إلى JPEG متدرّج الجودة إذا تجاوز PNG الحد."""
    try:
        if os.path.getsize(path) <= YOUTUBE_THUMB_MAX_BYTES:
            return path
    except OSError:
        return path
    descriptor, jpg_path = tempfile.mkstemp(prefix="fb-youtube-thumb-", suffix=".jpg")
    os.close(descriptor)
    for quality in (90, 80, 70):
        try:
            with Image.open(path) as source:
                source.convert("RGB").save(jpg_path, "JPEG", quality=quality, optimize=True)
            if os.path.getsize(jpg_path) <= YOUTUBE_THUMB_MAX_BYTES:
                break
        except Exception:
            break
    return jpg_path


def _http_error_reason(error):
    """يستخرج سبب خطأ Google API القابل للقراءة بدل رسالة عامة."""
    try:
        data = json.loads(error.resp.get("content", "{}") or "{}")
        errs = (data.get("error") or {}).get("errors") or []
        reason = errs[0].get("reason") if errs else (data.get("error") or {}).get("status")
        if reason:
            reasons = {
                "quotaExceeded": "نفدت حصة YouTube Data API اليومية",
                "dailyLimitExceeded": "نفدت حصة YouTube Data API اليومية",
                "rateLimitExceeded": "تجاوز معدل الطلبات — أعد المحاولة لاحقاً",
                "forbidden": "تتطلب قناة موثّقة أو صلاحية حساب كافية",
                "unauthorized": "صلاحية OAuth غير كافية لهذا الحساب",
                "invalidThumbnailImageFile": "صيغة الصورة غير مقبولة",
                "thumbnailImageTooLarge": "حجم الصورة يتجاوز 2MB",
            }
            return reasons.get(reason, reason)
    except Exception:
        pass
    return "سبب غير معروف — راجع errors.log"


def upload_thumbnail(youtube, video_id, path, log_cb=None):
    """يرفع صورة مصغرة للفيديو (50 وحدة). الفشل يحذّر فقط ولا يوقف الرفع."""
    if not os.path.isfile(path):
        if log_cb:
            log_cb("ملف الصورة المصغرة مفقود")
        return False
    upload_path = _thumbnail_upload_path(path)
    converted = upload_path != path
    try:
        youtube.thumbnails().set(videoId=video_id,
                                 media_body=MediaFileUpload(upload_path)).execute()
        if log_cb:
            if converted:
                log_cb(f"رُفعت الصورة المصغرة للفيديو {video_id} (صيغة JPEG ضمن حد 2MB)")
            else:
                log_cb(f"رُفعت الصورة المصغرة للفيديو {video_id}")
        return True
    except HttpError as error:
        logging.warning("فشل رفع الصورة المصغرة %s: %s", video_id, error)
        if log_cb:
            log_cb(f"تعذر رفع الصورة المصغرة ({_http_error_reason(error)}) — تابعنا الرفع")
        return False
    finally:
        if converted:
            try:
                os.remove(upload_path)
            except OSError:
                pass


SHORTS_CLIP_SECONDS = 58
SHORTS_MIN_SOURCE = 95  # أقل مدة للمقطع الأصلي حتى يستحق القص


def _audio_rms_per_second(video_path):
    """يعيد {ثانية: متوسط RMS (dB)} عبر ffmpeg astats. {} عند التعذر."""
    try:
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", video_path, "-vn",
               "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
               "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        samples, current_t = [], None
        for raw in (proc.stderr or "").splitlines() + (proc.stdout or "").splitlines():
            line = raw.strip()
            m = re.search(r"pts_time:([0-9.]+)", line)
            if m:
                current_t = float(m.group(1))
                continue
            if "RMS_level" in line and current_t is not None:
                m2 = re.search(r"RMS_level=(-?[0-9.]+|-inf)", line)
                if m2:
                    try:
                        val = float(m2.group(1))
                    except ValueError:
                        val = -91.0
                    if val == float("-inf"):
                        val = -91.0
                    samples.append((int(current_t), val))
                    current_t = None
        buckets = {}
        for sec, val in samples:
            buckets.setdefault(sec, []).append(val)
        return {sec: sum(vals) / len(vals) for sec, vals in buckets.items()}
    except Exception as error:
        logging.warning("تعذر تحليل الصوت للقص الذكي: %s", error)
        return {}


def find_hot_segment(rms_by_second, clip_seconds=SHORTS_CLIP_SECONDS,
                     min_source=SHORTS_MIN_SOURCE, source_duration=None):
    """نافذة انزلاقية بطول الـ Short تختار أعلى متوسط صوت. يعيد (بداية، مدة) أو None."""
    span = int(source_duration) if source_duration else (max(rms_by_second) + 1 if rms_by_second else 0)
    if span < min_source or span < clip_seconds:
        return None
    last_start = span - clip_seconds
    best_s, best_avg = 0, -1e9
    for s in range(0, last_start + 1):
        window = [rms_by_second[i] for i in range(s, s + clip_seconds) if i in rms_by_second]
        if not window:
            continue
        avg = sum(window) / len(window)
        if avg > best_avg:
            best_avg, best_s = avg, s
    if best_avg <= -1e8:
        return None
    return best_s, clip_seconds


def cut_shorts_clip(source_path, start, duration, out_path=None, log_cb=None):
    """يقص نسخة مؤقتة فريدة ولا يستبدل أي ملف موجود."""
    source_path = os.path.abspath(os.fspath(source_path or ""))
    if os.path.islink(source_path) or not os.path.isfile(source_path):
        if log_cb:
            log_cb("تعذر قص Shorts: ملف المصدر غير موجود أو ليس ملفاً عادياً.")
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        if log_cb:
            log_cb("تعذر قص Shorts: FFmpeg غير مثبت. ثبّته وأضفه إلى PATH.")
        return None
    try:
        start_value = float(start)
        duration_value = float(duration)
    except (TypeError, ValueError, OverflowError):
        if log_cb:
            log_cb("تعذر قص Shorts: مدة البداية أو القص غير صالحة.")
        return None
    if not math.isfinite(start_value) or not math.isfinite(duration_value) or start_value < 0 or duration_value <= 0:
        if log_cb:
            log_cb("تعذر قص Shorts: مدة البداية أو القص غير صالحة.")
        return None
    owned_output = False
    if out_path is None:
        output_directory = os.path.dirname(source_path)
        prefix = f"{os.path.splitext(os.path.basename(source_path))[0]}_shorts-"
        try:
            descriptor, out_path = tempfile.mkstemp(
                prefix=prefix, suffix=".mp4", dir=output_directory
            )
            os.close(descriptor)
            owned_output = True
        except OSError as error:
            if log_cb:
                log_cb(f"تعذر إنشاء ملف Shorts مؤقت: {redact_sensitive_text(error)}")
            return None
    else:
        out_path = os.path.abspath(os.fspath(out_path))
        if os.path.lexists(out_path):
            if log_cb:
                log_cb("تعذر قص Shorts: ملف الإخراج موجود مسبقاً ولم يُستبدل.")
            return None
        owned_output = True
    success = False
    try:
        cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", str(start_value), "-t", str(duration_value), "-i", source_path,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-movflags", "+faststart", out_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            detail = redact_sensitive_text((proc.stderr or "").strip())[-300:]
            logging.warning("فشل قص نسخة Shorts: %s", detail or "لم يُنشأ ملف صالح")
            if log_cb:
                log_cb(f"فشل قص Shorts: {detail or 'لم يُنشأ ملف صالح'}. تحقق من FFmpeg والملف ثم أعد المحاولة.")
            return None
        if os.name == "posix" and not restrict_file_permissions(out_path):
            if log_cb:
                log_cb("تعذر تقييد أذونات ملف Shorts؛ لم يُستخدم.")
            return None
        success = True
        return out_path
    except Exception as error:
        safe_error = redact_sensitive_text(error)
        logging.warning("خطأ في قص نسخة Shorts: %s", safe_error)
        if log_cb:
            log_cb(f"خطأ في قص Shorts: {safe_error}. تحقق من FFmpeg والملف ثم أعد المحاولة.")
        return None
    finally:
        if owned_output and not success and out_path and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                logging.warning("تعذر حذف ملف Shorts الجزئي: %s", redact_sensitive_text(error))


def _read_history():
    """يقرأ سجل الرفع jsonl بسطور سليمة فقط."""
    if not os.path.exists(HISTORY_FILE):
        return []
    out = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def collect_video_stats(youtube, log_cb=None, limit=50):
    """يقرأ إحصاءات آخر الفيديوهات المرفوعة ويحفظها في state.json (تكلفة 1 وحدة/نداء)."""
    history = [h for h in _read_history()
               if h.get("status") == "uploaded" and h.get("youtube_id")]
    ids = list(dict.fromkeys(h["youtube_id"] for h in history))[-limit:]
    if not ids:
        return {}
    state = load_state()
    stats = dict(state.get("video_stats") or {})
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            resp = youtube.videos().list(part="statistics", id=",".join(chunk)).execute()
        except Exception as error:
            logging.warning("تعذر قراءة إحصاءات يوتيوب: %s", error)
            continue
        for item in resp.get("items") or []:
            st = item.get("statistics") or {}
            try:
                stats[item["id"]] = {
                    "views": int(st.get("viewCount") or 0),
                    "likes": int(st.get("likeCount") or 0),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            except (KeyError, ValueError, TypeError):
                continue
    state["video_stats"] = stats
    save_state(state)
    if log_cb:
        log_cb(f"حدّثنا إحصاءات {len(stats)} فيديو")
    return stats


def top_performing_titles(limit=3, min_views=0):
    """أفضل العناوين حسب المشاهدات — تغذية راجعة لتوليد العناوين."""
    history = {h.get("youtube_id"): h.get("title", "")
               for h in _read_history() if h.get("youtube_id")}
    stats = load_state().get("video_stats") or {}
    scored = []
    for vid, title in history.items():
        if not title:
            continue
        views = (stats.get(vid) or {}).get("views", 0)
        if views >= min_views:
            scored.append((views, title))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [f"{title} ({views} مشاهدة)" for views, title in scored[:limit]]


def resolve_playlist_id(item, pages, settings):
    """قائمة تشغيل الصفحة إن وُجدت، وإلا الافتراضية العامة، وإلا None."""
    name = (item.get("page") or "")
    page_cfg = next((p for p in pages or []
                     if (p.get("name") or "") == name), None) or {}
    return (page_cfg.get("playlist") or settings.get("playlist_id") or "").strip() or None


# ════════════════════════════════════════════════════
# CATEGORY & DURATION FILTERS
# ════════════════════════════════════════════════════
YT_CATEGORIES = {
    "ترفيه (24)": "24", "أخبار وسياسة (25)": "25", "رياضة (17)": "17",
    "أشخاص ومدونات (22)": "22", "كوميديا (23)": "23", "موسيقى (10)": "10",
    "ألعاب (20)": "20", "علوم وتقنية (28)": "28", "تعليم (27)": "27",
}

def duration_ok(duration, min_sec, max_sec):
    """فلتر مدة الفيديو: 0 في الحد الأقصى يعني بلا سقف."""
    if duration and min_sec and duration < min_sec:
        return False, f"أقصر من {min_sec} ثانية ({int(duration)}ث)"
    if duration and max_sec and duration > max_sec:
        return False, f"أطول من {max_sec} ثانية ({int(duration)}ث)"
    return True, ""

def next_publish_time(min_delay, max_delay):
    """جدولة تراكمية محفوظة — لا تتداخل مواعيد النشر حتى بين عمليات التشغيل."""
    state = load_state()
    base = datetime.now(timezone.utc) + timedelta(minutes=5)
    stored = state.get("next_publish_at")
    if stored:
        try:
            stored_dt = datetime.fromisoformat(stored)
            if stored_dt > base:
                base = stored_dt
        except ValueError:
            pass
    pub = base + timedelta(minutes=random.randint(min_delay, max_delay))
    state["next_publish_at"] = pub.isoformat()
    save_state(state)
    return pub

def validate_media(path, log_cb=None, *, expected_duration=None):
    def reject(code, cause, action):
        details = {"error": code, "cause": cause, "action": action}
        if log_cb:
            log_cb(f"فشل فحص ملف الفيديو: {cause}. {action}")
        return False, details

    if not path or os.path.islink(path) or not os.path.isfile(path):
        return reject("missing-file", "ملف التنزيل غير موجود أو ليس ملفاً عادياً.",
                      "أعد تنزيل المصدر قبل محاولة الرفع.")
    try:
        file_size = os.path.getsize(path)
    except OSError:
        return reject("unreadable-file", "تعذرت قراءة حجم الملف محلياً.",
                      "تحقق من صلاحيات المجلد ومساحة القرص ثم أعد التنزيل.")
    if file_size <= 0:
        return reject("empty-file", "ملف التنزيل فارغ.",
                      "أعد تنزيل المصدر قبل محاولة الرفع.")
    if file_size > 256 * 1024**3:
        return reject("file-too-large", "تجاوز الملف الحد الأقصى المسموح به لرفع YouTube.",
                      "استخدم ملفاً أصغر من 256 GB.")
    if os.path.basename(path).lower().endswith((".part", ".ytdl", ".partial", ".tmp")):
        return reject("partial-file", "امتداد الملف يدل على تنزيل جزئي.",
                      "انتظر اكتمال التنزيل ثم أعد الفحص.")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return reject("ffprobe-missing", "ffprobe غير مثبت، لذلك لا يمكن إثبات سلامة الملف.",
                      "ثبّت FFmpeg الذي يتضمن ffprobe وأضفه إلى PATH، ثم أعد التشغيل.")

    try:
        proc = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries",
             "format=duration,size,format_name:stream=codec_type,codec_name,width,height",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return reject("ffprobe-timeout", "انتهت مهلة فحص الملف.",
                      "افحص الملف أو أعد تنزيله قبل إعادة المحاولة.")
    except OSError:
        return reject("ffprobe-unavailable", "تعذر تشغيل ffprobe.",
                      "تحقق من تثبيت FFmpeg ومسار PATH ثم أعد التشغيل.")
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        detail = redact_sensitive_text((proc.stderr or "").strip())[:240]
        cause = f"رفض ffprobe الملف أو لم يُرجع بيانات صالحة{': ' + detail if detail else ''}."
        return reject("ffprobe-failed", cause,
                      "أعد تنزيل الملف من المصدر؛ لن يُرسل ملف غير متحقق إلى YouTube.")
    try:
        data = json.loads(proc.stdout)
    except (TypeError, json.JSONDecodeError):
        return reject("ffprobe-invalid-json", "أعاد ffprobe بيانات غير قابلة للتحليل.",
                      "تحقق من سلامة تثبيت FFmpeg وأعد فحص الملف.")
    if not isinstance(data, dict):
        return reject("ffprobe-invalid-result", "بنية نتيجة ffprobe غير صالحة.",
                      "تحقق من سلامة تثبيت FFmpeg وأعد فحص الملف.")
    streams = data.get("streams")
    media_format = data.get("format")
    if not isinstance(streams, list) or not isinstance(media_format, dict):
        return reject("ffprobe-incomplete-result", "نتيجة ffprobe لا تتضمن المسارات أو الحاوية.",
                      "أعد تنزيل الملف ثم أعد فحصه.")
    video_streams = [stream for stream in streams
                     if isinstance(stream, dict) and stream.get("codec_type") == "video"]
    if not video_streams:
        return reject("video-stream-missing", "لا يحتوي الملف على مسار فيديو.",
                      "اختر مصدراً يعيد ملف فيديو صالحاً ثم أعد المحاولة.")
    video_stream = video_streams[0]
    try:
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        duration = float(media_format.get("duration") or 0)
    except (TypeError, ValueError, OverflowError):
        return reject("invalid-media-metadata", "بيانات المدة أو الأبعاد غير صالحة.",
                      "أعد تنزيل الملف من المصدر ثم أعد فحصه.")
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        return reject("invalid-media-metadata", "المدة أو أبعاد الفيديو صفرية أو غير محدودة.",
                      "أعد تنزيل الملف من المصدر ثم أعد فحصه.")
    if duration > 12 * 60 * 60:
        return reject("duration-too-long", "تجاوزت مدة الفيديو الحد الأقصى ليوتيوب البالغ 12 ساعة.",
                      "قصّر الفيديو قبل محاولة الرفع.")
    extension = os.path.splitext(path)[1].lower()
    format_names = {part.strip().lower() for part in str(media_format.get("format_name") or "").split(",")}
    accepted_formats = {
        ".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        ".m4v": {"mov", "mp4"},
        ".mov": {"mov", "mp4"},
        ".3gp": {"mov", "3gp", "3g2", "mj2"},
        ".3gpp": {"mov", "3gp", "3g2", "mj2"},
        ".avi": {"avi"},
        ".wmv": {"asf"},
        ".asf": {"asf"},
        ".flv": {"flv"},
        ".webm": {"matroska", "webm"},
        ".mkv": {"matroska", "webm"},
        ".mpg": {"mpeg", "mpegvideo", "mpegts"},
        ".mpeg": {"mpeg", "mpegvideo", "mpegts"},
        ".ts": {"mpegts"},
        ".m2ts": {"mpegts"},
        ".ogg": {"ogg"},
        ".ogv": {"ogg"},
    }
    expected_formats = accepted_formats.get(extension)
    if not expected_formats or not format_names.intersection(expected_formats):
        return reject("unsupported-container", "حاوية الفيديو غير معروفة أو لا تطابق امتداد الملف.",
                      "استخدم MP4 أو WebM أو صيغة يدعمها YouTube ثم أعد الفحص.")
    try:
        reported_size = int(media_format.get("size") or file_size)
    except (TypeError, ValueError, OverflowError):
        reported_size = file_size
    if reported_size <= 0 or abs(reported_size - file_size) > max(1024 * 1024, file_size // 100):
        return reject("size-mismatch", "حجم الملف الذي أبلغ عنه ffprobe لا يطابق الملف المحلي.",
                      "أعد تنزيل الملف للتحقق من عدم كونه جزئياً.")
    if expected_duration:
        try:
            expected = float(expected_duration)
        except (TypeError, ValueError, OverflowError):
            expected = 0.0
        if math.isfinite(expected) and expected > 0 and abs(duration - expected) > max(3.0, expected * 0.05):
            return reject("duration-mismatch", "مدة الملف تختلف كثيراً عن مدة المصدر المتوقعة.",
                          "أعد تنزيل الملف أو راجع المصدر قبل الرفع.")
    return True, {
        "duration": duration,
        "size": file_size,
        "width": width,
        "height": height,
        "format": sorted(format_names),
        "codec": str(video_stream.get("codec_name") or ""),
    }


def _insert_to_playlist(youtube, playlist_id, video_id, log_cb=None):
    """إضافة الفيديو لقائمة تشغيل — خطوة اختيارية لا تُفسد نجاح الرفع ولا تُسبب رفعاً مكرراً."""
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        ).execute()
        return True
    except HttpError as error:
        logging.warning("رُفع الفيديو %s لكن تعذّرت إضافته لقائمة التشغيل: %s", video_id, error)
        if log_cb:
            log_cb(f"رُفع الفيديو لكن تعذّرت إضافته لقائمة التشغيل: {error}")
        return False


def upload_video(youtube, video_data, privacy="private", playlist_id=None, tags=None,
                 category="22", sched_time=None, log_cb=None, retries=MAX_RETRIES,
                 quota_manager=None, dry_run=False, content_review_required=False,
                 publication_approved=False, allow_public_automation=False,
                 before_upload=None, on_confirmed=None, stop_event=None):
    if dry_run:
        if log_cb:
            log_cb("وضع المحاكاة: لن يُرفع الفيديو فعلياً (dry-run).")
        return "DRY_RUN"
    publication_errors = validate_publication_controls(
        privacy, bool(sched_time), publication_approved, allow_public_automation
    )
    if publication_errors:
        if log_cb:
            log_cb("أُوقف النشر قبل احتساب الحصة: " + "؛ ".join(publication_errors))
        return "PUBLICATION_APPROVAL_REQUIRED"
    try:
        retry_limit = int(retries)
    except (TypeError, ValueError, OverflowError):
        if log_cb:
            log_cb("عدد محاولات الرفع غير صالح؛ لم يبدأ طلب YouTube.")
        return UPLOAD_NOT_STARTED
    if not 1 <= retry_limit <= 10:
        if log_cb:
            log_cb("عدد محاولات الرفع يجب أن يكون بين 1 و10؛ لم يبدأ طلب YouTube.")
        return UPLOAD_NOT_STARTED
    raw_title = video_data.get("title")
    if not isinstance(raw_title, str):
        if log_cb:
            log_cb("أُوقف الرفع قبل الإرسال لأن العنوان ليس نصاً صالحاً.")
        return "INVALID_TITLE"
    raw_title = raw_title.strip()
    if len(raw_title) > YOUTUBE_TITLE_MAX and (publication_approved or content_review_required):
        if log_cb:
            log_cb("أُوقف الرفع: العنوان الذي تمت مراجعته يتجاوز حد YouTube؛ اختصره وأعد مراجعته.")
        return "INVALID_TITLE"
    upload_title = normalize_youtube_title(raw_title)
    if not upload_title:
        if log_cb:
            log_cb("أُوقف الرفع قبل إرسال الطلب لأن العنوان فارغ بعد تنظيفه.")
        return "INVALID_TITLE"
    if upload_title != raw_title and (publication_approved or content_review_required):
        if log_cb:
            log_cb("تغيّر العنوان عند التنظيف؛ يلزم عرضه للمراجعة مجدداً قبل الرفع.")
        return "INVALID_TITLE"
    if upload_title != raw_title:
        logging.info("نُظّف عنوان الرفع أو قُصّ إلى حد YouTube المسموح.")
    video_data["title"] = upload_title
    description = video_data.get("desc", "")
    if not isinstance(description, str):
        if log_cb:
            log_cb("أُوقف الرفع قبل الإرسال لأن الوصف ليس نصاً صالحاً.")
        return "INVALID_DESCRIPTION"
    if len(description) > YOUTUBE_DESCRIPTION_MAX:
        if log_cb:
            log_cb(f"أُوقف الرفع: الوصف يتجاوز {YOUTUBE_DESCRIPTION_MAX} حرفاً؛ اختصره وأعد مراجعته.")
        return "INVALID_DESCRIPTION"
    video_data["desc"] = description
    effective_tags = normalize_youtube_tags(tags or ["Facebook", "Reel"], log_cb)
    if content_review_required:
        caption = build_review_caption(video_data.get("desc", ""), effective_tags)
        allowed, status = validate_content_review(
            video_data.get("content_safety_review"),
            video_data.get("file", ""),
            video_data.get("title", ""),
            caption,
        )
        if not allowed:
            if log_cb:
                log_cb(f"أوقفت بوابة سياسة المحتوى الرفع قبل احتساب الحصة: {status}.")
            return "CONTENT_SAFETY_BLOCKED"
    body = {
        "snippet": {
            "title": upload_title,
            "description": video_data["desc"],
            "tags": effective_tags,
            "categoryId": category,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    if sched_time:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = sched_time.astimezone(timezone.utc).isoformat()

    file_path = video_data.get("file") if isinstance(video_data, dict) else None
    if not isinstance(file_path, str) or not file_path or not os.path.isfile(file_path):
        message = "ملف الفيديو غير موجود أو غير صالح؛ لم يبدأ طلب YouTube."
        logging.error(message)
        if log_cb:
            log_cb(message)
        return UPLOAD_NOT_STARTED

    def wait_or_stop(seconds):
        if stop_event is None:
            time.sleep(seconds)
            return False
        stopped = stop_event.wait(seconds)
        if stopped and log_cb:
            log_cb("أُوقف الرفع بناءً على طلب المستخدم.")
        return stopped

    for attempt in range(1, retry_limit + 1):
        if stop_event is not None and stop_event.is_set():
            return UPLOAD_NOT_STARTED
        if quota_manager is not None and not quota_manager.can_afford():
            if log_cb:
                log_cb("وصل حد الرفع اليومي لهذا المشروع.")
            return "QUOTA_ERROR"
        try:
            media = MediaFileUpload(video_data["file"], chunksize=1024 * 1024, resumable=True)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        except HttpError as error:
            kind = classify_upload_error(error)
            if kind == "channel_limit":
                if log_cb:
                    log_cb("وصلت القناة إلى حدّ الرفع اليومي في YouTube.")
                return "CHANNEL_LIMIT_ERROR"
            if kind == "quota":
                if log_cb:
                    log_cb("نفدت حصة مشروع YouTube API.")
                return "QUOTA_ERROR"
            if kind in {"auth", "fatal"}:
                if log_cb:
                    log_cb(f"رفض YouTube طلب الرفع: {str(error)[:200]}")
                return UPLOAD_REJECTED
            if attempt < retry_limit:
                delay = min(_backoff_seconds(attempt), 30)
                if log_cb:
                    log_cb(f"تعذّر تجهيز طلب الرفع؛ إعادة المحاولة {attempt}/{retries} بعد {int(delay)} ثانية.")
                if wait_or_stop(delay):
                    return UPLOAD_NOT_STARTED
                continue
            if log_cb:
                log_cb("تعذّر تجهيز طلب الرفع؛ لم يُرسل الفيديو.")
            return UPLOAD_NOT_STARTED
        except Exception as error:
            logging.exception("تعذّر تجهيز طلب YouTube")
            if attempt < retry_limit:
                delay = min(_backoff_seconds(attempt), 30)
                if log_cb:
                    log_cb(f"تعذّر تجهيز طلب الرفع؛ إعادة المحاولة {attempt}/{retries} بعد {int(delay)} ثانية.")
                if wait_or_stop(delay):
                    return UPLOAD_NOT_STARTED
                continue
            if log_cb:
                log_cb(f"تعذّر تجهيز طلب الرفع: {error}")
            return UPLOAD_NOT_STARTED

        try:
            if before_upload is not None:
                before_upload()
            if quota_manager is not None:
                quota_manager.register()
        except Exception as error:
            logging.exception("تعذّر حفظ حالة الرفع قبل الاتصال")
            if log_cb:
                log_cb(f"لم يبدأ الرفع؛ تعذر حفظ سجل العملية قبل الاتصال: {error}")
            return UPLOAD_NOT_STARTED

        response = None
        chunk_failures = 0
        idle_chunks = 0
        last_pct = 0
        upload_request_started = False
        while response is None:
            if stop_event is not None and stop_event.is_set():
                if log_cb:
                    if upload_request_started:
                        log_cb("توقف الرفع بعد بدء جلسة YouTube؛ النتيجة غير مؤكدة، تحقق من YouTube Studio قبل إعادة المحاولة.")
                    else:
                        log_cb("أُوقف الرفع قبل إرسال أول جزء إلى YouTube؛ سيبقى آمناً لإعادة المحاولة.")
                return UPLOAD_RESULT_UNKNOWN if upload_request_started else UPLOAD_NOT_STARTED
            try:
                upload_request_started = True
                status, response = request.next_chunk()
            except HttpError as error:
                kind = classify_upload_error(error)
                if kind == "channel_limit":
                    if log_cb:
                        log_cb("وصلت القناة إلى حدّ الرفع اليومي في YouTube.")
                    return "CHANNEL_LIMIT_ERROR"
                if kind == "quota":
                    if log_cb:
                        log_cb("نفدت حصة مشروع YouTube API.")
                    return "QUOTA_ERROR"
                if kind in {"auth", "fatal"}:
                    if log_cb:
                        log_cb(f"رفض YouTube طلب الرفع: {str(error)[:200]}")
                    return UPLOAD_REJECTED
                chunk_failures += 1
                if chunk_failures > MAX_CHUNK_RETRIES:
                    if log_cb:
                        log_cb("تعذّر تأكيد نتيجة الرفع بعد استئناف الجلسة؛ أوقفت إعادة المحاولة لمنع رفع نسخة مكررة. تحقق من YouTube Studio.")
                    return UPLOAD_RESULT_UNKNOWN
                delay = min(_backoff_seconds(chunk_failures), 30)
                if log_cb:
                    log_cb(f"انقطع الاتصال أثناء الرفع ({_http_status(error)})؛ أستأنف الجلسة نفسها من {last_pct}% بعد {int(delay)} ثانية.")
                if wait_or_stop(delay):
                    return UPLOAD_RESULT_UNKNOWN
                continue
            except Exception:
                chunk_failures += 1
                logging.warning("انقطع طلب الرفع؛ ستُستأنف الجلسة نفسها (%s/%s)", chunk_failures, MAX_CHUNK_RETRIES, exc_info=True)
                if chunk_failures > MAX_CHUNK_RETRIES:
                    if log_cb:
                        log_cb("تعذّر تأكيد نتيجة الرفع بعد استئناف الجلسة؛ أوقفت إعادة المحاولة لمنع رفع نسخة مكررة. تحقق من YouTube Studio.")
                    return UPLOAD_RESULT_UNKNOWN
                delay = min(_backoff_seconds(chunk_failures), 30)
                if log_cb:
                    log_cb(f"انقطع الاتصال أثناء الرفع؛ أستأنف الجلسة نفسها من {last_pct}% بعد {int(delay)} ثانية.")
                if wait_or_stop(delay):
                    return UPLOAD_RESULT_UNKNOWN
                continue
            if response is None and stop_event is not None and stop_event.is_set():
                if log_cb:
                    log_cb("توقف الرفع بعد بدء جلسة YouTube؛ النتيجة غير مؤكدة، تحقق من YouTube Studio قبل إعادة المحاولة.")
                return UPLOAD_RESULT_UNKNOWN
            if status:
                idle_chunks = 0
                last_pct = int(status.progress() * 100)
                if log_cb:
                    log_cb(f"جاري الرفع: {last_pct}%", replace_last=True)
            elif response is None:
                idle_chunks += 1
                if idle_chunks > 3:
                    if log_cb:
                        log_cb("لم تتقدم جلسة الرفع؛ النتيجة غير مؤكدة وتم إيقافها لمنع التكرار. تحقق من YouTube Studio.")
                    return UPLOAD_RESULT_UNKNOWN
                if wait_or_stop(1):
                    return UPLOAD_RESULT_UNKNOWN

        raw_video_id = response.get("id") if isinstance(response, dict) else None
        video_id = extract_youtube_video_id(str(raw_video_id or ""))
        if video_id is None or video_id != str(raw_video_id or ""):
            if log_cb:
                log_cb("أنهى YouTube الطلب دون إرجاع معرّف فيديو صالح من 11 حرفاً؛ النتيجة غير مؤكدة. تحقق من YouTube Studio قبل أي إعادة محاولة.")
            return UPLOAD_RESULT_UNKNOWN
        if on_confirmed is not None:
            try:
                on_confirmed(video_id)
            except Exception as error:
                logging.exception("YouTube confirmed upload, but local receipt persistence failed")
                if log_cb:
                    log_cb(f"أكد YouTube الرفع بالمعرّف {video_id}، لكن تعذر حفظ إيصال العملية محلياً؛ لن أكرر الرفع تلقائياً.")
                raise RuntimeError(
                    f"YouTube confirmed upload {video_id}, but local receipt persistence failed."
                ) from error

        if log_cb:
            log_cb(f"تم الرفع بنجاح. ID: {video_id}")
        if playlist_id:
            try:
                _insert_to_playlist(youtube, playlist_id, video_id, log_cb)
            except Exception as error:
                logging.exception("رُفع الفيديو لكن تعذّرت إضافة قائمة التشغيل")
                if log_cb:
                    log_cb(f"رُفع الفيديو؛ تعذّرت إضافة قائمة التشغيل: {error}")
        try:
            verify_upload(youtube, video_id, body["status"]["privacyStatus"], sched_time, log_cb)
        except Exception as error:
            logging.exception("رُفع الفيديو لكن تعذّر التحقق من حالته")
            if log_cb:
                log_cb(f"رُفع الفيديو؛ تعذّر التحقق من الحالة: {error}")
        return video_id
    return UPLOAD_NOT_STARTED


# ════════════════════════════════════════════════════
# UI COMPONENTS
# ════════════════════════════════════════════════════
class Btn(tk.Button):
    def __init__(self, master, text, command, color=ACCENT, **kwargs):
        super().__init__(master, text=text, command=command, bg=color, fg="white",
                         font=FB, relief="flat", padx=15, pady=6, activebackground=color,
                         activeforeground="white", cursor="hand2", **kwargs)
        self.default_bg = color
        self.bind("<Enter>", lambda e: self.config(bg=self._lighten(color)))
        self.bind("<Leave>", lambda e: self.config(bg=color))

    def set_color(self, color):
        self.default_bg = color
        self.config(bg=color)

    def _lighten(self, hex_color):
        h = hex_color.lstrip('#')
        rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        return '#%02x%02x%02x' % tuple(min(255, c + 30) for c in rgb)

class PEntry(tk.Entry):
    def __init__(self, master, ph="", **kwargs):
        super().__init__(master, bg=INP, fg=TXT, insertbackground=ACCENT, relief="flat",
                         font=FB, highlightthickness=1, highlightbackground=BORDER, **kwargs)
        self.ph = ph
        self._on = False
        if ph:
            self._put()
            self.bind("<FocusIn>", self._clear)
            self.bind("<FocusOut>", self._put)

    def _put(self, _=None):
        if not self.get():
            self.insert(0, self.ph)
            self.config(fg=TXT2)
            self._on = True

    def _clear(self, _=None):
        if self._on:
            self.delete(0, "end")
            self.config(fg=TXT)
            self._on = False

    def val(self):
        return "" if self._on else self.get()

def sec(master, text, icon=""):
    f = tk.Frame(master, bg=master["bg"])
    f.pack(fill="x", pady=(12, 6))
    tk.Label(f, text=text, bg=master["bg"], fg=ACCENT, font=FS).pack(side="left")
    tk.Frame(f, bg=BORDER, height=1).pack(side="left", fill="x", expand=True, padx=(10, 0))

def mk_card(master):
    return tk.Frame(master, bg=CARD, highlightthickness=1, highlightbackground=BORDER)

class StatsCard:
    def __init__(self, master):
        self.f = tk.Frame(master, bg=BG)
        self.f.pack(fill="x", pady=10)
        self.vars = {
            "total_uploaded":   tk.StringVar(value="0"),
            "total_failed":     tk.StringVar(value="0"),
            "today_uploaded":   tk.StringVar(value="0"),
            "disk_free":        tk.StringVar(value="0 GB"),
        }
        cols = [("مرفوع الإجمالي", "total_uploaded", SUCCESS),
                ("مرفوع اليوم", "today_uploaded", ACCENT),
                ("فشل الإجمالي", "total_failed", ERR),
                ("مساحة القرص", "disk_free", WARN)]
        for t, v, c in cols:
            cf = mk_card(self.f)
            cf.pack(side="left", fill="both", expand=True, padx=5)
            tk.Label(cf, text=t, bg=CARD, fg=TXT2, font=FSM).pack(pady=(10, 2))
            tk.Label(cf, textvariable=self.vars[v], bg=CARD, fg=c,
                     font=("Segoe UI Semibold", 16)).pack(pady=(0, 10))
        self.refresh()

    def refresh(self, s=None):
        if s is None:
            s = load_stats()
        daily = s.get("daily", {})
        today = daily.get("uploaded", 0) if daily.get("date") == pt_now().date().isoformat() else 0
        self.vars["total_uploaded"].set(str(s.get("total_uploaded", 0)))
        self.vars["total_failed"].set(str(s.get("total_failed", 0)))
        self.vars["today_uploaded"].set(str(today))
        self.vars["disk_free"].set(f"{disk_free_gb()} GB")

def wait_for_review_decision(ui_queue, stop_event, payload, poll_interval=0.2):
    request = {"payload": payload, "decision": {}, "ready": threading.Event()}
    ui_queue.put(("review_request", request))
    while not request["ready"].wait(poll_interval):
        if stop_event.is_set():
            request["decision"].update({"action": "cancel"})
            request["ready"].set()
            break
    return request["decision"]


def automatic_rights_preflight_errors(settings):
    return validate_automatic_rights_settings(settings)


def cli_rights_preflight_errors(settings):
    """Explain that headless CLI cannot perform the GUI's per-video review."""
    if not isinstance(settings, dict):
        return ["إعدادات بوابة الحقوق غير صالحة."]
    if settings.get("dry_run"):
        return []
    if str(settings.get("rights_gate_mode") or "strict").strip().lower() == "strict":
        return [
            "وضع --cli لا يعرض نافذة مراجعة لكل فيديو؛ RIGHTS_GATE_MODE=strict يمنع الرفع الآلي. "
            "استخدم الواجهة الرسومية للموافقة اليدوية، أو operator_confirmed بإقرار صادق ومكتمل فقط."
        ]
    return automatic_rights_preflight_errors(settings)


def release_rights_holds_for_auto_mode(items, settings):
    if not isinstance(settings, dict):
        return list(items or []), 0
    if settings.get("dry_run") or settings.get("review_before_upload"):
        return list(items or []), 0
    if automatic_rights_preflight_errors(settings):
        return list(items or []), 0
    return release_rights_holds(items)


# ════════════════════════════════════════════════════
# MAIN APP (V4.0 PRO — WATCH MODE)
# ════════════════════════════════════════════════════
class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"FB → YouTube Uploader Pro — v{__version__}")
        self.root.configure(bg=BG)
        self._setup_window()

        self._stop   = threading.Event()
        self._pause  = threading.Event(); self._pause.set()
        self._thread = None
        self._logq   = queue.Queue()
        self._uiq    = queue.Queue()
        self._last_log_replace = False
        self._watching = False
        self._pending_review = None
        self._closing = False

        self._preflight_running = False
        self._facebook_probe_running = False
        self._facebook_probe_thread = None
        self._facebook_network_test_running = False
        self._diagnostic_report_running = False
        self.accounts = load_accounts()
        self.stats    = load_stats()
        self.pages    = load_pages()
        self.page_status = load_page_status()

        # تخطيط grid (صف 0 رأس، صف 1 نوتبوك يتمدد، صف 2-3 قضيب الأزرار):
        # grid يضمن ظهور قضيب الأزرار مهما كان ترتيب البناء،
        # والترتيب (نوتبوك قبل القضيب) يمنع انهياراً متقطعاً في Tcl لإنشاء ttk.Treeview.
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._apply_styles()
        self._build_header()
        self._build_nb()
        self._build_footer()
        self._load_ui_settings()
        self._poll()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.autostart_var.get():
            self.root.after(1500, self._autostart_watch)

    def _setup_window(self):
        """يضبط حجم النافذة لتناسب الشاشة فعلياً.

        كان الحجم مثبّتاً 1100x880، فيختفي شريط البدء/الإيقاف على أي شاشة أقصر من 880px
        (لابتوبات 768px) rغم أن التخطيط grid — لأن الطلب أكبر من الشاشة نفسها.
        """
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1100, max(880, screen_w - 60))
        height = min(880, max(560, screen_h - 90))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(840, 540)
        self.root.resizable(True, True)
        logging.info("نافذة %sx%s على شاشة %sx%s", width, height, screen_w, screen_h)

    def _autostart_watch(self):
        if self._closing or self._watching or (self._thread and self._thread.is_alive()):
            return
        self._log("بدء تلقائي للمراقبة (مفعّل في الإعدادات)…")
        self._do_start(True)

    # ─────────── حفظ واسترجاع إعدادات الواجهة ───────────
    def _collect_ui_values(self):
        """يجمع كل قيم الواجهة الحالية في قاموس (القسم، المفتاح) → قيمة."""
        b = lambda var: "true" if var.get() else "false"
        g = lambda w: w.get()
        return {
            ("SETTINGS", "VIDEO_DIRECTORY"): self.video_directory_e.get().strip(),
            ("SETTINGS", "CLIENT_SECRETS_FILE"): self.client_secrets_e.get().strip(),
            ("SETTINGS", "ACCOUNT"): self.acc_var.get(),
            ("SETTINGS", "PRIVACY"): self.priv_var.get(),
            ("SETTINGS", "HEADLESS"): b(self.headless_var),
            ("SETTINGS", "DELETE_AFTER_UPLOAD"): b(self.del_after_var),
            ("SETTINGS", "REMOVE_TAGS"): b(self.remove_tags_var),
            ("SETTINGS", "BETWEEN_VIDEOS"): g(self.delay_e),
            ("SETTINGS", "DAILY_UPLOAD_LIMIT"): g(self.quota_e),
            ("SETTINGS", "MAX_ATTEMPTS"): g(self.max_att_e),
            ("SETTINGS", "MIN_DISK_GB"): g(self.disk_e),
            ("SETTINGS", "DRY_RUN"): b(self.dry_run_var),
            ("WATCH", "INTERVAL_MINUTES"): g(self.interval_e),
            ("WATCH", "FAST_SCRAPE"): b(self.fast_scrape_var),
            ("WATCH", "AUTO_START"): b(self.autostart_var),
            ("STUDIO", "TITLE_PREFIX"): g(self.title_prefix_e),
            ("STUDIO", "TITLE_SUFFIX"): g(self.title_suffix_e),
            ("STUDIO", "CATEGORY"): YT_CATEGORIES.get(self.cat_var.get(), "25"),
            ("STUDIO", "TITLE_OPTIMIZE"): b(self.title_opt_var),
            ("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE"): b(self.omit_source_page_name_var),
            ("STUDIO", "AUTO_SHORTS"): b(self.auto_shorts_var),
            ("STUDIO", "AUTO_HASHTAGS"): b(self.auto_tags_var),
            ("STUDIO", "DEDUPE_CONTENT"): b(self.dedupe_var),
            ("STUDIO", "CREDIT_LINE"): g(self.credit_e),
            ("STUDIO", "EXTRA_DESCRIPTION"): self.desc_e.get("1.0", "end").strip(),
            ("STUDIO", "TAGS"): g(self.tags_e),
            ("STUDIO", "LOGO_ENABLED"): b(self.logo_enabled_var),
            ("STUDIO", "LOGO_PATH"): self.logo_path_var.get(),
            ("STUDIO", "LOGO_POSITION"): self.logo_pos_var.get(),
            ("STUDIO", "LOGO_SCALE"): g(self.logo_scale_e),
            ("STUDIO", "LOGO_OPACITY"): g(self.logo_op_e),
            ("STUDIO", "LOGO_COVER_WIDTH"): g(self.logo_cover_width_e),
            ("STUDIO", "LOGO_COVER_OLD"): b(self.cover_old_var),
            ("STUDIO", "LOGO_PLATE"): b(self.plate_var),
            ("STUDIO", "LOGO_SECONDARY"): b(self.secondary_var),
            ("STUDIO", "LOGO_SECONDARY_COVER_OLD"): b(self.secondary_cover_var),
            ("STUDIO", "PLAYLIST_ID"): g(self.playlist_e),
            ("STUDIO", "REVIEW_BEFORE_UPLOAD"): b(self.review_before_upload_var),
            ("STUDIO", "RIGHTS_AUTO_DRAFT"): b(self.rights_auto_draft_var),
            ("STUDIO", "RIGHTS_GATE_MODE"): self.rights_gate_var.get(),
            ("STUDIO", "RIGHTS_OPERATOR_BASIS"): self.rights_basis_var.get(),
            ("STUDIO", "RIGHTS_OPERATOR_STATEMENT"): self.rights_statement_e.get().strip(),
            ("STUDIO", "RIGHTS_OPERATOR_EVIDENCE"): self.rights_evidence_e.get().strip(),
            ("STUDIO", "THUMBNAIL_ENABLED"): b(self.thumbnail_var),
            ("STUDIO", "SHORTS_CUT"): b(self.shorts_cut_var),
            ("STUDIO", "MIN_DURATION"): g(self.min_dur_e),
            ("STUDIO", "MAX_DURATION"): g(self.max_dur_e),
            ("STUDIO", "SCHEDULE"): b(self.sched_var),
            ("STUDIO", "SCHEDULE_MIN"): g(self.min_d),
            ("STUDIO", "SCHEDULE_MAX"): g(self.max_d),
            ("STUDIO", "COOKIES_FILE"): self.cookies_path_var.get(),
            ("STUDIO", "COOKIES_ENABLED"): b(self.cookies_enabled_var),
            ("STUDIO", "TELEGRAM_TOKEN"): g(self.tg_token_e),
            ("STUDIO", "TELEGRAM_CHAT_ID"): g(self.tg_chat_e),
            ("STUDIO", "TELEGRAM_ERRORS"): b(self.tg_errors_var),
            ("STUDIO", "PG_SCROLL"): g(self.pg_scroll),
            ("STUDIO", "PG_LIMIT"): g(self.pg_limit),
            ("AI", "ENABLED"): b(self.ai_enabled_var),
            ("AI", "MODEL"): self.ai_model_var.get(),
            ("AI", "STYLE"): self.ai_style_var.get(),
            ("SAFETY", "ENABLED"): b(self.content_review_enabled_var),
            ("SAFETY", "ALLOW_PUBLIC_AUTOMATION"): b(self.allow_public_automation_var),
            ("SAFETY", "CUTTER_ROOT"): self.cutter_root_var.get().strip(),
            ("SAFETY", "CUTTER_PYTHON"): self.cutter_python_var.get().strip(),
        }

    def _save_ui_settings(self):
        try:
            saved = save_ui_values(
                self._collect_ui_values(), os.path.join(APP_DIRECTORY, "config.ini")
            )
            if not saved:
                self._log("تعذر حفظ الإعدادات في config.ini؛ لم يبدأ التشغيل.")
                return False
            self._log("حُفظت الإعدادات المحلية.")
            return True
        except Exception as error:
            safe_error = redact_sensitive_text(error)
            self._log(f"تعذر حفظ الإعدادات: {safe_error}")
            if not getattr(self, "_closing", False):
                try:
                    messagebox.showerror("تعذر حفظ الإعدادات", safe_error, parent=self.root)
                except Exception:
                    pass
            return False

    def _save_facebook_cookie_settings(self):
        proxy_text = self.facebook_proxy_var.get().strip()
        try:
            proxy = _selenium_proxy_server(proxy_text) if proxy_text else ""
            timeout = int(self.facebook_network_timeout_var.get())
            if not 10 <= timeout <= 15:
                raise ValueError("اختر مهلة بين 10 و15 ثانية لكل مسار.")
        except (AttributeError, ValueError) as error:
            self._log(f"إعداد شبكة Facebook غير صالح: {error}")
            messagebox.showerror("إعداد شبكة غير صالح", str(error), parent=self.root)
            return
        values = {
            ("STUDIO", "COOKIES_ENABLED"): str(bool(self.cookies_enabled_var.get())).lower(),
            ("STUDIO", "COOKIES_FILE"): self.cookies_path_var.get().strip(),
            ("WATCH", "PROXY"): proxy,
            ("WATCH", "NETWORK_PREFLIGHT_TIMEOUT"): str(timeout),
        }
        try:
            saved = save_ui_values(values, os.path.join(APP_DIRECTORY, "config.ini"))
        except Exception as error:
            self._log(f"تعذر حفظ إعدادات Facebook: {error}")
            return
        if saved:
            if not config.has_section("WATCH"):
                config.add_section("WATCH")
            config.set("WATCH", "PROXY", proxy)
            config.set("WATCH", "NETWORK_PREFLIGHT_TIMEOUT", str(timeout))
            self.facebook_proxy_var.set(proxy)
            self._log("حُفظت إعدادات Facebook والشبكة؛ سيستخدمها الاختبار والدورة التالية.")
        else:
            self._log("تعذر حفظ إعدادات Facebook في config.ini.")

    def _test_facebook_network(self):
        if self._facebook_network_test_running:
            return
        try:
            proxy_text = self.facebook_proxy_var.get().strip()
            proxy = _selenium_proxy_server(proxy_text) if proxy_text else ""
            timeout = int(self.facebook_network_timeout_var.get())
            if not 10 <= timeout <= 15:
                raise ValueError("اختر مهلة بين 10 و15 ثانية لكل مسار.")
            force_ipv4 = config.getboolean("WATCH", "FORCE_IPV4", fallback=True)
        except (ValueError, configparser.Error) as error:
            messagebox.showerror("إعداد شبكة غير صالح", str(error), parent=self.root)
            return
        self._facebook_network_test_running = True
        self.facebook_network_test_button.config(state="disabled")
        self.facebook_network_status_var.set("جارٍ اختبار اتصال TCP بفيسبوك…")

        def worker():
            try:
                route = facebook_network_route(proxy, force_ipv4, timeout=timeout)
            except Exception:
                logging.exception("Facebook network test failed unexpectedly")
                route = None
            self._uiq.put(("facebook_network_test_finished", {
                "route": route,
                "timeout": timeout,
                "proxy": bool(proxy),
            }))

        threading.Thread(target=worker, daemon=True, name="facebook-network-test").start()

    def _apply_ui_values(self, values):
        def text(w, v):
            try:
                w.delete(0, "end")
                if hasattr(w, "_on"):
                    w._on = False
                w.config(fg=TXT)
                w.insert(0, v)
            except Exception:
                pass

        def choice(var, v, options):
            if v in options:
                var.set(v)

        def onoff(var, v):
            var.set(str(v).lower() in ("1", "true", "yes"))

        for (section, key), v in values.items():
            try:
                if (section, key) == ("SETTINGS", "ACCOUNT"):
                    if v in self.acc_combo["values"]:
                        self.acc_var.set(v)
                elif (section, key) == ("SETTINGS", "VIDEO_DIRECTORY"):
                    text(self.video_directory_e, v)
                elif (section, key) == ("SETTINGS", "CLIENT_SECRETS_FILE"):
                    text(self.client_secrets_e, v)
                elif (section, key) == ("SETTINGS", "PRIVACY"):
                    if v in ("public", "private", "unlisted"):
                        self.priv_var.set(v)
                elif (section, key) == ("SETTINGS", "HEADLESS"):
                    onoff(self.headless_var, v)
                elif (section, key) == ("SETTINGS", "DELETE_AFTER_UPLOAD"):
                    onoff(self.del_after_var, v)
                elif (section, key) == ("SETTINGS", "REMOVE_TAGS"):
                    onoff(self.remove_tags_var, v)
                elif (section, key) == ("SETTINGS", "BETWEEN_VIDEOS"):
                    text(self.delay_e, v)
                elif (section, key) == ("SETTINGS", "DAILY_UPLOAD_LIMIT"):
                    text(self.quota_e, v)
                elif (section, key) == ("SETTINGS", "MAX_ATTEMPTS"):
                    text(self.max_att_e, v)
                elif (section, key) == ("SETTINGS", "MIN_DISK_GB"):
                    text(self.disk_e, v)
                elif (section, key) == ("SETTINGS", "DRY_RUN"):
                    onoff(self.dry_run_var, v)
                elif (section, key) == ("WATCH", "INTERVAL_MINUTES"):
                    text(self.interval_e, v)
                elif (section, key) == ("WATCH", "FAST_SCRAPE"):
                    onoff(self.fast_scrape_var, v)
                elif (section, key) == ("WATCH", "AUTO_START"):
                    onoff(self.autostart_var, v)
                elif (section, key) == ("STUDIO", "TITLE_PREFIX"):
                    text(self.title_prefix_e, v)
                elif (section, key) == ("STUDIO", "TITLE_SUFFIX"):
                    text(self.title_suffix_e, v)
                elif (section, key) == ("STUDIO", "CATEGORY"):
                    labels = list(YT_CATEGORIES.keys())
                    label = v if v in labels else next(
                        (k for k, i in YT_CATEGORIES.items() if i == v), labels[0])
                    self.cat_var.set(label)
                elif (section, key) == ("STUDIO", "TITLE_OPTIMIZE"):
                    onoff(self.title_opt_var, v)
                elif (section, key) == ("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE"):
                    onoff(self.omit_source_page_name_var, v)
                elif (section, key) == ("STUDIO", "AUTO_SHORTS"):
                    onoff(self.auto_shorts_var, v)
                elif (section, key) == ("STUDIO", "AUTO_HASHTAGS"):
                    onoff(self.auto_tags_var, v)
                elif (section, key) == ("STUDIO", "DEDUPE_CONTENT"):
                    onoff(self.dedupe_var, v)
                elif (section, key) == ("STUDIO", "CREDIT_LINE"):
                    text(self.credit_e, v)
                elif (section, key) == ("STUDIO", "EXTRA_DESCRIPTION"):
                    self.desc_e.delete("1.0", "end")
                    self.desc_e.insert("1.0", v)
                elif (section, key) == ("STUDIO", "TAGS"):
                    text(self.tags_e, v)
                elif (section, key) == ("STUDIO", "LOGO_ENABLED"):
                    onoff(self.logo_enabled_var, v)
                elif (section, key) == ("STUDIO", "LOGO_PATH"):
                    self.logo_path_var.set(_app_path(v) if v.strip() else "")
                elif (section, key) == ("STUDIO", "LOGO_POSITION"):
                    choice(self.logo_pos_var, v, list(LOGO_POSITIONS.keys()))
                elif (section, key) == ("STUDIO", "LOGO_SCALE"):
                    text(self.logo_scale_e, v)
                elif (section, key) == ("STUDIO", "LOGO_OPACITY"):
                    text(self.logo_op_e, v)
                elif (section, key) == ("STUDIO", "LOGO_COVER_WIDTH"):
                    text(self.logo_cover_width_e, v)
                elif (section, key) == ("STUDIO", "LOGO_COVER_OLD"):
                    onoff(self.cover_old_var, v)
                elif (section, key) == ("STUDIO", "LOGO_PLATE"):
                    onoff(self.plate_var, v)
                elif (section, key) == ("STUDIO", "LOGO_SECONDARY"):
                    onoff(self.secondary_var, v)
                elif (section, key) == ("STUDIO", "LOGO_SECONDARY_COVER_OLD"):
                    onoff(self.secondary_cover_var, v)
                elif (section, key) == ("STUDIO", "PLAYLIST_ID"):
                    text(self.playlist_e, v)
                elif (section, key) == ("STUDIO", "REVIEW_BEFORE_UPLOAD"):
                    onoff(self.review_before_upload_var, v)
                elif (section, key) == ("STUDIO", "RIGHTS_AUTO_DRAFT"):
                    onoff(self.rights_auto_draft_var, v)
                elif (section, key) == ("STUDIO", "RIGHTS_GATE_MODE"):
                    self.rights_gate_var.set(v if v in ("strict", "operator_confirmed") else "strict")
                elif (section, key) == ("STUDIO", "RIGHTS_OPERATOR_BASIS"):
                    self.rights_basis_var.set(v if v in RIGHTS_BASIS_LABELS else "owned")
                elif (section, key) == ("STUDIO", "RIGHTS_OPERATOR_STATEMENT"):
                    text(self.rights_statement_e, v)
                elif (section, key) == ("STUDIO", "RIGHTS_OPERATOR_EVIDENCE"):
                    text(self.rights_evidence_e, v)
                elif (section, key) == ("STUDIO", "THUMBNAIL_ENABLED"):
                    onoff(self.thumbnail_var, v)
                elif (section, key) == ("STUDIO", "SHORTS_CUT"):
                    onoff(self.shorts_cut_var, v)
                elif (section, key) == ("STUDIO", "MIN_DURATION"):
                    text(self.min_dur_e, v)
                elif (section, key) == ("STUDIO", "MAX_DURATION"):
                    text(self.max_dur_e, v)
                elif (section, key) == ("STUDIO", "SCHEDULE"):
                    onoff(self.sched_var, v)
                elif (section, key) == ("STUDIO", "SCHEDULE_MIN"):
                    text(self.min_d, v)
                elif (section, key) == ("STUDIO", "SCHEDULE_MAX"):
                    text(self.max_d, v)
                elif (section, key) == ("STUDIO", "COOKIES_FILE"):
                    self.cookies_path_var.set(v)
                elif (section, key) == ("STUDIO", "COOKIES_ENABLED"):
                    onoff(self.cookies_enabled_var, v)
                elif (section, key) == ("STUDIO", "TELEGRAM_TOKEN"):
                    text(self.tg_token_e, v)
                elif (section, key) == ("STUDIO", "TELEGRAM_CHAT_ID"):
                    text(self.tg_chat_e, v)
                elif (section, key) == ("STUDIO", "TELEGRAM_ERRORS"):
                    onoff(self.tg_errors_var, v)
                elif (section, key) == ("STUDIO", "PG_SCROLL"):
                    text(self.pg_scroll, v)
                elif (section, key) == ("STUDIO", "PG_LIMIT"):
                    text(self.pg_limit, v)
                elif (section, key) == ("AI", "ENABLED"):
                    onoff(self.ai_enabled_var, v)
                elif (section, key) == ("AI", "MODEL"):
                    choice(self.ai_model_var, v, GEMINI_MODELS)
                elif (section, key) == ("AI", "STYLE"):
                    choice(self.ai_style_var, v, list(AI_STYLES.keys()))
                elif (section, key) == ("SAFETY", "ENABLED"):
                    safety_value = str(v).strip().lower()
                    if safety_value in ("1", "true", "yes", "on"):
                        self.content_review_enabled_var.set(True)
                    elif safety_value in ("0", "false", "no", "off"):
                        self.content_review_enabled_var.set(False)
                    else:
                        self.content_review_enabled_var.set(True)
                elif (section, key) == ("SAFETY", "ALLOW_PUBLIC_AUTOMATION"):
                    onoff(self.allow_public_automation_var, v)
                elif (section, key) == ("SAFETY", "CUTTER_ROOT"):
                    self.cutter_root_var.set(v)
                elif (section, key) == ("SAFETY", "CUTTER_PYTHON"):
                    self.cutter_python_var.set(v)
            except Exception:
                continue

    def _load_ui_settings(self):
        """يعيد كل إعدادات الواجهة من config.ini — لا إعادة إدخال بعد إعادة الفتح."""
        try:
            self._apply_ui_values(load_ui_values())
        except Exception as error:
            self._log(f"تعذر تحميل الإعدادات: {error}")

    def _cancel_pending_review(self):
        request = self._pending_review
        if not request:
            return
        if not request["ready"].is_set():
            request["decision"].update({"action": "cancel"})
            request["ready"].set()
        window = request.get("window")
        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass
        self._pending_review = None

    def _on_close(self):
        if self._closing:
            return
        self._closing = True
        self._stop.set()
        self._cancel_pending_review()
        self._save_ui_settings()
        self._finish_close_when_idle()

    def _finish_close_when_idle(self):
        worker = self._thread
        probe = getattr(self, "_facebook_probe_thread", None)
        if (worker is not None and worker.is_alive()) or (probe is not None and probe.is_alive()):
            self.status_lbl.config(text="جارٍ إنهاء العمل أو فحص Facebook بأمان…", fg=WARN)
            self.root.after(100, self._finish_close_when_idle)
            return
        self.root.destroy()

    # ─────────── بناء الواجهة ───────────
    def _apply_styles(self):
        s = ttk.Style(); s.theme_use("clam")
        s.configure("TProgressbar", troughcolor=INP, background=ACCENT, borderwidth=0, thickness=12)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=CARD, foreground=TXT2, padding=[20, 10], font=FB)
        s.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.configure("Treeview", background=CARD, foreground=TXT, rowheight=32,
                    fieldbackground=CARD, font=FB, borderwidth=0)
        s.configure("Treeview.Heading", background=PANEL, foreground=ACCENT, font=FS, relief="flat")
        s.map("Treeview", background=[("selected", ACCENT)])
        s.configure("TCombobox", fieldbackground=INP, background=CARD, foreground=TXT,
                    selectbackground=ACCENT, font=FB)
        s.configure("TCheckbutton", background=BG, foreground=TXT, font=FB)

    def _build_header(self):
        h = tk.Frame(self.root, bg=PANEL, pady=16)
        h.grid(row=0, column=0, sticky="ew")
        inner = tk.Frame(h, bg=PANEL); inner.pack(padx=24)
        tk.Label(inner, text="", bg=PANEL, fg=ACCENT, font=("Segoe UI", 26)).pack(side="left", padx=(0, 15))
        tl = tk.Frame(inner, bg=PANEL); tl.pack(side="left")
        tk.Label(tl, text="FB → YouTube PRO", bg=PANEL, fg=TXT,
                 font=("Segoe UI Semibold", 18, "bold")).pack(anchor="w")
        tk.Label(tl, text=f"مراقبة + استوديو + بوابة أمان + سجل حقوق محكوم  •  v{__version__}",
                 bg=PANEL, fg=TXT2, font=FSM).pack(anchor="w")
        Btn(inner, "الإحصائيات", lambda: self.nb.select(self.t_dash), color="#303050").pack(side="right")
        tk.Frame(self.root, bg=ACCENT, height=2).grid(row=4, column=0, sticky="ew")

    def _build_nb(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.grid(row=1, column=0, sticky="nsew", padx=16, pady=(12, 0))

        self.t_watch = tk.Frame(self.nb, bg=BG)
        self.t_pages = tk.Frame(self.nb, bg=BG)
        self.t_facebook = tk.Frame(self.nb, bg=BG)
        self.t_yt    = tk.Frame(self.nb, bg=BG)
        self.t_opts  = tk.Frame(self.nb, bg=BG)
        self.t_advanced = tk.Frame(self.nb, bg=BG)
        self.t_studio = tk.Frame(self.nb, bg=BG)
        self.t_ai    = tk.Frame(self.nb, bg=BG)
        self.t_log   = tk.Frame(self.nb, bg=BG)
        self.t_accs  = tk.Frame(self.nb, bg=BG)
        self.t_dash  = tk.Frame(self.nb, bg=BG)

        self.nb.add(self.t_watch, text="  المراقبة المستمرة  ")
        self.nb.add(self.t_pages, text="  الصفحات  ")
        self.nb.add(self.t_facebook, text="  Facebook  ")
        self.nb.add(self.t_yt,    text="  يوتيوب  ")
        self.nb.add(self.t_studio, text="  الاستوديو  ")
        self.nb.add(self.t_ai,    text="  الذكاء الاصطناعي  ")
        self.nb.add(self.t_opts,  text="  خيارات متقدمة  ")
        self.nb.add(self.t_advanced, text="  المسارات المحلية  ")
        self.nb.add(self.t_log,   text="  السجل  ")
        self.nb.add(self.t_accs,  text="  الحسابات  ")
        self.nb.add(self.t_dash,  text="  لوحة التحكم  ")

        self._tab_watch()
        self._tab_pages()
        self._tab_youtube()
        self._tab_options()
        self._tab_advanced()
        self._tab_studio()
        self._tab_facebook()
        self._tab_ai()
        self._tab_log()
        self._tab_accounts()
        self._tab_dashboard()

    # ─────────── تبويب المراقبة ───────────
    def _read_autostart(self):
        try:
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read(os.path.join(APP_DIRECTORY, "config.ini"), encoding="utf-8")
            return cfg.get("WATCH", "AUTO_START", fallback="false").strip().lower() in ("1", "true", "yes")
        except (configparser.Error, OSError, UnicodeError):
            return False

    def _save_autostart(self):
        try:
            config_path = os.path.join(APP_DIRECTORY, "config.ini")
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read(config_path, encoding="utf-8")
            if not cfg.has_section("WATCH"):
                cfg.add_section("WATCH")
            cfg.set("WATCH", "AUTO_START", "true" if self.autostart_var.get() else "false")
            _write_config_atomically(cfg, config_path)
            _harden_secret_file(config_path, self._log)
            self._log("حُفظ إعداد البدء التلقائي في config.ini")
        except (configparser.Error, OSError, UnicodeError) as error:
            self._log(f"تعذر حفظ config.ini: {error}")

    def _tab_watch(self):
        out = tk.Frame(self.t_watch, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)

        sec(out, "وضع المراقبة المستمرة — لا يتوقف أبداً", "")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")

        r1 = tk.Frame(inn, bg=CARD); r1.pack(fill="x", pady=4)
        tk.Label(r1, text="الفحص كل (دقيقة):", bg=CARD, fg=TXT, width=20, anchor="e").pack(side="left")
        self.interval_e = PEntry(r1, width=8); self.interval_e.insert(0, "10")
        self.interval_e.pack(side="left", padx=10, ipady=4)
        tk.Label(r1, text="كل دورة تكشف الريلز الجديدة وترفعها فوراً، ثم تنتظر وتعيد الفحص.",
                 bg=CARD, fg=TXT2, font=FSM).pack(side="left", padx=10)

        r2 = tk.Frame(inn, bg=CARD); r2.pack(fill="x", pady=4)
        self.fast_scrape_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="السحب السريع بدون متصفح (yt-dlp أولاً، والمتصفح احتياطي تلقائي)",
                        variable=self.fast_scrape_var).pack(anchor="w")
        self.autostart_var = tk.BooleanVar(value=self._read_autostart())
        ttk.Checkbutton(inn, text="بدء المراقبة تلقائياً عند فتح البرنامج (للتشغيل الدائم مع بدء Windows)",
                        variable=self.autostart_var,
                        command=self._save_autostart).pack(anchor="w", pady=4)

        self.countdown_var = tk.StringVar(value="المراقبة متوقفة")
        tk.Label(inn, textvariable=self.countdown_var, bg=CARD, fg=INFO,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w", pady=(8, 0))

        sec(out, "حالة الصفحات المراقبة", "")
        tf = mk_card(out); tf.pack(fill="both", expand=True)
        cols = ("الصفحة", "آخر فحص", "روابط", "جديدة", "الحالة")
        self.wtree = ttk.Treeview(tf, columns=cols, show="headings", height=7, selectmode="browse")
        for cl, w in zip(cols, [300, 145, 70, 70, 260]):
            self.wtree.heading(cl, text=cl)
            self.wtree.column(cl, width=w, anchor="center")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.wtree.yview)
        self.wtree.configure(yscrollcommand=vsb.set)
        self.wtree.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        vsb.pack(side="right", fill="y")
        self._refresh_watch_tree()

    def _refresh_watch_tree(self):
        for i in self.wtree.get_children():
            self.wtree.delete(i)
        for p in self.pages:
            if not p.get("enabled", True):
                continue
            st = self.page_status.get(p["url"], {})
            self.wtree.insert("", "end", iid=p["url"], values=(
                p.get("name") or p["url"],
                st.get("time", "—"),
                st.get("found", "—"),
                st.get("new", "—"),
                st.get("status", "بانتظار أول فحص…"),
            ))

    # ─────────── تبويب الصفحات ───────────
    def _tab_pages(self):
        out = tk.Frame(self.t_pages, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)
        sec(out, "قائمة الصفحات المستهدفة (تُحفظ تلقائياً في pages.json)", "")
        tf = mk_card(out); tf.pack(fill="both", expand=True, pady=(0, 10))
        cols = ("التفعيل", "الرابط", "اسم الصفحة", "تمرير", "حد")
        self.ptree = ttk.Treeview(tf, columns=cols, show="headings", height=8, selectmode="browse")
        for cl, w in zip(cols, [45, 380, 170, 70, 70]):
            self.ptree.heading(cl, text=cl)
            self.ptree.column(cl, width=w, anchor="center")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.ptree.yview)
        self.ptree.configure(yscrollcommand=vsb.set)
        self.ptree.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        vsb.pack(side="right", fill="y")
        self.ptree.bind("<Double-1>", self._page_toggle)
        self._reload_pages_tree()

        sec(out, "إضافة صفحة جديدة", "")
        fm = mk_card(out); fm.pack(fill="x", pady=(0, 8))
        inn = tk.Frame(fm, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        r1 = tk.Frame(inn, bg=CARD); r1.pack(fill="x", pady=4)
        tk.Label(r1, text="رابط الصفحة:", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.pg_url = PEntry(r1, ph="https://www.facebook.com/...", width=60)
        self.pg_url.pack(side="left", padx=10, ipady=4)

        r2 = tk.Frame(inn, bg=CARD); r2.pack(fill="x", pady=4)
        tk.Label(r2, text="اسم الصفحة:", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.pg_name = PEntry(r2, ph="اختياري", width=25)
        self.pg_name.pack(side="left", padx=10, ipady=4)
        tk.Label(r2, text="تمرير:", bg=CARD, fg=TXT).pack(side="left")
        self.pg_scroll = PEntry(r2, width=6); self.pg_scroll.insert(0, "5")
        self.pg_scroll.pack(side="left", padx=8, ipady=4)
        tk.Label(r2, text="حد الفيديوهات:", bg=CARD, fg=TXT).pack(side="left")
        self.pg_limit = PEntry(r2, width=6); self.pg_limit.insert(0, "30")
        self.pg_limit.pack(side="left", padx=8, ipady=4)

        br = tk.Frame(inn, bg=CARD); br.pack(fill="x", pady=(10, 0))
        Btn(br, "إضافة", self._page_add, ACCENT).pack(side="left")
        Btn(br, "حذف", self._page_del, ERR).pack(side="left", padx=10)
        Btn(br, "تفعيل/تعطيل", lambda: self._page_toggle(None), "#6a5acd").pack(side="left")
        Btn(br, "استيراد CSV", self._import_pages, "#37474f").pack(side="right")

    def _reload_pages_tree(self):
        for i in self.ptree.get_children():
            self.ptree.delete(i)
        for p in self.pages:
            self.ptree.insert("", "end", values=(
                "✓" if p.get("enabled", True) else "—",
                p["url"], p.get("name", ""), p.get("scroll", 5), p.get("limit", 30)))

    def _persist_pages_from_tree(self):
        pages = []
        for iid in self.ptree.get_children():
            v = self.ptree.item(iid, "values")
            pages.append({"enabled": v[0] == "✓", "url": v[1], "name": v[2],
                          "scroll": int(v[3]), "limit": int(v[4])})
        self.pages = pages
        try:
            save_pages(pages)
        except OSError:
            self._log("تعذر حفظ pages.json")
        if hasattr(self, "facebook_probe_page_combo"):
            self._refresh_facebook_probe_pages()

    def _page_toggle(self, _event):
        sel = self.ptree.selection()
        if not sel:
            return
        v = list(self.ptree.item(sel[0], "values"))
        v[0] = "—" if v[0] == "✓" else "✓"
        self.ptree.item(sel[0], values=v)
        self._persist_pages_from_tree()
        self._refresh_watch_tree()

    def _page_add(self):
        url = self.pg_url.val().strip()
        if not url:
            return
        try:
            sc, lm = int(self.pg_scroll.get()), int(self.pg_limit.get())
            self.ptree.insert("", "end", values=("✓", url, self.pg_name.val().strip(), sc, lm))
            self.pg_url.delete(0, "end"); self.pg_url._put()
            self._persist_pages_from_tree()
            self._refresh_watch_tree()
        except ValueError:
            messagebox.showerror("خطأ", "أدخل أرقاماً صحيحة")

    def _page_del(self):
        s = self.ptree.selection()
        if s:
            self.ptree.delete(s[0])
            self._persist_pages_from_tree()
            self._refresh_watch_tree()

    def _import_pages(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("Text", "*.txt")])
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                p = [x.strip() for x in line.split(",")]
                if p and p[0].startswith("http"):
                    self.ptree.insert("", "end", values=(
                        "✓", p[0], p[1] if len(p) > 1 else "",
                        p[2] if len(p) > 2 else 5, p[3] if len(p) > 3 else 30))
        self._persist_pages_from_tree()
        self._refresh_watch_tree()

    def _get_pages(self):
        return list(self.pages)

    # ─────────── تبويب يوتيوب ───────────
    def _tab_youtube(self):
        out = tk.Frame(self.t_yt, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)
        sec(out, "إعدادات القناة", "")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        r1 = tk.Frame(inn, bg=CARD); r1.pack(fill="x", pady=4)
        tk.Label(r1, text="الحساب النشط:", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.acc_var = tk.StringVar(value=self.accounts[0] if self.accounts else "account1")
        self.acc_combo = ttk.Combobox(r1, textvariable=self.acc_var, values=self.accounts,
                                      state="readonly", width=30)
        self.acc_combo.pack(side="left", padx=10, ipady=3)

        r1b = tk.Frame(inn, bg=CARD); r1b.pack(fill="x", pady=6)
        tk.Label(r1b, text="محاولات الرفع اليومية:", bg=CARD, fg=TXT, width=22, anchor="e").pack(side="left")
        self.quota_e = PEntry(r1b, width=10); self.quota_e.insert(0, str(DEFAULT_DAILY_UPLOAD_LIMIT))
        self.quota_e.pack(side="left", padx=10, ipady=4)
        self.quota_var = tk.StringVar(value="100 طلب رفع يومياً افتراضياً لكل مشروع Google Cloud")
        tk.Label(r1b, textvariable=self.quota_var, bg=CARD, fg=TXT2, font=FSM).pack(side="left", padx=8)

        sec(out, "تفاصيل الفيديو", "")
        c2 = mk_card(out); c2.pack(fill="x")
        inn2 = tk.Frame(c2, bg=CARD, padx=14, pady=12); inn2.pack(fill="x")

        r2 = tk.Frame(inn2, bg=CARD); r2.pack(fill="x", pady=4)
        tk.Label(r2, text="الخصوصية:", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.priv_var = tk.StringVar(value="private")
        for v, t in [("public", "عام"), ("private", "خاص"), ("unlisted", "غير مدرج")]:
            ttk.Radiobutton(r2, text=t, variable=self.priv_var, value=v).pack(side="left", padx=10)

        r3 = tk.Frame(inn2, bg=CARD); r3.pack(fill="x", pady=6)
        tk.Label(r3, text="الوسوم (Tags):", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.tags_e = PEntry(r3, width=50); self.tags_e.insert(0, "Facebook,Reel,Shorts")
        self.tags_e.pack(side="left", padx=10, ipady=4)

        r3pl = tk.Frame(inn2, bg=CARD); r3pl.pack(fill="x", pady=6)
        tk.Label(r3pl, text="قائمة تشغيل (ID):", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.playlist_e = PEntry(r3pl, width=50)
        self.playlist_e.pack(side="left", padx=10, ipady=4)
        tk.Label(r3pl, text="اختياري — تُستخدم لكل الرفع ما لم يتجاوزها مفتاح playlist لكل صفحة",
                 bg=CARD, fg=TXT2, font=FSM).pack(side="left", padx=6)

        r3b = tk.Frame(inn2, bg=CARD); r3b.pack(fill="x", pady=6)
        tk.Label(r3b, text="التصنيف:", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.cat_var = tk.StringVar(value="أخبار وسياسة (25)")
        ttk.Combobox(r3b, textvariable=self.cat_var, state="readonly", width=28,
                     values=list(YT_CATEGORIES.keys())).pack(side="left", padx=10)

        tk.Label(inn2, text="وصف إضافي:", bg=CARD, fg=TXT, anchor="e").pack(anchor="w", pady=(6, 0))
        self.desc_e = tk.Text(inn2, bg=INP, fg=TXT, height=4, width=70, relief="flat",
                              highlightthickness=1, highlightbackground=BORDER)
        self.desc_e.pack(pady=5)

    # ─────────── تبويب الذكاء الاصطناعي ───────────
    def _tab_ai(self):
        out = tk.Frame(self.t_ai, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)

        sec(out, "تحسين المحتوى بـ Gemini AI", "")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        self.ai_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn, text="تفعيل التحسين بالذكاء الاصطناعي (عنوان + وصف + وسوم احترافية لكل فيديو)",
                        variable=self.ai_enabled_var).pack(anchor="w", pady=2)
        r1 = tk.Frame(inn, bg=CARD); r1.pack(fill="x", pady=6)
        tk.Label(r1, text="النموذج:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.ai_model_var = tk.StringVar(value=GEMINI_MODELS[0])
        ttk.Combobox(r1, textvariable=self.ai_model_var, state="readonly",
                     width=22, values=GEMINI_MODELS).pack(side="left", padx=8)
        tk.Label(r1, text="الأسلوب:", bg=CARD, fg=TXT).pack(side="left", padx=(14, 0))
        self.ai_style_var = tk.StringVar(value="احترافي متوازن")
        ttk.Combobox(r1, textvariable=self.ai_style_var, state="readonly",
                     width=24, values=list(AI_STYLES.keys())).pack(side="left", padx=8)
        tk.Label(inn, text="ℹيُنادى AI مرة واحدة فقط لكل فيديو (النتيجة محفوظة)، وإذا تعذّر الوصول يكمل البرنامج بالتحسين العادي تلقائياً.",
                 bg=CARD, fg=TXT2, font=FSM, wraplength=820, justify="left").pack(anchor="w", pady=(4, 0))

        sec(out, "مفاتيح API — من aistudio.google.com/apikey", "")
        c2 = mk_card(out); c2.pack(fill="both", expand=True, pady=(0, 10))
        inn2 = tk.Frame(c2, bg=CARD, padx=14, pady=12); inn2.pack(fill="both", expand=True)
        tk.Label(inn2, text="عند نفاد حصة المفتاح ينتقل البرنامج للتالي تلقائياً، ويعيد تفعيل النافد غداً.",
                 bg=CARD, fg=TXT2, font=FSM).pack(anchor="w", pady=(0, 6))

        list_frame = tk.Frame(inn2, bg=CARD); list_frame.pack(fill="both", expand=True)
        cols = ("#", "المفتاح", "الحالة")
        self.ai_tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                                    height=6, selectmode="browse")
        for cl, w in zip(cols, [40, 460, 300]):
            self.ai_tree.heading(cl, text=cl)
            self.ai_tree.column(cl, width=w, anchor="center")
        ksb = ttk.Scrollbar(list_frame, orient="vertical", command=self.ai_tree.yview)
        self.ai_tree.configure(yscrollcommand=ksb.set)
        self.ai_tree.pack(side="left", fill="both", expand=True)
        ksb.pack(side="right", fill="y")

        r2 = tk.Frame(inn2, bg=CARD); r2.pack(fill="x", pady=(10, 0))
        self.ai_key_e = PEntry(r2, ph="مفتاح API (مخفي)", width=50, show="•")
        self.ai_key_e.pack(side="left", ipady=4)
        Btn(r2, "إضافة", self._ai_key_add, ACCENT).pack(side="left", padx=8)
        Btn(r2, "حذف", self._ai_key_del, ERR).pack(side="left")
        Btn(r2, "تفعيل الكل الآن", self._ai_keys_reset, "#6a5acd").pack(side="left", padx=8)
        Btn(r2, "اختبار المفاتيح", self._ai_keys_test, "#00695c").pack(side="right")
        self._reload_ai_tree()

    def _reload_ai_tree(self, statuses=None):
        for i in self.ai_tree.get_children():
            self.ai_tree.delete(i)
        for i, entry in enumerate(load_ai_keys()):
            key = entry.get("key", "")
            masked = mask_secret(key)
            if statuses and i in statuses:
                status = statuses[i]
            elif _iso_in_past(entry.get("exhausted_until", "")):
                status = "جاهز"
            else:
                until = entry.get("exhausted_until", "")[:16].replace("T", " ")
                status = f"مستنفد حتى {until}"
            self.ai_tree.insert("", "end", iid=str(i), values=(i + 1, masked, status))

    def _ai_key_add(self):
        key = self.ai_key_e.val().strip()
        if not key:
            return
        keys = load_ai_keys()
        if any(k.get("key") == key for k in keys):
            messagebox.showinfo("مكرر", "هذا المفتاح مضاف مسبقاً")
            return
        keys.append({"key": key, "exhausted_until": "", "last_error": ""})
        save_ai_keys(keys)
        self.ai_key_e.delete(0, "end"); self.ai_key_e._put()
        self._reload_ai_tree()
        self._log(f"أُضيف مفتاح Gemini (الإجمالي: {len(keys)})")

    def _ai_key_del(self):
        sel = self.ai_tree.selection()
        if not sel:
            return
        keys = load_ai_keys()
        del keys[int(sel[0])]
        save_ai_keys(keys)
        self._reload_ai_tree()

    def _ai_keys_reset(self):
        keys = load_ai_keys()
        for k in keys:
            k["exhausted_until"] = ""
        save_ai_keys(keys)
        self._reload_ai_tree()
        self._log("أُعيد تفعيل كل المفاتيح")

    def _ai_keys_test(self):
        keys = load_ai_keys()
        if not keys:
            messagebox.showinfo("لا مفاتيح", "أضف مفتاحاً أولاً")
            return
        model = self.ai_model_var.get()
        def job():
            self._log(f"اختبار {len(keys)} مفتاح Gemini…")
            statuses = {}
            for i, entry in enumerate(keys):
                ok, msg = ai_ping_key(entry.get("key", ""), model)
                statuses[i] = msg
                if not ok and ("429" in msg or "403" in msg):
                    fresh = load_ai_keys()          # يحدّث الملف مباشرة دون فقد تعديلات
                    if i < len(fresh):
                        ai_mark_exhausted(fresh, i, msg)
                self._log(f"   مفتاح #{i+1}: {msg}")
            self.root.after(0, lambda: self._reload_ai_tree(statuses))
        threading.Thread(target=job, daemon=True).start()

    # ─────────── تبويب الخيارات ───────────
    def _tab_options(self):
        out = tk.Frame(self.t_opts, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)
        sec(out, "إعدادات التشغيل الذكي", "")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")

        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn, text="تشغيل المتصفح في الخلفية (Headless)",
                        variable=self.headless_var).pack(anchor="w", pady=4)
        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn, text="وضع تجربة: لا يرفع إلى YouTube ولا يحتسب حصة الرفع",
                        variable=self.dry_run_var).pack(anchor="w", pady=4)
        self.allow_public_automation_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            inn,
            text="السماح بالرفع العام أو المجدول تلقائياً دون معاينة لكل فيديو",
            variable=self.allow_public_automation_var,
        ).pack(anchor="w", pady=4)
        tk.Label(
            inn,
            text="اتركه مغلقاً للمراجعة اليدوية؛ عند فتحه يصبح الرفع العام والـ Shorts المجدولة آلية.",
            bg=CARD,
            fg=WARN,
            font=FSM,
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        self.del_after_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn, text="حذف الفيديو من الجهاز بعد الرفع",
                        variable=self.del_after_var).pack(anchor="w", pady=4)
        self.remove_tags_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn, text="تنظيف العنوان من الهاشتاجات الأصلية",
                        variable=self.remove_tags_var).pack(anchor="w", pady=4)

        r1 = tk.Frame(inn, bg=CARD); r1.pack(fill="x", pady=10)
        tk.Label(r1, text="تأخير بين الفيديوهات (ثواني):", bg=CARD, fg=TXT).pack(side="left")
        self.delay_e = PEntry(r1, width=8); self.delay_e.insert(0, "10")
        self.delay_e.pack(side="left", padx=10)
        tk.Label(r1, text="أقصى محاولات للفاشل:", bg=CARD, fg=TXT).pack(side="left", padx=(20, 0))
        self.max_att_e = PEntry(r1, width=6); self.max_att_e.insert(0, "3")
        self.max_att_e.pack(side="left", padx=8)
        tk.Label(r1, text="أدنى مساحة قرص (GB):", bg=CARD, fg=TXT).pack(side="left", padx=(20, 0))
        self.disk_e = PEntry(r1, width=6); self.disk_e.insert(0, "2")
        self.disk_e.pack(side="left", padx=8)

        sec(out, "الجدولة الذكية (تراكمية — لا تتداخل المواعيد)", "")
        c2 = mk_card(out); c2.pack(fill="x", pady=(0, 10))
        inn2 = tk.Frame(c2, bg=CARD, padx=14, pady=12); inn2.pack(fill="x")
        self.sched_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn2, text="تفعيل جدولة النشر على يوتيوب (لتجنب الحظر وتوزيع النشر)",
                        variable=self.sched_var).pack(anchor="w")
        r2 = tk.Frame(inn2, bg=CARD); r2.pack(fill="x", pady=8)
        tk.Label(r2, text="تأخير (دقيقة) من:", bg=CARD, fg=TXT).pack(side="left")
        self.min_d = PEntry(r2, width=6); self.min_d.insert(0, "20")
        self.min_d.pack(side="left", padx=5)
        tk.Label(r2, text="إلى:", bg=CARD, fg=TXT).pack(side="left")
        self.max_d = PEntry(r2, width=6); self.max_d.insert(0, "60")
        self.max_d.pack(side="left", padx=5)

        sec(out, "إشعارات Telegram (اختياري)", "")
        c3 = mk_card(out); c3.pack(fill="x")
        inn3 = tk.Frame(c3, bg=CARD, padx=14, pady=12); inn3.pack(fill="x")
        self.tg_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn3, text="أرسل إشعاراً لكل فيديو يُرفع بنجاح",
                        variable=self.tg_enabled_var).pack(anchor="w")
        self.tg_errors_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn3, text="إشعار الأخطاء الحرجة + ملخص يومي بصحة المراقبة",
                        variable=self.tg_errors_var).pack(anchor="w", pady=2)
        r3 = tk.Frame(inn3, bg=CARD); r3.pack(fill="x", pady=6)
        tk.Label(r3, text="Bot Token:", bg=CARD, fg=TXT, width=12, anchor="e").pack(side="left")
        self.tg_token_e = PEntry(r3, width=45, show="•"); self.tg_token_e.pack(side="left", padx=8, ipady=4)
        tk.Label(r3, text="Chat ID:", bg=CARD, fg=TXT).pack(side="left")
        self.tg_chat_e = PEntry(r3, width=15); self.tg_chat_e.pack(side="left", padx=8, ipady=4)

        sec(out, "الصيانة", "")
        c5 = mk_card(out); c5.pack(fill="x")
        inn5 = tk.Frame(c5, bg=CARD, padx=14, pady=12); inn5.pack(fill="x")
        rm = tk.Frame(inn5, bg=CARD); rm.pack(fill="x", pady=4)
        try:
            from yt_dlp.version import __version__ as _ytdlp_ver
        except Exception:
            _ytdlp_ver = "؟"
        tk.Label(rm, text=f"إصدار yt-dlp الحالي: {_ytdlp_ver}", bg=CARD, fg=TXT2,
                 font=FSM).pack(side="left")
        Btn(rm, "تحديث yt-dlp", self._update_ytdlp, "#00695c").pack(side="left", padx=15)
        tk.Label(inn5, text="فيسبوك يغيّر موقعه باستمرار — إذا توقف التحميل فجأة، حدّث yt-dlp أولاً.",
                 bg=CARD, fg=TXT2, font=FSM).pack(anchor="w", pady=(4, 0))

    def _browse_video_directory(self):
        current = self.video_directory_e.get().strip() or APP_DIRECTORY
        selected = filedialog.askdirectory(parent=self.root, initialdir=current)
        if selected:
            self.video_directory_e.delete(0, "end")
            self.video_directory_e.insert(0, selected)

    def _browse_oauth_client(self):
        current = self.client_secrets_e.get().strip() or APP_DIRECTORY
        initialdir = current if os.path.isdir(current) else os.path.dirname(current)
        selected = filedialog.askopenfilename(
            parent=self.root,
            initialdir=initialdir or APP_DIRECTORY,
            filetypes=[("Google OAuth client JSON", "*.json"), ("JSON", "*.json")],
        )
        if selected:
            self.client_secrets_e.delete(0, "end")
            self.client_secrets_e.insert(0, selected)

    def _tab_advanced(self):
        out = tk.Frame(self.t_advanced, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=16)
        sec(out, "إعدادات محلية متقدمة", "مسارات التخزين وملف عميل OAuth؛ تُحفظ في config.ini المحلي.")
        card = mk_card(out)
        card.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(card, bg=CARD, padx=18, pady=16)
        inner.pack(fill="x")

        tk.Label(inner, text="مجلد ملفات الفيديو:", bg=CARD, fg=TXT, width=22, anchor="e").grid(
            row=0, column=0, sticky="e", pady=6
        )
        self.video_directory_e = PEntry(inner, width=62)
        self.video_directory_e.grid(row=0, column=1, sticky="ew", padx=8, pady=6, ipady=4)
        Btn(inner, "اختيار مجلد…", self._browse_video_directory, "#37474f").grid(row=0, column=2, padx=4)

        tk.Label(inner, text="ملف عميل Google OAuth:", bg=CARD, fg=TXT, width=22, anchor="e").grid(
            row=1, column=0, sticky="e", pady=6
        )
        self.client_secrets_e = PEntry(inner, width=62)
        self.client_secrets_e.grid(row=1, column=1, sticky="ew", padx=8, pady=6, ipady=4)
        Btn(inner, "اختيار ملف JSON…", self._browse_oauth_client, "#37474f").grid(row=1, column=2, padx=4)
        inner.grid_columnconfigure(1, weight=1)

        tk.Label(
            inner,
            text=(
                "لا تعرض هذه اللوحة محتويات ملف OAuth أو رمز الجلسة. احفظ ملف العميل محلياً ولا تشاركه. "
                "على POSIX تُقيّد أذونات ملفات الإعداد والرموز؛ على Windows احمِ حساب المستخدم والقرص."
            ),
            bg=CARD,
            fg=WARN,
            font=FSM,
            wraplength=850,
            justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 4))
        tk.Label(
            inner,
            text="التطبيق واجهة مكتبية محلية؛ لا يفتح منفذ HTTP ولا ينشئ لوحة ويب أو خدمة عامة.",
            bg=CARD,
            fg=TXT2,
            font=FSM,
            wraplength=850,
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))
        Btn(inner, "حفظ الإعدادات المحلية", self._save_ui_settings, ACCENT).grid(
            row=4, column=0, columnspan=3, sticky="e", pady=(4, 0)
        )

    def _tab_facebook(self):
        out = tk.Frame(self.t_facebook, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=16)

        sec(out, "إعداد الوصول إلى Facebook", "")
        card = mk_card(out)
        card.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(card, bg=CARD, padx=18, pady=16)
        inner.pack(fill="x")
        tk.Label(
            inner,
            text=(
                "سجّل الدخول إلى Facebook بنفسك، وأكمل أي تحقق أمني، ثم صدّر ملف cookies.txt. "
                "هذا الإعداد لا يتجاوز تسجيل الدخول أو فحوص الأمان."
            ),
            bg=CARD,
            fg=TXT2,
            font=FSM,
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        self.cookies_enabled_var = tk.BooleanVar(value=False)
        self.cookies_path_var = tk.StringVar(value="cookies.txt")
        self.facebook_cookie_toggle = ttk.Checkbutton(
            inner,
            text="استخدام كوكيز حسابي في Facebook عند السحب والتحميل",
            variable=self.cookies_enabled_var,
        )
        self.facebook_cookie_toggle.pack(anchor="w", pady=4)

        path_row = tk.Frame(inner, bg=CARD)
        path_row.pack(fill="x", pady=8)
        tk.Label(path_row, text="ملف الكوكيز:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.facebook_cookies_entry = tk.Entry(
            path_row,
            textvariable=self.cookies_path_var,
            bg=INP,
            fg=TXT,
            relief="flat",
            font=FB,
            width=54,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.facebook_cookies_entry.pack(side="left", padx=8, ipady=5, fill="x", expand=True)
        self.facebook_pick_button = Btn(path_row, "اختيار ملف…", self._pick_cookies, "#37474f")
        self.facebook_pick_button.pack(side="left")

        tk.Label(
            inner,
            text=(
                "احفظ cookies.txt بجانب البرنامج أو اختر مكانه. الملف سرّي ويمنح الوصول إلى جلسة حسابك؛ "
                "لا تشاركه ولا ترفعه إلى أي مكان."
            ),
            bg=CARD,
            fg=WARN,
            font=FSM,
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=(2, 12))

        sec(out, "اختبار شبكة Facebook والوكيل الاختياري", "")
        network_card = mk_card(out)
        network_card.pack(fill="x", pady=(0, 12))
        network_inner = tk.Frame(network_card, bg=CARD, padx=18, pady=14)
        network_inner.pack(fill="x")
        proxy_row = tk.Frame(network_inner, bg=CARD)
        proxy_row.pack(fill="x", pady=4)
        tk.Label(proxy_row, text="الوكيل (اختياري):", bg=CARD, fg=TXT, width=17, anchor="e").pack(side="left")
        self.facebook_proxy_var = tk.StringVar(value=config.get("WATCH", "PROXY", fallback=""))
        tk.Entry(
            proxy_row,
            textvariable=self.facebook_proxy_var,
            bg=INP,
            fg=TXT,
            relief="flat",
            font=FB,
            highlightthickness=1,
            highlightbackground=BORDER,
        ).pack(side="left", padx=8, ipady=5, fill="x", expand=True)
        timeout_row = tk.Frame(network_inner, bg=CARD)
        timeout_row.pack(fill="x", pady=4)
        tk.Label(timeout_row, text="مهلة كل مسار (ثوانٍ):", bg=CARD, fg=TXT, width=17, anchor="e").pack(side="left")
        self.facebook_network_timeout_var = tk.StringVar(value=str(_facebook_network_preflight_timeout()))
        tk.Spinbox(
            timeout_row,
            from_=10,
            to=15,
            width=5,
            textvariable=self.facebook_network_timeout_var,
            bg=INP,
            fg=TXT,
            relief="flat",
            font=FB,
            highlightthickness=1,
            highlightbackground=BORDER,
        ).pack(side="left", padx=8)
        tk.Label(
            network_inner,
            text=(
                "يُمنح IPv4 وIPv6 كامل المهلة كلٌّ على حدة. استخدم فقط وكيلاً محلياً تملكه أو تثق به "
                "(http/https/socks5)؛ لا تضع اسم مستخدم أو كلمة مرور في العنوان."
            ),
            bg=CARD,
            fg=TXT2,
            font=FSM,
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=(4, 8))
        network_actions = tk.Frame(network_inner, bg=CARD)
        network_actions.pack(fill="x", pady=2)
        self.facebook_network_test_button = Btn(
            network_actions,
            "اختبار اتصال الشبكة",
            self._test_facebook_network,
            "#455a64",
        )
        self.facebook_network_test_button.pack(side="left")
        self.facebook_network_status_var = tk.StringVar(value="")
        self.facebook_network_status_label = tk.Label(
            network_actions,
            textvariable=self.facebook_network_status_var,
            bg=CARD,
            fg=TXT2,
            font=FSM,
            wraplength=740,
            justify="left",
        )
        self.facebook_network_status_label.pack(side="left", padx=12, fill="x", expand=True)

        sec(out, "حالة ملف الجلسة", "")
        status_card = mk_card(out)
        status_card.pack(fill="x", pady=(0, 12))
        status_inner = tk.Frame(status_card, bg=CARD, padx=18, pady=14)
        status_inner.pack(fill="x")
        self.facebook_cookie_status_var = tk.StringVar(value="")
        self.facebook_cookie_status_label = tk.Label(
            status_inner,
            textvariable=self.facebook_cookie_status_var,
            bg=CARD,
            fg=WARN,
            font=FB,
            wraplength=860,
            justify="left",
        )
        self.facebook_cookie_status_label.pack(anchor="w")
        tk.Label(
            status_inner,
            text="هذا فحص محلي للملف؛ لا يثبت أن Facebook سيقبل الجلسة. قد تحتاج إلى تسجيل الدخول والتحقق مجدداً.",
            bg=CARD,
            fg=TXT2,
            font=FSM,
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        actions = tk.Frame(out, bg=BG)
        actions.pack(fill="x", pady=4)
        Btn(actions, "فحص الملف", self._refresh_facebook_cookie_status, "#455a64").pack(side="left", padx=(0, 8))
        self.facebook_save_button = Btn(actions, "حفظ إعدادات Facebook", self._save_facebook_cookie_settings, "#00695c")
        self.facebook_save_button.pack(side="left")

        sec(out, "اختبار الوصول قبل بدء المراقبة", "")
        probe_card = mk_card(out)
        probe_card.pack(fill="x", pady=(10, 0))
        probe_inner = tk.Frame(probe_card, bg=CARD, padx=18, pady=14)
        probe_inner.pack(fill="x")
        tk.Label(
            probe_inner,
            text=(
                "يفتح الاختبار صفحة المصدر في Chrome من دون سحب فيديو أو رفعه. "
                "يكشف تعذر الشبكة، تعذر تسجيل الدخول أو طلب التحقق الأمني."
            ),
            bg=CARD,
            fg=TXT2,
            font=FSM,
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))
        probe_row = tk.Frame(probe_inner, bg=CARD)
        probe_row.pack(fill="x", pady=4)
        tk.Label(probe_row, text="صفحة المصدر:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.facebook_probe_page_var = tk.StringVar(value="")
        self.facebook_probe_page_combo = ttk.Combobox(
            probe_row,
            textvariable=self.facebook_probe_page_var,
            state="readonly",
            width=62,
        )
        self.facebook_probe_page_combo.pack(side="left", padx=8, fill="x", expand=True)
        self.facebook_probe_refresh_button = Btn(
            probe_row,
            "تحديث الصفحات",
            self._refresh_facebook_probe_pages,
            "#455a64",
        )
        self.facebook_probe_refresh_button.pack(side="left")
        self.facebook_probe_status_var = tk.StringVar(value="")
        self.facebook_probe_status_label = tk.Label(
            probe_inner,
            textvariable=self.facebook_probe_status_var,
            bg=CARD,
            fg=TXT2,
            font=FB,
            wraplength=860,
            justify="left",
        )
        self.facebook_probe_status_label.pack(anchor="w", pady=(8, 4))
        self.facebook_probe_button = Btn(
            probe_inner,
            "اختبار Facebook الآن",
            self._start_facebook_page_probe,
            "#1f6feb",
        )
        self.facebook_probe_button.pack(anchor="w", pady=(4, 0))

        self.cookies_enabled_var.trace_add("write", lambda *_args: self._refresh_facebook_cookie_status())
        self.cookies_path_var.trace_add("write", lambda *_args: self._refresh_facebook_cookie_status())
        self._refresh_facebook_cookie_status()
        self._refresh_facebook_probe_pages()

    def _refresh_facebook_cookie_status(self):
        if not hasattr(self, "facebook_cookie_status_var"):
            return
        status = facebook_cookie_setup_status(
            self.cookies_enabled_var.get(),
            self.cookies_path_var.get(),
        )
        self.facebook_cookie_status_var.set(status["message"])
        colors = {
            "ready": SUCCESS,
            "disabled": WARN,
            "path_missing": WARN,
            "file_missing": ERR,
            "invalid_file": ERR,
            "expired": ERR,
            "session_incomplete": WARN,
        }
        self.facebook_cookie_status_label.config(fg=colors.get(status["status"], WARN))
        return status

    def _refresh_facebook_probe_pages(self):
        if not hasattr(self, "facebook_probe_page_combo"):
            return []
        current = self.facebook_probe_page_var.get()
        pages = [page for page in self._get_pages() if page.get("enabled", True)]
        page_urls = {}
        for index, page in enumerate(pages, start=1):
            url = str(page.get("url") or "").strip()
            if not url:
                continue
            name = str(page.get("name") or "صفحة بلا اسم").strip()
            label = f"{name} — {url}"
            if label in page_urls:
                label = f"{name} ({index}) — {url}"
            page_urls[label] = url
        labels = list(page_urls)
        self._facebook_probe_page_urls = page_urls
        self.facebook_probe_page_combo.configure(values=labels)
        if current not in page_urls:
            self.facebook_probe_page_var.set(labels[0] if labels else "")
        if not labels:
            self.facebook_probe_status_var.set("لا توجد صفحات مفعّلة؛ أضف صفحة من تبويب الصفحات أولاً.")
            if not self._facebook_probe_running:
                self.facebook_probe_button.config(state="disabled")
        elif not self._facebook_probe_running:
            self.facebook_probe_button.config(state="normal")
            if not current:
                self.facebook_probe_status_var.set("اختر صفحة ثم اختبر الوصول قبل بدء المراقبة.")
        return labels

    def _set_facebook_probe_controls(self, running):
        control_state = "disabled" if running else "normal"
        self.facebook_cookie_toggle.config(state=control_state)
        self.facebook_cookies_entry.config(state=control_state)
        self.facebook_pick_button.config(state=control_state)
        self.facebook_save_button.config(state=control_state)
        self.facebook_probe_refresh_button.config(state=control_state)
        self.facebook_probe_page_combo.config(state="disabled" if running else "readonly")
        has_pages = bool(getattr(self, "_facebook_probe_page_urls", {}))
        self.facebook_probe_button.config(state="disabled" if running or not has_pages else "normal")

    def _start_facebook_page_probe(self):
        if self._closing or self._facebook_probe_running:
            return
        if self._watching or (self._thread is not None and self._thread.is_alive()):
            messagebox.showwarning(
                "العامل يعمل",
                "أوقف المراقبة قبل اختبار Facebook حتى لا يعمل متصفحان معاً.",
                parent=self.root,
            )
            return
        page_label = self.facebook_probe_page_var.get()
        page_url = getattr(self, "_facebook_probe_page_urls", {}).get(page_label)
        if not page_url:
            messagebox.showwarning(
                "اختر صفحة",
                "أضف صفحة Facebook مفعّلة أو اختر صفحة من القائمة أولاً.",
                parent=self.root,
            )
            return
        cookie_status = self._refresh_facebook_cookie_status()
        cookies_enabled = bool(self.cookies_enabled_var.get())
        cookies_file = self.cookies_path_var.get().strip()
        if cookies_enabled and cookie_status.get("status") != "ready":
            self.facebook_probe_status_var.set(cookie_status["message"])
            self.facebook_probe_status_label.config(fg=WARN)
            return

        try:
            page_timeout = config.getint("WATCH", "PAGE_LOAD_TIMEOUT", fallback=25)
        except (ValueError, configparser.Error):
            page_timeout = 25
        self._facebook_probe_running = True
        self._set_facebook_probe_controls(True)
        self.facebook_probe_status_var.set("جارٍ فحص الشبكة وفتح الصفحة في Chrome…")
        self.facebook_probe_status_label.config(fg=WARN)
        self._log(f"بدء اختبار وصول Facebook إلى {page_label}؛ لن يُنزّل أو يرفع أي فيديو.")

        def network_probe():
            if not _facebook_network_preflight(self._log):
                return None
            return _FACEBOOK_NETWORK_ROUTE

        def worker():
            try:
                result = probe_facebook_page_access(
                    page_url,
                    cookies_enabled,
                    cookies_file,
                    network_probe=network_probe,
                    driver_factory=lambda: init_driver(headless=True, log_cb=self._log),
                    cookie_loader=lambda driver, path: load_cookies_to_driver(
                        driver, path, self._log, page_load_timeout=page_timeout
                    ),
                    page_timeout=page_timeout,
                )
            except Exception:
                logging.exception("فشل اختبار وصول صفحة Facebook")
                result = {
                    "status": "probe_error",
                    "message": "تعذر إكمال الاختبار. راجع سجل البرنامج ثم أعد المحاولة.",
                    "network_route": None,
                }
            self._uiq.put(("facebook_probe_result", (page_label, result)))

        self._facebook_probe_thread = threading.Thread(
            target=worker,
            daemon=True,
            name="facebook-page-preflight",
        )
        self._facebook_probe_thread.start()

    def _finish_facebook_page_probe(self, payload):
        page_label, result = payload
        self._facebook_probe_running = False
        self._facebook_probe_thread = None
        self._set_facebook_probe_controls(False)
        route = result.get("network_route")
        route_note = {
            "ipv4": "المسار: IPv4. ",
            "ipv6": "المسار: IPv6 (تحويل تلقائي من IPv4). ",
            "proxy": "المسار: الوكيل. ",
            "direct": "المسار: مباشر. ",
        }.get(route, "")
        self.facebook_probe_status_var.set(f"{page_label}: {route_note}{result.get('message', '')}")
        colors = {
            "page_accessible": SUCCESS,
            "login_required": WARN,
            "security_checkpoint": WARN,
            "recovery_required": WARN,
            "page_timeout": WARN,
            "cookies_unready": WARN,
            "network_unavailable": ERR,
            "browser_unavailable": ERR,
            "browser_error": ERR,
            "cookies_not_loaded": ERR,
            "invalid_url": ERR,
            "probe_error": ERR,
        }
        status = result.get("status", "probe_error")
        self.facebook_probe_status_label.config(fg=colors.get(status, WARN))
        self._log(f"انتهى اختبار Facebook ({status}): {result.get('message', '')}")

    def _pick_cookies(self):
        path = filedialog.askopenfilename(filetypes=[("Cookies", "*.txt"), ("كل الملفات", "*.*")])
        if path:
            self.cookies_path_var.set(path)

    def _pick_cutter_root(self):
        path = filedialog.askdirectory(title="حدد مجلد OUSSAMA Cutter")
        if path:
            self.cutter_root_var.set(path)

    def _pick_cutter_python(self):
        path = filedialog.askopenfilename(
            title="حدد مفسّر Python الخاص بأداة المراجعة",
            filetypes=[("Python", "python*"), ("كل الملفات", "*.*")],
        )
        if path:
            self.cutter_python_var.set(path)

    def _reconcile_unknown_upload(self):
        if self._watching:
            messagebox.showwarning("العامل يعمل", "أوقف العامل قبل مطابقة نتيجة الرفع.", parent=self.root)
            return
        try:
            items = load_failed_queue()
            recovered, recovered_count = recover_interrupted_uploads(items, load_uploaded_ids())
            if recovered_count or recovered != items:
                items = recovered
                save_failed_queue(items)
            unresolved = [item for item in items if item.get("last_error") == UPLOAD_RESULT_UNKNOWN]
            if not unresolved:
                messagebox.showinfo("لا توجد نتيجة معلّقة", "لا توجد محاولة رفع غير مؤكدة تحتاج إلى مطابقة.", parent=self.root)
                return
            choices = "\n".join(
                f"{item.get('id', '')} — {item.get('upload_title', 'عنوان غير محفوظ')[:60]}"
                for item in unresolved
            )
            source_id = simpledialog.askstring(
                "مطابقة نتيجة الرفع",
                "اختر معرّف المصدر من القائمة والصقه هنا:\n\n" + choices,
                parent=self.root,
            )
            if not source_id:
                return
            source_id = source_id.strip()
            item = next((entry for entry in unresolved if str(entry.get("id", "")) == source_id), None)
            if item is None:
                messagebox.showerror("معرّف غير صالح", "المعرّف ليس ضمن النتائج المعلّقة.", parent=self.root)
                return
            youtube_value = simpledialog.askstring(
                "رابط الفيديو في YouTube",
                "ألصق رابط الفيديو الذي ظهر في YouTube Studio، أو معرّف الفيديو المكوّن من 11 حرفاً.",
                parent=self.root,
            )
            if not youtube_value:
                return
            video_id = extract_youtube_video_id(youtube_value)
            if not video_id:
                messagebox.showerror("رابط غير صالح", "لم أتعرف على رابط أو معرّف YouTube صالح.", parent=self.root)
                return
            account = self.acc_var.get().strip()
            if not account:
                messagebox.showerror("حساب YouTube", "اختر حساب YouTube النشط أولاً.", parent=self.root)
                return
            youtube, error = get_youtube(account, allow_interactive=False)
            if not youtube:
                messagebox.showerror("تعذر الاتصال", error or "تعذر الاتصال بحساب YouTube الحالي.", parent=self.root)
                return
            try:
                exists = youtube_video_belongs_to_current_channel(youtube, video_id)
            except Exception as error:
                messagebox.showerror("تعذر التحقق", f"لم أتمكن من التحقق من القناة: {error}", parent=self.root)
                return
            if exists:
                confirmed = messagebox.askyesno(
                    "تأكيد المطابقة",
                    f"أكدت YouTube أن الفيديو {video_id} موجود في القناة الحالية.\n"
                    f"تحقق أنه يطابق المصدر {source_id} قبل تأكيد المطابقة.\n\nهل هو الفيديو المقصود؟",
                    parent=self.root,
                )
                if not confirmed:
                    return
                updated, decision = reconcile_upload_result(items, source_id, video_exists=True)
                if decision != "uploaded":
                    messagebox.showerror("تعذر الحفظ", "تغيرت حالة العنصر؛ أعد فحص قائمة الانتظار.", parent=self.root)
                    return
                source_record = next(
                    (entry for entry in items if str(entry.get("id") or "").strip() == source_id), {}
                )
                persist_upload_receipt(
                    source_id,
                    video_id,
                    account=account,
                    url=source_record.get("url", ""),
                    page=source_record.get("page", ""),
                    title=source_record.get("upload_title", source_record.get("title", "")),
                )
                save_uploaded_id(source_id)
                save_failed_queue(updated)
                append_history({
                    "source_id": source_id, "youtube_id": video_id,
                    "account": account, "status": "uploaded_reconciled",
                    "error": "تمت المطابقة يدوياً مع فيديو أكد YouTube وجوده في القناة الحالية.",
                })
                self._log(f"طوبقت محاولة المصدر {source_id} مع فيديو القناة {video_id}؛ لن تُعاد.")
                messagebox.showinfo("اكتملت المطابقة", "سُجل المصدر كمرفوع وأُزيل من قائمة الإعادة.", parent=self.root)
                return
            retry = messagebox.askyesno(
                "الفيديو غير موجود في القناة الحالية",
                f"لم يعثر YouTube على الفيديو {video_id} في القناة الحالية.\n"
                f"افحص YouTube Studio بنفسك. إذا تأكدت أن المصدر {source_id} لم يُرفع،"
                " هل تريد اعتماده لإعادة محاولة واحدة؟",
                parent=self.root,
            )
            if not retry:
                return
            updated, decision = reconcile_upload_result(items, source_id, video_exists=False)
            if decision != "retry":
                messagebox.showerror("تعذر الحفظ", "تغيرت حالة العنصر؛ أعد فحص قائمة الانتظار.", parent=self.root)
                return
            save_failed_queue(updated)
            self._log(f"اعتمد المشغّل إعادة محاولة المصدر {source_id} بعد التحقق من YouTube Studio.")
            messagebox.showinfo("أُجيزت إعادة المحاولة", "أُعيد العنصر إلى قائمة العمل؛ لن يبدأ الرفع إلا في الدورة التالية.", parent=self.root)
        except Exception as error:
            logging.exception("تعذر مطابقة نتيجة الرفع")
            messagebox.showerror("تعذر إكمال المطابقة", str(error), parent=self.root)

    def _release_policy_holds(self):
        worker = getattr(self, "_thread", None)
        if self._watching or (worker is not None and worker.is_alive()):
            messagebox.showwarning("العامل يعمل", "أوقف العامل أو انتظر انتهاء الدورة قبل تعديل قائمة الانتظار.", parent=self.root)
            return
        try:
            items = load_failed_queue()
            safety_count = sum(
                item.get("last_error") == "content_safety_review_required" for item in items
            )
            publication_count = sum(
                item.get("last_error") == "publication_approval_required" for item in items
            )
            rights_count = sum(
                item.get("last_error") == "rights_review_required" for item in items
            )
            retry_count = sum(
                item.get("last_error") in {"abandoned", UPLOAD_REJECTED} for item in items
            )
            if not safety_count and not publication_count and not rights_count and not retry_count:
                messagebox.showinfo("لا توجد عناصر معلّقة", "لا توجد عناصر سياسة أو حقوق أو محاولات متوقفة تحتاج إلى إعادة فحص.", parent=self.root)
                return
            if safety_count:
                if not self.content_review_enabled_var.get():
                    messagebox.showwarning(
                        "بوابة الأمان معطّلة",
                        "فعّل بوابة مراجعة المحتوى وصحّح إعداداتها قبل تحرير عناصرها.",
                        parent=self.root,
                    )
                    return
                review_errors = validate_content_review_settings(
                    True,
                    self.cutter_root_var.get().strip(),
                    self.cutter_python_var.get().strip(),
                )
                if review_errors:
                    messagebox.showwarning(
                        "إعدادات بوابة الأمان غير مكتملة",
                        "\n".join(review_errors),
                        parent=self.root,
                    )
                    return
            if publication_count:
                publication_errors = validate_publication_controls(
                    self.priv_var.get(),
                    self.sched_var.get(),
                    self.review_before_upload_var.get(),
                    self.allow_public_automation_var.get(),
                    dry_run=self.dry_run_var.get(),
                )
                if publication_errors:
                    messagebox.showwarning(
                        "إعدادات النشر غير مكتملة",
                        "\n".join(publication_errors),
                        parent=self.root,
                    )
                    return
            if rights_count:
                rights_settings = {
                    "rights_gate_mode": self.rights_gate_var.get(),
                    "rights_operator_basis": self.rights_basis_var.get(),
                    "rights_operator_statement": self.rights_statement_e.get().strip(),
                    "rights_operator_evidence": self.rights_evidence_e.get().strip(),
                    "review_before_upload": self.review_before_upload_var.get(),
                    "dry_run": self.dry_run_var.get(),
                }
                rights_errors = automatic_rights_preflight_errors(rights_settings)
                if (
                    not rights_settings["review_before_upload"]
                    and not rights_settings["dry_run"]
                    and str(rights_settings["rights_gate_mode"]).strip().lower() == "strict"
                ):
                    rights_errors.append(
                        "فعّل المراجعة اليدوية لكل فيديو أو أكمل إقرار المشغل الحقيقي قبل إعادة العناصر إلى قائمة العمل."
                    )
                if rights_errors:
                    messagebox.showwarning(
                        "إعدادات بوابة الحقوق غير مكتملة",
                        "\n".join(rights_errors),
                        parent=self.root,
                    )
                    return
            confirmed = messagebox.askyesno(
                "إعادة فحص العناصر المعلّقة",
                f"ستعود العناصر ({rights_count} محجوبة بالحقوق) إلى قائمة العمل فقط، ولن تُرفع الآن؛ "
                "ستُفحص مجدداً في الدورة التالية. تأكد من إصلاح السبب الحقيقي قبل المتابعة. هل تريد المتابعة؟",
                parent=self.root,
            )
            if not confirmed:
                return
            released, count = release_policy_holds(items)
            if count:
                save_failed_queue(released)
            self._log(f"حُرّرت {count} عناصر لإعادة فحصها قبل الرفع.")
        except Exception as error:
            self._log(f"تعذر تحرير العناصر المعلّقة: {error}")

    def _update_ytdlp(self):
        def job():
            self._log("جاري تحديث yt-dlp…")
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                    capture_output=True, text=True, timeout=600)
                tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
                self._log(("تم: " if proc.returncode == 0 else "خطأ: ") + (tail[0] if tail else "اكتمل التحديث"))
            except Exception as error:
                self._log(f"فشل التحديث: {error}")
        threading.Thread(target=job, daemon=True).start()

    # ─────────── تبويب الاستوديو (العنوان + الشعار) ───────────
    def _tab_studio(self):
        canvas = tk.Canvas(self.t_studio, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(self.t_studio, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        out = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=out, anchor="nw")
        out.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        pad = tk.Frame(out, bg=BG); pad.pack(fill="both", expand=True, padx=20, pady=10)

        # ── تحسين العنوان ──
        sec(pad, "تحسين العنوان قبل الرفع", "")
        c = mk_card(pad); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        self.title_opt_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn, text="تحسين ذكي: إزالة الحشو والكلمات المكررة من عناوين فيسبوك",
                        variable=self.title_opt_var).pack(anchor="w", pady=2)
        self.omit_source_page_name_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            inn,
            text="حذف اسم صفحة المصدر من العنوان فقط",
            variable=self.omit_source_page_name_var,
        ).pack(anchor="w", pady=2)
        tk.Label(
            inn,
            text="يبقى اسم الصفحة ورابط المقطع في الوصف وسجل الحقوق.",
            bg=CARD,
            fg=TXT2,
            font=FSM,
        ).pack(anchor="w", padx=(20, 0), pady=(0, 4))

        self.auto_shorts_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn, text="إضافة #Shorts تلقائياً للمقاطع الأقصر من 60 ثانية",
                        variable=self.auto_shorts_var).pack(anchor="w", pady=2)
        r1 = tk.Frame(inn, bg=CARD); r1.pack(fill="x", pady=6)
        tk.Label(r1, text="بادئة العنوان:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.title_prefix_e = PEntry(r1, width=24)
        self.title_prefix_e.pack(side="left", padx=8, ipady=4)
        tk.Label(r1, text="لاحقة العنوان:", bg=CARD, fg=TXT).pack(side="left", padx=(14, 0))
        self.title_suffix_e = PEntry(r1, width=24)
        self.title_suffix_e.pack(side="left", padx=8, ipady=4)

        sec(pad, "الوصف والهاشتاجات", "")
        c1b = mk_card(pad); c1b.pack(fill="x", pady=(0, 10))
        inn1b = tk.Frame(c1b, bg=CARD, padx=14, pady=12); inn1b.pack(fill="x")
        self.auto_tags_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn1b, text="توليد هاشتاجات تلقائية في الوصف من كلمات العنوان + الوسوم",
                        variable=self.auto_tags_var).pack(anchor="w", pady=2)
        r1c = tk.Frame(inn1b, bg=CARD); r1c.pack(fill="x", pady=6)
        tk.Label(r1c, text="سطر حقوق/توقيع:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.credit_e = PEntry(r1c, ph="مثال: تابعونا على قناة الوطنية", width=50)
        self.credit_e.pack(side="left", padx=8, ipady=4)

        # ── تركيب الشعار ──
        sec(pad, "الشعار فوق الشعار الأصلي (يتطلب ffmpeg)", "")
        c2 = mk_card(pad); c2.pack(fill="x", pady=(0, 10))
        inn2 = tk.Frame(c2, bg=CARD, padx=14, pady=12); inn2.pack(fill="x")
        self.logo_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn2, text="تفعيل تركيب شعارك على الفيديو قبل الرفع",
                        variable=self.logo_enabled_var).pack(anchor="w", pady=2)
        self.cover_old_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn2, text="تغطية شعار الصفحة الأصلي بخلفية معتمة تحت شعارك",
                        variable=self.cover_old_var).pack(anchor="w", pady=2)
        self.plate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn2, text="لوحة داكنة خلف الشعار (بدونها: شعارك يوضع فوق شعار القناة القديم مباشرة)",
                        variable=self.plate_var).pack(anchor="w", pady=2)
        r2 = tk.Frame(inn2, bg=CARD); r2.pack(fill="x", pady=6)
        tk.Label(r2, text="ملف الشعار:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.logo_path_var = tk.StringVar(value="logo.png")
        tk.Entry(r2, textvariable=self.logo_path_var, bg=INP, fg=TXT, relief="flat",
                 font=FB, width=38, highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", padx=8, ipady=4)
        Btn(r2, "اختيار", self._pick_logo, "#37474f").pack(side="left")
        r3 = tk.Frame(inn2, bg=CARD); r3.pack(fill="x", pady=6)
        tk.Label(r3, text="الموضع:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.logo_pos_var = tk.StringVar(value="أعلى اليمين")
        ttk.Combobox(r3, textvariable=self.logo_pos_var, state="readonly", width=12,
                     values=list(LOGO_POSITIONS.keys())).pack(side="left", padx=8)
        tk.Label(r3, text="الحجم % من العرض:", bg=CARD, fg=TXT).pack(side="left", padx=(14, 0))
        self.logo_scale_e = PEntry(r3, width=5); self.logo_scale_e.insert(0, "8")
        self.logo_scale_e.pack(side="left", padx=6)
        tk.Label(r3, text="الشفافية %:", bg=CARD, fg=TXT).pack(side="left", padx=(14, 0))
        self.logo_op_e = PEntry(r3, width=5); self.logo_op_e.insert(0, "100")
        self.logo_op_e.pack(side="left", padx=6)
        r3b = tk.Frame(inn2, bg=CARD); r3b.pack(fill="x", pady=6)
        tk.Label(r3b, text="عرض بطاقة التغطية (%):", bg=CARD, fg=TXT,
                 width=22, anchor="e").pack(side="left")
        self.logo_cover_width_e = PEntry(r3b, width=5)
        self.logo_cover_width_e.insert(0, "20")
        self.logo_cover_width_e.pack(side="left", padx=8)
        tk.Label(r3b, text="10–40% من عرض الفيديو", bg=CARD, fg=TXT2,
                 font=FSM).pack(side="left")
        r3s = tk.Frame(inn2, bg=CARD); r3s.pack(fill="x", pady=4)
        self.secondary_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3s, text="علامة شعار ثانوية صغيرة في الركن المقابل (تظهر أثناء المقطع)",
                        variable=self.secondary_var).pack(side="left")
        self.secondary_cover_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3s, text="مع غطاء معتم تحت العلامة الثانوية",
                        variable=self.secondary_cover_var).pack(side="left", padx=(12, 0))
        tk.Label(inn2, text="ضع شعارك بجانب البرنامج باسم logo.png (يفضّل PNG بخلفية شفافة)",
                 bg=CARD, fg=TXT2, font=FSM).pack(anchor="w", pady=(4, 0))

        # ── المصغرة والنسخة القصيرة ──
        sec(pad, "صورة مصغرة ونسخة قصيرة", "")
        c2b = mk_card(pad); c2b.pack(fill="x", pady=(0, 10))
        inn2b = tk.Frame(c2b, bg=CARD, padx=14, pady=12); inn2b.pack(fill="x")
        self.review_before_upload_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn2b, text="معاينة وموافقة يدوية قبل كل رفع (العنوان، الوصف، الخصوصية والصورة المصغرة)",
                        variable=self.review_before_upload_var).pack(anchor="w", pady=2)
        self.rights_auto_draft_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn2b, text="إنشاء مسودة غير معتمدة لتوثيق التحويلات؛ لا تثبت الملكية أو الإذن — يلزم الإقرار اليدوي قبل الرفع",
                        variable=self.rights_auto_draft_var).pack(anchor="w", pady=2)
        gate_row = tk.Frame(inn2b, bg=CARD); gate_row.pack(fill="x", pady=(6, 2))
        tk.Label(gate_row, text="بوابة الحقوق:", bg=CARD, fg=TXT,
                 width=13, anchor="e").pack(side="left")
        self.rights_gate_var = tk.StringVar(value="strict")
        ttk.Combobox(gate_row, textvariable=self.rights_gate_var, state="readonly",
                     width=22, values=("strict", "operator_confirmed")).pack(side="left", padx=8)
        tk.Label(gate_row, text="أساس الحقوق:", bg=CARD, fg=TXT,
                 width=12, anchor="e").pack(side="left")
        self.rights_basis_var = tk.StringVar(value="owned")
        ttk.Combobox(gate_row, textvariable=self.rights_basis_var, state="readonly",
                     width=16, values=tuple(RIGHTS_BASIS_LABELS)).pack(side="left", padx=8)
        stmt_row = tk.Frame(inn2b, bg=CARD); stmt_row.pack(fill="x", pady=2)
        tk.Label(stmt_row, text="إقرار المشغل:", bg=CARD, fg=TXT,
                 width=13, anchor="e").pack(side="left")
        self.rights_statement_e = tk.Entry(stmt_row, bg=INP, fg=TXT, relief="flat",
                                           font=FB, highlightthickness=1,
                                           highlightbackground=BORDER)
        self.rights_statement_e.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        ev_row = tk.Frame(inn2b, bg=CARD); ev_row.pack(fill="x", pady=2)
        tk.Label(ev_row, text="مرجع الإثبات:", bg=CARD, fg=TXT,
                 width=13, anchor="e").pack(side="left")
        self.rights_evidence_e = tk.Entry(ev_row, bg=INP, fg=TXT, relief="flat",
                                          font=FB, highlightthickness=1,
                                          highlightbackground=BORDER)
        self.rights_evidence_e.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        tk.Label(inn2b, text="operator_confirmed: يُرفع آلياً بناءً على الإقرار المحفوظ ويُوثّق في rights_manifest.json. "
                             "strict: يتوقف الرفع حتى الإقرار اليدوي لكل مقطع.",
                 bg=CARD, fg=TXT2, font=FSM, wraplength=680, justify="right"
                 ).pack(anchor="e", pady=(2, 0))
        self.thumbnail_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn2b, text="توليد صورة مصغرة من الفيديو مع العنوان ورفعها (50 وحدة/رفع — تتطلب قناة موثّقة)",
                        variable=self.thumbnail_var).pack(anchor="w", pady=2)
        self.shorts_cut_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn2b, text="قص المشهد الأعلى صوتاً كنسخة Shorts إضافية (للمقاطع 95 ثانية وأكثر)",
                        variable=self.shorts_cut_var).pack(anchor="w", pady=2)

        # ── فلاتر المحتوى ──
        sec(pad, "فلاتر المحتوى")
        c3 = mk_card(pad); c3.pack(fill="x")
        inn3 = tk.Frame(c3, bg=CARD, padx=14, pady=12); inn3.pack(fill="x")
        r4 = tk.Frame(inn3, bg=CARD); r4.pack(fill="x", pady=4)
        tk.Label(r4, text="أدنى مدة (ثانية):", bg=CARD, fg=TXT, width=16, anchor="e").pack(side="left")
        self.min_dur_e = PEntry(r4, width=6); self.min_dur_e.insert(0, "0")
        self.min_dur_e.pack(side="left", padx=6)
        tk.Label(r4, text="أقصى مدة (ثانية، 0 = بلا سقف):", bg=CARD, fg=TXT).pack(side="left", padx=(14, 0))
        self.max_dur_e = PEntry(r4, width=6); self.max_dur_e.insert(0, "0")
        self.max_dur_e.pack(side="left", padx=6)
        tk.Label(inn3, text="الفيديوهات خارج النطاق تُتخطى وتُؤرشف ولا تُعاد.",
                 bg=CARD, fg=TXT2, font=FSM).pack(anchor="w", pady=(4, 0))

        self.dedupe_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn3, text="منع رفع نفس المحتوى مرتين (تطابق العناوين بين الصفحات)",
                        variable=self.dedupe_var).pack(anchor="w", pady=6)

        sec(pad, "بوابة مراجعة سياسة المحتوى", "")
        c4 = mk_card(pad); c4.pack(fill="x", pady=(10, 0))
        inn4 = tk.Frame(c4, bg=CARD, padx=14, pady=12); inn4.pack(fill="x")
        self.content_review_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            inn4,
            text="اشترط تقرير مراجعة نصية حديثة قبل كل رفع؛ أي تقرير مفقود أو غير واضح يوقفه",
            variable=self.content_review_enabled_var,
        ).pack(anchor="w", pady=2)
        cutter_root_row = tk.Frame(inn4, bg=CARD); cutter_root_row.pack(fill="x", pady=5)
        tk.Label(cutter_root_row, text="مجلد OUSSAMA Cutter:", bg=CARD, fg=TXT,
                 width=20, anchor="e").pack(side="left")
        self.cutter_root_var = tk.StringVar(value="")
        tk.Entry(cutter_root_row, textvariable=self.cutter_root_var, bg=INP, fg=TXT,
                 relief="flat", font=FB, width=48, highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", padx=8, ipady=4)
        Btn(cutter_root_row, "اختيار المجلد", self._pick_cutter_root, "#37474f").pack(side="left")
        cutter_python_row = tk.Frame(inn4, bg=CARD); cutter_python_row.pack(fill="x", pady=5)
        tk.Label(cutter_python_row, text="Python الخاص بالمراجع:", bg=CARD, fg=TXT,
                 width=20, anchor="e").pack(side="left")
        self.cutter_python_var = tk.StringVar(value="")
        tk.Entry(cutter_python_row, textvariable=self.cutter_python_var, bg=INP, fg=TXT,
                 relief="flat", font=FB, width=48, highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", padx=8, ipady=4)
        Btn(cutter_python_row, "اختيار Python", self._pick_cutter_python, "#37474f").pack(side="left")
        tk.Label(
            inn4,
            text="عند تفعيلها، يمرّ الفيديو النهائي إلى أداة Cutter المحلية؛ قد ترسل الأداة نص التفريغ إلى مزوّد ذكاء اصطناعي حسب إعدادها. الفحص نصّي فقط، لا يحلل الصور ولا يضمن قرار YouTube. زر إعادة المحاولة يحرّر العناصر المعلّقة فقط؛ ثم يعيد العامل فحصها قبل أي رفع.",
            bg=CARD, fg=TXT2, font=FSM, wraplength=900, justify="left",
        ).pack(anchor="w", pady=(4, 8))
        Btn(inn4, "إعادة فحص العناصر المعلّقة", self._release_policy_holds,
            "#7f1d1d").pack(anchor="w")
        tk.Label(
            inn4,
            text="إذا انقطع الاتصال أثناء videos.insert، لا يعيد البرنامج الرفع تلقائياً؛ طابق النتيجة هنا لتجنب نسخة مكررة.",
            bg=CARD, fg=TXT2, font=FSM, wraplength=900, justify="left",
        ).pack(anchor="w", pady=(8, 4))
        Btn(inn4, "مطابقة نتيجة رفع غير مؤكدة", self._reconcile_unknown_upload,
            "#8a5b13").pack(anchor="w")


    def _pick_logo(self):
        path = filedialog.askopenfilename(
            filetypes=[("صور", "*.png *.jpg *.jpeg *.webp"), ("كل الملفات", "*.*")])
        if path:
            self.logo_path_var.set(path)

    # ─────────── تبويب السجل ───────────
    def _tab_log(self):
        out = tk.Frame(self.t_log, bg=BG)
        out.pack(fill="both", expand=True, padx=14, pady=10)
        actions = tk.Frame(out, bg=BG)
        actions.pack(fill="x", pady=(0, 8))
        self.diagnostic_report_button = Btn(
            actions,
            "إنشاء تقرير تشخيصي",
            self._create_diagnostic_report,
            "#455a64",
        )
        self.diagnostic_report_button.pack(side="left")
        tk.Label(
            actions,
            text="تقرير محلي للتشخيص؛ راجعه قبل مشاركته.",
            bg=BG,
            fg=TXT2,
            font=FSM,
        ).pack(side="left", padx=10)

        lf = tk.Frame(out, bg=INP, highlightbackground=BORDER, highlightthickness=1)
        lf.pack(fill="both", expand=True)
        self.log_txt = tk.Text(lf, bg=INP, fg=TXT, font=FM, wrap="word",
                               state="disabled", relief="flat", padx=10, pady=8)
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=vsb.set)
        self.log_txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.log_txt.tag_config("ok", foreground=SUCCESS)
        self.log_txt.tag_config("err", foreground=ERR)
        self.log_txt.tag_config("warn", foreground=WARN)
        self.log_txt.tag_config("ts", foreground="#555570")

    def _create_diagnostic_report(self):
        if self._diagnostic_report_running:
            return
        self._diagnostic_report_running = True
        self.diagnostic_report_button.config(state="disabled")
        self._log("بدأ جمع مؤشرات تشخيصية محلية؛ لا تُرسل أي بيانات إلى خدمة خارجية.")

        def worker():
            try:
                path = write_diagnostic_report(APP_DIRECTORY, __version__)
                result = {"path": os.path.relpath(path, APP_DIRECTORY)}
            except Exception as error:
                logging.exception("تعذر إنشاء التقرير التشخيصي المحلي: %s", type(error).__name__)
                result = {"error_type": type(error).__name__}
            self._uiq.put(("diagnostic_report_finished", result))

        threading.Thread(
            target=worker,
            daemon=True,
            name="diagnostic-report-worker",
        ).start()

    def _log(self, msg, replace_last=False):
        msg = redact_sensitive_text(msg)
        if not replace_last and "pytest" not in sys.modules:
            try:
                _configure_cli_logger().info("%s", msg)
            except Exception:
                logging.exception("تعذر حفظ رسالة واجهة المستخدم في automation.log")
        self._logq.put((msg, replace_last))

    # ─────────── تبويب الحسابات ───────────
    def _tab_accounts(self):
        out = tk.Frame(self.t_accs, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)
        sec(out, "إدارة الحسابات", "")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        self.acc_lb = tk.Listbox(inn, bg=INP, fg=TXT, font=FB, relief="flat", height=8)
        self.acc_lb.pack(fill="x", pady=8)
        for a in self.accounts:
            self.acc_lb.insert("end", a)
        r = tk.Frame(inn, bg=CARD); r.pack(fill="x")
        self.new_acc_e = PEntry(r, ph="اسم الحساب", width=30)
        self.new_acc_e.pack(side="left", ipady=4)
        Btn(r, "إضافة", self._acc_add, ACCENT).pack(side="left", padx=10)
        Btn(r, "حذف", self._acc_del, ERR).pack(side="left")

    def _acc_add(self):
        n = self.new_acc_e.val().strip()
        if n and n not in self.accounts:
            self.accounts.append(n); save_accounts(self.accounts)
            self.acc_lb.insert("end", n); self.acc_combo["values"] = self.accounts
        self.new_acc_e.delete(0, "end"); self.new_acc_e._put()

    def _acc_del(self):
        s = self.acc_lb.curselection()
        if s:
            n = self.acc_lb.get(s[0]); self.accounts.remove(n); save_accounts(self.accounts)
            self.acc_lb.delete(s[0]); self.acc_combo["values"] = self.accounts

    # ─────────── لوحة التحكم ───────────
    def _tab_dashboard(self):
        out = tk.Frame(self.t_dash, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)
        sec(out, "إحصائيات الأداء", "")
        self.stats_card = StatsCard(out)
        row = tk.Frame(out, bg=BG); row.pack(pady=10)
        Btn(row, "تحديث البيانات", lambda: self.stats_card.refresh(), color="#455a64").pack(side="left", padx=5)
        Btn(row, "فتح الأرشيف", self._open_history, color="#37474f").pack(side="left", padx=5)
        self._preflight_btn = Btn(row, "فحص الجاهزية", self._start_readiness_check, color="#1f6feb")
        self._preflight_btn.pack(side="left", padx=5)
        tk.Label(
            out,
            text="الفحص محلي للمتطلبات والإعدادات؛ لا يرفع ملفات ولا يغيّر إعداداتك.",
            bg=BG,
            fg=TXT2,
            font=FS,
        ).pack(anchor="w", pady=(2, 0))

    def _start_readiness_check(self):
        if self._watching or (self._thread is not None and self._thread.is_alive()):
            messagebox.showwarning(
                "العامل يعمل",
                "أوقف العامل قبل فحص الجاهزية حتى لا تُقرأ الإعدادات أثناء التشغيل.",
                parent=self.root,
            )
            return
        if self._preflight_running:
            return
        self._preflight_running = True
        self._preflight_btn.config(state="disabled")
        self.status_lbl.config(text="جاري فحص الجاهزية...", fg=WARN)

        def worker():
            try:
                result = run_readiness_check(os.path.dirname(os.path.abspath(__file__)))
            except Exception as error:
                logging.exception("تعذر إكمال فحص الجاهزية")
                result = (2, f"تعذر إكمال فحص الجاهزية: {error}")
            self._uiq.put(("preflight_result", result))

        threading.Thread(target=worker, daemon=True).start()

    def _show_readiness_report(self, return_code, output):
        window = tk.Toplevel(self.root)
        title = "تقرير الجاهزية — جاهز" if return_code == 0 else "تقرير الجاهزية — يحتاج مراجعة"
        window.title(title)
        window.geometry("860x620")
        window.minsize(680, 440)
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grid_rowconfigure(1, weight=1)
        window.grid_columnconfigure(0, weight=1)

        tk.Label(
            window,
            text=title,
            bg=PANEL,
            fg=SUCCESS if return_code == 0 else WARN,
            font=FB,
            anchor="e",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))

        body = tk.Frame(window, bg=PANEL)
        body.grid(row=1, column=0, sticky="nsew", padx=18)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        report = tk.Text(
            body,
            wrap="word",
            bg=BG,
            fg=TXT,
            insertbackground=TXT,
            relief="flat",
            padx=12,
            pady=10,
            font=("TkDefaultFont", 10),
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=report.yview)
        report.configure(yscrollcommand=scrollbar.set)
        report.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        report.insert("1.0", output or "لم يُنتج التشخيص تقريراً.")
        report.configure(state="disabled")

        actions = tk.Frame(window, bg=PANEL)
        actions.grid(row=2, column=0, sticky="ew", padx=18, pady=14)

        def copy_report():
            self.root.clipboard_clear()
            self.root.clipboard_append(output or "لم يُنتج التشخيص تقريراً.")
            self.status_lbl.config(text="نُسخ تقرير الجاهزية", fg=SUCCESS)

        Btn(actions, "نسخ التقرير", copy_report, "#455a64").pack(side="left")
        Btn(actions, "إغلاق", window.destroy, ACCENT).pack(side="right")
        window.grab_set()
        window.lift()

    def _open_history(self):
        if os.path.exists(HISTORY_FILE):
            try:
                os.startfile(HISTORY_FILE)  # Windows
            except AttributeError:
                import subprocess
                subprocess.Popen(["xdg-open", HISTORY_FILE])
        else:
            messagebox.showinfo("الأرشيف", "لا يوجد أرشيف بعد — سيُنشأ مع أول عملية.")

    # ─────────── التذييل والتحكم ───────────
    def _build_footer(self):
        # تخطيط grid ثابت — قضيب الأزرار ظاهر دائماً مهما كان حجم النافذة
        tk.Frame(self.root, bg=BORDER, height=1).grid(row=2, column=0, sticky="ew")
        foot = tk.Frame(self.root, bg=PANEL, pady=15)
        foot.grid(row=3, column=0, sticky="ew")
        inn = tk.Frame(foot, bg=PANEL); inn.pack(padx=25, fill="x")

        pg_row = tk.Frame(inn, bg=PANEL); pg_row.pack(fill="x", pady=(0, 8))
        self.prog_var = tk.DoubleVar()
        ttk.Progressbar(pg_row, variable=self.prog_var, orient="horizontal",
                        mode="determinate").pack(side="left", fill="x", expand=True, padx=(0, 15))
        self.pct_lbl = tk.Label(pg_row, text="0%", bg=PANEL, fg=ACCENT, font=FS, width=5)
        self.pct_lbl.pack(side="right")

        ctrl = tk.Frame(inn, bg=PANEL); ctrl.pack(fill="x")
        self.status_lbl = tk.Label(ctrl, text="جاهز — الصفحات محفوظة في pages.json",
                                   bg=PANEL, fg=TXT2, font=FB)
        self.status_lbl.pack(side="left")

        self.start_once_btn = Btn(ctrl, "▶ تشغيل مرة واحدة", lambda: self._do_start(False), SUCCESS)
        self.start_once_btn.pack(side="right")
        self.watch_btn = Btn(ctrl, "بدء المراقبة المستمرة", lambda: self._do_start(True), ACCENT)
        self.watch_btn.pack(side="right", padx=10)
        self.stop_btn = Btn(ctrl, "إيقاف", self._do_stop, ERR)
        self.stop_btn.pack(side="right")
        self.stop_btn.config(state="disabled")

    def _do_stop(self):
        self._stop.set()
        self._cancel_pending_review()
        self._save_ui_settings()
        self.status_lbl.config(text="جاري الإيقاف...", fg=ERR)
        self.countdown_var.set("جاري الإيقاف...")

    def _show_upload_review(self, request):
        if request["ready"].is_set() or self._stop.is_set():
            request["decision"].update({"action": "cancel"})
            request["ready"].set()
            return

        payload = request["payload"]
        window = tk.Toplevel(self.root)
        self._pending_review = {**request, "window": window}
        window.title("مراجعة الفيديو قبل الرفع")
        window.transient(self.root)
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        width = max(720, min(1040, screen_w - 60))
        height = max(640, min(900, screen_h - 70))
        window.geometry(f"{width}x{height}")
        window.minsize(680, 600)
        window.configure(bg=BG)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)

        top = tk.Frame(window, bg=PANEL, padx=18, pady=12)
        top.grid(row=0, column=0, sticky="ew")
        tk.Label(top, text="معاينة وموافقة قبل الرفع", bg=PANEL, fg=TXT,
                 font=("Segoe UI Semibold", 16, "bold")).pack(anchor="e")
        tk.Label(top, text=f"الصفحة: {payload.get('page') or 'غير محددة'}  •  "
                           f"المدة: {int(payload.get('duration') or 0)} ثانية",
                 bg=PANEL, fg=TXT2, font=FSM).pack(anchor="e", pady=(4, 0))
        duplicate_matches = payload.get("duplicate_matches", [])
        channel_index_warning = payload.get("channel_index_warning", "")
        rights_manifest_warning = payload.get("rights_manifest_warning", "")
        warning_lines = ["تغيير الشعار أو العنوان وحده لا يجعل الفيديو أصلياً ولا يضمن قبوله لتحقيق الربح."]
        if rights_manifest_warning:
            warning_lines.append(f"{rights_manifest_warning}")
        if channel_index_warning:
            warning_lines.append(f"{channel_index_warning}")
        if duplicate_matches:
            if any(match.get("match_type") == "visual_fingerprint" for match in duplicate_matches):
                warning_lines.append("تشابه بصري محتمل مع فيديو رفعه هذا المشروع سابقاً؛ البصمة قرينة للمراجعة وليست حكماً نهائياً.")
            else:
                warning_lines.append("احتمال تكرار: التشابه مبني على العنوان/المدة فقط، وليس إثباتاً لتطابق الفيديو.")
            for match in duplicate_matches[:3]:
                details = f"• {match.get('title', '')[:95]}"
                if match.get("matched_frames") is not None:
                    details += f" — تطابق بصري في {match['matched_frames']} إطارات"
                if match.get("published_at"):
                    details += f" — {match['published_at'][:10]}"
                if match.get("duration_delta") is not None:
                    details += f" — فرق المدة {match['duration_delta']} ث"
                if match.get("video_id"):
                    details += f" — https://www.youtube.com/watch?v={match['video_id']}"
                warning_lines.append(details)
        warning_lines.extend(build_upload_review_warnings(payload))
        if warning_lines:
            tk.Label(top, text="\n".join(warning_lines), bg=PANEL, fg=WARN,
                     font=FSM, justify="right", wraplength=width - 48).pack(anchor="e", pady=(8, 0))

        body = tk.Frame(window, bg=BG, padx=18, pady=12)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        preview_frame = tk.Frame(body, bg=CARD, padx=10, pady=8)
        preview_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_path = payload.get("thumbnail_path")
        preview_label = tk.Label(preview_frame, bg=CARD, fg=TXT2, font=FB)
        preview_label.pack(pady=4)

        def display_preview(path):
            if path and os.path.isfile(path):
                try:
                    from PIL import ImageTk
                    image = Image.open(path).convert("RGB")
                    image.thumbnail((width - 90, 340), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image)
                    preview_label.configure(image=photo, text="")
                    preview_label.image = photo
                    window._review_photo = photo
                    return True
                except Exception as error:
                    logging.warning("تعذر عرض الصورة المصغرة في نافذة المراجعة: %s", error)
                    preview_label.configure(image="", text="تعذر عرض الصورة المصغرة؛ ستظل بيانات الفيديو قابلة للمراجعة.")
                    preview_label.image = None
                    return False
            preview_label.configure(image="", text="لا توجد صورة مصغرة للمعاينة.")
            preview_label.image = None
            return False

        display_preview(preview_path)

        thumbnail_base_path = payload.get("thumbnail_base_path")
        preview_job = [None]
        preview_status = tk.Label(preview_frame, text="تتحدث المعاينة مع تعديل العنوان.",
                                  bg=CARD, fg=TXT2, font=FSM)
        preview_status.pack(anchor="e", pady=(2, 0))

        def refresh_thumbnail_preview():
            preview_job[0] = None
            if not thumbnail_base_path or not preview_path:
                return
            rendered = render_thumbnail_title_from_base(
                thumbnail_base_path, title_var.get().strip(), out_path=preview_path)
            if rendered:
                display_preview(rendered)
                preview_status.configure(text="المعاينة مطابقة للعنوان الحالي.", fg=SUCCESS)
            else:
                preview_status.configure(text="تعذر تحديث الصورة؛ لن تُرفع صورة مصغّرة مخصصة.", fg=WARN)

        def schedule_thumbnail_preview(*_args):
            if preview_job[0] is not None:
                try:
                    window.after_cancel(preview_job[0])
                except tk.TclError:
                    pass
            preview_job[0] = window.after(180, refresh_thumbnail_preview)

        fields = tk.Frame(body, bg=CARD, padx=14, pady=10)
        fields.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        fields.grid_columnconfigure(0, weight=1)
        tk.Label(fields, text="العنوان (حتى 100 حرف):", bg=CARD, fg=TXT,
                 font=FB).grid(row=0, column=0, sticky="e")
        title_var = tk.StringVar(value=payload.get("title", ""))
        title_entry = tk.Entry(fields, textvariable=title_var, bg=INP, fg=TXT,
                               insertbackground=TXT, relief="flat", justify="right",
                               font=FB, highlightthickness=1, highlightbackground=BORDER)
        title_entry.grid(row=1, column=0, sticky="ew", pady=(4, 8), ipady=5)
        title_var.trace_add("write", schedule_thumbnail_preview)

        tk.Label(fields, text="الوصف (حتى 5000 حرف):", bg=CARD, fg=TXT,
                 font=FB).grid(row=2, column=0, sticky="e")
        desc_frame = tk.Frame(fields, bg=CARD)
        desc_frame.grid(row=3, column=0, sticky="ew", pady=(4, 8))
        desc_frame.grid_columnconfigure(0, weight=1)
        desc_text = tk.Text(desc_frame, height=5, wrap="word", bg=INP, fg=TXT,
                            insertbackground=TXT, relief="flat", font=FB,
                            highlightthickness=1, highlightbackground=BORDER)
        desc_scroll = ttk.Scrollbar(desc_frame, orient="vertical", command=desc_text.yview)
        desc_text.configure(yscrollcommand=desc_scroll.set)
        desc_text.grid(row=0, column=0, sticky="ew")
        desc_scroll.grid(row=0, column=1, sticky="ns")
        desc_text.insert("1.0", payload.get("description", ""))

        privacy_names = {"private": "خاص", "unlisted": "غير مدرج", "public": "عام"}
        privacy_values = list(privacy_names.values())
        current_privacy = payload.get("privacy", "private")
        privacy_var = tk.StringVar(value=privacy_names.get(current_privacy, "خاص"))
        privacy_row = tk.Frame(fields, bg=CARD)
        privacy_row.grid(row=4, column=0, sticky="ew")
        tk.Label(privacy_row, text="الخصوصية:", bg=CARD, fg=TXT,
                 font=FB).pack(side="right", padx=(8, 0))
        privacy_combo = ttk.Combobox(privacy_row, textvariable=privacy_var,
                                     values=privacy_values, state="readonly", width=16)
        privacy_combo.pack(side="right")
        scheduled = bool(payload.get("scheduled"))
        if scheduled:
            privacy_var.set("خاص")
            privacy_combo.configure(state="disabled")
            tk.Label(privacy_row, text="النشر المجدول يبقى خاصاً حتى موعده.",
                     bg=CARD, fg=TXT2, font=FSM).pack(side="right", padx=10)

        initial_rights = payload.get("rights_record") or {}
        basis_choices = ["اختر أساس حقوق الاستخدام…", *RIGHTS_BASIS_LABELS.values()]
        basis_var = tk.StringVar(value=RIGHTS_BASIS_LABELS.get(
            initial_rights.get("rights_basis", ""), basis_choices[0]))
        evidence_var = tk.StringVar(value=initial_rights.get("evidence_reference", ""))
        attested_var = tk.BooleanVar(value=initial_rights.get("attested") is True)
        rights_box = tk.LabelFrame(
            fields, text="الحقوق والإضافة التحريرية الأصلية", bg=CARD, fg=TXT,
            padx=10, pady=6, font=FB
        )
        rights_box.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        rights_box.grid_columnconfigure(0, weight=1)
        tk.Label(rights_box, text="أساس حق النشر:", bg=CARD, fg=TXT, font=FB).grid(
            row=0, column=0, sticky="e")
        basis_combo = ttk.Combobox(
            rights_box, textvariable=basis_var, values=basis_choices,
            state="readonly", justify="right")
        basis_combo.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        tk.Label(rights_box, text="مرجع الترخيص أو الإذن (إذا كان المحتوى مرخّصاً):",
                 bg=CARD, fg=TXT, font=FB).grid(row=2, column=0, sticky="e")
        evidence_entry = tk.Entry(
            rights_box, textvariable=evidence_var, bg=INP, fg=TXT,
            insertbackground=TXT, relief="flat", justify="right", font=FB,
            highlightthickness=1, highlightbackground=BORDER)
        evidence_entry.grid(row=3, column=0, sticky="ew", pady=(2, 6), ipady=4)
        tk.Label(rights_box, text="ما الإضافة الأصلية الفعلية؟ (تعليق، تحليل، تقرير، سياق جديد…):",
                 bg=CARD, fg=TXT, font=FB).grid(row=4, column=0, sticky="e")
        contribution_text = tk.Text(
            rights_box, height=2, wrap="word", bg=INP, fg=TXT,
            insertbackground=TXT, relief="flat", font=FB,
            highlightthickness=1, highlightbackground=BORDER)
        contribution_text.grid(row=5, column=0, sticky="ew", pady=(2, 6))
        contribution_text.insert("1.0", initial_rights.get("original_contribution", ""))
        tk.Label(rights_box, text="إذا ظهر تشابه: وضّح الفرق الفعلي عن الفيديو السابق:",
                 bg=CARD, fg=TXT, font=FB).grid(row=6, column=0, sticky="e")
        duplicate_note_text = tk.Text(
            rights_box, height=2, wrap="word", bg=INP, fg=TXT,
            insertbackground=TXT, relief="flat", font=FB,
            highlightthickness=1, highlightbackground=BORDER)
        duplicate_note_text.grid(row=7, column=0, sticky="ew", pady=(2, 6))
        duplicate_note_text.insert("1.0", initial_rights.get("duplicate_review_note", ""))
        tk.Checkbutton(
            rights_box,
            text="أؤكد أن لدي حق النشر أو إذناً موثقاً، وأن وصفي للإضافة الأصلية دقيق.",
            variable=attested_var, bg=CARD, fg=TXT, selectcolor=INP,
            activebackground=CARD, activeforeground=TXT, justify="right",
            wraplength=width - 100, font=FB
        ).grid(row=8, column=0, sticky="e", pady=(2, 0))

        footer = tk.Frame(window, bg=PANEL, padx=16, pady=12)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        tk.Label(footer, text="الموافقة ترفع الفيديو. إغلاق النافذة يتخطاه دون رفع.",
                 bg=PANEL, fg=TXT2, font=FSM).grid(row=0, column=0, sticky="w", pady=(0, 8))

        def finish(action):
            if request["ready"].is_set():
                return
            decision = {"action": action}
            if action == "approve":
                title = title_var.get().strip()
                description = desc_text.get("1.0", "end-1c").strip()
                if not title:
                    messagebox.showerror("عنوان مطلوب", "اكتب عنواناً قبل الموافقة.", parent=window)
                    return
                if len(title) > 100:
                    messagebox.showerror("العنوان طويل", "حد عنوان YouTube هو 100 حرف.", parent=window)
                    return
                if len(description) > 5000:
                    messagebox.showerror("الوصف طويل", "اختصر الوصف إلى 5000 حرف أو أقل.", parent=window)
                    return
                selected_basis = next(
                    (key for key, label in RIGHTS_BASIS_LABELS.items() if label == basis_var.get()), "")
                rights_record = new_rights_record(
                    rights_basis=selected_basis,
                    evidence_reference=evidence_var.get(),
                    original_contribution=contribution_text.get("1.0", "end-1c"),
                    duplicate_review_note=duplicate_note_text.get("1.0", "end-1c"),
                    source_url=payload.get("source_url", ""),
                    attested=attested_var.get(),
                )
                rights_errors = rights_record_errors(rights_record, bool(duplicate_matches))
                if rights_errors:
                    messagebox.showerror(
                        "مراجعة الحقوق مطلوبة",
                        "لا يمكن رفع الفيديو قبل استكمال هذه النقاط:\n• " + "\n• ".join(rights_errors),
                        parent=window,
                    )
                    return
                selected_privacy = "private" if scheduled else next(
                    (key for key, value in privacy_names.items() if value == privacy_var.get()),
                    "private")
                thumbnail_for_upload = None
                if thumbnail_base_path and preview_path:
                    if preview_job[0] is not None:
                        try:
                            window.after_cancel(preview_job[0])
                        except tk.TclError:
                            pass
                        preview_job[0] = None
                    thumbnail_for_upload = render_thumbnail_title_from_base(
                        thumbnail_base_path, title, out_path=preview_path)
                    if thumbnail_for_upload:
                        display_preview(thumbnail_for_upload)
                        preview_status.configure(text="تمت مزامنة الصورة المصغّرة مع العنوان.", fg=SUCCESS)
                    else:
                        preview_status.configure(text="تعذر إنشاء الصورة المخصصة؛ سيُرفع الفيديو دونها.", fg=WARN)
                decision.update({"title": title, "description": description,
                                 "privacy": selected_privacy,
                                 "thumbnail_path": thumbnail_for_upload,
                                 "rights_record": rights_record})
            request["decision"].update(decision)
            request["ready"].set()
            self._pending_review = None
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass

        def stop_worker():
            self._stop.set()
            self._save_ui_settings()
            self.status_lbl.config(text="جاري الإيقاف...", fg=ERR)
            self.countdown_var.set("جاري الإيقاف...")
            finish("cancel")

        actions = tk.Frame(footer, bg=PANEL)
        actions.grid(row=1, column=0, sticky="ew")
        Btn(actions, "تخطي هذا الفيديو", lambda: finish("skip"), "#455a64").pack(side="left")
        Btn(actions, "إيقاف العامل", stop_worker, ERR).pack(side="left", padx=8)
        Btn(actions, "موافقة ورفع", lambda: finish("approve"), SUCCESS).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", lambda: finish("skip"))
        window.bind("<Escape>", lambda _event: finish("skip"))
        window.grab_set()
        window.lift()
        title_entry.focus_set()

    # ─────────── مضخة رسائل الواجهة ───────────
    def _poll(self):
        """ينفذ كل تحديثات Tkinter من الخيط الرئيسي فقط."""
        while not self._logq.empty():
            msg, repl = self._logq.get_nowait()
            self.log_txt.configure(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            if repl and self._last_log_replace:
                self.log_txt.delete("end-2l", "end-1c")
            low = msg
            if "خطأ" in low or "فشل" in low or "رفض" in low:
                tag = "err"
            elif "تحذير" in low or "تعذر" in low or "تنبيه" in low:
                tag = "warn"
            elif "اكتملت" in low or "نجاح" in low or "تم الرفع" in low:
                tag = "ok"
            else:
                tag = "info"
            self.log_txt.insert("end", f"[{ts}] ", "ts")
            self.log_txt.insert("end", f"{msg}\n", tag)
            self.log_txt.see("end")
            self.log_txt.configure(state="disabled")
            self._last_log_replace = repl

        while not self._uiq.empty():
            event, payload = self._uiq.get_nowait()
            if event == "review_request":
                if self._stop.is_set() or payload["ready"].is_set():
                    payload["decision"].update({"action": "cancel"})
                    payload["ready"].set()
                else:
                    try:
                        self._show_upload_review(payload)
                    except Exception as error:
                        logging.exception("تعذر فتح نافذة مراجعة الفيديو")
                        payload["decision"].update({"action": "cancel"})
                        payload["ready"].set()
                        self._log(f"تعذر فتح نافذة المراجعة؛ أُعيد الفيديو إلى قائمة الانتظار: {error}")
            elif event == "progress":
                self.prog_var.set(payload)
                self.pct_lbl.config(text=f"{payload}%")
            elif event == "countdown":
                self.countdown_var.set(payload)
            elif event == "facebook_network_test_finished":
                self._facebook_network_test_running = False
                self.facebook_network_test_button.config(state="normal")
                route = payload.get("route")
                if route == "proxy":
                    message = "منفذ الوكيل يستجيب؛ اختبر صفحة المصدر للتأكد من أن Facebook متاح عبره."
                    color = WARN
                elif route:
                    route_label = {"ipv4": "IPv4", "ipv6": "IPv6", "direct": "المسار المباشر"}.get(route, route)
                    message = f"اتصال TCP بفيسبوك متاح عبر {route_label}؛ لم تُختبر صلاحية تسجيل الدخول أو الصفحة."
                    color = SUCCESS
                else:
                    timeout = payload.get("timeout", DEFAULT_NETWORK_PREFLIGHT_TIMEOUT)
                    message = (
                        f"تعذر اتصال TCP عبر {timeout} ثانية لكل مسار. افحص إن كان الإنترنت يعمل "
                        "في المتصفح أو يحجب جدار حماية Windows/مزود الخدمة Facebook."
                    )
                    color = ERR
                self.facebook_network_status_var.set(message)
                self.facebook_network_status_label.config(fg=color)
                self._log(message)
            elif event == "facebook_probe_result":
                self._finish_facebook_page_probe(payload)
            elif event == "preflight_result":
                self._preflight_running = False
                self._preflight_btn.config(state="normal")
                return_code, output = payload
                status = "فحص الجاهزية اكتمل" if return_code == 0 else "فحص الجاهزية يحتاج مراجعة"
                color = SUCCESS if return_code == 0 else WARN
                self.status_lbl.config(text=status, fg=color)
                self._show_readiness_report(return_code, output)
            elif event == "diagnostic_report_finished":
                self._diagnostic_report_running = False
                self.diagnostic_report_button.config(state="normal")
                if payload.get("path"):
                    self._log(
                        f"حُفظ التقرير التشخيصي محلياً في {payload['path']}. "
                        "راجعه قبل مشاركته؛ لا يحتوي على قيم كوكيز أو رموز OAuth."
                    )
                else:
                    self._log(
                        "تعذر إنشاء التقرير التشخيصي؛ راجع errors.log محلياً "
                        f"(نوع الخطأ: {payload.get('error_type', 'غير معروف')})."
                    )
            elif event == "page_status":
                self.page_status[payload["url"]] = payload
                try:
                    save_page_status(self.page_status)
                except OSError:
                    logging.exception("تعذر حفظ آخر حالة للصفحات")
                self._refresh_watch_tree()
            elif event == "quota":
                self.quota_var.set(f"طلبات الرفع اليوم: {payload['used']} / {payload['limit']}")
            elif event == "finished":
                self._finish_worker(payload)
        self.root.after(100, self._poll)

    # ════════════════════════════════════════════════
    # إعدادات العامل — تُقرأ قبل بدء الخيط الخلفي
    # ════════════════════════════════════════════════
    def _capture_worker_settings(self):
        try:
            min_delay = int(self.min_d.get() or 20)
            max_delay = int(self.max_d.get() or 60)
            between_videos = int(self.delay_e.get() or 10)
            interval = int(self.interval_e.get() or 10)
            daily_upload_limit = int(self.quota_e.get() or DEFAULT_DAILY_UPLOAD_LIMIT)
            max_attempts = int(self.max_att_e.get() or 3)
            min_disk = float(self.disk_e.get() or 2)
        except ValueError as error:
            raise ValueError("أدخل أرقاماً صحيحة للتأخيرات وفاصل المراقبة وحد الرفع.") from error
        if min_delay < 1 or max_delay < min_delay or between_videos < 0:
            raise ValueError("تحقق من نطاق الجدولة والتأخير بين الفيديوهات.")
        if not (1 <= interval <= 720):
            raise ValueError("فاصل المراقبة يجب أن يكون بين 1 و720 دقيقة.")
        if not (1 <= max_attempts <= 10):
            raise ValueError("محاولات الفاشل بين 1 و10.")
        if daily_upload_limit < 1:
            raise ValueError("حد الرفع اليومي يجب أن يكون رقماً موجباً.")

        if not math.isfinite(min_disk) or min_disk < 0:
            raise ValueError("قيمة مساحة القرص غير صالحة.")
        try:
            logo_scale = int(self.logo_scale_e.get() or 8)
            logo_opacity = int(self.logo_op_e.get() or 100)
            logo_cover_width = int(self.logo_cover_width_e.get() or 20)
            min_dur = int(self.min_dur_e.get() or 0)
            max_dur = int(self.max_dur_e.get() or 0)
        except ValueError as error:
            raise ValueError("أدخل أرقاماً صحيحة لإعدادات الشعار وفلاتر المدة.") from error
        if not (1 <= logo_scale <= 60) or not (5 <= logo_opacity <= 100):
            raise ValueError("حجم الشعار بين 1 و60%، والشفافية بين 5 و100%.")
        if not (10 <= logo_cover_width <= 40):
            raise ValueError("عرض بطاقة التغطية بين 10 و40% من عرض الفيديو.")
        if min_dur < 0 or max_dur < 0 or (max_dur and max_dur <= min_dur):
            raise ValueError("تحقق من نطاق فلتر المدة.")
        if self.logo_enabled_var.get() and not ffmpeg_available():
            raise ValueError("فعّلتَ تركيب الشعار لكن ffmpeg غير مثبت على الجهاز.\n"
                             "حمّله من ffmpeg.org وأضفه إلى PATH، أو عطّل الميزة.")
        logo_path = self.logo_path_var.get().strip()
        if self.logo_enabled_var.get() and not os.path.isfile(logo_path):
            raise ValueError("فعّلتَ تركيب الشعار لكن الملف غير موجود. اختر صورة صالحة؛ يفضّل PNG بخلفية شفافة.")
        content_review_enabled = self.content_review_enabled_var.get()
        dry_run = self.dry_run_var.get()
        cutter_root = self.cutter_root_var.get().strip()
        cutter_python = self.cutter_python_var.get().strip()
        review_errors = validate_content_review_settings(
            content_review_enabled and not dry_run, cutter_root, cutter_python
        )
        if review_errors:
            raise ValueError("إعدادات بوابة مراجعة المحتوى غير مكتملة:\n• " + "\n• ".join(review_errors))
        allow_public_automation = self.allow_public_automation_var.get()
        publication_errors = validate_publication_controls(
            self.priv_var.get(),
            self.sched_var.get(),
            self.review_before_upload_var.get(),
            allow_public_automation,
            dry_run=dry_run,
        )
        if publication_errors:
            raise ValueError("إعدادات النشر تحتاج تأكيداً صريحاً:\n• " + "\n• ".join(publication_errors))


        video_directory_value = self.video_directory_e.get().strip()
        client_secrets_value = self.client_secrets_e.get().strip()
        if not video_directory_value or not client_secrets_value:
            raise ValueError("حدد مجلد الفيديو وملف عميل OAuth في تبويب المسارات المحلية.")
        if "\x00" in video_directory_value or "\x00" in client_secrets_value:
            raise ValueError("أحد المسارات يحتوي محرفاً غير صالح.")
        video_directory = _app_path(video_directory_value)
        client_secrets_file = _app_path(client_secrets_value)

        try:
            facebook_network_preflight = config.getboolean("WATCH", "NETWORK_PREFLIGHT", fallback=True)
        except (ValueError, configparser.Error):
            facebook_network_preflight = True
        return {
            "account": self.acc_var.get().strip(),
            "video_directory": video_directory,
            "client_secrets_file": client_secrets_file,
            "headless": self.headless_var.get(),
            "remove_tags": self.remove_tags_var.get(),
            "extra_description": self.desc_e.get("1.0", "end").strip(),
            "schedule_enabled": self.sched_var.get(),
            "min_delay": min_delay,
            "max_delay": max_delay,
            "privacy": self.priv_var.get(),
            "dry_run": dry_run,
            "content_review_enabled": content_review_enabled,
            "cutter_root": cutter_root,
            "allow_public_automation": allow_public_automation,

            "cutter_python": cutter_python,
            "tags": self.tags_e.get().split(","),
            "delete_after_upload": self.del_after_var.get(),
            "between_videos": between_videos,
            "interval_minutes": interval,
            "fast_scrape": self.fast_scrape_var.get(),
            "facebook_network_preflight": facebook_network_preflight,
            "daily_upload_limit": daily_upload_limit,
            "max_attempts": max_attempts,
            "min_disk_gb": min_disk,
            "telegram_enabled": self.tg_enabled_var.get(),
            "tg_token": self.tg_token_e.get(),
            "tg_chat_id": self.tg_chat_e.get(),
            "tg_errors": self.tg_errors_var.get(),
            "cookies_enabled": self.cookies_enabled_var.get(),
            "cookies_file": self.cookies_path_var.get().strip(),
            "dedupe_content": self.dedupe_var.get(),
            "ai_enabled": self.ai_enabled_var.get(),
            "ai_model": self.ai_model_var.get(),
            "ai_style": self.ai_style_var.get(),
            # ── الاستوديو ──
            "title_optimize": self.title_opt_var.get(),
            "omit_source_page_name": self.omit_source_page_name_var.get(),
            "auto_shorts": self.auto_shorts_var.get(),
            "title_prefix": self.title_prefix_e.get().strip(),
            "title_suffix": self.title_suffix_e.get().strip(),
            "auto_hashtags": self.auto_tags_var.get(),
            "credit_line": self.credit_e.val().strip(),
            "logo_enabled": self.logo_enabled_var.get(),
            "logo_path": self.logo_path_var.get().strip(),
            "logo_position": self.logo_pos_var.get(),
            "logo_scale": logo_scale,
            "logo_opacity": logo_opacity,
            "logo_cover_width": logo_cover_width,
            "logo_cover_old": self.cover_old_var.get(),
            "logo_plate": self.plate_var.get(),
            "logo_secondary_enabled": self.secondary_var.get(),
            "logo_secondary_cover_old": self.secondary_cover_var.get(),
            "playlist_id": self.playlist_e.get().strip(),
            "review_before_upload": self.review_before_upload_var.get(),
            "rights_auto_draft": self.rights_auto_draft_var.get(),
            "rights_gate_mode": self.rights_gate_var.get(),
            "rights_operator_basis": self.rights_basis_var.get(),
            "rights_operator_statement": self.rights_statement_e.get().strip(),
            "rights_operator_evidence": self.rights_evidence_e.get().strip(),
            "thumbnail_enabled": self.thumbnail_var.get(),
            "shorts_cut_enabled": self.shorts_cut_var.get(),
            "min_duration": min_dur,
            "max_duration": max_dur,
            "category": YT_CATEGORIES.get(self.cat_var.get(), "25"),
        }

    def _apply_runtime_paths(self, settings):
        global VIDEO_DIRECTORY, CLIENT_SECRETS_FILE
        video_directory = _app_path(settings.get("video_directory") or VIDEO_DIRECTORY)
        client_secrets_file = _app_path(settings.get("client_secrets_file") or CLIENT_SECRETS_FILE)
        try:
            os.makedirs(video_directory, exist_ok=True)
            if not os.path.isdir(video_directory) or not os.access(video_directory, os.W_OK | os.X_OK):
                raise OSError("selected video directory is not writable")
        except OSError as error:
            raise ValueError(
                "تعذر استخدام مجلد الفيديو المحدد. اختر مجلداً محلياً موجوداً أو قابلاً للإنشاء والكتابة."
            ) from error
        VIDEO_DIRECTORY = video_directory
        CLIENT_SECRETS_FILE = client_secrets_file
        settings["video_directory"] = video_directory
        settings["client_secrets_file"] = client_secrets_file
        if not config.has_section("SETTINGS"):
            config.add_section("SETTINGS")
        config.set("SETTINGS", "VIDEO_DIRECTORY", video_directory)
        config.set("SETTINGS", "CLIENT_SECRETS_FILE", client_secrets_file)

    def _do_start(self, watch_mode):
        if self._closing:
            return
        if getattr(self, "_facebook_probe_running", False):
            messagebox.showwarning(
                "اختبار Facebook يعمل",
                "انتظر انتهاء اختبار الصفحة قبل بدء المراقبة.",
                parent=self.root,
            )
            return
        if self._thread is not None and self._thread.is_alive():
            self._log("العامل الحالي ما زال يعمل؛ لن أبدأ عملية متزامنة ثانية.")
            return
        pages = [p for p in self._get_pages() if p.get("enabled", True)]
        if not pages:
            messagebox.showerror("خطأ", "أضف صفحة مفعّلة واحدة على الأقل")
            return
        try:
            settings = self._capture_worker_settings()
        except ValueError as error:
            messagebox.showerror("خطأ في الإعدادات", str(error))
            return
        if not settings["account"]:
            messagebox.showerror("خطأ", "اختر حساب YouTube نشطاً أولاً")
            return
        rights_setup_errors = automatic_rights_preflight_errors(settings)
        if rights_setup_errors:
            self._log("أُوقف التشغيل قبل الاتصال بـFacebook أو تنزيل أي فيديو بسبب نقص إعدادات بوابة الحقوق:")
            for error in rights_setup_errors:
                self._log(f"• {error}")
            self._log("أكمل الإقرار الحقيقي ومرجع الإثبات في الاستوديو > حقوق النشر، أو استخدم المراجعة اليدوية لكل فيديو؛ لا تُدخل إقراراً غير صحيح.")
            self.status_lbl.config(text="التشغيل الآلي متوقف: أكمل إعداد بوابة الحقوق", fg=WARN)
            self.nb.select(self.t_log)
            return
        if self._save_ui_settings() is False:
            self.status_lbl.config(text="لم يبدأ التشغيل: تعذر حفظ الإعدادات", fg=ERR)
            return
        try:
            self._apply_runtime_paths(settings)
        except ValueError as error:
            messagebox.showerror("مسار غير صالح", str(error), parent=self.root)
            self._log(f"لم يبدأ التشغيل بسبب إعداد مسار غير صالح: {error}")
            return
        self._stop.clear()
        self._watching = watch_mode
        self.start_once_btn.config(state="disabled")
        self.watch_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        mode_txt = "المراقبة المستمرة تعمل…" if watch_mode else "تشغيل مرة واحدة…"
        self.status_lbl.config(text=mode_txt, fg=SUCCESS)
        self.nb.select(self.t_log if not watch_mode else self.t_watch)
        self._thread = threading.Thread(
            target=self._worker, args=(pages, settings, watch_mode),
            daemon=False, name="fb-youtube-watch-worker",
        )
        self._thread.start()

    # ════════════════════════════════════════════════
    # منطق العامل الخلفي — لا يلمس Tkinter إطلاقاً
    # ════════════════════════════════════════════════
    def _worker(self, pages, settings, watch_mode):
        up_total, fail_total = 0, 0
        network_error_total = 0
        cycle_error_total = 0
        fatal_error = None
        run_lock = None
        try:
            run_lock = AutomationRunLock(os.path.join(APP_DIRECTORY, ".automation.lock")).acquire()
            if settings.get("cli_mode"):
                yt, error = get_youtube(settings["account"], allow_interactive=False)
            else:
                yt, error = get_youtube(settings["account"])
            if not yt:
                fatal_error = error or "تعذر ربط حساب YouTube."
                self._log(f"خطأ: {fatal_error}")
                return
            self._log("متصل بـ YouTube API")
            quota = QuotaManager(settings["daily_upload_limit"], self._log)
            ctx = {"yt": yt, "quota": quota, "account": settings["account"]}
            self._uiq.put(("quota", {"used": quota.used, "limit": quota.daily_limit}))

            if not watch_mode:
                up, fail = self._run_cycle(ctx, pages, settings, watch_mode=False)
                up_total, fail_total = up, fail
                network_error_total = int(bool(ctx.pop("facebook_network_unavailable", False)))
            else:
                self._log(f"بدأت المراقبة المستمرة — الفحص كل {settings['interval_minutes']} دقيقة")
                send_telegram(settings, "بدأت المراقبة المستمرة للريلز.", self._log)
                cycle, consec_errors = 0, 0
                while not self._stop.is_set():
                    cycle += 1
                    try:
                        self._log(f"━━ دورة المراقبة #{cycle} ━━")
                        up, fail = self._run_cycle(ctx, pages, settings, watch_mode=True)
                        up_total += up; fail_total += fail
                        network_unavailable = bool(ctx.pop("facebook_network_unavailable", False))
                        if network_unavailable:
                            network_error_total += 1
                        if ctx.pop("facebook_auth_required", False):
                            self._log("المراقبة معلّقة بانتظار جلسة Facebook صالحة؛ لن تُعاد محاولات تسجيل الدخول تلقائياً.")
                            if settings.get("tg_errors"):
                                try:
                                    send_telegram(settings, "المراقبة معلّقة: أكمل التحقق من Facebook، حدّث ملف cookies واضغط حفظ إعدادات Facebook. ستُستأنف المراقبة تلقائياً عند اكتشاف جلسة صالحة.", self._log)
                                except Exception:
                                    logging.exception("تعذر إرسال تنبيه انتهاء جلسة Facebook")
                            previous_cookie_fingerprint = ctx.pop("facebook_cookie_fingerprint", None)
                            if self._wait_for_facebook_cookie_refresh(settings, previous_cookie_fingerprint):
                                self._log("اكتُشف ملف cookies محدّث؛ ستُستأنف المراقبة في الدورة التالية.")
                                continue
                            break
                        if ctx.get("channel_limit_reached"):
                            self._log("توقف الرفع عند حد القناة اليومي؛ أعد تشغيل البرنامج بعد تجدد الحد.")
                            break
                        self.stats["cycles"] = self.stats.get("cycles", 0) + 1
                        if network_unavailable:
                            consec_errors += 1
                            wait_min = min(60, settings["interval_minutes"] * (2 ** consec_errors))
                            self._log(f"تعذر فحص بعض صفحات المصدر؛ إعادة الفحص بعد {wait_min} دقيقة.")
                        else:
                            consec_errors = 0
                            wait_min = settings["interval_minutes"]
                    except Exception as error:
                        consec_errors += 1
                        cycle_error_total += 1
                        logging.exception("خطأ غير متوقع في دورة المراقبة #%s", cycle)
                        wait_min = min(60, settings["interval_minutes"] * (2 ** consec_errors))
                        self._log(f"خطأ في الدورة: {error} — إعادة المحاولة بعد {wait_min} دقيقة")
                        if settings.get("tg_errors"):
                            send_telegram(settings, f"خطأ في دورة المراقبة: {error}", self._log)
                    if self._stop.is_set():
                        break
                    self._maybe_daily_summary(settings)
                    self._countdown_wait(wait_min * 60, "الفحص القادم بعد")
                self._log("توقفت المراقبة المستمرة.")
        except AutomationAlreadyRunning as error:
            fatal_error = "يوجد تشغيل آخر للبرنامج؛ لن تبدأ عملية ثانية."
            self._log(f"{fatal_error} ({error})")
        except Exception as error:
            logging.exception("توقف عامل المراقبة بصورة غير متوقعة")
            fatal_error = f"خطأ غير متوقع: {error}"
            self._log(f"{fatal_error}")
            if settings.get("tg_errors"):
                try:
                    send_telegram(settings, f"توقف البرنامج بخطأ: {error}", self._log)
                except Exception:
                    pass
        finally:
            if run_lock is not None:
                try:
                    run_lock.release()
                except Exception:
                    logging.exception("تعذر تحرير قفل التشغيل")
            self.stats["total_uploaded"] += up_total
            self.stats["total_failed"] += fail_total
            try:
                save_stats(self.stats)
            except OSError:
                logging.exception("تعذر حفظ الإحصاءات")
            try:
                pending_total = len(load_failed_queue())
            except Exception:
                logging.exception("تعذر قراءة عدد العناصر المعلقة")
                pending_total = None
            pending_label = pending_total if pending_total is not None else "غير معروف"
            self._log(
                f"انتهى! مرفوع: {up_total}, فشل فعلي: {fail_total}, "
                f"تعذر اتصال الشبكة: {network_error_total}, أخطاء دورات: {cycle_error_total}, "
                f"قيد الانتظار/المراجعة: {pending_label}"
            )
            self._uiq.put(("finished", {
                "uploaded": up_total, "failed": fail_total, "pending": pending_total,
                "network_errors": network_error_total, "cycle_errors": cycle_error_total,
                "fatal": fatal_error, "watch": watch_mode,
            }))

    def _run_cycle(self, ctx, pages, settings, watch_mode):
        """دورة واحدة: سحب الروابط الجديدة + إعادة محاولة الفاشلين + الرفع.

        ctx = {"yt": اتصال يوتيوب, "quota": العداد المشترك للمشروع, "account": اسم الحساب}.
        """
        up_count, fail_count, dry_count = 0, 0, 0
        cycle_started = time.time()
        ctx["facebook_network_unavailable"] = False
        ctx["facebook_auth_required"] = False
        uploaded_ids = load_uploaded_ids()
        receipts = load_upload_receipts()
        receipt_source_ids = set(receipts)
        for source_id in receipt_source_ids - uploaded_ids:
            try:
                save_uploaded_id(source_id)
            except (OSError, RuntimeError, ValueError):
                logging.exception("Upload receipt is authoritative; legacy uploaded-id sync failed")
            uploaded_ids.add(source_id)
        seen_ids = load_seen_ids()
        stored_queue = load_failed_queue()
        recovered_queue, recovered_count = recover_interrupted_uploads(stored_queue, uploaded_ids)
        recovered_queue, rights_released_count = release_rights_holds_for_auto_mode(
            recovered_queue, settings
        )
        if recovered_queue != stored_queue:
            save_failed_queue(recovered_queue)
        if recovered_count:
            self._log(f"استُعيدت {recovered_count} محاولة رفع غير مكتملة وعُلّقت للتحقق اليدوي؛ لن تُعاد تلقائياً منعاً للتكرار.")
        if rights_released_count:
            self._log(f"أُعيدت {rights_released_count} مواد محجوبة سابقاً بالحقوق إلى قائمة المعالجة؛ ستُفحص للتكرار قبل الرفع.")
        dropped_uploaded = len({str(it.get("id", "")).strip() for it in stored_queue if str(it.get("id", "")).strip()}) - len(
            {str(it.get("id", "")).strip() for it in recovered_queue if str(it.get("id", "")).strip()}
        )
        if dropped_uploaded > 0:
            self._log(f"تخطّينا {dropped_uploaded} عنصراً من قائمة الإعادة لأنه مسجّل مسبقاً كمرفوع؛ لن يُعاد رفعه.")
        failed_q, held_duplicate_q = split_duplicate_review_queue(
            prune_failed_queue(recovered_queue, settings.get("failed_max_age_days", FAILED_QUEUE_MAX_AGE_DAYS)),
            settings.get("review_before_upload", False)
        )
        queued_ids = {it["id"] for it in failed_q + held_duplicate_q}
        cycle_started = time.time()
        cookies = cookies_arg(settings)
        ctx["facebook_cookie_fingerprint"] = facebook_cookie_fingerprint(cookies)
        if settings.get("cookies_enabled"):
            if not cookies:
                self._log("كوكيز Facebook مفعّلة لكن الملف المحدد غير موجود أو غير قابل للقراءة.")
            else:
                cookie_status = inspect_facebook_cookies(cookies)
                if cookie_status["has_active_session_pair"]:
                    self._log("ملف Facebook يحتوي مبدئياً على c_user وxs؛ صلاحية الجلسة لا تُؤكَّد إلا عند فتح الصفحة.")
                else:
                    self._log("الملف لا يحتوي على زوج جلسة Facebook نشط c_user/xs؛ أعد تصديره من جلسة مسجّلة.")
        else:
            self._log("استخدام كوكيز Facebook معطّل؛ فعّله إذا كانت صفحة المصدر تتطلب تسجيل الدخول.")

        if settings.get("facebook_network_preflight", False) and not _facebook_network_preflight(self._log):
            ctx["facebook_network_unavailable"] = True
            for page in pages:
                self._emit_page_status(page, 0, 0, "تعذر الاتصال بفيسبوك")
            retry_items = [dict(item, retry=True) for item in failed_q]
            failed_q = held_duplicate_q
            self._requeue_unprocessed(failed_q, retry_items)
            save_failed_queue(failed_q)
            self._log("أُجّلت الدورة قبل السحب بسبب الشبكة؛ لم يُنزّل أو يُرفع أي فيديو، وبقيت العناصر معلّقة للمحاولة التالية.")
            return 0, 0

        # ─── 1) سحب الروابط من كل الصفحات (متصفح واحد يُعاد استخدامه) ───
        fresh = []
        scrape_failures = 0
        scrape_network_failures = 0
        network_failures = False
        driver = None
        for page in pages:
            if self._stop.is_set():
                break
            label = page.get("name") or page["url"]
            links = None
            if settings["fast_scrape"]:
                try:
                    links = scrape_links_fast(_safe_normalize_channel_url(page["url"]), page["limit"], self._log, cookies)
                    if links:
                        self._log(f"سحب سريع: {len(links)} رابط من {label}")
                except FacebookNetworkUnavailable as error:
                    scrape_failures += 1
                    scrape_network_failures += 1
                    network_failures = True
                    logging.info("Facebook network unavailable during fast scraping for %s: %s", page["url"], error)
                    self._emit_page_status(page, 0, 0, "تعذر الاتصال بفيسبوك")
                    continue
                except Exception as error:
                    logging.info("السحب السريع فشل لـ %s: %s", page["url"], error)
                    links = None
            if links is None:
                if driver is None:
                    driver = init_driver(settings["headless"], self._log)
                    if driver is not None and cookies:
                        try:
                            load_cookies_to_driver(
                                driver, cookies, self._log,
                                page_load_timeout=_facebook_page_load_timeout(),
                            )
                        except Exception as error:
                            logging.warning("تعذر حقن الكوكيز: %s", error)
                if driver is None:
                    scrape_failures += 1
                    self._emit_page_status(page, 0, 0, "تعذر تشغيل المتصفح")
                    continue
                try:
                    self._log(f"سحب عبر المتصفح: {label}")
                    links = get_reels(driver, _safe_normalize_channel_url(page["url"]), page["scroll"], page["limit"], self._log)
                except FacebookLoginRequired as error:
                    scrape_failures += 1
                    ctx["facebook_auth_required"] = True
                    self._emit_page_status(page, 0, 0, "يلزم تسجيل الدخول إلى Facebook")
                    self._log(f"توقّف سحب المصادر عند {label}: أكمل تسجيل الدخول أو التحقق يدوياً ثم أعد التشغيل. ({error})")
                    break
                except FacebookNetworkUnavailable as error:
                    scrape_failures += 1
                    scrape_network_failures += 1
                    network_failures = True
                    logging.info("Facebook network unavailable for %s: %s", page["url"], error)
                    self._emit_page_status(page, 0, 0, "تعذر الاتصال بفيسبوك")
                    continue
                except WebDriverException as error:
                    logging.warning("تعطل المتصفح، إعادة تشغيله: %s", error)
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = init_driver(settings["headless"], self._log)
                    if driver is None:
                        scrape_failures += 1
                        self._emit_page_status(page, 0, 0, "تعطل المتصفح")
                        continue
                    if cookies:
                        try:
                            load_cookies_to_driver(
                                driver, cookies, self._log,
                                page_load_timeout=_facebook_page_load_timeout(),
                            )
                        except Exception:
                            pass
                    try:
                        links = get_reels(driver, _safe_normalize_channel_url(page["url"]), page["scroll"], page["limit"], self._log)
                    except FacebookLoginRequired as error:
                        scrape_failures += 1
                        ctx["facebook_auth_required"] = True
                        self._emit_page_status(page, 0, 0, "يلزم تسجيل الدخول إلى Facebook")
                        self._log(f"توقّف سحب المصادر عند {label}: أكمل تسجيل الدخول أو التحقق يدوياً ثم أعد التشغيل. ({error})")
                        break
                    except FacebookNetworkUnavailable as retry_error:
                        scrape_failures += 1
                        scrape_network_failures += 1
                        network_failures = True
                        logging.info("Facebook retry network failure for %s: %s", page["url"], retry_error)
                        self._emit_page_status(page, 0, 0, "تعذر الاتصال بفيسبوك")
                        continue
                    except Exception as retry_error:
                        scrape_failures += 1
                        transient_error = is_transient_facebook_error(retry_error)
                        network_failures = network_failures or transient_error
                        scrape_network_failures += int(transient_error)
                        logging.exception("فشل استخراج الروابط بعد إعادة تشغيل المتصفح")
                        self._emit_page_status(page, 0, 0, f"فشل بعد إعادة تشغيل المتصفح: {retry_error}")
                        continue
                except Exception as error:
                    scrape_failures += 1
                    transient_error = is_transient_facebook_error(error)
                    network_failures = network_failures or transient_error
                    scrape_network_failures += int(transient_error)
                    logging.exception("خطأ في استخراج الروابط")
                    self._emit_page_status(page, 0, 0, f"خطأ: {error}")
                    continue
            new_items = filter_new_reels(links or [], uploaded_ids, seen_ids, queued_ids)
            for it in new_items:
                it["page"] = page.get("name", "")
                it["attempts"] = 0
            fresh.extend(new_items)
            queued_ids.update(it["id"] for it in new_items)  # لا تُكرر داخل الدورة نفسها
            if links is None:
                scrape_failures += 1
                page_status = "تعذر استخراج روابط الفيديو"
                self._log(f"تعذر استخراج روابط فيديو من {label}؛ تحقق من رابط الصفحة أو تسجيل الدخول/الكوكيز.")
            elif not links:
                scrape_failures += 1
                page_status = "لم تُرجع الصفحة روابط فيديو؛ يلزم التحقق"
                self._log(f"لم تُرجع صفحة {label} أي روابط فيديو؛ سُجّلت الدورة كمشكلة سحب بدلاً من نجاح زائف.")
            elif not new_items:
                page_status = "لا توجد روابط جديدة"
                self._log(f"عُثر على {len(links)} رابطاً في {label}، لكن لا يوجد عنصر جديد غير مرفوع أو معلّق.")
            else:
                page_status = "تم الفحص"
            self._emit_page_status(page, len(links or []), len(new_items), page_status)
        if ctx.get("facebook_auth_required"):
            self._safe_quit_driver(driver)
            driver = None
            pending = [dict(item, retry=True) for item in failed_q] + fresh
            failed_q = held_duplicate_q
            self._requeue_unprocessed(failed_q, pending)
            save_failed_queue(failed_q)
            ctx["facebook_network_unavailable"] = False
            self._log("أُوقفت الدورة قبل أي تنزيل أو رفع بسبب جلسة Facebook؛ حُفظت العناصر المكتشفة لإكمالها بعد تحديث الجلسة.")
            self._safe_quit_driver(driver)
            return up_count, fail_count + max(1, scrape_failures)
        if scrape_failures:
            fail_count += max(0, scrape_failures - scrape_network_failures)
            ctx["facebook_network_unavailable"] = network_failures
            self._log(f"تعذر فحص {scrape_failures} صفحة مصدر؛ اعتُبرت هذه الدورة متعثرة بدلاً من تسجيلها كنجاح فارغ.")

        # ─── 2) قائمة المعالجة: الفاشلون أولاً ثم الجدد ───
        history_pages = self._history_page_index()
        for stale in failed_q:
            if not str(stale.get("page") or "").strip() and not str(stale.get("title_hint") or "").strip():
                known_page = history_pages.get(str(stale.get("id") or "").strip(), "")
                if known_page:
                    stale["page"] = known_page
        process_list = [dict(it, retry=True) for it in failed_q] + fresh
        failed_q = held_duplicate_q
        total = len(process_list)
        if total == 0:
            if held_duplicate_q:
                self._log(f"{len(held_duplicate_q)} عنصر ينتظر تفعيل المراجعة اليدوية؛ لم يُرفع ولم يُحذف من السجل.")
            else:
                self._log("لا توجد فيديوهات جديدة — في انتظار الدورة القادمة.")
            save_failed_queue(failed_q)
            self._safe_quit_driver(driver)
            return up_count, fail_count
        self._log(f"هذه الدورة: {total} فيديو للمعالجة ({len(fresh)} جديد + {total - len(fresh)} إعادة محاولة)")
        gate_mode = str(settings.get("rights_gate_mode", "strict")).strip() or "strict"
        if gate_mode == "operator_confirmed":
            self._log("بوابة الحقوق: إقرار المشغل مفعّل — الرفع الآلي مستمر.")
        else:
            self._log("بوابة الحقوق: strict — لا يُستخدم إقرار عام؛ يلزم سجل حقوق صالح خاص بهذا الفيديو أو استكمال المراجعة اليدوية قبل رفعه.")

        channel_videos = []
        channel_index_warning = ""
        if settings.get("dedupe_content", True):
            try:
                channel_videos = fetch_channel_video_index(
                    ctx["yt"], ctx.get("account", ""), log_cb=self._log
                )
                if any(video.get("_stale_index") for video in channel_videos):
                    channel_index_warning = "تعذر تحديث سجل القناة؛ استُخدمت نسخة قديمة وقد تفوتها فيديوهات أحدث."
            except Exception as error:
                logging.warning("تعذر فحص سجل فيديوهات القناة: %s", error)
                channel_index_warning = "تعذر التحقق من سجل القناة عبر YouTube Data API."
                self._log("تعذر فحص سجل القناة.")

        if should_pause_for_unverified_channel(
            settings.get("dedupe_content", True),
            settings.get("review_before_upload", False),
            not channel_index_warning,
        ):
            self._log("أُوقفت الدفعة الآلية: لم أستطع التحقق من سجل القناة؛ أُعيدت العناصر إلى قائمة الانتظار منعاً لرفع مكرر غير مفحوص.")
            self._requeue_unprocessed(failed_q, process_list)
            save_failed_queue(failed_q)
            self._safe_quit_driver(driver)
            return up_count, fail_count + 1

        for index, item in enumerate(process_list, start=1):
            if self._stop.is_set():
                self._requeue_unprocessed(failed_q, process_list[index - 1:])
                break
            if disk_free_gb() < settings["min_disk_gb"]:
                self._log(f"مساحة القرص أقل من {settings['min_disk_gb']}GB — أُوقفت الدورة حمايةً للنظام.")
                self._requeue_unprocessed(failed_q, process_list[index - 1:])
                break

            self._uiq.put(("progress", int(((index - 1) / total) * 100)))
            tag = "إعادة" if item.get("retry") else ""
            self._log(f"{tag} [{index}/{total}] {item['url']}")

            video_data = None
            try:
                video_data = download_video(
                    item["url"], item.get("page", ""), self._log,
                    MAX_RETRIES, settings["remove_tags"], cookies,
                    self._stop, settings.get("omit_source_page_name", True),
                    output_dir=settings.get("video_directory", VIDEO_DIRECTORY),
                    title_hint=item.get("title_hint", ""),
                    page_title_fetcher=(
                        (lambda u: self._rescue_title_via_driver(driver, u))
                        if driver is not None else None
                    ),
                )
            except StopRequested:
                self._log("أُوقف التنزيل الجاري بناءً على طلب المستخدم.")
                self._requeue_unprocessed(failed_q, process_list[index - 1:])
                save_failed_queue(failed_q)
                break
            except PipelineFailure as failure:
                self._log(str(failure))
                if failure.code == "facebook_auth_required":
                    ctx["facebook_auth_required"] = True
                    self._requeue_unprocessed(failed_q, process_list[index - 1:])
                    save_failed_queue(failed_q)
                    break
                fail_count += 1
                self._handle_failure(
                    failed_q, seen_ids, item, failure.code, settings,
                    failure_details=failure,
                )
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "status": failure.code,
                    "error": str(failure), "failure_stage": failure.stage,
                    "failure_cause": failure.cause, "next_action": failure.action,
                })
                continue
            if not video_data:
                failure = PipelineFailure(
                    "تنزيل الفيديو", "download_missing_output",
                    "لم يُرجع yt-dlp ملفاً صالحاً.",
                    "تحقق من رابط Facebook والجلسة والاتصال ثم أعد المحاولة.",
                    retryable=True,
                )
                self._log(str(failure))
                fail_count += 1
                self._handle_failure(
                    failed_q, seen_ids, item, failure.code, settings,
                    failure_details=failure,
                )
                continue
            self.stats["total_downloaded"] = self.stats.get("total_downloaded", 0) + 1

            _ok_media, _media_info = validate_media(
                video_data["file"], expected_duration=video_data.get("duration")
            )
            if not _ok_media:
                failure = PipelineFailure(
                    "فحص سلامة الفيديو",
                    str(_media_info.get("error", "invalid_media")),
                    str(_media_info.get("cause", "تعذر إثبات سلامة الملف.")),
                    str(_media_info.get("action", "أعد التنزيل ثم أعد الفحص.")),
                    retryable=True,
                )
                self._log(str(failure))
                self._cleanup_file(video_data)
                fail_count += 1
                self._handle_failure(
                    failed_q, seen_ids, item, failure.code, settings,
                    failure_details=failure,
                )
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "status": failure.code,
                    "error": str(failure), "failure_stage": failure.stage,
                    "failure_cause": failure.cause, "next_action": failure.action,
                })
                continue

            media_hash = ""
            if settings.get("dedupe_content", True):
                try:
                    media_hash = file_sha256(video_data["file"])
                    video_data["source_sha256"] = media_hash
                except OSError as error:
                    logging.warning("تعذر حساب بصمة المقطع %s: %s", video_data.get("file"), error)
                if media_hash and is_known_media_hash(media_hash):
                    self._log("هذا ملف مطابق تماماً لفيديو سبق رفعه؛ أُلغي الرفع.")
                    self._cleanup_file(video_data)
                    save_seen_id(item["id"]); seen_ids.add(item["id"])
                    append_history({"source_id": item["id"], "url": item["url"],
                                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                                    "status": "skipped", "error": "تطابق SHA-256 مع فيديو مرفوع سابقاً"})
                    continue
                try:
                    frame_fingerprint = extract_video_fingerprint(
                        video_data["file"], video_data.get("duration")
                    )
                    video_data["frame_fingerprint"] = frame_fingerprint
                    frame_matches = find_video_fingerprint_matches(
                        frame_fingerprint, load_state().get("video_frame_fingerprints", [])
                    )
                    if frame_matches:
                        self._log("عُثر على تشابه بصري مع فيديو رفعه هذا المشروع سابقاً؛ ستُطلب مراجعة بشرية.")
                        video_data["visual_duplicate_matches"] = frame_matches
                except Exception as error:
                    logging.warning("تعذر استخراج بصمة الإطارات: %s", error)
                    video_data["frame_fingerprint"] = None

            # ─── فلتر المدة ───
            ok_dur, why = duration_ok(video_data.get("duration", 0),
                                      settings["min_duration"], settings["max_duration"])
            if not ok_dur:
                self._log(f"تخطي ({why}): {video_data['title'][:40]}")
                self._cleanup_file(video_data)
                save_seen_id(item["id"]); seen_ids.add(item["id"])
                append_history({"source_id": item["id"], "url": item["url"],
                                "page": item.get("page", ""), "status": "skipped",
                                "error": f"فلتر المدة: {why}"})
                continue

            # ─── مقارنة الملف والعنوان بتاريخ القناة ───
            duplicate_matches = list(video_data.get("visual_duplicate_matches", []))
            if settings.get("dedupe_content", True):
                if is_duplicate_content(video_data.get("title", "")):
                    duplicate_matches.append({
                        "video_id": "", "title": "عنوان مشابه رُفع سابقاً بواسطة هذا المشروع",
                        "published_at": "", "duration": None, "similarity": 1.0,
                        "duration_delta": None,
                    })
                duplicate_matches.extend(find_channel_title_matches(
                    video_data.get("title", ""), video_data.get("duration"), channel_videos
                ))
                unique_matches = {}
                for match in duplicate_matches:
                    key = match.get("video_id") or match.get("title", "")
                    unique_matches[key] = match
                duplicate_matches = list(unique_matches.values())
                if duplicate_matches:
                    if settings.get("review_before_upload", False):
                        video_data["duplicate_matches"] = duplicate_matches
                        self._log(f"عُثر على {len(duplicate_matches)} عنوان مشابه في القناة؛ راجعه قبل الرفع.")
                    else:
                        self._log(f"احتمال تكرار العنوان ({video_data['title'][:40]}). حُفظ للمراجعة اليدوية؛ لم يُرفع ولم يُسجّل كمكتمل.")
                        self._cleanup_file(video_data)
                        failed_q.append({
                            "id": item["id"], "url": item["url"],
                            "page": item.get("page", ""),
                            "attempts": item.get("attempts", 0),
                            "last_error": "duplicate_review_required",
                        })
                        append_history({"source_id": item["id"], "url": item["url"],
                                        "page": item.get("page", ""), "title": video_data["title"],
                                        "status": "duplicate_review_required",
                                        "duplicate_video_ids": [match.get("video_id") for match in duplicate_matches if match.get("video_id")],
                                        "error": "عنوان مشابه؛ ينتظر مراجعة بشرية"})
                        continue

            rights_manifest_path = settings.get("rights_manifest_file") or RIGHTS_MANIFEST_FILE
            rights_manifest_warning = ""
            try:
                rights_record = get_rights_record(item["id"], rights_manifest_path)
            except Exception as error:
                logging.warning("تعذر قراءة سجل الحقوق: %s", error)
                rights_record = None
                rights_manifest_warning = "تعذر قراءة rights_manifest.json؛ أصلح الملف قبل الرفع."

            rights_errors = []
            transformations = []
            if settings.get("logo_enabled"):
                transformations.append("إضافة شعار القناة على الفيديو")
            if settings.get("shorts_cut_enabled"):
                transformations.append("إعادة قص المشاهد وتنسيقها")
            if settings.get("auto_shorts"):
                transformations.append("توليد نسخة عمودية مقصوصة")
            if settings.get("ai_enabled"):
                transformations.append("إعادة صياغة العنوان والوصف تحريرياً بالذكاء الاصطناعي")
            operator_gate = str(settings.get("rights_gate_mode", "strict")).strip() == "operator_confirmed"
            if operator_gate and not rights_manifest_warning \
                    and rights_record_errors(rights_record, bool(duplicate_matches)):
                if duplicate_matches:
                    self._log("الإقرار العام في الإعدادات لا يحسم تشابه هذا الفيديو؛ يلزم توثيق الفرق لكل فيديو في نافذة المراجعة.")
                else:
                    try:
                        operator_record = build_operator_rights_record(
                            item.get("url", ""),
                            transformations,
                            basis=str(settings.get("rights_operator_basis", "owned")).strip(),
                            statement=str(settings.get("rights_operator_statement", "")).strip(),
                            evidence_reference=str(settings.get("rights_operator_evidence", "")).strip(),
                        )
                    except ValueError as error:
                        self._log(f"إقرار المشغل غير مكتمل: {error}؛ سيُوقف الرفع حتى تصحيحه.")
                    else:
                        operator_errors = rights_record_errors(operator_record, False)
                        if operator_errors:
                            self._log("إقرار المشغل غير مكتمل: " + "؛ ".join(operator_errors) + "؛ سيُوقف الرفع حتى تصحيحه.")
                        else:
                            try:
                                save_rights_record(item["id"], operator_record, rights_manifest_path)
                            except Exception as error:
                                logging.warning("تعذر حفظ سجل الحقوق الآلي: %s", error)
                            else:
                                rights_record = operator_record
                                self._log("يُرفع آلياً بناءً على إقرار المشغل المحفوظ في config.ini (RIGHTS_GATE_MODE=operator_confirmed).")
            elif rights_record is None and not rights_manifest_warning and not duplicate_matches \
                    and settings.get("rights_auto_draft", True):
                auto_record = build_auto_rights_record(item.get("url", ""), transformations)
                if auto_record is not None:
                    try:
                        save_rights_record(item["id"], auto_record, rights_manifest_path)
                    except Exception as error:
                        logging.warning("تعذر حفظ مسودة الحقوق الآلية: %s", error)
                    else:
                        rights_record = auto_record
                        self._log("حُفظت مسودة توثيق غير معتمدة؛ لا تثبت الملكية أو الإذن.")

            if not settings.get("review_before_upload", False):
                rights_errors = rights_record_errors(rights_record, bool(duplicate_matches))
                if rights_manifest_warning or rights_errors:
                    reason = rights_manifest_warning or " | ".join(rights_errors)
                    gate_mode = str(settings.get("rights_gate_mode", "strict")).strip() or "strict"
                    self._log(f"أُوقف الرفع الآلي (بوابة الحقوق: {gate_mode}): {reason}.")
                    if gate_mode == "operator_confirmed":
                        self._log("الإقرار غير مكتمل: عبّئ «إقرار المشغل» في قسم حقوق النشر (لا يقل عن 25 حرفاً)، ثم احفظ الإعدادات وأعد التشغيل.")
                    self._cleanup_file(video_data)
                    failed_q.append({
                        "id": item["id"], "url": item["url"],
                        "page": item.get("page", ""),
                        "attempts": item.get("attempts", 0),
                        "last_error": "rights_review_required",
                    })
                    append_history({"source_id": item["id"], "url": item["url"],
                                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                                    "status": "rights_review_required", "error": reason})
                    continue

            # ─── تحسين الذكاء الاصطناعي (Gemini) قبل القواعد ───
            configured_tags = settings.get("tags") or []
            eff_tags = [configured_tags] if isinstance(configured_tags, str) else list(configured_tags)
            # العنوان الأصلي من فيسبوك يُنقل كما هو (بعد التنظيف) ولا يعيد Gemini
            # صياغته؛ الذكاء الاصطناعي يحسّن الوصف والوسوم فقط بطلب صريح من المشغل.
            if settings.get("ai_enabled"):
                ai_meta = self._ai_metadata(item, video_data, settings)
                if ai_meta:
                    if ai_meta.get("description"):
                        video_data["desc"] = ai_meta["description"]
                    for t in ai_meta.get("tags", []):
                        if t not in eff_tags:
                            eff_tags.append(t)
            eff_tags = normalize_youtube_tags(eff_tags, self._log)

            # ─── تحسين العنوان وبناء الوصف ───
            if settings["title_optimize"]:
                video_data["title"] = optimize_title(
                    video_data["title"], item.get("page", ""),
                    duration=video_data.get("duration", 0),
                    prefix=settings["title_prefix"], suffix=settings["title_suffix"],
                    auto_shorts=settings["auto_shorts"],
                    remove_hashtags=settings["remove_tags"])
            source_names = list(video_data.get("source_names") or [])
            if item.get("page"):
                source_names.insert(0, item["page"])
            video_data["title"] = prepare_public_title(
                video_data.get("title", ""),
                source_names,
                remove_hashtags=settings["remove_tags"],
                omit_source_page_name=settings.get("omit_source_page_name", True),
            )
            self._log(f"العنوان النهائي: {video_data['title'][:60]}")
            source_page = video_data.get("source_page") or item.get("page", "")
            source_url = video_data.get("source_url") or item.get("url", "")
            video_data["desc"] = ensure_source_attribution(
                video_data.get("desc", ""), source_page, source_url
            )
            video_data["desc"] = build_description(
                video_data["desc"], settings["extra_description"],
                video_data["title"], eff_tags,
                auto_hashtags=settings["auto_hashtags"],
                credit_line=settings["credit_line"])

            # ─── تركيب الشعار ───
            branded_path = None
            if settings["logo_enabled"]:
                branded_path = apply_logo_overlay(
                    video_data["file"], settings["logo_path"],
                    position_name=settings["logo_position"],
                    scale_pct=settings["logo_scale"],
                    opacity=settings["logo_opacity"],
                    cover_old=settings["logo_cover_old"],
                    plate=settings.get("logo_plate", True),
                    landscape_cover_width_pct=settings["logo_cover_width"],
                    secondary_enabled=settings.get("logo_secondary_enabled", False),
                    secondary_cover_old=settings.get("logo_secondary_cover_old", True),
                    log_cb=self._log)
                if branded_path:
                    video_data["original_file"] = video_data["file"]
                    video_data["file"] = branded_path
                else:
                    self._log("تعذر تجهيز بطاقة الشعار؛ أُجّل المقطع ولن يُرفع بلا شعار.")
                    fail_count += 1
                    self._cleanup_file(video_data)
                    failed_q.append({
                        "id": item["id"], "url": item["url"],
                        "page": item.get("page", ""),
                        "attempts": item.get("attempts", 0),
                        "last_error": "تعذر تركيب بطاقة الشعار",
                    })
                    self._requeue_unprocessed(failed_q, process_list[index:])
                    break

            # ─── تنظيف المقطع: faststart + إزالة البيانات الوصفية المسرّبة (v4.9) ───
            cleaned = finalize_video(video_data["file"], self._log)
            if cleaned and cleaned != video_data["file"]:
                video_data["original_file"] = video_data.get("original_file") or video_data["file"]
                video_data["file"] = cleaned

            # ─── حصة الرفع مشتركة بين كل الحسابات المرتبطة بمشروع API ───
            if not settings.get("dry_run") and not ctx["quota"].can_afford():
                if watch_mode:
                    self._wait_for_quota_reset(ctx["quota"])
                else:
                    self._log("اكتمل حد الرفع اليومي — أُوقف التشغيل اليدوي. فعّل المراقبة للاستئناف بعد إعادة الضبط.")
                    self._cleanup_file(video_data, branded_path)
                    self._requeue_unprocessed(failed_q, process_list[index - 1:])
                    break

            if settings.get("content_review_enabled"):
                review_report, review_status = run_policy_review(
                    video_data, eff_tags, settings, self._log
                )
                if isinstance(review_report, dict):
                    video_data["content_safety_review"] = review_report
                else:
                    video_data.pop("content_safety_review", None)
                dry_run_review = settings.get("dry_run") and review_status == "dry_run_skipped"
                review_passed = review_status == "clear" and isinstance(review_report, dict)
                if not review_passed and not dry_run_review:
                    fail_count += 1
                    self._cleanup_file(video_data, branded_path)
                    failed_q.append({
                        "id": item["id"], "url": item["url"],
                        "page": item.get("page", ""),
                        "attempts": item.get("attempts", 0),
                        "last_error": "content_safety_review_required",
                    })
                    append_history({
                        "source_id": item["id"], "url": item["url"],
                        "page": item.get("page", ""), "title": video_data.get("title", ""),
                        "status": "content_safety_review_required",
                        "content_review_status": review_status,
                        "error": "لم يكتمل فحص سياسة المحتوى أو لم يجتز المراجعة.",
                    })
                    self._log("لم يُرفع الفيديو؛ أصلح سبب إيقاف بوابة الأمان ثم أعد فحصه.")
                    continue

            thumbnail_base_path = None
            thumbnail_path = None
            review_before_upload = bool(settings.get("review_before_upload", False))
            if settings.get("thumbnail_enabled", True):
                if review_before_upload:
                    thumbnail_base_path, thumbnail_path = create_thumbnail_preview(
                        video_data["file"], video_data["title"], log_cb=self._log)
                else:
                    thumbnail_path = generate_thumbnail(
                        video_data["file"], video_data["title"], log_cb=self._log)

            def cleanup_review_thumbnail():
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)

            item_privacy = settings.get("privacy", "private")
            publication_approved = False
            if review_before_upload:
                review_payload = {
                    "title": video_data.get("title", ""),
                    "description": video_data.get("desc", ""),
                    "privacy": item_privacy,
                    "duration": video_data.get("duration", 0),
                    "page": item.get("page", ""),
                    "scheduled": bool(settings.get("schedule_enabled")),
                    "thumbnail_path": thumbnail_path,
                    "thumbnail_base_path": thumbnail_base_path,
                    "duplicate_matches": video_data.get("duplicate_matches", []),
                    "channel_index_warning": channel_index_warning,
                    "rights_record": rights_record,
                    "rights_manifest_warning": rights_manifest_warning,
                    "source_url": item.get("url", ""),
                    "content_review_enabled": bool(settings.get("content_review_enabled")),
                    "dry_run": bool(settings.get("dry_run")),
                    "content_safety_review": video_data.get("content_safety_review"),
                }
                try:
                    decision = wait_for_review_decision(self._uiq, self._stop, review_payload)
                except Exception:
                    cleanup_review_thumbnail()
                    raise

                action = decision.get("action")
                if action == "approve":
                    video_data["title"] = prepare_public_title(
                        decision["title"],
                        source_names,
                        remove_hashtags=settings["remove_tags"],
                        omit_source_page_name=settings.get("omit_source_page_name", True),
                    )
                    video_data["desc"] = ensure_source_attribution(
                        decision["description"], source_page, source_url
                    )
                    item_privacy = (
                        "private" if settings.get("schedule_enabled")
                        else decision.get("privacy", item_privacy)
                    )
                    if "thumbnail_path" in decision:
                        thumbnail_path = decision["thumbnail_path"]
                    approved_rights = decision.get("rights_record") or rights_record
                    rights_errors = rights_record_errors(approved_rights, bool(duplicate_matches))
                    if rights_errors:
                        cleanup_review_thumbnail()
                        self._cleanup_file(video_data, branded_path)
                        failed_q.append({
                            "id": item["id"], "url": item["url"],
                            "page": item.get("page", ""),
                            "attempts": item.get("attempts", 0),
                            "last_error": "rights_review_required",
                        })
                        append_history({"source_id": item["id"], "url": item["url"],
                                        "page": item.get("page", ""), "title": video_data.get("title", ""),
                                        "status": "rights_review_required", "error": " | ".join(rights_errors)})
                        self._log("لم يُرفع الفيديو: بيانات الحقوق أو الإضافة الأصلية غير مكتملة.")
                        continue
                    try:
                        save_rights_record(item["id"], approved_rights, rights_manifest_path)
                    except Exception as error:
                        logging.exception("تعذر حفظ سجل الحقوق قبل الرفع")
                        cleanup_review_thumbnail()
                        self._cleanup_file(video_data, branded_path)
                        self._requeue_unprocessed(failed_q, process_list[index - 1:])
                        self._log(f"لم يُرفع الفيديو لأن سجل الحقوق لم يُحفظ بأمان: {error}")
                        break
                    rights_record = approved_rights
                    self._log("تمت مراجعة الفيديو وحقوقه وحفظ سجل الحقوق قبل الرفع.")
                    if settings.get("content_review_enabled") and not settings.get("dry_run"):
                        final_caption = build_review_caption(video_data.get("desc", ""), eff_tags)
                        review_passed, review_status = validate_content_review(
                            video_data.get("content_safety_review"),
                            video_data.get("file", ""),
                            video_data.get("title", ""),
                            final_caption,
                        )
                        if not review_passed:
                            review_report, review_status = run_policy_review(
                                video_data, eff_tags, settings, self._log
                            )
                            if isinstance(review_report, dict):
                                video_data["content_safety_review"] = review_report
                            else:
                                video_data.pop("content_safety_review", None)
                            review_passed = review_status == "clear" and isinstance(review_report, dict)
                        if not review_passed:
                            cleanup_review_thumbnail()
                            self._cleanup_file(video_data, branded_path)
                            fail_count += 1
                            failed_q.append({
                                "id": item["id"], "url": item["url"],
                                "page": item.get("page", ""),
                                "attempts": item.get("attempts", 0),
                                "last_error": "content_safety_review_required",
                            })
                            append_history({
                                "source_id": item["id"], "url": item["url"],
                                "page": item.get("page", ""), "title": video_data.get("title", ""),
                                "status": "content_safety_review_required",
                                "content_review_status": review_status,
                                "error": "لم يجتز العنوان أو الوصف النهائي مراجعة سياسة المحتوى.",
                            })
                            self._log("أُوقف الرفع بعد تعديل البيانات؛ يلزم تقرير جديد مطابق للمحتوى النهائي.")
                            continue
                    publication_approved = True
                elif action == "skip":
                    cleanup_review_thumbnail()
                    self._cleanup_file(video_data, branded_path)
                    save_seen_id(item["id"])
                    seen_ids.add(item["id"])
                    append_history({"source_id": item["id"], "url": item["url"],
                                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                                    "status": "skipped_manual", "error": "تخطّاه المستخدم في المراجعة"})
                    self._log("تخطّى المستخدم هذا الفيديو؛ لن يُرفع ولن يُعاد في الدورة التالية.")
                    self._uiq.put(("progress", int((index / total) * 100)))
                    continue
                else:
                    cleanup_review_thumbnail()
                    self._cleanup_file(video_data, branded_path)
                    self._requeue_unprocessed(failed_q, process_list[index - 1:])
                    self._log("أُوقف الرفع قبل النشر؛ أُعيد العنصر إلى قائمة الانتظار.")
                    break

            publish_at = None
            if settings["schedule_enabled"]:
                publish_at = next_publish_time(settings["min_delay"], settings["max_delay"])
                self._log(f"مجدول للنشر: {publish_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")

            self._log(f"رفع [{ctx['account']}]: {video_data['title'][:40]}...")
            playlist_id = resolve_playlist_id(item, pages, settings)
            upload_receipt_recorded = False
            upload_receipt_video_id = ""
            if settings.get("dry_run"):
                self._log("DRY-RUN: توقف قبل رفع الفيديو — لن تُحتسب حصة ولن يُسجّل كمرفوع.")
                result = "DRY_RUN"
            else:
                def persist_upload_intent():
                    nonlocal failed_q
                    entry = {
                        **item,
                        "upload_title": video_data.get("title", ""),
                        "upload_account": ctx.get("account", ""),
                        "upload_privacy": "private" if publish_at else item_privacy,
                        "scheduled_for": publish_at.isoformat() if publish_at else "",
                    }
                    failed_q = begin_upload_attempt(failed_q, entry)
                    save_failed_queue(failed_q)

                def persist_upload_confirmation(youtube_video_id):
                    nonlocal failed_q, upload_receipt_recorded, upload_receipt_video_id
                    persist_upload_receipt(
                        item["id"], youtube_video_id,
                        account=ctx.get("account", ""), url=item["url"],
                        page=item.get("page", ""), title=video_data.get("title", ""),
                    )
                    upload_receipt_recorded = True
                    upload_receipt_video_id = youtube_video_id
                    uploaded_ids.add(item["id"])
                    try:
                        save_uploaded_id(item["id"])
                    except (OSError, RuntimeError, ValueError):
                        logging.exception("YouTube upload receipt saved; legacy uploaded-id sync failed")
                    history_saved = append_history({
                        "source_id": item["id"], "url": item["url"],
                        "page": item.get("page", ""), "title": video_data.get("title", ""),
                        "account": ctx.get("account", ""),
                        "youtube_id": youtube_video_id, "status": "uploaded",
                    })
                    if not history_saved:
                        self._log("تأكد الرفع وحُفظ إيصال منع التكرار؛ تعذر تحديث history.jsonl فقط.")
                    failed_q = remove_queue_item(failed_q, item["id"])
                    try:
                        save_failed_queue(failed_q)
                    except (OSError, RuntimeError, ValueError):
                        logging.exception("YouTube upload receipt saved; queue cleanup will be recovered on next cycle")
                        self._log("تأكد الرفع وحُفظ الإيصال؛ تعذر تنظيف القائمة محلياً، وسيُسترد ذلك من الإيصال في الدورة التالية.")

                try:
                    result = upload_video(
                        ctx["yt"], video_data, item_privacy, playlist_id,
                        eff_tags, settings["category"], publish_at, self._log,
                        quota_manager=ctx["quota"],
                        content_review_required=bool(settings.get("content_review_enabled")),
                        publication_approved=publication_approved,
                        allow_public_automation=bool(settings.get("allow_public_automation")),
                        before_upload=persist_upload_intent,
                        on_confirmed=persist_upload_confirmation,
                        stop_event=self._stop,
                    )
                except Exception:
                    remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                    self._cleanup_file(video_data, branded_path)
                    raise
            known_results = {
                "CHANNEL_LIMIT_ERROR", "QUOTA_ERROR", "DRY_RUN",
                "INVALID_TITLE", "INVALID_DESCRIPTION", "INVALID_TAGS",
                "CONTENT_SAFETY_BLOCKED", "PUBLICATION_APPROVAL_REQUIRED",
                UPLOAD_NOT_STARTED, UPLOAD_RESULT_UNKNOWN, UPLOAD_REJECTED,
            }
            if upload_receipt_recorded:
                result = upload_receipt_video_id
            elif result is None or result is False:
                result = UPLOAD_NOT_STARTED
            elif not isinstance(result, str):
                result = UPLOAD_RESULT_UNKNOWN
            elif result not in known_results:
                confirmed_id = extract_youtube_video_id(result)
                if confirmed_id == result:
                    try:
                        persist_upload_confirmation(confirmed_id)
                    except Exception:
                        logging.exception("Valid-looking YouTube response could not be recorded safely")
                        self._log("أعاد YouTube معرّفاً صالحاً لكن تعذر حفظ إيصال منع التكرار؛ النتيجة غير مؤكدة ولن يُعاد الرفع تلقائياً.")
                        result = UPLOAD_RESULT_UNKNOWN
                    else:
                        result = confirmed_id
                else:
                    self._log("أعاد upload_video نتيجة غير معروفة؛ لم تُعتبر نجاحاً ولم يُسجّل الفيديو كمرفوع.")
                    result = UPLOAD_RESULT_UNKNOWN
            self._uiq.put(("quota", {"used": ctx["quota"].used, "limit": ctx["quota"].daily_limit}))
            if result == "CHANNEL_LIMIT_ERROR":
                ctx["channel_limit_reached"] = True
                failed_q = remove_queue_item(failed_q, item["id"])
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                self._handle_failure(failed_q, seen_ids, item, "وصلت القناة إلى حد الرفع اليومي", settings, count_attempt=False)
                self._requeue_unprocessed(failed_q, process_list[index:])
                self._log("أُوقفت الدورة لحماية الحصة؛ أعد التشغيل بعد إعادة ضبط حد القناة في YouTube.\n")
                break
            if result == "QUOTA_ERROR":
                ctx["quota"].mark_exhausted()
                failed_q = remove_queue_item(failed_q, item["id"])
                self._uiq.put(("quota", {"used": ctx["quota"].used, "limit": ctx["quota"].daily_limit}))
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                self._handle_failure(failed_q, seen_ids, item, "رفض الحصة", settings, count_attempt=False)
                if watch_mode:
                    self._wait_for_quota_reset(ctx["quota"])
                else:
                    self._requeue_unprocessed(failed_q, process_list[index:])
                    break
                continue

            if result == "DRY_RUN":
                dry_count += 1
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                append_history({"source_id": item["id"], "url": item["url"],
                                "page": item.get("page", ""), "title": video_data["title"],
                                "status": "dry_run", "error": "وضع المحاكاة — لم يُرفع"})
            elif result in {"INVALID_TITLE", "INVALID_DESCRIPTION", "INVALID_TAGS"}:
                fail_count += 1
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                failure_status, failure_text = {
                    "INVALID_TITLE": ("invalid_title", "راجع العنوان النهائي واختصره أو صحح محتواه."),
                    "INVALID_DESCRIPTION": ("invalid_description", "اختصر الوصف إلى حد YouTube ثم أعد مراجعته."),
                    "INVALID_TAGS": ("invalid_tags", "راجع الوسوم واجعلها ضمن حد YouTube."),
                }[result]
                failed_q = upsert_queue_item(remove_queue_item(failed_q, item["id"]), {
                    **item,
                    "last_error": failure_status,
                    "failed_at": item.get("failed_at") or time.time(),
                    "upload_error": failure_text,
                })
                save_failed_queue(failed_q)
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                    "status": failure_status,
                    "error": f"{failure_text} لم يُرسل طلب YouTube.",
                })
                self._log(f"{failure_text} حُفظ العنصر للمراجعة ولم يُرسل إلى YouTube.")
            elif result == "CONTENT_SAFETY_BLOCKED":
                fail_count += 1
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                failed_q.append({
                    "id": item["id"], "url": item["url"],
                    "page": item.get("page", ""),
                    "attempts": item.get("attempts", 0),
                    "last_error": "content_safety_review_required",
                })
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                    "status": "content_safety_review_required",
                    "error": "تغيّر الملف أو بيانات النشر بعد المراجعة النهائية.",
                })
                self._log("رفض فحص المطابقة الأخير الرفع؛ عُلّق العنصر لإعادة المراجعة.")
            elif result == "PUBLICATION_APPROVAL_REQUIRED":
                fail_count += 1
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                failed_q.append({
                    "id": item["id"], "url": item["url"],
                    "page": item.get("page", ""),
                    "attempts": item.get("attempts", 0),
                    "last_error": "publication_approval_required",
                })
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                    "status": "publication_approval_required",
                })
                self._log("أُوقف النشر العام أو المجدول؛ يلزم اعتماد بشري أو تفعيل السماح الآلي صراحةً.")
            elif result == UPLOAD_NOT_STARTED:
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                if self._stop.is_set():
                    self._requeue_unprocessed(failed_q, process_list[index - 1:])
                    self._log("أُوقف التشغيل قبل إرسال طلب الرفع؛ حُفظ العنصر للدورة التالية دون احتساب محاولة فاشلة.")
                    break
                fail_count += 1
                failed_q = remove_queue_item(failed_q, item["id"])
                self._handle_failure(failed_q, seen_ids, item, "لم يبدأ طلب الرفع", settings)
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                    "account": ctx.get("account", ""),
                    "status": UPLOAD_NOT_STARTED,
                })
                self._log("لم يبدأ إرسال الفيديو؛ أُعيد إلى قائمة المحاولة الآمنة.")
            elif result == UPLOAD_RESULT_UNKNOWN:
                fail_count += 1
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                if not any(entry.get("id") == item["id"] for entry in failed_q):
                    failed_q = begin_upload_attempt(failed_q, {
                        **item,
                        "upload_title": video_data.get("title", ""),
                        "upload_account": ctx.get("account", ""),
                    })
                try:
                    failed_q = mark_upload_result_unknown(
                        failed_q,
                        item["id"],
                        "تعذّر تأكيد نتيجة جلسة الرفع بعد انقطاع الاتصال.",
                    )
                except Exception:
                    logging.exception("تعذر تعليم نتيجة الرفع كغير مؤكدة")
                    failed_q = upsert_queue_item(failed_q, {
                        **item,
                        "last_error": UPLOAD_RESULT_UNKNOWN,
                        "unknown_since": time.time(),
                        "upload_title": video_data.get("title", ""),
                        "upload_account": ctx.get("account", ""),
                    })
                save_failed_queue(failed_q)
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                    "account": ctx.get("account", ""),
                    "status": UPLOAD_RESULT_UNKNOWN,
                    "error": "تحقق من YouTube Studio قبل اعتماد إعادة المحاولة.",
                })
                self._log("نتيجة الرفع غير مؤكدة؛ أُوقفت الإعادة الآلية وسُجّل العنصر للمطابقة اليدوية منعاً لرفع نسخة مكررة.")
            elif result == UPLOAD_REJECTED:
                fail_count += 1
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                failed_q = upsert_queue_item(remove_queue_item(failed_q, item["id"]), {
                    **item,
                    "last_error": UPLOAD_REJECTED,
                    "failed_at": item.get("failed_at") or time.time(),
                    "upload_error": "رفض YouTube الطلب؛ أصلح سبب الرفض قبل تحرير العنصر.",
                    "upload_title": video_data.get("title", ""),
                    "upload_account": ctx.get("account", ""),
                })
                save_failed_queue(failed_q)
                append_history({
                    "source_id": item["id"], "url": item["url"],
                    "page": item.get("page", ""), "title": video_data.get("title", ""),
                    "account": ctx.get("account", ""),
                    "status": UPLOAD_REJECTED,
                    "error": "رفض YouTube الطلب؛ لن يُعاد تلقائياً.",
                })
                self._log("رفض YouTube الطلب؛ لم يُسجّل كمرفوع ولم يُعد تلقائياً. أصلح سبب الرفض ثم حرّر العنصر يدوياً.")

            elif result:
                self._uiq.put(("quota", {"used": ctx["quota"].used, "limit": ctx["quota"].daily_limit}))
                if item["id"] not in uploaded_ids:
                    save_uploaded_id(item["id"])
                    uploaded_ids.add(item["id"])
                failed_q = remove_queue_item(failed_q, item["id"])
                up_count += 1
                bump_daily_uploads(self.stats)
                remember_content(
                    video_data["title"], video_data.get("source_sha256"),
                    video_data.get("frame_fingerprint"), result, video_data.get("duration")
                )
                if not upload_receipt_recorded:
                    append_history({"source_id": item["id"], "url": item["url"],
                                    "page": item.get("page", ""), "title": video_data["title"],
                                    "account": ctx["account"],
                                    "youtube_id": result, "status": "uploaded"})
                send_telegram(settings,
                              f"رُفع فيديو جديد إلى قناتك\n{video_data['title']}\nhttps://youtu.be/{result}",
                              self._log)
                if settings.get("thumbnail_enabled", True) and thumbnail_path:
                    try:
                        upload_thumbnail(ctx["yt"], result, thumbnail_path, self._log)
                    except Exception as error:
                        logging.warning("تعذر رفع الصورة المصغرة الاختيارية: %s", error)
                        self._log("تعذر رفع الصورة المصغرة؛ اكتمل رفع الفيديو.")
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                if settings.get("shorts_cut_enabled") and not settings.get("dry_run") and not self._stop.is_set():
                    self._make_shorts_from(
                        ctx, settings, item, video_data, uploaded_ids, publish_at=publish_at
                    )
                if settings["delete_after_upload"]:
                    self._cleanup_file(video_data, branded_path)
            else:
                fail_count += 1
                remove_thumbnail_preview(thumbnail_base_path, thumbnail_path)
                self._cleanup_file(video_data, branded_path)
                self._handle_failure(failed_q, seen_ids, item, "فشل الرفع", settings)

            self._uiq.put(("progress", int((index / total) * 100)))
            if index < total and not self._stop.is_set():
                self._stop.wait(settings["between_videos"])

        save_failed_queue(failed_q)
        if up_count:
            collect_video_stats(ctx["yt"], self._log)
        send_cycle_summary(
            ctx, settings,
            {"up_count": up_count, "fail_count": fail_count,
             "pending": len(failed_q), "dry_count": dry_count,
             "elapsed": max(0, int(time.time() - cycle_started)),
             "dry_run": dry_count > 0},
            self._log,
        )
        self._safe_quit_driver(driver)
        return up_count, fail_count

    @staticmethod
    def _safe_quit_driver(driver):
        if driver is None:
            return
        try:
            driver.quit()
        except Exception:
            pass

    def _rescue_title_via_driver(self, driver, url):
        """طبقة الإنقاذ الأخيرة: يستخدم الجلسة المسجلة نفسها لفتح صفحة الفيديو
        وقراءة العنوان من DOM بدل الاعتماد على طلبات HTTP مجهولة تحجبها فيسبوك.
        يعيد نصاً خاماً (قد يكون فارغاً) ولا يرفع أي استثناء."""
        if driver is None:
            return ""
        try:
            driver.get(url)
            time.sleep(4)
            og = driver.execute_script(
                "var m=document.querySelector('meta[property=\"og:title\"]');"
                "return m ? (m.content || '') : '';"
            ) or ""
            doc_title = str(driver.title or "")
            main_text = driver.execute_script(
                "var m=document.querySelector('div[role=\"main\"]');"
                "return m ? (m.innerText || '') : '';"
            ) or ""
            try:
                driver.get("about:blank")
            except Exception:
                pass
            candidates = [og, doc_title.split("|")[0], pick_caption_line(main_text)]
            for candidate in candidates:
                cleaned = sanitize_public_text(str(candidate or ""), keep_newlines=False).strip()
                if cleaned and not is_generic_title(cleaned):
                    return cleaned
            return ""
        except Exception as error:
            logging.info("تعذر إنقاذ العنوان عبر الجلسة المسجلة: %s", redact_sensitive_text(str(error)))
            try:
                driver.get("about:blank")
            except Exception:
                pass
            return ""

    def _make_shorts_from(self, ctx, settings, item, video_data, uploaded_ids, publish_at=None):
        if self._stop.is_set():
            return
        if settings.get("dry_run"):
            self._log("وضع المحاكاة: لن تُنشأ أو تُرفع نسخة Shorts.")
            return
        review_enabled = bool(settings.get("review_before_upload"))
        allow_public_automation = bool(settings.get("allow_public_automation"))
        rights_manifest_path = settings.get("rights_manifest_file") or RIGHTS_MANIFEST_FILE
        clip = None
        thumbnail_base_path = None
        thumbnail_path = None
        thumbnail_upload_path = None
        shorts_source_id = f"{item['id']}_shorts"

        def record(status, error="", **details):
            try:
                saved = append_history({
                    "source_id": shorts_source_id,
                    "url": redact_sensitive_text(str(item.get("url", ""))),
                    "page": item.get("page", ""),
                    "title": str(details.pop("title", video_data.get("title", "")))[:100],
                    "status": status,
                    "note": "shorts-cut",
                    "error": redact_sensitive_text(str(error)) if error else "",
                    **details,
                })
            except Exception as save_error:
                self._log(f"تعذر حفظ history.jsonl بعد {status}: {redact_sensitive_text(str(save_error))}.")
                return False
            if saved is False:
                self._log("تعذر حفظ سجل نتيجة Shorts؛ تحقق من history.jsonl. لا تُعد الرفع قبل مراجعة إيصال upload_receipts.json.")
            return saved

        try:
            try:
                rights_record = get_rights_record(item["id"], rights_manifest_path)
            except Exception as error:
                self._log(f"تعذر قراءة سجل حقوق Shorts: {redact_sensitive_text(str(error))}؛ أصلح rights_manifest.json قبل المتابعة.")
                record("rights_review_required", str(error))
                return
            rights_errors = rights_record_errors(
                rights_record, bool(video_data.get("duplicate_matches"))
            )
            if not review_enabled:
                automatic_errors = automatic_rights_preflight_errors(settings)
                if automatic_errors or rights_errors:
                    reason = "؛ ".join(automatic_errors + rights_errors)
                    self._log(f"أُوقفت نسخة Shorts قبل الرفع بسبب بوابة الحقوق: {reason}. فعّل المراجعة اليدوية أو استكمل إقراراً صادقاً وسجل حقوق صالحاً.")
                    record("rights_review_required", reason)
                    return
            publication_errors = validate_publication_controls(
                settings.get("privacy", "private"),
                bool(settings.get("schedule_enabled")),
                review_enabled,
                allow_public_automation,
            )
            if publication_errors:
                reason = "؛ ".join(publication_errors)
                self._log("لم تُرفع نسخة Shorts: " + reason + " عالج الإعداد ثم أعد المحاولة.")
                record("publication_approval_required", reason)
                return
            duration = float(video_data.get("duration") or 0)
            if duration < SHORTS_MIN_SOURCE:
                self._log(f"تُخطى Shorts: مدة المصدر {duration:.1f} ثانية أقل من الحد {SHORTS_MIN_SOURCE}؛ لا يلزم إجراء.")
                return
            source = video_data.get("file")
            if not source or not os.path.isfile(source):
                reason = "ملف المصدر غير موجود. أعد تنزيل المقطع وتحقق من مساحة القرص والصلاحيات."
                self._log(reason)
                record("media_validation_failed", reason)
                return
            rms = _audio_rms_per_second(source)
            segment = find_hot_segment(rms, source_duration=duration)
            if not segment:
                self._log("تُخطى Shorts: لم يُكتشف مقطع صوتي صالح للقص؛ راجع الصوت أو ffmpeg.")
                record("shorts_cut_skipped", "لم يُعثر على مقطع صالح للقص.")
                return
            start, span = segment
            self._log(f"قصّ نسخة Shorts من الثانية {start} ({span} ثانية)…")
            clip = cut_shorts_clip(source, start, span, log_cb=self._log)
            if not clip:
                reason = "تعذر قص النسخة القصيرة؛ تحقق من ffmpeg ومساحة القرص وصلاحيات مجلد الفيديوهات."
                self._log(reason)
                record("shorts_cut_failed", reason)
                return
            media_ok, media_info = validate_media(
                clip, self._log, expected_duration=span
            )
            if not media_ok:
                cause = media_info.get("cause", "فشل فحص ملف Shorts.")
                action = media_info.get("action", "أعد إنشاء المقطع بعد إصلاح الملف المصدر.")
                self._log(f"رُفضت نسخة Shorts بعد فحص الوسائط: {cause} الإجراء: {action}")
                record("media_validation_failed", f"{cause} {action}")
                return
            clip_data = dict(
                video_data,
                file=clip,
                title=normalize_youtube_title(
                    str(video_data.get("title", "")) + " #Shorts"
                ),
            )
            clip_data.pop("content_safety_review", None)
            clip_data["duration"] = media_info.get("duration", span)
            clip_data["thumbnail"] = None
            configured_tags = settings.get("tags") or []
            if isinstance(configured_tags, str):
                configured_tags = [configured_tags]
            shorts_tags = list(dict.fromkeys(list(configured_tags) + ["Shorts"]))
            review_report = None
            if settings.get("content_review_enabled"):
                review_report, review_status = run_policy_review(
                    clip_data, shorts_tags, settings, self._log
                )
                if review_status != "clear" or not isinstance(review_report, dict):
                    reason = f"بوابة أمان المحتوى لم تُرجع حالة clear (الحالة: {review_status}). أصلح أداة المراجعة أو راجع المقطع يدوياً."
                    self._log(reason)
                    record(
                        "content_safety_review_required",
                        reason,
                        content_review_status=review_status,
                        title=clip_data["title"],
                    )
                    return
                clip_data["content_safety_review"] = review_report
            selected_privacy = settings.get("privacy", "private")
            publication_approved = False
            thumbnail_upload_path = None
            if review_enabled:
                if settings.get("thumbnail_enabled", True):
                    thumbnail_base_path, thumbnail_path = create_thumbnail_preview(
                        clip, clip_data["title"], self._log
                    )
                review_payload = {
                    "title": clip_data["title"],
                    "description": clip_data.get("desc", ""),
                    "privacy": selected_privacy,
                    "duration": clip_data.get("duration", span),
                    "page": item.get("page", ""),
                    "scheduled": bool(settings.get("schedule_enabled")),
                    "thumbnail_path": thumbnail_path,
                    "thumbnail_base_path": thumbnail_base_path,
                    "duplicate_matches": video_data.get("duplicate_matches", []),
                    "channel_index_warning": "",
                    "rights_record": rights_record,
                    "rights_manifest_warning": "",
                    "source_url": item.get("url", ""),
                    "content_review_enabled": bool(settings.get("content_review_enabled")),
                    "dry_run": False,
                    "content_safety_review": review_report,
                }
                decision = wait_for_review_decision(self._uiq, self._stop, review_payload)
                if decision.get("action") != "approve":
                    self._log("تُخطى رفع Shorts؛ لم يمنح المشغل موافقة صريحة.")
                    record("operator_skipped", "لم يوافق المشغل على رفع Shorts.")
                    return
                clip_data["title"] = str(decision.get("title", "")).strip()
                clip_data["desc"] = str(decision.get("description", "")).strip()
                selected_privacy = decision.get("privacy", "private")
                thumbnail_upload_path = decision.get("thumbnail_path")
                clip_data["thumbnail"] = thumbnail_upload_path
                rights_record = decision.get("rights_record")
                rights_errors = rights_record_errors(
                    rights_record, bool(video_data.get("duplicate_matches"))
                )
                if rights_errors:
                    reason = "؛ ".join(rights_errors)
                    self._log("لم تُرفع Shorts؛ بقيت نقاط حقوق غير مكتملة: " + reason)
                    record("rights_review_required", reason, title=clip_data["title"])
                    return
                try:
                    save_rights_record(item["id"], rights_record, rights_manifest_path)
                except Exception as error:
                    reason = f"تعذر حفظ الإقرار: {redact_sensitive_text(str(error))}. تحقق من صلاحيات ونسخ rights_manifest.json؛ لم يبدأ الرفع."
                    self._log(reason)
                    record("rights_record_save_failed", reason, title=clip_data["title"])
                    return
                clip_data["rights_record"] = rights_record
                publication_approved = True
                if settings.get("content_review_enabled"):
                    review_report, review_status = run_policy_review(
                        clip_data, shorts_tags, settings, self._log
                    )
                    if review_status != "clear" or not isinstance(review_report, dict):
                        reason = f"التعديل النهائي لم يجتز مراجعة المحتوى (الحالة: {review_status}). راجع العنوان والوصف وأعد الفحص."
                        self._log(reason)
                        record(
                            "content_safety_review_required",
                            reason,
                            content_review_status=review_status,
                            title=clip_data["title"],
                        )
                        return
                    clip_data["content_safety_review"] = review_report
            elif rights_errors:
                reason = "؛ ".join(rights_errors)
                self._log("لم تُرفع Shorts؛ سجل الحقوق غير صالح: " + reason)
                record("rights_review_required", reason, title=clip_data["title"])
                return
            if not review_enabled and settings.get("thumbnail_enabled", True):
                thumbnail_upload_path = generate_thumbnail(
                    clip, clip_data["title"], log_cb=self._log
                )
                clip_data["thumbnail"] = thumbnail_upload_path
                if not thumbnail_upload_path:
                    self._log("ستُرفع Shorts دون صورة مصغّرة مخصصة؛ تعذر توليدها.")
            publication_errors = validate_publication_controls(
                selected_privacy,
                bool(settings.get("schedule_enabled")),
                publication_approved,
                allow_public_automation,
            )
            if publication_errors:
                reason = "؛ ".join(publication_errors)
                self._log("لم تُرفع Shorts قبل اجتياز بوابة النشر: " + reason)
                record("publication_approval_required", reason, title=clip_data["title"])
                return
            if not clip_data["title"] or len(clip_data["title"]) > 100:
                reason = "عنوان Shorts فارغ أو يتجاوز 100 حرف؛ عدّله في نافذة المراجعة ثم أعد المحاولة."
                self._log(reason)
                record("invalid_title", reason, title=clip_data["title"])
                return
            if len(clip_data.get("desc", "")) > YOUTUBE_DESCRIPTION_MAX:
                reason = f"وصف Shorts يتجاوز {YOUTUBE_DESCRIPTION_MAX} حرفاً؛ اختصره قبل الرفع."
                self._log(reason)
                record("invalid_description", reason, title=clip_data["title"])
                return
            shorts_tags = normalize_youtube_tags(shorts_tags, self._log)
            upload_receipt_recorded = False

            def persist_short_receipt(youtube_id):
                nonlocal upload_receipt_recorded
                persist_upload_receipt(
                    shorts_source_id,
                    youtube_id,
                    account=ctx.get("account", ""),
                    url=redact_sensitive_text(str(item.get("url", ""))),
                    page=item.get("page", ""),
                    title=clip_data["title"],
                )
                save_uploaded_id(shorts_source_id)
                upload_receipt_recorded = True

            result = upload_video(
                ctx["yt"], clip_data, selected_privacy, None,
                shorts_tags, settings.get("category", "22"),
                publish_at if settings.get("schedule_enabled") else None,
                self._log,
                quota_manager=ctx["quota"],
                content_review_required=bool(settings.get("content_review_enabled")),
                publication_approved=publication_approved,
                allow_public_automation=allow_public_automation,
                on_confirmed=persist_short_receipt,
                stop_event=self._stop,
            )
            self._uiq.put(("quota", {
                "used": ctx["quota"].used,
                "limit": ctx["quota"].daily_limit,
            }))
            if result == "QUOTA_ERROR":
                ctx["quota"].mark_exhausted()
                reason = "نفدت حصة YouTube أثناء رفع Shorts؛ انتظر التجديد اليومي ولا تكرر الرفع الآن."
                self._log(reason)
                record("quota_exhausted", reason, title=clip_data["title"])
                return
            confirmed_id = extract_youtube_video_id(str(result or ""))
            if confirmed_id:
                uploaded_ids.add(shorts_source_id)
                bump_daily_uploads(self.stats)
                if not upload_receipt_recorded:
                    try:
                        persist_short_receipt(confirmed_id)
                    except Exception as error:
                        save_uploaded_id(shorts_source_id)
                        reason = (
                            f"YouTube أكد Shorts بالمعرّف {confirmed_id}، لكن تعذر حفظ الإيصال: "
                            f"{redact_sensitive_text(str(error))}. لا تُعد الرفع؛ طابق الفيديو يدوياً في القناة."
                        )
                        self._log(reason)
                        record(
                            "upload_confirmed_receipt_failed",
                            reason,
                            title=clip_data["title"],
                            youtube_id=confirmed_id,
                        )
                        return
                history_saved = record(
                    "uploaded",
                    title=clip_data["title"],
                    account=ctx.get("account", ""),
                    youtube_id=confirmed_id,
                )
                if history_saved is False:
                    self._log("تأكد رفع Shorts وحُفظ إيصال YouTube، لكن تعذر تحديث سجل history.jsonl. لا تُعد الرفع؛ طابق الإيصال يدوياً.")
                send_telegram(
                    settings,
                    f"نُشرت نسخة Shorts إضافية\nhttps://youtu.be/{confirmed_id}",
                    self._log,
                )
                if settings.get("thumbnail_enabled", True) and thumbnail_upload_path:
                    try:
                        upload_thumbnail(ctx["yt"], confirmed_id, thumbnail_upload_path, self._log)
                    except Exception as error:
                        safe_error = redact_sensitive_text(str(error))
                        logging.warning("تعذر رفع صورة Shorts المصغّرة: %s", safe_error)
                        self._log(f"تعذر رفع صورة Shorts المصغّرة: {safe_error}؛ اكتمل رفع الفيديو.")
                return
            status_messages = {
                "UPLOAD_RESULT_UNKNOWN": (
                    "نتيجة رفع Shorts غير مؤكدة؛ ابحث يدوياً في YouTube وطابق upload_receipts.json قبل أي إعادة محاولة.",
                    "upload_result_unknown",
                ),
                "UPLOAD_NOT_STARTED": (
                    "لم يبدأ رفع Shorts. أصلح السبب المسجل ثم شغّل مراجعة جديدة.",
                    "upload_not_started",
                ),
                "UPLOAD_REJECTED": (
                    "رفض YouTube رفع Shorts. راجع رسالة API والخصوصية والقيود قبل إعادة المحاولة.",
                    "upload_rejected",
                ),
                "CONTENT_SAFETY_BLOCKED": (
                    "أوقفت بوابة أمان المحتوى رفع Shorts؛ أصلح تقرير المراجعة قبل المتابعة.",
                    "content_safety_review_required",
                ),
                "PUBLICATION_APPROVAL_REQUIRED": (
                    "لم يجتز Shorts بوابة موافقة النشر؛ راجع الخصوصية والموافقة قبل المتابعة.",
                    "publication_approval_required",
                ),
                "INVALID_TITLE": (
                    "عنوان Shorts غير صالح؛ اختصره إلى 100 حرف أو أقل ثم راجعه.",
                    "invalid_title",
                ),
                "INVALID_DESCRIPTION": (
                    f"وصف Shorts غير صالح؛ اختصره إلى {YOUTUBE_DESCRIPTION_MAX} حرفاً أو أقل.",
                    "invalid_description",
                ),
            }
            message, status = status_messages.get(
                str(result),
                (f"تعذر رفع Shorts (النتيجة: {redact_sensitive_text(str(result))}). راجع السجل قبل أي إعادة محاولة.", "upload_failed"),
            )
            self._log(message)
            record(status, message, title=clip_data["title"], upload_result=redact_sensitive_text(str(result)))
        except PipelineFailure as error:
            message = f"فشلت معالجة Shorts ({error.code}): {redact_sensitive_text(error.cause)} الإجراء: {redact_sensitive_text(error.action)}"
            self._log(message)
            record("shorts_pipeline_failed", message, error_code=error.code)
        except Exception as error:
            message = f"فشلت معالجة Shorts: {redact_sensitive_text(str(error))}. راجع الخطأ المحلي وأصلح سببه؛ لا تُعد رفع نتيجة غير مؤكدة."
            logging.exception("Unexpected Shorts processing failure")
            self._log(message)
            record("shorts_pipeline_failed", message)
        finally:
            if clip:
                self._cleanup_file({"file": clip})
            remove_thumbnail_preview(thumbnail_base_path, thumbnail_path, thumbnail_upload_path)

    def _ai_metadata(self, item, video_data, settings):
        """يولّد بيانات الفيديو بالذكاء الاصطناعي مرة واحدة لكل مقطع (كاش دائم)."""
        state = load_state()
        cache = state.get("ai_cache", {})
        if item["id"] in cache:
            self._log("بيانات AI من الذاكرة (سبق توليدها لهذا المقطع)")
            return cache[item["id"]]
        hint = "\n".join(top_performing_titles(3, min_views=100))
        meta = ai_generate_metadata(
            video_data.get("title", ""), video_data.get("desc", ""),
            item.get("page", ""), video_data.get("duration", 0),
            settings.get("ai_model", GEMINI_MODELS[0]),
            settings.get("ai_style", "احترافي متوازن"), self._log,
            performance_hint=hint)
        if meta:
            cache[item["id"]] = meta
            # حدّ أقصى للكاش حتى لا يتضخم state.json
            while len(cache) > 1500:
                cache.pop(next(iter(cache)))
            state["ai_cache"] = cache
            save_state(state)
            self._log(f"AI اقترح: {meta['title'][:60]}")
        return meta

    def _maybe_daily_summary(self, settings):
        """يرسل ملخص الأمس على Telegram مرة واحدة يومياً (عند أول دورة في اليوم الجديد)."""
        if not settings.get("tg_errors"):
            return
        state = load_state()
        today = pt_now().date().isoformat()
        if state.get("last_summary_date") == today:
            return
        state["last_summary_date"] = today
        save_state(state)
        daily = self.stats.get("daily", {})
        yesterday_count = daily.get("uploaded", 0) if daily.get("date") != today else 0
        send_telegram(
            settings,
            f"الملخص اليومي\n▫رُفع أمس: {yesterday_count} فيديو\n"
            f"▫الإجمالي الكلي: {self.stats.get('total_uploaded', 0)}\n"
            f"▫الفاشل الكلي: {self.stats.get('total_failed', 0)}\n"
            f"▫المراقبة تعمل (دورة #{self.stats.get('cycles', 0)})",
            self._log)

    def _handle_failure(self, failed_q, seen_ids, item, reason, settings, count_attempt=True, failure_details=None):
        if count_attempt:
            item["attempts"] = item.get("attempts", 0) + 1
        item["last_error"] = reason
        details = {}
        if failure_details is not None:
            details = {
                "failure_stage": str(failure_details.stage)[:100],
                "failure_cause": str(failure_details.cause)[:500],
                "next_action": str(failure_details.action)[:500],
            }
        if item.get("attempts", 0) >= settings["max_attempts"]:
            save_seen_id(item["id"])
            seen_ids.add(item["id"])
            entry = {
                "id": item["id"], "url": item["url"],
                "page": item.get("page", ""), "attempts": item["attempts"],
                "last_error": "abandoned",
                "failure_reason": reason,
                "failed_at": item.get("failed_at") or time.time(),
                **details,
            }
            failed_q[:] = upsert_queue_item(failed_q, entry)
            append_history({"source_id": item["id"], "url": item["url"],
                            "page": item.get("page", ""), "status": "abandoned",
                            "error": f"{reason} — {item['attempts']} محاولات", **details})
            self._log(f"توقفت إعادة المحاولة تلقائياً بعد {item['attempts']} محاولات: {item['id']}؛ حرّره يدوياً بعد إصلاح السبب.")
        else:
            entry = {"id": item["id"], "url": item["url"],
                     "page": item.get("page", ""), "attempts": item["attempts"],
                     "last_error": reason,
                     "failed_at": item.get("failed_at") or time.time(),
                     **details}
            failed_q[:] = upsert_queue_item(failed_q, entry)
            self._log(f"أُضيف لقائمة إعادة المحاولة (محاولة {item['attempts']}/{settings['max_attempts']})")
        save_failed_queue(failed_q)
    def _history_page_index(self):
        """فهرس source_id → اسم الصفحة من السجل؛ يعيد أسماء الصفحات للعناصر القديمة
        المحفوظة قبل إضافة حقل page، حتى لا يُرفع عنوان بلا اسم مصدر."""
        index = {}
        for record in _read_history():
            source_id = str(record.get("source_id") or "").strip()
            page = str(record.get("page") or "").strip()
            if source_id and page:
                index[source_id] = page
        return index

    def _requeue_unprocessed(self, failed_q, remaining):
        """يحفظ العناصر غير المعالجة لتلتقطها الدورة القادمة."""
        queued = {it["id"] for it in failed_q}
        for it in remaining:
            if it["id"] in queued:
                continue
            failed_q.append({"id": it["id"], "url": it["url"],
                             "page": it.get("page", ""), "attempts": it.get("attempts", 0),
                             "title_hint": str(it.get("title_hint") or "")[:300],
                             "last_error": "مؤجل",
                             "failed_at": it.get("failed_at") or time.time()})
            queued.add(it["id"])
        if remaining:
            self._log(f"أُجّل {len(remaining)} {_arabic_plural(len(remaining), 'عنصر', 'عناصر', 'عنصراً')} للدورة القادمة.")

    def _cleanup_file(self, video_data, branded_path=None):
        paths = {video_data.get("file", "")}
        if branded_path:
            paths.add(branded_path)
        if video_data.get("original_file"):
            paths.add(video_data["original_file"])
        root = os.path.realpath(VIDEO_DIRECTORY)
        for path in paths:
            if not path:
                continue
            candidate = os.path.abspath(os.fspath(path))
            if os.path.islink(candidate) or not os.path.basename(candidate).startswith("fb2yt-"):
                continue
            try:
                if os.path.commonpath((root, os.path.realpath(candidate))) != root:
                    continue
            except ValueError:
                continue
            try:
                os.unlink(candidate)
            except FileNotFoundError:
                continue
            except OSError as error:
                safe_error = redact_sensitive_text(error)
                logging.warning("تعذر تنظيف ملف ناتج محلي %s: %s", os.path.basename(candidate), safe_error)
                self._log(f"تعذر تنظيف ملف ناتج محلي {os.path.basename(candidate)}: {safe_error}")

    def _emit_page_status(self, page, found, new, status):
        self._uiq.put(("page_status", {
            "url": page["url"],
            "time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "found": found, "new": new, "status": status,
        }))

    def _countdown_wait(self, seconds, label):
        """انتظار بعدّاد تنازلي مرئي مع استجابة فورية لزر الإيقاف."""
        for remaining in range(int(seconds), 0, -1):
            if self._stop.is_set():
                return
            mins, secs = divmod(remaining, 60)
            hours, mins = divmod(mins, 60)
            txt = f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
            self._uiq.put(("countdown", f"{label}: {txt}"))
            time.sleep(1)
        self._uiq.put(("countdown", "يبدأ الفحص الآن…"))

    def _wait_for_facebook_cookie_refresh(self, settings, previous_fingerprint):
        self._uiq.put(("countdown", "المراقبة معلّقة بانتظار جلسة Facebook صالحة"))
        next_status = time.monotonic() + 60
        while not self._stop.wait(5):
            refresh_facebook_cookie_settings(settings)
            current_fingerprint = facebook_cookie_fingerprint(cookies_arg(settings))
            if (
                current_fingerprint
                and current_fingerprint[-1]
                and current_fingerprint != previous_fingerprint
            ):
                return True
            if time.monotonic() >= next_status:
                self._uiq.put(("countdown", "صدّر cookies.txt جديدة واضغط حفظ إعدادات Facebook"))
                next_status = time.monotonic() + 60
        return False

    def _wait_for_quota_reset(self, quota):
        wait_s = quota.seconds_until_reset()
        self._log(f"حصة يوتيوب اليومية مستنفدة — استئناف تلقائي بعد التصفير "
                  f"(منتصف الليل بتوقيت المحيط الهادئ، بعد ~{wait_s // 3600} ساعة)")
        self._countdown_wait(wait_s, "تصفير الحصة بعد")
        quota._refresh()

    def _finish_worker(self, result):
        if self._closing:
            self._finish_close_when_idle()
            return
        self._watching = False
        self.start_once_btn.config(state="normal")
        self.watch_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.countdown_var.set("المراقبة متوقفة")
        self.stats_card.refresh()
        if result.get("fatal"):
            self.status_lbl.config(text="توقفت العملية؛ راجع السجل", fg=ERR)
            messagebox.showerror("تعذرت العملية", result["fatal"] + "\n\nراجع ملف errors.log للتفاصيل.")
        elif result.get("failed") or result.get("pending") or result.get("network_errors") or result.get("cycle_errors"):
            failed = int(result.get("failed", 0) or 0)
            network_errors = int(result.get("network_errors", 0) or 0)
            cycle_errors = int(result.get("cycle_errors", 0) or 0)
            pending = result.get("pending")
            pending_label = str(pending) if pending is not None else "غير معروف"
            self.status_lbl.config(
                text=f"اكتملت مع {failed} إخفاقات فعلية، و{network_errors} تعذرات شبكة، "
                     f"و{cycle_errors} أخطاء دورات، و{pending_label} عنصر قيد الانتظار/المراجعة؛ راجع السجل",
                fg=WARN,
            )
        else:
            self.status_lbl.config(text="اكتملت العملية", fg=SUCCESS)
        if not result.get("watch"):
            self.prog_var.set(100)
            self.pct_lbl.config(text="100%")

# ════════════════════════════════════════════════════
# UI SETTINGS PERSISTENCE — حفظ شامل لإعدادات الواجهة
# ════════════════════════════════════════════════════
# (القسم، المفتاح، القيمة الافتراضية)
UI_KEYS = [
    ("SETTINGS", "VIDEO_DIRECTORY", "videos/"),
    ("SETTINGS", "CLIENT_SECRETS_FILE", "client_secrets.json"),
    ("SETTINGS", "ACCOUNT", "account1"),
    ("SETTINGS", "PRIVACY", "private"),
    ("SETTINGS", "HEADLESS", "true"),
    ("SETTINGS", "DELETE_AFTER_UPLOAD", "true"),
    ("SETTINGS", "REMOVE_TAGS", "false"),
    ("SETTINGS", "BETWEEN_VIDEOS", "10"),
    ("SETTINGS", "DAILY_UPLOAD_LIMIT", str(DEFAULT_DAILY_UPLOAD_LIMIT)),
    ("SETTINGS", "MAX_ATTEMPTS", "3"),
    ("SETTINGS", "MIN_DISK_GB", "2"),
    ("SETTINGS", "DRY_RUN", "false"),
    ("WATCH", "INTERVAL_MINUTES", "10"),
    ("WATCH", "FAST_SCRAPE", "true"),
    ("WATCH", "AUTO_START", "false"),
    ("STUDIO", "TITLE_PREFIX", ""),
    ("STUDIO", "TITLE_SUFFIX", ""),
    ("STUDIO", "CATEGORY", "25"),
    ("STUDIO", "TITLE_OPTIMIZE", "true"),
    ("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE", "true"),
    ("STUDIO", "AUTO_SHORTS", "true"),
    ("STUDIO", "AUTO_HASHTAGS", "true"),
    ("STUDIO", "DEDUPE_CONTENT", "true"),
    ("STUDIO", "CREDIT_LINE", ""),
    ("STUDIO", "EXTRA_DESCRIPTION", ""),
    ("STUDIO", "TAGS", "Facebook,Reel,Shorts"),
    ("STUDIO", "LOGO_ENABLED", "false"),
    ("STUDIO", "LOGO_PATH", "logo.png"),
    ("STUDIO", "LOGO_POSITION", "أعلى اليمين"),
    ("STUDIO", "LOGO_SCALE", "8"),
    ("STUDIO", "LOGO_OPACITY", "100"),
    ("STUDIO", "LOGO_COVER_WIDTH", "20"),
    ("STUDIO", "LOGO_COVER_OLD", "true"),
    ("STUDIO", "LOGO_PLATE", "false"),
    ("STUDIO", "LOGO_SECONDARY", "false"),
    ("STUDIO", "LOGO_SECONDARY_COVER_OLD", "true"),
    ("STUDIO", "PLAYLIST_ID", ""),
    ("STUDIO", "REVIEW_BEFORE_UPLOAD", "true"),
    ("STUDIO", "RIGHTS_AUTO_DRAFT", "true"),
    ("STUDIO", "RIGHTS_GATE_MODE", "strict"),
    ("STUDIO", "RIGHTS_GATE_MIGRATION", "strict-default-v3"),
    ("STUDIO", "RIGHTS_OPERATOR_BASIS", "owned"),
    ("STUDIO", "RIGHTS_OPERATOR_STATEMENT", DEFAULT_OPERATOR_RIGHTS_STATEMENT),
    ("STUDIO", "RIGHTS_OPERATOR_EVIDENCE", ""),
    ("STUDIO", "THUMBNAIL_ENABLED", "true"),
    ("STUDIO", "SHORTS_CUT", "false"),
    ("STUDIO", "MIN_DURATION", "0"),
    ("STUDIO", "MAX_DURATION", "0"),
    ("STUDIO", "SCHEDULE", "false"),
    ("STUDIO", "SCHEDULE_MIN", "20"),
    ("STUDIO", "SCHEDULE_MAX", "60"),
    ("STUDIO", "COOKIES_FILE", "cookies.txt"),
    ("STUDIO", "COOKIES_ENABLED", "false"),
    ("STUDIO", "TELEGRAM_TOKEN", ""),
    ("STUDIO", "TELEGRAM_CHAT_ID", ""),
    ("STUDIO", "TELEGRAM_ERRORS", "true"),
    ("STUDIO", "PG_SCROLL", "5"),
    ("STUDIO", "PG_LIMIT", "30"),
    ("AI", "ENABLED", "false"),
    ("AI", "MODEL", GEMINI_MODELS[0]),
    ("AI", "STYLE", "احترافي متوازن"),
    ("SAFETY", "ENABLED", "false"),
    ("SAFETY", "ALLOW_PUBLIC_AUTOMATION", "false"),

    ("SAFETY", "CUTTER_ROOT", ""),
    ("SAFETY", "CUTTER_PYTHON", ""),
]

def load_ui_values(config_path=None):
    """يقرأ إعدادات الواجهة من الملف المحلي أو يستعيد آخر نسخة احتياطية صالحة."""
    if config_path is None:
        config_path = os.path.join(APP_DIRECTORY, "config.ini")
    cfg, recovered_from = read_config(config_path)
    if recovered_from and os.path.abspath(recovered_from) != os.path.abspath(config_path):
        logging.warning("استُعيد config.ini من النسخة الاحتياطية %s", recovered_from.name)
    if migrate_legacy_rights_gate_config(cfg):
        _write_config_atomically(cfg, config_path)
    out = {}
    for section, key, default in UI_KEYS:
        try:
            out[(section, key)] = cfg.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            out[(section, key)] = default
    out[("SETTINGS", "DAILY_UPLOAD_LIMIT")] = str(_daily_upload_limit_from_config(cfg))
    return out

def save_ui_values(values, config_path=None):
    """يتحقق من الإعدادات ثم يحفظها ذرياً مع إبقاء نسخة احتياطية محلية."""
    global config
    if config_path is None:
        config_path = os.path.join(APP_DIRECTORY, "config.ini")
    cfg, _ = read_config(config_path)
    for (section, key), value in values.items():
        if not cfg.has_section(section):
            cfg.add_section(section)
        cfg.set(section, key, str(value))
    for section, key, default in UI_KEYS:
        if not cfg.has_option(section, key):
            if not cfg.has_section(section):
                cfg.add_section(section)
            cfg.set(section, key, str(default))
    if cfg.has_option("SETTINGS", "DAILY_QUOTA"):
        cfg.remove_option("SETTINGS", "DAILY_QUOTA")
    errors = validate_configuration(cfg)
    if errors:
        raise ValueError("إعدادات غير صالحة:\n• " + "\n• ".join(errors))
    try:
        _write_config_atomically(cfg, config_path)
        if os.path.abspath(config_path) == os.path.abspath(CONFIG_FILE):
            config = cfg
        return True
    except OSError:
        logging.warning("تعذر حفظ config.ini؛ بقي الملف السابق دون استبدال.")
        return False


def _write_config_atomically(cfg, config_path):
    write_config_atomically(cfg, config_path)

# ════════════════════════════════════════════════════
# CLI MODE — تشغيل 24/7 بدون واجهة (للسيرفرات)
# ════════════════════════════════════════════════════
def build_cli_settings(cfg=None):
    """يبني إعدادات وضع --cli من config.ini مع افتراضيات الواجهة نفسها."""
    use_project_config = cfg is None
    cfg = cfg or config
    if migrate_legacy_rights_gate_config(cfg) and use_project_config:
        try:
            config_path = os.path.join(APP_DIRECTORY, "config.ini")
            _write_config_atomically(cfg, config_path)
            _harden_secret_file(config_path)
        except OSError as error:
            logging.warning("تعذر حفظ ترقية بوابة الحقوق: %s", error)
    try:
        content_review_enabled = cfg.getboolean("SAFETY", "ENABLED", fallback=False)
        content_review_config_error = False
    except ValueError:
        content_review_enabled = True
        content_review_config_error = True
    g = lambda section, key, fallback: cfg.get(section, key, fallback=fallback)
    def gi(section, key, fallback):
        try:
            return cfg.getint(section, key, fallback=fallback)
        except Exception:
            return fallback
    def gb(section, key, fallback):
        try:
            return cfg.getboolean(section, key, fallback=fallback)
        except Exception:
            return fallback
    try:
        min_disk_gb = float(g("SETTINGS", "MIN_DISK_GB", "2"))
        min_disk_config_error = not math.isfinite(min_disk_gb) or min_disk_gb < 0
    except (ValueError, OverflowError):
        min_disk_gb = 2.0
        min_disk_config_error = True
    if min_disk_config_error:
        min_disk_gb = 2.0
    accounts = load_accounts()
    return {
        "account": g("SETTINGS", "ACCOUNT", accounts[0] if accounts else "account1"),
        "headless": True,
        "remove_tags": gb("SETTINGS", "REMOVE_TAGS", gb("STUDIO", "REMOVE_TAGS", False)),
        "extra_description": g("STUDIO", "EXTRA_DESCRIPTION", ""),
        "schedule_enabled": gb("STUDIO", "SCHEDULE", False),
        "min_delay": gi("STUDIO", "SCHEDULE_MIN", 20),
        "max_delay": gi("STUDIO", "SCHEDULE_MAX", 60),
        "privacy": g("SETTINGS", "PRIVACY", "private"),
        "tags": g("STUDIO", "TAGS", "Facebook,Reel,Shorts").split(","),
        "delete_after_upload": gb("SETTINGS", "DELETE_AFTER_UPLOAD", True),
        "between_videos": gi("SETTINGS", "BETWEEN_VIDEOS", 10),
        "interval_minutes": gi("WATCH", "INTERVAL_MINUTES", 10),
        "fast_scrape": gb("WATCH", "FAST_SCRAPE", True),
        "facebook_network_preflight": gb("WATCH", "NETWORK_PREFLIGHT", True),
        "daily_upload_limit": _daily_upload_limit_from_config(cfg),
        "max_attempts": gi("SETTINGS", "MAX_ATTEMPTS", 3),
        "failed_max_age_days": gi("SETTINGS", "FAILED_MAX_AGE_DAYS", FAILED_QUEUE_MAX_AGE_DAYS),
        "min_disk_gb": min_disk_gb,
        "min_disk_config_error": min_disk_config_error,
        "telegram_enabled": bool(g("STUDIO", "TELEGRAM_TOKEN", "").strip()),
        "tg_token": g("STUDIO", "TELEGRAM_TOKEN", ""),
        "tg_chat_id": g("STUDIO", "TELEGRAM_CHAT_ID", ""),
        "tg_errors": gb("STUDIO", "TELEGRAM_ERRORS", True),
        "cookies_enabled": gb("STUDIO", "COOKIES_ENABLED", False),
        "cookies_file": g("STUDIO", "COOKIES_FILE", "cookies.txt"),
        "dedupe_content": gb("STUDIO", "DEDUPE_CONTENT", True),
        "rights_manifest_file": _app_path(
            g("STUDIO", "RIGHTS_MANIFEST_FILE", "rights_manifest.json").strip()
            or "rights_manifest.json"
        ),
        "title_optimize": gb("STUDIO", "TITLE_OPTIMIZE", True),
        "omit_source_page_name": gb("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE", True),
        "auto_shorts": gb("STUDIO", "AUTO_SHORTS", True),
        "title_prefix": g("STUDIO", "TITLE_PREFIX", ""),
        "title_suffix": g("STUDIO", "TITLE_SUFFIX", ""),
        "auto_hashtags": gb("STUDIO", "AUTO_HASHTAGS", True),
        "credit_line": g("STUDIO", "CREDIT_LINE", ""),
        "logo_enabled": gb("STUDIO", "LOGO_ENABLED", False),
        "logo_path": _app_path(g("STUDIO", "LOGO_PATH", "logo.png").strip() or "logo.png"),
        "logo_position": g("STUDIO", "LOGO_POSITION", "أعلى اليمين"),
        "logo_scale": gi("STUDIO", "LOGO_SCALE", 8),
        "logo_opacity": gi("STUDIO", "LOGO_OPACITY", 100),
        "logo_cover_width": gi("STUDIO", "LOGO_COVER_WIDTH", 20),
        "logo_cover_old": gb("STUDIO", "LOGO_COVER_OLD", True),
        "logo_plate": gb("STUDIO", "LOGO_PLATE", False),
        "logo_secondary_enabled": gb("STUDIO", "LOGO_SECONDARY", False),
        "logo_secondary_cover_old": gb("STUDIO", "LOGO_SECONDARY_COVER_OLD", True),
        "playlist_id": g("STUDIO", "PLAYLIST_ID", ""),
        "review_before_upload": False,
        "rights_auto_draft": gb("STUDIO", "RIGHTS_AUTO_DRAFT", True),
        "rights_gate_mode": (g("STUDIO", "RIGHTS_GATE_MODE", "strict").strip() or "strict").lower(),
        "rights_operator_basis": g("STUDIO", "RIGHTS_OPERATOR_BASIS", "owned").strip().lower(),
        "rights_operator_statement": g("STUDIO", "RIGHTS_OPERATOR_STATEMENT", DEFAULT_OPERATOR_RIGHTS_STATEMENT).strip(),
        "rights_operator_evidence": g("STUDIO", "RIGHTS_OPERATOR_EVIDENCE", "").strip(),
        "thumbnail_enabled": gb("STUDIO", "THUMBNAIL_ENABLED", True),
        "shorts_cut_enabled": gb("STUDIO", "SHORTS_CUT", False),
        "min_duration": gi("STUDIO", "MIN_DURATION", 0),
        "max_duration": gi("STUDIO", "MAX_DURATION", 0),
        "category": g("STUDIO", "CATEGORY", "25"),
        "ai_enabled": gb("AI", "ENABLED", False),
        "ai_model": g("AI", "MODEL", GEMINI_MODELS[0]),
        "ai_style": g("AI", "STYLE", "احترافي متوازن"),
        "dry_run": gb("SETTINGS", "DRY_RUN", False),
        "content_review_enabled": content_review_enabled,
        "content_review_config_error": content_review_config_error,
        "cutter_root": g("SAFETY", "CUTTER_ROOT", "").strip(),
        "allow_public_automation": gb("SAFETY", "ALLOW_PUBLIC_AUTOMATION", False),

        "cutter_python": g("SAFETY", "CUTTER_PYTHON", "").strip(),
    }

class _CLIEventSink:
    def __init__(self):
        self.result = None

    def put(self, event):
        if isinstance(event, tuple) and len(event) == 2 and event[0] == "finished":
            self.result = event[1]


def _run_cli_preflight(settings):
    token_path = os.path.join(
        APP_DIRECTORY,
        f"{sanitize_account_name(settings['account'])}_token.json",
    )
    code, report = run_automation_preflight(APP_DIRECTORY, token_path, SCOPES)
    errors = []
    if settings.get("min_disk_config_error"):
        errors.append("قيمة SETTINGS.MIN_DISK_GB غير صالحة؛ أدخل رقماً محدوداً يساوي صفرًا أو أكبر.")
    if not any(page.get("enabled", True) for page in load_pages()):
        errors.append("لا توجد صفحات Facebook مفعّلة في pages.json.")
    if settings.get("content_review_config_error") and not settings.get("dry_run"):
        errors.append("قيمة SAFETY.ENABLED غير صالحة؛ استخدم true أو false.")
    errors.extend(cli_rights_preflight_errors(settings))

    errors.extend(validate_content_review_settings(
        settings.get("content_review_enabled") and not settings.get("dry_run"),
        settings.get("cutter_root", ""),
        settings.get("cutter_python", ""),
    ))
    errors.extend(validate_publication_controls(
        settings.get("privacy", "private"),
        bool(settings.get("schedule_enabled")),
        False,
        bool(settings.get("allow_public_automation")),
        dry_run=bool(settings.get("dry_run")),
    ))
    if errors:
        report = f"{report}\n\nإعدادات التشغيل الآلي:\n" + "\n".join(f"• {error}" for error in errors)
        code = 2
    print(report)
    logger = _configure_cli_logger()
    for line in report.splitlines():
        logger.info("%s", line)
    return code


def _configure_cli_logger():
    logger = logging.getLogger("fb_youtube_uploader.automation")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path = os.path.abspath(os.path.join(APP_DIRECTORY, "automation.log"))
    if not any(getattr(handler, "baseFilename", None) == log_path for handler in logger.handlers):
        handler = SecureRotatingFileHandler(
            log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(secure_log_handler(handler))
    return logger


def run_cli(dry_run=False, once=False, preflight_only=False):
    """تشغيل فحص الجاهزية أو المراقبة المستمرة أو دورة واحدة دون واجهة."""
    settings = build_cli_settings()
    if dry_run:
        settings["dry_run"] = True
    if preflight_only:
        return _run_cli_preflight(settings)
    shim = object.__new__(App)
    shim._stop = threading.Event()
    shim._uiq = _CLIEventSink()
    shim._logq = queue.Queue()
    shim.stats = load_stats()
    cli_logger = _configure_cli_logger()

    def cli_log(msg, replace_last=False):
        msg = redact_sensitive_text(msg)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] {msg}", flush=True)
        cli_logger.info("%s", msg)
    shim._log = cli_log

    if settings.get("content_review_config_error") and not settings.get("dry_run"):
        print("قيمة SAFETY.ENABLED غير صالحة؛ استخدم true أو false قبل تشغيل الرفع.")
        return 2
    review_errors = validate_content_review_settings(
        settings.get("content_review_enabled") and not settings.get("dry_run"),
        settings.get("cutter_root", ""),
        settings.get("cutter_python", ""),
    )
    if review_errors:
        print("إعداد بوابة مراجعة المحتوى غير صالح:")
        for error in review_errors:
            print(f"• {error}")
        return 2
    publication_errors = validate_publication_controls(
        settings.get("privacy", "private"),
        bool(settings.get("schedule_enabled")),
        False,
        bool(settings.get("allow_public_automation")),
        dry_run=bool(settings.get("dry_run")),
    )
    if publication_errors:
        print("إعدادات النشر العام أو الجدولة غير آمنة لوضع CLI:")
        for error in publication_errors:
            print(f"• {error}")
        return 2
    pages = [p for p in load_pages() if p.get("enabled", True)]
    if not pages:
        print("لا توجد صفحات مفعّلة في pages.json")
        return 1
    preflight_code = _run_cli_preflight(settings)
    if preflight_code:
        print("أُوقف التشغيل قبل الرفع لأن فحص الجاهزية لم يجتز.")
        return preflight_code
    settings["cli_mode"] = True
    print("═══════════════════════════════════════════")
    print(f" FB → YouTube v{__version__} PRO — وضع سطر الأوامر")
    mode = "تشغيل مرة واحدة" if once else f"المراقبة كل {settings['interval_minutes']} دقيقة"
    print(f" الصفحات: {len(pages)} | الوضع: {mode}")
    print(f" الحساب: {settings['account']} | كوكيز: {'مفعّلة' if settings['cookies_enabled'] else 'غير مفعّلة'}")
    if settings.get("dry_run"):
        print(" وضع المحاكاة (DRY-RUN): كل شيء يعمل بدون رفع فعلي إلى YouTube")
    print(" للإيقاف: Ctrl+C")
    print("═══════════════════════════════════════════")
    interrupted = False
    try:
        App._worker(shim, pages, settings, watch_mode=not once)
    except KeyboardInterrupt:
        print("\nأُوقف بواسطة المستخدم (Ctrl+C)")
        shim._stop.set()
        interrupted = True
    return automation_exit_code(shim._uiq.result, interrupted=interrupted)

def gui_main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fb-youtube-uploader",
        description="أداة سطح مكتب لمراجعة ورفع الفيديوهات، مع وضع CLI اختياري.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"FB → YouTube Uploader v{__version__} PRO",
    )
    parser.add_argument("--cli", action="store_true", help="تشغيل وضع المراقبة في الطرفية")
    parser.add_argument("--once", action="store_true", help="معالجة دورة واحدة ثم التوقف؛ يتطلب --cli")

    parser.add_argument("--dry-run", action="store_true", help="محاكاة دون رفع؛ يتطلب --cli")
    parser.add_argument("--preflight", action="store_true", help="فحص كل متطلبات التشغيل دون سحب أو رفع")
    args = parser.parse_args(argv)
    if args.preflight and (args.cli or args.once or args.dry_run):
        parser.error("--preflight cannot be combined with --cli, --once, or --dry-run")
    if (args.dry_run or args.once) and not args.cli:
        parser.error("--dry-run and --once require --cli")
    if args.preflight:
        return run_cli(preflight_only=True)
    if args.cli:
        return run_cli(dry_run=args.dry_run, once=args.once)
    return gui_main()


if __name__ == "__main__":
    os.chdir(APP_DIRECTORY)
    sys.exit(main())
