# -*- coding: utf-8 -*-
"""انحدار 4.49.25 — جولة تدقيق التقوية: 15 إصلاحاً.

1. repair-state أعمى عن الملفات البنيوية التالفة + سجل الحقوق خارج STATE_LEDGERS
2. ذاكرة AI تتجاهل العنوان/الأسلوب/النموذج
3. socks4 يمرّ التحقق ويُرفض عند التشغيل
4. VIDEO_DIRECTORY ملف عادي يُسقط الاستيراد
5. DNS فرع الوكيل بلا حدّ
6. doctor يمرّر config.ini رمزياً وملف توكن مجلداً
7. enable_automation: فشل كتابة بلا معالجة
8. AI.MODEL بلا تحقق
9. cut_shorts_clip يفشل على أبعاد فردية
10. finalize_video / apply_logo_overlay يقصّران ملفاً مبتوراً بصمت
11. find_hot_segment يعدّل على الثواني الحاضرة فقط
12. compact_upload_receipts يسقط الإيصال الأحدث
13. الإيقاف يُتجاهل بعد انتظار الحصة مع المراجعة مفعّلة
14. تنزيل نموذج التفريغ المحلي غير قابل للمقاطعة
"""
from __future__ import annotations

import configparser
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import diagnostic_report
import doctor
import enable_automation as ea
import fb_youtube_uploader_v49_pro as app
import local_transcription as transcription
import media_processing
from facebook_scraper import facebook_network_route_detailed
from pipeline_errors import PipelineFailure
from settings_validation import validate_configuration
from upload_pipeline import finalize_video, validate_media

STATEMENT = "أنا مالك حقوق هذا المحتوى الأصلي وأتحمّل المسؤولية الكاملة عن نشره."


def _valid_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({
        "SETTINGS": {
            "VIDEO_DIRECTORY": "videos",
            "CLIENT_SECRETS_FILE": "client_secrets.json",
            "PRIVACY": "private",
            "DRY_RUN": "false",
        },
        "STUDIO": {
            "RIGHTS_GATE_MODE": "strict",
            "RIGHTS_OPERATOR_BASIS": "owned",
            "REVIEW_BEFORE_UPLOAD": "true",
            "SCHEDULE": "false",
            "SCHEDULE_MIN": "20",
            "SCHEDULE_MAX": "60",
            "CATEGORY": "25",
        },
        "SAFETY": {"ALLOW_PUBLIC_AUTOMATION": "false"},
    })
    return cfg


def _ledger_paths(monkeypatch, tmp_path: Path, **overrides):
    """اختراق ثوابت مسارات السجلات كما تفعل اختبارات الإصلاح القديمة."""
    names = {
        "FAILED_QUEUE_FILE": tmp_path / "failed_queue.json",
        "UPLOAD_RECEIPTS_FILE": tmp_path / "upload_receipts.json",
        "PAGES_FILE": tmp_path / "pages.json",
        "ACCOUNTS_FILE": tmp_path / "accounts.json",
        "STATE_FILE": tmp_path / "state.json",
        "CHANNEL_INDEX_FILE": tmp_path / "channel_video_index.json",
        "RIGHTS_MANIFEST_FILE": tmp_path / "rights_manifest.json",
    }
    names.update(overrides)
    for name, path in names.items():
        monkeypatch.setattr(app, name, str(path))
    return names


def _receipt(source_id, youtube_id, confirmed_at):
    return {
        "youtube_id": youtube_id,
        "source_id": source_id,
        "confirmed_at": confirmed_at,
        "status": "confirmed",
    }


# ── 1+2) repair-state البنيوي + سجل الحقوق ──────────────────────────────
def test_repair_quarantines_receipts_with_bad_schema_version(monkeypatch, tmp_path: Path):
    _ledger_paths(monkeypatch, tmp_path)
    (tmp_path / "upload_receipts.json").write_text(
        json.dumps({"schema_version": 2, "receipts": {}}), encoding="utf-8")

    actions = app.repair_state_files()

    quarantined = [a for a in actions if a["file"] == "upload_receipts.json"]
    assert quarantined and quarantined[0]["action"] == "quarantine"
    assert not (tmp_path / "upload_receipts.json").exists()


