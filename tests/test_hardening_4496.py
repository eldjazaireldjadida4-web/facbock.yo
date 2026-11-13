"""انحدار 4.49.6 — تعديل قائمة الانتظار من نسختين، وأثر رفع Shorts.

قفل التشغيل كان يُحمي خيط الواجهة فقط: نسخة واجهة ثانية أو تشغيل CLI كان
يستطيع الكتابة على `failed_queue.json` من لقطة أقدم، فيختفي مدخل رفع قيد
التنفيذ أو يُلغى قرار اعتمده المشغّل.
"""
from __future__ import annotations

import pytest

import fb_youtube_uploader_v49_pro as app


class _FakeLock:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.released = False

    def acquire(self):
        if self.fail:
            raise app.AutomationAlreadyRunning("busy")
        return self

    def release(self):
        self.released = True


def _bare_app():
    instance = app.App.__new__(app.App)
    instance.root = object()
    instance._watching = False
    instance._thread = None
    return instance


def _capture_messages(monkeypatch):
    seen = []
    monkeypatch.setattr(app.messagebox, "showwarning", lambda title, text, **kw: seen.append(("warn", title, text)))
    monkeypatch.setattr(app.messagebox, "showerror", lambda title, text, **kw: seen.append(("error", title, text)))
    return seen


def test_queue_edit_refuses_when_another_run_holds_the_lock(monkeypatch) -> None:
    messages = _capture_messages(monkeypatch)
    monkeypatch.setattr(app, "AutomationRunLock", lambda *_a, **_k: _FakeLock(fail=True))
    instance = _bare_app()
    body_ran = []
    monkeypatch.setattr(instance, "_reconcile_unknown_upload_locked", lambda: body_ran.append(True))

    instance._reconcile_unknown_upload()

    assert body_ran == []
    assert messages and messages[0][0] == "warn"
    assert "تشغيل آخر" in messages[0][2]


def test_queue_edit_acquires_and_always_releases(monkeypatch) -> None:
    lock = _FakeLock()
    monkeypatch.setattr(app, "AutomationRunLock", lambda *_a, **_k: lock)
    instance = _bare_app()

    def boom():
        raise RuntimeError("dialog failed")

    monkeypatch.setattr(instance, "_release_policy_holds_locked", boom)

    with pytest.raises(RuntimeError):
        instance._release_policy_holds()

    assert lock.released is True


def test_shorts_records_intent_before_the_first_byte(tmp_path, monkeypatch) -> None:
    """record("shorts_upload_started") يجب أن يسبق أي محاولة رفع."""
    import queue
    import threading
    from types import SimpleNamespace

    history = []
    saved_rights = []
    saved_receipts = []
    statuses_at_upload = []
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    rights_record = {
        "rights_basis": "owned",
        "evidence_reference": "",
        "original_contribution": "مساهمة أصلية كافية للمراجعة في هذا الاختبار.",
        "duplicate_review_note": "",
        "source_url": "https://www.facebook.com/reel/1",
        "attested": True,
    }

    worker = app.App.__new__(app.App)
    worker._stop = threading.Event()
    worker._uiq = queue.Queue()
    worker._log = lambda *_a, **_k: None
    worker.stats = {"total_uploaded": 0, "total_failed": 0, "daily": {}}

    settings = {
        "dry_run": False,
        "review_before_upload": True,
        "allow_public_automation": False,
        "rights_manifest_file": str(tmp_path / "rights.json"),
        "privacy": "private",
        "schedule_enabled": False,
        "tags": ["Facebook"],
        "category": "22",
        "content_review_enabled": False,
        "thumbnail_enabled": False,
    }
    quota = SimpleNamespace(used=1, daily_limit=8, mark_exhausted=lambda: None)

    monkeypatch.setattr(app, "create_thumbnail_preview", lambda *_a: (None, None))
    monkeypatch.setattr(app, "get_rights_record", lambda *_a: None)
    monkeypatch.setattr(app, "save_rights_record", lambda sid, rec, path: saved_rights.append(sid))
    monkeypatch.setattr(app, "_audio_rms_per_second", lambda _s: {0: -2})
    monkeypatch.setattr(app, "find_hot_segment", lambda *_a, **_k: (0, 30))
    monkeypatch.setattr(app, "cut_shorts_clip", lambda *_a, **_k: str(clip))
    monkeypatch.setattr(app, "validate_media", lambda *_a, **_k: (True, {"duration": 30}))
    monkeypatch.setattr(app, "wait_for_review_decision", lambda *_a: {
        "action": "approve",
        "title": "Reviewed Shorts",
        "description": "desc",
        "privacy": "unlisted",
        "rights_record": rights_record,
        "thumbnail_path": None,
    })
    monkeypatch.setattr(app, "append_history", lambda rec: history.append(rec) or True)
    monkeypatch.setattr(app, "persist_upload_receipt", lambda sid, vid, **kw: saved_receipts.append(sid))
    monkeypatch.setattr(app, "save_uploaded_id", lambda _sid: None)
    monkeypatch.setattr(app, "send_telegram", lambda *_a, **_k: None)

    def fake_upload(_youtube, _data, *_args, **kwargs):
        statuses_at_upload.append(history[-1]["status"] if history else "")
        kwargs["on_confirmed"]("abcdefghijk")
        return "abcdefghijk"

    monkeypatch.setattr(app, "upload_video", fake_upload)

    app.App._make_shorts_from(
        worker,
        {"yt": object(), "quota": quota, "account": "test-channel"},
        settings,
        {"id": "reel-1", "url": "https://www.facebook.com/reel/1", "page": "Page"},
        {"title": "Original", "desc": "d", "duration": 120, "file": str(source), "duplicate_matches": []},
        set(),
    )

    assert statuses_at_upload == ["shorts_upload_started"]
    statuses = [record.get("status") for record in history]
    assert statuses.index("shorts_upload_started") < statuses.index("uploaded")
    assert saved_rights == ["reel-1_shorts"]
    assert saved_receipts == ["reel-1_shorts"]
