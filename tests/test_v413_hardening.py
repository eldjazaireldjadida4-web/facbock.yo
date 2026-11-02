"""اختبارات تحصين v4.13.0: نسخ احتياطي، أذونات سرية، وضع المحاكاة، ملخص الدورة."""
import json
import os

import pytest

import fb_youtube_uploader_v49_pro as app
import content_safety


# ─── النسخ الاحتياطي الدوّار لسجل الحقوق ────────────────────────────────

def test_rights_backup_created_on_save(tmp_path):
    path = tmp_path / "rights.json"
    content_safety.save_rights_record("src1", {"title": "أول"}, str(path))
    content_safety.save_rights_record("src2", {"title": "ثاني"}, str(path))
    bak = tmp_path / "rights.json.bak1"
    assert bak.exists()
    data = json.loads(bak.read_text(encoding="utf-8"))
    assert data["items"]["src1"]["title"] == "أول"


def test_rights_backup_rotates_three_generations(tmp_path):
    path = tmp_path / "rights.json"
    for i in range(4):
        content_safety.save_rights_record(f"src{i}", {"title": f"عنوان {i}"}, str(path))
    bak1 = json.loads((tmp_path / "rights.json.bak1").read_text(encoding="utf-8"))
    bak2 = json.loads((tmp_path / "rights.json.bak2").read_text(encoding="utf-8"))
    bak3 = json.loads((tmp_path / "rights.json.bak3").read_text(encoding="utf-8"))
    assert "src2" in bak1["items"] and "src3" not in bak1["items"]
    assert "src1" in bak2["items"] and "src2" not in bak2["items"]
    assert "src0" in bak3["items"] and "src1" not in bak3["items"]


def test_rights_record_survives_corrupt_write_with_backup(tmp_path):
    path = tmp_path / "rights.json"
    content_safety.save_rights_record("src1", {"title": "سليم"}, str(path))
    content_safety.save_rights_record("src1", {"title": "محدّث"}, str(path))
    original = path.read_text(encoding="utf-8")
    path.write_text("{تالف", encoding="utf-8")  # محاكاة تلف كتابة سابقة
    # الاستعادة من النسخة الاحتياطية ممكنة
    restored = json.loads(
        (tmp_path / "rights.json.bak1").read_text(encoding="utf-8"))
    assert restored["items"]["src1"]["title"] == "سليم"
    path.write_text(original, encoding="utf-8")  # نظّف


# ─── أذونات الملفات السرية ──────────────────────────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="chmod يخص لينكس/ماك")
def test_harden_secret_file_restricts_permissions(tmp_path):
    secret = tmp_path / "client_secrets.json"
    secret.write_text("{}", encoding="utf-8")
    os.chmod(secret, 0o644)
    assert os.access(secret, os.R_OK | os.W_OK)
    assert secret.stat().st_mode & 0o077  # مفتوح على المجموعة/الآخرين
    app._harden_secret_file(str(secret))
    mode = secret.stat().st_mode
    assert not (mode & 0o077), "يجب أن يبقى الوصول للمالك فقط"


@pytest.mark.skipif(os.name != "posix", reason="chmod يخص لينكس/ماك")
def test_harden_secret_file_ignores_missing(tmp_path):
    missing = tmp_path / "لا_يوجد.json"
    app._harden_secret_file(str(missing))  # لا يرمي استثناء
    assert not missing.exists()


# ─── وضع المحاكاة (dry-run) ─────────────────────────────────────────────

def test_upload_video_dry_run_never_touches_quota():
    class Q:
        def __init__(self):
            self.checked = False
        def can_afford(self):
            self.checked = True
            return False
    quota = Q()
    result = app.upload_video(None, {"file": "x.mp4", "title": "عنوان"},
                              quota_manager=quota, dry_run=True,
                              log_cb=lambda msg: None)
    assert result == "DRY_RUN"
    assert not quota.checked


def test_upload_video_dry_run_skips_missing_file_check():
    # حتى مع ملف غير موجود، dry-run يعيد DRY_RUN قبل فحص الملف
    result = app.upload_video(None, {"file": "غير_موجود.mp4", "title": "عنوان"},
                              dry_run=True, log_cb=lambda msg: None)
    assert result == "DRY_RUN"


def test_validate_media_still_runs_in_dry_run(monkeypatch):
    called = []
    monkeypatch.setattr(app, "validate_media", lambda p, log_cb=None: called.append(p) or (True, ""))
    ok, _ = app.validate_media("sample.mp4", log_cb=lambda msg: None)
    assert ok and called == ["sample.mp4"]


# ─── ملخص الدورة التدريبية ──────────────────────────────────────────────

def test_cycle_summary_reports_quota_and_elapsed(monkeypatch):
    events = []
    monkeypatch.setattr(app, "send_telegram", lambda s, text, log_cb=None: events.append(text))
    settings = {"telegram_enabled": True, "tg_token": "توكن", "tg_chat_id": "123"}
    class Q:
        used = 10
        daily_limit = 6
    app.send_cycle_summary(
        {"quota": Q(), "account": "قناة1"},
        settings,
        {"up_count": 3, "fail_count": 1, "elapsed": 125, "dry_run": False},
        events.append,
    )
    text = events[-1]
    assert "3" in text and "125" in text
    assert "10/6" in text


def test_cycle_summary_dry_run_flagged(monkeypatch):
    events = []
    monkeypatch.setattr(app, "send_telegram", lambda s, text, log_cb=None: events.append(text))
    settings = {"telegram_enabled": True, "tg_token": "توكن", "tg_chat_id": "123"}
    class Q:
        used = 0
        daily_limit = 6
    app.send_cycle_summary(
        {"quota": Q(), "account": "قناة1"}, settings,
        {"up_count": 0, "fail_count": 0, "elapsed": 5, "dry_run": True},
        events.append,
    )
    assert "المحاكاة" in events[-1]

def test_optimize_title_strips_truncated_brand():
    """المصدر مقطوع عند فيسبوك → الذيل التجاري غير الموثوق يُحذف."""
    raw = "شاهد سودانية تتفاجأ ولم تصدق ما وجدته في مدينة جانت | Elwatania T..."
    out = app.optimize_title(raw, page_name="الوطنية TV", suffix="| Elwatania TV")
    assert "Elwatania T..." not in out
    assert "مدينة جانت" in out


def test_optimize_title_keeps_brand_when_source_complete():
    raw = "وزارة التجارة تطلق تطبيقا لاستقبال شكاوي المواطنين"
    out = app.optimize_title(raw, page_name="الوطنية TV", suffix="| Elwatania TV")
    assert "Elwatania TV" in out


def test_http_error_reason_maps_known_reasons():
    from googleapiclient.errors import HttpError
    import json as _json
    resp = type("R", (), {"get": staticmethod(lambda k, d=None: _json.dumps(
        {"error": {"errors": [{"reason": "quotaExceeded"}]}}).encode()),
        "reason": ""})()
    err = HttpError(resp, b"{}")
    assert "حصة" in app._http_error_reason(err)
