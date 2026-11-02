from __future__ import annotations

import configparser
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app
from content_safety import (
    DEFAULT_OPERATOR_RIGHTS_STATEMENT,
    build_auto_rights_record,
    build_operator_rights_record,
    migrate_legacy_rights_gate_config,
    rights_record_errors,
    validate_operator_rights_settings,
)


PROJECT = Path(__file__).resolve().parents[1]

STATEMENT = "أقر بأنني أملك حق نشر مقاطع هذه الصفحات على قناتي وأتحمل المسؤولية الكاملة عن ذلك."


def test_auto_draft_fails_validation_and_halts_in_strict_mode():
    draft = build_auto_rights_record("https://example.com/reel/1", ["إضافة شعار القناة على الفيديو"])
    assert draft is not None
    assert draft["attested"] is False
    assert rights_record_errors(draft, duplicate_detected=False)


def test_operator_record_passes_validation_and_is_attested():
    record = build_operator_rights_record(
        "https://example.com/reel/2",
        ["إضافة شعار القناة على الفيديو"],
        basis="owned",
        statement=STATEMENT,
        duplicate_note=STATEMENT,
    )
    assert record["attested"] is True
    assert record["rights_basis"] == "owned"
    assert record["attested_at"]
    assert rights_record_errors(record, duplicate_detected=True) == []


def test_operator_record_requires_clear_statement():
    with pytest.raises(ValueError):
        build_operator_rights_record("https://example.com/reel/3", [], basis="owned", statement="قصير")


def test_operator_record_rejects_unknown_basis():
    with pytest.raises(ValueError):
        build_operator_rights_record("https://example.com/reel/4", [], basis="invented", statement=STATEMENT)


def test_evidence_required_bases_demand_reference():
    with pytest.raises(ValueError):
        build_operator_rights_record(
            "https://example.com/reel/5", [], basis="licensed", statement=STATEMENT, evidence_reference=""
        )
    record = build_operator_rights_record(
        "https://example.com/reel/6", [], basis="licensed", statement=STATEMENT, evidence_reference="license-2026-01"
    )
    assert rights_record_errors(record, duplicate_detected=False) == []


def test_operator_rights_settings_preflight_requires_truthful_complete_attestation():
    valid = {
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "permission",
        "rights_operator_statement": STATEMENT,
        "rights_operator_evidence": "permission-2026-01",
    }
    assert validate_operator_rights_settings(valid) == []

    incomplete = dict(valid, rights_operator_evidence="")
    errors = validate_operator_rights_settings(incomplete)
    assert errors and "RIGHTS_OPERATOR_EVIDENCE" in errors[0]


def test_owned_operator_attestation_does_not_require_external_evidence():
    settings = {
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "owned",
        "rights_operator_statement": STATEMENT,
        "rights_operator_evidence": "",
    }
    assert validate_operator_rights_settings(settings) == []


def test_automatic_preflight_only_blocks_real_unreviewed_uploads():
    invalid = {
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "licensed",
        "rights_operator_statement": STATEMENT,
        "rights_operator_evidence": "",
        "review_before_upload": False,
        "dry_run": False,
    }
    assert app.automatic_rights_preflight_errors(invalid)
    assert app.automatic_rights_preflight_errors(dict(invalid, review_before_upload=True)) == []
    assert app.automatic_rights_preflight_errors(dict(invalid, dry_run=True)) == []
    strict = dict(invalid, rights_gate_mode="strict", rights_operator_evidence="valid-reference")
    assert "strict" in app.automatic_rights_preflight_errors(strict)[0]
    assert app.automatic_rights_preflight_errors(dict(strict, review_before_upload=True)) == []


