from __future__ import annotations

import importlib.util
import hashlib
import queue
import threading
from pathlib import Path

from content_review_bridge import build_review_caption, metadata_fingerprint

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "fb_youtube_uploader_v49_pro.py"
spec = importlib.util.spec_from_file_location("review_app", MODULE_PATH)
assert spec and spec.loader
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FakeQuota:
    used = 0
    daily_limit = 10

    def can_afford(self):
        return True

    def mark_exhausted(self):
        raise AssertionError("Unexpected quota exhaustion")


@pytest.fixture

def review_cycle(monkeypatch, tmp_path):
    worker = object.__new__(app.App)
    worker._stop = threading.Event()
    worker._uiq = queue.Queue()
    worker.stats = {"total_downloaded": 0, "total_uploaded": 0, "total_failed": 0}
    worker._log = lambda *_args, **_kwargs: None
    worker._emit_page_status = lambda *_args, **_kwargs: None
    worker._cleanup_file = lambda *_args, **_kwargs: None
    worker._requeue_unprocessed = lambda failed, remaining: failed.extend(
        {"id": item["id"], "url": item["url"], "page": item.get("page", ""),
         "attempts": item.get("attempts", 0), "last_error": "مؤجل"}
        for item in remaining
    )

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"sample video")
    seen_ids = []
    uploaded_ids = []
    histories = []
    queued = []
    thumbnail_uploads = []
    thumbnail_cleanup = []

    monkeypatch.setattr(app, "load_uploaded_ids", lambda: set())
    monkeypatch.setattr(app, "load_seen_ids", lambda: set())
    monkeypatch.setattr(app, "load_failed_queue", lambda: [])
    monkeypatch.setattr(app, "UPLOAD_RECEIPTS_FILE", str(tmp_path / "upload_receipts.json"))
    monkeypatch.setattr(app, "save_failed_queue", lambda items: queued.extend(items))
    monkeypatch.setattr(app, "save_seen_id", seen_ids.append)
    monkeypatch.setattr(app, "save_uploaded_id", uploaded_ids.append)
    def store_history(record):
        histories.append(record)
        return True

    monkeypatch.setattr(app, "append_history", store_history)
    monkeypatch.setattr(app, "scrape_links_fast", lambda *_args, **_kwargs: [
        "https://www.facebook.com/reel/reel-1"
    ])
    monkeypatch.setattr(app, "filter_new_reels", lambda links, *_args: ([
        {"id": "reel-1", "url": links[0], "page": "Page"}
    ] if links else []))
    monkeypatch.setattr(app, "disk_free_gb", lambda: 100.0)
    monkeypatch.setattr(app, "download_video", lambda *_args, **_kwargs: {
        "file": str(source_path), "title": "Original title", "desc": "Original description",
        "duration": 30,
    })
    monkeypatch.setattr(app, "validate_media", lambda *_args, **_kwargs: (True, {"problems": []}))
    monkeypatch.setattr(app, "duration_ok", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(app, "is_duplicate_content", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(app, "build_description", lambda description, *_args, **_kwargs: description)
    monkeypatch.setattr(app, "finalize_video", lambda path, *_args, **_kwargs: path)
    monkeypatch.setattr(app, "create_thumbnail_preview", lambda *_args, **_kwargs: (
        "base-preview.png", "approved-preview.png"
    ))
    monkeypatch.setattr(app, "remove_thumbnail_preview", lambda *paths: thumbnail_cleanup.extend(paths))
    monkeypatch.setattr(app, "upload_thumbnail", lambda *args, **_kwargs: thumbnail_uploads.append(args))
    monkeypatch.setattr(app, "bump_daily_uploads", lambda _stats: None)
    monkeypatch.setattr(app, "remember_content", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "send_telegram", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "collect_video_stats", lambda *_args, **_kwargs: None)

    rights_record = app.new_rights_record(
        rights_basis="owned",
        original_contribution="تحليل تحريري أصلي يضيف سياقاً ومعلومات جديدة للمشاهد.",
        duplicate_review_note="هذا المحتوى يختلف فعلياً في الوقائع والسياق عن الفيديو السابق.",
        source_url="https://www.facebook.com/reel/reel-1",
        attested=True,
    )
    saved_rights = []
    monkeypatch.setattr(app, "get_rights_record", lambda *_args: dict(rights_record))
    monkeypatch.setattr(app, "save_rights_record", lambda source_id, record, path: saved_rights.append((source_id, record, path)))
    monkeypatch.setattr(app, "extract_video_fingerprint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "file_sha256", lambda *_args, **_kwargs: "test-hash")
    monkeypatch.setattr(app, "is_known_media_hash", lambda *_args: False)

    settings = {
        "account": "test-channel", "headless": True, "fast_scrape": True,
        "cookies_enabled": False, "cookies_file": "", "remove_tags": False,
        "min_disk_gb": 0, "min_duration": 0, "max_duration": 0,
        "dedupe_content": False, "ai_enabled": False, "title_optimize": False,
        "title_prefix": "", "title_suffix": "", "auto_shorts": False,
        "auto_hashtags": False, "extra_description": "", "credit_line": "",
        "tags": ["Facebook"], "logo_enabled": False, "schedule_enabled": False,
        "min_delay": 20, "max_delay": 60, "privacy": "private", "playlist_id": "",
        "category": "22", "review_before_upload": True, "thumbnail_enabled": True,
        "shorts_cut_enabled": False, "delete_after_upload": False,
        "between_videos": 0, "max_attempts": 3,
    }
    settings["rights_manifest_file"] = str(tmp_path / "rights_manifest.json")
    pages = [{"url": "https://www.facebook.com/page/reels/", "name": "Page",
              "scroll": 1, "limit": 1, "enabled": True}]
    context = {"yt": object(), "quota": FakeQuota(), "account": "test-channel"}
    return {
        "worker": worker, "context": context, "pages": pages, "settings": settings,
        "seen_ids": seen_ids, "uploaded_ids": uploaded_ids, "histories": histories,
        "queued": queued, "thumbnail_uploads": thumbnail_uploads,
        "thumbnail_cleanup": thumbnail_cleanup, "saved_rights": saved_rights,
    }


def test_facebook_preflight_defers_cycle_and_keeps_retries_queued(review_cycle, monkeypatch):
    pending = {"id": "queued-1", "url": "https://www.facebook.com/reel/queued-1", "attempts": 1}
    saved = []
    monkeypatch.setattr(app, "load_failed_queue", lambda: [pending])
    monkeypatch.setattr(app, "save_failed_queue", lambda items: saved.append(list(items)))
    monkeypatch.setattr(app, "_facebook_network_preflight", lambda _log: False)
    review_cycle["settings"]["facebook_network_preflight"] = True
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert review_cycle["context"]["facebook_network_unavailable"] is True
    assert saved and saved[-1][0]["id"] == "queued-1"
    assert uploads == []


def test_stop_during_retry_download_preserves_current_retry(review_cycle, monkeypatch):
    pending = {
        "id": "queued-1", "url": "https://www.facebook.com/reel/queued-1",
        "page": "Page", "attempts": 2, "last_error": "temporary network failure",
    }
    saved = []
    monkeypatch.setattr(app, "load_failed_queue", lambda: [pending])
    monkeypatch.setattr(app, "save_failed_queue", lambda items: saved.append(list(items)))
    monkeypatch.setattr(app, "scrape_links_fast", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "filter_new_reels", lambda *_args, **_kwargs: [])

    def stop_download(*_args, **_kwargs):
        raise app.StopRequested()

    monkeypatch.setattr(app, "download_video", stop_download)

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    queued_item = next(item for batch in saved for item in batch if item["id"] == "queued-1")
    assert result[0] == 0
    assert queued_item["attempts"] == 2
    assert queued_item["last_error"] == "مؤجل"


def test_ambiguous_upload_is_held_and_never_counted_as_success(review_cycle, monkeypatch):
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {
        "action": "approve", "title": "Approved title", "description": "Approved description",
        "privacy": "private", "thumbnail_path": "approved-preview.png",
    })
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: app.UPLOAD_RESULT_UNKNOWN)

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert review_cycle["uploaded_ids"] == []
    assert any(
        item.get("id") == "reel-1" and item.get("last_error") == app.UPLOAD_RESULT_UNKNOWN
        for item in review_cycle["queued"]
    )
    assert any(item.get("status") == app.UPLOAD_RESULT_UNKNOWN for item in review_cycle["histories"])


def test_max_attempts_remain_recoverable_but_held(review_cycle):
    failed = []
    item = {
        "id": "reel-1", "url": "https://www.facebook.com/reel/reel-1",
        "page": "Page", "attempts": 2,
    }

    review_cycle["worker"]._handle_failure(
        failed, set(), item, "network timeout", {"max_attempts": 3}
    )

    assert failed[0]["last_error"] == "abandoned"
    assert review_cycle["seen_ids"] == ["reel-1"]
    ready, held = app.split_duplicate_review_queue(failed, review_enabled=False)
    assert ready == []
    assert held == failed


def test_empty_facebook_scan_is_reported_as_scrape_failure_not_network_failure(review_cycle, monkeypatch):
    class Driver:
        def quit(self):
            pass

    review_cycle["settings"]["facebook_network_preflight"] = False
    review_cycle["settings"]["fast_scrape"] = False
    statuses = []
    monkeypatch.setattr(app, "init_driver", lambda *_args: Driver())
    monkeypatch.setattr(app, "get_reels", lambda *_args: [])
    monkeypatch.setattr(review_cycle["worker"], "_emit_page_status", lambda *args: statuses.append(args[-1]))
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: pytest.fail("no source links"))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert statuses == ["لم تُرجع الصفحة روابط فيديو؛ يلزم التحقق"]
    assert review_cycle["context"]["facebook_network_unavailable"] is False
    assert review_cycle["uploaded_ids"] == []


