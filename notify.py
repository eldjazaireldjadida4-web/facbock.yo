"""notify.py — إشعارات Telegram الاختيارية، معزولة عن التطبيق الرئيسي.

الاستيراد آمن: لا قراءة إعدادات ولا نداءات شبكة عند import.
دالة ملخص الدورة تقبل دالة الإرسال صراحةً (send) حتى تبقى قابلية الاستبدال
في الاختبارات والامتدادات مستقبلية دون لمس منطق التنسيق.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request

from safe_logging import redact_sensitive_text

# صيغة رمز بوت Telegram: معرّف رقمي ثم نقطتان ثم سرّ بلا فواصل. التحقق قبل
# بناء الرابط يمنع رمزاً فيه مسافة أو سطر جديد من الظهور كاملاً في رسالة
# استثناء (http.client يطبع الرابط كاملاً عند وجود محرف تحكم).
_TELEGRAM_TOKEN_RE = re.compile(r"^[0-9]+:[A-Za-z0-9_-]+$")


def send_cycle_summary(ctx, settings, stats, log_cb=None, send=None):
    """يرسل ملخص الدورة (وايدي/محاكاة) ويسجله في السجل المحلي."""
    sender = send or send_telegram
    up_count = stats.get("up_count", 0)
    fail_count = stats.get("fail_count", 0)
    pending = stats.get("pending", 0)
    dry_count = stats.get("dry_count", 0)
    dry_run = stats.get("dry_run", False)
    elapsed = max(0, int(stats.get("elapsed", 0)))
    minutes, seconds = divmod(elapsed, 60)
    quota = ctx.get("quota")
    quota_txt = f"{quota.used}/{quota.daily_limit}" if quota else "غير متوفر"
    summary = ("اكتملت الدورة — "
               f"مرفوع: {up_count}, فاشل/مؤجل: {fail_count}, "
               f"قيد الانتظار: {pending}, محاكاة: {dry_count}, "
               f"الحصة: {quota_txt}, الوقت: {minutes:02d}:{seconds:02d} ({elapsed} ثانية)")
    if dry_run:
        summary += " (وضع المحاكاة — لم يُرفع شيء)"
    if log_cb:
        log_cb(summary)
    # دورة فشلت بالكامل كانت لا تُرسل شيئاً: أسوأ نتيجة هي الأصمت.
    if up_count or dry_run or fail_count:
        sender(settings, f"ملخص الدورة — {summary}", log_cb)


def send_telegram(settings, text, log_cb=None):
    if not settings.get("telegram_enabled"):
        return
    token = (settings.get("tg_token") or "").strip()
    chat_id = (settings.get("tg_chat_id") or "").strip()
    if not token or not chat_id:
        return
    if not _TELEGRAM_TOKEN_RE.match(token):
        logging.warning("صيغة رمز Telegram غير صالحة؛ لم يُرسل الإشعار.")
        if log_cb:
            log_cb("صيغة رمز Telegram غير صالحة (يجب أن يكون رقماً ثم نقطتين ثم السر)؛ لم يُرسل الإشعار.")
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat_id, "text": text, "disable_web_page_preview": "true",
        }).encode()
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15
        ) as response:
            response.read(1024)
    except Exception as e:
        # نوع الخطأ فقط: نص الاستثناء قد يحتوي الرابط كاملاً بما فيه الرمز.
        safe_detail = redact_sensitive_text(str(e))[:200]
        logging.warning("فشل إشعار Telegram (%s): %s", type(e).__name__, safe_detail)
        if log_cb:
            log_cb(f"فشل إرسال إشعار Telegram ({type(e).__name__}): {safe_detail}")