"""مسارات النسخة المجمّدة (PyInstaller): APP_DIRECTORY من المنفّذ لا من _internal.

في حزمة one-dir يشير ``__file__`` إلى داخل مجلد ``_internal`` للمكتبات،
فلو بقيت مسارات الحالة على ``__file__`` لوُضعت config.ini والرموز
والإيصالات داخل ``_internal`` وتُجاهل مجلد التثبيت. هذه الاختبارات تثبّت
أن المسارات تُحلّ من المنفّذ في الوضع المجمّد، ومن الملف المصدري غيره.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import content_review_bridge as bridge
import control_plane
import doctor
import enable_automation as ea
import fb_youtube_uploader_v49_pro as app


def test_app_directory_uses_executable_when_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/bundle/facbock-yo")
    assert app._resolve_app_directory() == "/tmp/bundle"


def test_app_directory_uses_source_file_when_not_frozen() -> None:
    expected = os.path.dirname(os.path.abspath(app.__file__))
    assert app._resolve_app_directory() == expected


def test_doctor_directory_uses_executable_when_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/bundle/facbock-yo-doctor")
    assert doctor._resolve_app_directory() == "/tmp/bundle"


def test_enable_automation_dir_uses_executable_when_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/bundle/facbock-yo-automation")
    assert ea._resolve_app_dir() == Path("/tmp/bundle")


def test_readiness_check_runs_doctor_executable_when_frozen(tmp_path, monkeypatch) -> None:
    doctor_exe = tmp_path / "facbock-yo-doctor"
    doctor_exe.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "facbock-yo"))
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ready\n", stderr="")

    monkeypatch.setattr(control_plane.subprocess, "run", fake_run)
    code, output = control_plane.run_readiness_check(tmp_path)

    assert code == 0
    assert output == "ready"
    assert calls[0][0] == [str(doctor_exe.resolve())]
    assert calls[0][1]["cwd"] == tmp_path.resolve()


def test_readiness_check_reports_missing_doctor_executable_when_frozen(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "facbock-yo"))
    code, output = control_plane.run_readiness_check(tmp_path)

    assert code == 2
    assert "facbock-yo-doctor" in output


def test_update_ytdlp_disabled_when_frozen(monkeypatch) -> None:
    logs: list[str] = []
    fake_self = SimpleNamespace(_ytdlp_update_running=False, _log=logs.append)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    calls: list = []
    monkeypatch.setattr(app.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    app.App._update_ytdlp(fake_self)

    assert calls == []
    assert logs and "المثبّتة" in logs[0]


def test_content_review_frozen_without_python_reports_not_found(tmp_path, monkeypatch) -> None:
    cutter = tmp_path / "cutter"
    (cutter / "scripts").mkdir(parents=True)
    (cutter / "scripts" / "upload_content_review.py").write_text("pass", encoding="utf-8")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 10)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    result, reason = bridge.run_review(
        str(video), "title", "caption", cutter_root=str(cutter)
    )

    assert result is None
    assert reason == "content_review_python_not_found"
