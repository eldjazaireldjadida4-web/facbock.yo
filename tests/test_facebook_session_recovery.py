from __future__ import annotations

import configparser
import queue
import threading

import fb_youtube_uploader_v49_pro as app


FUTURE = 4_102_444_800


def _cookie(domain, name, value, expiry=FUTURE):
    return "\t".join((domain, "TRUE", "/", "TRUE", str(expiry), name, value))


def test_cookie_fingerprint_marks_only_active_session_pairs(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "\n".join((
            _cookie(".facebook.com", "c_user", "test-user"),
            _cookie(".facebook.com", "xs", "test-session"),
        )),
        encoding="utf-8",
    )

    fingerprint = app.facebook_cookie_fingerprint(str(path))
    expired_path = tmp_path / "expired-cookies.txt"
    expired_path.write_text(
        "\n".join((
            _cookie(".facebook.com", "c_user", "expired-user", expiry=999),
            _cookie(".facebook.com", "xs", "test-session"),
        )),
        encoding="utf-8",
    )
    expired_fingerprint = app.facebook_cookie_fingerprint(str(expired_path))

    assert fingerprint is not None
    assert fingerprint[0] == app.os.path.normcase(app.os.path.abspath(path))
    assert fingerprint[-1] is True
    assert "test-session" not in repr(fingerprint)
    assert expired_fingerprint is not None
    assert expired_fingerprint[-1] is False


def test_cookies_arg_resolves_relative_path_from_app_directory(monkeypatch, tmp_path):
    app_dir = tmp_path / "application"
    other_dir = tmp_path / "working-directory"
    app_dir.mkdir()
    other_dir.mkdir()
    cookie_file = app_dir / "cookies.txt"
    cookie_file.write_text("session cookie", encoding="utf-8")
    monkeypatch.setattr(app, "APP_DIRECTORY", str(app_dir))
    monkeypatch.chdir(other_dir)

    resolved = app.cookies_arg({"cookies_enabled": True, "cookies_file": "cookies.txt"})

    assert resolved == str(cookie_file.resolve())


def test_refresh_cookie_settings_reads_only_the_session_options(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[STUDIO]\nCOOKIES_ENABLED = true\nCOOKIES_FILE = D:/facebook/cookies.txt\n",
        encoding="utf-8",
    )
    settings = {"cookies_enabled": False, "cookies_file": "cookies.txt", "privacy": "private"}

    result = app.refresh_facebook_cookie_settings(settings, str(config_path))

    assert result is settings
    assert settings["cookies_enabled"] is True
    assert settings["cookies_file"] == "D:/facebook/cookies.txt"
    assert settings["privacy"] == "private"


def test_auth_wait_resumes_only_after_the_cookie_fingerprint_changes(monkeypatch):
    previous = ("cookies.txt", 100, 100, True)
    updated = ("cookies.txt", 101, 104, True)
    fingerprints = iter((previous, updated))

    class StopEvent:
        def __init__(self):
            self.wait_count = 0

        def wait(self, _seconds):
            self.wait_count += 1
            return False

    class Queue:
        def __init__(self):
            self.items = []

        def put(self, item):
            self.items.append(item)

    class Worker:
        pass

    worker = Worker()
    worker._stop = StopEvent()
    worker._uiq = Queue()
    settings = {"cookies_enabled": True, "cookies_file": "cookies.txt"}
    monkeypatch.setattr(app, "refresh_facebook_cookie_settings", lambda _settings: _settings)
    monkeypatch.setattr(app, "cookies_arg", lambda _settings: "cookies.txt")
    monkeypatch.setattr(app, "facebook_cookie_fingerprint", lambda _path: next(fingerprints))

    resumed = app.App._wait_for_facebook_cookie_refresh(worker, settings, previous)

    assert resumed is True
    assert worker._stop.wait_count == 2
    assert worker._uiq.items[0][0] == "countdown"


