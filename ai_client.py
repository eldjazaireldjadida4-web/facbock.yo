"""ai_client.py — عميل Gemini وأدوات إدارة المفاتيح، معزول عن التطبيق الرئيسي.

الاستيراد آمن: لا قراءة إعدادات ولا إنشاء مجلدات ولا نداءات شبكة عند import.
دوال الحفظ والقراءة تُمرَّر صراحةً (قراءة/كتابة JSON الذرية تبقى في مكان واحد
في التطبيق الرئيسي) لتبقى هذه الوحدة قابلة للاختبار بمعزل تام.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
AI_STYLES = {
    "احترافي متوازن": "اكتب بأسلوب احترافي متوازن: واضح، جذاب، وصادق بدون مبالغة.",
    "جذاب (Clickbait معتدل)": "اكتب بأسلوب جذاب يثير الفضول لكن بدون كذب أو وعود كاذبة — العنوان يجب أن يطابق المحتوى.",
    "إخباري رسمي": "اكتب بأسلوب إخباري رسمي موجز كما في النشرات، مع أهم المعلومات أولاً.",
    "رياضي حماسي": "اكتب بأسلوب رياضي حماسي مشوّق يليق بعشاق الرياضة.",
}

def load_ai_keys(keys_path, read_json):
    keys = read_json(keys_path, [])
    return keys if isinstance(keys, list) else []

def save_ai_keys(keys, keys_path, write_json, harden=None):
    write_json(keys_path, keys)
    if harden is not None:
        harden(keys_path)

def _iso_in_past(iso_text):
    if not iso_text:
        return True
    try:
        return datetime.fromisoformat(iso_text) <= datetime.now()
    except ValueError:
        return True

def ai_available_key(keys):
    """أول مفتاح غير مستنفد؛ يعيد (الفهرس، المفتاح) أو (None, None)."""
    for i, entry in enumerate(keys):
        if entry.get("key") and _iso_in_past(entry.get("exhausted_until", "")):
            return i, entry["key"]
    return None, None

def ai_mark_exhausted(keys, index, reason, keys_path, write_json, harden=None):
    """يعلّم المفتاح كمستنفد حتى منتصف الليل القادم (تتجدد حصة Gemini اليومية)."""
    nxt = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    keys[index]["exhausted_until"] = nxt.isoformat(timespec="seconds")
    keys[index]["last_error"] = reason[:200]
    save_ai_keys(keys, keys_path, write_json, harden=harden)

def _gemini_call(prompt, api_key, model, timeout=45, max_tokens=700):
    """نداء REST مباشر لـ Gemini بدون مكتبات إضافية؛ المفتاح في ترويسة لا في الرابط."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens,
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "x-goog-api-key": api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()

def gemini_generate(prompt, model, keys_path, read_json, write_json,
                    log_cb=None, max_tokens=700, harden=None):
    """يولّد نصاً عبر Gemini مع تدوير المفاتيح تلقائياً عند نفاد الحصة.

    يعيد (النص، المفاتيح المحدّثة) أو (None، المفاتيح) إذا استُنفدت كلها.
    """
    keys = load_ai_keys(keys_path, read_json)
    if not keys:
        if log_cb:
            log_cb("لا توجد مفاتيح Gemini — أضفها من تبويب الذكاء الاصطناعي")
        return None, keys
    while True:
        idx, key = ai_available_key(keys)
        if key is None:
            if log_cb:
                log_cb("كل مفاتيح Gemini مستنفدة اليوم — سأستخدم التحسين العادي")
            return None, keys
        try:
            return _gemini_call(prompt, key, model, max_tokens=max_tokens), keys
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            invalid = e.code in (400, 401) or (e.code == 403 and "API_KEY_INVALID" in body)
            if invalid:
                ai_mark_exhausted(keys, idx, f"مفتاح غير صالح (HTTP {e.code})",
                                  keys_path, write_json, harden=harden)
                if log_cb:
                    log_cb(f"مفتاح Gemini #{idx+1} غير صالح — عطّلته. راجعه في aistudio.google.com")
                continue
            if e.code in (403, 429):
                ai_mark_exhausted(keys, idx, f"نفاد الحصة (HTTP {e.code})",
                                  keys_path, write_json, harden=harden)
                if log_cb:
                    log_cb(f"نفدت حصة مفتاح Gemini #{idx+1} — الانتقال للتالي…")
                continue
            keys[idx]["last_error"] = f"HTTP {e.code}: {body[:120]}"
            save_ai_keys(keys, keys_path, write_json, harden=harden)
            if log_cb:
                log_cb(f"خطأ Gemini HTTP {e.code} للمفتاح #{idx+1} — تجربة التالي…")
            ai_mark_exhausted(keys, idx, f"HTTP {e.code}",
                              keys_path, write_json, harden=harden)
        except Exception as e:
            logging.warning("خطأ شبكة مع مفتاح Gemini #%s: %s", idx + 1, e)
            keys[idx]["last_error"] = str(e)[:200]
            save_ai_keys(keys, keys_path, write_json, harden=harden)
            if log_cb:
                log_cb(f"تعذر الوصول لمفتاح #{idx+1} ({e}) — تجربة التالي…")
            # أخطاء الشبكة لا تستنفد المفتاح لكن ننتقل للتالي لهذه المحاولة
            keys[idx]["exhausted_until"] = (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds")
            save_ai_keys(keys, keys_path, write_json, harden=harden)

def _extract_json(text):
    """يستخرج أول كائن JSON من رد النموذج حتى لو التفّ بعلامات markdown."""
    if not text:
        return None
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

def ai_ping_key(key, model, timeout=30):
    """اختبار سريع لمفتاح واحد. يعيد (نجاح؟، رسالة)."""
    try:
        text = _gemini_call("أجب بكلمة واحدة: تم", key, model, timeout=timeout, max_tokens=16)
        return (True, "يعمل") if text else (False, "رد فارغ")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{e}"
