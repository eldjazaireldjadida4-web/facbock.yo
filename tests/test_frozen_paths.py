"""مسارات النسخة المجمّدة (PyInstaller): APP_DIRECTORY من المنفّذ لا من _internal.

في حزمة one-dir يشير ``__file__`` إلى داخل مجلد ``_internal`` للمكتبات،
فلو بقيت مسارات الحالة على ``__file__`` لوُضعت config.ini والرموز
والإيصالات داخل ``_internal`` وتُجاهل مجلد التثبيت. هذه الاختبارات تثبّت
أن المسارات تُحلّ من المنفّذ في الوضع المجمّد، ومن الملف المصدري غيره.
"""
from __future__ import annotations

import ntpath
import os
import sys
from types import SimpleNamespace

import content_review_bridge as bridge
import control_plane
import doctor
import enable_automation as ea
import fb_youtube_uploader_v49_pro as app


def test_app_directory_uses_executable_when_frozen(tmp_path, monkeypatch) -> None:
    # مسار المنفّذ من tmp_path (صحيح على كل منصة) لا من "/tmp/bundle"
    # المكتوب بنمط POSIX — فشل على Windows لأن ntpath يحوّله إلى \tmp\bundle.
    exe = tmp_path / "bundle" / "facbock-yo"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert app._resolve_app_directory() == str(exe.parent)


def test_app_directory_uses_source_file_when_not_frozen() -> None:
    expected = os.path.dirname(os.path.abspath(app.__file__))
    assert app._resolve_app_directory() == expected


def test_doctor_directory_uses_executable_when_frozen(tmp_path, monkeypatch) -> None:
    exe = tmp_path / "bundle" / "facbock-yo-doctor"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert doctor._resolve_app_directory() == str(exe.parent)


def test_enable_automation_dir_uses_executable_when_frozen(tmp_path, monkeypatch) -> None:
    exe = tmp_path / "bundle" / "facbock-yo-automation"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert ea._resolve_app_dir() == exe.resolve().parent


def test_readiness_check_runs_doctor_executable_when_frozen(tmp_path, monkeypatch) -> None:
    # على Windows المنفّذ المجمّد اسمه facbock-yo-doctor.exe؛ إنشاؤه بلا
    # اللاحقة كان يجعل الفحص يفشل برمز 2 هناك (فشل CI في 4.49.26).
    exe_name = "facbock-yo-doctor.exe" if os.name == "nt" else "facbock-yo-doctor"
    doctor_exe = tmp_path / exe_name
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


# ── محاكاة Windows (فشل CI في 4.49.26 كان Windows فقط) ──────────────────
# هذه الاختبارات تثبّت سلوك Windows على أي منصة، حتى لا يعود الفشل يظهر
# في CI إلا على Windows نفسه.


def test_app_directory_resolves_windows_style_executable_path(monkeypatch) -> None:
    """محاكاة: مسار منفّذ Windows يُحلّ إلى مجلده حتى على Linux."""
    monkeypatch.setattr(os, "path", ntpath)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:\\bundle\\facbock-yo.exe")
    assert app._resolve_app_directory() == "C:\\bundle"


def test_doctor_directory_resolves_windows_style_executable_path(monkeypatch) -> None:
    monkeypatch.setattr(os, "path", ntpath)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:\\bundle\\facbock-yo-doctor.exe")
    assert doctor._resolve_app_directory() == "C:\\bundle"


class _WindowsPathEmulator:
    """محاكاة مصغّرة لـ WindowsPath: resolve() يضيف محرك الأقراص الحالي
    (``C:``) للمسار الجذري بلا محرك، كما يفعل Win32 GetFullPathName.
    لا يمكن إنشاء WindowsPath الحقيقي على POSIX (Python 3.12 يرفضه)."""

    def __init__(self, value):
        self._value = str(value)

    def resolve(self):
        path = ntpath.normpath(self._value)
        if path.startswith("\\") and not ntpath.splitdrive(path)[0]:
            path = "C:" + path
        return _WindowsPathEmulator(path)

    @property
    def parent(self):
        return _WindowsPathEmulator(ntpath.dirname(self._value))

    def __eq__(self, other):
        if isinstance(other, _WindowsPathEmulator):
            return ntpath.normcase(self._value) == ntpath.normcase(other._value)
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash(ntpath.normcase(self._value))

    def __str__(self):
        return self._value


def test_enable_automation_dir_resolves_windows_style_path(monkeypatch) -> None:
    """محاكاة: resolve() على Windows يضيف محرك الأقراص للمسار الجذري،
    فالتوقع القديم "/tmp/bundle" كان خاطئاً هناك."""
    monkeypatch.setattr(ea, "Path", _WindowsPathEmulator)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/bundle/facbock-yo-automation")
    assert ea._resolve_app_dir() == _WindowsPathEmulator("C:\\tmp\\bundle")


class _WindowsOS:
    """وكيل لوحدة ``os`` كما تراها وحدة واحدة: الاسم والمسارات Windows،
    والباقي يمرَّر إلى ``os`` الحقيقية. لا يمكن ترقيع ``os.name`` عالمياً:
    ``pathlib.Path`` يختار صنفه منه عند الإنشاء فينكسر على POSIX."""

    def __init__(self, real_os):
        self._real = real_os

    @property
    def name(self):
        return "nt"

    @property
    def path(self):
        return ntpath

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_readiness_check_uses_exe_suffix_on_windows_simulation(
    tmp_path, monkeypatch
) -> None:
    """محاكاة: في الوضع المجمّد على Windows يُبحث عن facbock-yo-doctor.exe."""
    doctor_exe = tmp_path / "facbock-yo-doctor.exe"
    doctor_exe.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(control_plane, "os", _WindowsOS(os))
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
