from __future__ import annotations

import facebook_scraper
from facebook_scraper import (
    canonicalize_facebook_video_url,
    facebook_auth_block_reason,
    facebook_login_required,
    facebook_media_id,
    facebook_network_reachable,
    facebook_network_route,
    facebook_network_route_detailed,
)


class _FakeDohResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_reel_and_video_paths_become_ytdlp_supported_watch_urls():
    for path in ("reel", "reels", "video", "videos"):
        url = f"https://www.facebook.com/channel/{path}/1760103355/?mibextid=tracking"
        assert canonicalize_facebook_video_url(url) == (
            "https://www.facebook.com/watch/?v=1760103355"
        )


def test_watch_url_drops_tracking_parameters():
    assert canonicalize_facebook_video_url(
        "https://m.facebook.com/watch/?v=1760103355&ref=sharing"
    ) == "https://www.facebook.com/watch/?v=1760103355"


def test_legacy_video_php_url_is_canonicalized():
    assert canonicalize_facebook_video_url(
        "https://www.facebook.com/video.php?v=1760103355&ref=sharing"
    ) == "https://www.facebook.com/watch/?v=1760103355"
    assert canonicalize_facebook_video_url(
        "https://www.facebook.com/watch.php?video_id=1760103355"
    ) == "https://www.facebook.com/watch/?v=1760103355"


def test_fb_watch_short_link_is_preserved_for_redirect_resolution():
    url = "https://fb.watch/AbC123/?mibextid=tracking"
    assert canonicalize_facebook_video_url(url) == "https://fb.watch/AbC123"
    assert facebook_media_id(url) == "fb.watch:AbC123"


def test_facebook_share_video_urls_are_preserved_and_have_stable_ids():
    for kind in ("r", "v"):
        url = f"https://www.facebook.com/share/{kind}/AbC123/?mibextid=tracking"
        assert canonicalize_facebook_video_url(url) == (
            f"https://www.facebook.com/share/{kind}/AbC123/"
        )
        assert facebook_media_id(url) == f"facebook-share:{kind}:AbC123"


def test_non_video_and_non_facebook_urls_are_rejected():
    assert canonicalize_facebook_video_url("https://example.com/reel/123") is None
    assert canonicalize_facebook_video_url("https://facebook.com/channel/reels/") is None
    assert canonicalize_facebook_video_url("javascript:alert(1)") is None


def test_canonical_reel_url_is_supported_by_ytdlp_facebook_extractor():
    from yt_dlp.extractor.facebook import FacebookIE

    url = canonicalize_facebook_video_url("https://www.facebook.com/reel/1760103355")
    assert url == "https://www.facebook.com/watch/?v=1760103355"
    assert FacebookIE.suitable(url)

def test_login_redirect_is_reported_as_missing_session():
    assert facebook_login_required("https://www.facebook.com/login/?next=/page/reels")
    assert facebook_login_required("https://www.facebook.com/checkpoint/")
    assert facebook_login_required("https://www.facebook.com/page/reels/", '<form id="login_form">')
    assert not facebook_login_required("https://example.com/login/")


def test_facebook_auth_block_reason_distinguishes_login_checkpoint_and_recovery():
    assert facebook_auth_block_reason("https://www.facebook.com/login/") == "login"
    assert facebook_auth_block_reason("https://www.facebook.com/checkpoint/") == "checkpoint"
    assert facebook_auth_block_reason("https://www.facebook.com/recover/") == "recovery"
    assert facebook_auth_block_reason("https://example.com/login/") is None


def test_captcha_and_challenge_are_security_holds_not_scrape_successes():
    assert facebook_auth_block_reason("https://www.facebook.com/challenge/") == "checkpoint"
    assert facebook_auth_block_reason("https://www.facebook.com/captcha/") == "checkpoint"
    assert facebook_auth_block_reason(
        "https://www.facebook.com/page/reels/",
        '<form id="captcha_form"><input name="captcha"></form>',
    ) == "checkpoint"

def test_legacy_video_php_urls_are_canonicalized():
    for url in (
        "https://www.facebook.com/video.php?v=1760103355&ref=sharing",
        "https://m.facebook.com/watch.php?video_id=1760103355&ref=sharing",
    ):
        assert canonicalize_facebook_video_url(url) == (
            "https://www.facebook.com/watch/?v=1760103355"
        )

def test_legacy_video_php_and_watch_php_links_normalize_to_canonical_watch_urls():
    assert canonicalize_facebook_video_url(
        "https://m.facebook.com/video.php?video_id=1760103355&ref=share"
    ) == "https://www.facebook.com/watch/?v=1760103355"
    assert canonicalize_facebook_video_url(
        "https://www.facebook.com/watch.php?v=1760103355&ref=share"
    ) == "https://www.facebook.com/watch/?v=1760103355"


def test_video_php_and_watch_query_links_canonicalize_to_same_source_id():
    legacy = canonicalize_facebook_video_url("https://www.facebook.com/video.php?video_id=1760103355&ref=share")
    watch = canonicalize_facebook_video_url("https://m.facebook.com/watch.php?v=1760103355&ref=share")

    assert legacy == watch == "https://www.facebook.com/watch/?v=1760103355"
    assert facebook_media_id(legacy) == facebook_media_id(watch) == "1760103355"


