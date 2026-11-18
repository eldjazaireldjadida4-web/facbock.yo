"""التحديث التلقائي — فحص GitHub Releases وتنزيل المثبّت.

كل النداءات الشبكية مزيفة عبر opener قابل للحقن؛ لا شبكة حقيقية هنا.
الفحص لا يجوز أن يرفع أبداً، والتنزيل يرفض أي ملف حجمه أو بصمته
لا تطابق المعلن في الإصدار.
"""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

import pytest

import fb_youtube_uploader_v49_pro as app
import updater
from updater import UpdateError


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self, size=-1):
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _release_payload(tag="v4.49.30", asset_size=1030, asset_name=None):
    return json.dumps(
        {
            "tag_name": tag,
            "html_url": f"https://github.com/eldjazaireldjadida4-web/facbock.yo/releases/tag/{tag}",
            "body": "إصلاحات وتحسينات.",
            "assets": [
                {
                    "name": asset_name or f"facbock-yo-{tag.lstrip('vV')}-windows-x86_64-setup.exe",
                    "browser_download_url": "https://example.com/facbock-yo-4.49.30-windows-x86_64-setup.exe",
                    "size": asset_size,
                }
            ],
        }
    ).encode("utf-8")


def _opener(payload=None, error=None):
    def fake(request, timeout=None):
        if error is not None:
            raise error
        return _FakeResponse(payload)

    return fake


@pytest.mark.parametrize(
    "text, expected",
    [
        ("v4.49.30", (4, 49, 30)),
        ("4.49.29", (4, 49, 29)),
        ("v1.2", (1, 2)),
        ("  v4.49.30  ", (4, 49, 30)),
        ("abc", None),
        ("v4.x", None),
        ("", None),
        (None, None),
        ("4..30", None),
    ],
)
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


@pytest.mark.parametrize(
    "latest, current, expected",
    [
        ((4, 49, 30), (4, 49, 29), True),
        ((4, 49, 29), (4, 49, 29), False),
        ((4, 49, 28), (4, 49, 29), False),
        ((4, 50), (4, 49, 29), True),
        ((5,), (4, 49, 29), True),
        ((4, 49, 29, 1), (4, 49, 29), True),
    ],
)
def test_is_newer(latest, current, expected):
    assert updater.is_newer(latest, current) is expected


def test_check_for_update_returns_info_when_newer():
    info = updater.check_for_update("4.49.29", opener=_opener(_release_payload()))
    assert info is not None
    assert info.version == "4.49.30"
    assert info.tag == "v4.49.30"
    assert info.download_url.endswith("-windows-x86_64-setup.exe")
    assert info.size == 1030
    assert "إصلاحات" in info.body


def test_check_for_update_returns_none_when_up_to_date_or_older():
    assert updater.check_for_update("4.49.30", opener=_opener(_release_payload())) is None
    assert updater.check_for_update("4.49.31", opener=_opener(_release_payload())) is None


def test_check_for_update_never_raises_and_reports_error():
    seen = []
    result = updater.check_for_update(
        "4.49.29",
        opener=_opener(error=urllib.error.URLError("no network")),
        on_error=lambda e: seen.append(type(e).__name__),
    )
    assert result is None
    assert seen == ["URLError"]


def test_check_for_update_returns_none_on_malformed_payload():
    assert updater.check_for_update("4.49.29", opener=_opener(b"not json")) is None
    assert updater.check_for_update("4.49.29", opener=_opener(b"[1, 2]")) is None
    assert updater.check_for_update("4.49.29", opener=_opener(b"{}")) is None


def test_check_for_update_returns_none_without_installer_asset():
    payload = json.dumps({"tag_name": "v4.49.30", "assets": []}).encode("utf-8")
    assert updater.check_for_update("4.49.29", opener=_opener(payload)) is None


