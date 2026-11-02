from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "fb_youtube_uploader_v49_pro.py"
spec = importlib.util.spec_from_file_location("channel_dedupe_app", MODULE_PATH)
assert spec and spec.loader
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FakeRequest:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeResource:
    def __init__(self, handler):
        self.handler = handler

    def list(self, **kwargs):
        return FakeRequest(self.handler(**kwargs))


class FakeYoutube:
    def __init__(self):
        self.playlist_calls = []
        self.video_calls = []
        self.fail = False

    def channels(self):
        return FakeResource(lambda **_kwargs: {
            "items": [{
                "id": "channel-1",
                "snippet": {"title": "قناة اختبار"},
                "contentDetails": {"relatedPlaylists": {"uploads": "uploads-1"}},
            }]
        } if not self.fail else RuntimeError("offline"))

    def playlistItems(self):
        def handle(**kwargs):
            self.playlist_calls.append(kwargs)
            token = kwargs.get("pageToken")
            if token is None:
                return {
                    "items": [
                        {"snippet": {"title": "خبر أول", "publishedAt": "2026-01-01T00:00:00Z"},
                         "contentDetails": {"videoId": "video-1", "videoPublishedAt": "2026-01-01T00:00:00Z"}},
                        {"snippet": {"title": "Deleted video"},
                         "contentDetails": {"videoId": "deleted-id"}},
                    ],
                    "nextPageToken": "page-2",
                }
            return {
                "items": [
                    {"snippet": {"title": "خبر ثانٍ", "publishedAt": "2026-01-02T00:00:00Z"},
                     "contentDetails": {"videoId": "video-2", "videoPublishedAt": "2026-01-02T00:00:00Z"}},
                ]
            }

        return FakeResource(handle)

    def videos(self):
        def handle(**kwargs):
            self.video_calls.append(kwargs)
            ids = kwargs["id"].split(",")
            durations = {"video-1": "PT1M30S", "video-2": "P1DT1H2M3S"}
            return {"items": [
                {"id": video_id, "contentDetails": {"duration": durations[video_id]}}
                for video_id in ids
            ]}

        return FakeResource(handle)


def test_fingerprint_ignores_arabic_diacritics_and_punctuation():
    assert app.content_fingerprint("نِقاشٌ طريف — مقطع!") == app.content_fingerprint("نقاش طريف مقطع")


def test_channel_title_match_includes_duration_and_video_id():
    title = "توقعات حفيظ دراجي حول حظوظ المنتخب الوطني في كأس إفريقيا"
    matches = app.find_channel_title_matches(title, 221, [{
        "video_id": "existing-123",
        "title": title,
        "published_at": "2026-09-01T12:00:00Z",
        "duration": 221,
    }])
    assert len(matches) == 1
    assert matches[0]["video_id"] == "existing-123"
    assert matches[0]["duration_delta"] == 0
    assert matches[0]["similarity"] == 1.0


def test_unrelated_channel_title_is_not_flagged():
    matches = app.find_channel_title_matches(
        "توقعات حفيظ دراجي حول حظوظ المنتخب الوطني في كأس إفريقيا", 221,
        [{"video_id": "other", "title": "طقس الغد في وهران", "duration": 220}],
    )
    assert matches == []


def test_exact_file_hash_is_persisted(tmp_path, monkeypatch):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"same-source-video")
    monkeypatch.setattr(app, "STATE_FILE", str(tmp_path / "state.json"))
    digest = app.file_sha256(media)
    app.remember_content("عنوان الفيديو", digest)
    assert app.is_known_media_hash(digest)
    assert not app.is_known_media_hash("different-hash")


