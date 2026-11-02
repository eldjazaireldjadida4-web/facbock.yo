from __future__ import annotations

import configparser
import importlib.util
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def _load_app():
    spec = importlib.util.spec_from_file_location(
        "fb_youtube_uploader_v49_pro", PROJECT / "fb_youtube_uploader_v49_pro.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ui_keys_include_rights_gate_mode():
    app = _load_app()
    keys = {key for _, key, _ in app.UI_KEYS}
    for required in (
        "RIGHTS_AUTO_DRAFT",
        "RIGHTS_GATE_MODE",
        "RIGHTS_OPERATOR_BASIS",
        "RIGHTS_OPERATOR_STATEMENT",
        "RIGHTS_OPERATOR_EVIDENCE",
    ):
        assert required in keys, f"{required} مفقود من UI_KEYS؛ الواجهة ستمحو الإعداد عند الحفظ."


def test_load_ui_values_reads_operator_confirmed_gate(tmp_path):
    app = _load_app()
    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text(
        "[STUDIO]\nRIGHTS_GATE_MODE = operator_confirmed\n"
        "RIGHTS_OPERATOR_BASIS = owned\n"
        "RIGHTS_OPERATOR_STATEMENT = إقرار صالح أطول من خمسة وعشرين حرفاً بلا شك.\n",
        encoding="utf-8",
    )
    values = app.load_ui_values(str(cfg_file))
    assert values[("STUDIO", "RIGHTS_GATE_MODE")] == "operator_confirmed"
    assert values[("STUDIO", "RIGHTS_OPERATOR_STATEMENT")].startswith("إقرار صالح")


def test_save_then_load_preserves_operator_gate(tmp_path):
    app = _load_app()
    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text("[STUDIO]\n", encoding="utf-8")
    values = app.load_ui_values(str(cfg_file))
    values[("STUDIO", "RIGHTS_GATE_MODE")] = "operator_confirmed"
    app.save_ui_values(values, str(cfg_file))
    reloaded = app.load_ui_values(str(cfg_file))
    assert reloaded[("STUDIO", "RIGHTS_GATE_MODE")] == "operator_confirmed"


def test_build_cli_settings_follows_saved_gate(tmp_path):
    app = _load_app()
    cfg = configparser.ConfigParser()
    cfg.read_dict({"STUDIO": {"RIGHTS_GATE_MODE": "operator_confirmed"}})
    settings = app.build_cli_settings(cfg)
    assert settings["rights_gate_mode"] == "operator_confirmed"


def test_stop_message_names_gate_mode(tmp_path):
    source = (PROJECT / "fb_youtube_uploader_v49_pro.py").read_text(encoding="utf-8")
    assert "بوابة الحقوق:" in source and "RIGHTS_GATE_MODE=operator_confirmed" in source
