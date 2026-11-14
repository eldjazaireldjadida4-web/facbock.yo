"""انحدار 4.49.3 — عناصر تختفي من قائمة الإعادة، وحصة تُحرق بلا رفع، وحالة بلا مخرج.

المحور الأول: فيديو رفضه YouTube بعد القبول كان يُحذف من قائمة الإعادة في
الدورة التالية لأنه ما زال في uploaded_ids.txt. والمحور الثاني: تقليم العمر
كان يمحو عناصر تنتظر قراراً بشرياً، والحصة تُخصم قبل إرسال أي بايت.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import control_plane
import fb_youtube_uploader_v49_pro as app


# ─────────────────────────── إيصال مرفوض ≠ مرفوع ───────────────────────────
def test_rejected_receipt_does_not_count_as_uploaded() -> None:
    receipts = {
        "keep": {"source_id": "keep", "status": "uploaded"},
        "rejected": {"source_id": "rejected", "status": "rejected"},
    }

    active, rejected = app._active_uploaded_ids(receipts, {"keep", "rejected", "legacy"})

    assert active == {"keep", "legacy"}
    assert rejected == {"rejected"}


# ─────────────────────────── تقليم العمر ───────────────────────────
@pytest.mark.parametrize(
    "status",
    [
        app.UPLOAD_RESULT_UNKNOWN,
        app.UPLOAD_REJECTED,
        "manual_retry_approved",
        "invalid_title",
        "content_safety_review_required",
        "publication_approval_required",
    ],
)
def test_prune_keeps_items_waiting_for_a_human(status: str) -> None:
    old = time.time() - 30 * 86400
    items = [{"id": "x", "url": "https://www.facebook.com/reel/1", "last_error": status, "failed_at": old}]

    assert app.prune_failed_queue(items, 14) == items


def test_prune_still_drops_plain_stale_failures() -> None:
    old = time.time() - 30 * 86400
    items = [{"id": "x", "url": "https://www.facebook.com/reel/1", "last_error": "download_failed", "failed_at": old}]

    assert app.prune_failed_queue(items, 14) == []


def test_approved_manual_retry_refreshes_the_clock() -> None:
    old = time.time() - 30 * 86400
    items = [{
        "id": "x",
        "url": "https://www.facebook.com/reel/1",
        "last_error": app.UPLOAD_RESULT_UNKNOWN,
        "failed_at": old,
    }]

    updated, decision = control_plane.reconcile_upload_result(items, "x", video_exists=False)

    assert decision == "retry"
    assert updated[0]["last_error"] == "manual_retry_approved"
    assert updated[0]["failed_at"] > old
    # وبالتالي يبقى بعد التقليم
    assert app.prune_failed_queue(updated, 14) == updated


# ─────────────────────────── الفشل لا يمحو بيانات العنصر ───────────────────────────
def _bare_app():
    instance = app.App.__new__(app.App)
    instance._log = lambda *_args, **_kwargs: None
    return instance


def test_failure_keeps_upload_metadata_and_refreshes_clock() -> None:
    instance = _bare_app()
    old = time.time() - 30 * 86400
    item = {
        "id": "reel-1",
        "url": "https://www.facebook.com/reel/1",
        "page": "Page",
        "attempts": 0,
        "failed_at": old,
        "upload_title": "عنوان محفوظ",
        "upload_privacy": "public",
        "scheduled_for": "2026-12-01T10:00:00",
    }
    queue = [dict(item)]

    instance._handle_failure(
        queue, set(), item, "فشل الشبكة", {"max_attempts": 3}, count_attempt=True
    )

    entry = next(row for row in queue if row["id"] == "reel-1")
    assert entry["upload_title"] == "عنوان محفوظ"
    assert entry["upload_privacy"] == "public"
    assert entry["scheduled_for"] == "2026-12-01T10:00:00"
    assert entry["attempts"] == 1
    assert entry["failed_at"] > old
    assert entry["last_error"] == "فشل الشبكة"


# ─────────────────────────── الحصة ───────────────────────────
class _Quota:
    def __init__(self, on_register=None):
        self.used = 1
        self.refunds = 0
        self.on_register = on_register

    def can_afford(self):
        return True

    def register(self):
        self.used += 1
        if self.on_register is not None:
            self.on_register()

    def refund(self):
        self.refunds += 1
        self.used -= 1

    def mark_exhausted(self):
        self.used = 99


class _BlockedRequest:
    def next_chunk(self):
        raise AssertionError("no chunk should be sent after a stop request")


class _UnusedYouTube:
    def videos(self):
        return self

    def insert(self, **_kwargs):
        return _BlockedRequest()

    def list(self, **_kwargs):
        raise AssertionError("verification must not run when nothing was uploaded")


def test_stopping_before_the_first_chunk_refunds_the_quota_slot(tmp_path: Path) -> None:
    import threading

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    stop_event = threading.Event()
    # الإيقاف يصل بعد حجز الحصة وقبل إرسال أول جزء: النافذة التي كانت تحرق مقعداً.
    quota = _Quota(on_register=stop_event.set)

    result = app.upload_video(
        _UnusedYouTube(),
        {"file": str(video), "title": "عنوان اختبار واضح", "desc": "description"},
        quota_manager=quota,
        stop_event=stop_event,
    )

    assert result == app.UPLOAD_NOT_STARTED
    assert quota.refunds == 1
    assert quota.used == 1


def test_quota_state_is_not_truncated_on_load(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "state.json"
    today = app.pt_now().date().isoformat()
    state_file.write_text(
        json.dumps({"youtube_uploads_date": today, "youtube_uploads_used": 20}),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "STATE_FILE", str(state_file))

    quota = app.QuotaManager(daily_limit=6)

    # العداد يُقرأ كما هو؛ القصّ في can_afford لا في الحفظ
    assert quota.used == 20
    assert quota.can_afford() is False


def test_legacy_quota_migration_does_not_round_up(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "state.json"
    today = app.pt_now().date().isoformat()
    state_file.write_text(
        json.dumps({"quota_date": today, "quota_used": 500}),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "STATE_FILE", str(state_file))

    quota = app.QuotaManager(daily_limit=6)

    # 500 وحدة = صفر رفع كامل، لا رفع واحد
    assert quota.used == 0


# ─────────────────────────── إصلاح الحالة ───────────────────────────
def test_repair_quarantines_corrupt_ledgers(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "failed_queue.json"
    ledger.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(app, "FAILED_QUEUE_FILE", str(ledger))
    for name in ("UPLOAD_RECEIPTS_FILE", "PAGES_FILE", "ACCOUNTS_FILE", "STATE_FILE", "CHANNEL_INDEX_FILE"):
        monkeypatch.setattr(app, name, str(tmp_path / f"{name.lower()}.json"))

    actions = app.repair_state_files()

    quarantined = [action for action in actions if action["file"] == "failed_queue.json"]
    assert quarantined and quarantined[0]["action"] == "quarantine"
    assert not ledger.exists()
    copies = list(tmp_path.glob("failed_queue.json.corrupt-*"))
    assert copies and copies[0].read_text(encoding="utf-8") == "{broken"


def test_repair_dry_run_moves_nothing(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "pages.json"
    ledger.write_text("not json", encoding="utf-8")
    for name, path in (
        ("PAGES_FILE", ledger),
        ("UPLOAD_RECEIPTS_FILE", tmp_path / "r.json"),
        ("FAILED_QUEUE_FILE", tmp_path / "q.json"),
        ("ACCOUNTS_FILE", tmp_path / "a.json"),
        ("STATE_FILE", tmp_path / "s.json"),
        ("CHANNEL_INDEX_FILE", tmp_path / "c.json"),
    ):
        monkeypatch.setattr(app, name, str(path))

    actions = app.repair_state_files(dry_run=True)

    assert any(action["file"] == "pages.json" for action in actions)
    assert ledger.exists()


def test_repair_reports_a_valid_backup_instead_of_quarantining(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "pages.json"
    ledger.write_text("{broken", encoding="utf-8")
    (tmp_path / "pages.json.bak1").write_text("[]", encoding="utf-8")
    for name, path in (
        ("PAGES_FILE", ledger),
        ("UPLOAD_RECEIPTS_FILE", tmp_path / "r.json"),
        ("FAILED_QUEUE_FILE", tmp_path / "q.json"),
        ("ACCOUNTS_FILE", tmp_path / "a.json"),
        ("STATE_FILE", tmp_path / "s.json"),
        ("CHANNEL_INDEX_FILE", tmp_path / "c.json"),
    ):
        monkeypatch.setattr(app, name, str(path))

    actions = app.repair_state_files()

    assert actions == [{"file": "pages.json", "action": "restore_from_backup"}]
    assert ledger.exists()
