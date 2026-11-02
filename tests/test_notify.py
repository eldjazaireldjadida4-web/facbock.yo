"""اختبارات notify: صمت بلا إعداد، وتفادي الأسرار، واستبدال دالة الإرسال."""
from __future__ import annotations

import logging
import types

import notify


def _settings(**overrides):
    base = {
        "telegram_enabled": True,
        "tg_token": "123:ABC",
        "tg_chat_id": "555",
    }
    base.update(overrides)
    return base


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"ok": true}'

    def getcode(self):
        return 200


def test_send_telegram_silent_when_disabled_or_incomplete(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: calls.append(a) or _FakeResp())
    notify.send_telegram(_settings(telegram_enabled=False), "نص")
    notify.send_telegram(_settings(tg_token=""), "نص")
    notify.send_telegram(_settings(tg_chat_id=""), "نص")
    assert calls == []


def test_send_telegram_builds_request_and_failure_is_logged_not_raised(monkeypatch):
    captured = {}

    def fake_urlopen(url, data=None, timeout=0):
        captured["url"] = url
        captured["data"] = data.decode("utf-8")
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    notify.send_telegram(_settings(), "رسالة تجريبية")
    assert "api.telegram.org/bot123:ABC/sendMessage" in captured["url"]
    assert "chat_id=555" in captured["data"]
    assert "disable_web_page_preview=true" in captured["data"]
    assert captured["timeout"] == 15

    logs = []
    def boom(url, data=None, timeout=0):
        raise ConnectionError("شبكة مقطوعة")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify.send_telegram(_settings(), "نص", logs.append) is None
    assert any("فشل إرسال إشعار Telegram" in message for message in logs)


def test_send_telegram_failure_does_not_log_token(monkeypatch, caplog):
    def boom(url, data=None, timeout=0):
        raise RuntimeError("فشل عام")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    with caplog.at_level(logging.WARNING):
        notify.send_telegram(_settings(), "نص")
    warning = " ".join(record.getMessage() for record in caplog.records)
    assert "فشل إشعار Telegram" in warning
    assert "123:ABC" not in warning
    assert "bot123" not in warning


def _ctx(quota=None):
    return {"quota": quota}


def _quota(used, daily_limit):
    return types.SimpleNamespace(used=used, daily_limit=daily_limit)

def _quota(used, daily_limit):
    return types.SimpleNamespace(used=used, daily_limit=daily_limit)


def test_cycle_summary_silent_without_uploads_or_dry_run():
    calls = []
    logs = []
    notify.send_cycle_summary(
        _ctx(), _settings(telegram_enabled=False),
        {"up_count": 0, "fail_count": 0, "pending": 0, "dry_count": 0, "dry_run": False, "elapsed": 61},
        log_cb=logs.append, send=lambda *a: calls.append(a),
    )
    assert calls == []
    assert any("اكتملت الدورة" in message for message in logs)
    assert "01:01" in logs[0]


def test_cycle_summary_sends_when_uploaded():
    calls = []
    notify.send_cycle_summary(
        _ctx(_quota(3, 50)), _settings(),
        {"up_count": 2, "fail_count": 1, "pending": 4, "dry_count": 0, "dry_run": False, "elapsed": 125},
        send=lambda *a: calls.append(a),
    )
    assert len(calls) == 1
    text = calls[0][1]
    assert "مرفوع: 2" in text and "الحصة: 3/50" in text and "02:05" in text


def test_cycle_summary_sends_in_dry_run():
    calls = []
    notify.send_cycle_summary(
        _ctx(), _settings(),
        {"up_count": 0, "fail_count": 0, "pending": 0, "dry_count": 3, "dry_run": True, "elapsed": 0},
        send=lambda *a: calls.append(a),
    )
    assert len(calls) == 1
    assert "وضع المحاكاة" in calls[0][1]


def test_main_wrapper_routes_through_monkeypatched_send_telegram():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "merged49_notify",
        Path(__file__).resolve().parent.parent / "fb_youtube_uploader_v49_pro.py",
    )
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)

    seen = []
    original = app.send_telegram
    app.send_telegram = lambda settings, text, log_cb=None: seen.append(text)
    try:
        app.send_cycle_summary(
            _ctx(), {"telegram_enabled": True, "tg_token": "t", "tg_chat_id": "c"},
            {"up_count": 1, "fail_count": 0, "pending": 0, "dry_count": 0, "dry_run": False, "elapsed": 5},
        )
    finally:
        app.send_telegram = original
    assert len(seen) == 1 and "ملخص الدورة" in seen[0]