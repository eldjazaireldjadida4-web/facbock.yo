from __future__ import annotations

import queue
import threading

import fb_youtube_uploader_v49_pro as app


def test_watch_cycle_exception_is_reported_even_when_stopped(monkeypatch):
    class Lock:
        def __init__(self, *_args):
            pass

        def acquire(self):
            return self

        def release(self):
            pass

    class Quota:
        used = 0
        daily_limit = 10

    worker = object.__new__(app.App)
    worker._stop = threading.Event()
    worker._uiq = queue.Queue()
    worker._logq = queue.Queue()
    worker.stats = {"total_uploaded": 0, "total_failed": 0}
    worker._log = lambda *_args, **_kwargs: None
    worker._run_cycle = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cycle broke"))
    worker._maybe_daily_summary = lambda *_args: None
    worker._countdown_wait = lambda *_args: worker._stop.set()

    monkeypatch.setattr(app, "AutomationRunLock", Lock)
    monkeypatch.setattr(app, "get_youtube", lambda *_args, **_kwargs: (object(), None))
    monkeypatch.setattr(app, "QuotaManager", lambda *_args, **_kwargs: Quota())
    monkeypatch.setattr(app, "send_telegram", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "save_stats", lambda *_args: None)
    monkeypatch.setattr(app, "load_failed_queue", lambda: [])

    worker._worker(
        [],
        {
            "account": "channel",
            "cli_mode": False,
            "daily_upload_limit": 10,
            "interval_minutes": 1,
            "tg_errors": False,
        },
        watch_mode=True,
    )

    events = []
    while not worker._uiq.empty():
        events.append(worker._uiq.get_nowait())
    finished = [payload for name, payload in events if name == "finished"]

    assert len(finished) == 1
    assert finished[0]["cycle_errors"] == 1
    assert finished[0]["fatal"] is None
    assert finished[0]["uploaded"] == 0
