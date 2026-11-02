"""
╔══════════════════════════════════════════════════════════════╗
║   FB → YouTube Uploader  v4.7 PRO — AI Studio Edition        ║
║   عناوين ووصف بالذكاء الاصطناعي (Gemini) مع تدوير مفاتيح     ║
║   API تلقائياً عند نفاد الحصة • مراقبة بلا توقف • الاستوديو   ║
╚══════════════════════════════════════════════════════════════╝
"""

import time, os, re, json, logging, random, threading, queue, configparser, shutil
import subprocess, sys
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, timezone

import yt_dlp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

import tkinter as tk
from tkinter import messagebox, ttk, filedialog

# ════════════════════════════════════════════════════
# CONSTANTS & CONFIG
# ════════════════════════════════════════════════════
BG      = "#0f0f1a"
CARD    = "#161625"
PANEL   = "#1c1c2e"
ACCENT  = "#3d5afe"
BORDER  = "#2a2a40"
TXT     = "#e0e0e0"
TXT2    = "#9fa8da"
INP     = "#1e1e32"
ERR     = "#ff5252"
SUCCESS = "#00e676"
WARN    = "#ffab40"
INFO    = "#40c4ff"

FB  = ("Segoe UI", 10)
FS  = ("Segoe UI", 12, "bold")
FSM = ("Segoe UI", 9)
FM  = ("Consolas", 10)

config = configparser.ConfigParser()
config.read('config.ini')

SCOPES              = ["https://www.googleapis.com/auth/youtube.upload"]
VIDEO_DIRECTORY     = config.get('SETTINGS', 'VIDEO_DIRECTORY',     fallback='videos/')
CLIENT_SECRETS_FILE = config.get('SETTINGS', 'CLIENT_SECRETS_FILE', fallback='client_secrets.json')
UPLOADED_IDS_FILE   = "uploaded_ids.txt"
SEEN_IDS_FILE       = "seen_ids.txt"
PAGES_FILE          = "pages.json"
ACCOUNTS_FILE       = "accounts.json"
STATS_FILE          = "stats.json"
STATE_FILE          = "state.json"
FAILED_QUEUE_FILE   = "failed_queue.json"
HISTORY_FILE        = "history.jsonl"
MAX_RETRIES         = 3
RETRY_DELAY         = 8

# الصفحات المحفوظة في المشروع — تُكتب تلقائياً في pages.json عند أول تشغيل
DEFAULT_PAGES = [
    {"url": "https://www.facebook.com/elwataniatvweb/reels/", "name": "الوطنية TV",
     "scroll": 5, "limit": 30, "enabled": True},
    {"url": "https://www.facebook.com/ElwataniaSport/reels/", "name": "الوطنية سبورت",
     "scroll": 5, "limit": 30, "enabled": True},
]

os.makedirs(VIDEO_DIRECTORY, exist_ok=True)

