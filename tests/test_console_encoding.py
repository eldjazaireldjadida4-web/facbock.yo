"""انحدار 4.49.28 — إخراج UTF-8 على طرفية Windows (cp1252).

السبب: طرفية Windows الافتراضية cp1252، فطباعة النص العربي والسهم
(→) تُسقط التطبيق بـ UnicodeEncodeError قبل أي عمل (تشغيل الإصدار
44340376582: --preflight انهار برمز 1 بدل 2 المتوقع، و--version/--help
بالطريقة نفسها). الإصلاح يعيد تهيئة stdout/stderr إلى UTF-8 مع
errors="replace" في بداية main() لكل نقطة دخول.

تُجبر هذه الاختبارات الترميز على cp1252 صارماً (كما في طرفية Windows)
وتتحقق أن نقاط الدخول لا تنهار: نسخة عملية فرعية لكل أمر، ونسخة
داخلية للفحص المسبق بتيارات cp1252 حقيقية.
"""
from __future__ import annotations

import configparser
import io
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app
import version
from console_encoding import reconfigure_std_streams_utf8

ROOT = Path(__file__).resolve().parents[1]
STATEMENT = "أنا مالك حقوق هذا المحتوى الأصلي وأتحمّل المسؤولية الكاملة عن نشره."

# ─────────────────────────── اختبارات وحدة للمُعيد ───────────────────────────


class _RecordingStream:
    """تيار وهمي يسجّل استدعاءات reconfigure."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def reconfigure(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_reconfigure_sets_utf8_replace_on_both_streams(monkeypatch) -> None:
    out, err = _RecordingStream(), _RecordingStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    reconfigure_std_streams_utf8()

    assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert err.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_reconfigure_noops_on_streams_without_reconfigure(monkeypatch) -> None:
    """بدائل الاختبار والأنابيب بلا reconfigure: لا رفع ولا تغيير."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", object())

    reconfigure_std_streams_utf8()  # يجب ألا يرفع