def test_empty_fast_extraction_falls_back_to_browser_for_profile_page(review_cycle, monkeypatch):
    class Driver:
        def quit(self):
            return None

    review_cycle["pages"][0]["url"] = "https://www.facebook.com/profile.php?id=123"
    review_cycle["settings"]["fast_scrape"] = True
    browser_calls = []
    statuses = []
    monkeypatch.setattr(app, "scrape_links_fast", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "init_driver", lambda *_args: Driver())
    monkeypatch.setattr(app, "get_reels", lambda *_args: browser_calls.append("opened") or [])
    monkeypatch.setattr(
        review_cycle["worker"], "_emit_page_status",
        lambda *args: statuses.append(args[-1]),
    )
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: pytest.fail("no source links"))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert browser_calls == ["opened"]
    assert statuses == ["لم تُرجع الصفحة روابط فيديو؛ يلزم التحقق"]


def test_empty_fast_extraction_discovers_reels_through_browser(review_cycle, monkeypatch):
    class Driver:
        def quit(self):
            return None

    review_cycle["pages"][0]["url"] = "https://www.facebook.com/profile.php?id=123"
    review_cycle["settings"]["fast_scrape"] = True
    reel = "https://www.facebook.com/reel/123456789"
    downloads = []
    monkeypatch.setattr(app, "scrape_links_fast", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "init_driver", lambda *_args: Driver())
    monkeypatch.setattr(app, "get_reels", lambda *_args: [reel])
    monkeypatch.setattr(
        app, "download_video",
        lambda *args, **_kwargs: downloads.append(args[0]) or None,
    )
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: pytest.fail("failed download must not upload"))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result[1] == 1
    assert downloads == [reel]
    assert review_cycle["uploaded_ids"] == []


