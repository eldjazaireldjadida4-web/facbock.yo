"""اختبارات v4.9: حفظ/استرجاع إعدادات الواجهة في config.ini + سلامة التخطيط."""
from __future__ import annotations

import configparser
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "fb_youtube_uploader_v49_pro.py"
spec = importlib.util.spec_from_file_location("pro49", MODULE_PATH)
assert spec and spec.loader
pro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pro)


def test_ui_defaults_cover_all_keys():
    vals = pro.load_ui_values("لا-يوجد-ملف.ini")
    assert len(vals) == len(pro.UI_KEYS)
    assert vals[("SETTINGS", "ACCOUNT")] == "account1"
    assert vals[("SETTINGS", "PRIVACY")] == "private"
    assert vals[("SETTINGS", "DAILY_UPLOAD_LIMIT")] == str(pro.DEFAULT_DAILY_UPLOAD_LIMIT)
    assert vals[("WATCH", "INTERVAL_MINUTES")] == "10"
    assert vals[("AI", "MODEL")] == pro.GEMINI_MODELS[0]
    assert vals[("AI", "TRANSCRIBE_LOCAL")] == "false"
    assert vals[("AI", "TRANSCRIPTION_MODEL")] == "base"
    assert vals[("STUDIO", "CATEGORY")] == "25"
    assert vals[("STUDIO", "PRESERVE_FACEBOOK_TITLE")] == "true"
    assert vals[("STUDIO", "REVIEW_BEFORE_UPLOAD")] == "true"
    assert vals[("STUDIO", "RIGHTS_GATE_MODE")] == "strict"
    assert vals[("STUDIO", "RIGHTS_OPERATOR_STATEMENT")] == ""
    assert vals[("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE")] == "true"
    assert vals[("SETTINGS", "DRY_RUN")] == "false"
    assert vals[("SAFETY", "ENABLED")] == "false"
    assert vals[("SAFETY", "ALLOW_PUBLIC_AUTOMATION")] == "false"


def test_ui_roundtrip_preserves_other_sections(tmp_path):
    cfg_file = tmp_path / "config.ini"
    # ملف موجود مسبقاً بمفاتيح لا تمسها الواجهة
    cfg_file.write_text(
        "[SETTINGS]\nVIDEO_DIRECTORY = D:/videos\nCLIENT_SECRETS_FILE = s.json\n",
        encoding="utf-8")

    values = pro.load_ui_values(str(cfg_file))
    values[("WATCH", "INTERVAL_MINUTES")] = "30"
    values[("STUDIO", "TITLE_PREFIX")] = "الوطنية"
    values[("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE")] = "false"
    values[("STUDIO", "PRESERVE_FACEBOOK_TITLE")] = "false"
    values[("AI", "STYLE")] = "إخباري رسمي"
    values[("AI", "TRANSCRIBE_LOCAL")] = "true"
    values[("AI", "TRANSCRIPTION_MODEL")] = "small"
    values[("SETTINGS", "DRY_RUN")] = "true"
    values[("SAFETY", "ENABLED")] = "true"
    values[("SAFETY", "ALLOW_PUBLIC_AUTOMATION")] = "true"
    values[("SAFETY", "CUTTER_ROOT")] = "D:/Cutter"
    pro.save_ui_values(values, str(cfg_file))

    again = pro.load_ui_values(str(cfg_file))
    assert again[("WATCH", "INTERVAL_MINUTES")] == "30"
    assert again[("STUDIO", "TITLE_PREFIX")] == "الوطنية"
    assert again[("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE")] == "false"
    assert again[("STUDIO", "PRESERVE_FACEBOOK_TITLE")] == "false"
    assert again[("AI", "STYLE")] == "إخباري رسمي"
    assert again[("AI", "TRANSCRIBE_LOCAL")] == "true"
    assert again[("AI", "TRANSCRIPTION_MODEL")] == "small"
    assert again[("SETTINGS", "DRY_RUN")] == "true"
    assert again[("SAFETY", "ENABLED")] == "true"
    assert again[("SAFETY", "ALLOW_PUBLIC_AUTOMATION")] == "true"
    assert again[("SAFETY", "CUTTER_ROOT")] == "D:/Cutter"
    # المسارات الأصلية لم تُمَس
    cfg = configparser.ConfigParser()
    cfg.read(str(cfg_file), encoding="utf-8")
    assert cfg.get("SETTINGS", "VIDEO_DIRECTORY") == "D:/videos"
    assert cfg.get("SETTINGS", "CLIENT_SECRETS_FILE") == "s.json"


