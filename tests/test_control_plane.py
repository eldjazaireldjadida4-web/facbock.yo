from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import control_plane
import pytest


def test_readiness_check_runs_local_doctor_with_current_interpreter(tmp_path, monkeypatch):
    doctor = tmp_path / "doctor.py"
    doctor.write_text("print('ready')", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ready\n", stderr="")

    monkeypatch.setattr(control_plane.subprocess, "run", fake_run)
    code, output = control_plane.run_readiness_check(tmp_path)

    assert code == 0
    assert output == "ready"
    assert calls[0][0] == [sys.executable, str(doctor.resolve())]
    assert calls[0][1]["cwd"] == tmp_path.resolve()
    assert calls[0][1]["timeout"] == 90


def test_readiness_check_reports_timeout_without_raising(tmp_path, monkeypatch):
    (tmp_path / "doctor.py").write_text("pass", encoding="utf-8")

    def timeout(command, timeout, **_kwargs):
        raise subprocess.TimeoutExpired(command, timeout, output=b"partial report")

    monkeypatch.setattr(control_plane.subprocess, "run", timeout)
    code, output = control_plane.run_readiness_check(tmp_path)

    assert code == 2
    assert "مهلة" in output
    assert "partial report" in output


def test_readiness_check_reports_missing_doctor(tmp_path):
    code, output = control_plane.run_readiness_check(tmp_path)

    assert code == 2
    assert "doctor.py" in output


def test_upload_journal_blocks_unresolved_retries():
    entry = {"id": "source-1", "url": "https://www.facebook.com/reel/1", "attempts": 2}
    started = control_plane.begin_upload_attempt([], entry, now=100)

    assert started[0]["last_error"] == control_plane.UPLOAD_IN_PROGRESS
    assert started[0]["failed_at"] == 100
    assert started[0]["attempts"] == 2

    unknown = control_plane.mark_upload_result_unknown(started, "source-1", "timeout", now=120)
    assert unknown[0]["last_error"] == control_plane.UPLOAD_RESULT_UNKNOWN
    assert unknown[0]["unknown_since"] == 100
    assert unknown[0]["upload_error"] == "timeout"

    with pytest.raises(RuntimeError, match="reconciled"):
        control_plane.begin_upload_attempt(unknown, entry, now=130)


def test_recover_interrupted_uploads_holds_unknown_and_drops_confirmed():
    recovered, count = control_plane.recover_interrupted_uploads(
        [
            {"id": "uploaded", "last_error": control_plane.UPLOAD_IN_PROGRESS},
            {"id": "interrupted", "last_error": control_plane.UPLOAD_IN_PROGRESS, "upload_started_at": 10},
        ],
        uploaded_ids={"uploaded"},
    )

    assert count == 1
    assert len(recovered) == 1
    assert recovered[0]["id"] == "interrupted"
    assert recovered[0]["last_error"] == control_plane.UPLOAD_RESULT_UNKNOWN
    assert recovered[0]["unknown_since"] == 10


def test_unknown_upload_is_held_until_explicit_reconciliation():
    item = {"id": "source-2", "last_error": control_plane.UPLOAD_RESULT_UNKNOWN}
    ready, held = control_plane.split_duplicate_review_queue([item], review_enabled=False)
    assert ready == []
    assert held == [item]

    removed, decision = control_plane.reconcile_upload_result([item], "source-2", video_exists=True)
    assert removed == []
    assert decision == "uploaded"

    retry, decision = control_plane.reconcile_upload_result([item], "source-2", video_exists=False)
    assert decision == "retry"
    assert retry[0]["last_error"] == control_plane.MANUAL_RETRY_APPROVED

def test_upload_attempt_is_persistable_and_duplicate_attempt_is_blocked():
    item = {"id": "source-1", "url": "https://facebook.com/reel/source-1", "attempts": 1}
    pending = control_plane.begin_upload_attempt([], item, now=100.0)
    assert pending[0]["last_error"] == control_plane.UPLOAD_IN_PROGRESS
    assert pending[0]["upload_started_at"] == 100.0
    import pytest
    with pytest.raises(RuntimeError):
        control_plane.begin_upload_attempt(pending, item, now=101.0)

def test_interrupted_upload_becomes_manual_reconciliation_hold():
    item = {"id": "source-2", "last_error": control_plane.UPLOAD_IN_PROGRESS, "upload_started_at": 100.0}
    recovered, count = control_plane.recover_interrupted_uploads([item], uploaded_ids=[])
    assert count == 1
    assert recovered[0]["last_error"] == control_plane.UPLOAD_RESULT_UNKNOWN
    ready, held = control_plane.split_duplicate_review_queue(recovered, review_enabled=False)
    assert ready == []
    assert held == recovered

def test_confirmed_upload_is_removed_and_failed_retry_requires_operator_resolution():
    item = {"id": "source-3", "last_error": control_plane.UPLOAD_RESULT_UNKNOWN}
    confirmed, decision = control_plane.reconcile_upload_result([item], "source-3", video_exists=True)
    assert decision == "uploaded"
    assert confirmed == []
    retry, decision = control_plane.reconcile_upload_result([item], "source-3", video_exists=False)
    assert decision == "retry"
    assert retry[0]["last_error"] == control_plane.MANUAL_RETRY_APPROVED

def test_youtube_video_match_is_verified_against_the_authorized_channel():
    class Request:
        def __init__(self, payload):
            self.payload = payload
        def execute(self):
            return self.payload

    class YouTube:
        def channels(self):
            return SimpleNamespace(list=lambda **_kwargs: Request({"items": [{"id": "channel-1"}]}))
        def videos(self):
            return SimpleNamespace(list=lambda **_kwargs: Request({"items": [{"id": "abcdefghijk", "snippet": {"channelId": "channel-1"}}]}))

    assert control_plane.youtube_video_belongs_to_current_channel(YouTube(), "abcdefghijk")
    assert not control_plane.youtube_video_belongs_to_current_channel(YouTube(), "invalid")



def test_queue_deduplication_keeps_uncertain_state_and_highest_attempt_count():
    items = [
        {"id": "source-1", "attempts": 1, "last_error": "network timeout"},
        {"id": "source-1", "attempts": 2, "last_error": control_plane.UPLOAD_RESULT_UNKNOWN},
    ]

    deduplicated = control_plane.deduplicate_queue_items(items)

    assert len(deduplicated) == 1
    assert deduplicated[0]["attempts"] == 2
    assert deduplicated[0]["last_error"] == control_plane.UPLOAD_RESULT_UNKNOWN
