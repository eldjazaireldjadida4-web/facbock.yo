"""اختبارات ai_client — عزل عميل Gemini وتدوير المفاتيح عن التطبيق الرئيسي."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import ai_client


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://generativelanguage.googleapis.com", code, "err", {}, io.BytesIO(body))


class _FakeResp:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps({
            "candidates": [{"content": {"parts": [{"text": self._text}]}}],
        }).encode("utf-8")


def _fake_urlopen(monkeypatch, outcomes):
    """outcomes: قائمة؛ كل عنصر استثناء يُرفع أو نص يعيد. تعيد قائمة الطلبات الملتقطة."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _read_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _keys_file(tmp_path):
    return str(tmp_path / "ai_keys.json")


def test_gemini_call_sends_key_in_header_not_url(monkeypatch):
    calls = _fake_urlopen(monkeypatch, ["تم"])
    result = ai_client._gemini_call("اختبر", "secret-key-123", "gemini-2.5-flash")

    assert result == "تم"
    assert "secret-key-123" not in calls[0].full_url
    assert "key=" not in calls[0].full_url
    headers = {k.lower(): v for k, v in calls[0].header_items()}
    assert headers["x-goog-api-key"] == "secret-key-123"


def test_rotation_skips_quota_exhausted_key(monkeypatch, tmp_path):
    keys_file = _keys_file(tmp_path)
    keys = [
        {"key": "k1", "exhausted_until": "", "last_error": ""},
        {"key": "k2", "exhausted_until": "", "last_error": ""},
    ]
    _write_json(keys_file, keys)
    writes = []
    hardened = []

    def write_json(path, data):
        writes.append(json.loads(json.dumps(data)))
        _write_json(path, data)

    def harden(path):
        hardened.append(path)

    _fake_urlopen(monkeypatch, [
        _http_error(429, b'{"error": {"message": "quota"}}'),
        "جواب",
    ])
    text, updated = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", keys_file, _read_json, write_json,
        harden=harden)
    assert text == "جواب"
    assert updated[0]["exhausted_until"] > datetime.now().isoformat(timespec="seconds")
    assert "نفاد الحصة" in updated[0]["last_error"]
    assert updated[1]["exhausted_until"] == ""
    assert len(writes) == 1
    assert hardened == [keys_file]


def test_invalid_key_marked_and_rotates(monkeypatch, tmp_path):
    keys_file = _keys_file(tmp_path)
    keys = [
        {"key": "bad", "exhausted_until": "", "last_error": ""},
        {"key": "good", "exhausted_until": "", "last_error": ""},
    ]
    _write_json(keys_file, keys)
    _fake_urlopen(monkeypatch, [
        _http_error(400),
        "تمام",
    ])
    logs = []
    text, updated = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", keys_file, _read_json, _write_json,
        log_cb=logs.append)

    assert text == "تمام"
    assert updated[0]["exhausted_until"] > datetime.now().isoformat(timespec="seconds")
    assert "غير صالح" in updated[0]["last_error"]
    assert any("غير صالح" in message for message in logs)


def test_network_failure_sets_short_cooldown(monkeypatch, tmp_path):
    keys_file = _keys_file(tmp_path)
    keys = [
        {"key": "k1", "exhausted_until": "", "last_error": ""},
        {"key": "k2", "exhausted_until": "", "last_error": ""},
    ]
    _write_json(keys_file, keys)
    _fake_urlopen(monkeypatch, [
        urllib.error.URLError("timeout"),
        "بعد الانقطاع",
    ])
    before = datetime.now()
    text, updated = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", keys_file, _read_json, _write_json)

    assert text == "بعد الانقطاع"
    until = datetime.fromisoformat(updated[0]["exhausted_until"])
    delta_minutes = (until - before).total_seconds() / 60
    assert 9 <= delta_minutes <= 11
    assert updated[1]["exhausted_until"] == ""

def test_all_keys_exhausted_returns_none(tmp_path, monkeypatch):
    keys_file = _keys_file(tmp_path)
    keys = [{"key": "k1", "exhausted_until": (
        datetime.now() + timedelta(days=1)).isoformat(timespec="seconds"),
        "last_error": ""}]
    _write_json(keys_file, keys)
    logs = []

    def forbidden_urlopen(*args, **kwargs):
        raise AssertionError("لا نداء عند استنفاد كل المفاتيح")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_urlopen)
    result = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", keys_file, _read_json, _write_json,
        log_cb=logs.append)

    assert result[0] is None
    assert any("مستنفدة" in message for message in logs)


def test_no_keys_returns_none_with_hint(tmp_path):
    logs = []
    result = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", _keys_file(tmp_path), _read_json, _write_json,
        log_cb=logs.append)

    assert result == (None, [])
    assert any("لا توجد مفاتيح" in message for message in logs)


def test_load_save_roundtrip_persists_keys(tmp_path):
    keys_file = _keys_file(tmp_path)
    keys = [{"key": "k", "exhausted_until": "", "last_error": ""}]

    ai_client.save_ai_keys(keys, keys_file, _write_json, harden=None)
    loaded = ai_client.load_ai_keys(keys_file, _read_json)

    assert loaded == keys
    # كائن غير قائمة يُعامَل كقائمة فارغة
    Path(keys_file).write_text('{"not": "a list"}', encoding="utf-8")
    assert ai_client.load_ai_keys(keys_file, _read_json) == []