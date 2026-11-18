"""انحدار 4.49.23 — تسريب كوكيز Netscape، ترتيب أرشفة الإيصالات،
التحقق من الإعدادات في CLI، تبريد HTTP 400 الفارغ، وأخطاء نظيفة
لملفات الحالة التالفة."""
from __future__ import annotations

import configparser
import http.cookiejar
import io
import json
import logging
import urllib.error
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import ai_client
import diagnostic_report
import enable_automation as ea
import fb_youtube_uploader_v49_pro as app
from safe_logging import redact_sensitive_text

XS_VALUE = "29%3Aabc123%3A-1%3A9999"
COOKIE_LINE = f".facebook.com\tTRUE\t/\tTRUE\t1893456000\txs\t{XS_VALUE}"
STATEMENT = "أنا مالك حقوق هذا المحتوى الأصلي وأتحمّل المسؤولية الكاملة عن نشره."


# ─────────────────────────── تنقيح سطور كوكيز Netscape ───────────────────────────
def test_redact_masks_netscape_cookie_value_raw_tabs() -> None:
    redacted = redact_sensitive_text(COOKIE_LINE)
    assert XS_VALUE not in redacted
    assert "[REDACTED]" in redacted
    assert ".facebook.com" in redacted
    assert "\txs\t" in redacted


def test_redact_masks_netscape_cookie_value_in_load_error(monkeypatch, tmp_path: Path) -> None:
    """LoadError من MozillaCookieJar يضمّن السطر كاملاً بتابات مهرّبة."""
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(
        "# Netscape HTTP Cookie File\n"
        f".facebook.com\tTRUE\t/\tTRUE\tnot-a-number\txs\t{XS_VALUE}\n",
        encoding="utf-8",
    )
    jar = http.cookiejar.MozillaCookieJar(str(cookies_file))
    # cookiejar نفسه يُطلق UserWarning عند فشل التحميل؛ ليس جزءاً من الاختبار.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with pytest.raises(http.cookiejar.LoadError) as excinfo:
            jar.load()
    raw = str(excinfo.value)
    assert XS_VALUE in raw  # من دون التنقيح كان السرّ يتسرب فعلاً
    redacted = redact_sensitive_text(raw)
    assert XS_VALUE not in redacted
    assert "[REDACTED]" in redacted


def test_redact_masks_malformed_flag_cookie_line() -> None:
    bad_flag = f".facebook.com\tYES\t/\tNO\t1893456000\txs\t{XS_VALUE}"
    assert XS_VALUE not in redact_sensitive_text(bad_flag)


def test_redact_leaves_non_cookie_tab_lines_alone() -> None:
    plain = "a\tb\tc\td\te\tf\tg"
    assert redact_sensitive_text(plain) == plain
    assert redact_sensitive_text("# Netscape HTTP Cookie File") == "# Netscape HTTP Cookie File"


def test_diagnostic_excerpt_masks_cookie_line() -> None:
    line = f"تعذّر جلب العنوان: {COOKIE_LINE}"
    excerpt = diagnostic_report._safe_error_excerpt(line)
    assert XS_VALUE not in excerpt
    assert "[REDACTED]" in excerpt


def test_cli_preflight_empty_ambient_config_reports_required_settings(monkeypatch, capsys) -> None:
    """انحدار CI في 4.49.23: نسخة نظيفة بلا config.ini تعطي config فارغاً،
    فيرفض الفحص المسبق برمز 2 برسالتَي VIDEO_DIRECTORY وCLIENT_SECRETS_FILE
    المطلوبتين. هذا سلوك إنتاجي مقصود (لا خطأ اختبار)، ويُثبَّت هنا حتى لا
    يُفسَّر فشله لاحقاً كعطل في CI."""
    _mock_preflight(monkeypatch)
    monkeypatch.setattr(app, "config", configparser.ConfigParser())
    settings = app.build_cli_settings(app.config)
    settings.update({
        "account": "account1",
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "owned",
        "rights_operator_statement": STATEMENT,
        "rights_operator_evidence": "",
    })

    assert app._run_cli_preflight(settings) == 2
    out = capsys.readouterr().out
    assert "VIDEO_DIRECTORY" in out
    assert "CLIENT_SECRETS_FILE" in out


# ─────────────────────────── أرشيف الإيصالات ───────────────────────────
def _receipt(source_id: str, confirmed_at: datetime) -> dict:
    return {
        "source_id": source_id,
        "youtube_id": f"vid{source_id:0>8}"[:11],
        "status": "uploaded",
        "confirmed_at": confirmed_at.isoformat(timespec="seconds"),
    }


def _seed_receipts(tmp_path: Path):
    now = datetime.now(timezone.utc)
    receipts_file = tmp_path / "upload_receipts.json"
    archive_file = tmp_path / "upload_receipts_archive.json"
    receipts_file.write_text(json.dumps({"schema_version": 1, "receipts": {
        "old": _receipt("old", now - timedelta(days=400)),
        "new": _receipt("new", now - timedelta(days=10)),
    }}), encoding="utf-8")
    return receipts_file, archive_file, now