def test_reconfigure_noops_when_stream_is_none(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    reconfigure_std_streams_utf8()  # يجب ألا يرفع


def test_reconfigure_survives_reconfigure_raising(monkeypatch) -> None:
    class _Raising:
        def reconfigure(self, **kwargs) -> None:
            raise ValueError("تيار مغلق")

    monkeypatch.setattr(sys, "stdout", _Raising())
    monkeypatch.setattr(sys, "stderr", _Raising())

    reconfigure_std_streams_utf8()  # يجب ألا يرفع؛ الإقلاع لا يتوقف


def test_reconfigure_real_cp1252_wrapper_accepts_arrows_and_arabic(monkeypatch) -> None:
    """تيار TextIOWrapper حقيقي بترميز cp1252 صارم: بعد الإصلاح يقبل → والعربية."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    reconfigure_std_streams_utf8()

    assert stream.encoding == "utf-8"
    stream.write("FB → YouTube Uploader — اختبار\n")
    stream.flush()
    assert "→".encode("utf-8") in buf.getvalue()
    assert "اختبار".encode("utf-8") in buf.getvalue()


# ─────────────────────────── عمليات فرعية بـ cp1252 صارم ───────────────────────────


def _run_cp1252(script: str, *args: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """شغّل سكربتاً ببيئة طرفية Windows: stdout/stderr‏ cp1252 صارم."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env.pop("PYTHONUTF8", None)  # كان سيفرض UTF-8 ويُخفي العطل
    return subprocess.run(
        [sys.executable, str(cwd / script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        env=env,
        timeout=timeout,
    )


def _assert_no_encoding_crash(result: subprocess.CompletedProcess) -> None:
    assert "UnicodeEncodeError" not in result.stderr
    assert "UnicodeDecodeError" not in result.stderr
    assert "Traceback (most recent call last)" not in result.stderr


def test_version_exits_0_under_strict_cp1252() -> None:
    """انحدار تشغيل الإصدار: --version كان ينهار على '→' في الموضع 3."""
    result = _run_cp1252("fb_youtube_uploader_v49_pro.py", "--version", cwd=ROOT, timeout=120)

    assert result.returncode == 0
    _assert_no_encoding_crash(result)
    assert "FB → YouTube Uploader" in result.stdout
    assert version.APP_VERSION in result.stdout


def test_help_exits_0_under_strict_cp1252() -> None:
    """--help كان ينهار على أحرف عربية في المواضع 132-135."""
    result = _run_cp1252("fb_youtube_uploader_v49_pro.py", "--help", cwd=ROOT, timeout=120)

    assert result.returncode == 0
    _assert_no_encoding_crash(result)
    assert "fb-youtube-uploader" in result.stdout


def test_doctor_exits_without_traceback_under_strict_cp1252() -> None:
    """تقرير doctor عربي بالكامل؛ رمز 0 أو 1 شرعي، والانهيار مرفوض."""
    result = _run_cp1252("doctor.py", cwd=ROOT, timeout=180)

    assert result.returncode in (0, 1)
    _assert_no_encoding_crash(result)
    assert "فحص جاهزية" in result.stdout


def test_enable_automation_show_exits_0_under_strict_cp1252() -> None:
    result = _run_cp1252("enable_automation.py", "--show", cwd=ROOT, timeout=120)

    assert result.returncode == 0
    _assert_no_encoding_crash(result)


# ─────────────────────────── الفحص المسبق في نسخة نظيفة ───────────────────────────


def _clean_copy(tmp_path: Path) -> Path:
    """انسخ الملفات المتعقَّبة وغير المتعقَّبة (عدا المُتجاهَلة) كما هي على
    القرص (مع الإصلاح) بلا config.ini.

    نسخة نظيفة كما في CI: config.ini مُتجاهَل في git فلا يصل إلى النسخة،
    فيرفض الفحص المسبق برمز 2. انسخ من شجرة العمل لا من HEAD حتى يشمل
    الإصلاح غير المُثبَّت بعد — بما فيه الوحدة الجديدة console_encoding.py
    التي لا يعرفها HEAD أصلاً (لهذا السبب لا يكفي `git archive`).
    """
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=str(ROOT),
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        pytest.skip("git ls-files غير متاح؛ لا يمكن بناء نسخة نظيفة")
    dest = tmp_path / "clean"
    dest.mkdir()
    for rel in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if not rel:
            continue
        src = ROOT / rel
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    assert not (dest / "config.ini").exists(), "النسخة النظيفة يجب ألا تحوي config.ini"
    return dest


def test_preflight_exits_2_in_clean_copy_under_strict_cp1252(tmp_path: Path) -> None:
    """انحدار تشغيل الإصدار 44340376582: --preflight في نسخة نظيفة بلا
    config.ini كان ينهار برمز 1 (UnicodeEncodeError) بدل رمز 2 المتوقع
    (بيانات اعتماد مفقودة)."""
    clean = _clean_copy(tmp_path)
    result = _run_cp1252(
        "fb_youtube_uploader_v49_pro.py", "--preflight", cwd=clean, timeout=300
    )

    assert result.returncode == 2, (
        f"preflight exit was {result.returncode}\n"
        f"--- child stdout ---\n{result.stdout}\n--- child stderr ---\n{result.stderr}"
    )
    _assert_no_encoding_crash(result)
    assert "ModuleNotFoundError" not in result.stderr
    assert "VIDEO_DIRECTORY" in result.stdout


# ─────────────────────────── الفحص المسبق داخلياً بتيارات cp1252 ───────────────────────────


def test_preflight_prints_arabic_under_strict_cp1252_streams(monkeypatch) -> None:
    """main(["--preflight"]) مع stdout/stderr‏ cp1252 صارمين حقيقيين:
    يعيد المُعيد تهيئتهما قبل الطباعة، فيطبع التقرير العربي ويرجع 2."""
    buf_out, buf_err = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(buf_out, encoding="cp1252", errors="strict")
    err = io.TextIOWrapper(buf_err, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(app, "run_automation_preflight", lambda *args: (0, "جاهز"))
    monkeypatch.setattr(
        app,
        "load_pages",
        lambda: [{"enabled": True, "url": "https://www.facebook.com/x", "name": "x"}],
    )
    monkeypatch.setattr(
        app, "_configure_cli_logger", lambda: logging.getLogger("test-cli-44928")
    )
    monkeypatch.setattr(app, "config", configparser.ConfigParser())

    assert app.main(["--preflight"]) == 2

    out.flush()
    err.flush()
    text = buf_out.getvalue().decode("utf-8")
    assert "VIDEO_DIRECTORY" in text
    assert "CLIENT_SECRETS_FILE" in text
    assert "UnicodeEncodeError" not in buf_err.getvalue().decode("utf-8", errors="replace")