def test_repair_quarantines_receipts_with_invalid_youtube_id(monkeypatch, tmp_path: Path):
    _ledger_paths(monkeypatch, tmp_path)
    (tmp_path / "upload_receipts.json").write_text(json.dumps({
        "schema_version": 1,
        "receipts": {"src1": _receipt("src1", "not-a-video-id", "2024-01-01T00:00:00+00:00")},
    }), encoding="utf-8")

    actions = app.repair_state_files()

    assert any(a["file"] == "upload_receipts.json" and a["action"] == "quarantine"
               for a in actions)


def test_repair_quarantines_queue_entry_without_url(monkeypatch, tmp_path: Path):
    _ledger_paths(monkeypatch, tmp_path)
    (tmp_path / "failed_queue.json").write_text(
        json.dumps([{"id": "x", "url": ""}]), encoding="utf-8")

    actions = app.repair_state_files()

    assert any(a["file"] == "failed_queue.json" and a["action"] == "quarantine"
               for a in actions)


def test_repair_quarantines_accounts_with_blank_entry(monkeypatch, tmp_path: Path):
    _ledger_paths(monkeypatch, tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps(["account1", "  "]), encoding="utf-8")

    actions = app.repair_state_files()

    assert any(a["file"] == "accounts.json" and a["action"] == "quarantine"
               for a in actions)


def test_repair_quarantines_corrupt_rights_manifest(monkeypatch, tmp_path: Path, capsys):
    _ledger_paths(monkeypatch, tmp_path)
    (tmp_path / "rights_manifest.json").write_text(
        json.dumps({"version": 1, "items": "not-a-dict"}), encoding="utf-8")

    code = app._repair_state_cli()

    assert code == 0
    output = capsys.readouterr().out
    assert "عُزل rights_manifest.json" in output
    assert "عزل rights_manifest.json يُلغي سجل الحقوق" in output
    assert not (tmp_path / "rights_manifest.json").exists()


def test_repair_restores_structurally_valid_backup_receipts(monkeypatch, tmp_path: Path):
    _ledger_paths(monkeypatch, tmp_path)
    (tmp_path / "upload_receipts.json").write_text(json.dumps({
        "schema_version": 1,
        "receipts": {"src1": _receipt("src1", "garbage", "2024-01-01T00:00:00+00:00")},
    }), encoding="utf-8")
    (tmp_path / "upload_receipts.json.bak1").write_text(json.dumps({
        "schema_version": 1,
        "receipts": {"src1": _receipt("src1", "dQw4w9WgXcQ", "2024-01-01T00:00:00+00:00")},
    }), encoding="utf-8")

    actions = app.repair_state_files()

    assert actions == [{"file": "upload_receipts.json", "action": "restore_from_backup"}]


def test_repair_leaves_structurally_valid_ledgers_alone(monkeypatch, tmp_path: Path):
    _ledger_paths(monkeypatch, tmp_path)
    (tmp_path / "upload_receipts.json").write_text(json.dumps({
        "schema_version": 1,
        "receipts": {"src1": _receipt("src1", "dQw4w9WgXcQ", "2024-01-01T00:00:00+00:00")},
    }), encoding="utf-8")
    (tmp_path / "failed_queue.json").write_text(
        json.dumps([{"id": "x", "url": "https://example.invalid/v/1"}]), encoding="utf-8")
    (tmp_path / "accounts.json").write_text(json.dumps(["account1"]), encoding="utf-8")
    (tmp_path / "rights_manifest.json").write_text(
        json.dumps({"version": 1, "items": {}}), encoding="utf-8")

    assert app.repair_state_files() == []
    for name in ("upload_receipts.json", "failed_queue.json",
                 "accounts.json", "rights_manifest.json"):
        assert (tmp_path / name).exists()


