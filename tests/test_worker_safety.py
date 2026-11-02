from __future__ import annotations

import importlib.util
import queue
import threading
from pathlib import Path


MODULE_PATH = Path(__file__).parent / "fixtures" / "versions" / "fb_youtube_uploader_v32_fixed.py"
spec = importlib.util.spec_from_file_location("fixed_app", MODULE_PATH)
assert spec and spec.loader
fixed_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixed_app)


class FakeDriver:
    def quit(self) -> None:
        return None


def test_worker_completes_without_accessing_tkinter_widgets(monkeypatch) -> None:
    application = object.__new__(fixed_app.App)
    application._stop = threading.Event()
    application._uiq = queue.Queue()
    application.stats = {"total_uploaded": 0, "total_failed": 0}
    application._log = lambda _message, replace_last=False: None

    saved_ids: list[str] = []
    monkeypatch.setattr(fixed_app, "load_uploaded_ids", lambda: set())
    monkeypatch.setattr(fixed_app, "get_youtube", lambda _account: (object(), None))
    monkeypatch.setattr(fixed_app, "init_driver", lambda _headless, _log: FakeDriver())
    monkeypatch.setattr(
        fixed_app,
        "get_reels",
        lambda _driver, _url, _scroll, _limit, _log: ["https://facebook.com/reel/123"],
    )
    monkeypatch.setattr(
        fixed_app,
        "download_video",
        lambda *_args: {"file": "video.mp4", "title": "عنوان", "desc": "وصف"},
    )
    monkeypatch.setattr(fixed_app, "upload_video", lambda *_args: "youtube-video-id")
    monkeypatch.setattr(fixed_app, "save_uploaded_id", saved_ids.append)
    monkeypatch.setattr(fixed_app, "save_stats", lambda _stats: None)

    settings = {
        "account": "channel",
        "headless": True,
        "remove_tags": False,
        "extra_description": "",
        "schedule_enabled": False,
        "min_delay": 20,
        "max_delay": 60,
        "privacy": "public",
        "tags": ["Facebook", "Reel"],
        "delete_after_upload": False,
        "between_videos": 0,
    }
    application._worker(
        [{"url": "https://facebook.com/page/reels/", "name": "page", "scroll": 1, "limit": 1}],
        settings,
    )

    events = []
    while not application._uiq.empty():
        events.append(application._uiq.get_nowait())

    assert saved_ids == ["123"]
    assert ("progress", 100) in events
    assert events[-1] == ("finished", {"uploaded": 1, "failed": 0, "fatal": None})


def test_resolve_downloaded_file_prefers_existing_merged_mp4(tmp_path: Path) -> None:
    merged = tmp_path / "reel.mp4"
    merged.write_bytes(b"video")

    class FakeYdl:
        def prepare_filename(self, _info):
            return str(tmp_path / "reel.webm")

    assert fixed_app._resolve_downloaded_file({}, FakeYdl()) == str(merged)
