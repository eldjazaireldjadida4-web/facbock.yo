from __future__ import annotations

import json

import fb_youtube_uploader_v49_pro as app


def test_page_status_round_trip_and_sanitizes_counts(tmp_path):
    path = tmp_path / "page_status.json"
    records = {
        "https://www.facebook.com/news/reels/": {
            "time": "2026-10-28 16:00:00",
            "found": "7",
            "new": -2,
            "status": "تم الفحص",
            "ignored": "value",
        }
    }

    app.save_page_status(records, path)
    loaded = app.load_page_status(path)

    assert loaded == {
        "https://www.facebook.com/news/reels/": {
            "time": "2026-10-28 16:00:00",
            "found": 7,
            "new": 0,
            "status": "تم الفحص",
        }
    }


def test_page_status_ignores_malformed_records(tmp_path):
    path = tmp_path / "page_status.json"
    path.write_text(
        json.dumps({
            "": {"time": "now", "found": 1, "new": 1, "status": "ok"},
            "https://www.facebook.com/news": "not-a-record",
            7: {"time": "now", "found": 1, "new": 1, "status": "ok"},
        }),
        encoding="utf-8",
    )

    assert app.load_page_status(path) == {}


def test_page_status_storage_is_bounded_to_the_newest_500_entries():
    records = {
        f"https://www.facebook.com/page-{index}": {
            "time": f"2026-10-28 00:{index:04d}",
            "found": index,
            "new": index,
            "status": "ok",
        }
        for index in range(501)
    }

    cleaned = app._clean_page_status_records(records)

    assert len(cleaned) == 500
    assert "https://www.facebook.com/page-0" not in cleaned
    assert "https://www.facebook.com/page-500" in cleaned
