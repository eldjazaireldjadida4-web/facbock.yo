"""اختبارات مزايا v4.7: مفاتيح Gemini، التدوير عند نفاد الحصة، توليد البيانات، التراجع الآمن."""
from __future__ import annotations

import importlib.util
import io
import json
import queue
import threading
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent / "fixtures" / "versions" / "fb_youtube_uploader_v47_pro.py"
spec = importlib.util.spec_from_file_location("pro47", MODULE_PATH)
assert spec and spec.loader
pro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pro)


@pytest.fixture()
def sandboxed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(pro, "UPLOADED_IDS_FILE", str(tmp_path / "uploaded_ids.txt"))
    monkeypatch.setattr(pro, "SEEN_IDS_FILE", str(tmp_path / "seen_ids.txt"))
    monkeypatch.setattr(pro, "PAGES_FILE", str(tmp_path / "pages.json"))
    monkeypatch.setattr(pro, "FAILED_QUEUE_FILE", str(tmp_path / "failed_queue.json"))
    monkeypatch.setattr(pro, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(pro, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(pro, "STATS_FILE", str(tmp_path / "stats.json"))
    monkeypatch.setattr(pro, "AI_KEYS_FILE", str(tmp_path / "ai_keys.json"))
    return tmp_path


def make_app_stub():
    app = object.__new__(pro.App)
    app._stop = threading.Event()
    app._uiq = queue.Queue()
    app._logq = queue.Queue()
    app.stats = {"total_uploaded": 0, "total_failed": 0, "total_downloaded": 0, "cycles": 0}
    app._log = lambda *_a, **_k: None
    return app


def base_settings(**over):
    s = {
        "account": "c", "headless": True, "remove_tags": False, "extra_description": "",
        "schedule_enabled": False, "min_delay": 20, "max_delay": 60, "privacy": "public",
        "tags": ["Facebook"], "delete_after_upload": False, "between_videos": 0,
        "interval_minutes": 10, "fast_scrape": True, "daily_quota": 10000,
        "max_attempts": 3, "min_disk_gb": 0, "telegram_enabled": False,
        "tg_token": "", "tg_chat_id": "", "tg_errors": False,
        "cookies_enabled": False, "cookies_file": "", "dedupe_content": True,
        "title_optimize": True, "auto_shorts": True, "title_prefix": "",
        "title_suffix": "", "auto_hashtags": True, "credit_line": "",
        "logo_enabled": False, "logo_path": "logo.png", "logo_position": "أعلى اليمين",
        "logo_scale": 18, "logo_opacity": 100, "logo_cover_old": True,
        "min_duration": 0, "max_duration": 0, "category": "25",
        "ai_enabled": True, "ai_model": "gemini-2.5-flash", "ai_style": "احترافي متوازن",
    }
    s.update(over)
    return s


PAGES = [{"url": "https://www.facebook.com/elwataniatvweb/reels/", "name": "الوطنية TV",
          "scroll": 1, "limit": 5, "enabled": True}]

GOOD_BODY = json.dumps({"candidates": [{"content": {"parts": [{"text":
    json.dumps({"title": "عنوان AI احترافي", "description": "وصف من AI",
                "tags": ["AI1", "AI2"]}, ensure_ascii=False)}]}}]}).encode()

class FakeResp:
    def __init__(self, body): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False

def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


# ─────────────────────────────────────────────
# 1) إدارة المفاتيح
# ─────────────────────────────────────────────
def test_keys_roundtrip_and_pick(sandboxed_files):
    pro.save_ai_keys([
        {"key": "KEY1", "exhausted_until": "", "last_error": ""},
        {"key": "KEY2", "exhausted_until": "", "last_error": ""},
    ])
    idx, key = pro.ai_available_key(pro.load_ai_keys())
    assert (idx, key) == (0, "KEY1")
    # استنفاد الأول → الثاني يصبح المتاح
    keys = pro.load_ai_keys()
    pro.ai_mark_exhausted(keys, 0, "نفاد")
    idx, key = pro.ai_available_key(pro.load_ai_keys())
    assert (idx, key) == (1, "KEY2")
    # استنفاد الكل → لا شيء
    keys = pro.load_ai_keys()
    pro.ai_mark_exhausted(keys, 1, "نفاد")
    assert pro.ai_available_key(pro.load_ai_keys()) == (None, None)


def test_exhausted_key_recovers_next_day(sandboxed_files):
    pro.save_ai_keys([{"key": "K", "exhausted_until": "", "last_error": ""}])
    pro.ai_mark_exhausted(pro.load_ai_keys(), 0)
    keys = pro.load_ai_keys()
    assert not pro._iso_in_past(keys[0]["exhausted_until"])      # مستنفد الآن
    # محاكاة الغد
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    keys[0]["exhausted_until"] = yesterday
    pro.save_ai_keys(keys)
    assert pro.ai_available_key(pro.load_ai_keys()) == (0, "K")  # عاد متاحاً


# ─────────────────────────────────────────────
# 2) التدوير الفعلي أثناء التوليد
# ─────────────────────────────────────────────
def test_rotation_on_429_then_success(sandboxed_files, monkeypatch):
    pro.save_ai_keys([
        {"key": "DEAD", "exhausted_until": "", "last_error": ""},
        {"key": "ALIVE", "exhausted_until": "", "last_error": ""},
    ])
    calls = []
    def fake_urlopen(req, timeout=0):
        key = req.full_url.split("key=")[-1]
        calls.append(key)
        if key == "DEAD":
            raise http_error(429)
        return FakeResp(GOOD_BODY)
    monkeypatch.setattr(pro.urllib.request, "urlopen", fake_urlopen)

    text, keys = pro.gemini_generate("اختبار", "gemini-2.5-flash")
    assert calls == ["DEAD", "ALIVE"]                  # انتقل للثاني تلقائياً
    assert text
    saved = pro.load_ai_keys()
    assert not pro._iso_in_past(saved[0]["exhausted_until"])   # الأول مستنفد
    assert saved[1]["exhausted_until"] == ""                   # الثاني سليم


def test_all_keys_exhausted_returns_none(sandboxed_files, monkeypatch):
    pro.save_ai_keys([{"key": "X", "exhausted_until": "", "last_error": ""}])
    monkeypatch.setattr(pro.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(http_error(429)))
    text, _ = pro.gemini_generate("اختبار", "gemini-2.5-flash")
    assert text is None
    # محاولة ثانية لا تلمس الشبكة إطلاقاً (المفتاح معلّم)
    def boom(*_a, **_k):
        raise AssertionError("لا يجب النداء والمفتاح مستنفد")
    monkeypatch.setattr(pro.urllib.request, "urlopen", boom)
    assert pro.gemini_generate("اختبار", "gemini-2.5-flash")[0] is None


def test_no_keys_returns_none_gracefully(sandboxed_files):
    text, keys = pro.gemini_generate("اختبار", "gemini-2.5-flash")
    assert text is None and keys == []


# ─────────────────────────────────────────────
# 3) توليد البيانات والتحليل
# ─────────────────────────────────────────────
def test_ai_generate_metadata_parses_json(sandboxed_files, monkeypatch):
    pro.save_ai_keys([{"key": "K1", "exhausted_until": "", "last_error": ""}])
    monkeypatch.setattr(pro.urllib.request, "urlopen", lambda req, timeout=0: FakeResp(GOOD_BODY))
    meta = pro.ai_generate_metadata("عنوان خام", "وصف", "صفحة", 45,
                                    "gemini-2.5-flash", "احترافي متوازن")
    assert meta["title"] == "عنوان AI احترافي"
    assert meta["tags"] == ["AI1", "AI2"]


def test_extract_json_handles_markdown_fence():
    fenced = '```json\n{"title": "T", "description": "D", "tags": ["a"]}\n```'
    data = pro._extract_json(fenced)
    assert data["title"] == "T"
    assert pro._extract_json("لا يوجد json") is None
    assert pro._extract_json("") is None


# ─────────────────────────────────────────────
# 4) التكامل مع الدورة: AI ثم الكاش ثم التراجع
# ─────────────────────────────────────────────
def test_cycle_uses_ai_metadata_and_caches(sandboxed_files, monkeypatch):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/AI1"])
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: {
        "file": "v.mp4", "title": "عنوان خام من فيسبوك", "desc": "", "duration": 40})
    captured = {}
    def fake_upload(yt, vd, privacy, playlist, tags, category, sched, log_cb):
        captured.update(title=vd["title"], desc=vd["desc"], tags=tags)
        return "YT-AI"
    monkeypatch.setattr(pro, "upload_video", fake_upload)
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)
    pro.save_ai_keys([{"key": "K", "exhausted_until": "", "last_error": ""}])
    ai_calls = []
    def fake_gemini(req, timeout=0):
        ai_calls.append(1)
        return FakeResp(GOOD_BODY)
    monkeypatch.setattr(pro.urllib.request, "urlopen", fake_gemini)

    quota = pro.QuotaManager(10000)
    ctx = {"yt": object(), "quota": quota, "account": "c"}
    up, _ = app._run_cycle(ctx, PAGES, base_settings(), True)
    assert up == 1
    assert len(ai_calls) == 1                          # نداء واحد فقط
    assert captured["title"].startswith("عنوان AI احترافي")
    assert captured["title"].endswith("#Shorts")       # قواعد الاستوديو فوق AI
    assert "AI1" in captured["tags"]                   # وسوم AI اندمجت
    # الكاش: النداء الثاني لنفس المقطع لا يستهلك API
    meta = app._ai_metadata({"id": "AI1"}, {"title": "x", "desc": "", "duration": 1},
                            base_settings())
    assert meta["title"] == "عنوان AI احترافي" and len(ai_calls) == 1


