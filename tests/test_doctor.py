"""اختبارات doctor: استيراد آمن بلا آثار جانبية، ومنطق فحص قابل للاختبار."""
from __future__ import annotations

import importlib
import json
import os

import pytest

import doctor


@pytest.fixture
def hermetic(monkeypatch, tmp_path):
    """عزل بيئة الفحص: مجلد مؤقت وبلا نداء شبكة حقيقي."""
    monkeypatch.setattr(doctor, "APP_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(doctor, "facebook_network_route", lambda *a, **k: "ipv4")
    return tmp_path


def test_import_does_not_chdir_or_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("doctor")
    assert os.getcwd() == str(tmp_path)
    assert module.OK == "[OK]"


def test_collect_checks_reports_missing_secrets(hermetic):
    config, config_error = doctor._load_config(str(hermetic))
    results = []
    doctor._collect_checks(
        lambda name, status, detail="", fix="": results.append((status, name, detail, fix)),
        config,
        config_error,
        str(hermetic),
    )
    assert all(row[0] in (doctor.OK, doctor.WARN, doctor.BAD, doctor.INFO) for row in results)
    secrets = next(row for row in results if row[1] == "ملف تفويض Google")
    assert secrets[0] == doctor.BAD
    config_row = next(row for row in results if row[1] == "ملف config.ini")
    assert config_row[0] == doctor.INFO


def test_collect_checks_accepts_desktop_oauth_file(hermetic):
    (hermetic / "client_secrets.json").write_text(
        json.dumps({"installed": {"client_id": "id", "client_secret": "secret"}}), encoding="utf-8"
    )
    config, config_error = doctor._load_config(str(hermetic))
    results = []
    doctor._collect_checks(
        lambda name, status, detail="", fix="": results.append((status, name, detail, fix)),
        config,
        config_error,
        str(hermetic),
    )
    secrets = next(row for row in results if row[1] == "ملف تفويض Google")
    assert secrets[0] == doctor.OK
    assert "Desktop" in secrets[2]


def test_main_returns_1_when_environment_incomplete(hermetic, capsys):
    exit_code = doctor.main()
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "فحص جاهزية FB → YouTube Uploader" in output
    assert "النتيجة: [فشل]" in output


def test_main_with_complete_rights_config_reports_strict_gate(hermetic, capsys):
    (hermetic / "config.ini").write_text(
        "[STUDIO]\nRIGHTS_GATE_MODE = strict\nREVIEW_BEFORE_UPLOAD = true\n",
        encoding="utf-8",
    )
    doctor.main()
    output = capsys.readouterr().out
    assert "strict — المراجعة اليدوية لكل فيديو أو وضع المحاكاة مفعّل" in output