def test_default_ui_config_is_app_relative_outside_project_directory(monkeypatch, tmp_path):
    app_dir = tmp_path / "application"
    working_dir = tmp_path / "unrelated"
    app_dir.mkdir()
    working_dir.mkdir()
    app_config = app_dir / "config.ini"
    app_config.write_text(
        "[SETTINGS]\nPRIVACY = unlisted\n"
        "[STUDIO]\nRIGHTS_GATE_MODE = operator_confirmed\n"
        "RIGHTS_GATE_MIGRATION = operator-confirmed-v2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pro, "APP_DIRECTORY", str(app_dir))
    monkeypatch.chdir(working_dir)

    values = pro.load_ui_values()
    assert values[("SETTINGS", "PRIVACY")] == "unlisted"
    values[("SETTINGS", "PRIVACY")] = "private"
    assert pro.save_ui_values(values) is True

    saved = configparser.ConfigParser(interpolation=None)
    saved.read(app_config, encoding="utf-8")
    assert saved.get("SETTINGS", "PRIVACY") == "private"
    assert not (working_dir / "config.ini").exists()


def test_bool_values_roundtrip(tmp_path):
    cfg_file = tmp_path / "config.ini"
    values = pro.load_ui_values(str(cfg_file))
    values[("WATCH", "AUTO_START")] = "true"
    values[("STUDIO", "LOGO_ENABLED")] = "false"
    pro.save_ui_values(values, str(cfg_file))
    again = pro.load_ui_values(str(cfg_file))
    assert again[("WATCH", "AUTO_START")] == "true"
    assert again[("STUDIO", "LOGO_ENABLED")] == "false"


def test_hashtags_in_text_values_roundtrip_without_truncation(tmp_path):
    cfg_file = tmp_path / "config.ini"
    values = pro.load_ui_values(str(cfg_file))
    values[("STUDIO", "EXTRA_DESCRIPTION")] = "تابعنا #shorts #ريلز — وفر 50% اليوم"
    values[("STUDIO", "TAGS")] = "Facebook,#Shorts,ريلز"

    pro.save_ui_values(values, str(cfg_file))

    again = pro.load_ui_values(str(cfg_file))
    assert again[("STUDIO", "EXTRA_DESCRIPTION")] == "تابعنا #shorts #ريلز — وفر 50% اليوم"
    assert again[("STUDIO", "TAGS")] == "Facebook,#Shorts,ريلز"


def test_interrupted_config_write_keeps_previous_file_intact(tmp_path):
    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text("[SETTINGS]\nPRIVACY = private\n", encoding="utf-8")

    class BrokenConfig:
        def write(self, stream):
            stream.write("[SETTINGS]\nPRIVACY =")
            raise OSError("simulated disk failure")

    try:
        pro._write_config_atomically(BrokenConfig(), cfg_file)
    except OSError as error:
        assert "simulated disk failure" in str(error)
    else:
        raise AssertionError("the simulated write failure must propagate")

    assert cfg_file.read_text(encoding="utf-8") == "[SETTINGS]\nPRIVACY = private\n"
    assert not list(tmp_path.glob(".config.ini.*.tmp"))


def test_ui_keys_complete_for_worker():
    """كل إعداد يعمل به العامل له مفتاح محفوظ — لا مفاجآت بعد إعادة الفتح."""
    saved = {(s, k) for (s, k) in pro.load_ui_values()}
    # (قسم، مفتاح) لكل إعداد يستخدمه العامل
    required = {
        ("SETTINGS", "ACCOUNT"), ("SETTINGS", "PRIVACY"), ("SETTINGS", "HEADLESS"),
        ("SETTINGS", "DELETE_AFTER_UPLOAD"), ("SETTINGS", "REMOVE_TAGS"),
        ("SETTINGS", "BETWEEN_VIDEOS"), ("SETTINGS", "DAILY_UPLOAD_LIMIT"),
        ("SETTINGS", "MAX_ATTEMPTS"), ("SETTINGS", "MIN_DISK_GB"),
        ("SETTINGS", "DRY_RUN"),
        ("WATCH", "INTERVAL_MINUTES"), ("WATCH", "FAST_SCRAPE"), ("WATCH", "AUTO_START"),
        ("STUDIO", "TITLE_PREFIX"), ("STUDIO", "TITLE_SUFFIX"), ("STUDIO", "CATEGORY"),
        ("STUDIO", "TITLE_OPTIMIZE"), ("STUDIO", "AUTO_SHORTS"),
        ("STUDIO", "PRESERVE_FACEBOOK_TITLE"),
        ("STUDIO", "OMIT_SOURCE_PAGE_NAME_FROM_TITLE"), ("STUDIO", "AUTO_HASHTAGS"),
        ("STUDIO", "DEDUPE_CONTENT"), ("STUDIO", "CREDIT_LINE"),
        ("STUDIO", "EXTRA_DESCRIPTION"), ("STUDIO", "TAGS"),
        ("STUDIO", "LOGO_ENABLED"), ("STUDIO", "LOGO_PATH"), ("STUDIO", "LOGO_POSITION"),
        ("STUDIO", "LOGO_SCALE"), ("STUDIO", "LOGO_OPACITY"),
        ("STUDIO", "LOGO_COVER_WIDTH"), ("STUDIO", "LOGO_COVER_OLD"),
        ("STUDIO", "LOGO_SECONDARY"), ("STUDIO", "LOGO_SECONDARY_COVER_OLD"),
        ("STUDIO", "PLAYLIST_ID"), ("STUDIO", "REVIEW_BEFORE_UPLOAD"),
        ("STUDIO", "THUMBNAIL_ENABLED"),
        ("STUDIO", "SHORTS_CUT"),
        ("STUDIO", "MIN_DURATION"), ("STUDIO", "MAX_DURATION"),
        ("STUDIO", "SCHEDULE"), ("STUDIO", "SCHEDULE_MIN"), ("STUDIO", "SCHEDULE_MAX"),
        ("STUDIO", "COOKIES_FILE"), ("STUDIO", "COOKIES_ENABLED"),
        ("STUDIO", "TELEGRAM_TOKEN"), ("STUDIO", "TELEGRAM_CHAT_ID"),
        ("STUDIO", "TELEGRAM_ERRORS"), ("STUDIO", "PG_SCROLL"), ("STUDIO", "PG_LIMIT"),
        ("AI", "ENABLED"), ("AI", "MODEL"), ("AI", "STYLE"),
        ("AI", "TRANSCRIBE_LOCAL"), ("AI", "TRANSCRIPTION_MODEL"),
        ("SAFETY", "ENABLED"), ("SAFETY", "CUTTER_ROOT"), ("SAFETY", "CUTTER_PYTHON"),
        ("SAFETY", "ALLOW_PUBLIC_AUTOMATION"),
    }
    missing = required - saved
    assert not missing, f"مفاتيح غير محفوظة: {missing}"