def test_compact_archive_write_failure_loses_nothing(monkeypatch, tmp_path: Path) -> None:
    receipts_file, archive_file, now = _seed_receipts(tmp_path)
    original_write = app._write_json

    def failing_write(path, data, **kwargs):
        if str(path) == str(archive_file):
            raise OSError("قرص ممتلئ")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(app, "_write_json", failing_write)
    with pytest.raises(OSError):
        app.compact_upload_receipts(
            path=str(receipts_file), archive_path=str(archive_file), now=now
        )
    active = json.loads(receipts_file.read_text(encoding="utf-8"))["receipts"]
    assert set(active) == {"old", "new"}  # لم يُقلَّم النشط قبل نجاح الأرشيف
    assert not archive_file.exists()


# ─────────────────────────── التحقق من الإعدادات في CLI ───────────────────────────
def _bad_cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_string(
        "[STUDIO]\nMIN_DURATION = 120\nMAX_DURATION = 60\nCATEGORY = 99\n"
    )
    return cfg


def _mock_preflight(monkeypatch) -> None:
    monkeypatch.setattr(app, "run_automation_preflight", lambda *args: (0, "جاهز"))
    monkeypatch.setattr(
        app, "load_pages",
        lambda: [{"enabled": True, "url": "https://www.facebook.com/x", "name": "x"}],
    )
    monkeypatch.setattr(
        app, "_configure_cli_logger", lambda: logging.getLogger("test-cli-44923")
    )


def test_cli_preflight_rejects_invalid_duration_and_category(monkeypatch, capsys) -> None:
    cfg = _bad_cfg()
    monkeypatch.setattr(app, "config", cfg)
    _mock_preflight(monkeypatch)
    settings = app.build_cli_settings(cfg)

    code = app._run_cli_preflight(settings)

    out = capsys.readouterr().out
    assert code == 2
    assert "MAX_DURATION" in out
    assert "CATEGORY" in out


def test_cli_preflight_corrupt_pages_returns_2_not_traceback(monkeypatch, capsys) -> None:
    _mock_preflight(monkeypatch)
    monkeypatch.setattr(
        app, "load_pages",
        lambda: (_ for _ in ()).throw(RuntimeError("تعذرت قراءة pages.json؛ أُوقف التشغيل.")),
    )
    settings = app.build_cli_settings()

    code = app._run_cli_preflight(settings)

    out = capsys.readouterr().out
    assert code == 2
    assert "--repair-state" in out


def test_run_cli_corrupt_accounts_returns_2_not_traceback(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        app, "build_cli_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("تعذرت قراءة accounts.json؛ أُوقف التشغيل.")),
    )

    assert app.run_cli(preflight_only=True) == 2
    assert "--repair-state" in capsys.readouterr().out


# ─────────────────────────── Gemini: HTTP 400 بجسم فارغ ───────────────────────────
def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://generativelanguage.googleapis.com", code, "err", {}, io.BytesIO(body))


def _keys_file(tmp_path: Path) -> str:
    return str(tmp_path / "ai_keys.json")


def _write_keys(path: str, keys: list) -> None:
    Path(path).write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")


def _read_json(path, default):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _fake_urlopen(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


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


def test_gemini_400_empty_body_gets_short_cooldown_not_day_ban(monkeypatch, tmp_path: Path) -> None:
    keys_file = _keys_file(tmp_path)
    _write_keys(keys_file, [{"key": "k1", "exhausted_until": "", "last_error": ""}])
    _fake_urlopen(monkeypatch, [_http_error(400, b"")])
    before = datetime.now()

    text, updated = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", keys_file, _read_json, _write_json)

    assert text is None
    until = datetime.fromisoformat(updated[0]["exhausted_until"])
    assert 9 <= (until - before).total_seconds() / 60 <= 11
    assert "غير صالح" not in updated[0]["last_error"]


def test_gemini_400_with_key_problem_still_marks_invalid(monkeypatch, tmp_path: Path) -> None:
    keys_file = _keys_file(tmp_path)
    _write_keys(keys_file, [
        {"key": "bad", "exhausted_until": "", "last_error": ""},
        {"key": "good", "exhausted_until": "", "last_error": ""},
    ])
    _fake_urlopen(monkeypatch, [
        _http_error(400, b'{"error": {"message": "API key not valid. Pass a valid key."}}'),
        "تمام",
    ])

    text, updated = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", keys_file, _read_json, _write_json)

    assert text == "تمام"
    until = datetime.fromisoformat(updated[0]["exhausted_until"])
    assert until > datetime.now() + timedelta(hours=1)  # حظر حتى منتصف الليل
    assert "غير صالح" in updated[0]["last_error"]


# ─────────────────────────── enable_automation: config تالف ───────────────────────────
def test_enable_automation_corrupt_config_show_returns_2(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text("[STUDIO\nRIGHTS_GATE_MODE = strict\n", encoding="utf-8")

    assert ea.main(["--show", "--config", str(config_path)]) == 2
    assert "config.ini" in capsys.readouterr().err


def test_enable_automation_corrupt_config_apply_returns_2(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text("not ini at all [[[\n", encoding="utf-8")

    assert ea.main([
        "--config", str(config_path), "--basis", "owned",
        "--statement", STATEMENT, "--dry-run",
    ]) == 2
    # الملف نفسه لم يُمسّ
    assert config_path.read_text(encoding="utf-8") == "not ini at all [[[\n"