# ── 3) ذاكرة AI: بصمة المطالبة ──────────────────────────────────────────
def test_ai_metadata_regenerates_when_title_or_style_changes(monkeypatch):
    state = {"ai_cache": {}}
    calls = []
    worker = object.__new__(app.App)
    worker._log = lambda *_args, **_kwargs: None
    monkeypatch.setattr(app, "load_state", lambda: state)
    monkeypatch.setattr(app, "save_state", lambda updated: state.update(updated))
    monkeypatch.setattr(app, "top_performing_titles", lambda *_args, **_kwargs: [])

    def generate(title, desc, *_args, **_kwargs):
        calls.append((title, _kwargs.get("performance_hint", "")))
        return {"title": title, "description": f"desc for {title}", "tags": []}

    monkeypatch.setattr(app, "ai_generate_metadata", generate)
    item = {"id": "video-1", "page": "Page"}

    first = worker._ai_metadata(item, {"title": "T1", "desc": "", "transcript": "t"}, {})
    cached = worker._ai_metadata(item, {"title": "T1", "desc": "", "transcript": "t"}, {})
    retitled = worker._ai_metadata(item, {"title": "T2", "desc": "", "transcript": "t"}, {})
    restyled = worker._ai_metadata(
        item, {"title": "T2", "desc": "", "transcript": "t"},
        {"ai_style": "إخباري رسمي"},
    )

    assert len(calls) == 3  # توليد أول + عنوان مصحح + أسلوب مختلف؛ الثاني من الذاكرة
    assert first["description"] == cached["description"]
    assert retitled["description"] == "desc for T2"
    assert restyled["description"] == "desc for T2"
    assert state["ai_cache"]["video-1"]["_transcript_hash"]
    assert state["ai_cache"]["video-1"]["_prompt_hash"]


# ── 4) socks4 مدعوم في كل مكان ──────────────────────────────────────────
@pytest.mark.parametrize("scheme", ["http", "https", "socks4", "socks5", "socks5h"])
def test_proxy_scheme_agreement_across_validator_and_route(scheme):
    cfg = _valid_config()
    cfg.add_section("WATCH")
    cfg.set("WATCH", "PROXY", f"{scheme}://127.0.0.1:1080")

    assert not any("PROXY" in error for error in validate_configuration(cfg))

    _route, failure = facebook_network_route_detailed(f"{scheme}://127.0.0.1:1080", timeout=5)
    # المنفذ المحلي مغلق غالباً فيُرفض الاتصال، لكن المخطط نفسه لا يُصنَّف invalid
    assert failure.get("proxy") in {"unreachable", None}


def test_socks4_defaults_to_port_1080_everywhere():
    assert app._selenium_proxy_server("socks4://proxy.local") == "socks4://proxy.local:1080"
    assert app._selenium_proxy_server("socks4://proxy.local:1081") == "socks4://proxy.local:1081"
    assert diagnostic_report._proxy_endpoint("socks4://proxy.local") == (
        "configured", "proxy.local", 1080)
    with pytest.raises(ValueError, match="socks4"):
        app._selenium_proxy_server("socks4a://proxy.local")


# ── 5) VIDEO_DIRECTORY ملف عادي ─────────────────────────────────────────
def test_validate_configuration_rejects_file_backed_video_directory(tmp_path: Path):
    backing = tmp_path / "videos"
    backing.write_text("not a directory", encoding="utf-8")
    cfg = _valid_config()
    cfg.set("SETTINGS", "VIDEO_DIRECTORY", str(backing))

    errors = validate_configuration(cfg)

    assert any("VIDEO_DIRECTORY" in error and "ليس مجلداً" in error for error in errors)


def test_validate_configuration_accepts_real_video_directory(tmp_path: Path):
    cfg = _valid_config()
    cfg.set("SETTINGS", "VIDEO_DIRECTORY", str(tmp_path))

    assert not any("VIDEO_DIRECTORY" in e for e in validate_configuration(cfg))


