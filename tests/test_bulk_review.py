# -*- coding: utf-8 -*-
"""المراجعة السريعة بالجملة (4.49.31): تحرير محدد من قائمة الانتظار، وتصفية،
واختصارات لوحة المفاتيح في حوار المراجعة، وتذكّر نموذج الحقوق داخل الدورة.

قاعدة السلامة: إجراءات المراجعة السريعة فرز فقط — إعادة الإدراج تعيد العنصر
إلى قائمة العمل ليفحص مجدداً، ولا موافقة على الرفع تحدث خارج حوار المراجعة
وبوابة الحقوق. الإقرار (attested) لا يُورَّث أبداً بين الفيديوهات.
"""
from __future__ import annotations

import fb_youtube_uploader_v49_pro as app
from control_plane import (
    QUEUE_REASON_LABELS,
    filter_bulk_review_items,
    queue_reason_label,
    release_policy_holds_for_ids,
)


def _items():
    return [
        {"id": "a", "page": "صفحة الأخبار", "upload_title": "خبر عاجل", "last_error": "rights_review_required", "attempts": 1},
        {"id": "b", "page": "صفحة الرياضة", "upload_title": "هدف رائع", "last_error": "content_safety_review_required", "attempts": 2},
        {"id": "c", "page": "صفحة الأخبار", "upload_title": "تقرير الطقس", "last_error": "upload_result_unknown", "attempts": 0},
        {"id": "d", "page": "صفحة الطبخ", "upload_title": "وصفة سريعة", "last_error": "abandoned", "attempts": 3},
    ]


def test_release_policy_holds_for_ids_only_selected():
    released, count = release_policy_holds_for_ids(_items(), ["a", "d", "missing"])
    assert count == 2
    by_id = {item["id"]: item for item in released}
    assert by_id["a"]["last_error"] == ""
    assert by_id["d"]["last_error"] == ""
    # غير المحدد يبقى كما هو.
    assert by_id["b"]["last_error"] == "content_safety_review_required"
    # خطأ غير قابل للتحرير لا يُحرَّر حتى لو حُدّد.
    released, count = release_policy_holds_for_ids(_items(), ["c"])
    assert count == 0
    assert released[2]["last_error"] == "upload_result_unknown"


def test_release_policy_holds_for_ids_empty_selection():
    released, count = release_policy_holds_for_ids(_items(), [])
    assert count == 0
    assert [item["last_error"] for item in released] == [
        item["last_error"] for item in _items()
    ]


def test_filter_bulk_review_items_by_reason_and_query():
    assert [item["id"] for item in filter_bulk_review_items(_items())] == ["a", "b", "c", "d"]
    assert [item["id"] for item in filter_bulk_review_items(_items(), reason="rights_review_required")] == ["a"]
    assert [item["id"] for item in filter_bulk_review_items(_items(), page_query="الأخبار")] == ["a", "c"]
    assert [item["id"] for item in filter_bulk_review_items(_items(), page_query="هدف")] == ["b"]
    assert [item["id"] for item in filter_bulk_review_items(_items(), page_query="b")] == ["b"]
    assert filter_bulk_review_items(_items(), reason="rights_review_required", page_query="الرياضة") == []
    # عناصر غير Mapping تُتخطى.
    assert filter_bulk_review_items([None, "x", *_items()]) == _items()


def test_queue_reason_label_known_empty_unknown():
    assert queue_reason_label("rights_review_required") == QUEUE_REASON_LABELS["rights_review_required"]
    assert queue_reason_label("") == "جاهز لإعادة الفحص"
    assert queue_reason_label(None) == "جاهز لإعادة الفحص"
    assert queue_reason_label("some_new_error") == "some_new_error"


class _FakeWidget:
    def __init__(self, cls):
        self._cls = cls

    def winfo_class(self):
        return self._cls


class _ExplodingWidget:
    def winfo_class(self):
        raise RuntimeError("dead widget")


def test_shortcut_typing_focus_blocks_text_fields_only():
    assert app._shortcut_typing_focus(None) is False
    assert app._shortcut_typing_focus(_FakeWidget("Entry")) is True
    assert app._shortcut_typing_focus(_FakeWidget("Text")) is True
    assert app._shortcut_typing_focus(_FakeWidget("TCombobox")) is True
    assert app._shortcut_typing_focus(_FakeWidget("Button")) is False
    assert app._shortcut_typing_focus(_FakeWidget("Treeview")) is False
    assert app._shortcut_typing_focus(_ExplodingWidget()) is False


