# -*- coding: utf-8 -*-
"""اختبارات طبقات إنقاذ العنوان الحقيقي: التسمية التوضيحية، الجلسة المسجلة، وبطاقة السحب."""
from __future__ import annotations

from pathlib import Path

import fb_youtube_uploader_v49_pro as app
import texttools


def _fake_ydl_factory(download_info: dict, metadata_title: str = "Video"):
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
            info = dict(download_info)
            info.setdefault("duration", 30)
            info.setdefault("requested_downloads", [{"filepath": str(target)}])
            return info

    return FakeYDL


def test_pick_caption_line_prefers_longest_meaningful_line():
    card = (
        "الوطنية TV\n"
        "قبل ساعتين ·\n"
        "هدف رائع في المباراة النهائية بين المنتخبين\n"
        "٣٢ ألف\n"
        "التعليقات ٤٥\n"
    )
    assert texttools.pick_caption_line(card) == "هدف رائع في المباراة النهائية بين المنتخبين"


def test_pick_caption_line_rejects_generic_and_short_lines():
    assert texttools.pick_caption_line("Video\n٣٢ ألف\nتعليق") == ""
    assert texttools.pick_caption_line("") == ""
    assert texttools.pick_caption_line(None) == ""


def test_download_video_uses_caption_from_description(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(tmp_path / "out"))
    monkeypatch.setattr(app, "_facebook_watch_og_title", lambda *_a, **_k: "")
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _fake_ydl_factory(
        {"title": "Facebook Live",
         "description": "هدف رائع في المباراة النهائية بين المنتخبين\n#كرة"},
    ))
    fetcher_calls = []
    data = app.download_video(
        "https://www.facebook.com/watch/?v=123", page_name="الوطنية TV",
        page_title_fetcher=lambda url: fetcher_calls.append(url) or "غير مستدعى",
    )
    assert data["title"].startswith("هدف رائع في المباراة")
    assert data["title_generic"] is False
    assert fetcher_calls == []  # لم تُستدعَ الجلسة المسجلة — التسمية كافية


def test_download_video_rescue_via_logged_session(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(tmp_path / "out"))
    monkeypatch.setattr(app, "_facebook_watch_og_title", lambda *_a, **_k: "")
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _fake_ydl_factory(
        {"title": "Facebook Live", "description": ""},
    ))
    data = app.download_video(
        "https://www.facebook.com/watch/?v=123", page_name="الوطنية TV",
        page_title_fetcher=lambda url: "هدف رائع في المباراة النهائية",
    )
    assert data["title"].startswith("هدف رائع في المباراة")
    assert data["title_generic"] is False


def test_rescue_title_via_driver_reads_dom_and_goes_back():
    visited = []

    class FakeDriver:
        title = "هدف رائع في المباراة النهائية | Facebook"

        def get(self, url):
            visited.append(url)

        def execute_script(self, script):
            if "og:title" in script:
                return "Facebook Live"
            return "الوطنية TV\n٣٢ ألف مشاهدة\nهدف رائع في المباراة النهائية بين المنتخبين"

    result = app.App._rescue_title_via_driver(object.__new__(app.App),
                                                      FakeDriver(),
                                                      "https://www.facebook.com/watch/?v=123")
    assert result == "هدف رائع في المباراة النهائية"
    assert visited[-1] == "about:blank"


def test_rescue_title_via_driver_swallows_errors_and_returns_empty():
    class BrokenDriver:
        def get(self, _url):
            raise RuntimeError("driver crashed")

    result = app.App._rescue_title_via_driver(object.__new__(app.App),
                                                      BrokenDriver(),
                                                      "https://www.facebook.com/watch/?v=123")
    assert result == ""
    assert app.App._rescue_title_via_driver(object.__new__(app.App),
                                                    None, "https://x") == ""


def test_safe_quit_driver_tolerates_none_and_failures():
    app.App._safe_quit_driver(None)

    class AngryDriver:
        def quit(self):
            raise RuntimeError("already closed")

    app.App._safe_quit_driver(AngryDriver())


def test_extract_article_hint_climbs_to_article():
    class FakeArticle:
        text = ("الوطنية TV\n"
                "قبل ٣ ساعات ·\n"
                "تغطية شاملة لمنتخبنا في التصفيات القارية هذا المساء\n"
                "١٢ ألف إعجاب\n")

    class FakeElement:
        def find_element(self, *_args, **_kwargs):
            return FakeArticle()

    hint = app._extract_article_hint(object(), FakeElement())
    assert hint == "تغطية شاملة لمنتخبنا في التصفيات القارية هذا المساء"


def test_extract_article_hint_returns_empty_without_article():
    class FakeElement:
        def find_element(self, *_args, **_kwargs):
            raise app.WebDriverException("no ancestor")

    assert app._extract_article_hint(object(), FakeElement()) == ""
