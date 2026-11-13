"""انحدار 4.49.4 — سقف رفع صادق وأرشيف إيصالات بارد.

الحد المحلي 100 كان أعلى من قدرة مشروع Google المجاني (6 رفعات)، فلا يتدخل
قبل أن يرفض YouTube. وملف الإيصالات كان ينمو سجلاً لكل رفع ويُقرأ ويُنسخ
بالكامل عند كل رفع.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app


# ─────────────────────────── السقف الصادق ───────────────────────────
def test_default_upload_limit_matches_the_free_project_quota() -> None:
    assert app.LEGACY_DEFAULT_QUOTA_UNITS // app.LEGACY_UPLOAD_COST_UNITS == app.DEFAULT_DAILY_UPLOAD_LIMIT
    assert app.DEFAULT_DAILY_UPLOAD_LIMIT == 6


def test_example_config_ships_the_same_honest_limit() -> None:
    import configparser

    cfg = configparser.ConfigParser()
    cfg.read(Path(__file__).resolve().parents[1] / "config.ini.example", encoding="utf-8")
    assert cfg.getint("SETTINGS", "DAILY_UPLOAD_LIMIT") == app.DEFAULT_DAILY_UPLOAD_LIMIT


def test_explicit_limit_is_never_overridden() -> None:
    import configparser

    cfg = configparser.ConfigParser()
    cfg.read_dict({"SETTINGS": {"DAILY_UPLOAD_LIMIT": "25"}})
    assert app._daily_upload_limit_from_config(cfg) == 25


# ─────────────────────────── أرشيف الإيصالات ───────────────────────────
def _receipt(source_id: str, confirmed_at: datetime) -> dict:
    return {
        "source_id": source_id,
        "youtube_id": f"vid{source_id:0>8}"[:11],
        "status": "uploaded",
        "confirmed_at": confirmed_at.isoformat(timespec="seconds"),
    }


def _seed(tmp_path: Path, now: datetime):
    receipts_file = tmp_path / "upload_receipts.json"
    archive_file = tmp_path / "upload_receipts_archive.json"
    old = _receipt("old", now - timedelta(days=400))
    recent = _receipt("new", now - timedelta(days=10))
    receipts_file.write_text(json.dumps({"schema_version": 1, "receipts": {
        "old": old, "new": recent,
    }}), encoding="utf-8")
    return receipts_file, archive_file


def test_old_receipts_move_to_the_cold_archive(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    receipts_file, archive_file = _seed(tmp_path, now)

    moved = app.compact_upload_receipts(
        path=str(receipts_file), archive_path=str(archive_file), now=now
    )

    assert moved == 1
    active = json.loads(receipts_file.read_text(encoding="utf-8"))["receipts"]
    archived = json.loads(archive_file.read_text(encoding="utf-8"))["receipts"]
    assert set(active) == {"new"}
    assert set(archived) == {"old"}


def test_compaction_is_idempotent_and_keeps_the_archive(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    receipts_file, archive_file = _seed(tmp_path, now)
    app.compact_upload_receipts(path=str(receipts_file), archive_path=str(archive_file), now=now)

    assert app.compact_upload_receipts(
        path=str(receipts_file), archive_path=str(archive_file), now=now
    ) == 0
    archived = json.loads(archive_file.read_text(encoding="utf-8"))["receipts"]
    assert "old" in archived


def test_archive_merge_never_overwrites_an_existing_entry(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    receipts_file, archive_file = _seed(tmp_path, now)
    archive_file.write_text(json.dumps({"schema_version": 1, "receipts": {
        "old": {**_receipt("old", now - timedelta(days=900)), "title": "السجل الأصلي"},
    }}), encoding="utf-8")

    app.compact_upload_receipts(path=str(receipts_file), archive_path=str(archive_file), now=now)

    archived = json.loads(archive_file.read_text(encoding="utf-8"))["receipts"]
    assert archived["old"]["title"] == "السجل الأصلي"


def test_zero_age_disables_compaction(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    receipts_file, archive_file = _seed(tmp_path, now)

    assert app.compact_upload_receipts(
        max_age_days=0, path=str(receipts_file), archive_path=str(archive_file), now=now
    ) == 0
    assert not archive_file.exists()


def test_corrupt_archive_is_treated_as_empty_not_fatal(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    receipts_file, archive_file = _seed(tmp_path, now)
    archive_file.write_text("{broken", encoding="utf-8")

    moved = app.compact_upload_receipts(
        path=str(receipts_file), archive_path=str(archive_file), now=now
    )

    assert moved == 1
    archived = json.loads(archive_file.read_text(encoding="utf-8"))["receipts"]
    assert set(archived) == {"old"}


def test_receipts_without_a_timestamp_are_kept_active(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    receipts_file = tmp_path / "upload_receipts.json"
    archive_file = tmp_path / "upload_receipts_archive.json"
    receipts_file.write_text(json.dumps({"schema_version": 1, "receipts": {
        "no-ts": {"source_id": "no-ts", "youtube_id": "abcdefghijk", "status": "uploaded"},
    }}), encoding="utf-8")

    assert app.compact_upload_receipts(
        path=str(receipts_file), archive_path=str(archive_file), now=now
    ) == 0
    assert "no-ts" in json.loads(receipts_file.read_text(encoding="utf-8"))["receipts"]


@pytest.mark.parametrize("stamp", ["2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00"])
def test_timestamp_parsing_handles_aware_and_naive_values(stamp: str) -> None:
    ts = app._receipt_timestamp({"confirmed_at": stamp})
    assert ts is not None and ts.tzinfo is not None
