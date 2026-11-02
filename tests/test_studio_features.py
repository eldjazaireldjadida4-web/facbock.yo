"""اختبارات مزايا v4.5: تحسين العنوان، بناء الوصف، تركيب الشعار بـ ffmpeg، فلاتر المدة."""
from __future__ import annotations

import configparser
import importlib.util
import os
import queue
import subprocess
import threading
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent / "fixtures" / "versions" / "fb_youtube_uploader_v46_pro.py"
spec = importlib.util.spec_from_file_location("pro45", MODULE_PATH)
assert spec and spec.loader
pro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pro)

MAIN_PATH = Path(__file__).resolve().parents[1] / "fb_youtube_uploader_v49_pro.py"
main_spec = importlib.util.spec_from_file_location("pro49", MAIN_PATH)
assert main_spec and main_spec.loader
pro49 = importlib.util.module_from_spec(main_spec)
main_spec.loader.exec_module(pro49)


@pytest.fixture()
def sandboxed_files(tmp_path, monkeypatch):
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
    s = {
        "account": "c", "headless": True, "remove_tags": False, "extra_description": "",
        "schedule_enabled": False, "min_delay": 20, "max_delay": 60, "privacy": "public",
        "tags": ["Facebook"], "delete_after_upload": False, "between_videos": 0,
        "interval_minutes": 10, "fast_scrape": True, "daily_quota": 10000,
        "max_attempts": 3, "min_disk_gb": 0, "telegram_enabled": False,
        "tg_token": "", "tg_chat_id": "",
        "title_optimize": True, "auto_shorts": True, "title_prefix": "",
        "title_suffix": "", "auto_hashtags": True, "credit_line": "",
        "logo_enabled": False, "logo_path": "logo.png", "logo_position": "أعلى اليمين",
        "logo_scale": 18, "logo_opacity": 100, "logo_cover_old": True,
        "min_duration": 0, "max_duration": 0, "category": "25",
        "tg_errors": False, "cookies_enabled": False, "cookies_file": "",
        "dedupe_content": True,
    }
    s.update(over)
    return s


PAGES = [{"url": "https://www.facebook.com/elwataniatvweb/reels/", "name": "الوطنية TV",
          "scroll": 1, "limit": 5, "enabled": True}]

# ─────────────────────────────────────────────
# 1) محسّن العناوين
# ─────────────────────────────────────────────
def test_optimize_title_removes_filler_and_junk():
    t = pro.optimize_title("شاهد الآن 🔴 هدف رائع رائع في الدوري reels http://x.co")
    assert "شاهد" not in t and "reels" not in t.lower() and "http" not in t
    assert "رائع رائع" not in t          # إزالة التكرار المتتالي
    assert "هدف" in t


def test_optimize_title_adds_shorts_for_short_videos():
    t = pro.optimize_title("لقطة ممتعة", duration=45)
    assert t.endswith("#Shorts")
    t_long = pro.optimize_title("مباراة كاملة", duration=300, auto_shorts=True)
    assert "#Shorts" not in t_long


def test_optimize_title_prefix_suffix_and_cap():
    t = pro.optimize_title("خبر مهم", prefix="الوطنية", suffix="حصري")
    assert t.startswith("الوطنية") and t.endswith("حصري")
    long_title = "كلمة " * 60
    t2 = pro.optimize_title(long_title, prefix="P", suffix="S")
    assert len(t2) <= pro.YOUTUBE_TITLE_MAX
    assert t2.startswith("P") and t2.endswith("S")


def test_optimize_title_collapses_symbol_runs():
    assert pro49.optimize_title("هدف رائع!!! عظيم") == "هدف رائع! عظيم"
    assert pro49.optimize_title("\U0001F525\U0001F525\U0001F525 لقطة نادرة") == "\U0001F525 لقطة نادرة"
    assert pro49.optimize_title("نتيجة... المباراة كاملة") == "نتيجة\u2026 المباراة كاملة"
    assert pro49.optimize_title("AAA111 واكد") == "AAA111 واكد"


