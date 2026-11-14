"""انحدار 4.49.10 — «الفيديو بلا عنوان» على YouTube.

السبب ليس رفع عنوان فارغاً (YouTube يرفضه)، بل عنوان بديل ينتجه مسار
التنظيف: اسم الصفحة وحده يتحول إلى «مقطع فيديو»، وعلامات الاتجاه غير
المرئية تجعل العنوان يبدو فارغاً في الاستوديو.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app
from texttools import is_generic_title, prepare_public_title, preserve_source_title


# ─────────────────────────── المصدر ───────────────────────────
def test_page_name_only_becomes_a_placeholder() -> None:
    """توثيق السبب: العنوان البديل يُنتج من اسم صفحة فقط."""
    prepared = prepare_public_title("الوطنية TV", ["الوطنية TV"])

    assert prepared == "مقطع فيديو"
    assert is_generic_title(prepared)


def test_directional_marks_are_stripped_from_preserved_titles() -> None:
    assert preserve_source_title("\u200fهدف المباراة\u200f") == "هدف المباراة"
    assert preserve_source_title("\u061cعنوان\u200e") == "عنوان"


# ─────────────────────────── حارس الرفع ───────────────────────────
class _NeverCalledYouTube:
    def __init__(self):
        self.insert_calls = 0

    def videos(self):
        return self

    def insert(self, **_kwargs):
        self.insert_calls += 1
        raise AssertionError("must not build an upload request for a placeholder title")

    def list(self, **_kwargs):
        raise AssertionError("must not verify a request that was never sent")


@pytest.mark.parametrize("title", ["مقطع فيديو", "Video", "Facebook", "Reel", "  "])
def test_placeholder_titles_are_rejected_before_upload(tmp_path: Path, title: str) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    youtube = _NeverCalledYouTube()
    messages = []

    result = app.upload_video(
        youtube,
        {"file": str(video), "title": title, "desc": "وصف"},
        log_cb=messages.append,
    )

    assert result == "INVALID_TITLE"
    assert youtube.insert_calls == 0
    assert messages


def test_title_read_back_mismatch_is_logged(tmp_path: Path, caplog) -> None:
    class StatusRequest:
        def execute(self):
            return {"items": [{
                "status": {"privacyStatus": "private", "uploadStatus": "uploaded"},
                "snippet": {"title": "مقطع فيديو"},
            }]}

    class YouTube:
        def videos(self):
            return self

        def list(self, **_kwargs):
            return StatusRequest()

    logs = []
    app.verify_upload(
        YouTube(), "abcdefghijk", "private", None, logs.append,
        expected_title="هدف المباراة في الدقيقة الأخيرة",
    )

    assert any("يختلف عن العنوان المُرسل" in message for message in logs)
    assert "مقطع فيديو" in caplog.text or any("مقطع فيديو" in m for m in logs)


def test_matching_title_produces_no_warning() -> None:
    class StatusRequest:
        def execute(self):
            return {"items": [{
                "status": {"privacyStatus": "private"},
                "snippet": {"title": "عنوان مطابق"},
            }]}

    class YouTube:
        def videos(self):
            return self

        def list(self, **_kwargs):
            return StatusRequest()

    logs = []
    app.verify_upload(YouTube(), "abcdefghijk", "private", None, logs.append,
                      expected_title="عنوان مطابق")

    assert not any("يختلف" in message for message in logs)


# ─────────────────────────── الدورة لا ترفع بديلاً ───────────────────────────
def test_cycle_queues_instead_of_uploading_a_placeholder_title(monkeypatch, tmp_path) -> None:
    for name in ("STATE_FILE", "FAILED_QUEUE_FILE", "UPLOADED_IDS_FILE", "SEEN_IDS_FILE",
                 "UPLOAD_RECEIPTS_FILE", "UPLOAD_RECEIPTS_ARCHIVE_FILE", "HISTORY_FILE",
                 "RIGHTS_MANIFEST_FILE", "STATS_FILE", "PAGE_STATUS_FILE", "CHANNEL_INDEX_FILE",
                 "AI_KEYS_FILE", "PAGES_FILE", "ACCOUNTS_FILE"):
        monkeypatch.setattr(app, name, str(tmp_path / f"{name.lower()}.json"), raising=False)
    monkeypatch.setattr(app, "_RECEIPTS_COMPACTED", True)
    monkeypatch.setattr(app, "disk_free_gb", lambda: 100.0)
    monkeypatch.setattr(app, "collect_video_stats", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "send_cycle_summary", lambda *_a, **_k: None)

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    uploaded = []

    monkeypatch.setattr(app, "scrape_links_fast", lambda *_a, **_k: ["https://www.facebook.com/reel/777"])
    monkeypatch.setattr(app, "download_video", lambda url, page, *_a, **_k: {
        "file": str(clip), "title": "الوطنية TV", "desc": "وصف", "duration": 120,
        "source_page": page, "source_url": url, "title_generic": False,
    })
    monkeypatch.setattr(app, "validate_media", lambda *_a, **_k: (True, {}))
    monkeypatch.setattr(app, "finalize_video", lambda path, _log=None: path)
    monkeypatch.setattr(app, "upload_video", lambda *_a, **_k: uploaded.append(True) or "abcdefghijk")

    import queue
    import threading

    worker = app.App.__new__(app.App)
    worker._stop = threading.Event()
    worker._uiq = queue.Queue()
    worker._watching = False
    worker._thread = None
    worker.stats = {}
    worker._log = lambda *_a, **_k: None
    worker._emit_page_status = lambda *_a, **_k: None
    worker._safe_quit_driver = lambda *_a, **_k: None
    worker._history_page_index = lambda: {}
    worker._make_shorts_from = lambda *_a, **_k: None
    worker._cleanup_file = lambda *_a, **_k: None

    settings = app.build_cli_settings(configparser.ConfigParser(interpolation=None))
    settings.update({
        "account": "acc", "fast_scrape": True, "facebook_network_preflight": False,
        "cookies_enabled": False, "dedupe_content": False, "review_before_upload": False,
        "content_review_enabled": False, "ai_enabled": False, "thumbnail_enabled": False,
        "auto_shorts": False, "shorts_cut_enabled": False, "logo_enabled": False,
        "delete_after_upload": False, "schedule_enabled": False, "dry_run": False,
        "rights_gate_mode": "operator_confirmed", "rights_operator_basis": "owned",
        "rights_operator_statement": "أقر بأنني مالك المادة وأملك حق إعادة نشرها على قناتي.",
        "rights_operator_evidence": "https://example.test/evidence",
        "min_duration": 0, "max_duration": 0, "between_videos": 0,
        "title_optimize": False, "preserve_facebook_title": False,
    })
    pages = [{"url": "https://www.facebook.com/SomePage", "name": "الوطنية TV", "limit": 10, "scroll": 2}]
    ctx = {"yt": object(), "account": "acc",
           "quota": type("Q", (), {"used": 0, "daily_limit": 6,
                                   "can_afford": lambda *a: True,
                                   "register": lambda *a: None,
                                   "mark_exhausted": lambda self=None: None})()}

    app.App._run_cycle(worker, ctx, pages, settings, False)

    assert uploaded == []
    queued = app.load_failed_queue()
    assert queued and queued[0]["last_error"] == "facebook_title_missing"
    assert queued[0]["id"] == "777"
