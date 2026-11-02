"""اختبارات منطق المراقبة المستمرة في النسخة v4.0 Pro — بدون واجهة وبدون شبكة."""
from __future__ import annotations

import importlib.util
import json
import queue
import threading
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent / "fixtures" / "versions" / "fb_youtube_uploader_v40_pro.py"
spec = importlib.util.spec_from_file_location("pro_app", MODULE_PATH)
assert spec and spec.loader
pro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pro)


# ─────────────────────────────────────────────
# أدوات مساعدة للاختبارات
# ─────────────────────────────────────────────
@pytest.fixture()
def sandboxed_files(tmp_path, monkeypatch):
    """يوجّه كل ملفات الحالة إلى مجلد مؤقت معزول."""
    monkeypatch.setattr(pro, "UPLOADED_IDS_FILE", str(tmp_path / "uploaded_ids.txt"))
    monkeypatch.setattr(pro, "SEEN_IDS_FILE", str(tmp_path / "seen_ids.txt"))
    monkeypatch.setattr(pro, "PAGES_FILE", str(tmp_path / "pages.json"))
    monkeypatch.setattr(pro, "FAILED_QUEUE_FILE", str(tmp_path / "failed_queue.json"))
    monkeypatch.setattr(pro, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(pro, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(pro, "STATS_FILE", str(tmp_path / "stats.json"))
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
    settings = {
        "account": "channel",
        "headless": True,
        "remove_tags": False,
        "extra_description": "",
        "schedule_enabled": False,
        "min_delay": 20,
        "max_delay": 60,
        "privacy": "public",
        "tags": ["Facebook"],
        "delete_after_upload": False,
        "between_videos": 0,
        "interval_minutes": 10,
        "fast_scrape": True,
        "daily_quota": 10000,
        "max_attempts": 3,
        "min_disk_gb": 0,
        "telegram_enabled": False,
        "tg_token": "",
        "tg_chat_id": "",
    }
    settings.update(over)
    return settings


PAGES = [
    {"url": "https://www.facebook.com/elwataniatvweb/reels/", "name": "الوطنية TV",
     "scroll": 1, "limit": 5, "enabled": True},
    {"url": "https://www.facebook.com/ElwataniaSport/reels/", "name": "الوطنية سبورت",
     "scroll": 1, "limit": 5, "enabled": True},
]


# ─────────────────────────────────────────────
# 1) استخراج المعرفات والفلترة
# ─────────────────────────────────────────────
def test_reel_id_from_url():
    assert pro.reel_id_from_url("https://www.facebook.com/reel/123456/?x=1") == "123456"
    assert pro.reel_id_from_url("https://www.facebook.com/watch/videos/987654") == "987654"
    assert pro.reel_id_from_url("no-match-here") == "no-match-here"


def test_filter_new_reels_skips_known_and_duplicates():
    links = [
        "https://facebook.com/reel/AAA",
        "https://facebook.com/reel/BBB",
        "https://facebook.com/reel/CCC",
        "https://facebook.com/reel/DDD",
        "https://facebook.com/reel/AAA",  # مكرر داخل نفس السحب
    ]
    fresh = pro.filter_new_reels(links, {"AAA"}, {"BBB"}, {"CCC"})
    assert [i["id"] for i in fresh] == ["DDD"]


def test_filter_new_reels_empty():
    assert pro.filter_new_reels([], set(), set(), set()) == []


# ─────────────────────────────────────────────
# 2) الصفحات الافتراضية محفوظة في المشروع
# ─────────────────────────────────────────────
def test_default_pages_are_saved_on_first_run(sandboxed_files):
    pages = pro.load_pages()
    urls = {p["url"] for p in pages}
    assert "https://www.facebook.com/elwataniatvweb/reels/" in urls
    assert "https://www.facebook.com/ElwataniaSport/reels/" in urls
    # الملف كُتب فعلاً على القرص
    saved = json.loads((sandboxed_files / "pages.json").read_text(encoding="utf-8"))
    assert len(saved) == 2
    assert all(p["enabled"] for p in saved)


def test_pages_roundtrip_with_toggle(sandboxed_files):
    pages = pro.load_pages()
    pages[0]["enabled"] = False
    pro.save_pages(pages)
    again = pro.load_pages()
    assert again[0]["enabled"] is False
    assert again[1]["enabled"] is True


# ─────────────────────────────────────────────
# 3) مدير الحصة
# ─────────────────────────────────────────────
def test_quota_register_and_limit(sandboxed_files):
    q = pro.QuotaManager(daily_limit=10000)
    assert q.can_afford()           # 1600 متاحة
    for _ in range(6):              # 6 × 1600 = 9600
        q.register()
    assert not q.can_afford()       # 9600 + 1600 > 10000
    # الاستهلاك محفوظ — مدير جديد بنفس اليوم يرى نفس القيمة
    q2 = pro.QuotaManager(daily_limit=10000)
    assert q2.used == 9600


def test_quota_mark_exhausted(sandboxed_files):
    q = pro.QuotaManager(daily_limit=10000)
    q.mark_exhausted()
    assert not q.can_afford()


def test_quota_reset_after_new_day(sandboxed_files, monkeypatch):
    q = pro.QuotaManager(daily_limit=10000)
    q.register(); q.register()
    assert q.used == 3200
    # محاكاة يوم جديد
    future = pro.pt_now() + pro.timedelta(days=1)
    monkeypatch.setattr(pro, "pt_now", lambda: future)
    assert q.can_afford() and q.used == 0


def test_seconds_until_reset_bounds():
    s = pro.QuotaManager.seconds_until_reset(object.__new__(pro.QuotaManager))
    # بين دقيقة و ~24 ساعة + هامش
    assert 60 <= s <= 86400 + 60


# ─────────────────────────────────────────────
# 4) قائمة الفاشلين
# ─────────────────────────────────────────────
def test_failed_queue_roundtrip(sandboxed_files):
    items = [{"id": "X1", "url": "u", "page": "p", "attempts": 1, "last_error": "فشل التحميل"}]
    pro.save_failed_queue(items)
    assert pro.load_failed_queue() == items


# ─────────────────────────────────────────────
# 5) دورة مراقبة كاملة — رفع جديد + إعادة فاشل
# ─────────────────────────────────────────────
def test_watch_cycle_uploads_fresh_and_retries_failed(sandboxed_files, monkeypatch):
    app = make_app_stub()

    # عنصر فاشل سابق بانتظار إعادة المحاولة
    pro.save_failed_queue([
        {"id": "OLD1", "url": "https://facebook.com/reel/OLD1", "page": "قديمة",
         "attempts": 1, "last_error": "فشل التحميل"}
    ])
    # عنصر مرفوع سابقاً — يجب ألا يعود
    pro.save_uploaded_id("DONE1")

    def fake_scrape(url, limit, log_cb=None):
        if "elwataniatvweb" in url:
            return ["https://facebook.com/reel/NEW1", "https://facebook.com/reel/DONE1"]
        return ["https://facebook.com/reel/NEW2"]
    monkeypatch.setattr(pro, "scrape_links_fast", fake_scrape)
    monkeypatch.setattr(pro, "init_driver",
                        lambda *_a, **_k: pytest.fail("لا يجب تشغيل المتصفح والسحب السريع ناجح"))
    monkeypatch.setattr(pro, "download_video",
                        lambda *_a, **_k: {"file": "v.mp4", "title": "عنوان", "desc": "وصف"})
    monkeypatch.setattr(pro, "upload_video", lambda *_a, **_k: "YT-123")
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)

    quota = pro.QuotaManager(daily_limit=10000)
    up, fail = app._run_cycle(object(), quota, PAGES, base_settings(), watch_mode=True)

    assert (up, fail) == (3, 0)                       # OLD1 + NEW1 + NEW2
    assert pro.load_failed_queue() == []              # لا شيء معلق
    assert pro.load_uploaded_ids() == {"DONE1", "NEW1", "NEW2", "OLD1"}
    assert quota.used == 3 * pro.QuotaManager.COST_UPLOAD
    # الأرشيف سجّل 3 عمليات رفع
    lines = (sandboxed_files / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert all(json.loads(l)["status"] == "uploaded" for l in lines)


def test_download_failure_requeues_then_abandons(sandboxed_files, monkeypatch):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/BAD1"])
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: None)
    monkeypatch.setattr(pro, "upload_video",
                        lambda *_a, **_k: pytest.fail("لا رفع بعد فشل التحميل"))
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)

    settings = base_settings(max_attempts=2)
    quota = pro.QuotaManager(daily_limit=10000)

    up1, fail1 = app._run_cycle(object(), quota, PAGES[:1], settings, watch_mode=True)
    assert (up1, fail1) == (0, 1)
    q = pro.load_failed_queue()
    assert len(q) == 1 and q[0]["attempts"] == 1      # بانتظار دورة ثانية

    up2, fail2 = app._run_cycle(object(), quota, PAGES[:1], settings, watch_mode=True)
    assert (up2, fail2) == (0, 1)
    assert pro.load_failed_queue() == []              # استُنفدت المحاولات
    assert "BAD1" in pro.load_seen_ids()              # تجاهل نهائي

    # دورة ثالثة: BAD1 في seen — لا يُعاد سحبه
    up3, fail3 = app._run_cycle(object(), quota, PAGES[:1], settings, watch_mode=True)
    assert (up3, fail3) == (0, 0)