def test_optimize_title_truncation_adds_ellipsis_after_body():
    long_title = " ".join(f"مقطع{i}" for i in range(40))
    t = pro49.optimize_title(long_title, prefix="بادئة طويلة للتوضيح", suffix="قناة كاملة")
    assert len(t) <= pro49.YOUTUBE_TITLE_MAX
    assert t.startswith("بادئة طويلة للتوضيح") and t.endswith("قناة كاملة")
    assert "…" in t


def test_optimize_title_empty_falls_back():
    assert pro.optimize_title("") == "Facebook Reel"
    assert pro.optimize_title("http://only-link.com") == "Facebook Reel"


# ─────────────────────────────────────────────
# 2) الوصف والهاشتاجات
# ─────────────────────────────────────────────
def test_extract_hashtags_from_arabic_title():
    line = pro.extract_hashtags("المنتخب الجزائري يفوز في مباراة مثيرة", ["Reel"])
    assert "#المنتخب" in line and "#Reel" in line
    assert "#في" not in line  # كلمات الوقف مستبعدة


def test_build_description_order_and_cap():
    d = pro.build_description("وصف أصلي", "إضافي", "عنوان تجريبي للاختبار",
                              ["Tag1"], auto_hashtags=True, credit_line="تابعونا")
    parts = d.split("\n\n")
    assert parts[0] == "وصف أصلي" and "إضافي" in parts[1]
    assert parts[-1] == "تابعونا"
    assert "#Tag1" in d


def test_build_description_without_hashtags():
    d = pro.build_description("أ", "", "عنوان", [], auto_hashtags=False, credit_line="")
    assert "#" not in d


# ─────────────────────────────────────────────
# 3) فلتر المدة
# ─────────────────────────────────────────────
def test_duration_filter():
    assert pro.duration_ok(30, 0, 0)[0]          # بلا فلاتر
    assert not pro.duration_ok(5, 10, 0)[0]      # أقصر من الأدنى
    assert not pro.duration_ok(500, 0, 120)[0]   # أطول من الأقصى
    assert pro.duration_ok(60, 10, 120)[0]


# ─────────────────────────────────────────────
# 4) بطاقة الشعار الاحترافية في النسخة المدمجة
# ─────────────────────────────────────────────
def test_overlay_filter_structure():
    fc = pro49.build_overlay_filter("أعلى اليمين", 20)
    assert "[1:v]format=rgba[badge]" in fc
    assert "overlay=x='main_w-overlay_w-20':y='20':format=auto[vout]" in fc
    fc2 = pro49.build_overlay_filter("أسفل اليسار", 10)
    assert "overlay=x='10':y='main_h-overlay_h-10'" in fc2


