from __future__ import annotations

import fb_youtube_uploader_v49_pro as app


def test_rotate_history_moves_file_to_bak1(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    path = app.HISTORY_FILE
    with open(path, "w", encoding="utf-8") as f:
        f.write("x" * (app.HISTORY_MAX_BYTES + 100))
    app._rotate_history_if_needed()
    import os

    assert not os.path.exists(path)
    assert os.path.getsize(path + ".bak1") == app.HISTORY_MAX_BYTES + 100


def test_rotate_history_keeps_small_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    with open(app.HISTORY_FILE, "w", encoding="utf-8") as f:
        f.write("small")
    app._rotate_history_if_needed()
    import os

    assert os.path.exists(app.HISTORY_FILE)


def test_prune_failed_queue_drops_stale_and_keeps_fresh():
    import time

    now = time.time()
    stale = {"id": "a", "url": "u", "failed_at": now - (app.FAILED_QUEUE_MAX_AGE_DAYS + 1) * 86400}
    fresh = {"id": "b", "url": "u", "failed_at": now - 100}
    legacy = {"id": "c", "url": "u"}  # بدون طابع — يُحفظ
    kept = app.prune_failed_queue([stale, fresh, legacy])
    assert [it["id"] for it in kept] == ["b", "c"]


def test_prune_failed_queue_respects_zero_disable():
    items = [{"id": "a", "failed_at": 1}]
    assert app.prune_failed_queue(items, 0) == items


def test_build_cli_settings_reads_failed_max_age_days(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[SETTINGS]\nFAILED_MAX_AGE_DAYS = 7\n", encoding="utf-8")
    import configparser

    parser = configparser.ConfigParser()
    parser.read(cfg, encoding="utf-8")
    assert parser.getint("SETTINGS", "FAILED_MAX_AGE_DAYS") == 7
