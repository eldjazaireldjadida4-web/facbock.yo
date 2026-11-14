from __future__ import annotations

import configparser
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from googleapiclient.errors import HttpError

MODULE_PATH = Path(__file__).resolve().parents[1] / "fb_youtube_uploader_v49_pro.py"
spec = importlib.util.spec_from_file_location("quota49", MODULE_PATH)
assert spec and spec.loader
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FakeResp:
    def __init__(self, status: int, reason: str = "Service Unavailable") -> None:
        self.status = status
        self.reason = reason


def _http_error(status: int, reason: str = "backendError") -> HttpError:
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return HttpError(FakeResp(status), body)


def _config(text: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_string(text)
    return cfg


def _fake_state(monkeypatch, initial=None):
    state = dict(initial or {})
    monkeypatch.setattr(app, "load_state", lambda: dict(state))

    def save_state(updated):
        state.clear()
        state.update(updated)

    monkeypatch.setattr(app, "save_state", save_state)
    today = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(app, "pt_now", lambda: today)
    return state, today.date().isoformat()


def test_new_quota_setting_defaults_to_the_free_tier_upload_count():
    # 10,000 وحدة / 1,600 لكل رفع = 6 رفعات فعلية في المشروع المجاني.
    assert app._daily_upload_limit_from_config(_config("[SETTINGS]\n")) == 6
    assert app._daily_upload_limit_from_config(
        _config("[SETTINGS]\nDAILY_UPLOAD_LIMIT = 37\nDAILY_QUOTA = 10000\n")
    ) == 37


def test_legacy_quota_setting_migrates_to_upload_count():
    assert app._daily_upload_limit_from_config(
        _config("[SETTINGS]\nDAILY_QUOTA = 10000\n")
    ) == 6
    assert app._daily_upload_limit_from_config(
        _config("[SETTINGS]\nDAILY_QUOTA = 8000\n")
    ) == 5


def test_upload_count_is_shared_across_oauth_accounts(monkeypatch):
    state, _ = _fake_state(monkeypatch)
    first = app.QuotaManager(100, account="account1")
    first.register(4)
    second = app.QuotaManager(100, account="account2")
    assert second.used == 4
    assert state["youtube_uploads_used"] == 4


def test_same_day_legacy_account_counters_are_migrated(monkeypatch):
    _fake_state(monkeypatch, {
        "quota_used_account1": 3200,
        "quota_date_account1": "2026-10-17",
        "quota_used_account2": 1600,
        "quota_date_account2": "2026-10-17",
    })
    quota = app.QuotaManager(100)
    assert quota.used == 3


def test_each_insert_attempt_counts_against_upload_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(app.time, "sleep", lambda *_args: None)
    state, _ = _fake_state(monkeypatch)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")

    class Request:
        def next_chunk(self):
            raise _http_error(503)

    class YouTube:
        def __init__(self):
            self.insert_calls = 0

        def videos(self):
            return self

        def insert(self, **_kwargs):
            self.insert_calls += 1
            return Request()

    yt = YouTube()
    quota = app.QuotaManager(2)
    result = app.upload_video(
        yt,
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        retries=3,
        quota_manager=quota,
    )
    assert result == app.UPLOAD_RESULT_UNKNOWN
    assert yt.insert_calls == 1
    assert quota.used == app.QuotaManager.COST_UPLOAD
    assert state["youtube_uploads_used"] == app.QuotaManager.COST_UPLOAD





def test_channel_upload_limit_does_not_exhaust_project_bucket(monkeypatch, tmp_path):
    state, _ = _fake_state(monkeypatch)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")

    class Request:
        def next_chunk(self):
            raise _http_error(400, "uploadLimitExceeded")

    class YouTube:
        def __init__(self):
            self.insert_calls = 0

        def videos(self):
            return self

        def insert(self, **_kwargs):
            self.insert_calls += 1
            return Request()

    youtube = YouTube()
    quota = app.QuotaManager(100)
    result = app.upload_video(
        youtube,
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        retries=3,
        quota_manager=quota,
    )
    assert app.classify_upload_error(_http_error(400, "uploadLimitExceeded")) == "channel_limit"
    assert result == "CHANNEL_LIMIT_ERROR"
    assert youtube.insert_calls == 1
    assert quota.used == 1
    assert quota.can_afford()
    assert state["youtube_uploads_used"] == 1