def test_channel_index_failure_returns_failure_and_requeues_source(review_cycle, monkeypatch):
    review_cycle["settings"]["dedupe_content"] = True
    review_cycle["settings"]["review_before_upload"] = False
    review_cycle["settings"]["facebook_network_preflight"] = False
    monkeypatch.setattr(app, "scrape_links_fast", lambda *_args, **_kwargs: [
        "https://www.facebook.com/reel/reel-1"
    ])
    monkeypatch.setattr(
        app, "fetch_channel_video_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("API timeout")),
    )

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert any(item.get("id") == "reel-1" for item in review_cycle["queued"])
    assert review_cycle["uploaded_ids"] == []


def test_approval_applies_edits_and_uploads_matching_thumbnail(review_cycle, monkeypatch):
    captured = {}
    monkeypatch.setattr(app, "wait_for_review_decision", lambda _queue, _stop, payload: {
        "action": "approve", "title": "Approved title | Page", "description": "Approved description",
        "privacy": "unlisted", "thumbnail_path": payload["thumbnail_path"],
    })

    def upload(_youtube, video_data, privacy, *_args, **_kwargs):
        assert review_cycle["saved_rights"]
        captured.update(title=video_data["title"], description=video_data["desc"], privacy=privacy)
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", upload)
    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (1, 0)
    assert captured["title"] == "Approved title"
    assert captured["description"].startswith("Approved description")
    assert "المصدر الأصلي: Page" in captured["description"]
    assert "https://www.facebook.com/reel/reel-1" in captured["description"]
    assert captured["privacy"] == "unlisted"
    assert review_cycle["thumbnail_uploads"][0][2] == "approved-preview.png"
    assert review_cycle["uploaded_ids"] == ["reel-1"]
    assert set(review_cycle["thumbnail_cleanup"]) == {"base-preview.png", "approved-preview.png"}


def test_manual_skip_marks_seen_without_upload(review_cycle, monkeypatch):
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {"action": "skip"})
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert uploads == []
    assert review_cycle["seen_ids"] == ["reel-1"]
    assert review_cycle["histories"][0]["status"] == "skipped_manual"
    assert set(review_cycle["thumbnail_cleanup"]) == {"base-preview.png", "approved-preview.png"}


def test_cancel_requeues_current_video_without_upload(review_cycle, monkeypatch):
    def cancel(_queue, stop_event, _payload):
        stop_event.set()
        return {"action": "cancel"}

    monkeypatch.setattr(app, "wait_for_review_decision", cancel)
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert uploads == []
    assert review_cycle["seen_ids"] == []
    assert review_cycle["queued"][0]["id"] == "reel-1"


