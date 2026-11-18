# -*- coding: utf-8 -*-
"""اختبارات enable_automation — تطبيق ملف الوضع الآلي على config.ini."""

from __future__ import annotations

import configparser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import enable_automation as ea


STATEMENT = "أنا مالك حقوق هذا المحتوى الأصلي وأتحمّل المسؤولية الكاملة عن نشره."


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.ini"
    config_path.write_text(body, encoding="utf-8")
    return config_path


def test_apply_sets_full_automation_profile(tmp_path):
    config_path = _write_config(tmp_path, "[STUDIO]\nRIGHTS_GATE_MODE = strict\n")
    changes = ea.apply_automation_profile(config_path, "owned", STATEMENT, "")
    studio = changes["STUDIO"]
    assert studio["RIGHTS_GATE_MODE"] == ("strict", "operator_confirmed")
    assert studio["REVIEW_BEFORE_UPLOAD"] == ("", "false")
    assert studio["PRESERVE_FACEBOOK_TITLE"] == ("", "false")
    assert studio["TITLE_OPTIMIZE"] == ("", "true")
    assert changes["AI"]["ENABLED"] == ("", "true")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    assert parser.get("STUDIO", "RIGHTS_GATE_MODE") == "operator_confirmed"
    assert parser.get("STUDIO", "RIGHTS_OPERATOR_STATEMENT") == STATEMENT
    assert parser.get("AI", "ENABLED") == "true"


def test_apply_is_idempotent(tmp_path):
    config_path = _write_config(tmp_path, "[STUDIO]\n")
    ea.apply_automation_profile(config_path, "owned", STATEMENT, "")
    changes = ea.apply_automation_profile(config_path, "owned", STATEMENT, "")
    assert changes == {}


def test_dry_run_writes_nothing(tmp_path):
    config_path = _write_config(tmp_path, "[STUDIO]\nRIGHTS_GATE_MODE = strict\n")
    changes = ea.apply_automation_profile(config_path, "owned", STATEMENT, "", dry_run=True)
    assert changes
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    assert parser.get("STUDIO", "RIGHTS_GATE_MODE") == "strict"


def test_short_statement_is_rejected(tmp_path):
    config_path = _write_config(tmp_path, "[STUDIO]\n")
    try:
        ea.apply_automation_profile(config_path, "owned", "إقرار قصير", "")
    except SystemExit as error:
        assert "25" in str(error)
    else:
        raise AssertionError("لا بد من رفض الإقرار القصير")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    assert not parser.has_option("STUDIO", "RIGHTS_GATE_MODE")


def test_licensed_requires_evidence(tmp_path):
    config_path = _write_config(tmp_path, "[STUDIO]\n")
    try:
        ea.apply_automation_profile(config_path, "licensed", STATEMENT, "")
    except SystemExit as error:
        assert "--evidence" in str(error) or "إثبات" in str(error)
    else:
        raise AssertionError("لا بد من رفض licensed بلا إثبات")


def test_unknown_basis_is_rejected(tmp_path):
    config_path = _write_config(tmp_path, "[STUDIO]\n")
    try:
        ea.apply_automation_profile(config_path, "stolen", STATEMENT, "")
    except SystemExit as error:
        assert "owned" in str(error)
    else:
        raise AssertionError("لا بد من رفض أساس غير معروف")


def test_show_lists_current_values(tmp_path, capsys):
    config_path = _write_config(tmp_path, "[STUDIO]\nRIGHTS_GATE_MODE = strict\n")
    output = ea.show_status(config_path)
    assert output.startswith("الإعدادات الحالية")
    assert "RIGHTS_GATE_MODE = strict" in output
