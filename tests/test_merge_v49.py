"""اختبارات الدمج (v4.9) — تغطي التحصينات المرحّلة إلى fb_youtube_uploader_v49_pro.py.

تشمل: تصنيف أخطاء الرفع، التراجع الأسّي، استئناف الرفع، التحقق بعد الرفع،
إخفاء اسم الصفحة المصدر، توحيد رابط الريلز، الترتيب الحتمي، وفحص/تنظيف المقطع.

شغّل: python -m pytest -q tests/test_merge_v49.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError

MODULE_PATH = Path(__file__).resolve().parents[1] / "fb_youtube_uploader_v49_pro.py"
spec = importlib.util.spec_from_file_location("merged49", MODULE_PATH)
assert spec and spec.loader
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


# ─────────────────────────── أدوات مساعدة ───────────────────────────
class FakeResp:
    def __init__(self, status: int = 404, reason: str = "Not Found") -> None:
        self.status = status
        self.reason = reason


def _http_error(status: int, reason: str = "") -> HttpError:
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode() if reason else b"{}"
    return HttpError(FakeResp(status), body)


class FakeUploadRequest:
    def __init__(self, video_id: str = "youtube-video-id") -> None:
        self._video_id = video_id

    def next_chunk(self):
        return None, {"id": self._video_id}


class FlakyYouTube:
    """يرفع خطأً في أول N محاولة ثم ينجح."""

    def __init__(self, failures) -> None:
        self.failures = list(failures)
        self.calls = 0

    def videos(self):
        return self

    def insert(self, **_kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return FakeUploadRequest()


class ListYouTube:
    def __init__(self, items) -> None:
        self._items = items

    def videos(self):
        return self

    def list(self, **_kwargs):
        return self

    def execute(self):
        return {"items": self._items}


# ─────────────────────────── تصنيف الأخطاء والتراجع ───────────────────────────
def test_classify_quota_vs_rate_limit() -> None:
    assert app.classify_upload_error(_http_error(403, "quotaExceeded")) == "quota"
    assert app.classify_upload_error(_http_error(429, "rateLimitExceeded")) == "retryable"
    assert app.classify_upload_error(_http_error(403, "userRateLimitExceeded")) == "retryable"
    assert app.classify_upload_error(_http_error(503, "backendError")) == "retryable"
    assert app.classify_upload_error(_http_error(400, "uploadLimitExceeded")) == "channel_limit"


def test_classify_auth_and_fatal() -> None:
    assert app.classify_upload_error(_http_error(403, "")) == "auth"
    assert app.classify_upload_error(_http_error(401, "")) == "fatal"
    assert app.classify_upload_error(_http_error(400, "invalidMetadata")) == "fatal"


def test_backoff_grows_with_cap() -> None:
    assert 8 <= app._backoff_seconds(1) <= 12
    assert app._backoff_seconds(2) >= 24
    assert app._backoff_seconds(10) <= 304


# ─────────────────────────── الرفع: إعادة المحاولة والاستئناف ───────────────────────────
def test_upload_retries_rate_limit_then_succeeds(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video-bytes")
    monkeypatch.setattr(app.time, "sleep", lambda *_a: None)
    yt = FlakyYouTube([_http_error(429, "rateLimitExceeded"), _http_error(503, "")])
    assert app.upload_video(yt, {"file": str(f), "title": "t", "desc": "d"}) == "youtube-video-id"
    assert yt.calls == 3


def test_upload_quota_stops_immediately(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video-bytes")
    monkeypatch.setattr(app.time, "sleep", lambda *_a: None)
    yt = FlakyYouTube([_http_error(403, "quotaExceeded")])
    assert app.upload_video(yt, {"file": str(f), "title": "t", "desc": "d"}) == "QUOTA_ERROR"
    assert yt.calls == 1


def test_upload_channel_limit_stops_without_retry(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video-bytes")
    monkeypatch.setattr(app.time, "sleep", lambda *_a: None)
    yt = FlakyYouTube([_http_error(400, "uploadLimitExceeded")])
    assert app.upload_video(yt, {"file": str(f), "title": "t", "desc": "d"}) == "CHANNEL_LIMIT_ERROR"
    assert yt.calls == 1


def test_upload_auth_error_no_retry(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video-bytes")
    monkeypatch.setattr(app.time, "sleep", lambda *_a: None)
    yt = FlakyYouTube([_http_error(403, "")])
    assert app.upload_video(yt, {"file": str(f), "title": "t", "desc": "d"}) == "UPLOAD_REJECTED"
    assert yt.calls == 1


class ResumingRequest:
    def __init__(self) -> None:
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        if self.calls == 1:
            raise _http_error(429, "rateLimitExceeded")
        return None, {"id": "vid-resumed"}


class ResumeYouTube:
    def __init__(self) -> None:
        self.request = ResumingRequest()
        self.insert_calls = 0

    def videos(self):
        return self

    def insert(self, **_kwargs):
        self.insert_calls += 1
        return self.request


def test_upload_resumes_after_transient_chunk_failure(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video-bytes")
    monkeypatch.setattr(app.time, "sleep", lambda *_a: None)
    yt = ResumeYouTube()
    assert app.upload_video(yt, {"file": str(f), "title": "t", "desc": "d"}) == "vid-resumed"
    assert yt.request.calls == 2
    assert yt.insert_calls == 1


def test_upload_missing_file_returns_none() -> None:
    assert app.upload_video(object(), {"file": "/no/such.mp4", "title": "t", "desc": "d"}) is None


def test_playlist_failure_does_not_duplicate_upload(monkeypatch, tmp_path: Path) -> None:
    """فشل إضافة قائمة التشغيل كان يعيد رفع الفيديو كاملاً — الآن لا."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video-bytes")

    class PlaylistFails:
        def insert(self, **_kwargs):
            return self

        def execute(self):
            raise HttpError(FakeResp(404), b"{}")

    class YT:
        def videos(self):
            return self

        def insert(self, **_kwargs):
            return FakeUploadRequest()

        def playlistItems(self):
            return PlaylistFails()

    result = app.upload_video(YT(), {"file": str(f), "title": "t", "desc": "d"},
                              playlist_id="PL1")
    assert result == "youtube-video-id"


