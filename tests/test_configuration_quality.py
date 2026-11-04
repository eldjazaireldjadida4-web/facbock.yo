from __future__ import annotations

import configparser
import json
import sys
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app


PROJECT = Path(__file__).resolve().parents[1]


def test_example_configuration_parses_without_inline_comment_values():
    cfg = configparser.ConfigParser()
    assert cfg.read(PROJECT / "config.ini.example", encoding="utf-8")
    assert cfg.getint("SETTINGS", "MIN_DISK_GB") == 2
    assert cfg.getboolean("SETTINGS", "DRY_RUN") is False
    assert cfg.get("STUDIO", "RIGHTS_MANIFEST_FILE") == "rights_manifest.json"
    assert cfg.getboolean("SAFETY", "ENABLED") is False
    assert cfg.getboolean("SAFETY", "ALLOW_PUBLIC_AUTOMATION") is False
    assert cfg.getboolean("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE") is True
    assert cfg.get("SAFETY", "CUTTER_ROOT") == ""
    assert cfg.get("STUDIO", "RIGHTS_GATE_MODE") == "strict"
    assert cfg.getboolean("STUDIO", "REVIEW_BEFORE_UPLOAD") is True
    assert cfg.get("STUDIO", "RIGHTS_OPERATOR_STATEMENT").strip() == ""


def test_runtime_ledgers_and_default_directories_are_app_relative():
    paths = (
        app.VIDEO_DIRECTORY, app.CLIENT_SECRETS_FILE, app.ERROR_LOG_FILE,
        app.UPLOADED_IDS_FILE, app.SEEN_IDS_FILE, app.PAGES_FILE, app.ACCOUNTS_FILE, app.STATS_FILE,
        app.PAGE_STATUS_FILE, app.STATE_FILE, app.FAILED_QUEUE_FILE,
        app.HISTORY_FILE, app.CHANNEL_INDEX_FILE, app.RIGHTS_MANIFEST_FILE,
        app.AI_KEYS_FILE,
    )
    assert all(Path(path).is_absolute() for path in paths)
    assert all(Path(path).parent == Path(app.APP_DIRECTORY) for path in paths)


def test_cli_settings_load_safety_gate_and_dry_run():
    cfg = configparser.ConfigParser()
    cfg.read_string(
        "[SETTINGS]\nDRY_RUN = true\n"
        "[SAFETY]\nENABLED = true\nCUTTER_ROOT = /srv/cutter\n"
        "CUTTER_PYTHON = /srv/cutter/.venv/bin/python\n"
        "[STUDIO]\nOMIT_SOURCE_PAGE_NAME_FROM_TITLE = false\n"
    )

    settings = app.build_cli_settings(cfg)

    assert settings["dry_run"] is True
    assert settings["content_review_enabled"] is True
    assert settings["cutter_root"] == "/srv/cutter"
    assert settings["cutter_python"] == "/srv/cutter/.venv/bin/python"
    assert settings["allow_public_automation"] is False
    assert settings["omit_source_page_name"] is False


def test_relative_cli_paths_resolve_beside_application(monkeypatch, tmp_path):
    app_dir = tmp_path / "application"
    app_dir.mkdir()
    cookie_path = app_dir / "cookies.txt"
    cookie_path.write_text("local test file", encoding="utf-8")
    monkeypatch.setattr(app, "APP_DIRECTORY", str(app_dir))
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_string(
        "[SETTINGS]\nVIDEO_DIRECTORY = videos\nCLIENT_SECRETS_FILE = oauth/client.json\n"
        "[STUDIO]\nCOOKIES_ENABLED = true\nCOOKIES_FILE = cookies.txt\n"
        "RIGHTS_MANIFEST_FILE = data/rights.json\nLOGO_PATH = assets/logo.png\n"
    )

    settings = app.build_cli_settings(cfg)

    assert app._app_path("videos") == str((app_dir / "videos").resolve())
    assert app._app_path("oauth/client.json") == str((app_dir / "oauth/client.json").resolve())
    assert settings["rights_manifest_file"] == str((app_dir / "data/rights.json").resolve())
    assert settings["logo_path"] == str((app_dir / "assets/logo.png").resolve())
    assert app.cookies_arg(settings) == str(cookie_path.resolve())