def test_initial_rights_form_remembered_wins_attestation_never_inherited():
    payload = {
        "rights_basis": "own_content",
        "evidence_reference": "old-ref",
        "original_contribution": "تحليل قديم",
        "attested": True,
    }
    remembered = {
        "rights_basis": "licensed",
        "evidence_reference": "lic-123",
        "original_contribution": "تعليق جديد",
    }
    form = app.initial_rights_form(payload, remembered)
    assert form["rights_basis"] == "licensed"
    assert form["evidence_reference"] == "lic-123"
    assert form["original_contribution"] == "تعليق جديد"
    # الإقرار لا يُورَّث أبداً حتى لو كان في السجل أو في المُتذكَّر.
    assert form["attested"] is False
    form = app.initial_rights_form(payload, None)
    assert form["rights_basis"] == "own_content"
    assert form["attested"] is False
    form = app.initial_rights_form(None, None)
    assert form == {
        "rights_basis": "",
        "evidence_reference": "",
        "original_contribution": "",
        "attested": False,
    }


def test_gui_wiring_bulk_review_present():
    """زر لوحة التحكم وطريقة النافذة موجودتان."""
    assert hasattr(app.App, "_open_bulk_review")
    assert hasattr(app.App, "_open_bulk_review_locked")


def test_bulk_review_refuses_while_worker_running(monkeypatch):
    """لا تُفتح النافذة أثناء التشغيل؛ تحذير فقط ولا قفل ولا نافذة."""
    calls = []
    monkeypatch.setattr(app.messagebox, "showwarning", lambda *a, **k: calls.append(a[1]))

    stub = app.App.__new__(app.App)
    stub._bulk_review_window = None
    stub._watching = True
    stub._thread = None
    stub.root = None
    stub._open_bulk_review()

    assert calls == ["أوقف العامل قبل مراجعة قائمة الانتظار."]


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _release_stub(**overrides):
    stub = app.App.__new__(app.App)
    defaults = {
        "content_review_enabled_var": _Var(False),
        "cutter_root_var": _Var(""),
        "cutter_python_var": _Var(""),
        "priv_var": _Var("private"),
        "sched_var": _Var(False),
        "review_before_upload_var": _Var(True),
        "allow_public_automation_var": _Var(False),
        "dry_run_var": _Var(False),
        "rights_gate_var": _Var("strict"),
        "rights_basis_var": _Var("own_content"),
        "rights_statement_e": _Var("بيان"),
        "rights_evidence_e": _Var(""),
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(stub, key, value)
    return stub


def test_bulk_release_errors_safety_gate_disabled():
    stub = _release_stub()
    errors = stub._bulk_release_errors(
        [{"id": "b", "last_error": "content_safety_review_required"}])
    assert any("بوابة مراجعة المحتوى" in error for error in errors)


def test_bulk_release_errors_strict_rights_without_manual_review(monkeypatch):
    monkeypatch.setattr(app, "automatic_rights_preflight_errors", lambda settings: [])
    stub = _release_stub(review_before_upload_var=_Var(False))
    errors = stub._bulk_release_errors(
        [{"id": "a", "last_error": "rights_review_required"}])
    assert errors == [
        "فعّل المراجعة اليدوية لكل فيديو أو أكمل إقرار المشغل الحقيقي قبل إعادة العناصر إلى قائمة العمل."
    ]


def test_bulk_release_errors_clean_when_gates_ok(monkeypatch):
    monkeypatch.setattr(app, "automatic_rights_preflight_errors", lambda settings: [])
    monkeypatch.setattr(app, "validate_content_review_settings", lambda *a, **k: [])
    monkeypatch.setattr(app, "validate_publication_controls", lambda *a, **k: [])
    stub = _release_stub(content_review_enabled_var=_Var(True))
    errors = stub._bulk_release_errors(_items())
    assert errors == []


def test_bulk_release_errors_ignores_releasable_without_gate():
    stub = _release_stub()
    errors = stub._bulk_release_errors(
        [{"id": "d", "last_error": "abandoned"}])
    assert errors == []