# ─────────────────────────── التحقق بعد الرفع ───────────────────────────
def test_verify_upload_warns_when_public_requested_but_private() -> None:
    messages: list[str] = []
    app.verify_upload(ListYouTube([{"status": {"privacyStatus": "private"}}]),
                      "vid", "public", None, messages.append)
    assert any("private" in m or "غير مُدقّق" in m for m in messages)


def test_verify_upload_accepts_schedule() -> None:
    messages: list[str] = []
    app.verify_upload(ListYouTube([{"status": {"privacyStatus": "private",
                                               "publishAt": "2026-09-13T10:00:00Z"}}]),
                      "vid", "private",
                      datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc), messages.append)
    assert any("الجدولة مقبولة" in m for m in messages)


def test_verify_upload_flags_missing_publish_at() -> None:
    messages: list[str] = []
    app.verify_upload(ListYouTube([{"status": {"privacyStatus": "private"}}]),
                      "vid", "private",
                      datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc), messages.append)
    assert any("لم يقبل وقت الجدولة" in m for m in messages)


def test_verify_upload_soft_on_failure() -> None:
    class Broken:
        def videos(self):
            raise RuntimeError("no scope")

    assert app.verify_upload(Broken(), "vid", "public", None, None) is None


# ─────────────────────────── الإسناد الشفاف للمصدر ───────────────────────────
def test_title_preserves_source_unless_explicitly_requested() -> None:
    raw = "35K views · 576 reactions | جيجل حين تعانق الزعانف الأمواج | Elwatania TV"
    out = app.build_title(raw, ["Elwatania TV"])
    anonymized = app.build_title(raw, ["Elwatania TV"], anonymize=True)
    assert "views" not in out and "reactions" not in out
    assert "Elwatania" in out
    assert "Elwatania" not in anonymized
    assert "جيجل حين تعانق الزعانف الأمواج" in out


def test_prepare_public_title_removes_page_names_by_default() -> None:
    raw = "عنوان واضح | Elwatania TV #Shorts"
    result = app.prepare_public_title(raw, ["Elwatania TV", "الوطنية TV"])
    assert result == "عنوان واضح #Shorts"