def test_youtube_oauth_token_is_loaded_from_app_directory(monkeypatch, tmp_path):
    app_dir = tmp_path / "application"
    working_dir = tmp_path / "outside"
    app_dir.mkdir()
    working_dir.mkdir()
    token_path = app_dir / "channel1_token.json"
    token_path.write_text(json.dumps({"scopes": app.SCOPES}), encoding="utf-8")
    monkeypatch.setattr(app, "APP_DIRECTORY", str(app_dir))
    monkeypatch.chdir(working_dir)
    loaded_paths = []

    class Credentials:
        valid = True
        expired = False
        refresh_token = None

    monkeypatch.setattr(
        app.Credentials,
        "from_authorized_user_file",
        lambda path, _scopes: loaded_paths.append(path) or Credentials(),
    )
    monkeypatch.setattr(app, "build", lambda *_args, **_kwargs: "youtube-service")

    service, error = app.get_youtube("channel1", allow_interactive=False)

    assert (service, error) == ("youtube-service", None)
    assert loaded_paths == [str(token_path)]


@pytest.mark.parametrize("allow_interactive", [True, False])
def test_expired_youtube_token_without_refresh_token_is_reauthorized_safely(
    monkeypatch, tmp_path, allow_interactive
):
    app_dir = tmp_path / "application"
    app_dir.mkdir()
    secrets_file = app_dir / "client_secrets.json"
    secrets_file.write_text("{}", encoding="utf-8")
    token_file = app_dir / "channel1_token.json"
    token_file.write_text(json.dumps({"scopes": app.SCOPES}), encoding="utf-8")
    monkeypatch.setattr(app, "APP_DIRECTORY", str(app_dir))

    class ExpiredCredentials:
        valid = False
        expired = True
        refresh_token = None

    class FreshCredentials:
        valid = True
        expired = False
        refresh_token = "refresh-token"

        def to_json(self):
            return json.dumps({"scopes": app.SCOPES, "token": "test-access-token", "refresh_token": self.refresh_token})

    monkeypatch.setattr(
        app.Credentials,
        "from_authorized_user_file",
        lambda *_args, **_kwargs: ExpiredCredentials(),
    )
    flow_calls = []

    class Flow:
        def run_local_server(self, port=0):
            flow_calls.append(port)
            return FreshCredentials()

    monkeypatch.setattr(
        app.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *_args, **_kwargs: Flow(),
    )
    built = []
    monkeypatch.setattr(
        app, "build", lambda *_args, **kwargs: built.append(kwargs["credentials"]) or "youtube-service"
    )

    service, error = app.get_youtube(
        "channel1", secrets_file=str(secrets_file), allow_interactive=allow_interactive
    )

    if allow_interactive:
        assert (service, error) == ("youtube-service", None)
        assert flow_calls == [0]
        assert len(built) == 1 and isinstance(built[0], FreshCredentials)
    else:
        assert service is None
        assert "لا يوجد تفويض YouTube صالح" in error
        assert flow_calls == []
        assert built == []


@pytest.mark.parametrize("value", ["two", "nan", "inf", "-1"])
def test_invalid_cli_min_disk_value_fails_closed(value):
    cfg = configparser.ConfigParser()
    cfg.read_string(f"[SETTINGS]\nMIN_DISK_GB = {value}\n")

    settings = app.build_cli_settings(cfg)

    assert settings["min_disk_gb"] == 2.0
    assert settings["min_disk_config_error"] is True


def test_cli_title_hides_source_page_by_default():
    cfg = configparser.ConfigParser()
    cfg.read_dict({"STUDIO": {}})
    assert app.build_cli_settings(cfg)["omit_source_page_name"] is True


def test_invalid_safety_boolean_fails_closed():
    cfg = configparser.ConfigParser()
    cfg.read_string("[SAFETY]\nENABLED = maybe\n")

    settings = app.build_cli_settings(cfg)

    assert settings["content_review_enabled"] is True
    assert settings["content_review_config_error"] is True


def test_content_review_settings_fail_closed_when_enabled(tmp_path):
    assert app.validate_content_review_settings(True, "")
    root = tmp_path / "cutter"
    script = root / "scripts" / "upload_content_review.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    assert app.validate_content_review_settings(True, str(root), sys.executable) == []
    assert app.validate_content_review_settings(True, str(root), str(tmp_path / "missing-python"))
    assert app.validate_content_review_settings(False, "") == []

def test_cli_entry_point_dispatches_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app,
        "run_cli",
        lambda dry_run=False, once=False: calls.append((dry_run, once)) or 7,
    )
    assert app.main(["--cli", "--dry-run"]) == 7
    assert calls == [(True, False)]

def test_cli_version_is_available(capsys):
    with pytest.raises(SystemExit) as result:
        app.main(["--version"])
    assert result.value.code == 0
    assert f"v{app.__version__}" in capsys.readouterr().out

def test_dry_run_requires_cli(capsys):
    with pytest.raises(SystemExit) as result:
        app.main(["--dry-run"])
    assert result.value.code == 2
    assert "require --cli" in capsys.readouterr().err


def test_once_requires_cli(capsys):
    with pytest.raises(SystemExit) as result:
        app.main(["--once"])
    assert result.value.code == 2
    assert "require --cli" in capsys.readouterr().err

