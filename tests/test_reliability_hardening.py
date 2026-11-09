from __future__ import annotations

import configparser
import json
import os
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app
from config_store import ConfigRecoveryError, read_config, write_config_atomically
from file_security import open_private_append
from safe_logging import mask_secret, redact_sensitive_text
from settings_validation import validate_configuration


def _valid_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({
        "SETTINGS": {
            "VIDEO_DIRECTORY": "videos",
            "CLIENT_SECRETS_FILE": "client_secrets.json",
            "PRIVACY": "private",
            "DRY_RUN": "false",
            "BETWEEN_VIDEOS": "10",
            "DAILY_UPLOAD_LIMIT": "100",
            "MAX_ATTEMPTS": "3",
            "MIN_DISK_GB": "2",
        },
        "STUDIO": {
            "RIGHTS_GATE_MODE": "strict",
            "RIGHTS_OPERATOR_BASIS": "owned",
            "REVIEW_BEFORE_UPLOAD": "true",
            "SCHEDULE": "false",
            "SCHEDULE_MIN": "20",
            "SCHEDULE_MAX": "60",
            "TAGS": "Facebook,Reel,Shorts",
        },
        "SAFETY": {"ALLOW_PUBLIC_AUTOMATION": "false"},
    })
    return cfg


def test_config_store_recovers_last_valid_backup_and_restricts_permissions(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    backup_path = tmp_path / "config.ini.bak1"
    config_path.write_text("[broken\n", encoding="utf-8")
    backup_path.write_text("[SETTINGS]\nVIDEO_DIRECTORY = saved-videos\n", encoding="utf-8")

    cfg, recovered_from = read_config(config_path)

    assert recovered_from == backup_path
    assert cfg.get("SETTINGS", "VIDEO_DIRECTORY") == "saved-videos"
    assert config_path.read_text(encoding="utf-8").startswith("[SETTINGS]")
    if os.name == "posix":
        assert config_path.stat().st_mode & 0o777 == 0o600
        assert backup_path.stat().st_mode & 0o777 == 0o600


def test_config_store_keeps_previous_version_in_backup(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text("[SETTINGS]\nACCOUNT = first\n", encoding="utf-8")
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({"SETTINGS": {"ACCOUNT": "second"}})

    write_config_atomically(cfg, config_path)

    assert "account = second" in config_path.read_text(encoding="utf-8")
    assert "ACCOUNT = first" in (tmp_path / "config.ini.bak1").read_text(encoding="utf-8")


def test_config_store_rejects_symlink_without_overwriting_target(tmp_path: Path) -> None:
    target = tmp_path / "target.ini"
    target.write_text("[SETTINGS]\nACCOUNT = keep\n", encoding="utf-8")
    link = tmp_path / "config.ini"
    link.symlink_to(target)
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({"SETTINGS": {"ACCOUNT": "replace"}})

    with pytest.raises(ConfigRecoveryError):
        write_config_atomically(cfg, link)

    assert "ACCOUNT = keep" in target.read_text(encoding="utf-8")


def test_critical_failed_queue_recovers_from_valid_backup(tmp_path: Path, monkeypatch) -> None:
    queue_path = tmp_path / "failed_queue.json"
    backup_path = tmp_path / "failed_queue.json.bak1"
    queue_path.write_text("not-json", encoding="utf-8")
    expected = [{"id": "reel-001", "url": "https://www.facebook.com/reel/123"}]
    backup_path.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(app, "FAILED_QUEUE_FILE", str(queue_path))

    assert app.load_failed_queue() == expected
    assert json.loads(queue_path.read_text(encoding="utf-8")) == expected


def test_critical_failed_queue_does_not_fall_back_to_empty(tmp_path: Path, monkeypatch) -> None:
    queue_path = tmp_path / "failed_queue.json"
    queue_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(app, "FAILED_QUEUE_FILE", str(queue_path))

    with pytest.raises(RuntimeError, match="failed_queue.json"):
        app.load_failed_queue()


def test_private_append_creates_mode_600_file(tmp_path: Path) -> None:
    target = tmp_path / "private.log"
    with open_private_append(target) as output:
        output.write("private\n")

    assert target.read_text(encoding="utf-8") == "private\n"
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600


def test_log_redaction_masks_credentials_in_assignments_urls_and_headers() -> None:
    text = (
        "access_token=secret-value "
        "Authorization: Bearer bearer-secret "
        "https://api.example.test/callback?code=oauth-secret&state=public-state "
        "https://user:password@example.test/path "
        "https://api.telegram.org/bot123456789:ABCdefghijklmnopqrstuvwxyz0123456789/sendMessage"
    )

    redacted = redact_sensitive_text(text)

    for secret in ("secret-value", "bearer-secret", "oauth-secret", "password", "ABCdefghijklmnopqrstuvwxyz0123456789"):
        assert secret not in redacted
    assert "public-state" in redacted


def test_settings_validation_rejects_bad_boolean_range_proxy_and_incomplete_rights() -> None:
    cfg = _valid_config()
    cfg.set("SETTINGS", "BETWEEN_VIDEOS", "-1")
    cfg.set("SETTINGS", "HEADLESS", "sometimes")
    cfg.add_section("WATCH")
    cfg.set("WATCH", "PROXY", "http://user:password@proxy.example:8080")
    cfg.set("STUDIO", "RIGHTS_GATE_MODE", "operator_confirmed")
    cfg.set("STUDIO", "REVIEW_BEFORE_UPLOAD", "false")
    cfg.set("STUDIO", "PRESERVE_FACEBOOK_TITLE", "sometimes")
    cfg.set("STUDIO", "RIGHTS_OPERATOR_STATEMENT", "")
    cfg.set("STUDIO", "PRESERVE_FACEBOOK_TITLE", "sometimes")
    cfg.add_section("AI")
    cfg.set("AI", "TRANSCRIBE_LOCAL", "sometimes")
    cfg.set("AI", "TRANSCRIPTION_MODEL", "large")

    errors = validate_configuration(cfg)

    assert any("BETWEEN_VIDEOS" in error for error in errors)
    assert any("HEADLESS" in error for error in errors)
    assert any("PRESERVE_FACEBOOK_TITLE" in error for error in errors)
    assert any("PROXY" in error for error in errors)
    assert any("PRESERVE_FACEBOOK_TITLE" in error for error in errors)
    assert any("TRANSCRIBE_LOCAL" in error for error in errors)
    assert any("TRANSCRIPTION_MODEL" in error for error in errors)
    assert any("operator" in error.lower() or "إقرار" in error for error in errors)


def test_save_ui_values_rejects_invalid_settings_without_replacing_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[SETTINGS]\nVIDEO_DIRECTORY = videos\nCLIENT_SECRETS_FILE = client_secrets.json\n"
        "PRIVACY = private\nDRY_RUN = false\nBETWEEN_VIDEOS = 10\n"
        "[STUDIO]\nRIGHTS_GATE_MODE = strict\nREVIEW_BEFORE_UPLOAD = true\n"
        "SCHEDULE = false\nSCHEDULE_MIN = 20\nSCHEDULE_MAX = 60\n",
        encoding="utf-8",
    )
    original = config_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="BETWEEN_VIDEOS"):
        app.save_ui_values({("SETTINGS", "BETWEEN_VIDEOS"): "-4"}, config_path)

    assert config_path.read_text(encoding="utf-8") == original


