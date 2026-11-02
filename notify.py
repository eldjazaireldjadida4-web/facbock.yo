"""notify.py — إشعارات Telegram الاختيارية، معزولة عن التطبيق الرئيسي.

الاستيراد آمن: لا قراءة إعدادات ولا نداءات شبكة عند import.
دالة ملخص الدورة تقبل دالة الإرسال صراحةً (send) حتى تبقى قابلية الاستبدال
في الاختبارات والامتدادات مستقبلية دون لمس منطق التنسيق.
"""
from __future__ import annotations

import logging
import urllib.parse
import urllib.request


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
    if up_count or dry_run:
        sender(settings, f"ملخص الدورة — {summary}", log_cb)


def send_telegram(settings, text, log_cb=None):
    if not settings.get("telegram_enabled"):
        return
    token = (settings.get("tg_token") or "").strip()
    chat_id = (settings.get("tg_chat_id") or "").strip()
    if not token or not chat_id:
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat_id, "text": text, "disable_web_page_preview": "true",
        }).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15
        )
    except Exception as e:
        logging.warning("فشل إشعار Telegram: %s", e)
        if log_cb:
            log_cb(f"فشل إرسال إشعار Telegram: {e}")