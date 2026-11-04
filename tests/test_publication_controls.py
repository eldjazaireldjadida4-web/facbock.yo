from __future__ import annotations

import fb_youtube_uploader_v49_pro as app


def test_public_upload_is_blocked_before_quota_or_api(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    class Quota:
        def __init__(self):
            self.checked = False

        def can_afford(self):
            self.checked = True
            return True

    class YouTube:
        def videos(self):
            raise AssertionError("YouTube API must not be reached")

    quota = Quota()
    result = app.upload_video(
        YouTube(),
        {"file": str(video), "title": "title", "desc": "description"},
        privacy="public",
        quota_manager=quota,
    )

    assert result == "PUBLICATION_APPROVAL_REQUIRED"
    assert quota.checked is False


def test_explicit_publication_approval_passes_final_guard(tmp_path):
    class YouTube:
        def videos(self):
            raise AssertionError("Missing-file guard must run before the API")

    result = app.upload_video(
        YouTube(),
        {"file": str(tmp_path / "missing.mp4"), "title": "title", "desc": "description"},
        privacy="public",
        publication_approved=True,
    )

    assert result == app.UPLOAD_NOT_STARTED


def test_explicit_automation_opt_in_passes_final_guard(tmp_path):
    class YouTube:
        def videos(self):
            raise AssertionError("Missing-file guard must run before the API")

    result = app.upload_video(
        YouTube(),
        {"file": str(tmp_path / "missing.mp4"), "title": "title", "desc": "description"},
        privacy="unlisted",
        allow_public_automation=True,
    )

    assert result == app.UPLOAD_NOT_STARTED


def test_cli_requires_explicit_opt_in_for_publication(monkeypatch):
    settings = {
        "content_review_config_error": False,
        "content_review_enabled": False,
        "dry_run": False,
        "privacy": "public",
        "schedule_enabled": False,
        "allow_public_automation": False,
    }
    monkeypatch.setattr(app, "build_cli_settings", lambda: settings)

    assert app.run_cli(once=True) == 2
