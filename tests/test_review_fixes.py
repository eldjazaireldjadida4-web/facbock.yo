# -*- coding: utf-8 -*-
"""اختبارات إصلاحات مراجعة الكود للإصدار 4.49.14:

1) تصنيف 403 PERMISSION_DENIED كحجب مفتاح لا نفاد حصة في ai_client.
2) حذف نماذج Gemini المتقاعدة من GEMINI_MODELS.
3) ضمان إنهاء العملية عند الإغلاق رغم بقاء خيط عامل حي.
4) تحذير لمرة واحدة على Windows في file_security.restrict_file_permissions.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import ai_client
import file_security

import fb_youtube_uploader_v49_pro as app


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://generativelanguage.googleapis.com", code, "err", {}, io.BytesIO(body))


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return b"{}"


def _fake_urlopen(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _read_json(path, default):
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(path, data):
    from pathlib import Path
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_version_single_source():
    import diagnostic_report
    import version

    assert app.__version__ == version.APP_VERSION
    assert diagnostic_report.APP_VERSION == version.APP_VERSION


def test_gemini_models_drop_retired_models():
    assert "gemini-2.0-flash" not in ai_client.GEMINI_MODELS
    assert "gemini-1.5-flash" not in ai_client.GEMINI_MODELS
    assert ai_client.GEMINI_MODELS[0] == "gemini-2.5-flash"


def test_gemini_permission_denied_blocks_key_not_quota(tmp_path, monkeypatch):
    keys_file = str(tmp_path / "ai_keys.json")
    _write_json(keys_file, [{"key": "k1", "exhausted_until": "", "last_error": ""}])
    body = json.dumps({"error": {
        "code": 403, "message": "billing disabled",
        "status": "PERMISSION_DENIED"}}).encode("utf-8")
    _fake_urlopen(monkeypatch, [_http_error(403, body)])

    _, keys = ai_client.gemini_generate(
        "نص", ai_client.GEMINI_MODELS[0], keys_file, _read_json, _write_json)

    assert "منع مشروع" in keys[0]["last_error"]
    assert "نفاد الحصة" not in keys[0]["last_error"]


def test_gemini_429_still_reports_quota(tmp_path, monkeypatch):
    keys_file = str(tmp_path / "ai_keys.json")
    _write_json(keys_file, [{"key": "k1", "exhausted_until": "", "last_error": ""}])
    _fake_urlopen(monkeypatch, [_http_error(429, b'{"error": {"status": "RESOURCE_EXHAUSTED"}}')])

    _, keys = ai_client.gemini_generate(
        "نص", ai_client.GEMINI_MODELS[0], keys_file, _read_json, _write_json)

    assert "نفاد الحصة" in keys[0]["last_error"]


def test_close_force_exits_when_worker_alive_past_deadline(monkeypatch):
    import threading
    import time as time_module

    gate = threading.Event()
    worker = threading.Thread(target=gate.wait, daemon=True)
    worker.start()
    exits = []

    def fake_exit(code):
        exits.append(code)
        gate.set()

    stub = object.__new__(app.App)
    stub._thread = worker
    stub._close_deadline = time_module.monotonic() - 1.0
    destroyed = []
    stub.root = SimpleNamespace(destroy=lambda: destroyed.append(True))
    stub.status_lbl = SimpleNamespace(config=lambda **_kw: None)

    monkeypatch.setattr(stub, "_exit_fn", fake_exit)
    stub._finish_close_when_idle()

    gate.set()
    worker.join(timeout=5)
    assert destroyed == [True]
    assert exits == [3]


def test_close_waits_then_exits_without_busy_worker(monkeypatch):
    stub = object.__new__(app.App)
    stub._thread = None
    stub._close_deadline = None
    destroyed = []

    class _Root:
        def after(self, _ms, _cb):
            raise AssertionError("no reschedule expected")

        def destroy(self):
            destroyed.append(True)

    stub.root = _Root()
    stub.status_lbl = SimpleNamespace(config=lambda **_kw: None)
    exits = []
    monkeypatch.setattr(stub, "_exit_fn", lambda code: exits.append(code))
    stub._finish_close_when_idle()

    assert destroyed == [True]
    assert exits == []


def test_windows_permission_denied_warns_once(monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(file_security, "_WINDOWS_PERMISSION_WARNING_EMITTED", False)
    monkeypatch.setattr(file_security, "_warn_windows_permissions_once", lambda: warnings.append(True))

    results = [file_security.restrict_file_permissions(
        str(tmp_path / "a.json"), _force_windows=True) for _ in range(2)]

    assert len(warnings) == 1
    assert results == [False, False]
