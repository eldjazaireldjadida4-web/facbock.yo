from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "content_safety.py"
spec = importlib.util.spec_from_file_location("content_safety_tested", MODULE_PATH)
assert spec and spec.loader
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


def valid_record(**changes):
    record = safety.new_rights_record(
        rights_basis="owned",
        original_contribution="تقرير أصلي يضيف سياقاً وتحليلاً ومعلومات موثقة للمشاهد.",
        duplicate_review_note="نسخة مختلفة في الوقائع والتغطية التحريرية عن الفيديو السابق.",
        source_url="https://facebook.com/reel/example",
        attested=True,
    )
    record.update(changes)
    return record


def test_owned_attested_record_with_original_contribution_is_valid():
    assert safety.rights_record_errors(valid_record()) == []


def test_permission_requires_evidence_reference():
    record = valid_record(rights_basis="permission", evidence_reference="")
    errors = safety.rights_record_errors(record)
    assert any("مرجع الترخيص" in error for error in errors)


def test_unattested_or_empty_original_contribution_is_rejected():
    record = valid_record(attested=False, original_contribution="")
    errors = safety.rights_record_errors(record)
    assert any("تأكيد" in error for error in errors)
    assert any("الإضافة التحريرية" in error for error in errors)


def test_possible_duplicate_requires_explanation():
    record = valid_record(duplicate_review_note="مختلف")
    assert any("يختلف هذا الفيديو" in error for error in safety.rights_record_errors(record, True))
    assert safety.rights_record_errors(record, False) == []


def test_rights_manifest_round_trip_is_keyed_by_source_id(tmp_path):
    path = tmp_path / "rights_manifest.json"
    record = valid_record()
    safety.save_rights_record("reel-123", record, str(path))
    assert safety.get_rights_record("reel-123", str(path)) == record
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_corrupt_manifest_is_not_overwritten(tmp_path):
    path = tmp_path / "rights_manifest.json"
    path.write_text("{not-json", encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        safety.save_rights_record("reel-123", valid_record(), str(path))
    assert path.read_bytes() == original


def test_identical_frame_fingerprints_are_detected():
    frames = [f"{index * 0x1100000000000001:016x}" for index in range(8)]
    result = safety.compare_video_fingerprints(frames, frames)
    assert result["similar"] is True
    assert result["matched_frames"] == 8
    assert result["coverage"] == 1.0


def test_overlapping_frame_fingerprints_are_detected_as_candidate():
    frames = [f"{index * 0x1100000000000001:016x}" for index in range(8)]
    result = safety.compare_video_fingerprints(frames[:5], frames[3:])
    assert result["similar"] is True
    assert result["matched_frames"] >= 3


def test_unrelated_frame_fingerprints_are_not_flagged():
    frames = [f"{index * 0x1100000000000001:016x}" for index in range(8)]
    inverted = [f"{int(value, 16) ^ 0xFFFFFFFFFFFFFFFF:016x}" for value in frames]
    result = safety.compare_video_fingerprints(frames, inverted)
    assert result["similar"] is False


def test_visual_match_contains_uploaded_video_identity():
    frames = [f"{index * 0x1100000000000001:016x}" for index in range(8)]
    matches = safety.find_video_fingerprint_matches(frames, [{
        "video_id": "yt-123", "title": "تغطية أصلية", "duration": 60, "frames": frames,
    }])
    assert matches[0]["video_id"] == "yt-123"
    assert matches[0]["match_type"] == "visual_fingerprint"


def test_missing_ffmpeg_fails_open_without_crashing(monkeypatch):
    monkeypatch.setattr(safety.shutil, "which", lambda _name: None)
    assert safety.extract_video_fingerprint("missing.mp4", 60) is None


def test_auto_rights_record_requires_real_transformation():
    from content_safety import build_auto_rights_record, rights_record_errors

    assert build_auto_rights_record("https://facebook.com/reel/1", []) is None
    record = build_auto_rights_record(
        "https://facebook.com/reel/1",
        ["إضافة شعار القناة على الفيديو", "إعادة صياغة العنوان والوصف تحريرياً بالذكاء الاصطناعي"],
    )
    assert record is not None
    assert record["rights_basis"] == ""
    assert record["evidence_reference"] == ""
    assert record["attested"] is False
    assert record["source_url"] == "https://facebook.com/reel/1"
    errors = rights_record_errors(record, duplicate_detected=False)
    assert any("اختر أساساً" in error for error in errors)
    assert any("يجب تأكيد" in error for error in errors)
    assert "لا تثبت الملكية أو الإذن" in record["original_contribution"]
