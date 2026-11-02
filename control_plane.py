from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
import time

import re
from urllib.parse import parse_qs, urlsplit

import portalocker
from portalocker.exceptions import LockException

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from content_review_bridge import build_review_caption, run_review

QueueItem = dict[str, Any]
PolicyLog = Callable[[str], None]
UPLOAD_IN_PROGRESS = "upload_in_progress"
UPLOAD_RESULT_UNKNOWN = "upload_result_unknown"
UPLOAD_NOT_STARTED = "upload_not_started"
UPLOAD_REJECTED = "upload_rejected"
MANUAL_RETRY_APPROVED = "manual_retry_approved"


def begin_upload_attempt(
    items: Iterable[QueueItem] | None,
    entry: Mapping[str, Any],
    now: float | None = None,
) -> list[QueueItem]:
    item = dict(entry)
    source_id = str(item.get("id") or "").strip()
    if not source_id:
        raise ValueError("An upload attempt must have a source ID.")
    previous = next(
        (dict(current) for current in items or []
         if isinstance(current, Mapping)
         and str(current.get("id") or "").strip() == source_id),
        None,
    )
    if previous and previous.get("last_error") in {UPLOAD_IN_PROGRESS, UPLOAD_RESULT_UNKNOWN}:
        raise RuntimeError("An unresolved upload result must be reconciled before retrying.")
    started_at = float(time.time() if now is None else now)
    item["last_error"] = UPLOAD_IN_PROGRESS
    item["upload_started_at"] = started_at
    item["failed_at"] = item.get("failed_at") or started_at
    item.pop("unknown_since", None)
    item.pop("upload_error", None)
    return upsert_queue_item(items, item)


def mark_upload_result_unknown(
    items: Iterable[QueueItem] | None,
    source_id: str,
    reason: str = "",
    now: float | None = None,
) -> list[QueueItem]:
    target = str(source_id or "").strip()
    if not target:
        raise ValueError("An uncertain upload must have a source ID.")
    result = []
    found = False
    timestamp = float(time.time() if now is None else now)
    for current in items or []:
        if not isinstance(current, Mapping):
            continue
        item = dict(current)
        if str(item.get("id") or "").strip() == target:
            item["last_error"] = UPLOAD_RESULT_UNKNOWN
            item["unknown_since"] = item.get("unknown_since") or item.get("upload_started_at") or timestamp
            if reason:
                item["upload_error"] = str(reason)[:500]
            found = True
        result.append(item)
    if not found:
        raise ValueError(f"No persisted upload attempt exists for {target}.")
    return result

def upsert_queue_item(items: Iterable[QueueItem] | None, entry: Mapping[str, Any]) -> list[QueueItem]:
    item = dict(entry)
    source_id = str(item.get("id") or "").strip()
    if not source_id:
        raise ValueError("A queued upload must have a source ID.")
    result = []
    inserted = False
    for current in items or []:
        if not isinstance(current, Mapping):
            continue
        current_item = dict(current)
        if str(current_item.get("id") or "").strip() == source_id:
            if not inserted:
                result.append({**current_item, **item})
                inserted = True
        else:
            result.append(current_item)
    if not inserted:
        result.append(item)
    return result


def deduplicate_queue_items(items: Iterable[QueueItem] | None) -> list[QueueItem]:
    protected = {
        UPLOAD_IN_PROGRESS: 100,
        UPLOAD_RESULT_UNKNOWN: 110,
        UPLOAD_REJECTED: 90,
        "abandoned": 80,
        "content_safety_review_required": 80,
        "publication_approval_required": 80,
        "duplicate_review_required": 80,
        "rights_review_required": 80,
    }
    result: list[QueueItem] = []
    positions: dict[str, int] = {}
    for current in items or []:
        if not isinstance(current, Mapping):
            continue
        item = dict(current)
        source_id = str(item.get("id") or "").strip()
        if not source_id:
            continue
        if source_id not in positions:
            positions[source_id] = len(result)
            result.append(item)
            continue
        index = positions[source_id]
        previous = result[index]
        previous_error = previous.get("last_error")
        current_error = item.get("last_error")
        merged = {**previous, **item}
        try:
            merged["attempts"] = max(int(previous.get("attempts", 0)), int(item.get("attempts", 0)))
        except (TypeError, ValueError):
            merged["attempts"] = previous.get("attempts", item.get("attempts", 0))
        if protected.get(previous_error, 0) > protected.get(current_error, 0):
            merged["last_error"] = previous_error
        result[index] = merged
    return result


