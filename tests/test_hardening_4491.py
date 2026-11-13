"""انحدار 4.49.1 — أسرار في الإشعارات، فحوص صحية كاذبة، وخيوط التفريغ.

كل حالة هنا أُعيد إنتاجها قبل الإصلاح: رمز بوت يظهر في السجل، doctor يقول
«سليم» لملف لا يستطيع قراءته، تقرير تشخيصي يقول «لا أخطاء» لأن السجل غير
مقروء، وتفريغ يترك النموذج يعمل بعد الإلغاء.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import types
from pathlib import Path

import pytest

import diagnostic_report as report
import doctor
import facebook_auth
import facebook_scraper
import local_transcription as transcription
import notify
import safe_logging


def _settings(**overrides):
    base = {"telegram_enabled": True, "tg_token": "123:ABC", "tg_chat_id": "555"}
    base.update(overrides)
    return base


# ─────────────────────────── الإشعارات ───────────────────────────
def test_bot_token_with_newline_never_reaches_the_log(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: calls.append(a))

    with caplog.at_level(logging.WARNING):
        notify.send_telegram(_settings(tg_token="123:ABC\nDEF"), "نص")

    assert calls == []
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "ABC" not in logged and "DEF" not in logged


def test_failed_cycle_still_sends_a_summary() -> None:
    sent = []
    notify.send_cycle_summary(
        {"quota": None},
        _settings(),
        {"up_count": 0, "fail_count": 3, "pending": 3, "elapsed": 5},
        send=lambda settings, text, log_cb=None: sent.append(text),
    )
    assert sent and "فاشل/مؤجل: 3" in sent[0]


def test_plain_bot_token_outside_urls_is_redacted() -> None:
    redacted = safe_logging.redact_sensitive_text(
        "خطأ في /bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage"
    )
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in redacted


# ─────────────────────────── doctor ───────────────────────────
def _doctor_checks(tmp_path: Path):
    config, config_error = doctor._load_config(str(tmp_path))
    results = []
    doctor._collect_checks(
        lambda name, status, detail="", fix="": results.append((status, name, detail)),
        config,
        config_error,
        str(tmp_path),
    )
    return results


@pytest.mark.parametrize("payload", ["null", '{"installed": null}', '{"installed": "oops"}'])
def test_doctor_reports_malformed_client_secrets_instead_of_crashing(tmp_path: Path, payload: str) -> None:
    (tmp_path / "config.ini").write_text(
        "[SETTINGS]\nVIDEO_DIRECTORY = videos\nCLIENT_SECRETS_FILE = client_secrets.json\n",
        encoding="utf-8",
    )
    (tmp_path / "client_secrets.json").write_text(payload, encoding="utf-8")

    results = _doctor_checks(tmp_path)

    row = next(item for item in results if item[1] == "ملف تفويض Google")
    assert row[0] == doctor.BAD


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions required")
def test_doctor_does_not_call_an_unreadable_config_ok(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text("[SETTINGS]\nVIDEO_DIRECTORY = videos\n", encoding="utf-8")
    config_path.chmod(0o000)
    try:
        results = _doctor_checks(tmp_path)
    finally:
        config_path.chmod(0o600)

    row = next(item for item in results if item[1] == "ملف config.ini")
    assert row[0] == doctor.BAD


def test_doctor_uses_configparser_boolean_for_force_ipv4(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config.ini").write_text(
        "[SETTINGS]\nVIDEO_DIRECTORY = videos\n[WATCH]\nFORCE_IPV4 = on\n", encoding="utf-8"
    )
    seen = {}
    monkeypatch.setattr(
        doctor, "facebook_network_route", lambda *a, **k: seen.setdefault("force", a[1]) or "ipv4"
    )

    _doctor_checks(tmp_path)

    assert seen["force"] is True


# ─────────────────────────── التقرير التشخيصي ───────────────────────────
@pytest.mark.parametrize(
    "line, secret",
    [
        ('{"client_secret": "GOCSPX-secretvalue", "refresh_token": "1//abc"}', "GOCSPX-secretvalue"),
        ("proxy socks5h://user:supersecret@proxy.example:9050", "supersecret"),
        ("API key invalid: AIzaSyD-1234567890abcdefghijklmnop", "AIzaSyD-1234567890abcdefghijklmnop"),
    ],
)
def test_diagnostic_excerpt_uses_the_shared_redactor(line: str, secret: str) -> None:
    assert secret not in report._safe_error_excerpt(line)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions required")
def test_unreadable_logs_are_reported_not_treated_as_clean(tmp_path: Path) -> None:
    log = tmp_path / "errors.log"
    log.write_text("ERROR upload failed\n", encoding="utf-8")
    log.chmod(0o000)
    try:
        unreadable = report._unreadable_log_sources(tmp_path)
    finally:
        log.chmod(0o600)

    assert unreadable == ["errors.log"]

    text = report.format_report({
        "version": "4.49.1",
        "created": "2026-11-13T00:00:00+00:00",
        "platform": "TestOS 1",
        "python": "3.12.1",
        "errors": [],
        "issues": [],
        "unreadable_logs": unreadable,
        "facebook_network": {
            "IPv4": {"dns": "ok", "tcp": "ok", "addresses": 1},
            "IPv6": {"dns": "ok", "tcp": "ok", "addresses": 1},
        },
        "network_timeout_seconds_per_family": 12,
        "network_preflight_enabled": True,
        "cookies": {"enabled": False, "status": "disabled",
                    "active_cookie_count": 0, "expired_cookie_count": 0},
        "youtube_network": {
            "IPv4": {"tcp": "reachable"},
            "IPv6": {"tcp": "reachable"},
        },
        "youtube_auth": {"client_secrets_file_present": True, "token_file_count": 1},
        "rights": {"mode": "strict", "configuration_errors": []},
        "pages": {"enabled": 1, "total": 1, "state": "ok"},
        "queue": {"total": 0, "state": "ok", "by_status": {}},
        "disk": {"free_gb": 20.0, "minimum_gb": 2.0},
        "proxy": {"state": "not_configured", "reachable": None},
    })
    assert "تعذرت قراءة ملفات سجل" in text
    assert "لم تُرصد رسائل خطأ" not in text


def test_report_flags_the_content_review_blocker_the_app_enforces(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config.ini").write_text(
        "[SETTINGS]\nVIDEO_DIRECTORY = videos\nCLIENT_SECRETS_FILE = client_secrets.json\n"
        "[STUDIO]\nRIGHTS_GATE_MODE = strict\nREVIEW_BEFORE_UPLOAD = true\n"
        "[SAFETY]\nENABLED = true\nCUTTER_ROOT =\n",
        encoding="utf-8",
    )
    reachable = {
        "IPv4": {"dns": "ok", "addresses": 1, "tcp": "reachable"},
        "IPv6": {"dns": "ok", "addresses": 0, "tcp": "no_address"},
    }
    monkeypatch.setattr(report, "probe_service", lambda *_a, **_k: reachable)

    data = report.collect_diagnostics(str(tmp_path), version="test")

    assert any(issue["category"] == "content_review" for issue in data["issues"])
    assert "OUSSAMA Cutter" in report.format_report(data)


# ─────────────────────────── التفريغ المحلي ───────────────────────────
def test_cancelled_transcription_closes_the_segment_generator(tmp_path, monkeypatch) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    stop_event = threading.Event()
    state = {"closed": False}

    class Segments:
        def __iter__(self):
            yield types.SimpleNamespace(text="بداية")
            stop_event.set()
            yield types.SimpleNamespace(text="بعد الإلغاء")

        def close(self):
            state["closed"] = True

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            return Segments(), types.SimpleNamespace(language="ar")

    monkeypatch.setattr(transcription, "_load_model", lambda _name: FakeModel())

    with pytest.raises(transcription.TranscriptionCancelled):
        transcription.transcribe_video_file(video, stop_event=stop_event)

    assert state["closed"] is True


def test_two_transcriptions_do_not_share_the_model_concurrently(tmp_path, monkeypatch) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    state = {"active": 0, "overlap": False}
    lock = threading.Lock()

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            with lock:
                state["active"] += 1
                if state["active"] > 1:
                    state["overlap"] = True
            time.sleep(0.05)
            with lock:
                state["active"] -= 1
            return iter([types.SimpleNamespace(text="نص")]), types.SimpleNamespace(language="ar")

    monkeypatch.setattr(transcription, "_load_model", lambda _name: FakeModel())
    threads = [
        threading.Thread(target=transcription.transcribe_video_file, args=(video,))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert state["overlap"] is False


# ─────────────────────────── كوكيز Facebook ───────────────────────────
class _BrokenDriver:
    def set_page_load_timeout(self, *_args):
        pass

    def get(self, _url):
        raise RuntimeError("renderer crashed")

    def add_cookie(self, _cookie):
        pass


def test_navigation_failure_is_reported_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(
        ".facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t1\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\txs\tabc\n",
        encoding="utf-8",
    )

    result = facebook_auth.load_facebook_cookies(_BrokenDriver(), str(path))

    assert result["navigation_failed"] is True
    assert result["loaded_cookie_count"] == 0


class _HostDriver:
    def __init__(self):
        self.navigations = []

    def set_page_load_timeout(self, *_args):
        pass

    def get(self, url):
        self.navigations.append(url)

    def add_cookie(self, _cookie):
        pass


def test_host_only_cookies_cannot_trigger_unbounded_navigation(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    lines = []
    for index in range(12):
        lines.append(f"m{index}.facebook.com\tFALSE\t/\tTRUE\t0\tname{index}\tv{index}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    driver = _HostDriver()

    result = facebook_auth.load_facebook_cookies(driver, str(path))

    # تنقل واحد للصفحة الرئيسية + حد أقصى 5 تنقلات مضيف
    assert len(driver.navigations) <= 6
    assert result["loaded_cookie_count"] >= 1


# ─────────────────────────── شبکه وDNS ───────────────────────────
def test_dns_resolution_is_bounded_by_the_timeout(monkeypatch) -> None:
    def hang(*_args, **_kwargs):
        time.sleep(5)
        return []

    monkeypatch.setattr(facebook_scraper.socket, "getaddrinfo", hang)
    started = time.monotonic()
    route = facebook_scraper.facebook_network_route(timeout=1)
    elapsed = time.monotonic() - started

    # مهلة واحدة لكل عائلة (1s) بدل انتظار getaddrinfo 5 ثوانٍ لكل منها.
    assert route is None
    assert elapsed < 3


# ─────────────────────────── تنظيف بدء التشغيل ───────────────────────────
def test_startup_sweep_uses_the_configured_video_directory(tmp_path: Path, monkeypatch) -> None:
    import fb_youtube_uploader_v49_pro as app

    stale = tmp_path / ".fb2yt-old-attempt"
    stale.mkdir()
    (stale / "part.mp4").write_bytes(b"x")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    instance = app.App.__new__(app.App)
    instance.video_directory_e = type("E", (), {"get": lambda self: str(tmp_path)})()
    logs = []
    instance._log = logs.append

    instance._sweep_stale_downloads()

    assert not stale.exists()
    assert logs and "حُذف" in logs[0]