def test_channel_index_paginates_filters_deleted_and_caches(tmp_path, monkeypatch):
    cache_path = tmp_path / "channel_video_index.json"
    monkeypatch.setattr(app, "CHANNEL_INDEX_FILE", str(cache_path))
    client = FakeYoutube()

    videos = app.fetch_channel_video_index(client, "account-1")

    assert [video["video_id"] for video in videos] == ["video-1", "video-2"]
    assert videos[0]["duration"] == 90
    assert videos[1]["duration"] == 90123
    assert len(client.playlist_calls) == 2
    assert len(client.video_calls) == 1
    client.fail = True
    assert app.fetch_channel_video_index(client, "account-1") == videos


def test_stale_channel_index_is_used_when_api_is_unavailable(tmp_path, monkeypatch):
    cache_path = tmp_path / "channel_video_index.json"
    monkeypatch.setattr(app, "CHANNEL_INDEX_FILE", str(cache_path))
    cached = [{"video_id": "cached", "title": "عنوان محفوظ", "duration": 30}]
    app._write_json(cache_path, {
        "account_key": "account-1",
        "fetched_at": 0,
        "videos": cached,
    })
    client = FakeYoutube()
    client.fail = True

    stale = app.fetch_channel_video_index(client, "account-1", force=True)
    assert [video["video_id"] for video in stale] == ["cached"]
    assert stale[0]["_stale_index"] is True

def test_failed_channel_index_refresh_is_cached_briefly(tmp_path, monkeypatch):
    """بعد فشل التحديث تُستخدم النسخة المحلية بهدوء دون محاولات API متكررة."""
    cache_path = tmp_path / "channel_video_index.json"
    monkeypatch.setattr(app, "CHANNEL_INDEX_FILE", str(cache_path))
    app._write_json(cache_path, {
        "account_key": "account-1",
        "fetched_at": 0,
        "videos": [{"video_id": "cached", "title": "عنوان محفوظ", "duration": 30}],
    })
    client = FakeYoutube()
    client.fail = True
    app._channel_index_fail.update({"account": "", "at": 0.0})

    stale = app.fetch_channel_video_index(client, "account-1", force=True)
    assert stale[0]["_stale_index"] is True

    client.fail = False
    again = app.fetch_channel_video_index(client, "account-1")
    assert [video["video_id"] for video in again] == ["cached"]
    assert again[0]["_stale_index"] is True
    assert client.playlist_calls == []
    assert client.video_calls == []

    fresh = app.fetch_channel_video_index(client, "account-1", force=True)
    assert [video["video_id"] for video in fresh] == ["video-1", "video-2"]
    assert app._channel_index_fail == {"account": "", "at": 0.0}


def test_malformed_channel_cache_timestamp_does_not_crash(tmp_path, monkeypatch):
    cache_path = tmp_path / "channel_video_index.json"
    monkeypatch.setattr(app, "CHANNEL_INDEX_FILE", str(cache_path))
    app._write_json(cache_path, {
        "account_key": "account-1",
        "fetched_at": "invalid",
        "videos": [{"video_id": "old", "title": "عنوان قديم"}],
    })
    client = FakeYoutube()

    videos = app.fetch_channel_video_index(client, "account-1")
    assert [video["video_id"] for video in videos] == ["video-1", "video-2"]

def test_automatic_dedupe_pauses_when_channel_index_is_unverified():
    assert app.should_pause_for_unverified_channel(True, False, False)
    assert not app.should_pause_for_unverified_channel(True, True, False)
    assert not app.should_pause_for_unverified_channel(True, False, True)
    assert not app.should_pause_for_unverified_channel(False, False, False)


def test_duplicate_review_items_are_held_until_review_is_enabled():
    held_item = {"id": "duplicate-1", "last_error": "duplicate_review_required"}
    retry_item = {"id": "transient-1", "last_error": "timeout"}

    ready, held = app.split_duplicate_review_queue([held_item, retry_item], False)

    assert ready == [retry_item]
    assert held == [held_item]
    ready, held = app.split_duplicate_review_queue([held_item, retry_item], True)
    assert ready == [held_item, retry_item]
    assert held == []
