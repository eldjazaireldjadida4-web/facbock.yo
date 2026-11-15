"""اختبارات جلب og:title من صفحة مشاهدة فيسبوك وأولويته في إعادة جلب العنوان."""
from __future__ import annotations

import html as html_module
import urllib.error

import pytest

import fb_youtube_uploader_v49_pro as app
from texttools import is_generic_title

REAL_TITLE = (
    "محليات 2026..الأحزاب السياسية أمام تحدي إقناع الناخبين للإستجابة لطلباتهم"
    " | Elwatania TV"
)
OG_PAGE = (
    '<!DOCTYPE html><html><head>'
    f'<meta property="og:title" content="{html_module.escape(REAL_TITLE, quote=True)}" />'
    '<meta property="og:description" content="وصف" />'
    "</head><body></body></html>"
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size):
        return self._payload.encode("utf-8")


def test_og_title_unescapes_entities_and_strips_whitespace(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["ua"] = request.headers.get("User-agent", "")
        return _FakeResponse(OG_PAGE)

    monkeypatch.setattr(app.urllib.request, "urlopen", fake_urlopen)
    result = app._facebook_watch_og_title("https://www.facebook.com/watch/?v=123")
    assert result == REAL_TITLE
    assert captured["url"] == "https://www.facebook.com/watch/?v=123"
    assert "facebookexternalhit" in captured["ua"]


def test_og_title_returns_empty_when_meta_missing(monkeypatch):
    monkeypatch.setattr(
        app.urllib.request, "urlopen",
        lambda _request, timeout=None: _FakeResponse("<html><head></head></html>"),
    )
    assert app._facebook_watch_og_title("https://www.facebook.com/watch/?v=123") == ""


def test_og_title_returns_empty_on_network_error(monkeypatch):
    def boom(_request, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(app.urllib.request, "urlopen", boom)
    assert app._facebook_watch_og_title("https://www.facebook.com/watch/?v=123") == ""


def test_og_title_uses_configured_proxy_and_facebook_cookies(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        ".facebook.com\tTRUE\t/\tTRUE\t4102444800\tc_user\ttest-user\n",
        encoding="utf-8",
    )
    captured = {}

    class FakeOpener:
        def open(self, request, timeout=None):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _FakeResponse(OG_PAGE)

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(app.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        app.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("must not bypass configured proxy/cookies"),
    )

    result = app._facebook_watch_og_title(
        "https://www.facebook.com/watch/?v=123",
        ydl_opts={"proxy": "http://proxy.example:8080", "cookiefile": str(cookies)},
    )

    proxy_handler = next(
        handler for handler in captured["handlers"]
        if isinstance(handler, app.urllib.request.ProxyHandler)
    )
    cookie_handler = next(
        handler for handler in captured["handlers"]
        if isinstance(handler, app.urllib.request.HTTPCookieProcessor)
    )
    assert result == REAL_TITLE
    assert proxy_handler.proxies["https"] == "http://proxy.example:8080"
    assert any(cookie.name == "c_user" and cookie.value == "test-user" for cookie in cookie_handler.cookiejar)
    assert captured["url"] == "https://www.facebook.com/watch/?v=123"


def test_refetch_passes_download_transport_options_to_og_title(monkeypatch):
    captured = {}

    def fake_title(url, timeout=20, ydl_opts=None):
        captured.update(url=url, ydl_opts=ydl_opts)
        return REAL_TITLE

    monkeypatch.setattr(app, "_facebook_watch_og_title", fake_title)
    monkeypatch.setattr(
        app.yt_dlp,
        "YoutubeDL",
        lambda *_args, **_kwargs: pytest.fail("yt-dlp must not run for a valid og:title"),
    )
    options = {"proxy": "http://proxy.example:8080", "cookiefile": "/private/cookies.txt"}

    title, generic = app._refetch_facebook_title(
        "https://www.facebook.com/watch/?v=123", ["Page"], False, True, options
    )

    assert not generic
    assert title
    assert captured == {
        "url": "https://www.facebook.com/watch/?v=123",
        "ydl_opts": options,
    }


def test_refetch_prefers_og_title_over_ytdlp(monkeypatch):
    monkeypatch.setattr(
        app, "_facebook_watch_og_title",
        lambda _url, timeout=20: "محليات 2026..الأحزاب السياسية أمام تحدي إقناع الناخبين | Elwatania TV",
    )
    def _no_ytdlp(*_args, **_kwargs):
        pytest.fail("yt-dlp must not run when og:title provides a real title")

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _no_ytdlp)
    title, generic = app._refetch_facebook_title(
        "https://www.facebook.com/watch/?v=123", ["Page"], False, True, {}, None)
    assert not generic
    assert "الأحزاب السياسية" in title


def test_refetch_falls_back_to_ytdlp_when_og_title_generic(monkeypatch):
    monkeypatch.setattr(
        app, "_facebook_watch_og_title", lambda _url, timeout=20: "Video")
    monkeypatch.setattr(
        app, "prepare_public_title",
        lambda title, source_names, remove_hashtags=False, omit_source_page_name=True:
        title,
    )

    class FakeYdl:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, _url, download=False):
            return {"title": "عنوان حقيقي من yt-dlp"}

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FakeYdl)
    title, generic = app._refetch_facebook_title(
        "https://www.facebook.com/watch/?v=123", ["Page"], False, True, {}, None)
    assert not generic
    assert title == "عنوان حقيقي من yt-dlp"


def test_refetch_falls_back_to_descriptive_title_when_everything_fails(monkeypatch):
    monkeypatch.setattr(app, "_facebook_watch_og_title", lambda _url, timeout=20: "")

    class FakeYdl:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, _url, download=False):
            return {"title": "Video"}

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FakeYdl)
    title, generic = app._refetch_facebook_title(
        "https://www.facebook.com/watch/?v=123", ["Page"], False, True, {}, None)
    assert generic
    assert title.startswith("مقطع Page —")


def test_facebook_ui_scaffolding_is_generic():
    for scaffolding in (
        "title preview", "Title", "preview", "video preview", "play video",
        "watch reel", "fullscreen", "see more", "Video", "Facebook Live",
    ):
        assert is_generic_title(scaffolding), scaffolding
    assert not is_generic_title("محليات 2026..الأحزاب السياسية أمام تحدٍ إقناع الناخبين")