def remove_queue_item(items: Iterable[QueueItem] | None, source_id: str) -> list[QueueItem]:
    target = str(source_id or "").strip()
    return [dict(item) for item in items or []
            if isinstance(item, Mapping) and str(item.get("id") or "").strip() != target]


def recover_interrupted_uploads(
    items: Iterable[QueueItem] | None,
    uploaded_ids: Iterable[str] = (),
) -> tuple[list[QueueItem], int]:
    completed = {str(value).strip() for value in uploaded_ids if str(value).strip()}
    result: list[QueueItem] = []
    positions: dict[str, int] = {}
    recovered = 0
    uncertain_states = {UPLOAD_IN_PROGRESS, UPLOAD_RESULT_UNKNOWN}
    for current in items or []:
        if not isinstance(current, Mapping):
            continue
        item = dict(current)
        source_id = str(item.get("id") or "").strip()
        if not source_id or source_id in completed:
            continue
        if item.get("last_error") == UPLOAD_IN_PROGRESS:
            item["last_error"] = UPLOAD_RESULT_UNKNOWN
            item["unknown_since"] = item.get("upload_started_at") or time.time()
            recovered += 1
        if source_id not in positions:
            positions[source_id] = len(result)
            result.append(item)
            continue
        index = positions[source_id]
        previous = result[index]
        previous_uncertain = previous.get("last_error") in uncertain_states
        current_uncertain = item.get("last_error") in uncertain_states
        if current_uncertain and not previous_uncertain:
            result[index] = {**previous, **item}
        elif current_uncertain == previous_uncertain:
            result[index] = {**previous, **item}
    return result, recovered