def test_pruning_keeps_expired_queue_items_when_history_cannot_be_saved(monkeypatch) -> None:
    items = [{
        "id": "reel-old",
        "url": "https://www.facebook.com/reel/123",
        "failed_at": 1,
        "last_error": "download_failed",
    }]
    monkeypatch.setattr(app, "append_history", lambda _record: False)

    assert app.prune_failed_queue(items, max_age_days=14) == items

def test_failed_staged_download_preserves_existing_video_and_removes_only_attempt_files(tmp_path: Path, monkeypatch) -> None:
    output_dir = tmp_path / "videos"
    output_dir.mkdir()
    existing = output_dir / "123.mp4"
    existing.write_bytes(b"user-owned video")

    class FailedYDL:
        def __init__(self, options):
            self.partial = Path(options["outtmpl"].replace("%(id)s.%(ext)s", "123.mp4.part"))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=True):
            self.partial.write_bytes(b"partial")
            raise RuntimeError("non-transient extraction failure")

    monkeypatch.setattr(app, "VIDEO_DIRECTORY", str(output_dir))
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FailedYDL)

    with pytest.raises(app.PipelineFailure):
        app.download_video("https://www.facebook.com/reel/123", retries=1)

    assert existing.read_bytes() == b"user-owned video"
    assert not list(output_dir.glob(".fb2yt-*"))


