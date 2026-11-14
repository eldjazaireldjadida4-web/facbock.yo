"""اختبارات دورة الرفع نفسها — المسار الذي لم يكن مغطّى إطلاقاً.

`_run_cycle` كانت تُشغَّل فقط في الإنتاج: كل إصلاح سابق كان حولها لا فيها.
هنا تُدار الدورة بمحرّك وهمي (سحب/تنزيل/رفع) بينما تبقى منطقية القائمة
والبوابات والإيصالات حقيقية، فتُكتشف انحدارات التسلسل قبل الوصول للمستخدم.
"""
from __future__ import annotations

import configparser
import json
import queue
import threading
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app


FILE_CONSTANTS = (
    "STATE_FILE", "FAILED_QUEUE_FILE", "UPLOADED_IDS_FILE", "SEEN_IDS_FILE",
    "UPLOAD_RECEIPTS_FILE", "UPLOAD_RECEIPTS_ARCHIVE_FILE", "HISTORY_FILE",
    "RIGHTS_MANIFEST_FILE", "STATS_FILE", "PAGE_STATUS_FILE", "CHANNEL_INDEX_FILE",
    "AI_KEYS_FILE", "PAGES_FILE", "ACCOUNTS_FILE",
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for name in FILE_CONSTANTS:
        monkeypatch.setattr(app, name, str(tmp_path / f"{name.lower()}.json"), raising=False)
    monkeypatch.setattr(app, "_RECEIPTS_COMPACTED", True)
    monkeypatch.setattr(app, "disk_free_gb", lambda: 100.0)
    monkeypatch.setattr(app, "collect_video_stats", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "send_cycle_summary", lambda *_a, **_k: None)
    return tmp_path


def _settings(**overrides):
    cfg = configparser.ConfigParser(interpolation=None)
    settings = app.build_cli_settings(cfg)
    settings.update({
        "account": "acc",
        "fast_scrape": True,
        "facebook_network_preflight": False,
        "cookies_enabled": False,
        "dedupe_content": False,
        "review_before_upload": False,
        "content_review_enabled": False,
        "ai_enabled": False,
        "local_transcription_enabled": False,
        "thumbnail_enabled": False,
        "auto_shorts": False,
        "shorts_cut_enabled": False,
        "logo_enabled": False,
        "delete_after_upload": False,
        "schedule_enabled": False,
        "dry_run": False,
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "owned",
        "rights_operator_statement": "أقر بأنني مالك المادة وأملك حق إعادة نشرها على قناتي.",
        "rights_operator_evidence": "https://example.test/evidence",
        "min_duration": 0,
        "max_duration": 0,
        "between_videos": 0,
        "title_optimize": False,
        "preserve_facebook_title": True,
    })
    settings.update(overrides)
    return settings


class _Quota:
    def __init__(self):
        self.used = 0
        self.daily_limit = 6

    def can_afford(self, *_args, **_kwargs):
        return True

    def register(self, *_args, **_kwargs):
        self.used += 1

    def mark_exhausted(self):
        self.used = self.daily_limit


def _bare_worker():
    instance = app.App.__new__(app.App)
    instance._stop = threading.Event()
    instance._uiq = queue.Queue()
    instance._watching = False
    instance._thread = None
    instance.stats = {"total_uploaded": 0, "total_failed": 0, "total_downloaded": 0, "daily": {}}
    instance.logs = []
    instance._log = lambda msg, **_kw: instance.logs.append(str(msg))
    instance._emit_page_status = lambda *_a, **_k: None
    instance._safe_quit_driver = lambda *_a, **_k: None
    instance._history_page_index = lambda: {}
    instance._make_shorts_from = lambda *_a, **_k: None
    instance._cleanup_file = lambda *_a, **_k: None
    return instance


def _video_file(tmp_path: Path) -> Path:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    return clip


def _stub_pipeline(monkeypatch, tmp_path: Path, upload_result="abcdefghijk"):
    clip = _video_file(tmp_path)
    calls = {"download": 0, "upload": 0}

    monkeypatch.setattr(app, "scrape_links_fast", lambda *_a, **_k: ["https://www.facebook.com/reel/111"])

    def fake_download(url, page, *_args, **_kwargs):
        calls["download"] += 1
        return {
            "file": str(clip),
            "title": "عنوان تجريبي واضح",
            "desc": "وصف المصدر",
            "duration": 120,
            "source_page": page,
            "source_url": url,
            "title_generic": False,
        }

    monkeypatch.setattr(app, "download_video", fake_download)
    monkeypatch.setattr(app, "validate_media", lambda *_a, **_k: (True, {}))
    monkeypatch.setattr(app, "finalize_video", lambda path, _log=None: path)

    def fake_upload(_youtube, _data, *_args, **kwargs):
        calls["upload"] += 1
        if upload_result == app.UPLOAD_RESULT_UNKNOWN:
            return app.UPLOAD_RESULT_UNKNOWN
        kwargs["on_confirmed"]("abcdefghijk")
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", fake_upload)
    return calls


def _run(monkeypatch, sandbox, settings, upload_result="abcdefghijk"):
    calls = _stub_pipeline(monkeypatch, sandbox, upload_result)
    worker = _bare_worker()
    ctx = {"yt": object(), "quota": _Quota(), "account": "acc"}
    pages = [{"url": "https://www.facebook.com/SomePage", "name": "SomePage", "limit": 10, "scroll": 2}]
    up, failed = app.App._run_cycle(worker, ctx, pages, settings, False)
    return worker, up, failed, calls


# ─────────────────────────── المسار السعيد ───────────────────────────
def test_new_reel_is_downloaded_uploaded_and_recorded(monkeypatch, sandbox) -> None:
    worker, up, failed, calls = _run(monkeypatch, sandbox, _settings())

    assert (up, failed) == (1, 0)
    assert calls == {"download": 1, "upload": 1}
    receipts = app.load_upload_receipts()
    assert list(receipts.values())[0]["youtube_id"] == "abcdefghijk"
    assert app.load_failed_queue() == []

    history = [json.loads(line) for line in Path(app.HISTORY_FILE).read_text(encoding="utf-8").splitlines()]
    assert any(record["status"] == "uploaded" for record in history)


def test_upload_unknown_is_queued_for_reconciliation(monkeypatch, sandbox) -> None:
    worker, up, failed, calls = _run(
        monkeypatch, sandbox, _settings(), upload_result=app.UPLOAD_RESULT_UNKNOWN
    )

    assert up == 0
    queued = app.load_failed_queue()
    assert queued and queued[0]["last_error"] == app.UPLOAD_RESULT_UNKNOWN
    assert not app.load_upload_receipts()


def test_shorts_marker_is_never_treated_as_a_scrapable_item(monkeypatch, sandbox) -> None:
    marker = {
        "id": "reel-1_shorts",
        "url": "https://www.facebook.com/reel/1",
        "page": "Page",
        "attempts": 0,
        "shorts_of": "reel-1",
        "last_error": app.UPLOAD_RESULT_UNKNOWN,
    }
    app.save_failed_queue([marker])
    calls = _stub_pipeline(monkeypatch, sandbox)
    monkeypatch.setattr(app, "scrape_links_fast", lambda *_a, **_k: [])
    worker = _bare_worker()
    pages = [{"url": "https://www.facebook.com/SomePage", "name": "SomePage", "limit": 10, "scroll": 2}]

    up, failed = app.App._run_cycle(
        worker, {"yt": object(), "quota": _Quota(), "account": "acc"}, pages, _settings(), False
    )

    assert calls["download"] == 0
    assert up == 0
    # تبقى للمطابقة اليدوية ولا تتحول إلى محاولة تنزيل
    assert any(item["id"] == "reel-1_shorts" for item in app.load_failed_queue())


# ─────────────────────────── إيقاف في منتصف الدورة ───────────────────────────
def test_stop_requeues_the_remaining_items(monkeypatch, sandbox) -> None:
    monkeypatch.setattr(
        app, "scrape_links_fast",
        lambda *_a, **_k: [
            "https://www.facebook.com/reel/111",
            "https://www.facebook.com/reel/222",
        ],
    )
    clip = _video_file(sandbox)
    seen = []

    def fake_download(url, page, *_args, **_kwargs):
        seen.append(url)
        return {
            "file": str(clip), "title": "عنوان واضح", "desc": "d", "duration": 120,
            "source_page": page, "source_url": url, "title_generic": False,
        }

    worker = _bare_worker()

    def fake_upload(_youtube, _data, *_args, **kwargs):
        worker._stop.set()  # يوقف بعد أول فيديو
        kwargs["on_confirmed"]("abcdefghijk")
        return "abcdefghijk"

    monkeypatch.setattr(app, "download_video", fake_download)
    monkeypatch.setattr(app, "validate_media", lambda *_a, **_k: (True, {}))
    monkeypatch.setattr(app, "finalize_video", lambda path, _log=None: path)
    monkeypatch.setattr(app, "upload_video", fake_upload)
    pages = [{"url": "https://www.facebook.com/SomePage", "name": "SomePage", "limit": 10, "scroll": 2}]

    up, _failed = app.App._run_cycle(
        worker, {"yt": object(), "quota": _Quota(), "account": "acc"}, pages, _settings(), False
    )

    assert up == 1
    assert len(seen) == 1
    queued = app.load_failed_queue()
    assert [item["id"] for item in queued] == ["222"]
    assert queued[0]["last_error"] == "مؤجل"
