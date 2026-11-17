# -*- coding: utf-8 -*-
"""أنبوب الرفع: فلاتر المدة والفئات، التحقق من الملف، وإرسال الفيديو إلى YouTube.

منطق الرفع معزول هنا؛ الواجهة والجدولة تبقيان في fb_youtube_uploader_v49_pro.py.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from content_review_bridge import (
    build_review_caption,
    validate_report as validate_content_review,
)
from control_plane import (
    extract_youtube_video_id,
    validate_publication_controls,
)
from safe_logging import redact_sensitive_text
from texttools import (
    YOUTUBE_DESCRIPTION_MAX,
    YOUTUBE_TITLE_MAX,
    is_generic_title,
    normalize_youtube_tags,
    normalize_youtube_title,
)
from pipeline_errors import PipelineFailure
from uploaderrors import (
    _http_status,
    classify_upload_error,
)
from control_plane import (
    UPLOAD_NOT_STARTED,
    UPLOAD_REJECTED,
    UPLOAD_RESULT_UNKNOWN,
)
from file_security import restrict_file_permissions
from texttools import preserve_source_title


def _app_hook(name):
    """استدعاء مؤجّل لدوال الحالة في الملف الرئيسي لتجنّب الاستيراد الدائري."""
    import fb_youtube_uploader_v49_pro as app
    return getattr(app, name)


MAX_RETRIES         = 3
# عدد مرات استئناف الرفع داخل نفس الجلسة عند انقطاع مؤقت (لا يُعيد الملف من الصفر).
MAX_CHUNK_RETRIES   = 5
# نبضات بلا تقدّم فعلي في جلسة رفع مستأنفة قبل إعلان النتيجة غير مؤكدة.
MAX_IDLE_CHUNKS     = 3
IDLE_CHUNK_WAIT_SECONDS = 2

# ════════════════════════════════════════════════════
# CATEGORY & DURATION FILTERS
# ════════════════════════════════════════════════════
YT_CATEGORIES = {
    "ترفيه (24)": "24", "أخبار وسياسة (25)": "25", "رياضة (17)": "17",
    "أشخاص ومدونات (22)": "22", "كوميديا (23)": "23", "موسيقى (10)": "10",
    "ألعاب (20)": "20", "علوم وتقنية (28)": "28", "تعليم (27)": "27",
}

RETRY_DELAY = 8


def _backoff_seconds(attempt):
    """تراجع أسّي مع رجّة عشوائية (8 → 24 → 72… بحد 300 ثانية)."""
    base = min(RETRY_DELAY * (3 ** (attempt - 1)), 300)
    return base + random.uniform(0, RETRY_DELAY / 2)


def duration_ok(duration, min_sec, max_sec):
    """فلتر مدة الفيديو: 0 في الحد الأقصى يعني بلا سقف."""
    if duration and min_sec and duration < min_sec:
        return False, f"أقصر من {min_sec} ثانية ({int(duration)}ث)"
    if duration and max_sec and duration > max_sec:
        return False, f"أطول من {max_sec} ثانية ({int(duration)}ث)"
    return True, ""

def next_publish_time(min_delay, max_delay):
    """جدولة تراكمية محفوظة — لا تتداخل مواعيد النشر حتى بين عمليات التشغيل."""
    state = _app_hook("load_state")()
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
    _app_hook("save_state")(state)
    return pub

def validate_media(path, log_cb=None, *, expected_duration=None):
    # ffprobe يقرأ المسار الأخير كخيار إن بدأ بـ«-»؛ المسار المطلق يزيل الحالة.
    if isinstance(path, (str, os.PathLike)):
        path = os.path.abspath(os.fspath(path))

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
    if file_size > 256_000_000_000:
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
    preserve_facebook_title = bool(video_data.get("preserve_facebook_title", False))
    if preserve_facebook_title:
        upload_title = preserve_source_title(raw_title)
        if not upload_title:
            if log_cb:
                log_cb("أُوقف الرفع قبل الإرسال لأن عنوان Facebook فارغ.")
            return "INVALID_TITLE"
        if len(upload_title) > YOUTUBE_TITLE_MAX:
            if log_cb:
                log_cb("أُوقف الرفع: عنوان Facebook الأصلي يتجاوز حد YouTube البالغ 100 حرف؛ اختصره يدوياً ثم أعد مراجعته.")
            return "INVALID_TITLE"
        if upload_title != raw_title:
            if log_cb:
                log_cb("تغيّر العنوان أثناء تنظيف محارف غير صالحة؛ لم يبدأ الرفع، راجعه يدوياً.")
            return "INVALID_TITLE"
    else:
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
    if is_generic_title(upload_title):
        # «مقطع فيديو» و«Video» عناوين بديلة من مسار التنظيف؛ رفعها يعني
        # فيديو بلا عنوان حقيقي على YouTube.
        if log_cb:
            log_cb(
                f"أُوقف الرفع: العنوان «{upload_title[:40]}» عام أو بديل وليس عنواناً حقيقياً. "
                "أدخل عنواناً واضحاً في نافذة المراجعة."
            )
        return "INVALID_TITLE"
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
            if kind == "session_expired":
                if log_cb:
                    log_cb("رفض YouTube إنشاء جلسة الرفع (404/410)؛ لم يبدأ الرفع.")
                return UPLOAD_NOT_STARTED
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
        last_progress = 0
        upload_request_started = False
        while response is None:
            if stop_event is not None and stop_event.is_set():
                if log_cb:
                    if upload_request_started:
                        log_cb("توقف الرفع بعد بدء جلسة YouTube؛ النتيجة غير مؤكدة، تحقق من YouTube Studio قبل إعادة المحاولة.")
                    else:
                        log_cb("أُوقف الرفع قبل إرسال أول جزء إلى YouTube؛ سيبقى آمناً لإعادة المحاولة.")
                if not upload_request_started and quota_manager is not None:
                    # الحصة تُحجز قبل أول جزء؛ إيقاف المستخدم هنا لم يستهلك
                    # طلباً فعلياً، فإبقاء الخصم يحرق مقعداً كل إيقاف.
                    try:
                        quota_manager.refund()
                    except Exception:
                        logging.exception("تعذر إرجاع حصة رفع لم يبدأ")
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
                if kind == "session_expired":
                    if log_cb:
                        log_cb("انتهت جلسة الرفع المستأنفة (404/410)؛ تعذّر الاستئناف بأمان. تحقق من YouTube Studio قبل أي إعادة.")
                    return UPLOAD_RESULT_UNKNOWN
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
            if status is not None:
                try:
                    progress_bytes = int(getattr(status, "resumable_progress", 0) or 0)
                except (TypeError, ValueError):
                    progress_bytes = 0
                if progress_bytes <= last_progress:
                    # 308 متكرر دون تقدّم فعلي: next_chunk لا يعيد response في
                    # هذه الحالة، فكانت الحلقة تدور بلا حدّ لأن العدّاد لا يزيد.
                    idle_chunks += 1
                    if idle_chunks > MAX_IDLE_CHUNKS:
                        if log_cb:
                            log_cb("لم تتقدم جلسة الرفع؛ النتيجة غير مؤكدة وتم إيقافها لمنع التكرار. تحقق من YouTube Studio.")
                        return UPLOAD_RESULT_UNKNOWN
                    if wait_or_stop(IDLE_CHUNK_WAIT_SECONDS):
                        return UPLOAD_RESULT_UNKNOWN
                else:
                    last_progress = progress_bytes
                    idle_chunks = 0
                    # تقدّم فعلي يعني أن الأخطاء السابقة كانت عابرة؛ تراكمها
                    # عبر رفع يستغرق ساعات كان يجهض جلسة سليمة.
                    chunk_failures = 0
                last_pct = int(status.progress() * 100)
                if log_cb:
                    log_cb(f"جاري الرفع: {last_pct}%", replace_last=True)
            elif response is None:
                idle_chunks += 1
                if idle_chunks > MAX_IDLE_CHUNKS:
                    if log_cb:
                        log_cb("لم تتقدم جلسة الرفع؛ النتيجة غير مؤكدة وتم إيقافها لمنع التكرار. تحقق من YouTube Studio.")
                    return UPLOAD_RESULT_UNKNOWN
                if wait_or_stop(IDLE_CHUNK_WAIT_SECONDS):
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
        verification = None
        try:
            verification = verify_upload(
                youtube, video_id, body["status"]["privacyStatus"], sched_time, log_cb,
                expected_title=upload_title,
            )
        except Exception as error:
            logging.exception("رُفع الفيديو لكن تعذّر التحقق من حالته")
            if log_cb:
                log_cb(f"رُفع الفيديو؛ تعذّر التحقق من الحالة: {error}")
        rejection = upload_status_failure(verification)
        if rejection:
            # الإيصال حُفظ أولاً عمداً (حماية من التكرار). لكن قبول الطلب ثم
            # رفض المعالجة يعني أن الفيديو غير منشور: يُعلَّم الإيصال مرفوضاً
            # حتى لا يعدّه التشغيل التالي «مرفوعاً»، ولا يُحتسب نجاحاً.
            try:
                _app_hook("mark_upload_receipt_rejected")(video_id, rejection)
            except Exception:
                logging.exception("تعذر تعليم إيصال الرفع كمرفوض")
            if log_cb:
                log_cb(
                    f"YouTube رفض الفيديو بعد قبول الطلب ({rejection})؛ لن يُحتسب نجاحاً. "
                    "راجع إشعارات YouTube Studio ثم حرّر العنصر يدوياً بعد إصلاح السبب."
                )
            return UPLOAD_REJECTED
        if log_cb:
            log_cb(f"تم الرفع بنجاح. ID: {video_id}")
        if playlist_id:
            try:
                _insert_to_playlist(youtube, playlist_id, video_id, log_cb)
            except Exception as error:
                logging.exception("رُفع الفيديو لكن تعذّرت إضافة قائمة التشغيل")
                if log_cb:
                    log_cb(f"رُفع الفيديو؛ تعذّرت إضافة قائمة التشغيل: {error}")
        return video_id
    return UPLOAD_NOT_STARTED


def _backoff_seconds(attempt):
    """تراجع أسّي مع رجّة عشوائية (8 → 24 → 72… بحد 300 ثانية)."""
    base = min(RETRY_DELAY * (3 ** (attempt - 1)), 300)
    return base + random.uniform(0, RETRY_DELAY / 2)


def verify_upload(youtube, video_id, requested_privacy, sched_time=None, log_cb=None,
                  expected_title=None):
    """يتحقق بعد الرفع أن الفيديو ظهر وبالخصوصية/الجدولة المطلوبة (best-effort).

    يكشف الحالة الشهيرة: الفيديو يُرفع لكن يبقى private لأن مشروع Google غير مُدقّق.
    """
    try:
        response = youtube.videos().list(
            part="status,processingDetails,snippet", id=video_id
        ).execute()
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
    item = items[0] if isinstance(items[0], dict) else {}
    status = item.get("status") or {}
    processing = item.get("processingDetails") or {}
    processing_status = str(processing.get("processingStatus") or "")
    if processing_status:
        status = {**status, "processingStatus": processing_status}
    snippet = item.get("snippet") or {}
    stored_title = str(snippet.get("title") or "")
    if expected_title and stored_title and stored_title != expected_title:
        # «الفيديو بلا عنوان» في الواقع غالباً عنوان بديل أو محارف اتجاه؛
        # قراءته من YouTube تجعل الفرق مرئياً في السجل بدل التخمين.
        logging.warning(
            "عنوان الفيديو %s على YouTube (%r) يختلف عن المُرسل (%r)",
            video_id, stored_title, expected_title,
        )
        if log_cb:
            log_cb(
                f"تنبيه: العنوان المحفوظ على YouTube «{stored_title[:60]}» يختلف عن "
                f"العنوان المُرسل «{expected_title[:60]}»."
            )
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


def upload_status_failure(status):
    """سبب نصي إذا أكّد YouTube أن الفيديو مرفوض أو فشلت معالجته.

    ``verify_upload`` كانت تفحص الخصوصية فقط: فيديو بـ``uploadStatus=rejected``
    كان يُسجَّل «مرفوعاً» ويُحذف إيصال الإعادة، فلا يُعاد ولا يظهر للمشغّل سبب.
    """
    if not isinstance(status, dict):
        return ""
    upload_status = str(status.get("uploadStatus") or "")
    if upload_status in {"rejected", "failed", "deleted"}:
        return f"uploadStatus={upload_status}"
    if status.get("processingStatus") == "failed":
        return "processingStatus=failed"
    reason = status.get("failureReason") or status.get("rejectionReason")
    if reason:
        return f"reason={reason}"
    return ""


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
    cmd = [ffmpeg_bin, "-y", "-nostdin", "-v", "error", "-i", source_path,
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