def test_stop_requeues_unprocessed(sandboxed_files, monkeypatch):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/A1",
                                           "https://facebook.com/reel/A2",
                                           "https://facebook.com/reel/A3"])
    def stop_after_first(*_a, **_k):
        app._stop.set()
        return {"file": "v.mp4", "title": "t", "desc": "d"}
    monkeypatch.setattr(pro, "download_video", stop_after_first)
    monkeypatch.setattr(pro, "upload_video", lambda *_a, **_k: "YT")
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)

    quota = pro.QuotaManager(daily_limit=10000)
    up, _ = app._run_cycle(object(), quota, PAGES[:1], base_settings(), watch_mode=True)
    assert up == 1
    remaining = {it["id"] for it in pro.load_failed_queue()}
    assert remaining == {"A2", "A3"}                  # الباقي محفوظ للدورة القادمة


def test_quota_gate_defers_when_exhausted_single_run(sandboxed_files, monkeypatch):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/Q1"])
    monkeypatch.setattr(pro, "download_video",
                        lambda *_a, **_k: {"file": "v.mp4", "title": "t", "desc": "d"})
    monkeypatch.setattr(pro, "upload_video",
                        lambda *_a, **_k: pytest.fail("لا رفع والحصة مستنفدة"))
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)
    # الملف الوهمي غير موجود فعلياً — _cleanup_file يبتلع OSError بأمان

    quota = pro.QuotaManager(daily_limit=10000)
    quota.mark_exhausted()
    up, fail = app._run_cycle(object(), quota, PAGES[:1], base_settings(), watch_mode=False)
    assert (up, fail) == (0, 0)
    assert {it["id"] for it in pro.load_failed_queue()} == {"Q1"}  # مؤجل، لا ضائع


def test_publish_time_accumulates(sandboxed_files):
    t1 = pro.next_publish_time(20, 20)
    t2 = pro.next_publish_time(20, 20)
    assert t2 > t1  # المواعيد تراكمية ولا تتداخل
