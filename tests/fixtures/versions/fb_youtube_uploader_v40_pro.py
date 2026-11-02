"""
╔══════════════════════════════════════════════════════════════╗
║   FB → YouTube Uploader  v4.0 PRO — Watch Mode               ║
║   مراقبة مستمرة بلا توقف: أي Reel جديد يُرفع تلقائياً        ║
║   مزايا: مدير حصة يوتيوب، قائمة فاشلين ذكية، سحب سريع،       ║
║   إشعارات Telegram، حارس القرص، أرشيف كامل لكل عملية         ║
╚══════════════════════════════════════════════════════════════╝
"""

import time, os, re, json, logging, random, threading, queue, configparser, shutil
import urllib.request, urllib.parse
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

logging.basicConfig(
    filename="errors.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)

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
# QUOTA MANAGER — إدارة حصة يوتيوب اليومية
# ════════════════════════════════════════════════════
class QuotaManager:
    """يتتبع وحدات حصة YouTube API ويصفّر العداد تلقائياً منتصف الليل بتوقيت المحيط الهادئ."""

    COST_UPLOAD = 1600  # وحدة لكل videos.insert

    def __init__(self, daily_limit=10000, log_cb=None):
        self.daily_limit = max(self.COST_UPLOAD, int(daily_limit))
        self.log_cb = log_cb
        state = load_state()
        today = pt_now().date().isoformat()
        self.date = state.get("quota_date", "")
        self.used = int(state.get("quota_used", 0)) if self.date == today else 0
        self.date = today
        self._persist()

    def _persist(self):
        state = load_state()
        state["quota_date"] = self.date
        state["quota_used"] = self.used
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

def scrape_links_fast(page_url, limit, log_cb=None):
    """سحب سريع عبر yt-dlp من دون متصفح. يعيد None عند الفشل ليُستخدم المتصفح."""
    opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": 45,
    }
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