def test_gui_start_stops_before_worker_when_operator_evidence_is_missing():
    class Label:
        def __init__(self):
            self.values = {}

        def config(self, **values):
            self.values.update(values)

    class Notebook:
        def __init__(self):
            self.selected = None

        def select(self, value):
            self.selected = value

    instance = object.__new__(app.App)
    instance._closing = False
    instance._thread = None
    instance._get_pages = lambda: [{"url": "https://www.facebook.com/source", "enabled": True}]
    instance._capture_worker_settings = lambda: {
        "account": "account1",
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "permission",
        "rights_operator_statement": STATEMENT,
        "rights_operator_evidence": "",
        "review_before_upload": False,
        "dry_run": False,
    }
    instance._save_ui_settings = lambda: pytest.fail("invalid rights settings must stop before saving or starting")
    logs = []
    instance._log = logs.append
    instance.status_lbl = Label()
    instance.nb = Notebook()
    instance.t_log = "log"

    app.App._do_start(instance, watch_mode=True)

    assert any("قبل الاتصال بـFacebook أو تنزيل أي فيديو" in message for message in logs)
    assert instance.status_lbl.values["text"].startswith("التشغيل الآلي متوقف")
    assert instance.nb.selected == "log"


def test_gui_strict_auto_mode_stops_before_facebook_or_download():
    class Label:
        def __init__(self):
            self.values = {}

        def config(self, **values):
            self.values.update(values)

    class Notebook:
        def select(self, _value):
            pass

    instance = object.__new__(app.App)
    instance._closing = False
    instance._thread = None
    instance._get_pages = lambda: [{"url": "https://www.facebook.com/source", "enabled": True}]
    instance._capture_worker_settings = lambda: {
        "account": "account1",
        "rights_gate_mode": "strict",
        "rights_operator_basis": "owned",
        "rights_operator_statement": "",
        "rights_operator_evidence": "",
        "review_before_upload": False,
        "dry_run": False,
    }
    instance._save_ui_settings = lambda: pytest.fail("strict automatic mode must stop before saving or starting")
    logs = []
    instance._log = logs.append
    instance.status_lbl = Label()
    instance.nb = Notebook()
    instance.t_log = "log"

    app.App._do_start(instance, watch_mode=True)

    assert any("قبل الاتصال بـFacebook أو تنزيل أي فيديو" in message for message in logs)
    assert any("strict" in message for message in logs)
    assert instance.status_lbl.values["text"].startswith("التشغيل الآلي متوقف")


def test_rights_hold_release_requires_valid_automatic_settings(monkeypatch):
    class Value:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    instance = object.__new__(app.App)
    instance._watching = False
    instance.root = object()
    instance.rights_gate_var = Value("operator_confirmed")
    instance.rights_basis_var = Value("permission")
    instance.rights_statement_e = Value(STATEMENT)
    instance.rights_evidence_e = Value("")
    instance.review_before_upload_var = Value(False)
    instance.dry_run_var = Value(False)
    warnings = []

    monkeypatch.setattr(app, "load_failed_queue", lambda: [{"id": "rights", "last_error": "rights_review_required"}])
    monkeypatch.setattr(app, "save_failed_queue", lambda *_args: pytest.fail("blocked items must stay queued"))
    monkeypatch.setattr(app.messagebox, "showwarning", lambda title, *_args, **_kwargs: warnings.append(title))
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *_args, **_kwargs: pytest.fail("invalid settings must stop release"))

    app.App._release_policy_holds(instance)

    assert warnings == ["إعدادات بوابة الحقوق غير مكتملة"]


