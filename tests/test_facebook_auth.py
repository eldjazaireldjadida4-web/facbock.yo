from __future__ import annotations

import facebook_auth
from facebook_auth import (
    facebook_cookie_setup_status,
    inspect_facebook_cookies,
    load_facebook_cookies,
    parse_netscape_cookies,
)


FUTURE = 4_102_444_800


def _cookie(domain, name, value, expiry=FUTURE, secure="TRUE"):
    return "\t".join((domain, "TRUE", "/", secure, str(expiry), name, value))


def test_netscape_parser_normalizes_httponly_domain_and_skips_comments(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        + _cookie(".facebook.com", "c_user", "user-id") + "\n"
        + _cookie("#HttpOnly_.facebook.com", "xs", "session-token") + "\n",
        encoding="utf-8",
    )

    cookies = parse_netscape_cookies(str(path))

    assert [cookie["domain"] for cookie in cookies] == [".facebook.com", ".facebook.com"]
    assert cookies[0]["http_only"] is False
    assert cookies[1]["http_only"] is True
    assert cookies[1]["value"] == "session-token"


def test_parser_preserves_host_only_flag_and_loader_does_not_widen_scope(tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "www.facebook.com\tFALSE\t/\tTRUE\t" + str(FUTURE) + "\thost_only\tvalue\n"
        ".facebook.com\tTRUE\t/\tTRUE\t" + str(FUTURE) + "\tdomain_scoped\tvalue\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(facebook_auth.time, "sleep", lambda _seconds: None)

    class Driver:
        def __init__(self):
            self.visited = []
            self.cookies = []

        def get(self, url):
            self.visited.append(url)

        def add_cookie(self, cookie):
            self.cookies.append(cookie)

    parsed = parse_netscape_cookies(str(path))
    driver = Driver()
    report = load_facebook_cookies(driver, str(path), now=1_000)

    assert parsed[0]["include_subdomains"] is False
    assert parsed[1]["include_subdomains"] is True
    host_cookie = next(cookie for cookie in driver.cookies if cookie["name"] == "host_only")
    scoped_cookie = next(cookie for cookie in driver.cookies if cookie["name"] == "domain_scoped")
    assert "domain" not in host_cookie
    assert scoped_cookie["domain"] == ".facebook.com"
    assert report["loaded_cookie_count"] == 2


def test_cookie_loader_sets_page_timeout_before_home_navigation(tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "\n".join((
            _cookie(".facebook.com", "c_user", "user-id"),
            _cookie("#HttpOnly_.facebook.com", "xs", "session-token"),
        )),
        encoding="utf-8",
    )
    monkeypatch.setattr(facebook_auth.time, "sleep", lambda _seconds: None)

    class Driver:
        def __init__(self):
            self.calls = []

        def set_page_load_timeout(self, timeout):
            self.calls.append(("timeout", timeout))

        def get(self, url):
            self.calls.append(("get", url))

        def add_cookie(self, _cookie):
            return None

    driver = Driver()
    load_facebook_cookies(driver, str(path), now=1_000, page_load_timeout=13)

    assert driver.calls[0] == ("timeout", 13)
    assert driver.calls[1] == ("get", facebook_auth._FACEBOOK_HOME)


def test_inspection_filters_other_domains_and_expired_cookies(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "\n".join((
            _cookie(".facebook.com", "c_user", "user-id", expiry=999),
            _cookie("#HttpOnly_.facebook.com", "xs", "session-token"),
            _cookie(".example.com", "tracking", "not-facebook"),
        )),
        encoding="utf-8",
    )

    status = inspect_facebook_cookies(str(path), now=1_000)

    assert status["facebook_cookie_count"] == 2
    assert status["active_facebook_cookie_count"] == 1
    assert status["expired_facebook_cookie_count"] == 1
    assert status["session_cookies_present"] == ["xs"]
    assert status["has_active_session_pair"] is False


def test_setup_status_reports_disabled_and_missing_file(tmp_path):
    disabled = facebook_cookie_setup_status(False, str(tmp_path / "cookies.txt"))
    missing = facebook_cookie_setup_status(True, str(tmp_path / "cookies.txt"))

    assert disabled["status"] == "disabled"
    assert "معطّل" in disabled["message"]
    assert missing["status"] == "file_missing"


def test_setup_status_reports_invalid_and_expired_files(tmp_path):
    invalid = tmp_path / "invalid.txt"
    invalid.write_text("not a Netscape cookie file", encoding="utf-8")
    expired = tmp_path / "expired.txt"
    expired.write_text(
        _cookie(".facebook.com", "c_user", "private-value", expiry=999),
        encoding="utf-8",
    )

    invalid_status = facebook_cookie_setup_status(True, str(invalid), now=1_000)
    expired_status = facebook_cookie_setup_status(True, str(expired), now=1_000)

    assert invalid_status["status"] == "invalid_file"
    assert expired_status["status"] == "expired"
    assert "private-value" not in repr(expired_status)


def test_setup_status_requires_active_session_pair(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(_cookie(".facebook.com", "c_user", "private-value"), encoding="utf-8")

    status = facebook_cookie_setup_status(True, str(path), now=1_000)

    assert status["status"] == "session_incomplete"
    assert "private-value" not in repr(status)


def test_setup_status_reports_locally_valid_pair_without_claiming_remote_validity(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        _cookie(".facebook.com", "c_user", "private-user") + "\n"
        + _cookie(".facebook.com", "xs", "private-session"),
        encoding="utf-8",
    )

    status = facebook_cookie_setup_status(True, str(path), now=1_000)

    assert status["status"] == "ready"
    assert status["active_cookie_count"] == 2
    assert "اتصال فعلي" in status["message"]
    assert "private-session" not in repr(status)


def test_loader_injects_only_active_facebook_cookies_and_preserves_httponly(tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "\n".join((
            _cookie(".facebook.com", "c_user", "user-id"),
            _cookie("#HttpOnly_.facebook.com", "xs", "session-token"),
            _cookie(".example.com", "tracking", "not-facebook"),
            _cookie(".facebook.com", "expired", "old", expiry=999),
        )),
        encoding="utf-8",
    )
    monkeypatch.setattr(facebook_auth.time, "sleep", lambda _seconds: None)

    class Driver:
        def __init__(self):
            self.visited = []
            self.cookies = []

        def get(self, url):
            self.visited.append(url)

        def add_cookie(self, cookie):
            self.cookies.append(cookie)

    driver = Driver()
    messages = []

    report = load_facebook_cookies(driver, str(path), messages.append, now=1_000)

    assert driver.visited == ["https://www.facebook.com/"]
    assert report["loaded_cookie_count"] == 2
    assert report["rejected_cookie_count"] == 0
    assert report["session_cookies_loaded"] == ["c_user", "xs"]
    assert len(driver.cookies) == 2
    xs_cookie = next(cookie for cookie in driver.cookies if cookie["name"] == "xs")
    assert xs_cookie["domain"] == ".facebook.com"
    assert xs_cookie["httpOnly"] is True
    assert all("session-token" not in message for message in messages)


def test_loader_does_not_open_browser_for_expired_cookie_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(_cookie(".facebook.com", "c_user", "old", expiry=999), encoding="utf-8")

    class Driver:
        def get(self, _url):
            raise AssertionError("an expired cookie file must not open the browser")

    report = load_facebook_cookies(Driver(), str(path), now=1_000)

    assert report["active_facebook_cookie_count"] == 0
    assert report["loaded_cookie_count"] == 0
