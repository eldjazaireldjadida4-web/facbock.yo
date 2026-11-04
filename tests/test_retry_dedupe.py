"""اختبارات حماية التكرار: عناصر الإعادة المرفوعة تُسقط قبل أي تنزيل، والعناوين القصيرة تُقارن تطابقًا تامًا."""
from __future__ import annotations

import queue
import threading

import pytest

import fb_youtube_uploader_v49_pro as app


class FakeQuota:
    def __init__(self, daily_limit=50):
        self.daily_limit = daily_limit
        self.used = 0


@pytest.fixture
def worker(monkeypatch):
    instance = object.__new__(app.App)
    instance._stop = threading.Event()
    instance._uiq = queue.Queue()
    instance.stats = {"total_downloaded": 0, "total_uploaded": 0, "total_failed": 0, "cycles": 0}
    logs = []
    instance._log = logs.append
    instance._emit_page_status = lambda *_args, **_kwargs: None
    instance._cleanup_file = lambda *_args, **_kwargs: None
    instance._requeue_unprocessed = lambda failed, remaining: failed.extend(
        {"id": item["id"], "url": item["url"], "page": item.get("page", ""),
         "attempts": item.get("attempts", 0), "last_error": "مؤجل"}
        for item in remaining
    )
    monkeypatch.setattr(app, "save_stats", lambda *_args: None)
    monkeypatch.setattr(app, "send_telegram", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "disk_free_gb", lambda: 100.0)

    def forbidden_download(*_args, **_kwargs):
        raise AssertionError("لم ينبغ تنزيل مقطع مسجّل مسبقاً كمرفوع")

    monkeypatch.setattr(app, "download_video", forbidden_download)

    def forbidden_upload(*_args, **_kwargs):
        raise AssertionError("لم ينبغ رفع مقطع مسجّل مسبقاً كمرفوع")

    monkeypatch.setattr(app, "upload_video", forbidden_upload)
    instance.logs = logs
    return instance


def test_retry_item_already_uploaded_is_dropped_before_download(worker, monkeypatch):
    """عنصر في قائمة الفشل ومعرّفه في سجل المرفوع: يُسقط قبل التنزيل ويُسجل في السجل."""
    monkeypatch.setattr(app, "load_uploaded_ids", lambda: {"dup-1"})
    monkeypatch.setattr(app, "load_seen_ids", lambda: set())
    monkeypatch.setattr(app, "load_failed_queue", lambda: [
        {"id": "dup-1", "url": "https://www.facebook.com/watch/?v=dup-1", "page": "P", "attempts": 2},
    ])
    monkeypatch.setattr(app, "save_failed_queue", lambda items: None)

    up, fail = worker._run_cycle(
        {"yt": object(), "quota": FakeQuota(), "account": "a"}, [], {"max_attempts": 3}, False
    )

    assert (up, fail) == (0, 0)
    assert any("مسجّل مسبقاً كمرفوع" in message for message in worker.logs)


def test_cycle_retries_only_unuploaded_items(worker, monkeypatch):
    """قائمة فشل فيها مرفوع وغير مرفوع: المرفوع يُسقط والغير مرفوع يُعالج."""
    histories = []
    monkeypatch.setattr(app, "load_uploaded_ids", lambda: {"dup-1"})
    monkeypatch.setattr(app, "load_seen_ids", lambda: set())
    monkeypatch.setattr(app, "load_failed_queue", lambda: [
        {"id": "dup-1", "url": "https://www.facebook.com/watch/?v=dup-1", "attempts": 2},
        {"id": "new-1", "url": "https://www.facebook.com/watch/?v=new-1", "attempts": 1},
    ])
    monkeypatch.setattr(app, "append_history", histories.append)
    source = object.__new__(object)
    monkeypatch.setattr(app, "download_video", lambda *_args, **_kwargs: {"file": source})

    up, fail = worker._run_cycle(
        {"yt": object(), "quota": FakeQuota(), "account": "a"}, [], {"max_attempts": 3}, False
    )

    assert up == 0
    assert any("مسجّل مسبقاً كمرفوع" in message for message in worker.logs)


def test_meaningful_short_titles_match_exactly_in_channel_index():
    """عنوان حقيقي قصير يقابله نفسه في القناة بدقة، دون مطابقة ضبابية."""
    channel = [{"video_id": "abc", "title": "هدف رائع", "published_at": "", "duration": 30}]
    matches = app.find_channel_title_matches("هدف رائع", 30, channel)
    assert matches and matches[0]["video_id"] == "abc"


def test_generic_titles_skip_channel_title_matching():
    """العناوين العامة («Video») لا تُقارن بعناوين القناة؛ المطابقة عنوانية بلا معنى عندها."""
    channel = [{"video_id": "abc", "title": "هدف رائع", "published_at": "", "duration": 30}]
    assert app.find_channel_title_matches("Video", 30, channel) == []
    assert app.find_channel_title_matches("Facebook Live", 30, channel) == []


def test_meaningful_short_titles_do_not_match_different_words():
    channel = [{"video_id": "abc", "title": "أخبار اليوم", "published_at": "", "duration": 30}]
    assert app.find_channel_title_matches("هدف رائع", 30, channel) == []


def test_generic_titles_are_not_duplicates_in_content_memory(monkeypatch, tmp_path):
    """عنوان فيسبوك العام لا يُعد تكراراً حتى لو سبق رفع عنوان عام بنفس النص."""
    monkeypatch.setattr(app, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(app, "load_state", lambda: {"title_fingerprints": ["video"]})
    assert app.is_duplicate_content("Video") is False
    monkeypatch.setattr(app, "load_state", lambda: {"title_fingerprints": ["هدفرائع"]})
    assert app.is_duplicate_content("هدف رائع") is True