def test_cycle_falls_back_when_ai_fails(sandboxed_files, monkeypatch):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/FB1"])
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: {
        "file": "v.mp4", "title": "شاهد الآن خبر عاجل", "desc": "", "duration": 90})
    captured = {}
    monkeypatch.setattr(pro, "upload_video",
                        lambda yt, vd, *a, **k: (captured.update(title=vd["title"]) or "YT-F"))
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)
    # لا مفاتيح إطلاقاً → يجب أن يكمل بالتحسين العادي دون توقف
    quota = pro.QuotaManager(10000)
    ctx = {"yt": object(), "quota": quota, "account": "c"}
    up, _ = app._run_cycle(ctx, PAGES, base_settings(), True)
    assert up == 1
    assert "شاهد" not in captured["title"]             # التحسين العادي عمل
    assert "#Shorts" not in captured["title"]          # 90 ثانية > 60


def test_ai_ping_key_success_and_failure(sandboxed_files, monkeypatch):
    monkeypatch.setattr(pro.urllib.request, "urlopen",
                        lambda req, timeout=0: FakeResp(
                            json.dumps({"candidates": [{"content": {"parts": [{"text": "تم"}]}}]}).encode()))
    ok, msg = pro.ai_ping_key("K", "gemini-2.5-flash")
    assert ok and "يعمل" in msg
    monkeypatch.setattr(pro.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(http_error(429)))
    ok, msg = pro.ai_ping_key("K", "gemini-2.5-flash")
    assert not ok and "429" in msg


def test_cli_settings_include_ai():
    s = pro.build_cli_settings()
    assert s["ai_enabled"] is False
    assert s["ai_model"] == pro.GEMINI_MODELS[0]
    assert s["ai_style"] == "احترافي متوازن"