def test_auth_wait_stops_without_resuming_when_the_operator_stops(monkeypatch):
    class StopEvent:
        def wait(self, _seconds):
            return True

    class Queue:
        def put(self, _item):
            return None

    class Worker:
        pass

    worker = Worker()
    worker._stop = StopEvent()
    worker._uiq = Queue()
    monkeypatch.setattr(
        app,
        "refresh_facebook_cookie_settings",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must stop before polling")),
    )

    assert app.App._wait_for_facebook_cookie_refresh(worker, {}, None) is False


def test_cookie_settings_can_be_saved_while_the_monitor_is_running(monkeypatch, tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[SETTINGS]\nPRIVACY = private\n[STUDIO]\nCOOKIES_ENABLED = false\nCOOKIES_FILE = cookies.txt\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "APP_DIRECTORY", str(tmp_path))
    logs = []

    class Value:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Worker:
        cookies_enabled_var = Value(True)
        cookies_path_var = Value("D:/facebook/cookies.txt")
        facebook_proxy_var = Value("")
        facebook_network_timeout_var = Value("12")
        _log = staticmethod(logs.append)

    app.App._save_facebook_cookie_settings(Worker())

    saved = configparser.ConfigParser(interpolation=None)
    saved.read(config_path, encoding="utf-8")
    assert saved.get("SETTINGS", "PRIVACY") == "private"
    assert saved.getboolean("STUDIO", "COOKIES_ENABLED") is True
    assert saved.get("STUDIO", "COOKIES_FILE") == "D:/facebook/cookies.txt"
    assert saved.get("WATCH", "PROXY") == ""
    assert saved.getint("WATCH", "NETWORK_PREFLIGHT_TIMEOUT") == 12
    assert any("حُفظت إعدادات Facebook" in message for message in logs)


def test_watch_worker_runs_another_cycle_after_a_valid_cookie_refresh(monkeypatch):
    cycle_calls = []
    refresh_calls = []
    logs = []

    class Lock:
        def acquire(self):
            return self

        def release(self):
            return None

    class Quota:
        def __init__(self, daily_limit, _log):
            self.daily_limit = daily_limit
            self.used = 0

    class Worker:
        pass

    worker = Worker()
    worker._stop = threading.Event()
    worker._uiq = queue.Queue()
    worker.stats = {"total_uploaded": 0, "total_failed": 0, "cycles": 0}
    worker._log = logs.append

    def run_cycle(ctx, _pages, _settings, watch_mode):
        assert watch_mode is True
        cycle_calls.append(len(cycle_calls) + 1)
        if len(cycle_calls) == 1:
            ctx["facebook_auth_required"] = True
            ctx["facebook_cookie_fingerprint"] = ("cookies.txt", 1, 10, False)
            return 0, 1
        worker._stop.set()
        return 0, 0

    worker._run_cycle = run_cycle
    worker._wait_for_facebook_cookie_refresh = lambda *_args: refresh_calls.append(True) or True
    worker._maybe_daily_summary = lambda *_args: None
    worker._countdown_wait = lambda *_args: None
    monkeypatch.setattr(app, "AutomationRunLock", lambda *_args: Lock())
    monkeypatch.setattr(app, "get_youtube", lambda *_args, **_kwargs: (object(), None))
    monkeypatch.setattr(app, "QuotaManager", Quota)
    monkeypatch.setattr(app, "send_telegram", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "save_stats", lambda *_args: None)

    app.App._worker(
        worker,
        [],
        {
            "cli_mode": True,
            "account": "account1",
            "daily_upload_limit": 10,
            "interval_minutes": 1,
            "tg_errors": False,
        },
        True,
    )

    assert cycle_calls == [1, 2]
    assert refresh_calls == [True]
    assert any("ستُستأنف المراقبة" in message for message in logs)
    finished = [item for item in list(worker._uiq.queue) if item[0] == "finished"]
    assert finished and finished[0][1]["watch"] is True