def test_logo_badge_has_opaque_cover_and_transparent_corners(tmp_path):
    from PIL import Image, ImageDraw

    logo_path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (120, 60), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rounded_rectangle((5, 5, 115, 55), radius=12,
                                           fill=(240, 40, 50, 255))
    logo.save(logo_path)

    badge = pro49.create_logo_badge(str(logo_path), 1280, 720,
                                    scale_pct=10, cover_old=True,
                                    cover_width_pct=20)
    assert badge.size == (256, 136)
    assert badge.getpixel((0, 0))[3] == 0
    assert badge.getpixel((badge.width // 2, 15))[3] == 255
    center = badge.getpixel((badge.width // 2, badge.height // 2))
    assert center[0] > 200 and center[1] < 80 and center[2] < 80


@pytest.mark.skipif(not pro49.ffmpeg_available(), reason="ffmpeg غير متوفر")
def test_real_ffmpeg_overlay(tmp_path):
    """تركيب فعلي لبطاقة PNG فوق فيديو تجريبي، مع الحفاظ على القياس والصوت."""
    video = tmp_path / "in.mp4"
    logo = tmp_path / "logo.png"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=2:size=480x854:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
                   capture_output=True, check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "color=c=red@0.9:size=120x60,format=rgba",
                    "-frames:v", "1", str(logo)], capture_output=True, check=True)

    out = pro49.apply_logo_overlay(str(video), str(logo), "أعلى اليمين",
                                   scale_pct=25, opacity=100, cover_old=True,
                                   landscape_cover_width_pct=20)
    assert out and os.path.isfile(out)
    w, h, duration = pro49.probe_video(out)
    assert (w, h) == (480, 854) and 1.5 < duration < 3
    raw = subprocess.run(
        ["ffmpeg", "-y", "-i", out, "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    pixel = 50 * 480 * 3 + 400 * 3
    red, green, blue = raw[pixel], raw[pixel + 1], raw[pixel + 2]
    assert red > 120 and green < 110 and blue < 110


def test_overlay_missing_logo_returns_none(tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")
    assert pro49.apply_logo_overlay(str(video), str(tmp_path / "none.png")) is None


# ─────────────────────────────────────────────
# 5) دورة كاملة: تحسين عنوان + فلتر مدة + تصنيف
# ─────────────────────────────────────────────
def test_cycle_applies_title_optimization_and_category(sandboxed_files, monkeypatch):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/T1"])
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: {
        "file": "v.mp4", "title": "شاهد الآن هدف رائع", "desc": "", "duration": 45})
    captured = {}
    def fake_upload(yt, vd, privacy, pl, tags, category, sched, log_cb):
        captured.update(title=vd["title"], category=category, desc=vd["desc"])
        return "YT-1"
    monkeypatch.setattr(pro, "upload_video", fake_upload)
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)

    quota = pro.QuotaManager(daily_limit=10000)
    ctx = {"yt": object(), "quota": quota, "account": "c"}
    up, _ = app._run_cycle(ctx, PAGES, base_settings(category="17"), True)
    assert up == 1
    assert captured["category"] == "17"                      # التصنيف المختار
    assert "شاهد" not in captured["title"]                   # الحشو أُزيل
    assert captured["title"].endswith("#Shorts")             # 45 ثانية
    assert "#Facebook" in captured["desc"]                   # هاشتاجات تلقائية


def test_cycle_skips_by_duration_and_archives(sandboxed_files, monkeypatch):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/LONG1"])
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: {
        "file": "v.mp4", "title": "فيديو طويل", "desc": "", "duration": 600})
    monkeypatch.setattr(pro, "upload_video",
                        lambda *_a, **_k: pytest.fail("لا يجب رفع فيديو خارج الفلتر"))
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)

    quota = pro.QuotaManager(daily_limit=10000)
    ctx = {"yt": object(), "quota": quota, "account": "c"}
    up, fail = app._run_cycle(ctx, PAGES, base_settings(max_duration=120), True)
    assert (up, fail) == (0, 0)                              # تخطى، لم يفشل
    assert "LONG1" in pro.load_seen_ids()                    # لن يُعاد
    hist = (sandboxed_files / "history.jsonl").read_text(encoding="utf-8")
    assert '"status": "skipped"' in hist


def test_cycle_brands_video_before_upload(sandboxed_files, monkeypatch, tmp_path):
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/B1"])
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: {
        "file": "v.mp4", "title": "فيديو", "desc": "", "duration": 30})
    monkeypatch.setattr(pro, "apply_logo_overlay",
                        lambda *a, **k: str(tmp_path / "v_branded.mp4"))
    captured = {}
    def fake_upload(yt, vd, *_a):
        captured["file"] = vd["file"]
        return "YT-2"
    monkeypatch.setattr(pro, "upload_video", fake_upload)
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)

    quota = pro.QuotaManager(daily_limit=10000)
    ctx = {"yt": object(), "quota": quota, "account": "c"}
    up, _ = app._run_cycle(ctx, PAGES, base_settings(logo_enabled=True), True)
    assert up == 1
    assert captured["file"].endswith("_branded.mp4")         # رُفعت النسخة المركّبة


# ─────────────────────────────────────────────
# 6) مزايا v4.6
# ─────────────────────────────────────────────
def test_parse_netscape_cookies(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly.facebook.com\tTRUE\t/\tTRUE\t1893456000\tc_user\t12345\n"
        ".facebook.com\tTRUE\t/\tFALSE\t1893456000\txs\tsecret-value\n"
        "bad-line\n",
        encoding="utf-8")
    cookies = pro.parse_netscape_cookies(str(f))
    assert len(cookies) == 2
    assert cookies[0]["name"] == "c_user" and cookies[0]["secure"] is True
    assert cookies[1]["value"] == "secret-value" and cookies[1]["expiry"] == 1893456000
    assert pro.parse_netscape_cookies(str(tmp_path / "missing.txt")) == []


