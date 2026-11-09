# -*- coding: utf-8 -*-
"""اختبارات العناوين العامة: منع رفع «Video» إلى يوتيوب ومنع إغراق المراجعة."""
from __future__ import annotations

from pathlib import Path


import fb_youtube_uploader_v49_pro as app
import texttools


def test_is_generic_title_detects_facebook_placeholders():
    for value in ("Video", "video", "Facebook Live", "Facebook Reel", "Reel",
                  "مقطع فيديو", "فيديو", "", "  ", "…"):
        assert texttools.is_generic_title(value) is True, value


def test_is_generic_title_accepts_real_titles():
    for value in ("هدف رائع في المباراة", "الوطنية TV — 2026-11-05 01:23",
                  "أخبار الرياضة اليوم"):
        assert texttools.is_generic_title(value) is False, value


def _fake_ydl_factory(download_title: str, metadata_title: str):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = dict(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=True):
            if self.opts.get("skip_download"):
                return {"title": metadata_title}
            outtmpl = str(self.opts.get("outtmpl", ""))
            target = Path(outtmpl.replace("%(id)s", "abc").replace("%(ext)s", "mp4"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"v")
            return {"title": download_title, "duration": 30,
                    "requested_downloads": [{"filepath": str(target)}]}

    return FakeYDL


def test_preserve_source_title_keeps_hashtags_page_name_and_punctuation():
    source = "شاهد الفيديو الآن!!! #نهائي | الوطنية TV"
    assert texttools.preserve_source_title(source) == source


def test_download_video_preserve_mode_keeps_facebook_title_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(tmp_path / "out"))
    source = "شاهد الفيديو الآن!!! #نهائي | الوطنية TV"
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _fake_ydl_factory(source, source))
    data = app.download_video(
        "https://www.facebook.com/watch/?v=123",
        page_name="الوطنية TV",
        preserve_facebook_title=True,
    )
    assert data["title"] == source


def test_download_video_uses_real_title_when_extraction_returns_one(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(tmp_path / "out"))
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL",
                        _fake_ydl_factory("هدف رائع في المباراة", "Video"))
    data = app.download_video("https://www.facebook.com/watch/?v=123",
                              page_name="الوطنية TV")
    assert data["title"].startswith("هدف رائع")
    assert data["title_generic"] is False


def test_download_video_replaces_generic_title_with_descriptive_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(tmp_path / "out"))
    monkeypatch.setattr(app, "_facebook_watch_og_title", lambda *_a, **_k: "")
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _fake_ydl_factory("Video", "Video"))
    data = app.download_video("https://www.facebook.com/watch/?v=123",
                              page_name="الوطنية TV")
    assert data["title"].startswith("مقطع الوطنية TV — ")
    assert data["title_generic"] is True
    assert not texttools.is_generic_title(data["title"])


def test_download_video_refetch_recovers_real_title(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(tmp_path / "out"))
    monkeypatch.setattr(app, "_facebook_watch_og_title", lambda *_a, **_k: "")
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL",
                        _fake_ydl_factory("Video", "هدف رائع في المباراة"))
    data = app.download_video("https://www.facebook.com/watch/?v=123",
                              page_name="الوطنية TV")
    assert data["title"].startswith("هدف رائع")
    assert data["title_generic"] is False


def test_is_duplicate_content_ignores_generic_titles(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(app, "load_state",
                        lambda: {"title_fingerprints": ["video"]})
    assert app.is_duplicate_content("Video") is False


def test_find_channel_title_matches_ignores_generic_titles():
    channel = [{"video_id": "abc", "title": "Video", "published_at": "", "duration": 30}]
    assert app.find_channel_title_matches("Video", 30, channel) == []


def test_remember_content_does_not_store_generic_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "STATE_FILE", str(tmp_path / "state.json"))
    state = {}
    monkeypatch.setattr(app, "load_state", lambda: state)
    monkeypatch.setattr(app, "save_state", lambda _state: None)
    app.remember_content("Video")
    app.remember_content("هدف رائع في المباراة")
    expected = app.content_fingerprint("هدف رائع في المباراة")
    assert state.get("title_fingerprints") == [expected]


def test_ai_prompt_masks_generic_title_instead_of_passing_video(monkeypatch):
    captured = {}

    def fake_gemini(prompt, model, log_cb=None, max_tokens=0):
        captured["prompt"] = prompt
        return ('{"title": "هدف رائع في المباراة", "description": "وصف موجز للفيديو",'
                ' "tags": ["كرة"]}', [])

    monkeypatch.setattr(app, "gemini_generate", fake_gemini)
    meta = app.ai_generate_metadata("Video", "وصف موجز للفيديو", "الوطنية TV",
                                    30, "model-x", "احترافي متوازن")
    assert meta and meta["title"]
    assert "العنوان الأصلي: غير متوفر" in captured["prompt"]
    assert "العنوان الأصلي: Video" not in captured["prompt"]

def test_download_video_prefers_og_title_over_all_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(tmp_path / "out"))
    monkeypatch.setattr(
        app, "_facebook_watch_og_title",
        lambda *_a, **_k: "محليات 2026..الأحزاب السياسية أمام تحدي إقناع الناخبين | Elwatania TV",
    )
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _fake_ydl_factory("Video", "Video"))
    data = app.download_video("https://www.facebook.com/watch/?v=123",
                              page_name="الوطنية TV")
    assert "محليات 2026" in data["title"]
    assert data["title_generic"] is False


def test_gateway_og_titles_are_rejected_as_generic():
    for junk in ("Discover Popular Videos Facebook", "Log into Facebook",
                 "Content not found", "title preview"):
        assert texttools.is_generic_title(junk), junk
