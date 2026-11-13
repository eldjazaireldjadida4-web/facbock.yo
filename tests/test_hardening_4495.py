"""انحدار 4.49.5 — نص عربي يُقصّ في منتصف الحرف، وتكرار لا يُكتشف.

العنوان والوصف يمرّان في كل رفع؛ قصّهما داخل حركة أو نصف علم يشوّه الناتج
بلا خطأ. وعلى الجانب الآخر كانت بصمات العناوين القصيرة تُهمل، وتشابه العنوان
يكفي لحجب حلقة مختلفة المدة إلى الأبد.
"""
from __future__ import annotations

import configparser
import json
import unicodedata
from pathlib import Path

import pytest

import fb_youtube_uploader_v49_pro as app
import texttools
from settings_validation import validate_configuration


# ─────────────────────────── القصّ الآمن ───────────────────────────
def test_title_never_ends_on_a_dangling_diacritic() -> None:
    title = "ت" * 98 + "م" + "\u064f" + "د"
    result = texttools.build_title(title)

    assert len(result) <= 100
    assert unicodedata.category(result[-1]) not in {"Mn", "Mc", "Me"}


def test_title_never_cuts_a_flag_in_half() -> None:
    title = "ب" * 99 + "🇸🇦" + "ز" * 10
    result = texttools.build_title(title)

    assert not (0x1F1E6 <= ord(result[-1]) <= 0x1F1FF)


def test_description_truncation_respects_combining_marks() -> None:
    body = "ب" * (texttools.YOUTUBE_DESCRIPTION_MAX - 2) + "م" + "\u0650" + "x" * 10
    result = texttools.build_description(body, "", "", [])

    assert len(result) <= texttools.YOUTUBE_DESCRIPTION_MAX
    assert unicodedata.category(result[-1]) not in {"Mn", "Mc", "Me"}


def test_truncation_does_not_collapse_to_the_first_word() -> None:
    title = "تغطية خاصة #" + "ا" * 120
    result = texttools.build_title(title)

    assert len(result) > 40, result


# ─────────────────────────── الوسوم ───────────────────────────
def test_episode_numbers_survive_hashtag_removal() -> None:
    assert "#1" in texttools.optimize_title("الحلقة #1 من المسلسل", remove_hashtags=True)
    assert "#١٢" in texttools.optimize_title("شاهد الحلقة #١٢ الآن", remove_hashtags=True)
    assert "C#9" in texttools.optimize_title("C#9 دروس", remove_hashtags=True)


def test_real_hashtags_are_still_removed() -> None:
    result = texttools.optimize_title("عاجل #Breaking_News خبر مهم", remove_hashtags=True)
    assert "#" not in result
    assert "خبر مهم" in result


# ─────────────────────────── صلاحية الإعدادات ───────────────────────────
def _valid_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({
        "SETTINGS": {
            "VIDEO_DIRECTORY": "videos",
            "CLIENT_SECRETS_FILE": "client_secrets.json",
            "PRIVACY": "private",
            "DRY_RUN": "false",
        },
        "STUDIO": {
            "RIGHTS_GATE_MODE": "strict",
            "RIGHTS_OPERATOR_BASIS": "owned",
            "REVIEW_BEFORE_UPLOAD": "true",
            "SCHEDULE": "false",
            "SCHEDULE_MIN": "20",
            "SCHEDULE_MAX": "60",
            "CATEGORY": "25",
        },
        "SAFETY": {"ALLOW_PUBLIC_AUTOMATION": "false"},
    })
    return cfg


@pytest.mark.parametrize(
    "section, key, value, expected",
    [
        ("WATCH", "INTERVAL_MINUTES", "800", "INTERVAL_MINUTES"),
        ("STUDIO", "LOGO_SCALE", "80", "LOGO_SCALE"),
        ("STUDIO", "LOGO_OPACITY", "0", "LOGO_OPACITY"),
        ("STUDIO", "LOGO_COVER_WIDTH", "50", "LOGO_COVER_WIDTH"),
    ],
)
def test_validation_matches_runtime_bounds(section, key, value, expected) -> None:
    cfg = _valid_config()
    if not cfg.has_section(section):
        cfg.add_section(section)
    cfg.set(section, key, value)

    errors = validate_configuration(cfg)

    assert any(expected in error for error in errors)