def test_source_name_removal_preserves_embedded_words() -> None:
    title = app.prepare_public_title("Pageantry wins | Page #Shorts", ["Page"])
    assert title == "Pageantry wins #Shorts"



def test_sanitize_removes_page_name_variants_and_links() -> None:
    out = app.sanitize_public_text("ComedyClub يقدم https://fb.com/x مقطعاً", ["Comedy Club"])
    assert "comedyclub" not in out.lower()
    assert "http" not in out


def test_sanitize_keeps_hashtags_by_default() -> None:
    out = app.sanitize_public_text("رحلة & مغامرة #reel 2026")
    assert "&" in out and "#reel" in out


def test_sanitize_preserves_description_line_breaks_when_requested():
    text = "السطر الأول\nالسطر الثاني"
    assert app.sanitize_public_text(text, keep_newlines=True) == text
    assert app.sanitize_public_text(text) == "السطر الأول السطر الثاني"


def test_public_title_removes_bidirectional_override_controls():
    title = app.prepare_public_title("خبر \u202eمهم\u202c", omit_source_page_name=False)
    assert title == "خبر مهم"
    assert "\u202e" not in title and "\u202c" not in title


def test_clean_title_truncates_and_keeps_hash() -> None:
    title = app.clean_title("<b>عنوان</b>" + "ط" * 200)
    assert len(title) <= 100 and "<" not in title


def test_vocalized_arabic_hashtag_is_removed_as_a_whole():
    result = app.prepare_public_title(
        "خبر #مُهِمٌ بعد", remove_hashtags=True, omit_source_page_name=False
    )
    assert result == "خبر بعد"


def test_hashtag_extraction_keeps_arabic_diacritics_with_the_word():
    result = app.extract_hashtags("الموضوع مُهِمٌ جداً", [])
    assert "#مُهِمٌ" in result
    assert "#هِمٌ" not in result


def test_long_title_templates_preserve_title_and_shorts_marker():
    title = app.optimize_title(
        "عنوان أصلي واضح " * 12,
        prefix="بادئة طويلة " * 15,
        suffix="لاحقة طويلة " * 15,
        duration=30,
        auto_shorts=True,
    )
    assert len(title) <= app.YOUTUBE_TITLE_MAX
    assert "عنوان" in title
    assert title.endswith("#Shorts")
    assert "…" in title


def test_ai_metadata_rejects_blank_or_link_only_titles(monkeypatch):
    for invalid_title in ("", "   ", "https://example.com/video", "#Shorts", "!!!"):
        payload = json.dumps({
            "title": invalid_title, "description": "وصف", "tags": ["وسم"]
        }, ensure_ascii=False)
        monkeypatch.setattr(app, "gemini_generate", lambda *_a, _p=payload, **_k: (_p, []))
        assert app.ai_generate_metadata(
            "عنوان المصدر", "وصف المصدر", "صفحة", 30,
            "gemini-2.5-flash", "احترافي متوازن",
        ) is None