# ── 6) DNS فرع الوكيل محدود المهلة ──────────────────────────────────────
def test_proxy_dns_is_bounded_by_timeout(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo

    def stalled_getaddrinfo(*args, **kwargs):
        time.sleep(30)
        return real_getaddrinfo(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", stalled_getaddrinfo)
    started = time.monotonic()
    route, failure = facebook_network_route_detailed(
        "socks5://proxy.example.invalid:1080", timeout=4)
    elapsed = time.monotonic() - started

    assert route is None
    assert failure.get("proxy") == "unreachable"
    assert elapsed < 4 + 2  # كان 30 ثانية قبل الإصلاح رغم مهلة 5 ثوانٍ


# ── 7+8) doctor: رابط رمزي + مجلد توكن ──────────────────────────────────
def test_doctor_reports_bad_for_symlinked_config(tmp_path: Path, monkeypatch):
    real = tmp_path / "real.ini"
    real.write_text("[SETTINGS]\nVIDEO_DIRECTORY = videos\n", encoding="utf-8")
    os.symlink(real, tmp_path / "config.ini")
    monkeypatch.setattr(doctor, "APP_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(doctor, "facebook_network_route", lambda *a, **k: "ipv4")

    config, config_error = doctor._load_config(str(tmp_path))
    assert config_error is not None
    assert "symbolic-link" in str(config_error)

    results = []
    doctor._collect_checks(
        lambda name, status, detail="", fix="": results.append((status, name, detail, fix)),
        config, config_error, str(tmp_path),
    )
    config_row = next(row for row in results if row[1] == "ملف config.ini")
    assert config_row[0] == doctor.BAD


def test_doctor_ignores_token_directory(tmp_path: Path, monkeypatch):
    (tmp_path / "account1_token.json").mkdir()
    monkeypatch.setattr(doctor, "APP_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(doctor, "facebook_network_route", lambda *a, **k: "ipv4")

    config, config_error = doctor._load_config(str(tmp_path))
    results = []
    doctor._collect_checks(
        lambda name, status, detail="", fix="": results.append((status, name, detail, fix)),
        config, config_error, str(tmp_path),
    )
    token_row = next(row for row in results if row[1] == "ربط قناة YouTube")
    assert token_row[0] == doctor.INFO  # مجلد لا يُعدّ توكناً مربوطاً


def test_diagnostic_report_rejects_symlinked_config(tmp_path: Path):
    real = tmp_path / "real.ini"
    real.write_text("[SETTINGS]\nVIDEO_DIRECTORY = videos\n", encoding="utf-8")
    os.symlink(real, tmp_path / "config.ini")

    _parser, state = diagnostic_report._load_config(tmp_path)

    assert state == "invalid"


# ── 9) enable_automation: فشل الكتابة ───────────────────────────────────
def test_enable_automation_write_failure_returns_2_cleanly(tmp_path: Path, monkeypatch, capsys):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[STUDIO]\nRIGHTS_GATE_MODE = strict\n", encoding="utf-8")

    def failing_write(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(ea, "write_config_atomically", failing_write)

    code = ea.main([
        "--config", str(config_path), "--basis", "owned",
        "--statement", STATEMENT, "--yes",
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert "تعذرت كتابة config.ini" in captured.err
    assert "Traceback" not in captured.err


# ── 10) AI.MODEL ────────────────────────────────────────────────────────
def test_validate_configuration_rejects_unknown_ai_model():
    cfg = _valid_config()
    cfg.add_section("AI")
    cfg.set("AI", "MODEL", "gemini-9.9-nope")

    assert any("AI.MODEL" in error for error in validate_configuration(cfg))


def test_validate_configuration_accepts_supported_ai_model():
    cfg = _valid_config()
    cfg.add_section("AI")
    cfg.set("AI", "MODEL", "gemini-2.5-flash")

    assert not any("AI.MODEL" in error for error in validate_configuration(cfg))


# ── 11) قص Shorts على أبعاد فردية ───────────────────────────────────────
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg غير متوفر في هذه البيئة")
def test_cut_shorts_clip_succeeds_on_odd_dimensions(tmp_path: Path):
    source = tmp_path / "odd.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=851x481:rate=30:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True,
    )

    output = media_processing.cut_shorts_clip(str(source), 0, 1, log_cb=lambda _m: None)

    assert output is not None
    width, height, _ = media_processing.probe_video(output)
    assert width % 2 == 0 and height % 2 == 0
    assert (width, height) == (850, 480)


# ── 12) المدة بعد التنظيف/الشعار ────────────────────────────────────────
@pytest.fixture
def truncated_video(tmp_path: Path):
    full = tmp_path / "full.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30:duration=10",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(full)],
        check=True, capture_output=True,
    )
    data = full.read_bytes()
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(data[: int(len(data) * 0.55)])
    return truncated


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg غير متوفر في هذه البيئة")
def test_finalize_video_rejects_truncated_file(truncated_video: Path):
    # الفرضية: ffprobe يعلن مدة الترويسة الكاملة فيجتاز validate_media
    assert validate_media(str(truncated_video))[0] is True

    with pytest.raises(PipelineFailure) as excinfo:
        finalize_video(str(truncated_video))
    assert excinfo.value.code == "output_duration_mismatch"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg غير متوفر في هذه البيئة")
def test_logo_overlay_rejects_truncated_file(truncated_video: Path, tmp_path: Path):
    logo = tmp_path / "logo.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=red:size=256x128", "-frames:v", "1", str(logo)],
        check=True, capture_output=True,
    )
    logs = []

    output = media_processing.apply_logo_overlay(
        str(truncated_video), str(logo), log_cb=logs.append)

    assert output is None
    assert any("مدة الإخراج" in message for message in logs)


# ── 13) find_hot_segment: الثواني الغائبة صمت ──────────────────────────
def test_find_hot_segment_prefers_dense_audio_over_lone_spike():
    rms = {0: -10.0}  # ذروة ثانية واحدة
    rms.update({s: -40.0 for s in range(60, 90)})  # 30 ثانية متوسطة الارتفاع

    start, span = media_processing.find_hot_segment(
        rms, clip_seconds=58, min_source=95, source_duration=120)

    assert span == 58
    assert start >= 32  # النافذة التي تغطي الكتلة الكثيفة، لا الذروة عند 0


def test_find_hot_segment_dense_case_unchanged():
    rms = {s: -40.0 for s in range(0, 120)}
    for s in range(60, 70):
        rms[s] = -10.0
    start, span = media_processing.find_hot_segment(
        rms, clip_seconds=58, min_source=95, source_duration=120)
    assert span == 58
    assert 12 <= start <= 70


def test_find_hot_segment_empty_rms_is_none():
    assert media_processing.find_hot_segment({}, source_duration=200) is None


# ── 14) الأرشيف يحتفظ بالإيصال الأحدث ───────────────────────────────────
def test_compaction_keeps_newer_receipt_on_reupload(monkeypatch, tmp_path: Path):
    receipts_path = tmp_path / "upload_receipts.json"
    archive_path = tmp_path / "archive.json"
    monkeypatch.setattr(app, "UPLOAD_RECEIPTS_FILE", str(receipts_path))
    monkeypatch.setattr(app, "UPLOAD_RECEIPTS_ARCHIVE_FILE", str(archive_path))
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    old = _receipt("src1", "AAAAAAAAAAA", "2020-01-01T00:00:00+00:00")
    receipts_path.write_text(json.dumps(
        {"schema_version": 1, "receipts": {"src1": old}}), encoding="utf-8")
    assert app.compact_upload_receipts(now=now, path=str(receipts_path),
                                       archive_path=str(archive_path)) == 1

    new = _receipt("src1", "BBBBBBBBBBB", "2023-06-01T00:00:00+00:00")
    receipts_path.write_text(json.dumps(
        {"schema_version": 1, "receipts": {"src1": new}}), encoding="utf-8")
    assert app.compact_upload_receipts(now=now, path=str(receipts_path),
                                       archive_path=str(archive_path)) == 1

    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive["receipts"]["src1"]["youtube_id"] == "BBBBBBBBBBB"


# ── 15) الإيقاف بعد انتظار الحصة ────────────────────────────────────────
def test_wait_for_quota_reset_returns_false_when_stopped():
    worker = object.__new__(app.App)
    worker._log = lambda *_args, **_kwargs: None
    worker._uiq = queue.Queue()
    worker._stop = threading.Event()
    worker._stop.set()
    refreshed = []
    quota = SimpleNamespace(seconds_until_reset=lambda: 3600,
                            _refresh=lambda: refreshed.append(True))

    assert worker._wait_for_quota_reset(quota) is False
    assert refreshed == []  # لا تحديث للحصة بعد إيقاف مطلوب


def test_wait_for_quota_reset_refreshes_when_not_stopped():
    worker = object.__new__(app.App)
    worker._log = lambda *_args, **_kwargs: None
    worker._uiq = queue.Queue()
    worker._stop = threading.Event()
    refreshed = []
    quota = SimpleNamespace(seconds_until_reset=lambda: 0,
                            _refresh=lambda: refreshed.append(True))

    assert worker._wait_for_quota_reset(quota) is True
    assert refreshed == [True]


# ── 16) التفريغ المحلي: إيقاف بعد تنزيل النموذج ────────────────────────
def test_transcription_aborts_after_model_load_when_stopped(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    stop_event = threading.Event()
    stop_event.set()
    transcribe_called = []

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            transcribe_called.append(True)
            return iter([SimpleNamespace(text="should not run")]), SimpleNamespace(language="ar")

    monkeypatch.setattr(transcription, "_load_model", lambda _size: FakeModel())

    with pytest.raises(transcription.TranscriptionCancelled):
        transcription.transcribe_video_file(video, stop_event=stop_event)

    assert transcribe_called == []