def download_video(url, page_name="", log_cb=None, retries=MAX_RETRIES, remove_tags=False):
    ydl_opts = {
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(VIDEO_DIRECTORY, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "merge_output_format": "mp4",
        "retries": 10,
        "socket_timeout": 60,
    }
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
        self.root.title("🎬 FB → YouTube Uploader v4.0 PRO — Watch Mode")
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
        tk.Label(tl, text="مراقبة مستمرة للريلز ورفع تلقائي فوري  •  v4.0",
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
        self.t_log   = tk.Frame(self.nb, bg=BG)
        self.t_accs  = tk.Frame(self.nb, bg=BG)
        self.t_dash  = tk.Frame(self.nb, bg=BG)

        self.nb.add(self.t_watch, text="  🔁 المراقبة المستمرة  ")
        self.nb.add(self.t_pages, text="  📄 الصفحات  ")
        self.nb.add(self.t_yt,    text="  📺 يوتيوب  ")
        self.nb.add(self.t_opts,  text="  ⚙️ خيارات متقدمة  ")
        self.nb.add(self.t_log,   text="  📋 السجل  ")
        self.nb.add(self.t_accs,  text="  👤 الحسابات  ")
        self.nb.add(self.t_dash,  text="  📊 لوحة التحكم  ")

        self._tab_watch()
        self._tab_pages()
        self._tab_youtube()
        self._tab_options()
        self._tab_log()
        self._tab_accounts()
        self._tab_dashboard()

    # ─────────── تبويب المراقبة ───────────
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

        tk.Label(inn2, text="وصف إضافي:", bg=CARD, fg=TXT, anchor="e").pack(anchor="w", pady=(6, 0))
        self.desc_e = tk.Text(inn2, bg=INP, fg=TXT, height=4, width=70, relief="flat",
                              highlightthickness=1, highlightbackground=BORDER)
        self.desc_e.pack(pady=5)

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
        r3 = tk.Frame(inn3, bg=CARD); r3.pack(fill="x", pady=6)
        tk.Label(r3, text="Bot Token:", bg=CARD, fg=TXT, width=12, anchor="e").pack(side="left")
        self.tg_token_e = PEntry(r3, width=45); self.tg_token_e.pack(side="left", padx=8, ipady=4)
        tk.Label(r3, text="Chat ID:", bg=CARD, fg=TXT).pack(side="left")
        self.tg_chat_e = PEntry(r3, width=15); self.tg_chat_e.pack(side="left", padx=8, ipady=4)

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
            quota = QuotaManager(settings["daily_quota"], self._log)
            self._uiq.put(("quota", {"used": quota.used, "limit": quota.daily_limit}))

            if not watch_mode:
                up, fail = self._run_cycle(yt, quota, pages, settings, watch_mode=False)
                up_total, fail_total = up, fail
            else:
                self._log(f"🔁 بدأت المراقبة المستمرة — الفحص كل {settings['interval_minutes']} دقيقة")
                send_telegram(settings, "🔁 بدأت المراقبة المستمرة للريلز.", self._log)
                cycle, consec_errors = 0, 0
                while not self._stop.is_set():
                    cycle += 1
                    try:
                        self._log(f"━━ دورة المراقبة #{cycle} ━━")
                        up, fail = self._run_cycle(yt, quota, pages, settings, watch_mode=True)
                        up_total += up; fail_total += fail
                        self.stats["cycles"] = self.stats.get("cycles", 0) + 1
                        consec_errors = 0
                        wait_min = settings["interval_minutes"]
                    except Exception as error:
                        consec_errors += 1
                        logging.exception("خطأ غير متوقع في دورة المراقبة #%s", cycle)
                        wait_min = min(60, settings["interval_minutes"] * (2 ** consec_errors))
                        self._log(f"❌ خطأ في الدورة: {error} — إعادة المحاولة بعد {wait_min} دقيقة")
                    if self._stop.is_set():
                        break
                    self._countdown_wait(wait_min * 60, "الفحص القادم بعد")
                self._log("⏹ توقفت المراقبة المستمرة.")
        except Exception as error:
            logging.exception("توقف عامل المراقبة بصورة غير متوقعة")
            fatal_error = f"خطأ غير متوقع: {error}"
            self._log(f"❌ {fatal_error}")
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

    def _run_cycle(self, yt, quota, pages, settings, watch_mode):
        """دورة واحدة: سحب الروابط الجديدة + إعادة محاولة الفاشلين + الرفع."""
        up_count, fail_count = 0, 0
        uploaded_ids = load_uploaded_ids()
        seen_ids = load_seen_ids()
        failed_q = load_failed_queue()
        queued_ids = {it["id"] for it in failed_q}

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
                    links = scrape_links_fast(page["url"], page["limit"], self._log)
                    if links:
                        self._log(f"⚡ سحب سريع: {len(links)} رابط من {label}")
                except Exception as error:
                    logging.info("السحب السريع فشل لـ %s: %s", page["url"], error)
                    links = None
            if not links:
                if driver is None:
                    driver = init_driver(settings["headless"], self._log)
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
                                        MAX_RETRIES, settings["remove_tags"])
            if not video_data:
                fail_count += 1
                self._handle_failure(failed_q, seen_ids, item, "فشل التحميل", settings)
                continue
            self.stats["total_downloaded"] = self.stats.get("total_downloaded", 0) + 1

            if settings["extra_description"]:
                video_data["desc"] += f"\n\n{settings['extra_description']}"

            # ─── بوابة الحصة ───
            if not quota.can_afford():
                if watch_mode:
                    self._wait_for_quota_reset(quota)
                else:
                    self._log("🛑 حصة اليوم لا تكفي — أُوقف التشغيل اليدوي. فعّل المراقبة المستمرة للاستئناف التلقائي.")
                    self._cleanup_file(video_data)
                    self._requeue_unprocessed(failed_q, process_list[index - 1:])
                    break

            publish_at = None
            if settings["schedule_enabled"]:
                publish_at = next_publish_time(settings["min_delay"], settings["max_delay"])
                self._log(f"🕐 مجدول للنشر: {publish_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")

            self._log(f"⬆️ رفع: {video_data['title'][:40]}...")
            result = upload_video(yt, video_data, settings["privacy"], None,
                                  settings["tags"], "22", publish_at, self._log)
            if result == "QUOTA_ERROR":
                quota.mark_exhausted()
                self._uiq.put(("quota", {"used": quota.used, "limit": quota.daily_limit}))
                self._cleanup_file(video_data)
                self._handle_failure(failed_q, seen_ids, item, "رفض الحصة", settings, count_attempt=False)
                if watch_mode:
                    self._wait_for_quota_reset(quota)
                else:
                    self._requeue_unprocessed(failed_q, process_list[index:])
                    break
                continue

            if result:
                quota.register()
                self._uiq.put(("quota", {"used": quota.used, "limit": quota.daily_limit}))
                save_uploaded_id(item["id"])
                uploaded_ids.add(item["id"])
                up_count += 1
                bump_daily_uploads(self.stats)
                append_history({"source_id": item["id"], "url": item["url"],
                                "page": item.get("page", ""), "title": video_data["title"],
                                "youtube_id": result, "status": "uploaded"})
                send_telegram(settings,
                              f"✅ رُفع فيديو جديد إلى قناتك\n🎬 {video_data['title']}\n🔗 https://youtu.be/{result}",
                              self._log)
                if settings["delete_after_upload"]:
                    self._cleanup_file(video_data)
            else:
                fail_count += 1
                self._cleanup_file(video_data)
                self._handle_failure(failed_q, seen_ids, item, "فشل الرفع", settings)

            self._uiq.put(("progress", int((index / total) * 100)))
            if index < total and not self._stop.is_set():
                time.sleep(settings["between_videos"])

        save_failed_queue(failed_q)
        self._log(f"✅ اكتملت الدورة — مرفوع: {up_count}, فاشل/مؤجل: {fail_count}, قيد الانتظار: {len(failed_q)}")
        return up_count, fail_count

    # ─── أدوات العامل المساعدة ───
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

    def _cleanup_file(self, video_data):
        try:
            os.remove(video_data["file"])
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

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