def test_upload_rejects_empty_title_before_sending_request(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    class NeverCalledYouTube:
        calls = 0

        def videos(self):
            return self

        def insert(self, **_kwargs):
            self.calls += 1
            return FakeUploadRequest()

    youtube = NeverCalledYouTube()
    result = app.upload_video(
        youtube, {"file": str(video), "title": "https://example.com", "desc": "d"}
    )
    assert result == "INVALID_TITLE"
    assert youtube.calls == 0


def test_upload_enforces_title_and_total_tag_limits(monkeypatch, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    captured = {}

    class CaptureYouTube:
        def videos(self):
            return self

        def insert(self, **kwargs):
            captured.update(kwargs)
            return FakeUploadRequest()

    monkeypatch.setattr(app, "MediaFileUpload", lambda *_args, **_kwargs: object())
    raw_title = "عنوان " * 30
    tags = ["A" * 490, "Foo Bar", "Over budget"]
    result = app.upload_video(
        CaptureYouTube(),
        {"file": str(video), "title": raw_title, "desc": "وصف"},
        tags=tags,
    )
    snippet = captured["body"]["snippet"]
    assert result == "youtube-video-id"
    assert len(snippet["title"]) <= app.YOUTUBE_TITLE_MAX
    assert snippet["tags"] == ["A" * 490, "Foo Bar"]
    assert sum(app._youtube_tag_cost(tag) for tag in snippet["tags"]) + 1 == app.YOUTUBE_TAGS_MAX


# ─────────────────────────── الروابط والترتيب ───────────────────────────
def test_normalize_channel_url() -> None:
    assert app.normalize_channel_url("https://www.facebook.com/jireel") == \
        "https://www.facebook.com/jireel/reels"
    assert "sk=reels_tab" in app.normalize_channel_url(
        "https://www.facebook.com/profile.php?id=61554746552594")
    assert app.normalize_channel_url("https://www.facebook.com/x/reels") == \
        "https://www.facebook.com/x/reels"
    with pytest.raises(ValueError):
        app.normalize_channel_url("https://www.youtube.com/x")


def test_safe_normalize_never_raises() -> None:
    assert app._safe_normalize_channel_url("https://youtube.com/x") == "https://youtube.com/x"


def test_order_reels_dedupes_newest_first() -> None:
    links = ["https://www.facebook.com/reel/100", "https://www.facebook.com/reel/300",
             "https://www.facebook.com/reel/100", "https://www.facebook.com/reel/200"]
    assert app.order_reels(links, True) == [
        "https://www.facebook.com/watch/?v=300",
        "https://www.facebook.com/watch/?v=200",
        "https://www.facebook.com/watch/?v=100",
    ]


def test_sanitize_account_name_blocks_traversal() -> None:
    assert app.sanitize_account_name("../../etc/passwd") == "etc_passwd"
    assert app.sanitize_account_name("") == "account1"


# ─────────────────────────── فحص وتنظيف المقطع ───────────────────────────
def test_validate_media_rejects_missing_file(tmp_path: Path) -> None:
    ok, info = app.validate_media(str(tmp_path / "none.mp4"))
    assert ok is False
    assert info.get("error") == "missing-file"


def test_validate_media_is_lenient_when_probe_fails(tmp_path: Path) -> None:
    """ملف ليس فيديو حقيقياً: ffprobe يفشل → نعتبره مقبولاً (لا نرفض بسبب أداة الفحص)."""
    fake = tmp_path / "not-a-video.mp4"
    fake.write_bytes(b"not a video")
    ok, _info = app.validate_media(str(fake))
    assert ok is True


def test_validate_media_without_ffprobe_is_lenient(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "x.mp4"
    fake.write_bytes(b"x")
    monkeypatch.setattr(app.shutil, "which", lambda _name: None)
    ok, _info = app.validate_media(str(fake))
    assert ok is True


def test_finalize_video_without_ffmpeg_returns_original(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"video")
    monkeypatch.setattr(app.shutil, "which", lambda _name: None)
    assert app.finalize_video(str(f)) == str(f)


def test_finalize_video_success_returns_cleaned(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"video")
    out = tmp_path / "v.clean.mp4"

    monkeypatch.setattr(app.shutil, "which", lambda _name: "/fake/ffmpeg")

    def fake_run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"cleaned")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(app.subprocess, "run", fake_run)
    assert app.finalize_video(str(f)) == str(out)


def test_finalize_video_failure_returns_original(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"video")
    monkeypatch.setattr(app.shutil, "which", lambda _name: "/fake/ffmpeg")

    def fake_run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"partial")
        return SimpleNamespace(returncode=1, stderr="boom")

    monkeypatch.setattr(app.subprocess, "run", fake_run)
    assert app.finalize_video(str(f)) == str(f)
    assert not (tmp_path / "v.clean.mp4").exists()
    assert not list(tmp_path.glob(".v.mp4.*.mp4"))


def test_finalize_video_timeout_removes_partial_temp_file(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"video")
    monkeypatch.setattr(app.shutil, "which", lambda _name: "/fake/ffmpeg")

    def timeout_run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, 900)

    monkeypatch.setattr(app.subprocess, "run", timeout_run)
    assert app.finalize_video(str(f)) == str(f)
    assert not (tmp_path / "v.clean.mp4").exists()
    assert not list(tmp_path.glob(".v.mp4.*.mp4"))


