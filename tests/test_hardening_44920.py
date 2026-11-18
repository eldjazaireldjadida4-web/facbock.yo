"""انحدار 4.49.20 — مهلة DNS في التقرير التشخيصي، جدولة نشر لا تُسقط الدورة،
حارس SSRF لاستعادة og:title، ومسارات doctor/show_status الصحيحة.

كل اختبار هنا يثبت عطلاً حقيقياً وُجد في المراجعة: إما أنه كان يعلّق أو
يُسقط التشغيل، أو كان يقرأ/يجلب من المكان الخطأ.

4.49.21: اختبار أذونات doctor صار POSIX فقط (يُتخطَّى على Windows حيث
القسم 7.5 محروس بـ `os.name == "posix"` قصداً)، مع اختبار محاكاة يثبت
أن Windows لا يُنتج WARN أذونات.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import diagnostic_report as report
import doctor
import enable_automation as ea
import facebook_scraper
import fb_youtube_uploader_v49_pro as app
import upload_pipeline as pipe
import version

ROOT = Path(__file__).resolve().parents[1]


# ── diagnostic_report: مهلة DNS ──────────────────────────────────────────

def test_probe_service_returns_within_timeout_when_dns_hangs(monkeypatch):
    """getaddrinfo معلّق كان يعلّق التقرير كله رغم مهلة timeout المعلنة."""
    blocker = threading.Event()

    def hanging_getaddrinfo(_host, _port, family, _socktype):
        blocker.wait()  # لا يعود أبداً؛ الخيط daemon فلا يمنع خروج pytest

    monkeypatch.setattr(report.socket, "getaddrinfo", hanging_getaddrinfo)
    started = time.monotonic()
    result = report.probe_service("www.facebook.com", timeout=2)
    elapsed = time.monotonic() - started
    assert elapsed < 5
    assert result["IPv4"] == {"dns": "timeout", "addresses": 0, "tcp": "not_tested"}
    assert result["IPv6"] == {"dns": "timeout", "addresses": 0, "tcp": "not_tested"}


# ── upload_pipeline.next_publish_time ─────────────────────────────────────

def _fake_state_hook(monkeypatch, state):
    saved = {}

    def load_state():
        return state

    def save_state(new_state):
        saved.clear()
        saved.update(new_state)

    monkeypatch.setattr(
        pipe, "_app_hook",
        lambda name: {"load_state": load_state, "save_state": save_state}[name],
    )
    return saved


def test_next_publish_time_survives_inverted_bounds(monkeypatch):
    """SCHEDULE_MIN > SCHEDULE_MAX من config.ini محرَّر يدوياً كان يُسقط
    الدورة بـ ValueError من randint؛ مسار CLI لا يمرّ على التحقق."""
    state: dict = {}
    _fake_state_hook(monkeypatch, state)
    result = pipe.next_publish_time(60, 20)
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert state["next_publish_at"] == result.isoformat()


def test_next_publish_time_survives_naive_and_garbage_stored_value(monkeypatch):
    """طابع next_publish_at بلا منطقة زمنية كان يرفع TypeError عند مقارنته
    بأساس واعٍ؛ والقيمة غير النصية/التالفة كانت تُسقط الدورة أيضاً."""
    future_naive = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(tzinfo=None)
    state: dict = {"next_publish_at": future_naive.isoformat()}
    _fake_state_hook(monkeypatch, state)
    result = pipe.next_publish_time(20, 20)
    assert result > future_naive.replace(tzinfo=timezone.utc)

    state["next_publish_at"] = 12345  # ليس نصاً
    assert isinstance(pipe.next_publish_time(20, 20), datetime)
    state["next_publish_at"] = "not-a-date"
    assert isinstance(pipe.next_publish_time(20, 20), datetime)
    state["next_publish_at"] = ""
    assert isinstance(pipe.next_publish_time(None, None), datetime)


def test_backoff_seconds_defined_once():
    """إزالة التعريف المكرر لـ _backoff_seconds في upload_pipeline."""
    source = (ROOT / "upload_pipeline.py").read_text(encoding="utf-8")
    assert source.count("def _backoff_seconds(") == 1


# ── og:title: رفض الروابط غير Facebook قبل أي طلب ────────────────────────

def test_og_title_refuses_non_facebook_url_without_network(monkeypatch):
    monkeypatch.setattr(
        app.urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("must not fetch a non-Facebook URL"),
    )
    assert app._facebook_watch_og_title("https://example.com/watch/?v=123") == ""
    assert app._facebook_watch_og_title("http://169.254.169.254/latest/meta-data/") == ""
    assert app._facebook_watch_og_title("https://user:pass@www.facebook.com/watch/?v=1") == ""
    assert app._facebook_watch_og_title("javascript:alert(1)") == ""


# ── facebook_scraper: DoH مع JSON غير متوقع ──────────────────────────────

class _FakeDoHResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def test_doh_resolve_ignores_non_dict_payload(monkeypatch):
    monkeypatch.setattr(
        facebook_scraper.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _FakeDoHResponse(b"[1, 2]"),
    )
    assert facebook_scraper._doh_resolve("www.facebook.com", 443, socket.AF_INET, 5) == []


def test_doh_resolve_ignores_non_dict_answers(monkeypatch):
    payload = json.dumps({"Answer": ["not-a-dict", {"data": "not-an-ip"}]}).encode()
    monkeypatch.setattr(
        facebook_scraper.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _FakeDoHResponse(payload),
    )
    assert facebook_scraper._doh_resolve("www.facebook.com", 443, socket.AF_INET, 5) == []


def test_doh_resolve_parses_valid_answer(monkeypatch):
    payload = json.dumps({"Answer": [{"data": "93.184.216.34"}]}).encode()
    monkeypatch.setattr(
        facebook_scraper.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _FakeDoHResponse(payload),
    )
    result = facebook_scraper._doh_resolve("www.facebook.com", 443, socket.AF_INET, 5)
    assert result == [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


# ── doctor: فحص الأذونات في مجلد التطبيق لا مجلد العمل ───────────────────

# فحص الأذونات قسم 7.5 في doctor.py محروس بـ `if os.name == "posix"` قصداً:
# Windows بلا أوضاع chmod، فالفحص لا يعمل هناك أصلاً. هذا الاختبار كان
# يفشل في CI على windows-latest وحدها (4.49.20) لأن القسم لا يُنفَّذ هناك
# فلا يظهر أي WARN؛ يُتخطَّى على غير POSIX كما في اختبارات الأذونات الأخرى.
@pytest.mark.skipif(os.name != "posix", reason="فحص أذونات الملفات POSIX فقط")
def test_doctor_permission_check_uses_app_directory(monkeypatch, tmp_path):
    """ملف رمز بأذونات واسعة في مجلد التطبيق كان يُفوَّت عندما يكون مجلد
    العمل الحالي مختلفاً، لأن الفحص استخدم مسارات نسبية."""
    monkeypatch.setattr(doctor, "APP_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(doctor, "facebook_network_route", lambda *a, **k: "ipv4")
    token = tmp_path / "account_token.json"
    token.write_text('{"token": "x"}', encoding="utf-8")
    os.chmod(token, 0o644)
    config, config_error = doctor._load_config(str(tmp_path))
    results = []
    doctor._collect_checks(
        lambda name, status, detail="", fix="": results.append((status, name, detail, fix)),
        config,
        config_error,
        str(tmp_path),
    )
    assert any(
        status == doctor.WARN and "account_token.json" in name
        for status, name, _detail, _fix in results
    )


def test_doctor_permission_check_is_posix_only(monkeypatch, tmp_path):
    """محاكاة Windows: قسم الأذونات 7.5 لا يعمل على `nt` قصداً، فلا يظهر
    أي WARN لملف رمز بأذونات واسعة. هذا هو سلوك الإنتاج على Windows، لا
    عطلاً — ويُثبَّت هنا حتى لا يُعاد إدخال اختبار يفترض WARN على Windows.
    (`tempfile` يقرأ `os.O_TEMPORARY` عند `os.name == "nt"`؛ موجود أصلاً
    على مفسر Windows الحقيقي، ويُحاكى هنا على POSIX فقط.)"""
    monkeypatch.setattr(doctor, "APP_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(doctor, "facebook_network_route", lambda *a, **k: "ipv4")
    monkeypatch.setattr(os, "name", "nt")
    if not hasattr(os, "O_TEMPORARY"):
        monkeypatch.setattr(os, "O_TEMPORARY", 0, raising=False)
    token = tmp_path / "account_token.json"
    token.write_text('{"token": "x"}', encoding="utf-8")
    os.chmod(token, 0o644)
    config, config_error = doctor._load_config(str(tmp_path))
    results = []
    doctor._collect_checks(
        lambda name, status, detail="", fix="": results.append((status, name, detail, fix)),
        config,
        config_error,
        str(tmp_path),
    )
    assert not any(
        status == doctor.WARN and "account_token.json" in name
        for status, name, _detail, _fix in results
    )


# ── enable_automation: حالة الملفات من مجلد الإعدادات ────────────────────

def test_show_status_reports_files_next_to_config(tmp_path):
    """مع --config في مجلد آخر كان العرض يبلّغ عن ملفات APP_DIR الخطأ."""
    config_path = tmp_path / "config.ini"
    config_path.write_text("[STUDIO]\nRIGHTS_GATE_MODE = strict\n", encoding="utf-8")
    (tmp_path / "ai_keys.json").write_text("{}", encoding="utf-8")
    (tmp_path / "account_token.json").write_text("{}", encoding="utf-8")
    output = ea.show_status(config_path)
    assert "ai_keys.json: موجود" in output
    assert "account_token.json" in output


# ── تنظيف: كود ميت ───────────────────────────────────────────────────────

def test_dead_config_store_helper_removed():
    import config_store

    assert not hasattr(config_store, "_copy_private")


# ── اتساق الإصدار 4.49.20 ────────────────────────────────────────────────

def test_version_44924_consistent_everywhere():
    assert version.APP_VERSION == "4.49.24"
    assert app.__version__ == "4.49.24"
    header = (ROOT / "fb_youtube_uploader_v49_pro.py").read_text(encoding="utf-8")
    assert "v4.49.24" in header.splitlines()[2]
    assert "**4.49.24**" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "**4.49.24**" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