def test_cli_preflight_reports_incomplete_operator_rights_before_worker(monkeypatch, tmp_path, capsys):
    class Logger:
        def info(self, *_args):
            pass

    monkeypatch.setattr(app, "APP_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(app, "run_automation_preflight", lambda *_args: (0, "جاهز"))
    monkeypatch.setattr(app, "load_pages", lambda: [{"enabled": True}])
    monkeypatch.setattr(app, "validate_content_review_settings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "validate_publication_controls", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "_configure_cli_logger", lambda: Logger())
    settings = {
        "account": "account1",
        "min_disk_config_error": False,
        "content_review_config_error": False,
        "dry_run": False,
        "content_review_enabled": False,
        "cutter_root": "",
        "cutter_python": "",
        "privacy": "private",
        "schedule_enabled": False,
        "allow_public_automation": False,
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "permission",
        "rights_operator_statement": STATEMENT,
        "rights_operator_evidence": "",
    }

    assert app._run_cli_preflight(settings) == 2
    assert "RIGHTS_OPERATOR_EVIDENCE" in capsys.readouterr().out


def test_release_rights_hold_requires_a_valid_automatic_gate(monkeypatch):
    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    items = [{"id": "video-1", "last_error": "rights_review_required"}]
    warnings = []
    instance = object.__new__(app.App)
    instance._watching = False
    instance.root = object()
    instance.rights_gate_var = Var("operator_confirmed")
    instance.rights_basis_var = Var("permission")
    instance.rights_statement_e = Var(STATEMENT)
    instance.rights_evidence_e = Var("")
    instance.review_before_upload_var = Var(False)
    instance.dry_run_var = Var(False)
    instance._log = lambda _message: None
    monkeypatch.setattr(app, "load_failed_queue", lambda: items)
    monkeypatch.setattr(app.messagebox, "showwarning", lambda *args, **kwargs: warnings.append(args))
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *_args, **_kwargs: pytest.fail("invalid rights must not be released"))
    monkeypatch.setattr(app, "save_failed_queue", lambda *_args: pytest.fail("invalid rights must not be saved"))

    app.App._release_policy_holds(instance)

    assert warnings and "RIGHTS_OPERATOR_EVIDENCE" in warnings[0][1]


def test_release_rights_hold_after_fix_returns_it_to_the_queue(monkeypatch):
    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    items = [{"id": "video-1", "last_error": "rights_review_required"}]
    saved = []
    instance = object.__new__(app.App)
    instance._watching = False
    instance.root = object()
    instance.rights_gate_var = Var("operator_confirmed")
    instance.rights_basis_var = Var("permission")
    instance.rights_statement_e = Var(STATEMENT)
    instance.rights_evidence_e = Var("permission-2026-01")
    instance.review_before_upload_var = Var(False)
    instance.dry_run_var = Var(False)
    instance._log = lambda _message: None
    monkeypatch.setattr(app, "load_failed_queue", lambda: items)
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(app.messagebox, "showwarning", lambda *args, **kwargs: pytest.fail("valid rights settings should pass"))
    monkeypatch.setattr(app, "save_failed_queue", lambda rows: saved.extend(rows))

    app.App._release_policy_holds(instance)

    assert saved and saved[0]["last_error"] == ""


def test_cli_settings_expose_rights_gate_keys():
    cfg = configparser.ConfigParser()
    cfg.read_string(
        "[STUDIO]\n"
        "RIGHTS_GATE_MODE = operator_confirmed\n"
        "RIGHTS_OPERATOR_BASIS = owned\n"
        f"RIGHTS_OPERATOR_STATEMENT = {STATEMENT}\n"
        "RIGHTS_OPERATOR_EVIDENCE =\n"
    )
    settings = app.build_cli_settings(cfg)
    assert settings["rights_gate_mode"] == "operator_confirmed"
    assert settings["rights_operator_basis"] == "owned"
    assert settings["rights_operator_statement"] == STATEMENT
    assert settings["rights_operator_evidence"] == ""


def test_cli_settings_default_to_strict_without_operator_attestation():
    cfg = configparser.ConfigParser()
    cfg.read_dict({"STUDIO": {}})
    settings = app.build_cli_settings(cfg)
    assert settings["rights_gate_mode"] == "strict"
    assert settings["rights_operator_basis"] == "owned"
    assert settings["rights_operator_statement"] == ""
    assert DEFAULT_OPERATOR_RIGHTS_STATEMENT == ""


def test_operator_confirmed_requires_a_nonblank_truthful_statement():
    settings = {
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "owned",
        "rights_operator_statement": "",
        "rights_operator_evidence": "",
        "review_before_upload": False,
        "dry_run": False,
    }
    assert app.automatic_rights_preflight_errors(settings)


def test_packaged_configuration_defaults_to_strict_with_manual_review():
    cfg = configparser.ConfigParser()
    assert cfg.read(PROJECT / "config.ini.example", encoding="utf-8")
    assert cfg.get("STUDIO", "RIGHTS_GATE_MODE") == "strict"
    assert cfg.get("STUDIO", "RIGHTS_OPERATOR_BASIS") == "owned"
    assert cfg.get("STUDIO", "RIGHTS_OPERATOR_STATEMENT").strip() == ""
    assert cfg.get("STUDIO", "RIGHTS_GATE_MIGRATION") == "strict-default-v3"
    assert cfg.getboolean("STUDIO", "REVIEW_BEFORE_UPLOAD") is True


def test_legacy_strict_mode_is_not_promoted_or_rewritten(tmp_path):
    config_path = tmp_path / "config.ini"
    original = (
        "[STUDIO]\nRIGHTS_GATE_MODE = strict\nRIGHTS_OPERATOR_BASIS = permission\n"
        f"RIGHTS_OPERATOR_STATEMENT = {STATEMENT}\nRIGHTS_OPERATOR_EVIDENCE =\n"
    )
    config_path.write_text(original, encoding="utf-8")

    values = app.load_ui_values(str(config_path))

    assert values[("STUDIO", "RIGHTS_GATE_MODE")] == "strict"
    assert values[("STUDIO", "RIGHTS_OPERATOR_BASIS")] == "permission"
    assert values[("STUDIO", "RIGHTS_OPERATOR_STATEMENT")] == STATEMENT
    assert values[("STUDIO", "REVIEW_BEFORE_UPLOAD")] == "true"
    assert config_path.read_text(encoding="utf-8") == original


def test_explicit_strict_mode_is_preserved():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({"STUDIO": {"RIGHTS_GATE_MODE": "strict", "RIGHTS_GATE_MIGRATION": "operator-confirmed-v2"}})

    assert migrate_legacy_rights_gate_config(cfg) is False
    assert cfg.get("STUDIO", "RIGHTS_GATE_MODE") == "strict"
    assert cfg.get("STUDIO", "RIGHTS_GATE_MIGRATION") == "operator-confirmed-v2"


def test_attested_auto_mode_releases_only_rights_held_queue_items():
    items = [
        {"id": "rights-1", "last_error": "rights_review_required"},
        {"id": "duplicate-1", "last_error": "duplicate_review_required"},
        {"id": "unknown-1", "last_error": "upload_result_unknown"},
    ]
    settings = {
        "rights_gate_mode": "operator_confirmed",
        "rights_operator_basis": "owned",
        "rights_operator_statement": STATEMENT,
        "rights_operator_evidence": "",
        "review_before_upload": False,
        "dry_run": False,
    }

    released, count = app.release_rights_holds_for_auto_mode(items, settings)

    assert count == 1
    assert released[0]["last_error"] == ""
    assert released[1]["last_error"] == "duplicate_review_required"
    assert released[2]["last_error"] == "upload_result_unknown"

    strict_items, strict_count = app.release_rights_holds_for_auto_mode(
        items, dict(settings, rights_gate_mode="strict")
    )
    assert strict_count == 0
    assert strict_items[0]["last_error"] == "rights_review_required"


def test_release_policy_holds_refuses_while_one_shot_worker_is_active(monkeypatch):
    class Worker:
        def is_alive(self):
            return True

    warnings = []
    instance = object.__new__(app.App)
    instance._watching = False
    instance._thread = Worker()
    instance.root = object()
    monkeypatch.setattr(app.messagebox, "showwarning", lambda *args, **kwargs: warnings.append(args))
    monkeypatch.setattr(app, "load_failed_queue", lambda: pytest.fail("an active worker must prevent queue mutation"))

    app.App._release_policy_holds(instance)

    assert warnings and "أوقف العامل" in warnings[0][1]