# ─────────────────────────── التكامل: تنزيل يحجب اسم الصفحة ───────────────────────────
def test_download_hides_page_name_in_title_but_keeps_source_attribution(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "123.mp4"
    video.write_bytes(b"video")

    class FakeYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def extract_info(self, _url, download=True):
            return {
                "id": "123",
                "title": "12K views | عنوان المقطع | Elwatania TV",
                "description": "وصف من Elwatania TV مع رابط https://fb.com/x",
                "uploader": "Elwatania TV",
                "duration": 30,
                "filepath": str(video),
            }

        def prepare_filename(self, _info):
            return str(video)

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FakeYDL)
    url = "https://facebook.com/reel/123"
    data = app.download_video(url, "الوطنية TV")
    assert data is not None
    assert "Elwatania" not in data["title"]
    assert "الوطنية" not in data["title"]
    assert "views" not in data["title"]
    assert "Elwatania" in data["desc"]
    assert "المصدر الأصلي: الوطنية TV" in data["desc"]
    assert f"رابط المقطع الأصلي: {url}" in data["desc"]
    assert data["source_page"] == "الوطنية TV"
    assert data["source_url"] == url
    assert "https://fb.com/x" not in data["desc"]

    visible_source = app.download_video(url, "الوطنية TV", omit_source_page_name=False)
    assert visible_source is not None
    assert "Elwatania" in visible_source["title"]


# ─────────────────────────────────────────────
# ميزات v4.10: صور مصغرة، قص Shorts، قوائم لكل صفحة، تغذية راجعة
# ─────────────────────────────────────────────


def test_wrap_text_caps_at_three_lines():
    lines = app._wrap_text(" ".join(f"كلمة{i}" for i in range(40)), 28)
    assert len(lines) == 3
    assert all(len(line) <= 28 + 10 for line in lines)


def test_wrap_text_empty():
    assert app._wrap_text("", 28) == []


def test_thumbnail_title_cleans_hashtags_and_urls():
    title = app._clean_thumbnail_title("الجزائر 2026 #Shorts https://example.com")
    assert title == "الجزائر 2026"


def test_thumbnail_title_shapes_arabic_rtl():
    text = "هذا عنوان عربي واضح"
    display, rtl = app._thumbnail_display_text(text)
    assert rtl is True
    if app._thumbnail_uses_raqm():
        assert display == text
    else:
        assert display != text


def test_thumbnail_wrap_respects_pixel_width():
    font = app._load_caption_font(48)
    lines = app._wrap_text(
        "الرئيس تبون ونمو الاقتصاد الجزائري قبل نهاية عام 2028",
        520,
        font=font,
        max_lines=2,
    )
    assert lines and len(lines) <= 2
    assert all(app._thumbnail_text_width(line, font) <= 520 for line in lines)


def test_thumbnail_overlay_keeps_frame_size():
    source = app.Image.new("RGB", (1280, 720), (240, 240, 240))
    result = app._draw_thumbnail_title(source, "عنوان عربي مهني")
    assert result.size == (1280, 720)
    assert result.mode == "RGBA"
    assert result.getpixel((10, 700)) != source.convert("RGBA").getpixel((10, 700))


@pytest.mark.skipif(not app.ffmpeg_available(), reason="ffmpeg غير متوفر")
def test_generate_thumbnail_real_ffmpeg_shapes_arabic(tmp_path):
    video = tmp_path / "synthetic.mp4"
    thumb = tmp_path / "thumbnail.png"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=#5070a0:s=640x360:d=2", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(video)],
        capture_output=True,
        check=True,
    )
    result = app.generate_thumbnail(
        str(video), "اختبار عنوان عربي احترافي #Shorts", str(thumb)
    )
    assert result == str(thumb)
    with app.Image.open(thumb) as image:
        assert image.size == (1280, 720)
        assert image.mode == "RGB"


def test_find_hot_segment_picks_loudest_window():
    rms = {s: -40.0 for s in range(0, 120)}
    for s in range(60, 70):
        rms[s] = -10.0
    start, span = app.find_hot_segment(rms, clip_seconds=58, min_source=95,
                                       source_duration=120)
    assert span == 58
    assert 12 <= start <= 70  # النافذة تحتوي الذروة عند 60-70