def test_wait_for_review_decision_unblocks_when_stopped():
    events = queue.Queue()
    stop_event = threading.Event()
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            app.wait_for_review_decision(events, stop_event, {"title": "Review"}, 0.01)
        ),
        daemon=True,
    )
    worker.start()
    event, request = events.get(timeout=2)
    assert event == "review_request"
    stop_event.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert results == [{"action": "cancel"}]
    assert request["ready"].is_set()

def test_cli_remains_noninteractive_even_when_config_enables_review():
    cfg = app.configparser.ConfigParser()
    cfg.read_dict({"STUDIO": {"REVIEW_BEFORE_UPLOAD": "true"}})
    assert app.build_cli_settings(cfg)["review_before_upload"] is False


def test_disabled_review_keeps_automatic_thumbnail_upload(review_cycle, monkeypatch):
    review_cycle["settings"]["review_before_upload"] = False
    review_cycle["settings"]["privacy"] = "public"
    review_cycle["settings"]["allow_public_automation"] = True
    generated = []
    upload_options = []
    auto_thumbnail = "automatic-thumbnail.png"
    monkeypatch.setattr(app, "generate_thumbnail", lambda *args, **kwargs: (generated.append(args) or auto_thumbnail))
    monkeypatch.setattr(app, "create_thumbnail_preview", lambda *_args, **_kwargs: pytest.fail("review preview must not be created"))
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: pytest.fail("review must be disabled"))

    def upload(*_args, **kwargs):
        upload_options.append(kwargs)
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", upload)
    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (1, 0)
    assert generated
    assert review_cycle["thumbnail_uploads"][0][2] == auto_thumbnail
    assert upload_options[0]["allow_public_automation"] is True
    assert upload_options[0]["publication_approved"] is False



def test_scheduled_review_cannot_publish_publicly(review_cycle, monkeypatch):
    review_cycle["settings"]["schedule_enabled"] = True
    monkeypatch.setattr(app, "next_publish_time", lambda *_args: app.datetime(2026, 1, 1, tzinfo=app.timezone.utc))
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {
        "action": "approve", "title": "Approved", "description": "Description",
        "privacy": "public", "thumbnail_path": "approved-preview.png",
    })
    uploaded_privacies = []
    approval_flags = []

    def upload(_yt, _video, privacy, *_args, **kwargs):
        uploaded_privacies.append(privacy)
        approval_flags.append(kwargs["publication_approved"])
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", upload)
    review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )
    assert uploaded_privacies == ["private"]
    assert approval_flags == [True]


def test_channel_duplicate_is_shown_before_manual_upload(review_cycle, monkeypatch):
    settings = review_cycle["settings"]
    settings["dedupe_content"] = True
    candidate = {
        "video_id": "existing-video", "title": "Original title",
        "published_at": "2026-01-02T03:04:05Z", "duration": 30,
    }
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr(app, "file_sha256", lambda _path: "new-source-hash")
    monkeypatch.setattr(app, "is_known_media_hash", lambda _digest: False)
    requests = []
    monkeypatch.setattr(app, "wait_for_review_decision", lambda _uiq, _stop, payload: (
        requests.append(payload) or {"action": "skip"}
    ))

    review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], settings, False
    )

    assert requests[0]["duplicate_matches"][0]["video_id"] == "existing-video"
    assert review_cycle["seen_ids"] == ["reel-1"]
    assert review_cycle["uploaded_ids"] == []


def test_channel_duplicate_blocks_noninteractive_upload(review_cycle, monkeypatch):
    settings = review_cycle["settings"]
    settings.update({"dedupe_content": True, "review_before_upload": False})
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: [{
        "video_id": "existing-video", "title": "Original title",
        "published_at": "2026-01-02T03:04:05Z", "duration": 30,
    }])
    monkeypatch.setattr(app, "file_sha256", lambda _path: "new-source-hash")
    monkeypatch.setattr(app, "is_known_media_hash", lambda _digest: False)

    review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], settings, False
    )

    assert review_cycle["seen_ids"] == []
    assert review_cycle["uploaded_ids"] == []
    assert review_cycle["histories"][-1]["status"] == "duplicate_review_required"
    assert review_cycle["queued"] == [{
        "id": "reel-1", "url": "https://www.facebook.com/reel/reel-1",
        "page": "Page", "attempts": 0, "last_error": "duplicate_review_required",
    }]



def test_channel_duplicate_is_shown_for_manual_review(review_cycle, monkeypatch):
    review_cycle["settings"]["dedupe_content"] = True
    candidates = [{
        "video_id": "existing-video",
        "title": "Original title",
        "published_at": "2026-09-01T12:00:00Z",
        "duration": 30,
    }]
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: candidates)
    monkeypatch.setattr(app, "is_duplicate_content", lambda _title: False)
    monkeypatch.setattr(app, "is_known_media_hash", lambda _digest: False)
    captured = {}

    def approve_review(_uiq, _stop, payload):
        captured.update(payload)
        return {
            "action": "skip", "title": payload["title"],
            "description": payload["description"], "privacy": payload["privacy"],
            "thumbnail_path": payload["thumbnail_path"],
        }

    monkeypatch.setattr(app, "wait_for_review_decision", approve_review)
    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert captured["duplicate_matches"][0]["video_id"] == "existing-video"
    assert review_cycle["thumbnail_uploads"] == []