def test_cli_entry_point_dispatches_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app,
        "run_cli",
        lambda dry_run=False, once=False: calls.append((dry_run, once)) or 9,
    )
    assert app.main(["--cli", "--once"]) == 9
    assert calls == [(False, True)]


def test_publication_requires_manual_review_or_explicit_opt_in():
    assert app.validate_publication_controls("public", False, False, False)
    assert app.validate_publication_controls("unlisted", False, False, False)
    assert app.validate_publication_controls("private", True, False, False)
    assert app.validate_publication_controls("public", False, True, False) == []
    assert app.validate_publication_controls("public", False, False, True) == []
    assert app.validate_publication_controls("public", False, False, False, dry_run=True) == []

def test_preflight_entry_point_runs_without_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app,
        "run_cli",
        lambda dry_run=False, once=False, preflight_only=False: calls.append(preflight_only) or 11,
    )
    assert app.main(["--preflight"]) == 11
    assert calls == [True]

def test_cli_oauth_failure_never_opens_a_browser(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        app.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *_args, **_kwargs: pytest.fail("headless CLI must not start interactive OAuth"),
    )
    youtube, error = app.get_youtube(
        "automation",
        secrets_file=str(tmp_path / "client_secrets.json"),
        allow_interactive=False,
    )
    assert youtube is None
    assert "مفقود" in error
    assert "الإعدادات المتقدمة" in error

@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        ({"failed": 0, "fatal": None}, 0),
        ({"failed": 1, "fatal": None}, 1),
        ({"failed": 0, "fatal": "OAuth unavailable"}, 2),
    ],
)
def test_cli_returns_worker_outcome(monkeypatch, tmp_path, outcome, expected_code):
    settings = {
        "content_review_config_error": False,
        "content_review_enabled": False,
        "dry_run": True,
        "privacy": "private",
        "schedule_enabled": False,
        "allow_public_automation": False,
        "account": "automation",
        "cookies_enabled": False,
        "interval_minutes": 10,
        "cutter_root": "",
        "cutter_python": "",
    }
    monkeypatch.setattr(app, "build_cli_settings", lambda: settings.copy())
    monkeypatch.setattr(app, "_run_cli_preflight", lambda _settings: 0)
    monkeypatch.setattr(app, "load_pages", lambda: [{"enabled": True, "url": "https://example.com"}])
    monkeypatch.setattr(app, "load_stats", lambda: {})
    monkeypatch.setattr(app, "APP_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(app, "_configure_cli_logger", lambda: type("Logger", (), {"info": lambda *_args: None})())
    monkeypatch.setattr(
        app.App,
        "_worker",
        lambda shim, *_args, **_kwargs: shim._uiq.put(("finished", outcome)),
    )

    assert app.run_cli(once=True) == expected_code


def test_cli_returns_scheduler_friendly_worker_status(monkeypatch):
    settings = {
        "content_review_config_error": False,
        "content_review_enabled": False,
        "dry_run": False,
        "privacy": "private",
        "schedule_enabled": False,
        "allow_public_automation": False,
        "interval_minutes": 10,
        "account": "account1",
        "cookies_enabled": False,
    }
    monkeypatch.setattr(app, "build_cli_settings", lambda: dict(settings))
    monkeypatch.setattr(app, "load_stats", lambda: {"total_uploaded": 0, "total_failed": 0})
    monkeypatch.setattr(app, "validate_content_review_settings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "validate_publication_controls", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app, "load_pages", lambda: [{"enabled": True}])
    monkeypatch.setattr(app, "_run_cli_preflight", lambda _settings: 0)
    monkeypatch.setattr(app, "_configure_cli_logger", lambda: None)
    def finish_with_failures(shim, _pages, run_settings, watch_mode):
        assert run_settings["cli_mode"] is True
        assert watch_mode is False
        shim._uiq.put(("finished", {"failed": 2, "fatal": None}))
    monkeypatch.setattr(app.App, "_worker", finish_with_failures)
    assert app.run_cli(once=True) == 1

def test_cli_cookies_follow_explicit_studio_toggle(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("session", encoding="utf-8")
    cfg = configparser.ConfigParser()
    cfg.read_dict({"STUDIO": {"COOKIES_FILE": str(cookies), "COOKIES_ENABLED": "false"}})
    monkeypatch.setattr(app, "load_accounts", lambda: ["account1"])
    settings = app.build_cli_settings(cfg)
    assert settings["cookies_enabled"] is False
    assert app.cookies_arg(settings) is None
    cfg.set("STUDIO", "COOKIES_ENABLED", "true")
    settings = app.build_cli_settings(cfg)
    assert settings["cookies_enabled"] is True
    assert app.cookies_arg(settings) == str(cookies)
