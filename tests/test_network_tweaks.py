# -*- coding: utf-8 -*-
"""اختبارات مهلات Facebook ومحاولات الشبكة والوكيل."""
from __future__ import annotations

import configparser
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fb_youtube_uploader_v49_pro as app


def _reload_app(monkeypatch, watch: dict):
    cfg = configparser.ConfigParser()
    cfg.add_section("WATCH")
    for key, value in watch.items():
        cfg.set("WATCH", key, value)
    monkeypatch.setattr(app, "config", cfg)


def test_legacy_network_settings_are_capped(monkeypatch):
    _reload_app(monkeypatch, {"SOCKET_TIMEOUT": "60", "RETRIES": "10", "PROXY": "", "FORCE_IPV4": "false"})
    opts = app._network_tweaks(None)
    assert opts["socket_timeout"] == 30
    assert opts["retries"] == 3
    assert "proxy" not in opts
    assert "source_address" not in opts


def test_proxy_and_ipv4(monkeypatch):
    _reload_app(monkeypatch, {
        "SOCKET_TIMEOUT": "30", "RETRIES": "5",
        "PROXY": "socks5://127.0.0.1:1080", "FORCE_IPV4": "true",
    })
    opts = app._network_tweaks(None)
    assert opts["proxy"] == "socks5://127.0.0.1:1080"
    assert opts["source_address"] == "0.0.0.0"
    assert opts["socket_timeout"] == 30
    assert opts["retries"] == 3


def test_defaults_fall_back_with_global_timeout_ceiling(monkeypatch):
    _reload_app(monkeypatch, {"PROXY": "", "FORCE_IPV4": "false"})
    opts = app._network_tweaks(None, socket_timeout=45, retries=3)
    assert opts["socket_timeout"] == 30
    assert opts["retries"] == 3


def test_scrape_settings_have_a_shorter_hard_ceiling(monkeypatch):
    _reload_app(monkeypatch, {"SCRAPE_SOCKET_TIMEOUT": "60", "SCRAPE_RETRIES": "10"})
    opts = app._network_tweaks(
        None,
        socket_timeout=15,
        retries=2,
        timeout_key="SCRAPE_SOCKET_TIMEOUT",
        retries_key="SCRAPE_RETRIES",
    )
    assert opts["socket_timeout"] == 20
    assert opts["retries"] == 3


def test_cookies_only_if_file_exists(monkeypatch, tmp_path):
    _reload_app(monkeypatch, {"PROXY": "", "FORCE_IPV4": "false"})
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert app._network_tweaks(str(cookie))["cookiefile"] == str(cookie)
    assert "cookiefile" not in app._network_tweaks(str(tmp_path / "missing.txt"))
    assert "cookiefile" not in app._network_tweaks(None)


def test_fast_scrape_raises_network_failure_without_triggering_browser_fallback(monkeypatch):
    _reload_app(monkeypatch, {"PROXY": "", "FORCE_IPV4": "false"})

    class FakeYoutubeDL:
        def __init__(self, _options):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            raise TimeoutError("Connection to www.facebook.com timed out")

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    with pytest.raises(app.FacebookNetworkUnavailable):
        app.scrape_links_fast("https://www.facebook.com/watch/?v=123456", 10)


def test_fast_scrape_treats_empty_extraction_as_inconclusive(monkeypatch):
    _reload_app(monkeypatch, {"PROXY": "", "FORCE_IPV4": "false"})

    class FakeYoutubeDL:
        def __init__(self, _options):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {"entries": []}

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    messages = []
    assert app.scrape_links_fast(
        "https://www.facebook.com/watch/?v=123456", 10, messages.append
    ) is None
    assert any("المتصفح" in message for message in messages)


def test_empty_fast_scrape_with_cookies_falls_back_to_browser_auth_check(monkeypatch, tmp_path):
    _reload_app(monkeypatch, {"PROXY": "", "FORCE_IPV4": "false"})
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("session", encoding="utf-8")

    class FakeYoutubeDL:
        def __init__(self, _options):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {"entries": []}

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    assert app.scrape_links_fast(
        "https://www.facebook.com/watch/?v=123456", 10, cookies_file=str(cookies)
    ) is None