def test_cookies_arg_gate(tmp_path):
    f = tmp_path / "c.txt"; f.write_text("x", encoding="utf-8")
    assert pro.cookies_arg({"cookies_enabled": False, "cookies_file": str(f)}) is None
    assert pro.cookies_arg({"cookies_enabled": True, "cookies_file": str(f)}) == str(f)
    assert pro.cookies_arg({"cookies_enabled": True, "cookies_file": "nope.txt"}) is None


def test_quota_per_account_namespacing(sandboxed_files):
    qa = pro.QuotaManager(10000, account="acc1")
    qb = pro.QuotaManager(10000, account="acc2")
    qa.register(); qa.register()
    assert qa.used == 3200 and qb.used == 0          # عدادان مستقلان
    assert pro.QuotaManager(10000, account="acc2").used == 0
    assert pro.QuotaManager(10000, account="acc1").used == 3200
    # التوافق القديم: بدون حساب يبقى المفتاح العام
    qg = pro.QuotaManager(10000)
    qg.register()
    assert pro.load_state()["quota_used"] == 1600


def test_content_dedup_cycle(sandboxed_files, monkeypatch):
    """نفس العنوان من الصفحتين → الثاني يُتخطى كمحتوى مكرر."""
    app = make_app_stub()
    pages2 = PAGES + [{"url": "https://www.facebook.com/ElwataniaSport/reels/",
                       "name": "سبورت", "scroll": 1, "limit": 5, "enabled": True}]
    def fake_scrape(url, *_a, **_k):
        if "elwataniatvweb" in url:
            return ["https://facebook.com/reel/DUP1"]
        return ["https://facebook.com/reel/DUP2"]   # نفس المحتوى بمعرف آخر
    monkeypatch.setattr(pro, "scrape_links_fast", fake_scrape)
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: {
        "file": "v.mp4", "title": "هدف خرافي في الدقيقة الأخيرة", "desc": "", "duration": 40})
    monkeypatch.setattr(pro, "upload_video", lambda *_a, **_k: "YT-9")
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)

    quota = pro.QuotaManager(10000)
    ctx = {"yt": object(), "quota": quota, "account": "c"}
    up, fail = app._run_cycle(ctx, pages2, base_settings(), True)
    assert (up, fail) == (1, 0)                       # واحد فقط رُفع
    assert "DUP2" in pro.load_seen_ids()              # الثاني أُرشف
    hist = (sandboxed_files / "history.jsonl").read_text(encoding="utf-8")
    assert "محتوى مكرر" in hist


def test_is_duplicate_content_threshold(sandboxed_files):
    pro.remember_content("ملخص مباراة الجزائر ومصر كاملاً")
    assert pro.is_duplicate_content("ملخص مباراة الجزائر ومصر كاملاً")
    assert pro.is_duplicate_content("ملخص مباراة الجزائر ومصر كاملا")   # حرف ناقص
    assert not pro.is_duplicate_content("خبر مختلف تماماً عن كل شيء")
    assert not pro.is_duplicate_content("قصير")                          # أقل من 10 أحرف


def test_account_rotation_on_quota(sandboxed_files, monkeypatch):
    """رفض الحصة على الحساب الأول → تحويل تلقائي للثاني ورفع ناجح."""
    app = make_app_stub()
    monkeypatch.setattr(pro, "scrape_links_fast",
                        lambda *_a, **_k: ["https://facebook.com/reel/R1"])
    monkeypatch.setattr(pro, "download_video", lambda *_a, **_k: {
        "file": "v.mp4", "title": "فيديو التدوير", "desc": "", "duration": 30})
    monkeypatch.setattr(pro, "disk_free_gb", lambda: 100.0)
    calls = []
    monkeypatch.setattr(pro, "load_accounts", lambda: ["acc1", "acc2"])
    monkeypatch.setattr(pro, "get_youtube", lambda acc, secrets_file=None: (f"yt-{acc}", None))

    def fake_upload(yt, *_a, **_k):
        calls.append(yt)
        return "QUOTA_ERROR" if yt == "yt-acc1" else "YT-ROT"
    monkeypatch.setattr(pro, "upload_video", fake_upload)

    q1 = pro.QuotaManager(10000, account="acc1")
    ctx = {"yt": "yt-acc1", "quota": q1, "account": "acc1"}
    up, _ = app._run_cycle(ctx, PAGES, base_settings(), True)
    assert calls == ["yt-acc1", "yt-acc1", "yt-acc1", "yt-acc2"] or "yt-acc2" in calls
    assert up == 1
    assert ctx["account"] == "acc2"                     # التبديل تم فعلاً
    assert q1.used == q1.daily_limit                    # الأول عُلّم كمستنفد