def test_download_installer_verifies_size_and_pe_magic(tmp_path):
    payload = b"MZ" + b"x" * 1028
    info = updater.UpdateInfo(
        version="4.49.30",
        tag="v4.49.30",
        html_url="https://example.com/r",
        download_url="https://example.com/facbock-yo-4.49.30-windows-x86_64-setup.exe",
        size=len(payload),
    )
    progress = []
    path = updater.download_installer(
        info, dest_dir=tmp_path, opener=_opener(payload),
        progress_cb=lambda done, total: progress.append((done, total)),
    )
    assert path.read_bytes() == payload
    assert progress and progress[-1] == (len(payload), len(payload))


def test_download_installer_rejects_size_mismatch_and_removes_partial(tmp_path):
    payload = b"MZ" + b"x" * 100
    info = updater.UpdateInfo(
        version="4.49.30",
        tag="v4.49.30",
        html_url="https://example.com/r",
        download_url="https://example.com/facbock-yo-4.49.30-windows-x86_64-setup.exe",
        size=9999,
    )
    with pytest.raises(UpdateError):
        updater.download_installer(info, dest_dir=tmp_path, opener=_opener(payload))
    assert not list(tmp_path.iterdir())


def test_download_installer_rejects_non_pe_file(tmp_path):
    payload = b"PK" + b"x" * 100
    info = updater.UpdateInfo(
        version="4.49.30",
        tag="v4.49.30",
        html_url="https://example.com/r",
        download_url="https://example.com/facbock-yo-4.49.30-windows-x86_64-setup.exe",
        size=len(payload),
    )
    with pytest.raises(UpdateError):
        updater.download_installer(info, dest_dir=tmp_path, opener=_opener(payload))
    assert not list(tmp_path.iterdir())


def test_download_installer_rejects_bad_url(tmp_path):
    info = updater.UpdateInfo(
        version="4.49.30",
        tag="v4.49.30",
        html_url="https://example.com/r",
        download_url="http://example.com/evil.exe",
        size=10,
    )
    with pytest.raises(UpdateError):
        updater.download_installer(info, dest_dir=tmp_path, opener=_opener(b""))


def test_should_check_now_throttles_to_once_a_day():
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    assert updater.should_check_now(None, now=now) is True
    assert updater.should_check_now("not-a-date", now=now) is True
    recent = (now - timedelta(hours=1)).isoformat()
    assert updater.should_check_now(recent, now=now) is False
    old = (now - timedelta(hours=25)).isoformat()
    assert updater.should_check_now(old, now=now) is True
    future = (now + timedelta(hours=1)).isoformat()
    assert updater.should_check_now(future, now=now) is True


def test_is_frozen_reads_sys_flag(monkeypatch):
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    assert updater.is_frozen() is True
    monkeypatch.delattr(updater.sys, "frozen", raising=False)
    assert updater.is_frozen() is False


def test_launch_installer_runs_the_downloaded_file(monkeypatch):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args: calls.append(args))
    path = Path("/tmp/facbock-yo-4.49.30-windows-x86_64-setup.exe")
    updater.launch_installer(path)
    # المسار بصيغة النظام الحالي: '/' على POSIX و'\' على Windows.
    assert calls == [[str(path)]]


def test_launch_installer_passes_windows_path_verbatim(monkeypatch):
    """محاكاة Windows: مسار PureWindowsPath يمر إلى Popen بشرطاته الخلفية."""
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args: calls.append(args))
    updater.launch_installer(PureWindowsPath(r"C:\Users\test\Downloads\facbock-yo-4.49.30-windows-x86_64-setup.exe"))
    assert calls == [[r"C:\Users\test\Downloads\facbock-yo-4.49.30-windows-x86_64-setup.exe"]]


def test_gui_wiring_update_check_present():
    """زر التحديثات والفحص التلقائي وزر التبديل في الإعدادات موجودة."""
    assert hasattr(app.App, "_check_for_updates_manual")
    assert hasattr(app.App, "_maybe_check_for_updates")
    assert hasattr(app.App, "_show_update_dialog")
    assert ("SETTINGS", "CHECK_FOR_UPDATES", "true") in app.UI_KEYS