def test_proxy_preflight_uses_http_default_port_and_honors_explicit_port(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_connection(address, timeout=None):
        calls.append((address, timeout))
        return Connection()

    monkeypatch.setattr(facebook_scraper.socket, "create_connection", fake_connection)

    assert facebook_network_reachable("http://proxy.example", timeout=1)
    assert facebook_network_reachable("http://proxy.example:3128", timeout=1)
    assert calls == [(('proxy.example', 80), 1.0), (('proxy.example', 3128), 1.0)]


def test_preferred_ipv4_route_gives_ipv4_and_ipv6_a_full_timeout(monkeypatch):
    resolutions = []
    connections = []

    def fake_getaddrinfo(_host, _port, family, _socktype):
        resolutions.append(family)
        if family == facebook_scraper.socket.AF_INET:
            return [(family, facebook_scraper.socket.SOCK_STREAM, 6, "", ("203.0.113.8", 443))]
        if family == facebook_scraper.socket.AF_INET6:
            return [(family, facebook_scraper.socket.SOCK_STREAM, 6, "", ("2001:db8::8", 443, 0, 0))]
        raise AssertionError("unexpected address family")

    class FakeSocket:
        def __init__(self, family, _socktype, _proto):
            self.family = family
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, value):
            self.timeout = value

        def bind(self, _address):
            return None

        def connect(self, address):
            connections.append((self.family, address, self.timeout))
            if self.family == facebook_scraper.socket.AF_INET:
                raise OSError("IPv4 route unavailable")

    monkeypatch.setattr(facebook_scraper.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(facebook_scraper.socket, "socket", FakeSocket)

    route = facebook_network_route(force_ipv4=True, timeout=5)

    assert route == "ipv6"
    assert resolutions == [facebook_scraper.socket.AF_INET, facebook_scraper.socket.AF_INET6]
    assert [entry[0] for entry in connections] == [facebook_scraper.socket.AF_INET, facebook_scraper.socket.AF_INET6]
    assert 4.9 <= connections[0][2] <= 5.0
    assert 4.9 <= connections[1][2] <= 5.0


def test_network_reachable_uses_ipv6_fallback_when_ipv4_preference_fails(monkeypatch):
    monkeypatch.setattr(
        facebook_scraper,
        "facebook_network_route",
        lambda *_args, **_kwargs: "ipv6",
    )

    assert facebook_network_reachable(force_ipv4=True, timeout=1)


def test_transient_webdriver_error_normalizes_network_error_separators():
    assert facebook_scraper.is_transient_facebook_error(RuntimeError("net::ERR_CONNECTION_RESET"))


def test_doh_fallback_resolves_when_local_dns_fails(monkeypatch):
    def failing_getaddrinfo(*_args, **_kwargs):
        raise OSError("dns blocked")

    def fake_doh(host, port, family, timeout):
        assert host == "www.facebook.com" and port == 443
        ip = "157.240.1.35" if family == facebook_scraper.socket.AF_INET else "2a03:2880::1"
        return [(family, facebook_scraper.socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(facebook_scraper.socket, "getaddrinfo", failing_getaddrinfo)
    monkeypatch.setattr(facebook_scraper, "_doh_resolve", fake_doh)

    def ok_connect(self, address):
        assert address[0] in {"157.240.1.35", "2a03:2880::1"}

    monkeypatch.setattr(facebook_scraper.socket.socket, "connect", ok_connect)

    route, failure = facebook_network_route_detailed(force_ipv4=True, timeout=5)
    assert route in {"ipv4", "ipv6"}
    assert failure is None


def test_doh_resolve_parses_a_and_aaaa_answers(monkeypatch):
    import json

    payload = json.dumps({
        "Answer": [
            {"name": "www.facebook.com.", "type": 1, "data": "157.240.1.35"},
            {"name": "www.facebook.com.", "type": 28, "data": "2a03:2880:f277:1cd:face:b00c:0:167"},
            {"name": "www.facebook.com.", "type": 5, "data": "star.c10r.facebook.com."},
        ],
    }).encode("utf-8")
    monkeypatch.setattr(
        facebook_scraper.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeDohResponse(payload),
    )

    records = facebook_scraper._doh_resolve(
        "www.facebook.com", 443, facebook_scraper.socket.AF_UNSPEC, 5
    )
    families = {record[0] for record in records}
    assert families == {facebook_scraper.socket.AF_INET, facebook_scraper.socket.AF_INET6}
    assert all(record[4][1] == 443 for record in records)


def test_route_detail_classifies_dns_and_connect_failures(monkeypatch):
    def failing_getaddrinfo(*_args, **_kwargs):
        raise OSError("dns blocked")

    monkeypatch.setattr(facebook_scraper.socket, "getaddrinfo", failing_getaddrinfo)
    monkeypatch.setattr(facebook_scraper, "_doh_resolve", lambda *_a, **_k: [])

    route, failure = facebook_network_route_detailed(force_ipv4=True, timeout=5)
    assert route is None
    assert failure == {"dns": ["IPv4", "IPv6"], "connect": []}


def test_legacy_route_wrapper_still_returns_route_only(monkeypatch):
    monkeypatch.setattr(
        facebook_scraper,
        "facebook_network_route_detailed",
        lambda *_args, **_kwargs: ("ipv4", None),
    )
    assert facebook_network_route() == "ipv4"