from logging.handlers import RotatingFileHandler
if "pytest" not in sys.modules:
    _log_handler = RotatingFileHandler("errors.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    _log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(_log_handler)
logging.getLogger().setLevel(logging.INFO)

# توقيت المحيط الهادئ — حصة يوتيوب تُصفَّر منتصف الليل بهذا التوقيت
try:
    from zoneinfo import ZoneInfo
    PT_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # Windows بدون tzdata
    PT_TZ = None

def pt_now():
    """الوقت الحالي بتوقيت المحيط الهادئ (مع احتياطي إذا غابت قاعدة المناطق)."""
    if PT_TZ is not None:
        return datetime.now(PT_TZ)
    now = datetime.now(timezone.utc)
    offset = -7 if 3 <= now.month <= 11 else -8  # تقريب التوقيت الصيفي الأمريكي
    return now.astimezone(timezone(timedelta(hours=offset)))

# ════════════════════════════════════════════════════
# HELPERS — DATA & UTILS
# ════════════════════════════════════════════════════
def _read_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default

def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_accounts():
    return _read_json(ACCOUNTS_FILE, ["account1"])

def save_accounts(accounts):
    _write_json(ACCOUNTS_FILE, accounts)

def _load_id_set(path):
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def _append_id(path, vid):
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{vid}\n")

def load_uploaded_ids():
    return _load_id_set(UPLOADED_IDS_FILE)

def save_uploaded_id(video_id):
    _append_id(UPLOADED_IDS_FILE, video_id)

def load_seen_ids():
    """معرّفات استُنفدت محاولاتها — لا تُعاد معالجتها أبداً."""
    return _load_id_set(SEEN_IDS_FILE)

def save_seen_id(video_id):
    _append_id(SEEN_IDS_FILE, video_id)

def load_stats():
    stats = _read_json(STATS_FILE, {})
    stats.setdefault("total_uploaded", 0)
    stats.setdefault("total_failed", 0)
    stats.setdefault("total_downloaded", 0)
    stats.setdefault("cycles", 0)
    stats.setdefault("daily", {"date": "", "uploaded": 0})
    return stats

def save_stats(stats):
    _write_json(STATS_FILE, stats)

def bump_daily_uploads(stats):
    today = pt_now().date().isoformat()
    daily = stats.setdefault("daily", {"date": today, "uploaded": 0})
    if daily.get("date") != today:
        daily["date"], daily["uploaded"] = today, 0
    daily["uploaded"] += 1

def load_state():
    return _read_json(STATE_FILE, {})

def save_state(state):
    _write_json(STATE_FILE, state)

def load_failed_queue():
    """عناصر فشلت وستُعاد محاولتها في الدورات القادمة."""
    q = _read_json(FAILED_QUEUE_FILE, [])
    return q if isinstance(q, list) else []

def save_failed_queue(items):
    _write_json(FAILED_QUEUE_FILE, items)

def append_history(record):
    """أرشيف دائم لكل عملية (سطر JSON لكل حدث)."""
    record = dict(record)
    record.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logging.exception("تعذرت الكتابة في ملف الأرشيف")

def load_pages():
    """الصفحات المخزنة؛ عند أول تشغيل تُحفظ الصفحات الافتراضية للمشروع."""
    pages = _read_json(PAGES_FILE, None)
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
    m = re.search(r"/(reel|videos)/([^/?&]+)", url)
    return m.group(2) if m else url

def filter_new_reels(links, uploaded_ids, seen_ids, queued_ids):
    """يُبقي فقط الريلز الجديدة فعلاً — لم تُرفع ولم تُستنفد ولم تكن قيد الانتظار."""
    fresh, seen_here = [], set()
    for link in links:
        rid = reel_id_from_url(link)
        if rid in seen_here:
            continue
        seen_here.add(rid)
        if rid in uploaded_ids or rid in seen_ids or rid in queued_ids:
            continue
        fresh.append({"url": link, "id": rid})
    return fresh

# ════════════════════════════════════════════════════
# FACEBOOK COOKIES — رفع موثوقية السحب
# ════════════════════════════════════════════════════
def parse_netscape_cookies(path):
    """يقرأ ملف cookies.txt بصيغة Netscape ويعيد قائمة قواميس كوكيز."""
    cookies = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or (line.startswith("#") and not line.startswith("#HttpOnly")):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _flag, cpath, secure, expiry, name, value = parts[:7]
                try:
                    expiry_i = int(expiry)
                except ValueError:
                    expiry_i = 0
                cookies.append({
                    "domain": domain, "path": cpath or "/",
                    "secure": secure.upper() == "TRUE",
                    "expiry": expiry_i, "name": name, "value": value,
                })
    except OSError:
        return []
    return cookies

def load_cookies_to_driver(driver, cookies_path, log_cb=None):
    """يحقن كوكيز Netscape في متصفح Selenium (يجب زيارة النطاق أولاً)."""
    cookies = parse_netscape_cookies(cookies_path)
    if not cookies:
        if log_cb:
            log_cb("⚠️ ملف الكوكيز فارغ أو غير مقروء")
        return 0
    driver.get("https://www.facebook.com/")
    time.sleep(2)
    loaded = 0
    for c in cookies:
        try:
            cookie = {"name": c["name"], "value": c["value"],
                      "domain": c["domain"], "path": c["path"], "secure": c["secure"]}
            if c["expiry"]:
                cookie["expiry"] = c["expiry"]
            driver.add_cookie(cookie)
            loaded += 1
        except Exception:
            continue
    if log_cb:
        log_cb(f"🍪 حُقن {loaded} كوكيز فيسبوك في المتصفح")
    return loaded

def cookies_arg(settings):
    """يعيد مسار ملف الكوكيز إن كانت الميزة مفعّلة والملف موجوداً، وإلا None."""
    if not settings.get("cookies_enabled"):
        return None
    path = (settings.get("cookies_file") or "").strip()
    return path if path and os.path.isfile(path) else None

# ════════════════════════════════════════════════════
# CONTENT DEDUP — منع رفع نفس المقطع من صفحتين
# ════════════════════════════════════════════════════
def content_fingerprint(title):
    """بصمة نصية: حروف وأرقام فقط، بلا فراغات ولا تشكيل."""
    t = re.sub(r'[^\w\u0600-\u06FF]+', '', (title or "").lower())
    return t

def is_duplicate_content(title, threshold=0.88, lookback=600):
    """يقارن بصمة العنوان مع آخر العناوين المرفوعة (تشابه تقريبي)."""
    from difflib import SequenceMatcher
    fp = content_fingerprint(title)
    if len(fp) < 10:
        return False
    state = load_state()
    for old in state.get("title_fingerprints", [])[-lookback:]:
        if fp == old or SequenceMatcher(None, fp, old).ratio() >= threshold:
            return True
    return False

def remember_content(title):
    """يخزن بصمة العنوان بعد الرفع الناجح (بحد أقصى 2000 بصمة)."""
    fp = content_fingerprint(title)
    if len(fp) < 10:
        return
    state = load_state()
    fps = state.get("title_fingerprints", [])
    fps.append(fp)
    state["title_fingerprints"] = fps[-2000:]
    save_state(state)

# ════════════════════════════════════════════════════
# QUOTA MANAGER — إدارة حصة يوتيوب اليومية
# ════════════════════════════════════════════════════
class QuotaManager:
    """يتتبع وحدات حصة YouTube API ويصفّر العداد تلقائياً منتصف الليل بتوقيت المحيط الهادئ.

    مع account="" (الافتراضي) يبقى السلوك القديم؛ وعند تمرير اسم حساب يكون
    العداد مستقلاً لكل حساب — أساس ميزة تدوير الحسابات عند نفاد الحصة.
    """

    COST_UPLOAD = 1600  # وحدة لكل videos.insert

    def __init__(self, daily_limit=10000, log_cb=None, account=""):
        self.daily_limit = max(self.COST_UPLOAD, int(daily_limit))
        self.log_cb = log_cb
        self.account = account
        self._k_used = f"quota_used_{account}" if account else "quota_used"
        self._k_date = f"quota_date_{account}" if account else "quota_date"
        state = load_state()
        today = pt_now().date().isoformat()
        self.date = state.get(self._k_date, "")
        self.used = int(state.get(self._k_used, 0)) if self.date == today else 0
        self.date = today
        self._persist()

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
                self.log_cb("🌅 بدأ يوم جديد (توقيت المحيط الهادئ) — تم تصفير عداد الحصة")

    def can_afford(self, units=None):
        self._refresh()
        return self.used + (units or self.COST_UPLOAD) <= self.daily_limit

    def register(self, units=None):
        self._refresh()
        self.used += (units or self.COST_UPLOAD)
        self._persist()

    def mark_exhausted(self):
        """يُستدعى عندما يرفض YouTube الرفع رغم أن العدّاد المحلي يسمح."""
        self.used = self.daily_limit
        self._persist()

    def seconds_until_reset(self):
        now = pt_now()
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        return max(60, int((nxt - now).total_seconds()))

# ════════════════════════════════════════════════════
# GEMINI AI — تحسين المحتوى بالذكاء الاصطناعي مع تدوير المفاتيح
# ════════════════════════════════════════════════════
AI_KEYS_FILE = "ai_keys.json"
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
AI_STYLES = {
    "احترافي متوازن": "اكتب بأسلوب احترافي متوازن: واضح، جذاب، وصادق بدون مبالغة.",
    "جذاب (Clickbait معتدل)": "اكتب بأسلوب جذاب يثير الفضول لكن بدون كذب أو وعود كاذبة — العنوان يجب أن يطابق المحتوى.",
    "إخباري رسمي": "اكتب بأسلوب إخباري رسمي موجز كما في النشرات، مع أهم المعلومات أولاً.",
    "رياضي حماسي": "اكتب بأسلوب رياضي حماسي مشوّق يليق بعشاق الرياضة.",
}

def load_ai_keys():
    """كل عنصر: {key, exhausted_until (ISO أو ''), last_error}."""
    keys = _read_json(AI_KEYS_FILE, [])
    return keys if isinstance(keys, list) else []

def save_ai_keys(keys):
    _write_json(AI_KEYS_FILE, keys)

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

def ai_mark_exhausted(keys, index, reason=""):
    """يعلّم المفتاح كمستنفد حتى منتصف الليل القادم (تتجدد حصة Gemini اليومية)."""
    nxt = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    keys[index]["exhausted_until"] = nxt.isoformat(timespec="seconds")
    keys[index]["last_error"] = reason[:200]
    save_ai_keys(keys)

def _gemini_call(prompt, api_key, model, timeout=45, max_tokens=700):
    """نداء REST مباشر لـ Gemini بدون مكتبات إضافية."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens,
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()

def gemini_generate(prompt, model, log_cb=None, max_tokens=700):
    """يولّد نصاً عبر Gemini مع تدوير المفاتيح تلقائياً عند نفاد الحصة.

    يعيد (النص، المفاتيح المحدّثة) أو (None، المفاتيح) إذا استُنفدت كلها.
    """
    keys = load_ai_keys()
    if not keys:
        if log_cb:
            log_cb("⚠️ لا توجد مفاتيح Gemini — أضفها من تبويب 🤖 الذكاء الاصطناعي")
        return None, keys
    while True:
        idx, key = ai_available_key(keys)
        if key is None:
            if log_cb:
                log_cb("⛔ كل مفاتيح Gemini مستنفدة اليوم — سأستخدم التحسين العادي")
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
                ai_mark_exhausted(keys, idx, f"مفتاح غير صالح (HTTP {e.code})")
                if log_cb:
                    log_cb(f"❌ مفتاح Gemini #{idx+1} غير صالح — عطّلته. راجعه في aistudio.google.com")
                continue
            if e.code in (403, 429):
                ai_mark_exhausted(keys, idx, f"نفاد الحصة (HTTP {e.code})")
                if log_cb:
                    log_cb(f"🔄 نفدت حصة مفتاح Gemini #{idx+1} — الانتقال للتالي…")
                continue
            keys[idx]["last_error"] = f"HTTP {e.code}: {body[:120]}"
            save_ai_keys(keys)
            if log_cb:
                log_cb(f"⚠️ خطأ Gemini HTTP {e.code} للمفتاح #{idx+1} — تجربة التالي…")
            ai_mark_exhausted(keys, idx, f"HTTP {e.code}")
        except Exception as e:
            logging.warning("خطأ شبكة مع مفتاح Gemini #%s: %s", idx + 1, e)
            keys[idx]["last_error"] = str(e)[:200]
            save_ai_keys(keys)
            if log_cb:
                log_cb(f"⚠️ تعذر الوصول لمفتاح #{idx+1} ({e}) — تجربة التالي…")
            # أخطاء الشبكة لا تستنفد المفتاح لكن ننتقل للتالي لهذه المحاولة
            keys[idx]["exhausted_until"] = (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds")
            save_ai_keys(keys)

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

def ai_generate_metadata(raw_title, raw_desc, page_name, duration, model,
                         style_name, log_cb=None):
    """يسأل Gemini عن {title, description, tags} احترافية للفيديو. يعيد dict أو None."""
    style = AI_STYLES.get(style_name, AI_STYLES["احترافي متوازن"])
    dur_txt = f"{int(duration)} ثانية" if duration else "غير معروفة"
    prompt = f"""أنت خبير تسويق محتوى يوتيوب عربي. لدي فيديو قصير (Reel) من صفحة فيسبوك "{page_name or 'عامة'}".

العنوان الأصلي: {raw_title[:200]}
الوصف الأصلي: {(raw_desc or '')[:400]}
مدة الفيديو: {dur_txt}

{style}

أعد كتابة بيانات الفيديو ليوتيوب بصيغة JSON فقط بهذا الشكل:
{{"title": "...", "description": "...", "tags": ["...", "..."]}}

القواعد:
- title: عربي فصيح مبسط، 40-80 حرفاً، بلا إيموجي زائد (واحد كحد أقصى)، بلا هاشتاجات.
- description: 2-4 جمل تشوّق للمشاهدة وتلخص المحتوى، بلا روابط.
- tags: من 8 إلى 12 وسمًا (عربي وإنجليزي) مرتبطة فعلاً بموضوع الفيديو.
- لا تخترع أسماء أو أرقاماً غير موجودة في المدخلات."""
    text, _keys = gemini_generate(prompt, model, log_cb, max_tokens=700)
    data = _extract_json(text or "")
    if not data or not isinstance(data.get("title"), str):
        return None
    tags = data.get("tags")
    return {
        "title": data["title"].strip(),
        "description": str(data.get("description") or "").strip(),
        "tags": [str(t).strip() for t in tags if str(t).strip()] if isinstance(tags, list) else [],
    }

def ai_ping_key(key, model, timeout=30):
    """اختبار سريع لمفتاح واحد. يعيد (نجاح؟، رسالة)."""
    try:
        text = _gemini_call("أجب بكلمة واحدة: تم", key, model, timeout=timeout, max_tokens=16)
        return (True, "✅ يعمل") if text else (False, "⚠️ رد فارغ")
    except urllib.error.HTTPError as e:
        return False, f"❌ HTTP {e.code}"
    except Exception as e:
        return False, f"❌ {e}"

# ════════════════════════════════════════════════════
# TELEGRAM — إشعارات اختيارية
# ════════════════════════════════════════════════════
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
            log_cb(f"⚠️ فشل إرسال إشعار Telegram: {e}")

# ════════════════════════════════════════════════════
# HELPERS — CORE LOGIC
# ════════════════════════════════════════════════════
def clean_title(title, page_name="", remove_hashtags=False):
    if not title:
        return "Facebook Reel"
    title = re.sub(r'http\S+', '', title)
    title = re.sub(r'\b\d+\.?\d*[KM]?\s*(views|مشاهدة|مشاهدات|reactions|تفاعل|تفاعلات)\b', '', title, flags=re.IGNORECASE)
    if remove_hashtags:
        title = re.sub(r'#\w+', '', title)
    for c in ['<', '>', ':', '"', '/', '\\', '|', '?', '*', '#', '&']:
        title = title.replace(c, '')
    if page_name:
        title = re.sub(re.escape(page_name), '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'\s+', ' ', title).strip()
    return title[:90] or "Facebook Reel"

def init_driver(headless=True, log_cb=None):
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=ar")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    options.add_argument(f"user-agent={ua}")
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
    try:
        driver = webdriver.Chrome(options=options)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        })
        return driver
    except Exception as e:
        logging.error("Driver init error: %s", e)
        if log_cb:
            log_cb(f"❌ فشل تشغيل المتصفح: {e}")
        return None

def get_reels(driver, fb_page, scroll_times, video_limit, log_cb=None):
    """السحب عبر المتصفح (الطريقة المجرّبة) — يُستخدم كاحتياطي أو وضع أساسي."""
    driver.get(fb_page)
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "a")))
    except Exception:
        pass
    time.sleep(4)

    links, last_count, stall_count = set(), 0, 0
    for i in range(scroll_times):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        for el in driver.find_elements(By.TAG_NAME, "a"):
            try:
                href = el.get_attribute("href")
                if href and ("/reel/" in href or "/videos/" in href):
                    links.add(href.split("?")[0].split("&")[0])
            except Exception:
                continue
        if log_cb:
            log_cb(f"📜 تمرير {i+1}/{scroll_times} — {len(links)} رابط")
        if len(links) == last_count:
            stall_count += 1
            if stall_count >= 3:
                break
        else:
            stall_count = 0
        last_count = len(links)
        if len(links) >= video_limit:
            break
    return list(links)[:video_limit]

def scrape_links_fast(page_url, limit, log_cb=None, cookies_file=None):
    """سحب سريع عبر yt-dlp من دون متصفح. يعيد None عند الفشل ليُستخدم المتصفح."""
    opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": 45,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=False)
    links = []
    entries = info.get("entries") if isinstance(info, dict) else None
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        if url and "facebook.com" in url and ("/reel/" in url or "/videos/" in url):
            links.append(url.split("?")[0])
        elif str(e.get("id") or "").isdigit():
            links.append(f"https://www.facebook.com/reel/{e['id']}")
    # إزالة التكرار مع حفظ الترتيب
    seen, uniq = set(), []
    for link in links:
        if link not in seen:
            seen.add(link)
            uniq.append(link)
    return uniq[:limit]

def get_youtube(account_name, secrets_file=None):
    sf = secrets_file or CLIENT_SECRETS_FILE
    token_file = f"{account_name}_token.json"
    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds:
            if not os.path.exists(sf):
                return None, f"ملف {os.path.basename(sf)} مفقود"
            flow = InstalledAppFlow.from_client_secrets_file(sf, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds), None

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

def download_video(url, page_name="", log_cb=None, retries=MAX_RETRIES, remove_tags=False,
                   cookies_file=None):
    ydl_opts = {
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(VIDEO_DIRECTORY, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "merge_output_format": "mp4",
        "retries": 10,
        "socket_timeout": 60,
    }
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    for attempt in range(1, retries + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = _resolve_downloaded_file(info, ydl)
                if not file_path:
                    raise FileNotFoundError("اكتمل الاستخراج لكن لم يُعثر على ملف MP4 الناتج.")
                title = clean_title(info.get("title", ""), page_name, remove_tags)
                size_mb = round(os.path.getsize(file_path) / (1024**2), 1)
                if log_cb:
                    log_cb(f"✅ تحميل ناجح: {title[:40]}... ({size_mb} MB)")
                logging.info("اكتمل تنزيل الفيديو: %s", file_path)
                return {
                    "file": file_path,
                    "title": title,
                    "desc": info.get("description") or "",
                    "size_mb": size_mb,
                    "duration": info.get("duration", 0),
                }
        except Exception as error:
            logging.exception("فشل تنزيل الفيديو %s في المحاولة %s", url, attempt)
            if attempt < retries:
                if log_cb:
                    log_cb(f"⚠️ فشل التحميل: {error}. إعادة المحاولة ({attempt}/{retries})…")
                time.sleep(RETRY_DELAY)
            elif log_cb:
                log_cb(f"❌ فشل التحميل نهائياً: {error}")
    return None

# ════════════════════════════════════════════════════
# TITLE OPTIMIZER — تحسين العنوان قبل الرفع
# ════════════════════════════════════════════════════
YOUTUBE_TITLE_MAX = 100
# كلمات حشو شائعة في عناوين فيسبوك لا فائدة منها في يوتيوب
FILLER_PATTERNS = [
    r'\bريلز\b', r'\breels?\b', r'\bفيديو\s*جديد\b', r'\bشاهد\s*(الآن|الفيديو)?\b',
    r'\bلا\s*تفوت\w*\b', r'\bحصريا\b', r'\bعاجل\s*:\s*', r'📹|🎥|🎬|▶️|🔴',
]

def optimize_title(raw_title, page_name="", duration=0, prefix="", suffix="",
                   auto_shorts=True, remove_hashtags=True):
    """يحوّل عنوان فيسبوك الخام إلى عنوان يوتيوب نظيف وجذاب.

    الخطوات: تنظيف → إزالة الحشو → إزالة الكلمات المكررة → قالب بادئة/لاحقة
    → وسم #Shorts تلقائياً للمقاطع القصيرة → قصّ عند حد يوتيوب.
    """
    title = clean_title(raw_title, page_name, remove_hashtags)
    if title != "Facebook Reel":  # البديل الافتراضي لا تُزال منه كلمات الحشو
        for pat in FILLER_PATTERNS:
            title = re.sub(pat, ' ', title, flags=re.IGNORECASE)
        # إزالة الكلمات المتكررة المتتالية (شائع في عناوين الصفحات)
        words = title.split()
        deduped = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
        title = ' '.join(deduped)
        title = re.sub(r'\s+', ' ', title).strip(' -|،,.')
        if not title:
            title = "Facebook Reel"

    if auto_shorts and duration and 0 < duration <= 60 and '#Shorts' not in title:
        suffix = (suffix + ' #Shorts').strip()

    parts = []
    if prefix.strip():
        parts.append(prefix.strip())
    if title:
        parts.append(title)
    if suffix.strip():
        parts.append(suffix.strip())
    final = ' '.join(parts) if parts else "Facebook Reel"
    final = re.sub(r'\s+', ' ', final).strip()

    if len(final) > YOUTUBE_TITLE_MAX:
        # قصّ ذكي: نحافظ على البادئة واللاحقة ونقص من المتن
        fixed = len(prefix.strip()) + len(suffix.strip()) + 2
        room = max(20, YOUTUBE_TITLE_MAX - fixed)
        title = title[:room].rsplit(' ', 1)[0]
        parts = [p for p in [prefix.strip(), title, suffix.strip()] if p]
        final = ' '.join(parts)
    return final[:YOUTUBE_TITLE_MAX] or "Facebook Reel"

# ════════════════════════════════════════════════════
# DESCRIPTION BUILDER — وصف SEO مع هاشتاجات
# ════════════════════════════════════════════════════
AR_STOPWORDS = {'في', 'من', 'على', 'إلى', 'عن', 'أن', 'إن', 'ذا', 'ذلك', 'التي',
                'الذي', 'هذا', 'هذه', 'ما', 'لا', 'لم', 'لن', 'قد', 'و', 'أو',
                'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'on', 'for', 'is'}

def extract_hashtags(title, tags, max_tags=8):
    """يستخرج هاشتاجات من كلمات العنوان المميزة + الوسوم المضبوطة."""
    tags = [t.strip().lstrip('#') for t in (tags or []) if t.strip()]
    found, seen = [], set()
    for w in re.findall(r'[\w\u0600-\u06FF]{3,}', title):
        wl = w.lower()
        if wl in AR_STOPWORDS or wl in seen:
            continue
        seen.add(wl)
        found.append(w)
        if len(found) >= max_tags:
            break
    merged = []
    for t in tags + found:
        if t not in merged:
            merged.append(t)
    return ' '.join(f'#{t.replace(" ", "_")}' for t in merged[:12])

def build_description(original_desc, extra_desc, title, tags,
                      auto_hashtags=True, credit_line=""):
    """يبني وصفاً نهائياً: الوصف الأصلي + الإضافي + خط الهاشتاجات + حقوق اختيارية."""
    parts = []
    if original_desc and original_desc.strip():
        parts.append(original_desc.strip()[:2000])
    if extra_desc and extra_desc.strip():
        parts.append(extra_desc.strip())
    if auto_hashtags:
        tags_line = extract_hashtags(title, tags)
        if tags_line:
            parts.append(tags_line)
    if credit_line.strip():
        parts.append(credit_line.strip())
    return '\n\n'.join(parts)[:4500]

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
    # إحداثيات overlay (main_w/overlay_w متاحة في مرشح overlay فقط)
    "أعلى اليمين":  ("main_w-overlay_w-{m}-{p}", "{m}+{p}"),
    "أعلى اليسار":  ("{m}+{p}", "{m}+{p}"),
    "أسفل اليمين":  ("main_w-overlay_w-{m}-{p}", "main_h-overlay_h-{m}-{p}"),
    "أسفل اليسار":  ("{m}+{p}", "main_h-overlay_h-{m}-{p}"),
}

def _box_coords(position_name, w_ratio, h_ratio, margin, pad_px):
    """إحداثيات صندوق التغطية بمرشح drawbox (iw/ih = أبعاد الفيديو الأصلي)."""
    bw = f"iw*{w_ratio:.4f}+{2 * pad_px}"
    bh = f"ih*{h_ratio:.4f}+{2 * pad_px}"
    x_right = f"iw*(1-{w_ratio:.4f})-{margin + 2 * pad_px}"
    y_bottom = f"ih*(1-{h_ratio:.4f})-{margin + 2 * pad_px}"
    coords = {
        "أعلى اليمين":  (x_right, str(margin)),
        "أعلى اليسار":  (str(margin), str(margin)),
        "أسفل اليمين":  (x_right, y_bottom),
        "أسفل اليسار":  (str(margin), y_bottom),
    }
    x, y = coords[position_name]
    return x, y, bw, bh

def build_overlay_filter(position_name, margin, opacity, cover_old, pad_px,
                         w_ratio, h_ratio):
    """يبني سلسلة فلاتر ffmpeg لتركيب الشعار.

    المدخل [ls] هو الشعار بعد القياس. عند تفعيل "تغطية الشعار القديم" يُرسم
    صندوق أسود معتم خلف الشعار يخفي علامة الصفحة الأصلية ثم يُركّب الشعار فوقه.
    """
    x_tmpl, y_tmpl = LOGO_POSITIONS[position_name]
    ox = x_tmpl.format(m=margin, p=pad_px)
    oy = y_tmpl.format(m=margin, p=pad_px)
    alpha = max(0.05, min(1.0, opacity / 100.0))
    filters = [f"[ls]format=rgba,colorchannelmixer=aa={alpha:.2f}[logo]"]
    if cover_old:
        bx, by, bw, bh = _box_coords(position_name, w_ratio, h_ratio, margin, pad_px)
        filters.append(
            f"[0:v]drawbox=x='{bx}':y='{by}':w='{bw}':h='{bh}':color=black@0.85:t=fill[bg]"
        )
        base = "[bg]"
    else:
        base = "[0:v]"
    filters.append(f"{base}[logo]overlay=x='{ox}':y='{oy}'[vout]")
    return ';'.join(filters)

def apply_logo_overlay(input_path, logo_path, position_name="أعلى اليمين",
                       scale_pct=18, opacity=100, margin=20, cover_old=True,
                       log_cb=None):
    """يركّب الشعار فوق الفيديو. يعيد مسار الفيديو الجديد أو None عند الفشل."""
    if not os.path.isfile(logo_path):
        if log_cb:
            log_cb("⚠️ ملف الشعار غير موجود — تخطي التركيب")
        return None
    w, h, _ = probe_video(input_path)
    if w <= 0:
        if log_cb:
            log_cb("⚠️ تعذرت قراءة أبعاد الفيديو — تخطي التركيب")
        return None
    lw, lh, _ = probe_video(logo_path)
    logo_w = max(20, int(w * scale_pct / 100))
    w_ratio = scale_pct / 100.0
    # نسبة ارتفاع الشعار من ارتفاع الفيديو (لحساب صندوق التغطية بدقة)
    h_ratio = (logo_w * (lh / lw) / h) if (lw and lh and h) else 0.12
    fc = f"[1:v]scale={logo_w}:-1[ls];" + build_overlay_filter(
        position_name, margin, opacity, cover_old, pad_px=8,
        w_ratio=w_ratio, h_ratio=h_ratio)

    out_path = os.path.splitext(input_path)[0] + "_branded.mp4"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-i", logo_path,
           "-filter_complex", fc, "-map", "[vout]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "copy", "-movflags", "+faststart", out_path]
    try:
        if log_cb:
            log_cb("🖼 تركيب الشعار على الفيديو…")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not os.path.isfile(out_path):
            logging.error("ffmpeg overlay failed: %s", proc.stderr[-2000:])
            if log_cb:
                log_cb("⚠️ فشل تركيب الشعار — سيُرفع الفيديو الأصلي")
            return None
        return out_path
    except Exception as e:
        logging.exception("خطأ في تركيب الشعار")
        if log_cb:
            log_cb(f"⚠️ خطأ في تركيب الشعار: {e}")
        return None

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

def upload_video(youtube, video_data, privacy="public", playlist_id=None, tags=None,
                 category="22", sched_time=None, log_cb=None, retries=MAX_RETRIES):
    body = {
        "snippet": {
            "title": video_data["title"],
            "description": video_data["desc"],
            "tags": [t.strip() for t in (tags or ["Facebook", "Reel"]) if t.strip()],
            "categoryId": category,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    if sched_time:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = sched_time.astimezone(timezone.utc).isoformat()

    if not os.path.isfile(video_data["file"]):
        message = f"ملف الفيديو غير موجود بعد التنزيل: {video_data['file']}"
        logging.error(message)
        if log_cb:
            log_cb(f"❌ {message}")
        return None

    for attempt in range(1, retries + 1):
        try:
            media = MediaFileUpload(video_data["file"], chunksize=1024 * 1024, resumable=True)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status and log_cb:
                    log_cb(f"⬆️ جاري الرفع: {int(status.progress() * 100)}%", replace_last=True)
            vid_id = response.get("id")
            if not vid_id:
                raise RuntimeError("لم يرجع YouTube معرّف الفيديو بعد الرفع.")
            if log_cb:
                log_cb(f"🚀 تم الرفع بنجاح! ID: {vid_id}")
            if playlist_id:
                youtube.playlistItems().insert(
                    part="snippet",
                    body={"snippet": {"playlistId": playlist_id,
                                      "resourceId": {"kind": "youtube#video", "videoId": vid_id}}},
                ).execute()
            return vid_id
        except HttpError as error:
            logging.exception("خطأ YouTube API في المحاولة %s", attempt)
            if error.resp.status in [403, 429]:
                if log_cb:
                    log_cb(f"❌ رفض YouTube الرفع (HTTP {error.resp.status}). تحقق من الحصة والصلاحيات.")
                return "QUOTA_ERROR"
            message = f"خطأ YouTube HTTP {error.resp.status}: {error}"
        except Exception as error:
            logging.exception("خطأ غير متوقع أثناء رفع الفيديو في المحاولة %s", attempt)
            message = f"خطأ أثناء الرفع: {error}"
        if attempt < retries:
            if log_cb:
                log_cb(f"⚠️ {message}. إعادة المحاولة ({attempt}/{retries})…")
            time.sleep(RETRY_DELAY)
        elif log_cb:
            log_cb(f"❌ فشل الرفع نهائياً: {message}")
    return None

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
    tk.Label(f, text=f"{icon}  {text}", bg=master["bg"], fg=ACCENT, font=FS).pack(side="left")
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

# ════════════════════════════════════════════════════
# MAIN APP (V4.0 PRO — WATCH MODE)
# ════════════════════════════════════════════════════
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("🎬 FB → YouTube Uploader v4.7 PRO — AI Studio Edition")
        self.root.configure(bg=BG)
        self.root.geometry("1100x880")

        self._stop   = threading.Event()
        self._pause  = threading.Event(); self._pause.set()
        self._thread = None
        self._logq   = queue.Queue()
        self._uiq    = queue.Queue()
        self._last_log_replace = False
        self._watching = False

        self.accounts = load_accounts()
        self.stats    = load_stats()
        self.pages    = load_pages()
        self.page_status = {}  # url -> dict

        self._apply_styles()
        self._build_header()
        self._build_nb()
        self._build_footer()
        self._poll()
        if self.autostart_var.get():
            self.root.after(1500, self._autostart_watch)

    def _autostart_watch(self):
        if self._watching:
            return
        self._log("🚀 بدء تلقائي للمراقبة (مفعّل في الإعدادات)…")
        self._do_start(True)

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
        h.pack(fill="x")
        inner = tk.Frame(h, bg=PANEL); inner.pack(padx=24)
        tk.Label(inner, text="🚀", bg=PANEL, fg=ACCENT, font=("Segoe UI", 26)).pack(side="left", padx=(0, 15))
        tl = tk.Frame(inner, bg=PANEL); tl.pack(side="left")
        tk.Label(tl, text="FB → YouTube PRO", bg=PANEL, fg=TXT,
                 font=("Segoe UI Semibold", 18, "bold")).pack(anchor="w")
        tk.Label(tl, text="مراقبة + استوديو + ذكاء اصطناعي بتدوير المفاتيح  •  v4.7",
                 bg=PANEL, fg=TXT2, font=FSM).pack(anchor="w")
        Btn(inner, "📊 الإحصائيات", lambda: self.nb.select(self.t_dash), color="#303050").pack(side="right")
        tk.Frame(self.root, bg=ACCENT, height=2).pack(fill="x")

    def _build_nb(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(12, 0))

        self.t_watch = tk.Frame(self.nb, bg=BG)
        self.t_pages = tk.Frame(self.nb, bg=BG)
        self.t_yt    = tk.Frame(self.nb, bg=BG)
        self.t_opts  = tk.Frame(self.nb, bg=BG)
        self.t_studio = tk.Frame(self.nb, bg=BG)
        self.t_ai    = tk.Frame(self.nb, bg=BG)
        self.t_log   = tk.Frame(self.nb, bg=BG)
        self.t_accs  = tk.Frame(self.nb, bg=BG)
        self.t_dash  = tk.Frame(self.nb, bg=BG)

        self.nb.add(self.t_watch, text="  🔁 المراقبة المستمرة  ")
        self.nb.add(self.t_pages, text="  📄 الصفحات  ")
        self.nb.add(self.t_yt,    text="  📺 يوتيوب  ")
        self.nb.add(self.t_studio, text="  🎬 الاستوديو  ")
        self.nb.add(self.t_ai,    text="  🤖 الذكاء الاصطناعي  ")
        self.nb.add(self.t_opts,  text="  ⚙️ خيارات متقدمة  ")
        self.nb.add(self.t_log,   text="  📋 السجل  ")
        self.nb.add(self.t_accs,  text="  👤 الحسابات  ")
        self.nb.add(self.t_dash,  text="  📊 لوحة التحكم  ")

        self._tab_watch()
        self._tab_pages()
        self._tab_youtube()
        self._tab_options()
        self._tab_studio()
        self._tab_ai()
        self._tab_log()
        self._tab_accounts()
        self._tab_dashboard()

    # ─────────── تبويب المراقبة ───────────
    def _read_autostart(self):
        try:
            cfg = configparser.ConfigParser()
            cfg.read("config.ini")
            return cfg.get("WATCH", "AUTO_START", fallback="false").strip().lower() in ("1", "true", "yes")
        except Exception:
            return False

    def _save_autostart(self):
        try:
            cfg = configparser.ConfigParser()
            cfg.read("config.ini")
            if not cfg.has_section("WATCH"):
                cfg.add_section("WATCH")
            cfg.set("WATCH", "AUTO_START", "true" if self.autostart_var.get() else "false")
            with open("config.ini", "w", encoding="utf-8") as f:
                cfg.write(f)
            self._log("💾 حُفظ إعداد البدء التلقائي في config.ini")
        except OSError as error:
            self._log(f"⚠️ تعذر حفظ config.ini: {error}")

    def _tab_watch(self):
        out = tk.Frame(self.t_watch, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)

        sec(out, "وضع المراقبة المستمرة — لا يتوقف أبداً", "🔁")
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
        ttk.Checkbutton(r2, text="⚡ السحب السريع بدون متصفح (yt-dlp أولاً، والمتصفح احتياطي تلقائي)",
                        variable=self.fast_scrape_var).pack(anchor="w")
        self.autostart_var = tk.BooleanVar(value=self._read_autostart())
        ttk.Checkbutton(inn, text="🚀 بدء المراقبة تلقائياً عند فتح البرنامج (للتشغيل الدائم مع بدء Windows)",
                        variable=self.autostart_var,
                        command=self._save_autostart).pack(anchor="w", pady=4)

        self.countdown_var = tk.StringVar(value="⏸ المراقبة متوقفة")
        tk.Label(inn, textvariable=self.countdown_var, bg=CARD, fg=INFO,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w", pady=(8, 0))

        sec(out, "حالة الصفحات المراقبة", "📡")
        tf = mk_card(out); tf.pack(fill="both", expand=True)
        cols = ("الصفحة", "آخر فحص", "روابط", "جديدة", "الحالة")
        self.wtree = ttk.Treeview(tf, columns=cols, show="headings", height=7, selectmode="browse")
        for cl, w in zip(cols, [330, 110, 70, 70, 220]):
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
        sec(out, "قائمة الصفحات المستهدفة (تُحفظ تلقائياً في pages.json)", "📄")
        tf = mk_card(out); tf.pack(fill="both", expand=True, pady=(0, 10))
        cols = ("✔", "الرابط", "اسم الصفحة", "تمرير", "حد")
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

        sec(out, "إضافة صفحة جديدة", "➕")
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
        Btn(br, "➕ إضافة", self._page_add, ACCENT).pack(side="left")
        Btn(br, "🗑 حذف", self._page_del, ERR).pack(side="left", padx=10)
        Btn(br, "🔁 تفعيل/تعطيل", lambda: self._page_toggle(None), "#6a5acd").pack(side="left")
        Btn(br, "📋 استيراد CSV", self._import_pages, "#37474f").pack(side="right")

    def _reload_pages_tree(self):
        for i in self.ptree.get_children():
            self.ptree.delete(i)
        for p in self.pages:
            self.ptree.insert("", "end", values=(
                "✅" if p.get("enabled", True) else "⛔",
                p["url"], p.get("name", ""), p.get("scroll", 5), p.get("limit", 30)))

    def _persist_pages_from_tree(self):
        pages = []
        for iid in self.ptree.get_children():
            v = self.ptree.item(iid, "values")
            pages.append({"enabled": v[0] == "✅", "url": v[1], "name": v[2],
                          "scroll": int(v[3]), "limit": int(v[4])})
        self.pages = pages
        try:
            save_pages(pages)
        except OSError:
            self._log("⚠️ تعذر حفظ pages.json")

    def _page_toggle(self, _event):
        sel = self.ptree.selection()
        if not sel:
            return
        v = list(self.ptree.item(sel[0], "values"))
        v[0] = "⛔" if v[0] == "✅" else "✅"
        self.ptree.item(sel[0], values=v)
        self._persist_pages_from_tree()
        self._refresh_watch_tree()

    def _page_add(self):
        url = self.pg_url.val().strip()
        if not url:
            return
        try:
            sc, lm = int(self.pg_scroll.get()), int(self.pg_limit.get())
            self.ptree.insert("", "end", values=("✅", url, self.pg_name.val().strip(), sc, lm))
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
                        "✅", p[0], p[1] if len(p) > 1 else "",
                        p[2] if len(p) > 2 else 5, p[3] if len(p) > 3 else 30))
        self._persist_pages_from_tree()
        self._refresh_watch_tree()

    def _get_pages(self):
        return list(self.pages)

    # ─────────── تبويب يوتيوب ───────────
    def _tab_youtube(self):
        out = tk.Frame(self.t_yt, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)
        sec(out, "إعدادات القناة", "📺")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        r1 = tk.Frame(inn, bg=CARD); r1.pack(fill="x", pady=4)
        tk.Label(r1, text="الحساب النشط:", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.acc_var = tk.StringVar(value=self.accounts[0] if self.accounts else "account1")
        self.acc_combo = ttk.Combobox(r1, textvariable=self.acc_var, values=self.accounts,
                                      state="readonly", width=30)
        self.acc_combo.pack(side="left", padx=10, ipady=3)

        r1b = tk.Frame(inn, bg=CARD); r1b.pack(fill="x", pady=6)
        tk.Label(r1b, text="حصة اليوم (وحدات):", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.quota_e = PEntry(r1b, width=10); self.quota_e.insert(0, "10000")
        self.quota_e.pack(side="left", padx=10, ipady=4)
        self.quota_var = tk.StringVar(value="كل فيديو = 1600 وحدة ≈ 6 فيديوهات/يوم بالحصة الافتراضية")
        tk.Label(r1b, textvariable=self.quota_var, bg=CARD, fg=TXT2, font=FSM).pack(side="left", padx=8)

        sec(out, "تفاصيل الفيديو", "📤")
        c2 = mk_card(out); c2.pack(fill="x")
        inn2 = tk.Frame(c2, bg=CARD, padx=14, pady=12); inn2.pack(fill="x")

        r2 = tk.Frame(inn2, bg=CARD); r2.pack(fill="x", pady=4)
        tk.Label(r2, text="الخصوصية:", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.priv_var = tk.StringVar(value="public")
        for v, t in [("public", "🌍 عام"), ("private", "🔒 خاص"), ("unlisted", "🔗 غير مدرج")]:
            ttk.Radiobutton(r2, text=t, variable=self.priv_var, value=v).pack(side="left", padx=10)

        r3 = tk.Frame(inn2, bg=CARD); r3.pack(fill="x", pady=6)
        tk.Label(r3, text="الوسوم (Tags):", bg=CARD, fg=TXT, width=15, anchor="e").pack(side="left")
        self.tags_e = PEntry(r3, width=50); self.tags_e.insert(0, "Facebook,Reel,Shorts")
        self.tags_e.pack(side="left", padx=10, ipady=4)

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

        sec(out, "تحسين المحتوى بـ Gemini AI", "🤖")
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
        tk.Label(inn, text="ℹ️ يُنادى AI مرة واحدة فقط لكل فيديو (النتيجة محفوظة)، وإذا تعذّر الوصول يكمل البرنامج بالتحسين العادي تلقائياً.",
                 bg=CARD, fg=TXT2, font=FSM, wraplength=820, justify="left").pack(anchor="w", pady=(4, 0))

        sec(out, "مفاتيح API — من aistudio.google.com/apikey", "🔑")
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
        self.ai_key_e = PEntry(r2, ph="الصق مفتاح API هنا (AIza...)", width=50)
        self.ai_key_e.pack(side="left", ipady=4)
        Btn(r2, "➕ إضافة", self._ai_key_add, ACCENT).pack(side="left", padx=8)
        Btn(r2, "🗑 حذف", self._ai_key_del, ERR).pack(side="left")
        Btn(r2, "🔁 تفعيل الكل الآن", self._ai_keys_reset, "#6a5acd").pack(side="left", padx=8)
        Btn(r2, "🧪 اختبار المفاتيح", self._ai_keys_test, "#00695c").pack(side="right")
        self._reload_ai_tree()

    def _reload_ai_tree(self, statuses=None):
        for i in self.ai_tree.get_children():
            self.ai_tree.delete(i)
        for i, entry in enumerate(load_ai_keys()):
            key = entry.get("key", "")
            masked = key[:10] + "…" + key[-4:] if len(key) > 14 else key
            if statuses and i in statuses:
                status = statuses[i]
            elif _iso_in_past(entry.get("exhausted_until", "")):
                status = "🟢 جاهز"
            else:
                until = entry.get("exhausted_until", "")[:16].replace("T", " ")
                status = f"🔴 مستنفد حتى {until}"
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
        self._log(f"🔑 أُضيف مفتاح Gemini (الإجمالي: {len(keys)})")

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
        self._log("🔁 أُعيد تفعيل كل المفاتيح")

    def _ai_keys_test(self):
        keys = load_ai_keys()
        if not keys:
            messagebox.showinfo("لا مفاتيح", "أضف مفتاحاً أولاً")
            return
        model = self.ai_model_var.get()
        def job():
            self._log(f"🧪 اختبار {len(keys)} مفتاح Gemini…")
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
        sec(out, "إعدادات التشغيل الذكي", "⚙️")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")

        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn, text="تشغيل المتصفح في الخلفية (Headless)",
                        variable=self.headless_var).pack(anchor="w", pady=4)
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

        sec(out, "الجدولة الذكية (تراكمية — لا تتداخل المواعيد)", "🕐")
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

        sec(out, "إشعارات Telegram (اختياري)", "🔔")
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
        self.tg_token_e = PEntry(r3, width=45); self.tg_token_e.pack(side="left", padx=8, ipady=4)
        tk.Label(r3, text="Chat ID:", bg=CARD, fg=TXT).pack(side="left")
        self.tg_chat_e = PEntry(r3, width=15); self.tg_chat_e.pack(side="left", padx=8, ipady=4)

        sec(out, "كوكيز فيسبوك — حل مشكلة توقف السحب", "🍪")
        c4 = mk_card(out); c4.pack(fill="x", pady=(10, 10))
        inn4 = tk.Frame(c4, bg=CARD, padx=14, pady=12); inn4.pack(fill="x")
        self.cookies_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn4, text="استخدام كوكيز حسابي في فيسبوك عند السحب والتحميل",
                        variable=self.cookies_enabled_var).pack(anchor="w", pady=2)
        rc = tk.Frame(inn4, bg=CARD); rc.pack(fill="x", pady=6)
        tk.Label(rc, text="ملف الكوكيز:", bg=CARD, fg=TXT, width=12, anchor="e").pack(side="left")
        self.cookies_path_var = tk.StringVar(value="cookies.txt")
        tk.Entry(rc, textvariable=self.cookies_path_var, bg=INP, fg=TXT, relief="flat",
                 font=FB, width=38, highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", padx=8, ipady=4)
        Btn(rc, "📂 اختيار", self._pick_cookies, "#37474f").pack(side="left")
        tk.Label(inn4, text="صدّر الكوكيز بإضافة «Get cookies.txt LOCALLY» من متصفحك بعد تسجيل الدخول، "
                            "واحفظها باسم cookies.txt بجانب البرنامج. ⚠️ الملف سري — لا تشاركه.",
                 bg=CARD, fg=TXT2, font=FSM, wraplength=820, justify="left").pack(anchor="w", pady=(4, 0))

        sec(out, "الصيانة", "🛠")
        c5 = mk_card(out); c5.pack(fill="x")
        inn5 = tk.Frame(c5, bg=CARD, padx=14, pady=12); inn5.pack(fill="x")
        rm = tk.Frame(inn5, bg=CARD); rm.pack(fill="x", pady=4)
        try:
            from yt_dlp.version import __version__ as _ytdlp_ver
        except Exception:
            _ytdlp_ver = "؟"
        tk.Label(rm, text=f"إصدار yt-dlp الحالي: {_ytdlp_ver}", bg=CARD, fg=TXT2,
                 font=FSM).pack(side="left")
        Btn(rm, "⬆ تحديث yt-dlp", self._update_ytdlp, "#00695c").pack(side="left", padx=15)
        tk.Label(inn5, text="فيسبوك يغيّر موقعه باستمرار — إذا توقف التحميل فجأة، حدّث yt-dlp أولاً.",
                 bg=CARD, fg=TXT2, font=FSM).pack(anchor="w", pady=(4, 0))

    def _pick_cookies(self):
        path = filedialog.askopenfilename(filetypes=[("Cookies", "*.txt"), ("كل الملفات", "*.*")])
        if path:
            self.cookies_path_var.set(path)

    def _update_ytdlp(self):
        def job():
            self._log("⬆ جاري تحديث yt-dlp…")
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                    capture_output=True, text=True, timeout=600)
                tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
                self._log(f"{'✅' if proc.returncode == 0 else '❌'} {tail[0] if tail else 'اكتمل'}")
            except Exception as error:
                self._log(f"❌ فشل التحديث: {error}")
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
        sec(pad, "تحسين العنوان قبل الرفع", "✨")
        c = mk_card(pad); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        self.title_opt_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn, text="تحسين ذكي: إزالة الحشو والكلمات المكررة من عناوين فيسبوك",
                        variable=self.title_opt_var).pack(anchor="w", pady=2)
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

        sec(pad, "الوصف والهاشتاجات", "📝")
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
        sec(pad, "الشعار فوق الشعار الأصلي (يتطلب ffmpeg)", "🖼")
        c2 = mk_card(pad); c2.pack(fill="x", pady=(0, 10))
        inn2 = tk.Frame(c2, bg=CARD, padx=14, pady=12); inn2.pack(fill="x")
        self.logo_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(inn2, text="تفعيل تركيب شعارك على الفيديو قبل الرفع",
                        variable=self.logo_enabled_var).pack(anchor="w", pady=2)
        self.cover_old_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inn2, text="تغطية شعار الصفحة الأصلي بخلفية معتمة تحت شعارك",
                        variable=self.cover_old_var).pack(anchor="w", pady=2)
        r2 = tk.Frame(inn2, bg=CARD); r2.pack(fill="x", pady=6)
        tk.Label(r2, text="ملف الشعار:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.logo_path_var = tk.StringVar(value="logo.png")
        tk.Entry(r2, textvariable=self.logo_path_var, bg=INP, fg=TXT, relief="flat",
                 font=FB, width=38, highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", padx=8, ipady=4)
        Btn(r2, "📂 اختيار", self._pick_logo, "#37474f").pack(side="left")
        r3 = tk.Frame(inn2, bg=CARD); r3.pack(fill="x", pady=6)
        tk.Label(r3, text="الموضع:", bg=CARD, fg=TXT, width=14, anchor="e").pack(side="left")
        self.logo_pos_var = tk.StringVar(value="أعلى اليمين")
        ttk.Combobox(r3, textvariable=self.logo_pos_var, state="readonly", width=12,
                     values=list(LOGO_POSITIONS.keys())).pack(side="left", padx=8)
        tk.Label(r3, text="الحجم % من العرض:", bg=CARD, fg=TXT).pack(side="left", padx=(14, 0))
        self.logo_scale_e = PEntry(r3, width=5); self.logo_scale_e.insert(0, "18")
        self.logo_scale_e.pack(side="left", padx=6)
        tk.Label(r3, text="الشفافية %:", bg=CARD, fg=TXT).pack(side="left", padx=(14, 0))
        self.logo_op_e = PEntry(r3, width=5); self.logo_op_e.insert(0, "100")
        self.logo_op_e.pack(side="left", padx=6)
        tk.Label(inn2, text="⚠️ ضع شعارك بجانب البرنامج باسم logo.png (يفضّل PNG بخلفية شفافة)",
                 bg=CARD, fg=TXT2, font=FSM).pack(anchor="w", pady=(4, 0))

        # ── فلاتر المحتوى ──
        sec(pad, "فلاتر المحتوى", "⏱")
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
        ttk.Checkbutton(inn3, text="👯 منع رفع نفس المحتوى مرتين (تطابق العناوين بين الصفحات)",
                        variable=self.dedupe_var).pack(anchor="w", pady=6)

    def _pick_logo(self):
        path = filedialog.askopenfilename(
            filetypes=[("صور", "*.png *.jpg *.jpeg *.webp"), ("كل الملفات", "*.*")])
        if path:
            self.logo_path_var.set(path)

    # ─────────── تبويب السجل ───────────
    def _tab_log(self):
        out = tk.Frame(self.t_log, bg=BG)
        out.pack(fill="both", expand=True, padx=14, pady=10)
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

    def _log(self, msg, replace_last=False):
        self._logq.put((msg, replace_last))

    # ─────────── تبويب الحسابات ───────────
    def _tab_accounts(self):
        out = tk.Frame(self.t_accs, bg=BG)
        out.pack(fill="both", expand=True, padx=20, pady=10)
        sec(out, "إدارة الحسابات", "👤")
        c = mk_card(out); c.pack(fill="x", pady=(0, 10))
        inn = tk.Frame(c, bg=CARD, padx=14, pady=12); inn.pack(fill="x")
        self.acc_lb = tk.Listbox(inn, bg=INP, fg=TXT, font=FB, relief="flat", height=8)
        self.acc_lb.pack(fill="x", pady=8)
        for a in self.accounts:
            self.acc_lb.insert("end", a)
        r = tk.Frame(inn, bg=CARD); r.pack(fill="x")
        self.new_acc_e = PEntry(r, ph="اسم الحساب", width=30)
        self.new_acc_e.pack(side="left", ipady=4)
        Btn(r, "➕ إضافة", self._acc_add, ACCENT).pack(side="left", padx=10)
        Btn(r, "🗑 حذف", self._acc_del, ERR).pack(side="left")

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
        sec(out, "إحصائيات الأداء", "📊")
        self.stats_card = StatsCard(out)
        row = tk.Frame(out, bg=BG); row.pack(pady=10)
        Btn(row, "🔄 تحديث البيانات", lambda: self.stats_card.refresh(), color="#455a64").pack(side="left", padx=5)
        Btn(row, "📜 فتح الأرشيف", self._open_history, color="#37474f").pack(side="left", padx=5)

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
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")
        foot = tk.Frame(self.root, bg=PANEL, pady=15)
        foot.pack(fill="x", side="bottom")
        inn = tk.Frame(foot, bg=PANEL); inn.pack(padx=25, fill="x")

        pg_row = tk.Frame(inn, bg=PANEL); pg_row.pack(fill="x", pady=(0, 8))
        self.prog_var = tk.DoubleVar()
        ttk.Progressbar(pg_row, variable=self.prog_var, orient="horizontal",
                        mode="determinate").pack(side="left", fill="x", expand=True, padx=(0, 15))
        self.pct_lbl = tk.Label(pg_row, text="0%", bg=PANEL, fg=ACCENT, font=FS, width=5)
        self.pct_lbl.pack(side="right")

        ctrl = tk.Frame(inn, bg=PANEL); ctrl.pack(fill="x")
        self.status_lbl = tk.Label(ctrl, text="⏸ جاهز — الصفحات محفوظة في pages.json",
                                   bg=PANEL, fg=TXT2, font=FB)
        self.status_lbl.pack(side="left")

        self.start_once_btn = Btn(ctrl, "▶ تشغيل مرة واحدة", lambda: self._do_start(False), SUCCESS)
        self.start_once_btn.pack(side="right")
        self.watch_btn = Btn(ctrl, "🔁 بدء المراقبة المستمرة", lambda: self._do_start(True), ACCENT)
        self.watch_btn.pack(side="right", padx=10)
        self.stop_btn = Btn(ctrl, "⏹ إيقاف", self._do_stop, ERR)
        self.stop_btn.pack(side="right")
        self.stop_btn.config(state="disabled")

    def _do_stop(self):
        self._stop.set()
        self.status_lbl.config(text="⏹ جاري الإيقاف...", fg=ERR)
        self.countdown_var.set("⏹ جاري الإيقاف…")

    # ─────────── مضخة رسائل الواجهة ───────────
    def _poll(self):
        """ينفذ كل تحديثات Tkinter من الخيط الرئيسي فقط."""
        while not self._logq.empty():
            msg, repl = self._logq.get_nowait()
            self.log_txt.configure(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            if repl and self._last_log_replace:
                self.log_txt.delete("end-2l", "end-1c")
            tag = "ok" if "✅" in msg or "🚀" in msg else "err" if "❌" in msg else "warn" if "⚠️" in msg else "info"
            self.log_txt.insert("end", f"[{ts}] ", "ts")
            self.log_txt.insert("end", f"{msg}\n", tag)
            self.log_txt.see("end")
            self.log_txt.configure(state="disabled")
            self._last_log_replace = repl

        while not self._uiq.empty():
            event, payload = self._uiq.get_nowait()
            if event == "progress":
                self.prog_var.set(payload)
                self.pct_lbl.config(text=f"{payload}%")
            elif event == "countdown":
                self.countdown_var.set(payload)
            elif event == "page_status":
                self.page_status[payload["url"]] = payload
                self._refresh_watch_tree()
            elif event == "quota":
                self.quota_var.set(f"استهلاك اليوم: {payload['used']} / {payload['limit']} وحدة")
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
            daily_quota = int(self.quota_e.get() or 10000)
            max_attempts = int(self.max_att_e.get() or 3)
            min_disk = float(self.disk_e.get() or 2)
        except ValueError as error:
            raise ValueError("أدخل أرقاماً صحيحة للتأخيرات والفواصل والحصة.") from error
        if min_delay < 1 or max_delay < min_delay or between_videos < 0:
            raise ValueError("تحقق من نطاق الجدولة والتأخير بين الفيديوهات.")
        if not (1 <= interval <= 720):
            raise ValueError("فاصل المراقبة يجب أن يكون بين 1 و720 دقيقة.")
        if not (1 <= max_attempts <= 10):
            raise ValueError("محاولات الفاشل بين 1 و10.")
        if min_disk < 0:
            raise ValueError("قيمة مساحة القرص غير صالحة.")
        try:
            logo_scale = int(self.logo_scale_e.get() or 18)
            logo_opacity = int(self.logo_op_e.get() or 100)
            min_dur = int(self.min_dur_e.get() or 0)
            max_dur = int(self.max_dur_e.get() or 0)
        except ValueError as error:
            raise ValueError("أدخل أرقاماً صحيحة لإعدادات الشعار وفلاتر المدة.") from error
        if not (1 <= logo_scale <= 60) or not (5 <= logo_opacity <= 100):
            raise ValueError("حجم الشعار بين 1 و60%، والشفافية بين 5 و100%.")
        if min_dur < 0 or max_dur < 0 or (max_dur and max_dur <= min_dur):
            raise ValueError("تحقق من نطاق فلتر المدة.")
        if self.logo_enabled_var.get() and not ffmpeg_available():
            raise ValueError("فعّلتَ تركيب الشعار لكن ffmpeg غير مثبت على الجهاز.\n"
                             "حمّله من ffmpeg.org وأضفه إلى PATH، أو عطّل الميزة.")
        return {
            "account": self.acc_var.get().strip(),
            "headless": self.headless_var.get(),
            "remove_tags": self.remove_tags_var.get(),
            "extra_description": self.desc_e.get("1.0", "end").strip(),
            "schedule_enabled": self.sched_var.get(),
            "min_delay": min_delay,
            "max_delay": max_delay,
            "privacy": self.priv_var.get(),
            "tags": self.tags_e.get().split(","),
            "delete_after_upload": self.del_after_var.get(),
            "between_videos": between_videos,
            "interval_minutes": interval,
            "fast_scrape": self.fast_scrape_var.get(),
            "daily_quota": daily_quota,
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
            "logo_cover_old": self.cover_old_var.get(),
            "min_duration": min_dur,
            "max_duration": max_dur,
            "category": YT_CATEGORIES.get(self.cat_var.get(), "25"),
        }

    def _do_start(self, watch_mode):
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
        self._stop.clear()
        self._watching = watch_mode
        self.start_once_btn.config(state="disabled")
        self.watch_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        mode_txt = "🔁 المراقبة المستمرة تعمل…" if watch_mode else "🚀 تشغيل مرة واحدة…"
        self.status_lbl.config(text=mode_txt, fg=SUCCESS)
        self.nb.select(self.t_log if not watch_mode else self.t_watch)
        self._thread = threading.Thread(
            target=self._worker, args=(pages, settings, watch_mode),
            daemon=True, name="fb-youtube-watch-worker",
        )
        self._thread.start()

    # ════════════════════════════════════════════════
    # منطق العامل الخلفي — لا يلمس Tkinter إطلاقاً
    # ════════════════════════════════════════════════
    def _worker(self, pages, settings, watch_mode):
        up_total, fail_total = 0, 0
        fatal_error = None
        try:
            yt, error = get_youtube(settings["account"])
            if not yt:
                fatal_error = error or "تعذر ربط حساب YouTube."
                self._log(f"❌ خطأ: {fatal_error}")
                return
            self._log("✅ متصل بـ YouTube API")
            quota = QuotaManager(settings["daily_quota"], self._log, account=settings["account"])
            ctx = {"yt": yt, "quota": quota, "account": settings["account"]}
            self._uiq.put(("quota", {"used": quota.used, "limit": quota.daily_limit}))

            if not watch_mode:
                up, fail = self._run_cycle(ctx, pages, settings, watch_mode=False)
                up_total, fail_total = up, fail
            else:
                self._log(f"🔁 بدأت المراقبة المستمرة — الفحص كل {settings['interval_minutes']} دقيقة")
                send_telegram(settings, "🔁 بدأت المراقبة المستمرة للريلز.", self._log)
                cycle, consec_errors = 0, 0
                while not self._stop.is_set():
                    cycle += 1
                    try:
                        self._log(f"━━ دورة المراقبة #{cycle} ━━")
                        up, fail = self._run_cycle(ctx, pages, settings, watch_mode=True)
                        up_total += up; fail_total += fail
                        self.stats["cycles"] = self.stats.get("cycles", 0) + 1
                        consec_errors = 0
                        wait_min = settings["interval_minutes"]
                    except Exception as error:
                        consec_errors += 1
                        logging.exception("خطأ غير متوقع في دورة المراقبة #%s", cycle)
                        wait_min = min(60, settings["interval_minutes"] * (2 ** consec_errors))
                        self._log(f"❌ خطأ في الدورة: {error} — إعادة المحاولة بعد {wait_min} دقيقة")
                        if settings.get("tg_errors"):
                            send_telegram(settings, f"⚠️ خطأ في دورة المراقبة: {error}", self._log)
                    if self._stop.is_set():
                        break
                    self._maybe_daily_summary(settings)
                    self._countdown_wait(wait_min * 60, "الفحص القادم بعد")
                self._log("⏹ توقفت المراقبة المستمرة.")
        except Exception as error:
            logging.exception("توقف عامل المراقبة بصورة غير متوقعة")
            fatal_error = f"خطأ غير متوقع: {error}"
            self._log(f"❌ {fatal_error}")
            if settings.get("tg_errors"):
                try:
                    send_telegram(settings, f"❌ توقف البرنامج بخطأ: {error}", self._log)
                except Exception:
                    pass
        finally:
            self.stats["total_uploaded"] += up_total
            self.stats["total_failed"] += fail_total
            try:
                save_stats(self.stats)
            except OSError:
                logging.exception("تعذر حفظ الإحصاءات")
            self._log(f"🏁 انتهى! مرفوع: {up_total}, فاشل: {fail_total}")
            self._uiq.put(("finished", {
                "uploaded": up_total, "failed": fail_total,
                "fatal": fatal_error, "watch": watch_mode,
            }))

    def _run_cycle(self, ctx, pages, settings, watch_mode):
        """دورة واحدة: سحب الروابط الجديدة + إعادة محاولة الفاشلين + الرفع.

        ctx = {"yt": اتصال يوتيوب, "quota": QuotaManager, "account": اسم الحساب}
        ويُحدَّث داخلياً عند تدوير الحسابات.
        """
        up_count, fail_count = 0, 0
        uploaded_ids = load_uploaded_ids()
        seen_ids = load_seen_ids()
        failed_q = load_failed_queue()
        queued_ids = {it["id"] for it in failed_q}
        cookies = cookies_arg(settings)

        # ─── 1) سحب الروابط من كل الصفحات (متصفح واحد يُعاد استخدامه) ───
        fresh = []
        driver = None
        for page in pages:
            if self._stop.is_set():
                break
            label = page.get("name") or page["url"]
            links = None
            if settings["fast_scrape"]:
                try:
                    links = scrape_links_fast(page["url"], page["limit"], self._log, cookies)
                    if links:
                        self._log(f"⚡ سحب سريع: {len(links)} رابط من {label}")
                except Exception as error:
                    logging.info("السحب السريع فشل لـ %s: %s", page["url"], error)
                    links = None
            if not links:
                if driver is None:
                    driver = init_driver(settings["headless"], self._log)
                    if driver is not None and cookies:
                        try:
                            load_cookies_to_driver(driver, cookies, self._log)
                        except Exception as error:
                            logging.warning("تعذر حقن الكوكيز: %s", error)
                if driver is None:
                    self._emit_page_status(page, 0, 0, "❌ تعذر تشغيل المتصفح")
                    continue
                try:
                    self._log(f"🌐 سحب عبر المتصفح: {label}")
                    links = get_reels(driver, page["url"], page["scroll"], page["limit"], self._log)
                except WebDriverException as error:
                    logging.warning("تعطل المتصفح، إعادة تشغيله: %s", error)
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = init_driver(settings["headless"], self._log)
                    if driver is None:
                        self._emit_page_status(page, 0, 0, "❌ تعطل المتصفح")
                        continue
                    if cookies:
                        try:
                            load_cookies_to_driver(driver, cookies, self._log)
                        except Exception:
                            pass
                    try:
                        links = get_reels(driver, page["url"], page["scroll"], page["limit"], self._log)
                    except Exception:
                        links = []
                except Exception as error:
                    logging.exception("خطأ في استخراج الروابط")
                    self._emit_page_status(page, 0, 0, f"❌ خطأ: {error}")
                    continue
            new_items = filter_new_reels(links or [], uploaded_ids, seen_ids, queued_ids)
            for it in new_items:
                it["page"] = page.get("name", "")
                it["attempts"] = 0
            fresh.extend(new_items)
            queued_ids.update(it["id"] for it in new_items)  # لا تُكرر داخل الدورة نفسها
            self._emit_page_status(page, len(links or []), len(new_items), "✅ تم الفحص")
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

        # ─── 2) قائمة المعالجة: الفاشلون أولاً ثم الجدد ───
        process_list = [dict(it, retry=True) for it in failed_q] + fresh
        failed_q = []
        total = len(process_list)
        if total == 0:
            self._log("😴 لا توجد فيديوهات جديدة — في انتظار الدورة القادمة.")
            save_failed_queue(failed_q)
            return 0, 0
        self._log(f"🎯 هذه الدورة: {total} فيديو للمعالجة ({len(fresh)} جديد + {total - len(fresh)} إعادة محاولة)")

        for index, item in enumerate(process_list, start=1):
            if self._stop.is_set():
                self._requeue_unprocessed(failed_q, process_list[index - 1:])
                break
            if disk_free_gb() < settings["min_disk_gb"]:
                self._log(f"🛑 مساحة القرص أقل من {settings['min_disk_gb']}GB — أُوقفت الدورة حمايةً للنظام.")
                self._requeue_unprocessed(failed_q, process_list[index - 1:])
                break

            self._uiq.put(("progress", int(((index - 1) / total) * 100)))
            tag = "🔁 إعادة" if item.get("retry") else "⬇️"
            self._log(f"{tag} [{index}/{total}] {item['url']}")

            video_data = download_video(item["url"], item.get("page", ""), self._log,
                                        MAX_RETRIES, settings["remove_tags"], cookies)
            if not video_data:
                fail_count += 1
                self._handle_failure(failed_q, seen_ids, item, "فشل التحميل", settings)
                continue
            self.stats["total_downloaded"] = self.stats.get("total_downloaded", 0) + 1

            # ─── فلتر المدة ───
            ok_dur, why = duration_ok(video_data.get("duration", 0),
                                      settings["min_duration"], settings["max_duration"])
            if not ok_dur:
                self._log(f"⏭ تخطي ({why}): {video_data['title'][:40]}")
                self._cleanup_file(video_data)
                save_seen_id(item["id"]); seen_ids.add(item["id"])
                append_history({"source_id": item["id"], "url": item["url"],
                                "page": item.get("page", ""), "status": "skipped",
                                "error": f"فلتر المدة: {why}"})
                continue

            # ─── كشف نفس المحتوى (نشرته صفحة أخرى سابقاً) ───
            if settings.get("dedupe_content", True) and is_duplicate_content(video_data.get("title", "")):
                self._log(f"👯 محتوى مكرر (نُشر سابقاً بعنوان مشابه): {video_data['title'][:40]}")
                self._cleanup_file(video_data)
                save_seen_id(item["id"]); seen_ids.add(item["id"])
                append_history({"source_id": item["id"], "url": item["url"],
                                "page": item.get("page", ""), "title": video_data["title"],
                                "status": "skipped", "error": "محتوى مكرر"})
                continue

            # ─── تحسين الذكاء الاصطناعي (Gemini) قبل القواعد ───
            eff_tags = list(settings["tags"])
            if settings.get("ai_enabled"):
                ai_meta = self._ai_metadata(item, video_data, settings)
                if ai_meta:
                    video_data["title"] = ai_meta["title"]
                    if ai_meta.get("description"):
                        video_data["desc"] = ai_meta["description"]
                    for t in ai_meta.get("tags", []):
                        if t not in eff_tags:
                            eff_tags.append(t)

            # ─── تحسين العنوان وبناء الوصف ───
            if settings["title_optimize"]:
                video_data["title"] = optimize_title(
                    video_data["title"], item.get("page", ""),
                    duration=video_data.get("duration", 0),
                    prefix=settings["title_prefix"], suffix=settings["title_suffix"],
                    auto_shorts=settings["auto_shorts"],
                    remove_hashtags=settings["remove_tags"])
                self._log(f"✨ العنوان النهائي: {video_data['title'][:60]}")
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
                    log_cb=self._log)
                if branded_path:
                    video_data["original_file"] = video_data["file"]
                    video_data["file"] = branded_path

            # ─── بوابة الحصة: تدوير الحساب أولاً ثم الانتظار ───
            if not ctx["quota"].can_afford():
                rotated = watch_mode and self._try_rotate_account(ctx, settings)
                if not rotated:
                    if watch_mode:
                        self._wait_for_quota_reset(ctx["quota"])
                    else:
                        self._log("🛑 حصة اليوم لا تكفي — أُوقف التشغيل اليدوي. فعّل المراقبة المستمرة للاستئناف التلقائي.")
                        self._cleanup_file(video_data, branded_path)
                        self._requeue_unprocessed(failed_q, process_list[index - 1:])
                        break

            publish_at = None
            if settings["schedule_enabled"]:
                publish_at = next_publish_time(settings["min_delay"], settings["max_delay"])
                self._log(f"🕐 مجدول للنشر: {publish_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")

            self._log(f"⬆️ رفع [{ctx['account']}]: {video_data['title'][:40]}...")
            result = upload_video(ctx["yt"], video_data, settings["privacy"], None,
                                  eff_tags, settings["category"], publish_at, self._log)
            if result == "QUOTA_ERROR" and watch_mode:
                # جرّب التحويل لحساب آخر وأعد المحاولة فوراً قبل أي انتظار
                ctx["quota"].mark_exhausted()
                if self._try_rotate_account(ctx, settings):
                    self._log(f"🔁 إعادة رفع فورية بالحساب الجديد [{ctx['account']}]…")
                    result = upload_video(ctx["yt"], video_data, settings["privacy"], None,
                                          eff_tags, settings["category"], publish_at, self._log)
            if result == "QUOTA_ERROR":
                ctx["quota"].mark_exhausted()
                self._uiq.put(("quota", {"used": ctx["quota"].used, "limit": ctx["quota"].daily_limit}))
                self._cleanup_file(video_data, branded_path)
                self._handle_failure(failed_q, seen_ids, item, "رفض الحصة", settings, count_attempt=False)
                if watch_mode:
                    self._wait_for_quota_reset(ctx["quota"])
                else:
                    self._requeue_unprocessed(failed_q, process_list[index:])
                    break
                continue

            if result:
                ctx["quota"].register()
                self._uiq.put(("quota", {"used": ctx["quota"].used, "limit": ctx["quota"].daily_limit}))
                save_uploaded_id(item["id"])
                uploaded_ids.add(item["id"])
                up_count += 1
                bump_daily_uploads(self.stats)
                remember_content(video_data["title"])
                append_history({"source_id": item["id"], "url": item["url"],
                                "page": item.get("page", ""), "title": video_data["title"],
                                "account": ctx["account"],
                                "youtube_id": result, "status": "uploaded"})
                send_telegram(settings,
                              f"✅ رُفع فيديو جديد إلى قناتك\n🎬 {video_data['title']}\n🔗 https://youtu.be/{result}",
                              self._log)
                if settings["delete_after_upload"]:
                    self._cleanup_file(video_data, branded_path)
            else:
                fail_count += 1
                self._cleanup_file(video_data, branded_path)
                self._handle_failure(failed_q, seen_ids, item, "فشل الرفع", settings)

            self._uiq.put(("progress", int((index / total) * 100)))
            if index < total and not self._stop.is_set():
                time.sleep(settings["between_videos"])

        save_failed_queue(failed_q)
        self._log(f"✅ اكتملت الدورة — مرفوع: {up_count}, فاشل/مؤجل: {fail_count}, قيد الانتظار: {len(failed_q)}")
        return up_count, fail_count

    def _try_rotate_account(self, ctx, settings):
        """عند نفاد حصة الحساب الحالي: ينتقل لأول حساب آخر مربوط لديه حصة متاحة."""
        for acc in load_accounts():
            if acc == ctx["account"]:
                continue
            try:
                yt2, err = get_youtube(acc)
            except Exception as error:
                logging.warning("فشل ربط الحساب البديل %s: %s", acc, error)
                continue
            if not yt2:
                self._log(f"⚠️ الحساب {acc} غير مربوط بعد ({err}) — تخطيه")
                continue
            q2 = QuotaManager(settings["daily_quota"], self._log, account=acc)
            if not q2.can_afford():
                self._log(f"⚠️ حصة الحساب {acc} مستنفدة أيضاً — تخطيه")
                continue
            ctx.update(yt=yt2, quota=q2, account=acc)
            self._log(f"🔄 نفدت حصة الحساب السابق — التحويل إلى: {acc}")
            send_telegram(settings, f"🔄 تبديل حساب يوتيوب تلقائياً إلى: {acc}", self._log)
            self._uiq.put(("quota", {"used": q2.used, "limit": q2.daily_limit}))
            return True
        return False

    # ─── أدوات العامل المساعدة ───
    def _ai_metadata(self, item, video_data, settings):
        """يولّد بيانات الفيديو بالذكاء الاصطناعي مرة واحدة لكل مقطع (كاش دائم)."""
        state = load_state()
        cache = state.get("ai_cache", {})
        if item["id"] in cache:
            self._log("🤖 بيانات AI من الذاكرة (سبق توليدها لهذا المقطع)")
            return cache[item["id"]]
        meta = ai_generate_metadata(
            video_data.get("title", ""), video_data.get("desc", ""),
            item.get("page", ""), video_data.get("duration", 0),
            settings.get("ai_model", GEMINI_MODELS[0]),
            settings.get("ai_style", "احترافي متوازن"), self._log)
        if meta:
            cache[item["id"]] = meta
            # حدّ أقصى للكاش حتى لا يتضخم state.json
            while len(cache) > 1500:
                cache.pop(next(iter(cache)))
            state["ai_cache"] = cache
            save_state(state)
            self._log(f"🤖 AI اقترح: {meta['title'][:60]}")
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
            f"📊 الملخص اليومي\n▫️ رُفع أمس: {yesterday_count} فيديو\n"
            f"▫️ الإجمالي الكلي: {self.stats.get('total_uploaded', 0)}\n"
            f"▫️ الفاشل الكلي: {self.stats.get('total_failed', 0)}\n"
            f"▫️ المراقبة تعمل ✅ (دورة #{self.stats.get('cycles', 0)})",
            self._log)

    def _handle_failure(self, failed_q, seen_ids, item, reason, settings, count_attempt=True):
        if count_attempt:
            item["attempts"] = item.get("attempts", 0) + 1
        item["last_error"] = reason
        if item.get("attempts", 0) >= settings["max_attempts"]:
            save_seen_id(item["id"])
            seen_ids.add(item["id"])
            append_history({"source_id": item["id"], "url": item["url"],
                            "page": item.get("page", ""), "status": "abandoned",
                            "error": f"{reason} — {item['attempts']} محاولات"})
            self._log(f"🚫 تجاهل نهائي بعد {item['attempts']} محاولات: {item['id']}")
        else:
            failed_q.append({"id": item["id"], "url": item["url"],
                             "page": item.get("page", ""), "attempts": item["attempts"],
                             "last_error": reason})
            self._log(f"📥 أُضيف لقائمة إعادة المحاولة (محاولة {item['attempts']}/{settings['max_attempts']})")

    def _requeue_unprocessed(self, failed_q, remaining):
        """يحفظ العناصر غير المعالجة لتلتقطها الدورة القادمة."""
        queued = {it["id"] for it in failed_q}
        for it in remaining:
            if it["id"] in queued:
                continue
            failed_q.append({"id": it["id"], "url": it["url"],
                             "page": it.get("page", ""), "attempts": it.get("attempts", 0),
                             "last_error": "مؤجل"})
            queued.add(it["id"])
        if remaining:
            self._log(f"📦 أُجّل {len(remaining)} عنصراً للدورة القادمة.")

    def _cleanup_file(self, video_data, branded_path=None):
        """يحذف الملف المعالَج والأصلي (إن كانا مختلفين) دون أن يوقف أي خطأ العملية."""
        paths = {video_data.get("file", "")}
        if branded_path:
            paths.add(branded_path)
        if video_data.get("original_file"):
            paths.add(video_data["original_file"])
        for path in paths:
            if not path:
                continue
            try:
                os.remove(path)
            except OSError as error:
                logging.warning("تعذر حذف الملف المحلي: %s", error)
                self._log(f"⚠️ تعذر حذف الملف المحلي: {error}")

    def _emit_page_status(self, page, found, new, status):
        self._uiq.put(("page_status", {
            "url": page["url"],
            "time": datetime.now().strftime("%H:%M:%S"),
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
            self._uiq.put(("countdown", f"⏳ {label}: {txt}"))
            time.sleep(1)
        self._uiq.put(("countdown", "🔍 يبدأ الفحص الآن…"))

    def _wait_for_quota_reset(self, quota):
        wait_s = quota.seconds_until_reset()
        self._log(f"⏳ حصة يوتيوب اليومية مستنفدة — استئناف تلقائي بعد التصفير "
                  f"(منتصف الليل بتوقيت المحيط الهادئ، بعد ~{wait_s // 3600} ساعة)")
        self._countdown_wait(wait_s, "تصفير الحصة بعد")
        quota._refresh()

    def _finish_worker(self, result):
        self._watching = False
        self.start_once_btn.config(state="normal")
        self.watch_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.countdown_var.set("⏸ المراقبة متوقفة")
        self.stats_card.refresh()
        if result.get("fatal"):
            self.status_lbl.config(text="❌ توقفت العملية؛ راجع السجل", fg=ERR)
            messagebox.showerror("تعذرت العملية", result["fatal"] + "\n\nراجع ملف errors.log للتفاصيل.")
        elif result.get("failed"):
            self.status_lbl.config(text="⚠️ اكتملت مع أخطاء؛ راجع السجل", fg=WARN)
        else:
            self.status_lbl.config(text="✅ اكتملت العملية", fg=SUCCESS)
        if not result.get("watch"):
            self.prog_var.set(100)
            self.pct_lbl.config(text="100%")

# ════════════════════════════════════════════════════
# CLI MODE — تشغيل 24/7 بدون واجهة (للسيرفرات)
# ════════════════════════════════════════════════════
def build_cli_settings(cfg=None):
    """يبني إعدادات وضع --cli من config.ini مع افتراضيات الواجهة نفسها."""
    cfg = cfg or config
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
    accounts = load_accounts()
    return {
        "account": g("SETTINGS", "ACCOUNT", accounts[0] if accounts else "account1"),
        "headless": True,
        "remove_tags": gb("STUDIO", "REMOVE_TAGS", False),
        "extra_description": g("STUDIO", "EXTRA_DESCRIPTION", ""),
        "schedule_enabled": gb("STUDIO", "SCHEDULE", False),
        "min_delay": gi("STUDIO", "SCHEDULE_MIN", 20),
        "max_delay": gi("STUDIO", "SCHEDULE_MAX", 60),
        "privacy": g("SETTINGS", "PRIVACY", "public"),
        "tags": g("STUDIO", "TAGS", "Facebook,Reel,Shorts").split(","),
        "delete_after_upload": gb("SETTINGS", "DELETE_AFTER_UPLOAD", True),
        "between_videos": gi("SETTINGS", "BETWEEN_VIDEOS", 10),
        "interval_minutes": gi("WATCH", "INTERVAL_MINUTES", 10),
        "fast_scrape": gb("WATCH", "FAST_SCRAPE", True),
        "daily_quota": gi("SETTINGS", "DAILY_QUOTA", 10000),
        "max_attempts": gi("SETTINGS", "MAX_ATTEMPTS", 3),
        "min_disk_gb": float(g("SETTINGS", "MIN_DISK_GB", "2")),
        "telegram_enabled": bool(g("STUDIO", "TELEGRAM_TOKEN", "").strip()),
        "tg_token": g("STUDIO", "TELEGRAM_TOKEN", ""),
        "tg_chat_id": g("STUDIO", "TELEGRAM_CHAT_ID", ""),
        "tg_errors": gb("STUDIO", "TELEGRAM_ERRORS", True),
        "cookies_enabled": os.path.isfile(g("STUDIO", "COOKIES_FILE", "cookies.txt")),
        "cookies_file": g("STUDIO", "COOKIES_FILE", "cookies.txt"),
        "dedupe_content": gb("STUDIO", "DEDUPE_CONTENT", True),
        "title_optimize": gb("STUDIO", "TITLE_OPTIMIZE", True),
        "auto_shorts": gb("STUDIO", "AUTO_SHORTS", True),
        "title_prefix": g("STUDIO", "TITLE_PREFIX", ""),
        "title_suffix": g("STUDIO", "TITLE_SUFFIX", ""),
        "auto_hashtags": gb("STUDIO", "AUTO_HASHTAGS", True),
        "credit_line": g("STUDIO", "CREDIT_LINE", ""),
        "logo_enabled": gb("STUDIO", "LOGO_ENABLED", False),
        "logo_path": g("STUDIO", "LOGO_PATH", "logo.png"),
        "logo_position": g("STUDIO", "LOGO_POSITION", "أعلى اليمين"),
        "logo_scale": gi("STUDIO", "LOGO_SCALE", 18),
        "logo_opacity": gi("STUDIO", "LOGO_OPACITY", 100),
        "logo_cover_old": gb("STUDIO", "LOGO_COVER_OLD", True),
        "min_duration": gi("STUDIO", "MIN_DURATION", 0),
        "max_duration": gi("STUDIO", "MAX_DURATION", 0),
        "category": g("STUDIO", "CATEGORY", "25"),
        "ai_enabled": gb("AI", "ENABLED", False),
        "ai_model": g("AI", "MODEL", GEMINI_MODELS[0]),
        "ai_style": g("AI", "STYLE", "احترافي متوازن"),
    }

def run_cli():
    """وضع المراقبة المستمرة بدون واجهة: py fb_youtube_uploader_v46_pro.py --cli"""
    shim = object.__new__(App)  # بدون إنشاء أي عنصر Tkinter
    shim._stop = threading.Event()
    shim._uiq = queue.Queue()
    shim._logq = queue.Queue()
    shim.stats = load_stats()

    def cli_log(msg, replace_last=False):
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)
    shim._log = cli_log

    settings = build_cli_settings()
    pages = [p for p in load_pages() if p.get("enabled", True)]
    if not pages:
        print("❌ لا توجد صفحات مفعّلة في pages.json")
        return 1
    print("═══════════════════════════════════════════")
    print(" FB → YouTube v4.6 PRO — وضع سطر الأوامر")
    print(f" الصفحات: {len(pages)} | الفحص كل {settings['interval_minutes']} دقيقة")
    print(f" الحساب: {settings['account']} | كوكيز: {'✅' if settings['cookies_enabled'] else '❌'}")
    print(" للإيقاف: Ctrl+C")
    print("═══════════════════════════════════════════")
    try:
        App._worker(shim, pages, settings, watch_mode=True)
    except KeyboardInterrupt:
        print("\n⏹ أُوقف بواسطة المستخدم (Ctrl+C)")
        shim._stop.set()
    return 0

if __name__ == "__main__":
    if "--cli" in sys.argv:
        sys.exit(run_cli())
    root = tk.Tk()
    App(root)
    root.mainloop()
