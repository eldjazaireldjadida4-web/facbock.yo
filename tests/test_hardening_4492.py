"""انحدار 4.49.2 — حالة مخزنة تُفقد بصمت، ومخرجات ffmpeg تُقرأ خطأ.

المحور الأول: قرارات المستخدم (حذف كل الصفحات) وبيانات الحماية
(upload_result_unknown) كانت تُطمس عند القراءة أو عند إعادة الجدولة.
المحور الثاني: فشل ffmpeg كان يُفسَّر «لا صوت» و«لا أبعاد»، فيتعطل القص
أو يتوقف الرفع برسالة مضللة بلا سبب حقيقي.
"""
from __future__ import annotations

import json
import os
import tempfile
import types
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app
from content_review_bridge import _review_command


# ─────────────────────────── صفحات المصدر ───────────────────────────
def test_empty_pages_file_is_a_decision_not_a_first_run(tmp_path: Path, monkeypatch) -> None:
    pages_file = tmp_path / "pages.json"
    pages_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(app, "PAGES_FILE", str(pages_file))

    pages = app.load_pages()

    assert pages == []
    assert json.loads(pages_file.read_text(encoding="utf-8")) == []


def test_missing_pages_file_seeds_defaults_once(tmp_path: Path, monkeypatch) -> None:
    pages_file = tmp_path / "pages.json"
    monkeypatch.setattr(app, "PAGES_FILE", str(pages_file))

    pages = app.load_pages()

    assert pages and all(page["url"].startswith("https://www.facebook.com/") for page in pages)
    assert json.loads(pages_file.read_text(encoding="utf-8"))