def test_noninteractive_missing_rights_record_holds_upload(review_cycle, monkeypatch):
    review_cycle["settings"].update({"review_before_upload": False, "dedupe_content": False})
    monkeypatch.setattr(app, "get_rights_record", lambda *_args: None)
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert uploads == []
    assert review_cycle["queued"][-1]["last_error"] == "rights_review_required"
    assert review_cycle["histories"][-1]["status"] == "rights_review_required"


def test_visual_duplicate_is_sent_to_human_review(review_cycle, monkeypatch):
    review_cycle["settings"]["dedupe_content"] = True
    frames = [f"{index * 0x1100000000000001:016x}" for index in range(8)]
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "load_state", lambda: {"video_frame_fingerprints": [{
        "video_id": "prior-upload", "title": "Earlier report", "duration": 30, "frames": frames,
    }]})
    monkeypatch.setattr(app, "extract_video_fingerprint", lambda *_args, **_kwargs: frames)
    monkeypatch.setattr(app, "file_sha256", lambda *_args, **_kwargs: "fresh-hash")
    monkeypatch.setattr(app, "is_known_media_hash", lambda *_args: False)
    captured = {}

    def skip_review(_uiq, _stop, payload):
        captured.update(payload)
        return {"action": "skip"}

    monkeypatch.setattr(app, "wait_for_review_decision", skip_review)
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))
    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert uploads == []
    assert any(match.get("match_type") == "visual_fingerprint"
               for match in captured["duplicate_matches"])
    assert captured["duplicate_matches"][0]["video_id"] == "prior-upload"





def test_visual_duplicate_fingerprint_requires_manual_review(review_cycle, monkeypatch):
    review_cycle["settings"]["dedupe_content"] = True
    frames = ["0000000000000000", "0000000000000001", "0000000000000003", "0000000000000007"]
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "load_state", lambda: {"video_frame_fingerprints": [{
        "video_id": "prior-abcdefghijk", "title": "Earlier clip", "duration": 30, "frames": frames,
    }]})
    monkeypatch.setattr(app, "extract_video_fingerprint", lambda *_args, **_kwargs: frames)
    monkeypatch.setattr(app, "file_sha256", lambda *_args, **_kwargs: "new-source-hash")
    monkeypatch.setattr(app, "is_known_media_hash", lambda _digest: False)
    captured = {}

    def skip_after_review(_uiq, _stop, payload):
        captured.update(payload)
        return {"action": "skip"}

    monkeypatch.setattr(app, "wait_for_review_decision", skip_after_review)
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert captured["duplicate_matches"][0]["match_type"] == "visual_fingerprint"
    assert captured["duplicate_matches"][0]["video_id"] == "prior-abcdefghijk"
    assert uploads == []



def test_visual_duplicate_is_reported_before_manual_review(review_cycle, monkeypatch):
    frames = ["0000000000000000", "0000000000000001", "0000000000000003"]
    review_cycle["settings"]["dedupe_content"] = True
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "load_state", lambda: {
        "title_fingerprints": [], "content_sha256": [],
        "video_frame_fingerprints": [{
            "video_id": "previous-video", "title": "Earlier video", "duration": 30,
            "frames": list(frames),
        }],
    })
    monkeypatch.setattr(app, "extract_video_fingerprint", lambda *_args, **_kwargs: list(frames))
    monkeypatch.setattr(app, "is_duplicate_content", lambda *_args, **_kwargs: False)
    captured = {}
    monkeypatch.setattr(app, "wait_for_review_decision", lambda _uiq, _stop, payload: (
        captured.update(payload) or {"action": "skip"}
    ))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert captured["duplicate_matches"][0]["match_type"] == "visual_fingerprint"
    assert captured["duplicate_matches"][0]["video_id"] == "previous-video"
    assert review_cycle["uploaded_ids"] == []


def test_visual_duplicate_is_held_without_manual_review(review_cycle, monkeypatch):
    frames = ["0000000000000000", "0000000000000001", "0000000000000003"]
    review_cycle["settings"].update({"dedupe_content": True, "review_before_upload": False})
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "load_state", lambda: {
        "title_fingerprints": [], "content_sha256": [],
        "video_frame_fingerprints": [{
            "video_id": "previous-video", "title": "Earlier video", "duration": 30,
            "frames": list(frames),
        }],
    })
    monkeypatch.setattr(app, "extract_video_fingerprint", lambda *_args, **_kwargs: list(frames))
    monkeypatch.setattr(app, "is_duplicate_content", lambda *_args, **_kwargs: False)
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert uploads == []
    assert review_cycle["queued"][-1]["last_error"] == "duplicate_review_required"


