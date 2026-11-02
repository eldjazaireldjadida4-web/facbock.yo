from __future__ import annotations

import json
import socket
from pathlib import Path

import diagnostic_report as report


REACHABLE = {
    "IPv4": {"dns": "ok", "addresses": 1, "tcp": "reachable"},
    "IPv6": {"dns": "ok", "addresses": 0, "tcp": "no_address"},
}


def _write_app_state(root: Path, *, proxy="", queue=None, cookies=True):
    (root / "config.ini").write_text(
        "[SETTINGS]\n"
        "CLIENT_SECRETS_FILE = client_secrets.json\n"
        "VIDEO_DIRECTORY = videos\n"
        "MIN_DISK_GB = 0\n"
        "[WATCH]\n"
        f"PROXY = {proxy}\n"
        "NETWORK_PREFLIGHT_TIMEOUT = 5\n"
        "NETWORK_PREFLIGHT = true\n"
        "FORCE_IPV4 = true\n"
        "[STUDIO]\n"
        f"COOKIES_ENABLED = {str(cookies).lower()}\n"
        "COOKIES_FILE = cookies.txt\n"
        "RIGHTS_GATE_MODE = operator_confirmed\n"
        "RIGHTS_OPERATOR_BASIS = licensed\n"
        "RIGHTS_OPERATOR_STATEMENT = I hold documented rights to publish this source content.\n"
        "RIGHTS_OPERATOR_EVIDENCE = License-2026\n",
        encoding="utf-8",
    )
    (root / "videos").mkdir(exist_ok=True)
    (root / "client_secrets.json").write_text('{"client_secret":"must-not-appear"}', encoding="utf-8")
    (root / "account_token.json").write_text('{"token":"oauth-secret"}', encoding="utf-8")
    (root / "pages.json").write_text(
        json.dumps([{"url": "https://www.facebook.com/private-page", "name": "Private Page", "enabled": True}]),
        encoding="utf-8",
    )
    (root / "failed_queue.json").write_text(json.dumps(queue or []), encoding="utf-8")
    (root / "cookies.txt").write_text(
        "# Netscape HTTP Cookie File\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\tc_user\tcookie-secret-user\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\txs\tcookie-secret-session\n",
        encoding="utf-8",
    )


def test_error_code_distinguishes_network_failure_types():
    assert report._error_code(socket.timeout()) == "timeout"
    assert report._error_code(ConnectionRefusedError(111, "refused")) == "connection_refused"
    assert report._error_code(OSError(101, "unreachable")) == "network_unreachable"
    assert report._error_code(socket.gaierror(socket.EAI_NONAME, "missing")) == "dns_failure"