YOUTUBE_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def extract_youtube_video_id(value: str) -> str | None:
    raw = str(value or "").strip()
    if YOUTUBE_VIDEO_ID_RE.fullmatch(raw):
        return raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = (parts.path or "").strip("/").split("/", 1)[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        candidate = parse_qs(parts.query).get("v", [""])[0]
        if not candidate:
            segments = [segment for segment in (parts.path or "").split("/") if segment]
            if len(segments) >= 2 and segments[0].lower() in {"shorts", "embed", "live"}:
                candidate = segments[1]
    else:
        return None
    return candidate if YOUTUBE_VIDEO_ID_RE.fullmatch(candidate or "") else None


def youtube_video_belongs_to_current_channel(youtube: Any, video_id: str) -> bool:
    candidate = str(video_id or "").strip()
    if not YOUTUBE_VIDEO_ID_RE.fullmatch(candidate):
        return False
    channel_response = youtube.channels().list(
        part="id", mine=True, maxResults=1
    ).execute()
    channels = channel_response.get("items", [])
    if not channels or not channels[0].get("id"):
        raise RuntimeError("لم يعثر YouTube على القناة المرتبطة بالحساب الحالي.")
    channel_id = channels[0]["id"]
    video_response = youtube.videos().list(
        part="snippet", id=candidate, maxResults=1
    ).execute()
    return any(
        str(item.get("id") or "") == candidate
        and str(item.get("snippet", {}).get("channelId") or "") == channel_id
        for item in video_response.get("items", [])
    )


def reconcile_upload_result(
    items: Iterable[QueueItem] | None,
    source_id: str,
    video_exists: bool,
) -> tuple[list[QueueItem], str | None]:
    target = str(source_id or "").strip()
    result = []
    decision = None
    for current in items or []:
        if not isinstance(current, Mapping):
            continue
        item = dict(current)
        if (str(item.get("id") or "").strip() == target
                and item.get("last_error") == UPLOAD_RESULT_UNKNOWN):
            if video_exists:
                decision = "uploaded"
                continue
            item["last_error"] = MANUAL_RETRY_APPROVED
            item["manual_retry_approved_at"] = time.time()
            decision = "retry"
        result.append(item)
    return result, decision


def split_duplicate_review_queue(
    items: Iterable[QueueItem] | None,
    review_enabled: bool,
) -> tuple[list[QueueItem], list[QueueItem]]:
    remaining = []
    safety_held = []
    for item in items or []:
        if item.get("last_error") in {
            "content_safety_review_required",
            "publication_approval_required",
            UPLOAD_IN_PROGRESS,
            UPLOAD_RESULT_UNKNOWN,
            UPLOAD_REJECTED,
            "abandoned",
        }:
            safety_held.append(item)
        else:
            remaining.append(item)
    if review_enabled:
        return remaining, safety_held
    held_errors = {"duplicate_review_required", "rights_review_required"}
    held = [item for item in remaining if item.get("last_error") in held_errors]
    ready = [item for item in remaining if item.get("last_error") not in held_errors]
    return ready, held + safety_held


def release_policy_holds(
    items: Iterable[QueueItem] | None,
) -> tuple[list[QueueItem], int]:
    released = []
    count = 0
    for item in items or []:
        entry = dict(item)
        if entry.get("last_error") in {
            "content_safety_review_required",
            "publication_approval_required",
            "rights_review_required",
            UPLOAD_REJECTED,
            "abandoned",
        }:
            entry["last_error"] = ""
            count += 1
        released.append(entry)
    return released, count


def release_rights_holds(
    items: Iterable[QueueItem] | None,
) -> tuple[list[QueueItem], int]:
    released = []
    count = 0
    for item in items or []:
        entry = dict(item)
        if entry.get("last_error") == "rights_review_required":
            entry["last_error"] = ""
            count += 1
        released.append(entry)
    return released, count


def validate_content_review_settings(
    enabled: bool,
    cutter_root: str | Path,
    cutter_python: str | Path = "",
) -> list[str]:
    if not enabled:
        return []
    errors = []
    root_value = str(cutter_root or "").strip()
    root = Path(root_value).expanduser()
    if not root_value:
        errors.append("حدد مجلد OUSSAMA Cutter.")
    elif not root.is_dir():
        errors.append("مجلد OUSSAMA Cutter غير موجود أو غير صالح.")
    elif not (root / "scripts" / "upload_content_review.py").is_file():
        errors.append("لم يُعثر على scripts/upload_content_review.py داخل مجلد Cutter.")
    python_value = str(cutter_python or "").strip()
    if python_value and not Path(python_value).expanduser().is_file():
        errors.append("مفسّر Python المحدد لمراجعة المحتوى غير موجود.")
    return errors


def validate_publication_controls(
    privacy: str,
    schedule_enabled: bool,
    review_before_upload: bool,
    allow_public_automation: bool,
    dry_run: bool = False,
) -> list[str]:
    if dry_run:
        return []
    visibility = str(privacy or "").strip().lower()
    if visibility not in {"private", "unlisted", "public"}:
        return ["قيمة الخصوصية غير صالحة."]
    manual_or_explicit = review_before_upload or allow_public_automation
    errors = []
    if visibility != "private" and not manual_or_explicit:
        errors.append("النشر العام أو غير المدرج يحتاج معاينة بشرية أو سماحاً صريحاً بالنشر الآلي.")
    if schedule_enabled and not manual_or_explicit:
        errors.append("الجدولة تحتاج معاينة بشرية أو سماحاً صريحاً بالنشر الآلي.")
    return errors


def run_policy_review(
    video_data: Mapping[str, Any],
    tags: Iterable[str] | None,
    settings: Mapping[str, Any],
    log_cb: PolicyLog | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if not settings.get("content_review_enabled"):
        return None, "disabled"
    if settings.get("dry_run"):
        return None, "dry_run_skipped"
    caption = build_review_caption(str(video_data.get("desc", "")), tags)
    report, status = run_review(
        str(video_data.get("file", "")),
        str(video_data.get("title", "")),
        caption,
        cutter_root=str(settings.get("cutter_root", "")),
        python_executable=str(settings.get("cutter_python", "")),
    )
    if log_cb:
        if status == "clear" and isinstance(report, dict):
            log_cb(f"اجتازت مراجعة المحتوى {report.get('review_windows', 0)} نافذة نصية.")
        else:
            log_cb(f"بوابة أمان المحتوى أوقفت الرفع: {status}.")
    return report, status


def build_upload_review_warnings(payload: Mapping[str, Any]) -> list[str]:
    if not payload.get("content_review_enabled"):
        return []
    if payload.get("dry_run"):
        return ["وضع المحاكاة: لم يُرسل الفيديو إلى أداة المراجعة، ولا توجد موافقة صالحة للنشر."]
    report = payload.get("content_safety_review")
    if not isinstance(report, dict) or report.get("status") != "clear":
        return ["لم يكتمل فحص سياسة المحتوى؛ لا يجوز الرفع."]
    windows = report.get("review_windows")
    if type(windows) is int and windows > 0:
        return [
            f"اجتازت {windows} نافذة مراجعة نصية؛ الفحص لا يحلل الصور ولا يضمن قرار YouTube.",
        ]
    return ["اكتملت مراجعة المحتوى، لكن التقرير لا يتضمن عدد نوافذ صالحاً."]


class AutomationAlreadyRunning(RuntimeError):
    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        super().__init__(f"Another automation process holds {self.lock_path.name}.")


class AutomationRunLock:
    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path).expanduser().resolve()
        self._handle = None

    def acquire(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except LockException as error:
            handle.close()
            raise AutomationAlreadyRunning(self.lock_path) from error
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\nstarted_at={datetime.now(timezone.utc).isoformat()}\n")
            handle.flush()
        except Exception:
            portalocker.unlock(handle)
            handle.close()
            raise
        self._handle = handle
        return self

    def release(self):
        handle = self._handle
        if handle is None:
            return
        try:
            portalocker.unlock(handle)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()


def validate_unattended_oauth_token(
    token_path: str | Path,
    required_scopes: Iterable[str],
) -> list[str]:
    path = Path(token_path).expanduser()
    if not path.is_file():
        return [f"ملف تفويض YouTube {path.name} مفقود."]
    try:
        with path.open("r", encoding="utf-8") as token_file:
            token = json.load(token_file)
    except (OSError, json.JSONDecodeError):
        return [f"ملف تفويض YouTube {path.name} غير قابل للقراءة أو غير صالح."]
    if not isinstance(token, dict):
        return [f"بنية ملف تفويض YouTube {path.name} غير صالحة."]
    errors = []
    if not token.get("refresh_token"):
        errors.append("ملف التفويض لا يحتوي على refresh token؛ سجّل الدخول مرة من الواجهة أولاً.")
    scopes = token.get("scopes")
    granted_scopes = set(scopes) if isinstance(scopes, list) else set()
    missing_scopes = sorted(set(required_scopes) - granted_scopes)
    if missing_scopes:
        errors.append("يحتاج ملف التفويض إلى موافقة جديدة لصلاحيات رفع وقراءة YouTube.")
    return errors


def run_automation_preflight(
    project_directory: str | Path,
    token_path: str | Path,
    required_scopes: Iterable[str],
    timeout: int = 90,
) -> tuple[int, str]:
    code, report = run_readiness_check(project_directory, timeout=timeout)
    errors = validate_unattended_oauth_token(token_path, required_scopes)
    if errors:
        section = "\n".join(f"• {error}" for error in errors)
        report = f"{report}\n\nجاهزية OAuth:\n{section}".strip()
        if code == 0:
            code = 2
    return code, report


def automation_exit_code(result: Mapping[str, Any] | None, interrupted: bool = False) -> int:
    if interrupted:
        return 130
    if not isinstance(result, Mapping):
        return 1
    if result.get("fatal"):
        return 2
    try:
        failures = int(result.get("failed", 0) or 0)
        pending = int(result.get("pending", 0) or 0)
        network_errors = int(result.get("network_errors", 0) or 0)
    except (TypeError, ValueError):
        return 2
    return 1 if failures > 0 or pending > 0 or network_errors > 0 else 0


def run_readiness_check(project_directory: str | Path, timeout: int = 90) -> tuple[int, str]:
    root = Path(project_directory).expanduser().resolve()
    doctor = root / "doctor.py"
    if not doctor.is_file():
        return 2, f"لم يُعثر على أداة التشخيص: {doctor}"
    try:
        result = subprocess.run(
            [sys.executable, str(doctor)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 2, f"انتهت مهلة فحص الجاهزية.\n{output}".strip()
    except OSError as error:
        return 2, f"تعذر تشغيل فحص الجاهزية: {error}"
    output = result.stdout.strip()
    if result.stderr:
        output = f"{output}\n\n{result.stderr.strip()}".strip()
    return result.returncode, output
