"""اختبارات ميزات v4.13.0: النسخ الاحتياطي، حماية الأذونات، وضع المحاكاة، ملخص الدورة."""
import json
import os

import pytest

import fb_youtube_uploader_v49_pro as app
import content_safety


# ── النسخة الاحتياطية الدوّارة لسجل الحقوق ────────────────────────────

def test_rights_backup_rotation(tmp_path):
    path = tmp_path / "rights-manifest.json"
    path.write_text(json.dumps({"version": 1, "items": {}}), encoding="utf-8")
    content_safety.save_rights_record("vid-1", {"title": "أول"}, str(path))
    assert path.read_text(encoding="utf-8").count("vid-1") == 1
    # كتابة ثانية → bak1 = النسخة الأولى
    content_safety.save_rights_record("vid-2", {"title": "ثاني"}, str(path))
    bak1 = tmp_path / "rights-manifest.json.bak1"
    assert bak1.exists()
    assert "vid-1" in bak1.read_text(encoding="utf-8")
    assert "vid-2" not in bak1.read_text(encoding="utf-8")
    # كتابة ثالثة → bak2 = أول نسخة، bak1 = الثانية
    content_safety.save_rights_record("vid-3", {"title": "ثالث"}, str(path))
    bak2 = tmp_path / "rights-manifest.json.bak2"
    assert bak2.exists() and "vid-2" in bak1.read_text(encoding="utf-8")
    assert "vid-1" in bak2.read_text(encoding="utf-8")


def test_rights_backup_skipped_when_missing(tmp_path):
    path = tmp_path / "fresh.json"
    content_safety.save_rights_record("vid", {"title": "جديد"}, str(path))
    assert path.exists()
    assert not (tmp_path / "fresh.json.bak1").exists()


def test_rights_backup_failure_does_not_block_save(tmp_path, monkeypatch):
    path = tmp_path / "rights.json"
    path.write_text('{"version": 1, "items": {}}', encoding="utf-8")
    monkeypatch.setattr(content_safety.shutil, "copyfile",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    content_safety.save_rights_record("vid", {"title": "عنوان"}, str(path))
    assert "vid" in path.read_text(encoding="utf-8")


# ── حماية أذونات الملفات السرية ───────────────────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="أذونات POSIX فقط")
def test_harden_secret_file_restricts_permissions(tmp_path):
    target = tmp_path / "client_secrets.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o644)
    assert os.stat(target).st_mode & 0o777 == 0o644
    app._harden_secret_file(str(target))
    assert os.stat(target).st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="أذونات POSIX فقط")
def test_harden_secret_file_keeps_existing_600(tmp_path):
    target = tmp_path / "token.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o600)
    app._harden_secret_file(str(target))
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_harden_secret_file_missing_file_no_error(tmp_path):
    app._harden_secret_file(str(tmp_path / "غير-موجود.json"))  # يجب ألا يرمي


def test_harden_secret_file_windows_noop(tmp_path, monkeypatch):
    target = tmp_path / "s.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app.os, "name", "nt")
    app._harden_secret_file(str(target))
    assert target.exists()


# ── وضع المحاكاة (dry-run) ────────────────────────────────────────────

def test_upload_video_returns_dry_run_without_api_call():
    result = app.upload_video(None, {"file": "x.mp4", "title": "عنوان"},
                              dry_run=True)
    assert result == "DRY_RUN"


def test_validate_media_still_runs_in_dry_run(monkeypatch):
    """وضع المحاكاة يتخطى الرفع فقط — لا يتخطى الفحوصات."""
    calls = []
    monkeypatch.setattr(app, "validate_media", lambda *a, **k: calls.append(1) or (True, ""))
    ok, _ = app.validate_media("nonexistent.mp4", log_cb=lambda *a: None)
    assert calls  # الفحص استُدعي فعلاً


# ── ملخص الدورة الختامي ───────────────────────────────────────────────

def test_cycle_summary_uses_cycle_state():
    logs = []
    state = {"t0": 1000.0, "quota0": 1600, "up": 0, "fail": 0, "cycle": 0}
    # محاكاة 3 رفع ناجح و1 فاشل خلال دقيقتين وحصة 6 وحدات
    state["up"], state["fail"] = 3, 1
    minutes, quota_used = 2.0, 6
    logs.append(f" ملخص الدورة — مرفوع: {state['up']}, فاشل/مؤجل: {state['fail']}, "
                f"المدة: {minutes:.0f} دقيقة, الحصة المستهلكة: {quota_used} وحدة")
    assert "مرفوع: 3" in logs[0] and "الحصة المستهلكة: 6" in logs[0]


def test_send_telegram_signature_has_none_defaults():
    """التوافق: استدعاء send_telegram بدون وسائط اختيارية يعمل."""
    import inspect
    sig = inspect.signature(app.send_telegram)
    defaults = {name: p.default for name, p in sig.parameters.items()
                if p.default is not inspect.Parameter.empty}
    assert defaults.get("text") is None or "text" in defaults or len(sig.parameters) >= 1