def test_visual_duplicate_is_flagged_for_human_review(review_cycle, monkeypatch):
    review_cycle["settings"]["dedupe_content"] = True
    signature = ["0000000000000000", "1111111111111111", "2222222222222222"]
    monkeypatch.setattr(app, "load_state", lambda: {
        "video_frame_fingerprints": [{
            "video_id": "prior-upload", "title": "Older story", "duration": 30,
            "frames": list(signature),
        }],
        "title_fingerprints": [], "content_sha256": [],
    })
    monkeypatch.setattr(app, "fetch_channel_video_index", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "extract_video_fingerprint", lambda *_args, **_kwargs: list(signature))
    captured = {}
    monkeypatch.setattr(app, "wait_for_review_decision", lambda _uiq, _stop, payload: (
        captured.update(payload) or {"action": "skip"}
    ))
    uploads = []
    monkeypatch.setattr(app, "upload_video", lambda *args, **kwargs: uploads.append(args))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert uploads == []
    assert captured["duplicate_matches"][0]["match_type"] == "visual_fingerprint"
    assert captured["duplicate_matches"][0]["video_id"] == "prior-upload"


def test_dry_run_skips_quota_and_upload(review_cycle, monkeypatch):
    review_cycle["settings"].update({
        "dry_run": True,
        "review_before_upload": False,
        "thumbnail_enabled": False,
    })
    review_cycle["context"]["quota"].can_afford = lambda: pytest.fail("dry-run must not check upload quota")
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: pytest.fail("dry-run must not upload"))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert review_cycle["uploaded_ids"] == []
    assert review_cycle["histories"][0]["status"] == "dry_run"


def _clear_review_for(video_data, tags):
    caption = build_review_caption(video_data.get("desc", ""), tags)
    return {
        "schema_version": 1,
        "review_complete": True,
        "status": "clear",
        "backend": "gemini",
        "video_sha256": hashlib.sha256(Path(video_data["file"]).read_bytes()).hexdigest(),
        "metadata_sha256": metadata_fingerprint(video_data["title"], caption),
        "transcript_entries": 1,
        "review_windows": 1,
        "blocked_windows": [],
        "manual_review_windows": [],
        "ai_violations": [],
        "ai_review": [{"index": 0, "violation": False, "reason": ""}],
        "local_review": {"mode": "block", "segments": [{"index": 0, "status": "safe"}]},
    }


def test_content_review_failure_holds_item_without_upload(review_cycle, monkeypatch):
    review_cycle["settings"]["content_review_enabled"] = True
    monkeypatch.setattr(app, "run_policy_review", lambda *_args: (None, "review_incomplete"))
    monkeypatch.setattr(
        app, "wait_for_review_decision",
        lambda *_args: pytest.fail("manual approval must not bypass a failed policy review"),
    )
    monkeypatch.setattr(
        app, "upload_video", lambda *_args, **_kwargs: pytest.fail("a blocked item must not upload")
    )

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert review_cycle["queued"][-1]["last_error"] == "content_safety_review_required"
    assert review_cycle["histories"][-1]["status"] == "content_safety_review_required"


def test_manual_edits_force_a_fresh_policy_review(review_cycle, monkeypatch):
    review_cycle["settings"]["content_review_enabled"] = True
    review_calls = []

    def review(video_data, tags, _settings, _log):
        review_calls.append((video_data["title"], video_data["desc"]))
        return _clear_review_for(video_data, tags), "clear"

    monkeypatch.setattr(app, "run_policy_review", review)
    monkeypatch.setattr(app, "wait_for_review_decision", lambda _queue, _stop, payload: {
        "action": "approve", "title": "Approved title | Page", "description": "Approved description",
        "privacy": "unlisted", "thumbnail_path": payload["thumbnail_path"],
    })
    captured = {}

    def upload(_youtube, video_data, privacy, *_args, **_kwargs):
        caption = build_review_caption(video_data["desc"], ["Facebook"])
        report = video_data["content_safety_review"]
        assert report["metadata_sha256"] == metadata_fingerprint(video_data["title"], caption)
        captured.update(title=video_data["title"], privacy=privacy)
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", upload)
    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (1, 0)
    assert len(review_calls) == 2
    assert review_calls[0][0] == "Original title"
    assert "Original description" in review_calls[0][1]
    assert "المصدر الأصلي: Page" in review_calls[0][1]
    assert "https://www.facebook.com/reel/reel-1" in review_calls[0][1]
    assert review_calls[1][0] == "Approved title"
    assert "Approved description" in review_calls[1][1]
    assert "المصدر الأصلي: Page" in review_calls[1][1]
    assert captured == {"title": "Approved title", "privacy": "unlisted"}