def test_probe_service_reports_each_address_family_without_exposing_addresses(monkeypatch):
    def fake_getaddrinfo(_host, _port, family, _socktype):
        if family == socket.AF_INET:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]
        raise socket.gaierror(socket.EAI_NONAME, "no IPv6")

    class Socket:
        def __init__(self, *_args):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            assert address[0] == "192.0.2.1"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(report.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(report.socket, "socket", Socket)
    result = report.probe_service("example.invalid", timeout=1)

    assert result["IPv4"]["tcp"] == "reachable"
    assert result["IPv6"]["dns"] == "dns_failure"
    assert "192.0.2.1" not in json.dumps(result)


def test_probe_service_caps_timeouts_and_classifies_refused_connection(monkeypatch):
    monkeypatch.setattr(
        report.socket,
        "getaddrinfo",
        lambda _host, _port, family, _type: [(family, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    timeouts = []

    class RefusedSocket:
        def __init__(self, *_args):
            pass

        def settimeout(self, timeout):
            timeouts.append(timeout)

        def connect(self, _address):
            raise ConnectionRefusedError(111, "refused")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(report.socket, "socket", RefusedSocket)
    result = report.probe_service("example.invalid", timeout=100)

    assert result["IPv4"]["tcp"] == "connection_refused"
    assert result["IPv6"]["tcp"] == "connection_refused"
    assert timeouts and all(value <= 15 for value in timeouts)


def test_proxy_endpoint_redacts_credentials_and_rejects_malformed_values():
    state, host, port = report._proxy_endpoint("socks5h://user:secret@proxy.example:9050")
    assert (state, host, port) == ("configured", "proxy.example", 9050)
    assert report._proxy_endpoint("") == ("not_configured", None, None)
    assert report._proxy_endpoint("file:///tmp/proxy") == ("invalid", None, None)
    assert report._proxy_endpoint("http://proxy.example:not-a-port") == ("invalid", None, None)


def test_collect_diagnostics_aggregates_statuses_without_reading_secret_values(monkeypatch, tmp_path):
    queue = [{"id": "private-id", "url": "https://facebook.com/private-video", "status": "rights_review_required"}]
    _write_app_state(tmp_path, proxy="socks5h://user:proxy-secret@proxy.example:9050", queue=queue)
    (tmp_path / "automation.log").write_text(
        "[23:16:52] أوقف التشغيل بسبب نقص بوابة الحقوق ومرجع إثبات\n"
        "[23:17:45] تعذر الاتصال بفيسبوك عبر IPv4 وIPv6\n"
        "[23:18:01] فشل تسجيل الدخول Facebook؛ https://facebook.com/private-page?access_token=log-secret\n",
        encoding="utf-8",
    )
    (tmp_path / "errors.log").write_text("ERROR https://facebook.com/private-page?token=error-secret", encoding="utf-8")
    monkeypatch.setattr(report, "probe_service", lambda *_args, **_kwargs: REACHABLE)

    data = report.collect_diagnostics(str(tmp_path), version="test")
    rendered = report.format_report(data)

    assert data["network_timeout_seconds_per_family"] == 10
    assert data["pages"] == {"total": 1, "enabled": 1, "state": "ok"}
    assert data["queue"]["total"] == 1
    assert data["queue"]["by_status"]["rights_review_required"] == 1
    assert data["cookies"]["has_session_pair"] is True
    assert data["proxy"]["state"] == "configured"
    assert {item["category"] for item in data["errors"]} >= {"rights", "facebook_network", "facebook_auth"}
    for secret in (
        "must-not-appear",
        "oauth-secret",
        "cookie-secret-user",
        "cookie-secret-session",
        "proxy-secret",
        "log-secret",
        "error-secret",
        "private-page",
        "private-video",
        "Private Page",
        "private-id",
    ):
        assert secret not in rendered
    assert "أوقف التشغيل بسبب نقص بوابة الحقوق ومرجع إثبات" in rendered
    assert "مرجع إثبات" in rendered
    assert "لا يثبت أن Facebook قبل الجلسة" in rendered


def test_diagnostic_report_applies_legacy_rights_migration_without_writing_config(monkeypatch, tmp_path):
    _write_app_state(tmp_path)
    config_path = tmp_path / "config.ini"
    config_path.write_text("[STUDIO]\nRIGHTS_GATE_MODE = strict\nRIGHTS_OPERATOR_STATEMENT =\n", encoding="utf-8")
    monkeypatch.setattr(report, "probe_service", lambda *_args, **_kwargs: REACHABLE)

    data = report.collect_diagnostics(str(tmp_path), version="test")

    assert data["rights"]["mode"] == "operator_confirmed"
    assert data["rights"]["configuration_errors"] == []
    assert not any(issue["category"] == "rights" for issue in data["issues"])
    assert "RIGHTS_GATE_MODE = strict" in config_path.read_text(encoding="utf-8")


def test_report_lists_actionable_findings_and_numbers_them_once():
    data = {
        "version": "4.44.0",
        "created": "2026-10-31T10:00:00+00:00",
        "platform": "TestOS 1",
        "python": "3.12.1",
        "errors": [],
        "issues": [
            {"category": "facebook_network", "severity": "error"},
            {"category": "rights", "severity": "error"},
        ],
        "facebook_network": {
            "IPv4": {"dns": "timeout", "tcp": "timeout", "addresses": 0},
            "IPv6": {"dns": "ok", "tcp": "network_unreachable", "addresses": 1},
        },
        "network_timeout_seconds_per_family": 12,
        "network_preflight_enabled": True,
        "cookies": {"enabled": False, "status": "disabled", "active_cookie_count": 0, "expired_cookie_count": 0},
        "youtube_network": REACHABLE,
        "youtube_auth": {"client_secrets_file_present": True, "token_file_count": 1},
        "rights": {"mode": "operator_confirmed", "configuration_errors": ["مرجع إثبات ناقص"]},
        "pages": {"enabled": 1, "total": 1, "state": "ok"},
        "queue": {"total": 2, "state": "ok", "by_status": {"pending": 2}},
        "disk": {"free_gb": 20.0, "minimum_gb": 2.0},
        "proxy": {"state": "not_configured", "reachable": None},
    }
    rendered = report.format_report(data)

    assert "**مانع — اتصال Facebook**" in rendered
    assert "**مانع — بوابة الحقوق**" in rendered
    assert "1. " in rendered and "2. " in rendered
    assert "3. " not in rendered
    assert "مهلة التشغيل الآلي: `12`" in rendered
    assert "عنوانه ومنفذه" in rendered


def test_format_report_handles_clean_state_without_duplicate_sections():
    data = {
        "version": "4.44.0",
        "created": "2026-10-31T10:00:00+00:00",
        "platform": "TestOS 1",
        "python": "3.12.1",
        "errors": [],
        "issues": [],
        "facebook_network": {"IPv4": REACHABLE["IPv4"], "IPv6": REACHABLE["IPv6"]},
        "network_timeout_seconds_per_family": 12,
        "network_preflight_enabled": True,
        "cookies": {"enabled": False, "status": "disabled", "active_cookie_count": 0, "expired_cookie_count": 0},
        "youtube_network": REACHABLE,
        "youtube_auth": {"client_secrets_file_present": True, "token_file_count": 1},
        "rights": {"mode": "strict", "configuration_errors": []},
        "pages": {"enabled": 1, "total": 1, "state": "ok"},
        "queue": {"total": 0, "state": "missing", "by_status": {}},
        "disk": {"free_gb": 30.0, "minimum_gb": 2.0},
        "proxy": {"state": "not_configured", "reachable": None},
    }
    rendered = report.format_report(data)

    assert rendered.count("- الإصدار:") == 1
    assert rendered.count("## ملخص الأعطال") == 1
    assert "لم يظهر مانع إعدادات أو اتصال" in rendered
    assert "لم يُكتشف مانع إعدادات واضح" in rendered


def test_write_diagnostic_report_uses_atomic_local_markdown(monkeypatch, tmp_path):
    _write_app_state(tmp_path, cookies=False)
    monkeypatch.setattr(report, "probe_service", lambda *_args, **_kwargs: REACHABLE)

    path = report.write_diagnostic_report(str(tmp_path), version="4.44.0")

    assert path.parent == tmp_path / "reports"
    assert path.suffix == ".md"
    assert path.is_file()
    assert not list(path.parent.glob("*.tmp"))
    assert "لم يُرسل أي شيء إلى خدمة خارجية" in path.read_text(encoding="utf-8")


def test_classify_error_covers_primary_operational_categories():
    samples = {
        "الحصة اليومية نفدت": "youtube_quota",
        "YouTube OAuth token invalid": "youtube_auth",
        "مساحة التخزين منخفضة": "disk",
        "فشل تنزيل الفيديو": "download",
        "فشل رفع الفيديو": "upload",
        "unexpected exception": "runtime",
    }
    assert {text: report._classify_error(text) for text in samples} == samples


def test_queue_summary_sanitizes_status_and_never_returns_item_details(tmp_path):
    (tmp_path / "failed_queue.json").write_text(
        json.dumps([
            {"id": "private-id", "url": "https://facebook.com/private", "status": "upload_in_progress"},
            {"url": "https://facebook.com/other", "status": "unrecognized"},
            "malformed",
        ]),
        encoding="utf-8",
    )

    summary = report._queue_summary(tmp_path)

    assert summary == {
        "state": "ok",
        "total": 3,
        "by_status": {"upload_in_progress": 1, "other": 1, "invalid_item": 1},
    }
    assert "private-id" not in json.dumps(summary)
    assert "facebook.com" not in json.dumps(summary)


def test_invalid_config_and_queue_are_reported_without_exposing_contents(monkeypatch, tmp_path):
    (tmp_path / "config.ini").write_text("[broken\nsecret-value", encoding="utf-8")
    (tmp_path / "failed_queue.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(report, "probe_service", lambda *_args, **_kwargs: {
        "IPv4": {"dns": "dns_failure", "addresses": 0, "tcp": "not_tested"},
        "IPv6": {"dns": "dns_failure", "addresses": 0, "tcp": "not_tested"},
    })

    data = report.collect_diagnostics(str(tmp_path))
    rendered = report.format_report(data)

    assert data["config_state"] == "invalid"
    assert data["queue"]["state"] == "invalid"
    assert {item["category"] for item in data["issues"]} >= {"configuration", "queue_file", "facebook_network", "youtube_network", "pages"}
    assert "secret-value" not in rendered
    assert "ملف قائمة الانتظار" in rendered


def test_error_summaries_include_log_locations_and_redact_private_details(tmp_path):
    (tmp_path / "errors.log").write_text(
        "2026-10-31 10:11:12 - ERROR - failed upload title=PrivateTitle\n"
        "[10:12:13] تعذر الاتصال بفيسبوك?access_token=should-not-appear\n",
        encoding="utf-8",
    )

    summaries = report._error_summaries(tmp_path)
    by_category = {item["category"]: item for item in summaries}

    assert set(by_category) == {"facebook_network", "upload"}
    assert by_category["upload"]["source_file"] == "errors.log"
    assert by_category["upload"]["line_number"] == 1
    assert "title=[محتوى محذوف]" in by_category["upload"]["detail"]
    assert all("PrivateTitle" not in json.dumps(item) for item in summaries)
    assert all("should-not-appear" not in json.dumps(item) for item in summaries)


def test_error_summaries_read_rotated_logs_and_prefer_the_latest_source(tmp_path):
    (tmp_path / "automation.log.2").write_text("[08:00:00] Facebook network timeout old", encoding="utf-8")
    (tmp_path / "automation.log.1").write_text("[09:00:00] Facebook network timeout middle", encoding="utf-8")
    (tmp_path / "automation.log").write_text("[10:00:00] Facebook network timeout current", encoding="utf-8")
    (tmp_path / "errors.log.1").write_text("ERROR failed upload title=PrivateTitle", encoding="utf-8")

    summaries = report._error_summaries(tmp_path)
    by_category = {item["category"]: item for item in summaries}

    assert by_category["facebook_network"]["count"] == 3
    assert by_category["facebook_network"]["source_file"] == "automation.log"
    assert by_category["facebook_network"]["last_seen"] == "10:00:00"
    assert by_category["upload"]["source_file"] == "errors.log.1"
    assert "PrivateTitle" not in json.dumps(summaries)


def test_safe_error_excerpt_masks_cookie_and_authorization_headers():
    excerpt = report._safe_error_excerpt(
        "Request failed Cookie: c_user=private-user; xs=private-session"
    )
    authorization = report._safe_error_excerpt(
        "Request failed Authorization: Bearer private-oauth-token"
    )

    assert "private-user" not in excerpt
    assert "private-session" not in excerpt
    assert "private-oauth-token" not in authorization
    assert "[مخفي]" in excerpt and "[مخفي]" in authorization


def test_write_report_can_be_generated_from_a_fresh_install(monkeypatch, tmp_path):
    monkeypatch.setattr(report, "probe_service", lambda *_args, **_kwargs: {
        "IPv4": {"dns": "no_address", "addresses": 0, "tcp": "not_tested"},
        "IPv6": {"dns": "no_address", "addresses": 0, "tcp": "not_tested"},
    })

    path = report.write_diagnostic_report(str(tmp_path), version="test")
    text = path.read_text(encoding="utf-8")

    assert "config.ini" in text
    assert "cookies" in text.lower()
    assert "OAuth" in text
    assert "لم يُرسل أي شيء" in text


def test_rights_guidance_distinguishes_owned_content_from_permission():
    guidance = report.CATEGORY_GUIDANCE["rights"]
    assert "owned" in guidance
    assert "ترخيص" in guidance
    assert "مرجعاً حقيقياً" in guidance