def test_find_hot_segment_rejects_short_source():
    assert app.find_hot_segment({0: -20.0, 1: -20.0}, source_duration=40) is None


def test_find_hot_segment_empty_rms():
    assert app.find_hot_segment({}, source_duration=200) is None


def test_resolve_playlist_id_page_overrides_default():
    pages = [{"name": "الوطنية", "playlist": "PL_page"}]
    settings = {"playlist_id": "PL_default"}
    assert app.resolve_playlist_id({"page": "الوطنية"}, pages, settings) == "PL_page"
    assert app.resolve_playlist_id({"page": "أخرى"}, pages, settings) == "PL_default"
    assert app.resolve_playlist_id({"page": "أخرى"}, pages, {"playlist_id": ""}) is None


def test_top_performing_titles_sorts_by_views(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    records = [
        {"youtube_id": "a", "title": "ضعيف", "status": "uploaded"},
        {"youtube_id": "b", "title": "قوي", "status": "uploaded"},
        {"youtube_id": "c", "title": "بدون إحصاء", "status": "uploaded"},
    ]
    with open(tmp_path / "history.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    monkeypatch.setattr(app, "STATE_FILE", str(tmp_path / "state.json"))
    app.save_state({"video_stats": {
        "a": {"views": 50, "likes": 0, "fetched_at": ""},
        "b": {"views": 9000, "likes": 0, "fetched_at": ""},
    }})
    titles = app.top_performing_titles(3, min_views=100)
    assert titles == ["قوي (9000 مشاهدة)"]


class FakeThumbsSet:
    def __init__(self, calls):
        self._calls = calls

    def set(self, **_kw):
        self._calls.append(_kw)
        return self

    def execute(self):
        return {"id": "x"}


def test_upload_thumbnail_uses_media_body(tmp_path):
    calls = []
    yt = SimpleNamespace(thumbnails=lambda: FakeThumbsSet(calls))
    path = tmp_path / "thumb.png"
    path.write_bytes(b"png")
    assert app.upload_thumbnail(yt, "vid123", str(path)) is True
    assert calls and calls[0]["videoId"] == "vid123"


def test_upload_thumbnail_missing_file(tmp_path, capsys):
    assert app.upload_thumbnail(SimpleNamespace(), "vid", str(tmp_path / "no.png")) is False


class FakeVideosStats:
    def __init__(self, resp):
        self._resp = resp

    def list(self, **_kw):
        return self

    def execute(self):
        return self._resp


def test_collect_video_stats_saves_to_state(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    with open(tmp_path / "history.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"youtube_id": "vidA", "title": "t", "status": "uploaded"},
                           ensure_ascii=False) + "\n")
        f.write("سطر تالف\n")
    monkeypatch.setattr(app, "STATE_FILE", str(tmp_path / "state.json"))
    app.save_state({})
    resp = {"items": [{"id": "vidA", "statistics": {"viewCount": "77", "likeCount": "3"}}]}
    yt = SimpleNamespace(videos=lambda: FakeVideosStats(resp))
    stats = app.collect_video_stats(yt)
    assert stats["vidA"]["views"] == 77
    state = app.load_state()
    assert state["video_stats"]["vidA"]["likes"] == 3


@pytest.mark.skipif(not app.ffmpeg_available(), reason="ffmpeg غير متوفر")
def test_generate_thumbnail_real_ffmpeg(tmp_path):
    video = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    out = app.generate_thumbnail(str(video), "عنوان تجريبي للصورة المصغرة")
    assert out and os.path.isfile(out)
    from PIL import Image
    with Image.open(out) as img:
        assert img.size == (app.THUMB_WIDTH, app.THUMB_HEIGHT)


@pytest.mark.skipif(not app.ffmpeg_available(), reason="ffmpeg غير متوفر")
def test_cut_shorts_clip_real_ffmpeg(tmp_path):
    video = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=red:s=320x180:d=3",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    out = app.cut_shorts_clip(str(video), 0, 2)
    assert out and os.path.isfile(out)
    w, h, duration = app.probe_video(out)
    assert w > 0 and 1.5 <= duration <= 2.6


def test_generate_thumbnail_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ffmpeg_available", lambda: False)
    assert app.generate_thumbnail(str(tmp_path / "x.mp4"), "عنوان") is None