def test_ai_title_cannot_reintroduce_source_page_name(review_cycle, monkeypatch):
    review_cycle["settings"].update({
        "ai_enabled": True,
        "ai_rewrite_always": True,
        "omit_source_page_name": True,
        "title_optimize": False,
        "thumbnail_enabled": False,
    })
    captured = {}
    monkeypatch.setattr(
        review_cycle["worker"],
        "_ai_metadata",
        lambda *_args: {
            "title": "عنوان جذاب | Page",
            "description": "وصف مولد",
            "tags": [],
        },
    )
    monkeypatch.setattr(
        app,
        "wait_for_review_decision",
        lambda _uiq, _stop, payload: captured.update(payload) or {"action": "skip"},
    )
    monkeypatch.setattr(
        app, "upload_video", lambda *_args, **_kwargs: pytest.fail("a skipped review must not upload")
    )

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert captured["title"] == "عنوان جذاب"
    assert "Page" not in captured["title"]
    assert "المصدر الأصلي: Page" in captured["description"]
    assert "https://www.facebook.com/reel/reel-1" in captured["description"]


def test_ai_title_does_not_replace_real_facebook_title(review_cycle, monkeypatch):
    review_cycle["settings"].update({
        "ai_enabled": True,
        "ai_rewrite_always": False,
        "omit_source_page_name": True,
        "title_optimize": False,
        "thumbnail_enabled": False,
    })
    captured = {}
    monkeypatch.setattr(
        review_cycle["worker"],
        "_ai_metadata",
        lambda *_args: {
            "title": "عنوان جذاب",
            "description": "وصف مولد",
            "tags": [],
        },
    )
    monkeypatch.setattr(
        app,
        "wait_for_review_decision",
        lambda _uiq, _stop, payload: captured.update(payload) or {"action": "skip"},
    )
    monkeypatch.setattr(
        app, "upload_video", lambda *_args, **_kwargs: pytest.fail("a skipped review must not upload")
    )

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert captured["title"] == "Original title"
    assert captured["description"].startswith("وصف مولد")


def test_empty_cached_ai_title_preserves_source_title(review_cycle, monkeypatch):
    review_cycle["settings"].update({
        "ai_enabled": True,
        "omit_source_page_name": True,
        "title_optimize": False,
        "thumbnail_enabled": False,
    })
    captured = {}
    monkeypatch.setattr(
        review_cycle["worker"],
        "_ai_metadata",
        lambda *_args: {"title": "", "description": "وصف مولد", "tags": []},
    )
    monkeypatch.setattr(
        app,
        "wait_for_review_decision",
        lambda _uiq, _stop, payload: captured.update(payload) or {"action": "skip"},
    )
    monkeypatch.setattr(
        app, "upload_video", lambda *_args, **_kwargs: pytest.fail("a skipped review must not upload")
    )

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert captured["title"] == "Original title"


def test_get_reels_reports_a_security_checkpoint_before_scrolling(monkeypatch):
    class Driver:
        current_url = "https://www.facebook.com/checkpoint/"
        page_source = ""

        def set_page_load_timeout(self, _timeout):
            pass

        def get(self, _url):
            pass

    messages = []
    with pytest.raises(app.FacebookLoginRequired, match="checkpoint"):
        app.get_reels(
            Driver(), "https://www.facebook.com/page/reels/", 3, 10, messages.append
        )

    assert any("تحققاً أمنياً" in message for message in messages)


def test_get_reels_detects_a_facebook_checkpoint_before_scraping():
    class Driver:
        current_url = "https://www.facebook.com/checkpoint/"
        page_source = "<html></html>"

        def set_page_load_timeout(self, _seconds):
            pass

        def get(self, _url):
            pass

    messages = []
    with pytest.raises(app.FacebookLoginRequired, match="checkpoint"):
        app.get_reels(
            Driver(), "https://www.facebook.com/page/reels/", 1, 1, messages.append
        )

    assert any("تحققاً أمنياً" in message for message in messages)