def test_build_cli_settings_defaults():
    s = pro.build_cli_settings()
    assert s["interval_minutes"] == 10 and s["title_optimize"] is True
    assert s["category"] == "25" and s["dedupe_content"] is True
    assert s["logo_enabled"] is False

def _cfg_from_text(text):
    cfg = configparser.ConfigParser()
    cfg.read_string(text)
    return cfg


def test_cli_remove_tags_reads_settings_section():
    """REMOVE_TAGS يُكتب في [SETTINGS] من الواجهة؛ وضع CLI يجب أن يقرأ من نفس القسم."""
    cfg = _cfg_from_text(
        "[SETTINGS]\n"
        "REMOVE_TAGS = true\n"
        "[STUDIO]\n"
        "REMOVE_TAGS = false\n"
    )
    assert pro.build_cli_settings(cfg)["remove_tags"] is True


def test_cli_remove_tags_falls_back_to_legacy_studio_section():
    cfg = _cfg_from_text(
        "[SETTINGS]\n"
        "[STUDIO]\n"
        "REMOVE_TAGS = true\n"
    )
    assert pro.build_cli_settings(cfg)["remove_tags"] is True


def test_daily_summary_sent_once(sandboxed_files, monkeypatch):
    app = make_app_stub()
    sent = []
    monkeypatch.setattr(pro, "send_telegram", lambda s, text, log_cb=None: sent.append(text))
    settings = base_settings(tg_errors=True, telegram_enabled=True)
    app._maybe_daily_summary(settings)
    app._maybe_daily_summary(settings)   # نفس اليوم — لا إرسال ثانٍ
    assert len(sent) == 1 and "الملخص اليومي" in sent[0]


def test_logo_badge_no_plate_fills_cover_area(tmp_path):
    """وضع «الشعار فوق الشعار»: بلا لوحة، والشعار يملأ منطقة التغطية."""
    from PIL import Image
    logo_path = tmp_path / "logo.png"
    logo_img = Image.new("RGBA", (600, 600), (255, 255, 255, 0))
    from PIL import ImageDraw
    ImageDraw.Draw(logo_img).ellipse((20, 20, 580, 580), fill=(255, 255, 255, 255))
    logo_img.save(logo_path)
    badge = pro49.create_logo_badge(str(logo_path), 1340, 753, scale_pct=8,
                                  opacity=100, cover_old=True,
                                  cover_width_pct=20, cover_height_pct=19,
                                  plate=False)
    assert badge.size[0] >= 200  # يملأ منطقة التغطية وليس حجم 8% الصغير
    corners = (badge.getpixel((0, 0)), badge.getpixel((badge.width - 1, 0)),
               badge.getpixel((0, badge.height - 1)),
               badge.getpixel((badge.width - 1, badge.height - 1)))
    assert all(p[3] < 40 for p in corners), "لا يجب وجود لوحة معتمة في الوضع الجديد"


def test_logo_badge_plate_still_available(tmp_path):
    """خيار اللوحة الداكنة يبقى متاحاً لمن يريده."""
    from PIL import Image
    logo_path = tmp_path / "logo.png"
    Image.new("RGBA", (600, 600), (255, 255, 255, 255)).save(logo_path)
    badge = pro49.create_logo_badge(str(logo_path), 1340, 753, scale_pct=8,
                                  opacity=100, cover_old=True,
                                  cover_width_pct=20, cover_height_pct=19,
                                  plate=True)
    center_edge = badge.getpixel((10, badge.height // 2))
    assert center_edge[3] > 200, "اللوحة المعتمة يجب أن تظهر عند تفعيل الخيار"