def test_selenium_does_not_hide_automation_or_bypass_detection(monkeypatch) -> None:
    created = {}

    class FakeOptions:
        def __init__(self):
            self.arguments = []
            self.experiments = []

        def add_argument(self, value):
            self.arguments.append(value)

        def add_experimental_option(self, name, value):
            self.experiments.append((name, value))

    class FakeDriver:
        pass

    driver = FakeDriver()
    monkeypatch.setattr(app, "Options", FakeOptions)
    monkeypatch.setattr(app, "config", configparser.ConfigParser(interpolation=None))

    def make_driver(options):
        created["options"] = options
        return driver

    monkeypatch.setattr(app.webdriver, "Chrome", make_driver)
    returned = app.init_driver(headless=False)

    assert returned is driver
    combined = " ".join(created["options"].arguments + [repr(created["options"].experiments)])
    assert "AutomationControlled" not in combined
    assert "enable-automation" not in combined
    assert "navigator" not in combined
    assert not any(value.startswith("user-agent=") for value in created["options"].arguments)


def test_invalid_facebook_page_never_reaches_browser_or_ytdlp(monkeypatch) -> None:
    class Driver:
        def __init__(self):
            self.opened = []

        def set_page_load_timeout(self, _timeout):
            pass

        def get(self, url):
            self.opened.append(url)

    driver = Driver()
    assert app.get_reels(driver, "https://example.com/page", 1, 5) is None
    assert driver.opened == []
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", lambda *_args, **_kwargs: pytest.fail("invalid URL reached yt-dlp"))
    assert app.scrape_links_fast("https://example.com/page", 5) is None

def test_upload_receipt_is_atomic_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "upload_receipts.json"
    receipt, created = app.persist_upload_receipt(
        "source-reel-1", "abcdefghijk", account="channel", title="Reviewed title", path=path
    )
    repeated, created_again = app.persist_upload_receipt(
        "source-reel-1", "abcdefghijk", account="channel", title="Reviewed title", path=path
    )

    assert created is True
    assert created_again is False
    assert repeated == receipt
    assert app.load_upload_receipts(path)["source-reel-1"]["youtube_id"] == "abcdefghijk"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError, match="إيصال مختلف"):
        app.persist_upload_receipt("source-reel-1", "lmnopqrstuv", path=path)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError):
        app.load_upload_receipts(invalid)


def test_secret_masking_hides_all_but_last_four_characters():
    masked = mask_secret("AIzaABCDEFGHIJKL")
    assert masked.endswith("IJKL")
    assert len(masked) == len("AIzaABCDEFGHIJKL")
    assert "AIza" not in masked
    assert mask_secret("short") == "••••••••hort"

