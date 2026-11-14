from __future__ import annotations

import json
import threading

import pytest

import fb_youtube_uploader_v49_pro as app


class UploadRequest:
    def __init__(self, events, video_id="mnopqrstuvw"):
        self.events = events
        self.video_id = video_id

    def next_chunk(self):
        self.events.append("youtube-confirmed")
        return None, {"id": self.video_id}


class StatusRequest:
    def __init__(self, events):
        self.events = events

    def execute(self):
        self.events.append("status-verified")
        return {"items": [{"status": {"privacyStatus": "private"}}]}


class YouTube:
    def __init__(self):
        self.events = []
        self.request = UploadRequest(self.events)
        self.insert_calls = 0

    def videos(self):
        return self

    def insert(self, **_kwargs):
        self.insert_calls += 1
        return self.request

    def list(self, **_kwargs):
        return StatusRequest(self.events)


def test_upload_receipt_is_saved_before_optional_verification(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    youtube = YouTube()

    result = app.upload_video(
        youtube,
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        on_confirmed=lambda video_id: youtube.events.append(("receipt", video_id)),
    )

    assert result == "mnopqrstuvw"
    assert youtube.events == [
        "youtube-confirmed",
        ("receipt", "mnopqrstuvw"),
        "status-verified",
    ]


def test_receipt_persistence_failure_never_reports_a_safe_retry(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    youtube = YouTube()

    def fail_persist(_video_id):
        raise OSError("disk full")

    with pytest.raises(RuntimeError, match="mnopqrstuvw"):
        app.upload_video(
            youtube,
            {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
            on_confirmed=fail_persist,
        )

    assert youtube.insert_calls == 1
    assert youtube.events == ["youtube-confirmed"]


def test_failure_before_first_chunk_is_marked_safe_to_retry(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    youtube = YouTube()

    def fail_intent():
        raise OSError("cannot persist intent")

    result = app.upload_video(
        youtube,
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        before_upload=fail_intent,
    )

    assert result == app.UPLOAD_NOT_STARTED
    assert youtube.insert_calls == 1
    assert youtube.events == []


def test_stop_before_upload_does_not_create_youtube_request(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    youtube = YouTube()
    stop_event = threading.Event()
    stop_event.set()

    result = app.upload_video(
        youtube,
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        stop_event=stop_event,
    )

    assert result == app.UPLOAD_NOT_STARTED
    assert youtube.insert_calls == 0


def test_stop_after_intent_but_before_first_chunk_is_not_marked_unknown(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    youtube = YouTube()
    stop_event = threading.Event()

    result = app.upload_video(
        youtube,
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        stop_event=stop_event,
        before_upload=stop_event.set,
    )

    assert result == app.UPLOAD_NOT_STARTED
    assert youtube.insert_calls == 1
    assert youtube.events == []


def test_stop_after_upload_session_starts_marks_result_unknown(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    stop_event = threading.Event()
    youtube = YouTube()

    class InterruptedRequest:
        def next_chunk(self):
            stop_event.set()
            return None, None

    youtube.request = InterruptedRequest()
    result = app.upload_video(
        youtube,
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        stop_event=stop_event,
    )

    assert result == app.UPLOAD_RESULT_UNKNOWN
    assert youtube.insert_calls == 1


def test_start_is_ignored_while_worker_is_alive():
    class Worker:
        def is_alive(self):
            return True

    instance = object.__new__(app.App)
    instance._closing = False
    instance._thread = Worker()
    logs = []
    instance._log = logs.append

    instance._do_start(False)

    assert logs and "متزامنة" in logs[0]


def test_finish_worker_reports_pending_items_separately_from_failures():
    class Widget:
        def __init__(self):
            self.values = {}

        def config(self, **values):
            self.values.update(values)

        def set(self, value):
            self.values["value"] = value

        def refresh(self):
            pass

    instance = object.__new__(app.App)
    instance._closing = False
    instance.start_once_btn = Widget()
    instance.watch_btn = Widget()
    instance.stop_btn = Widget()
    instance.countdown_var = Widget()
    instance.stats_card = Widget()
    instance.status_lbl = Widget()

    app.App._finish_worker(instance, {
        "uploaded": 0,
        "failed": 0,
        "pending": 59,
        "fatal": None,
        "watch": True,
    })

    assert "59" in instance.status_lbl.values["text"]
    assert "قيد الانتظار/المراجعة" in instance.status_lbl.values["text"]


def test_closing_app_waits_for_worker_before_destroying_root():
    class Worker:
        alive = True

        def is_alive(self):
            return self.alive

    class Root:
        def __init__(self):
            self.callbacks = []
            self.destroyed = False

        def after(self, _delay, callback):
            self.callbacks.append(callback)

        def destroy(self):
            self.destroyed = True

    class Status:
        def config(self, **_kwargs):
            return None

    instance = object.__new__(app.App)
    instance._closing = False
    instance._stop = threading.Event()
    instance._pending_review = None
    instance._thread = Worker()
    instance.root = Root()
    instance.status_lbl = Status()
    instance._save_ui_settings = lambda: None

    instance._on_close()
    assert not instance.root.destroyed
    assert instance._stop.is_set()

    instance._thread.alive = False
    instance.root.callbacks.pop(0)()
    assert instance.root.destroyed


def test_uploaded_source_id_is_appended_only_once(tmp_path):
    ledger = tmp_path / "uploaded_ids.txt"

    assert app._append_id(ledger, "source-123") is True
    assert app._append_id(ledger, "source-123") is False
    assert ledger.read_text(encoding="utf-8") == "source-123\n"


def test_failed_queue_persistence_deduplicates_source_ids(tmp_path, monkeypatch):
    queue_file = tmp_path / "failed_queue.json"
    monkeypatch.setattr(app, "FAILED_QUEUE_FILE", str(queue_file))

    app.save_failed_queue([
        {"id": "source-1", "attempts": 1, "last_error": "download timeout"},
        {"id": "source-1", "attempts": 2, "last_error": app.UPLOAD_RESULT_UNKNOWN},
    ])

    saved = json.loads(queue_file.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["attempts"] == 2
    assert saved[0]["last_error"] == app.UPLOAD_RESULT_UNKNOWN


def test_scraper_deduplicates_alternate_facebook_urls_for_one_video():
    links = [
        "https://www.facebook.com/reel/1760103355",
        "https://www.facebook.com/watch/?v=1760103355",
        "https://www.facebook.com/video.php?video_id=1760103355",
    ]

    fresh = app.filter_new_reels(links, set(), set(), set())

    assert len(fresh) == 1
    assert fresh[0]["id"] == "1760103355"