def test_browser_scrape_caps_legacy_page_timeout_and_exits_fast(monkeypatch):
    cfg = configparser.ConfigParser()
    cfg.add_section("WATCH")
    cfg.set("WATCH", "PAGE_LOAD_TIMEOUT", "90")
    monkeypatch.setattr(app, "config", cfg)
    page_timeouts = []
    wait_timeouts = []
    sleep_delays = []

    class FakeDriver:
        current_url = "https://www.facebook.com/page/reels"
        page_source = ""

        def set_page_load_timeout(self, timeout):
            page_timeouts.append(timeout)

        def get(self, _url):
            raise app.TimeoutException("page load timed out")

        def execute_script(self, _script):
            return None

        def find_elements(self, *_args):
            return []

    class FakeWait:
        def __init__(self, _driver, timeout):
            wait_timeouts.append(timeout)

        def until(self, _condition):
            raise app.TimeoutException("anchor wait timed out")

    monkeypatch.setattr(app, "WebDriverWait", FakeWait)
    monkeypatch.setattr(app.time, "sleep", sleep_delays.append)

    with pytest.raises(app.FacebookNetworkUnavailable):
        app.get_reels(FakeDriver(), "https://www.facebook.com/page/reels", 8, 20)

    assert page_timeouts == [30]
    assert wait_timeouts == [2]
    assert sleep_delays == [1, 1]


def test_browser_proxy_is_normalized_without_credentials():
    assert app._selenium_proxy_server("socks5h://127.0.0.1:9050") == "socks5://127.0.0.1:9050"
    assert app._selenium_proxy_server("https://proxy.example") == "https://proxy.example:443"


def test_browser_proxy_rejects_credentials_without_echoing_them():
    try:
        app._selenium_proxy_server("http://private:secret@proxy.example:8080")
    except ValueError as error:
        assert "private" not in str(error)
        assert "secret" not in str(error)
    else:
        raise AssertionError("credential-bearing browser proxy should be rejected")


def test_browser_receives_the_configured_proxy(monkeypatch):
    _reload_app(monkeypatch, {"PROXY": "socks5h://127.0.0.1:9050", "FORCE_IPV4": "false"})
    arguments = []

    class FakeOptions:
        def add_argument(self, value):
            arguments.append(value)

        def add_experimental_option(self, *_args):
            return None

    class FakeDriver:
        def execute_cdp_cmd(self, *_args):
            return None

    monkeypatch.setattr(app, "Options", FakeOptions)
    monkeypatch.setattr(app.webdriver, "Chrome", lambda **_kwargs: FakeDriver())

    assert app.init_driver(headless=False) is not None
    assert "--proxy-server=socks5://127.0.0.1:9050" in arguments


def test_browser_initialization_applies_the_configured_proxy(monkeypatch):
    _reload_app(monkeypatch, {"PROXY": "socks5h://127.0.0.1:9050", "FORCE_IPV4": "true"})
    captured = {}

    class FakeOptions:
        def __init__(self):
            self.arguments = []

        def add_argument(self, argument):
            self.arguments.append(argument)

        def add_experimental_option(self, *_args):
            return None

    class FakeDriver:
        def execute_cdp_cmd(self, *_args):
            return None

    def make_driver(options):
        captured["options"] = options
        return FakeDriver()

    monkeypatch.setattr(app, "Options", FakeOptions)
    monkeypatch.setattr(app.webdriver, "Chrome", make_driver)

    assert app.init_driver(headless=False) is not None
    assert "--proxy-server=socks5://127.0.0.1:9050" in captured["options"].arguments


def test_preflight_uses_ipv6_fallback_and_does_not_bind_downloads_to_ipv4(monkeypatch):
    _reload_app(monkeypatch, {"PROXY": "", "FORCE_IPV4": "true"})
    monkeypatch.setattr(app, "_FACEBOOK_NETWORK_ROUTE", None)
    monkeypatch.setattr(app, "facebook_network_route", lambda *_args, **_kwargs: "ipv6")
    logs = []

    assert app._facebook_network_preflight(logs.append) is True
    assert app._FACEBOOK_NETWORK_ROUTE == "ipv6"
    assert any("IPv6" in message for message in logs)
    assert "source_address" not in app._network_tweaks(None)


def test_failed_preflight_reports_both_address_families(monkeypatch):
    _reload_app(monkeypatch, {"PROXY": "", "FORCE_IPV4": "true"})
    monkeypatch.setattr(app, "_FACEBOOK_NETWORK_ROUTE", None)
    monkeypatch.setattr(app, "facebook_network_route", lambda *_args, **_kwargs: None)
    logs = []

    assert app._facebook_network_preflight(logs.append) is False
    assert any("IPv4 وIPv6" in message for message in logs)
