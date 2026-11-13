"""انحدار اختبارات التقوية 4.49.0 — الأخطاء التي كانت تُسقط العمل بصمت.

كل اختبار هنا يقابل عطلاً حقيقياً: مسار يبتلع استثناءً، ملفاً يُقرأ بلا
تحقق، أو نتيجة رفع تُسجَّل نجاحاً وهي ليست كذلك.
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import urllib.error
from pathlib import Path

import pytest

import ai_client
import content_safety
import control_plane
import facebook_auth
import fb_youtube_uploader_v49_pro as app
import fburls
import safe_logging
import uploaderrors
from config_store import read_config
from settings_validation import validate_configuration


# ─────────────────────────── السجلات والأسرار ───────────────────────────
def test_basic_authorization_header_is_redacted() -> None:
    redacted = safe_logging.redact_sensitive_text(
        "Authorization: Basic dXNlcjpwYXNz\nProxy-Authorization: Basic cHJveHk6cHc="
    )
    assert "dXNlcjpwYXNz" not in redacted
    assert "cHJveHk6cHc=" not in redacted
    assert redacted.count("[REDACTED]") == 2


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX-only")
def test_secure_log_handler_creates_file_private_from_the_start(tmp_path: Path) -> None:
    handler = safe_logging.SecureRotatingFileHandler(
        tmp_path / "errors.log", maxBytes=1024, backupCount=1, encoding="utf-8"
    )
    try:
        assert stat.S_IMODE(os.stat(tmp_path / "errors.log").st_mode) == 0o600
    finally:
        handler.close()


# ─────────────────────────── استرداد الإعدادات ───────────────────────────
def test_read_config_recovers_from_backup_when_primary_is_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere.ini"
    real.write_text("[SETTINGS]\nVIDEO_DIRECTORY = wrong\n", encoding="utf-8")
    config_path = tmp_path / "config.ini"
    config_path.symlink_to(real)
    (tmp_path / "config.ini.bak1").write_text(
        "[SETTINGS]\nVIDEO_DIRECTORY = saved-videos\n", encoding="utf-8"
    )

    cfg, recovered_from = read_config(config_path)

    assert recovered_from == tmp_path / "config.ini.bak1"
    assert cfg.get("SETTINGS", "VIDEO_DIRECTORY") == "saved-videos"
    assert config_path.is_symlink()
    assert real.read_text(encoding="utf-8").strip().endswith("wrong")


# ─────────────────────────── طابور التشغيل ───────────────────────────
def test_deduplicate_tolerates_non_text_last_error() -> None:
    items = [
        {"id": "a", "last_error": ["corrupt"]},
        {"id": "a", "last_error": "upload_rejected"},
    ]
    merged = control_plane.deduplicate_queue_items(items)
    assert merged[0]["last_error"] == "upload_rejected"


def test_queue_helpers_ignore_non_mapping_entries() -> None:
    assert control_plane.split_duplicate_review_queue(["oops", {"id": "a"}], True)
    released, _count = control_plane.release_policy_holds(["oops", {"id": "a"}])
    assert all(isinstance(item, dict) for item in released)


def test_oauth_token_preflight_survives_invalid_utf8(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_bytes(b'{"refresh_token":"\xff\xfe"}')

    errors = control_plane.validate_unattended_oauth_token(token, [])

    assert errors and "غير قابل للقراءة" in errors[0]


# ─────────────────────────── مفاتيح Gemini ───────────────────────────
def test_plain_text_key_list_is_tolerated() -> None:
    assert ai_client.ai_available_key(["AIzaPLAINTEXT"]) == (None, None)


def test_aware_exhausted_until_does_not_crash() -> None:
    keys = [{"key": "k", "exhausted_until": "2000-01-01T00:00:00+00:00"}]
    assert ai_client.ai_available_key(keys) == (0, "k")


def test_extract_json_keeps_first_object_when_more_follow() -> None:
    assert ai_client._extract_json('{"title":"x"} then {"note":"y"}') == {"title": "x"}
    assert ai_client._extract_json('نص {"title":"x","tags":["a"]} ثم {"k":2}')["title"] == "x"


class _FakeResp:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}
        ).encode("utf-8")


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test", code, "err", {}, io.BytesIO(body))


def test_invalid_argument_400_uses_short_cooldown_not_a_day(monkeypatch, tmp_path: Path) -> None:
    keys_file = tmp_path / "ai_keys.json"
    keys_file.write_text(
        json.dumps([{"key": "good", "exhausted_until": "", "last_error": ""}]),
        encoding="utf-8",
    )
    outcomes = [
        _http_error(400, b'{"error":{"status":"INVALID_ARGUMENT","message":"bad maxOutputTokens"}}'),
    ]

    def fake_urlopen(_req, timeout=None):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(outcome)

    monkeypatch.setattr(ai_client.urllib.request, "urlopen", fake_urlopen)

    def read_json(path, default):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write_json(path, value):
        Path(path).write_text(json.dumps(value), encoding="utf-8")

    text, keys = ai_client.gemini_generate(
        "نص", "gemini-2.5-flash", keys_file, read_json, write_json
    )

    # خطأ طلب لا مفتاح: لا يُعاد استخدام المفتاح في هذه الدورة، لكن التبريد
    # قصير (دقائق) بدل تعطيله حتى منتصف الليل.
    assert text is None
    assert "HTTP 400" in keys[0]["last_error"]
    from datetime import datetime, timedelta

    cooldown = datetime.fromisoformat(keys[0]["exhausted_until"])
    assert datetime.now() < cooldown <= datetime.now() + timedelta(hours=1)


# ─────────────────────────── سجل الحقوق ───────────────────────────
def test_rights_manifest_recovers_from_backup_when_primary_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "rights_manifest.json"
    path.write_text('{"version": 1, "items": ', encoding="utf-8")
    (tmp_path / "rights_manifest.json.bak1").write_text(
        json.dumps({"version": 1, "items": {"123": {"rights_basis": "owned"}}}),
        encoding="utf-8",
    )

    manifest = content_safety.load_rights_manifest(str(path))

    assert manifest["items"]["123"]["rights_basis"] == "owned"


def test_rights_manifest_missing_file_is_empty_not_backup(tmp_path: Path) -> None:
    path = tmp_path / "rights_manifest.json"
    (tmp_path / "rights_manifest.json.bak1").write_text(
        json.dumps({"version": 1, "items": {"old": {}}}), encoding="utf-8"
    )

    assert content_safety.load_rights_manifest(str(path)) == {"version": 1, "items": {}}


# ─────────────────────────── الإعدادات والروابط والجلسة ───────────────────────────
def test_zero_failed_max_age_days_is_accepted() -> None:
    cfg = app.configparser.ConfigParser(interpolation=None)
    cfg.read_dict({
        "SETTINGS": {
            "VIDEO_DIRECTORY": "videos",
            "CLIENT_SECRETS_FILE": "client_secrets.json",
            "PRIVACY": "private",
            "DRY_RUN": "false",
            "FAILED_MAX_AGE_DAYS": "0",
        },
        "STUDIO": {
            "RIGHTS_GATE_MODE": "strict",
            "RIGHTS_OPERATOR_BASIS": "owned",
            "REVIEW_BEFORE_UPLOAD": "true",
            "SCHEDULE": "false",
            "SCHEDULE_MIN": "20",
            "SCHEDULE_MAX": "60",
        },
        "SAFETY": {"ALLOW_PUBLIC_AUTOMATION": "false"},
    })

    assert not any("FAILED_MAX_AGE_DAYS" in error for error in validate_configuration(cfg))


def test_page_tab_links_normalize_to_reels() -> None:
    assert fburls.normalize_channel_url("https://www.facebook.com/SomePage/about") == \
        "https://www.facebook.com/SomePage/reels"
    assert fburls.normalize_channel_url("https://www.facebook.com/SomePage/posts/123") == \
        "https://www.facebook.com/SomePage/reels"
    # مسارات محجوزة تُترك كما هي ولا تُقلب إلى /reels
    assert fburls.normalize_channel_url("https://www.facebook.com/groups/123") == \
        "https://www.facebook.com/groups/123"


def test_expired_resumable_session_is_not_classified_as_rejection() -> None:
    class Gone:
        resp = type("R", (), {"status": 410})()
        content = b""

    assert uploaderrors.classify_upload_error(Gone()) == "session_expired"
    assert app.classify_upload_error(Gone()) == "session_expired"


def test_tilde_cookie_path_is_expanded_for_loading(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "cooks").mkdir(parents=True)
    (home / "cooks" / "cookies.txt").write_text(
        ".facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t1\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\txs\tabc\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    active, status = facebook_auth._active_facebook_cookies("~/cooks/cookies.txt")

    assert status["file_exists"] is True
    assert status["has_active_session_pair"] is True
    assert len(active) == 2


# ─────────────────────────── الرفع ───────────────────────────
class _RejectedStatus:
    def execute(self):
        return {"items": [{"status": {"privacyStatus": "private", "uploadStatus": "rejected"},
                            "processingDetails": {"processingStatus": "failed"}}]}


class _RejectingYouTube:
    def videos(self):
        return self

    def insert(self, **_kwargs):
        return _Request()

    def list(self, **_kwargs):
        return _RejectedStatus()


class _Request:
    def next_chunk(self):
        return None, {"id": "mnopqrstuvw"}


def test_post_upload_rejection_is_not_reported_as_success(tmp_path: Path, monkeypatch) -> None:
    receipts = tmp_path / "upload_receipts.json"
    monkeypatch.setattr(app, "UPLOAD_RECEIPTS_FILE", str(receipts))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    confirmed = []

    def confirm(video_id):
        confirmed.append(video_id)
        app.persist_upload_receipt("src-1", video_id)

    result = app.upload_video(
        _RejectingYouTube(),
        {"file": str(video), "title": "title", "desc": "description"},
        on_confirmed=confirm,
    )

    assert confirmed == ["mnopqrstuvw"]      # الإيصال أولاً: لا رفع مكرر
    assert result == app.UPLOAD_REJECTED     # لكن ليس نجاحاً
    stored = json.loads(receipts.read_text(encoding="utf-8"))["receipts"]
    record = next(iter(stored.values()))
    assert record["status"] == "rejected"


def test_upload_status_failure_detects_processing_failure() -> None:
    assert app.upload_status_failure({"uploadStatus": "uploaded", "processingStatus": "failed"})
    assert app.upload_status_failure({"uploadStatus": "rejected"})
    assert app.upload_status_failure({"uploadStatus": "uploaded"}) == ""
    assert app.upload_status_failure(None) == ""


class _IdleProgress:
    def progress(self):
        return 0.0

    resumable_progress = 0


class _StalledRequest:
    def __init__(self):
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        return _IdleProgress(), None


class _StalledYouTube:
    def __init__(self):
        self.request = _StalledRequest()

    def videos(self):
        return self

    def insert(self, **_kwargs):
        return self.request


def test_stalled_resumable_session_terminates_instead_of_looping(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app, "IDLE_CHUNK_WAIT_SECONDS", 0)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    youtube = _StalledYouTube()

    result = app.upload_video(
        youtube,
        {"file": str(video), "title": "title", "desc": "description"},
    )

    assert result == app.UPLOAD_RESULT_UNKNOWN
    assert youtube.request.calls == app.MAX_IDLE_CHUNKS + 1


def test_promotion_keeps_the_file_when_permissions_cannot_be_restricted(
    tmp_path: Path, monkeypatch
) -> None:
    attempt = tmp_path / ".fb2yt-attempt"
    attempt.mkdir()
    source = attempt / "clip.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(app, "restrict_file_permissions", lambda _path: False)

    destination = app._promote_downloaded_file(str(source), str(attempt), str(tmp_path), "123")

    assert os.path.isfile(destination)
    assert not os.path.exists(source)


@pytest.mark.skipif(sys.platform == "win32", reason="sparse truncate is POSIX-oriented here")
def test_media_validation_rejects_files_above_youtube_256gb_limit(
    tmp_path: Path, monkeypatch
) -> None:
    video = tmp_path / "huge.mp4"
    video.write_bytes(b"x")
    try:
        os.truncate(video, 256_000_000_001)
    except OSError:
        pytest.skip("filesystem does not support sparse files")
    ok, details = app.validate_media(str(video))
    assert ok is False
    assert details["error"] == "file-too-large"


# ─────────────────────────── دورة حياة الواجهة ───────────────────────────
class _FakeRoot:
    def __init__(self) -> None:
        self.after_calls = []
        self.destroyed = False

    def after(self, delay, callback=None):
        self.after_calls.append((delay, callback))
        return "after-id"

    def destroy(self):
        self.destroyed = True


class _FakeLogWidget:
    def configure(self, **_kwargs):
        pass

    def delete(self, *_args):
        pass

    def insert(self, *_args):
        pass

    def see(self, *_args):
        pass


def _bare_app() -> app.App:
    import queue

    instance = app.App.__new__(app.App)
    instance.root = _FakeRoot()
    instance._logq = queue.Queue()
    instance._uiq = queue.Queue()
    instance._last_log_replace = False
    instance._closing = False
    instance.log_txt = _FakeLogWidget()
    return instance


def test_poll_survives_a_failing_event_and_rearms(monkeypatch) -> None:
    instance = _bare_app()
    instance._uiq.put(("progress", 5))

    def explode(_event, _payload):
        raise RuntimeError("bad payload")

    monkeypatch.setattr(instance, "_handle_ui_event", explode)

    instance._poll()  # must not raise

    assert instance.root.after_calls == [(100, instance._poll)]


def test_poll_stops_rearming_after_close() -> None:
    instance = _bare_app()
    instance._closing = True

    instance._poll()

    assert instance.root.after_calls == []


def test_close_has_a_deadline_instead_of_waiting_forever(monkeypatch) -> None:
    import time

    instance = _bare_app()

    class _Alive:
        def is_alive(self):
            return True

    instance._thread = _Alive()
    instance.status_lbl = type("L", (), {"config": lambda self, **_kw: None})()
    monkeypatch.setattr(app, "CLOSE_WAIT_SECONDS", 0.0)
    instance._close_deadline = time.monotonic() - 1

    instance._finish_close_when_idle()

    assert instance.root.destroyed is True