def test_invalid_page_entries_are_dropped_instead_of_crashing_startup(tmp_path: Path, monkeypatch) -> None:
    pages_file = tmp_path / "pages.json"
    pages_file.write_text(
        json.dumps([
            "https://facebook.com/not-a-dict",
            {"url": "https://www.youtube.com/@wrong"},
            {"url": "https://www.facebook.com/GoodPage", "scroll": "many", "limit": 9999},
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "PAGES_FILE", str(pages_file))

    pages = app.load_pages()

    assert len(pages) == 1
    assert pages[0]["url"].startswith("https://www.facebook.com/GoodPage")
    assert pages[0]["scroll"] == 5
    assert pages[0]["limit"] == 100
    assert pages[0]["enabled"] is True


# ─────────────────────────── ذاكرة العرض والإحصاءات ───────────────────────────
def test_corrupt_page_status_cannot_block_startup(tmp_path: Path, monkeypatch) -> None:
    status_file = tmp_path / "page_status.json"
    status_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(app, "PAGE_STATUS_FILE", str(status_file))

    assert app.load_page_status() == {}


@pytest.mark.parametrize(
    "stored",
    [
        {"total_uploaded": "12", "daily": None},
        {"total_uploaded": None, "daily": {"date": "2026-01-01", "uploaded": "5"}},
        {"daily": ["wrong"]},
    ],
)
def test_stats_are_coerced_instead_of_crashing_the_cycle(tmp_path: Path, monkeypatch, stored) -> None:
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(app, "STATS_FILE", str(stats_file))

    stats = app.load_stats()
    app.bump_daily_uploads(stats)

    assert isinstance(stats["total_uploaded"], int)
    assert isinstance(stats["daily"], dict)
    assert isinstance(stats["daily"]["uploaded"], int)


def test_failed_queue_coerces_string_attempts(tmp_path: Path, monkeypatch) -> None:
    queue_file = tmp_path / "failed_queue.json"
    queue_file.write_text(
        json.dumps([{"id": "reel-1", "url": "https://www.facebook.com/reel/1", "attempts": "3"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "FAILED_QUEUE_FILE", str(queue_file))

    items = app.load_failed_queue()

    assert items[0]["attempts"] == 3


# ─────────────────────────── إعادة جدولة العناصر ───────────────────────────
def _bare_app():
    instance = app.App.__new__(app.App)
    instance._log = lambda *_args, **_kwargs: None
    return instance


def test_requeue_keeps_metadata_and_protected_error() -> None:
    instance = _bare_app()
    failed_q = []
    pending = [{
        "id": "reel-9",
        "url": "https://www.facebook.com/reel/9",
        "page": "Page",
        "attempts": 2,
        "last_error": app.UPLOAD_RESULT_UNKNOWN,
        "upload_title": "عنوان محفوظ",
        "upload_tags": ["a", "b"],
        "source_sha256": "abc",
        "retry": True,
    }]

    instance._requeue_unprocessed(failed_q, pending)

    assert len(failed_q) == 1
    entry = failed_q[0]
    assert entry["last_error"] == app.UPLOAD_RESULT_UNKNOWN
    assert entry["upload_title"] == "عنوان محفوظ"
    assert entry["upload_tags"] == ["a", "b"]
    assert entry["source_sha256"] == "abc"
    assert "retry" not in entry
    assert entry["failed_at"]


def test_requeue_marks_only_unlabelled_items_as_deferred() -> None:
    instance = _bare_app()
    failed_q = []

    instance._requeue_unprocessed(failed_q, [{"id": "x", "url": "https://www.facebook.com/reel/1"}])

    assert failed_q[0]["last_error"] == "مؤجل"


# ─────────────────────────── نسخة تشخيصية لملف تالف ───────────────────────────
def test_corrupt_ledger_leaves_a_diagnostic_copy(tmp_path: Path) -> None:
    ledger = tmp_path / "receipts.json"
    ledger.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="تعذرت قراءة"):
        app._read_json(str(ledger), {}, expected_type=dict, fail_closed=True)

    copies = list(tmp_path.glob("receipts.json.corrupt-*"))
    assert copies, "corrupt state must be preserved for diagnosis"
    assert copies[0].read_text(encoding="utf-8") == "{broken"


# ─────────────────────────── ffmpeg ───────────────────────────
def test_ffmpeg_available_requires_a_successful_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        app.subprocess, "run",
        lambda *_a, **_k: types.SimpleNamespace(returncode=1, stdout="", stderr="missing dll"),
    )
    assert app.ffmpeg_available() is False

    monkeypatch.setattr(
        app.subprocess, "run",
        lambda *_a, **_k: types.SimpleNamespace(returncode=0, stdout="ffmpeg", stderr=""),
    )
    assert app.ffmpeg_available() is True


def test_probe_video_reports_failure_instead_of_silent_zeros(tmp_path: Path, monkeypatch, caplog) -> None:
    video = tmp_path / "-dash.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(
        app.subprocess, "run",
        lambda *_a, **_k: types.SimpleNamespace(
            returncode=1, stdout="", stderr="Invalid data found when processing input"
        ),
    )

    assert app.probe_video(str(video)) == (0, 0, 0.0)
    assert "Invalid data" in caplog.text


def test_audio_analysis_distinguishes_ffmpeg_failure_from_silence(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")

    monkeypatch.setattr(
        app.subprocess, "run",
        lambda *_a, **_k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    assert app._audio_rms_per_second(str(video)) is None

    monkeypatch.setattr(
        app.subprocess, "run",
        lambda *_a, **_k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert app._audio_rms_per_second(str(video)) == {}


def test_failed_frame_copy_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "frame.png"
    source.write_bytes(b"png")
    monkeypatch.setattr(app, "_extract_thumbnail_candidates", lambda *_a, **_k: [str(source)])
    monkeypatch.setattr(app, "_thumbnail_frame_score", lambda *_a, **_k: 1.0)

    def failing_copy(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app.shutil, "copyfile", failing_copy)
    before = {name for name in os.listdir(tempfile.gettempdir()) if name.startswith("fb-youtube-frame-best-")}

    with pytest.raises(OSError):
        app._pick_best_frame(str(source), 30.0)

    after = {name for name in os.listdir(tempfile.gettempdir()) if name.startswith("fb-youtube-frame-best-")}
    assert after == before


# ─────────────────────────── جسر مراجعة المحتوى ───────────────────────────
def test_review_command_is_dash_safe() -> None:
    command = _review_command("/usr/bin/python3", "-clip.mp4", "-عنوان", "caption")

    assert "--video=-clip.mp4" in command
    assert "--title=-عنوان" in command
    assert command[command.index("--caption") + 1] == "caption"

    plain = _review_command("/usr/bin/python3", "/videos/clip.mp4", "عنوان", "caption")
    assert plain[plain.index("--video") + 1] == "/videos/clip.mp4"
