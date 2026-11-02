from __future__ import annotations

from selenium.common.exceptions import TimeoutException, WebDriverException

from facebook_preflight import probe_facebook_page_access


FUTURE = 4_102_444_800


def _cookies_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".facebook.com\tTRUE\t/\tTRUE\t{FUTURE}\tc_user\tprivate-user\n"
        f"#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t{FUTURE}\txs\tprivate-session\n",
        encoding="utf-8",
    )
    return path


class FakeDriver:
    def __init__(self, current_url="https://www.facebook.com/page/reels/", page_source="", error=None):
        self.current_url = current_url
        self.page_source = page_source
        self.error = error
        self.visited = []
        self.quit_called = False
        self.stopped = False
        self.timeout = None

    def set_page_load_timeout(self, timeout):
        self.timeout = timeout

    def get(self, url):
        self.visited.append(url)
        if self.error:
            raise self.error

    def execute_script(self, script):
        self.stopped = script == "window.stop();"

    def quit(self):
        self.quit_called = True


def _probe(driver, *, url="https://www.facebook.com/page", cookies_enabled=False, cookies_file="", route="ipv4", loader=None):
    return probe_facebook_page_access(
        url,
        cookies_enabled,
        str(cookies_file),
        network_probe=lambda: route,
        driver_factory=lambda: driver,
        cookie_loader=loader or (lambda _driver, _path: {"session_cookies_loaded": ["c_user", "xs"]}),
    )


def test_rejects_non_facebook_urls_before_network_or_browser_are_touched():
    calls = []
    result = probe_facebook_page_access(
        "https://facebook.com.evil.example/page",
        False,
        "",
        network_probe=lambda: calls.append("network"),
        driver_factory=lambda: calls.append("browser"),
        cookie_loader=lambda *_args: {},
    )

    assert result["status"] == "invalid_url"
    assert calls == []


def test_normalizes_facebook_page_to_https_and_does_not_claim_more_than_page_access():
    driver = FakeDriver()
    result = _probe(driver, url="http://m.facebook.com/page")

    assert result["status"] == "page_accessible"
    assert result["network_route"] == "ipv4"
    assert driver.visited == ["https://www.facebook.com/page"]
    assert "لا يضمن" in result["message"]
    assert driver.quit_called


def test_cookie_file_must_have_a_live_session_pair_before_browser_is_started(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        f".facebook.com\tTRUE\t/\tTRUE\t{FUTURE}\tc_user\tprivate-user\n",
        encoding="utf-8",
    )
    calls = []
    result = probe_facebook_page_access(
        "https://www.facebook.com/page",
        True,
        str(path),
        network_probe=lambda: calls.append("network"),
        driver_factory=lambda: calls.append("browser"),
        cookie_loader=lambda *_args: {},
    )

    assert result["status"] == "cookies_unready"
    assert calls == []
    assert "private-user" not in repr(result)


def test_network_failure_is_distinguished_and_does_not_start_chrome():
    calls = []
    result = probe_facebook_page_access(
        "https://www.facebook.com/page",
        False,
        "",
        network_probe=lambda: None,
        driver_factory=lambda: calls.append("browser"),
        cookie_loader=lambda *_args: {},
    )

    assert result["status"] == "network_unavailable"
    assert calls == []


def test_login_redirect_is_reported_and_driver_is_closed():
    driver = FakeDriver(current_url="https://www.facebook.com/login/?next=/page")
    result = _probe(driver)

    assert result["status"] == "login_required"
    assert "تسجيل الدخول" in result["message"]
    assert driver.quit_called


def test_security_checkpoint_is_distinguished_from_a_login_redirect():
    driver = FakeDriver(current_url="https://www.facebook.com/checkpoint/")
    result = _probe(driver)

    assert result["status"] == "security_checkpoint"
    assert "لا يتجاوز" in result["message"]
    assert driver.quit_called


def test_enabled_cookie_test_requires_both_session_cookies_to_load(tmp_path):
    path = _cookies_file(tmp_path)
    driver = FakeDriver()
    result = _probe(
        driver,
        cookies_enabled=True,
        cookies_file=path,
        loader=lambda *_args: {"session_cookies_loaded": ["c_user"]},
    )

    assert result["status"] == "cookies_not_loaded"
    assert driver.visited == []
    assert driver.quit_called


def test_timeout_still_checks_for_authentication_redirect_and_closes_driver():
    driver = FakeDriver(current_url="https://www.facebook.com/login/", error=TimeoutException("load timeout"))
    result = _probe(driver)

    assert result["status"] == "login_required"
    assert driver.stopped
    assert driver.quit_called


def test_timeout_without_auth_signal_is_not_misreported_as_success():
    driver = FakeDriver(error=TimeoutException("load timeout"))
    result = _probe(driver)

    assert result["status"] == "page_timeout"
    assert driver.quit_called


def test_transient_browser_network_error_is_reported_as_network_failure():
    driver = FakeDriver(error=WebDriverException("net::ERR_CONNECTION_RESET"))
    result = _probe(driver)

    assert result["status"] == "network_unavailable"
    assert driver.quit_called


def test_cookie_values_are_never_included_in_preflight_result(tmp_path):
    path = _cookies_file(tmp_path)
    driver = FakeDriver(current_url="https://www.facebook.com/login/")
    result = _probe(driver, cookies_enabled=True, cookies_file=path)

    assert result["status"] == "login_required"
    assert "private-user" not in repr(result)
    assert "private-session" not in repr(result)


def test_invalid_browser_factory_is_reported_without_exception():
    result = probe_facebook_page_access(
        "https://www.facebook.com/page",
        False,
        "",
        network_probe=lambda: "ipv6",
        driver_factory=lambda: None,
        cookie_loader=lambda *_args: {},
    )

    assert result["status"] == "browser_unavailable"
    assert result["network_route"] == "ipv6"
