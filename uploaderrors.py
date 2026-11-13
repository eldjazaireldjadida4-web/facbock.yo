"""uploaderrors.py — تصنيف أخطاء رفع YouTube ودوال الاستجابة لها.

الاستيراد آمن: لا اعتماديات خارجية ولا قراءة إعدادات عند import.
"""

from __future__ import annotations

import json

RETRYABLE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
CHANNEL_LIMIT_REASONS = {"uploadLimitExceeded"}
NON_RETRYABLE_STATUSES = {400, 401, 409}
# 404/410 داخل جلسة رفع قابلة للاستئناف تعني أن الجلسة انتهت أو أُلغيت، لا أن
# الطلب نفسه مرفوض: إعادة المحاولة على العنوان نفسه ميؤوس منها، وتسجيلها
# «مرفوضاً» يحذف الملف ويمنع الإعادة الآلية.
SESSION_EXPIRED_STATUSES = {404, 410}


def _http_status(error):
    resp = getattr(error, "resp", None)
    return getattr(resp, "status", 0) if resp is not None else 0


def _http_reason(error):
    try:
        content = getattr(error, "content", b"") or b""
        data = json.loads(content.decode("utf-8", "ignore"))
        errors = (data.get("error") or {}).get("errors") or []
        if errors and isinstance(errors[0], dict):
            return errors[0].get("reason", "") or ""
    except (ValueError, AttributeError, TypeError):
        pass
    return ""


def classify_upload_error(error):
    """يصنّف الحصة، حد القناة اليومي، التحديد المؤقت، والصلاحيات بشكل مستقل.

    `uploadLimitExceeded` هو حد رفع للقناة، وليس حصة مشروع API.
    """
    status = _http_status(error)
    reason = _http_reason(error)
    if reason in CHANNEL_LIMIT_REASONS:
        return "channel_limit"
    if reason in QUOTA_REASONS:
        return "quota"
    if reason in RETRYABLE_REASONS:
        return "retryable"
    if status == 429 or status in (408, 425, 500, 502, 503, 504):
        return "retryable"
    if status == 403:
        return "auth"
    if status in SESSION_EXPIRED_STATUSES:
        return "session_expired"
    if status in NON_RETRYABLE_STATUSES:
        return "fatal"
    return "retryable"