def test_shorts_requires_explicit_manual_rights_and_records_confirmation(monkeypatch, tmp_path: Path) -> None:
    import queue
    import threading
    from types import SimpleNamespace

    logs = []
    history = []
    saved_rights = []
    saved_receipts = []
    saved_ids = []
    upload_calls = []
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    clip = tmp_path / "fb2yt-shorts.mp4"
    clip.write_bytes(b"short")
    rights_record = {
        "rights_basis": "owned",
        "attested": True,
        "original_contribution": "I own the recording and created this editorial cut myself.",
        "evidence_reference": "",
        "duplicate_review_note": "",
    }
    worker = SimpleNamespace(
        _stop=threading.Event(),
        _uiq=queue.Queue(),
        _log=logs.append,
        stats={"daily": {"date": "", "uploaded": 0}},
        _cleanup_file=lambda _data: None,
    )
    quota = SimpleNamespace(used=1, daily_limit=8, mark_exhausted=lambda: None)
    settings = {
        "dry_run": False,
        "review_before_upload": True,
        "allow_public_automation": False,
        "rights_manifest_file": str(tmp_path / "rights.json"),
        "privacy": "private",
        "schedule_enabled": False,
        "tags": ["Facebook"],
        "category": "22",
        "content_review_enabled": False,
        "thumbnail_enabled": True,
    }
    thumbnail_base = str(tmp_path / "fb-youtube-preview-base.png")
    thumbnail_preview = str(tmp_path / "fb-youtube-preview.png")
    thumbnail_calls = []
    monkeypatch.setattr(app, "create_thumbnail_preview", lambda *_args: (thumbnail_base, thumbnail_preview))
    monkeypatch.setattr(app, "upload_thumbnail", lambda _youtube, video_id, path, _log: thumbnail_calls.append((video_id, path)) or True)
    monkeypatch.setattr(app, "get_rights_record", lambda *_args: None)
    monkeypatch.setattr(app, "save_rights_record", lambda source_id, record, path: saved_rights.append((source_id, record, path)))
    monkeypatch.setattr(app, "_audio_rms_per_second", lambda _source: {0: -2})
    monkeypatch.setattr(app, "find_hot_segment", lambda *_args, **_kwargs: (0, 30))
    monkeypatch.setattr(app, "cut_shorts_clip", lambda *_args, **_kwargs: str(clip))
    monkeypatch.setattr(app, "validate_media", lambda *_args, **_kwargs: (True, {"duration": 30}))
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_args: {
        "action": "approve",
        "title": "Reviewed Shorts",
        "description": "Reviewed description",
        "privacy": "unlisted",
        "rights_record": rights_record,
        "thumbnail_path": thumbnail_preview,
    })
    monkeypatch.setattr(app, "append_history", lambda record: history.append(record) or True)
    monkeypatch.setattr(app, "persist_upload_receipt", lambda source_id, video_id, **_kwargs: saved_receipts.append((source_id, video_id)))
    monkeypatch.setattr(app, "save_uploaded_id", lambda source_id: saved_ids.append(source_id))
    monkeypatch.setattr(app, "send_telegram", lambda *_args, **_kwargs: None)

    def fake_upload(_youtube, _data, *_args, **kwargs):
        upload_calls.append(kwargs)
        kwargs["on_confirmed"]("abcdefghijk")
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", fake_upload)

    app.App._make_shorts_from(
        worker,
        {"yt": object(), "quota": quota, "account": "test-channel"},
        settings,
        {"id": "source-reel-1", "url": "https://www.facebook.com/reel/123", "page": "Page"},
        {"title": "Original", "desc": "Source description", "duration": 120, "file": str(source), "duplicate_matches": []},
        set(),
    )

    assert upload_calls[0]["publication_approved"] is True
    assert upload_calls[0]["on_confirmed"] is not None
    assert thumbnail_calls == [("abcdefghijk", thumbnail_preview)]
    assert saved_rights[0][0] == "source-reel-1"
    assert saved_receipts == [("source-reel-1_shorts", "abcdefghijk")]
    assert saved_ids == ["source-reel-1_shorts"]
    assert history[-1]["status"] == "uploaded"


def test_shorts_without_rights_never_calls_youtube(monkeypatch) -> None:
    import queue
    import threading
    from types import SimpleNamespace

    history = []
    logs = []
    worker = SimpleNamespace(
        _stop=threading.Event(),
        _uiq=queue.Queue(),
        _log=logs.append,
        stats={},
        _cleanup_file=lambda _data: None,
    )
    settings = {
        "dry_run": False,
        "review_before_upload": False,
        "allow_public_automation": True,
        "rights_gate_mode": "strict",
        "rights_operator_statement": "",
        "privacy": "public",
        "schedule_enabled": False,
        "rights_manifest_file": "rights.json",
    }
    monkeypatch.setattr(app, "get_rights_record", lambda *_args: None)
    monkeypatch.setattr(app, "append_history", lambda record: history.append(record) or True)
    monkeypatch.setattr(app, "upload_video", lambda *_args, **_kwargs: pytest.fail("Shorts upload bypassed rights gate"))

    app.App._make_shorts_from(
        worker,
        {"yt": object(), "quota": object(), "account": "test-channel"},
        settings,
        {"id": "source-reel-2", "url": "https://www.facebook.com/reel/456", "page": "Page"},
        {"title": "Original", "duration": 120, "file": "missing.mp4", "duplicate_matches": []},
        set(),
    )

    assert history[-1]["status"] == "rights_review_required"
    assert any("بوابة الحقوق" in line for line in logs)