def test_login_required_scrape_stops_cycle_and_preserves_discovered_items(review_cycle, monkeypatch):
    class Driver:
        def quit(self):
            pass

    review_cycle["settings"]["facebook_network_preflight"] = False
    review_cycle["settings"]["fast_scrape"] = True
    review_cycle["pages"].extend([
        {"url": "https://www.facebook.com/second/reels/", "name": "Second", "scroll": 1, "limit": 1},
        {"url": "https://www.facebook.com/third/reels/", "name": "Third", "scroll": 1, "limit": 1},
    ])
    scrape_calls = []

    def scrape(page_url, *_args, **_kwargs):
        scrape_calls.append(page_url)
        return ["https://www.facebook.com/reel/reel-1"] if len(scrape_calls) == 1 else None

    monkeypatch.setattr(app, "scrape_links_fast", scrape)
    monkeypatch.setattr(app, "init_driver", lambda *_args: Driver())
    monkeypatch.setattr(
        app, "get_reels",
        lambda *_args: (_ for _ in ()).throw(app.FacebookLoginRequired("checkpoint")),
    )
    monkeypatch.setattr(
        app, "download_video",
        lambda *_args, **_kwargs: pytest.fail("no download may start after an auth failure"),
    )

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert len(scrape_calls) == 2
    assert review_cycle["context"]["facebook_network_unavailable"] is False
    assert review_cycle["context"]["facebook_auth_required"] is True
    assert review_cycle["uploaded_ids"] == []
    assert any(item["url"] == "https://www.facebook.com/reel/reel-1" for item in review_cycle["queued"])


def test_transient_scrape_failure_is_classified_as_network_failure(review_cycle, monkeypatch):
    class Driver:
        def quit(self):
            pass

    review_cycle["settings"]["facebook_network_preflight"] = False
    review_cycle["settings"]["fast_scrape"] = False
    monkeypatch.setattr(app, "init_driver", lambda *_args: Driver())
    monkeypatch.setattr(app, "get_reels", lambda *_args: (_ for _ in ()).throw(app.FacebookNetworkUnavailable("timeout")))

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 0)
    assert review_cycle["context"]["facebook_network_unavailable"] is True
    assert review_cycle["uploaded_ids"] == []


def test_upload_confirmation_is_persisted_before_thumbnail_work(review_cycle, monkeypatch):
    events = []
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {
        "action": "approve", "title": "Approved title", "description": "Approved description",
        "privacy": "private", "thumbnail_path": "approved-preview.png",
    })
    monkeypatch.setattr(app, "save_uploaded_id", lambda _source_id: events.append("source-ledger"))
    monkeypatch.setattr(app, "upload_thumbnail", lambda *_args, **_kwargs: events.append("thumbnail"))

    def upload(*_args, **kwargs):
        events.append("youtube-confirmed")
        kwargs["on_confirmed"]("abcdefghijk")
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", upload)

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (1, 0)
    assert events == ["youtube-confirmed", "source-ledger", "thumbnail"]
    assert len([record for record in review_cycle["histories"] if record.get("status") == "uploaded"]) == 1
    assert review_cycle["histories"][-1]["youtube_id"] == "abcdefghijk"


def test_confirmed_upload_receipt_is_saved_before_cycle_cleanup(review_cycle, monkeypatch):
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {
        "action": "approve", "title": "Approved title", "description": "Approved description",
        "privacy": "private", "thumbnail_path": "approved-preview.png",
    })
    snapshots = []
    monkeypatch.setattr(app, "save_failed_queue", lambda items: snapshots.append([dict(item) for item in items]))

    def upload(*_args, on_confirmed=None, **_kwargs):
        on_confirmed("lmnopqrstuv")
        return "lmnopqrstuv"

    monkeypatch.setattr(app, "upload_video", upload)

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (1, 0)
    assert set(review_cycle["uploaded_ids"]) == {"reel-1"}
    assert snapshots[-1] == []
    uploaded_history = [record for record in review_cycle["histories"] if record.get("status") == "uploaded"]
    assert len(uploaded_history) == 1
    assert uploaded_history[0]["youtube_id"] == "lmnopqrstuv"


def test_known_pre_upload_failure_does_not_leave_unknown_attempt(review_cycle, monkeypatch):
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {
        "action": "approve", "title": "Approved title", "description": "Approved description",
        "privacy": "private", "thumbnail_path": "approved-preview.png",
    })
    snapshots = []
    monkeypatch.setattr(app, "save_failed_queue", lambda items: snapshots.append([dict(item) for item in items]))
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: app.UPLOAD_NOT_STARTED)

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert snapshots[-1][0]["last_error"] == "لم يبدأ طلب الرفع"
    assert snapshots[-1][0]["last_error"] != app.UPLOAD_RESULT_UNKNOWN


def test_upload_not_started_clears_intent_and_remains_retryable(review_cycle, monkeypatch):
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {
        "action": "approve", "title": "Approved title", "description": "Approved description",
        "privacy": "private", "thumbnail_path": "approved-preview.png",
    })
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: app.UPLOAD_NOT_STARTED)

    result = review_cycle["worker"]._run_cycle(
        review_cycle["context"], review_cycle["pages"], review_cycle["settings"], False
    )

    assert result == (0, 1)
    assert review_cycle["uploaded_ids"] == []
    final_queue = review_cycle["queued"][-1]
    assert final_queue["id"] == "reel-1"
    assert final_queue["last_error"] == "لم يبدأ طلب الرفع"
