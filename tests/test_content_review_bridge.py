from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


from content_review_bridge import (
    build_review_caption,
    metadata_fingerprint,
    run_review,
    validate_report,
)
import control_plane
import fb_youtube_uploader_v49_pro as app


def clear_report(video, title="title", description="description", tags=None):
    caption = build_review_caption(description, tags)
    return {
        "schema_version": 1,
        "review_complete": True,
        "status": "clear",
        "backend": "gemini",
        "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "metadata_sha256": metadata_fingerprint(title, caption),
        "transcript_entries": 1,
        "review_windows": 1,
        "blocked_windows": [],
        "manual_review_windows": [],
        "ai_violations": [],
        "ai_review": [{"index": 0, "violation": False, "reason": ""}],
        "local_review": {
            "mode": "block",
            "segments": [{"index": 0, "status": "safe"}],
        },
    }


def test_report_is_bound_to_exact_video_and_metadata(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    caption = build_review_caption("description", ["Facebook", "Reel"])
    report = clear_report(video, tags=["Facebook", "Reel"])

    assert validate_report(report, str(video), "title", caption) == (True, "clear")
    assert validate_report(report, str(video), "edited title", caption)[0] is False
    assert validate_report(report, str(video), "title", "description")[0] is False
    video.write_bytes(b"changed video")
    assert validate_report(report, str(video), "title", caption) == (
        False, "video_changed_after_review"
    )


def test_clear_report_requires_complete_bounded_evidence(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    caption = build_review_caption("description")
    report = clear_report(video)
    report["ai_review"] = []

    assert validate_report(report, str(video), "title", caption) == (
        False, "review_evidence_incomplete"
    )
    report = clear_report(video)
    report["review_windows"] = 10**12
    assert validate_report(report, str(video), "title", caption) == (
        False, "review_evidence_incomplete"
    )


def test_upload_stops_before_quota_or_youtube_without_clear_report(tmp_path):
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
        YouTube(), {"file": str(video), "title": "title", "desc": "description"},
        quota_manager=quota,
        content_review_required=True,
    )

    assert result == "CONTENT_SAFETY_BLOCKED"
    assert not quota.checked


def test_upload_rejects_stale_report_before_youtube(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    class YouTube:
        def videos(self):
            raise AssertionError("YouTube API must not be reached")

    result = app.upload_video(
        YouTube(), {
            "file": str(video), "title": "edited title", "desc": "description",
            "content_safety_review": clear_report(video),
        },
        content_review_required=True,
    )
    assert result == "CONTENT_SAFETY_BLOCKED"


def test_upload_rejects_unreviewed_tags(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    class YouTube:
        def videos(self):
            raise AssertionError("YouTube API must not be reached")

    result = app.upload_video(
        YouTube(), {
            "file": str(video), "title": "title", "desc": "description",
            "content_safety_review": clear_report(video),
        },
        tags=["different-tag"],
        content_review_required=True,
    )
    assert result == "CONTENT_SAFETY_BLOCKED"


def test_review_and_upload_use_same_budgeted_tags(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    tags = ["A" * 490, "Foo Bar", "Over budget"]
    captured = {}

    def allow_review(_report, _file, title, caption):
        captured["title"] = title
        captured["caption"] = caption
        return True, "clear"

    monkeypatch.setattr(app, "validate_content_review", allow_review)
    monkeypatch.setattr(app, "MediaFileUpload", lambda *_args, **_kwargs: object())

    class Request:
        def next_chunk(self):
            return None, {"id": "youtube-id"}

    class YouTube:
        def videos(self):
            return self

        def insert(self, **kwargs):
            captured["body"] = kwargs["body"]
            return Request()

    result = app.upload_video(
        YouTube(), {
            "file": str(video), "title": "title", "desc": "description",
            "content_safety_review": {},
        },
        tags=tags,
        content_review_required=True,
    )

    effective_tags = ["A" * 490, "Foo Bar"]
    assert result == "youtube-id"
    assert captured["caption"] == build_review_caption("description", effective_tags)
    assert captured["body"]["snippet"]["tags"] == effective_tags


def test_run_review_preserves_previous_report_after_process_failure(tmp_path, monkeypatch):
    root = tmp_path / "cutter"
    script = root / "scripts" / "upload_content_review.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    report_path = video.with_name(video.name + ".safety-review.json")
    previous = {"previous": "report"}
    report_path.write_text(json.dumps(previous), encoding="utf-8")

    def failed_process(command, **_kwargs):
        generated_path = Path(command[command.index("--report") + 1])
        assert generated_path != report_path
        assert not generated_path.exists()
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr("content_review_bridge.subprocess.run", failed_process)

    report, status = run_review(
        str(video), "title", build_review_caption("description"),
        cutter_root=str(root), python_executable=sys.executable,
    )

    assert report is None
    assert status == "content_review_process_failed"
    assert json.loads(report_path.read_text(encoding="utf-8")) == previous
    assert not list(tmp_path.glob(".content-review-*"))


def test_run_review_accepts_and_persists_a_fresh_report(tmp_path, monkeypatch):
    root = tmp_path / "cutter"
    script = root / "scripts" / "upload_content_review.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    caption = build_review_caption("description", ["Facebook", "Reel"])

    def successful_process(command, **_kwargs):
        report_path = Path(command[command.index("--report") + 1])
        report_path.write_text(
            json.dumps(clear_report(video, tags=["Facebook", "Reel"])), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("content_review_bridge.subprocess.run", successful_process)

    report, status = run_review(
        str(video), "title", caption, cutter_root=str(root),
        python_executable=sys.executable,
    )

    assert status == "clear"
    assert report["status"] == "clear"
    stored = json.loads(
        video.with_name(video.name + ".safety-review.json").read_text(encoding="utf-8")
    )
    assert stored == report
    assert not list(tmp_path.glob(".content-review-*"))


def test_run_review_timeout_does_not_replace_previous_report(tmp_path, monkeypatch):
    root = tmp_path / "cutter"
    script = root / "scripts" / "upload_content_review.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    report_path = video.with_name(video.name + ".safety-review.json")
    report_path.write_text('{"status":"older"}', encoding="utf-8")

    def timeout_process(*_args, **_kwargs):
        raise app.subprocess.TimeoutExpired("review", 1)

    monkeypatch.setattr("content_review_bridge.subprocess.run", timeout_process)
    report, status = run_review(
        str(video), "title", build_review_caption("description"),
        cutter_root=str(root), python_executable=sys.executable,
    )

    assert report is None
    assert status == "content_review_timed_out"
    assert json.loads(report_path.read_text(encoding="utf-8")) == {"status": "older"}


def test_review_config_is_validated_before_start(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    report, status = run_review(
        str(video), "title", build_review_caption("description"),
        cutter_root=str(tmp_path / "missing"), python_executable=sys.executable,
    )
    assert report is None
    assert status == "cutter_review_script_not_found"


def test_build_upload_review_warnings_show_coverage():
    warnings = app.build_upload_review_warnings({
        "content_review_enabled": True,
        "content_safety_review": {"status": "clear", "review_windows": 4},
    })
    assert any("اجتازت 4 نافذة" in line for line in warnings)


def test_safety_gate_holds_failures_until_explicit_release():
    items = [
        {"id": "blocked", "last_error": "content_safety_review_required"},
        {"id": "ready", "last_error": "network_error"},
    ]
    for review_enabled in (False, True):
        ready, held = app.split_duplicate_review_queue(items, review_enabled=review_enabled)
        assert [item["id"] for item in ready] == ["ready"]
        assert [item["id"] for item in held] == ["blocked"]

    released, count = app.release_policy_holds(held)
    assert count == 1
    assert released[0]["last_error"] == ""


def test_publication_hold_stays_blocked_until_manual_release():
    item = {"id": "public", "last_error": "publication_approval_required"}

    for review_enabled in (False, True):
        ready, held = app.split_duplicate_review_queue([item], review_enabled=review_enabled)
        assert ready == []
        assert held == [item]

    released, count = app.release_policy_holds([item])
    assert count == 1
    assert released[0]["last_error"] == ""


def test_rights_hold_stays_blocked_until_explicit_release():
    item = {"id": "rights", "last_error": "rights_review_required"}

    ready, held = app.split_duplicate_review_queue([item], review_enabled=False)
    assert ready == []
    assert held == [item]

    released, count = app.release_policy_holds(held)
    assert count == 1
    assert released[0]["last_error"] == ""


def test_dry_run_does_not_contact_external_review_tool(monkeypatch, tmp_path):
    def unexpected_review(*_args, **_kwargs):
        raise AssertionError("dry-run must not call the external reviewer")

    monkeypatch.setattr(control_plane, "run_review", unexpected_review)
    report, status = app.run_policy_review(
        {"file": str(tmp_path / "clip.mp4"), "title": "title", "desc": "description"},
        ["Facebook"],
        {"content_review_enabled": True, "dry_run": True},
    )
    assert report is None
    assert status == "dry_run_skipped"

def test_policy_review_uses_configured_reviewer_and_logs(monkeypatch, tmp_path):
    report = {"status": "clear", "review_windows": 3}
    calls = []

    def review(*args, **kwargs):
        calls.append((args, kwargs))
        return report, "clear"

    monkeypatch.setattr(control_plane, "run_review", review)
    video = tmp_path / "clip.mp4"
    settings = {
        "content_review_enabled": True,
        "dry_run": False,
        "cutter_root": str(tmp_path / "cutter"),
        "cutter_python": sys.executable,
    }
    logs = []

    result, status = control_plane.run_policy_review(
        {"file": str(video), "title": "title", "desc": "description"},
        ["Facebook", "Shorts"],
        settings,
        logs.append,
    )

    assert result is report
    assert status == "clear"
    assert calls[0][0] == (
        str(video), "title", build_review_caption("description", ["Facebook", "Shorts"])
    )
    assert calls[0][1] == {
        "cutter_root": settings["cutter_root"],
        "python_executable": sys.executable,
    }
    assert any("3 نافذة" in line for line in logs)