def test_validation_rejects_an_unknown_youtube_category() -> None:
    cfg = _valid_config()
    cfg.set("STUDIO", "CATEGORY", "99")

    assert any("CATEGORY" in error for error in validate_configuration(cfg))


def test_validation_accepts_every_gui_offered_category() -> None:
    for category_id in app.YT_CATEGORIES.values():
        cfg = _valid_config()
        cfg.set("STUDIO", "CATEGORY", str(category_id))
        assert not any("CATEGORY" in error for error in validate_configuration(cfg))


# ─────────────────────────── كشف التكرار ───────────────────────────
def test_short_titles_are_remembered_and_detected(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app, "STATE_FILE", str(state_file))
    monkeypatch.setattr(app, "is_generic_title", lambda _title: False)

    app.remember_content("هدف رائع")

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "هدفرائع" in state["title_fingerprints"]
    assert app.is_duplicate_content("هدف رائع") is True


def test_almost_identical_title_with_a_different_duration_is_not_a_duplicate() -> None:
    videos = [{
        "video_id": "abc",
        "title": "ملخص مباراة الأهلي والزمالك",
        "duration": 900,
        "published_at": "2026-01-01",
    }]

    matches = app.find_channel_title_matches("ملخص مباراة الأهلي والزمالك 2", 60, videos)

    assert matches == []


def test_same_title_and_duration_still_matches() -> None:
    videos = [{
        "video_id": "abc",
        "title": "ملخص مباراة الأهلي والزمالك",
        "duration": 900,
        "published_at": "2026-01-01",
    }]

    matches = app.find_channel_title_matches("ملخص مباراة الأهلي والزمالك", 905, videos)

    assert matches and matches[0]["video_id"] == "abc"


def test_unknown_duration_is_reported_last() -> None:
    videos = [
        {"video_id": "unknown", "title": "خبر اليوم", "duration": None},
        {"video_id": "known", "title": "خبر اليوم", "duration": 60},
    ]

    matches = app.find_channel_title_matches("خبر اليوم", 60, videos)

    assert [match["video_id"] for match in matches][0] == "known"


# ─────────────────────────── فهرس القناة ───────────────────────────
def test_malformed_channel_index_is_filtered_not_fatal(tmp_path: Path, monkeypatch) -> None:
    index_file = tmp_path / "channel_video_index.json"
    index_file.write_text(json.dumps({
        "account_key": "main",
        "fetched_at": 9_999_999_999,
        "channel_id": "UC1",
        "videos": ["oops", 5, {"video_id": "ok", "title": "خبر", "duration": 60}],
    }), encoding="utf-8")
    monkeypatch.setattr(app, "CHANNEL_INDEX_FILE", str(index_file))

    videos = app.fetch_channel_video_index(object(), account_key="main")

    assert videos == [{"video_id": "ok", "title": "خبر", "duration": 60}]


# ─────────────────────────── الصورة المصغرة ───────────────────────────
def test_thumbnail_conversion_failure_returns_none(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "thumb.png"
    image.write_bytes(b"x" * (app.YOUTUBE_THUMB_MAX_BYTES + 10))

    class BrokenImage:
        def __enter__(self):
            raise OSError("corrupt png")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(app.Image, "open", lambda *_a, **_k: BrokenImage())

    assert app._thumbnail_upload_path(str(image)) is None


def test_thumbnail_upload_refuses_when_conversion_fails(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "thumb.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(app, "_thumbnail_upload_path", lambda _path: None)
    messages = []

    assert app.upload_thumbnail(object(), "vid", str(image), messages.append) is False
    assert any("2MB" in message for message in messages)
