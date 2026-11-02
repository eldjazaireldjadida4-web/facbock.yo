from __future__ import annotations

import json

import pytest

import control_plane
from control_plane import (
    AutomationAlreadyRunning,
    AutomationRunLock,
    validate_unattended_oauth_token,
)


def test_automation_lock_blocks_a_second_process(tmp_path):
    lock_path = tmp_path / "automation.lock"
    first = AutomationRunLock(lock_path).acquire()
    try:
        with pytest.raises(AutomationAlreadyRunning):
            AutomationRunLock(lock_path).acquire()
        details = lock_path.read_text(encoding="utf-8")
        assert "pid=" in details
        assert "started_at=" in details
    finally:
        first.release()

    with AutomationRunLock(lock_path):
        pass


def test_automation_lock_releases_after_exception(tmp_path):
    lock_path = tmp_path / "automation.lock"
    with pytest.raises(RuntimeError):
        with AutomationRunLock(lock_path):
            raise RuntimeError("stop")

    with AutomationRunLock(lock_path):
        pass


def test_oauth_preflight_requires_refresh_token_and_scopes(tmp_path):
    token_path = tmp_path / "account_token.json"
    scopes = ["youtube.upload", "youtube.readonly"]

    assert validate_unattended_oauth_token(token_path, scopes)
    token_path.write_text(json.dumps({"refresh_token": "ready", "scopes": [scopes[0]]}), encoding="utf-8")
    assert validate_unattended_oauth_token(token_path, scopes)
    token_path.write_text(json.dumps({"scopes": scopes}), encoding="utf-8")
    assert validate_unattended_oauth_token(token_path, scopes)
    token_path.write_text(json.dumps({"refresh_token": "ready", "scopes": scopes}), encoding="utf-8")
    assert validate_unattended_oauth_token(token_path, scopes) == []


def test_automation_preflight_combines_doctor_and_oauth_results(tmp_path, monkeypatch):
    token_path = tmp_path / "account_token.json"
    monkeypatch.setattr(control_plane, "run_readiness_check", lambda *_args, **_kwargs: (0, "doctor ok"))

    code, report = control_plane.run_automation_preflight(tmp_path, token_path, ["youtube.upload"])

    assert code == 2
    assert "doctor ok" in report
    assert "جاهزية OAuth" in report
    assert "ملف تفويض YouTube" in report

def test_automation_exit_codes_report_success_and_failures():
    assert control_plane.automation_exit_code({"failed": 0, "fatal": None}) == 0
    assert control_plane.automation_exit_code({"failed": 2, "fatal": None}) == 1
    assert control_plane.automation_exit_code({"failed": 0, "pending": 59, "fatal": None}) == 1
    assert control_plane.automation_exit_code({"failed": 0, "pending": 0, "network_errors": 1}) == 1
    assert control_plane.automation_exit_code({"failed": 0, "fatal": "OAuth unavailable"}) == 2
    assert control_plane.automation_exit_code({"failed": "invalid"}) == 2
    assert control_plane.automation_exit_code(None) == 1
    assert control_plane.automation_exit_code({"failed": 0}, interrupted=True) == 130
