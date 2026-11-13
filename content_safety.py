from __future__ import annotations

import json
import math
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

RIGHTS_BASIS_LABELS = {
    "owned": "حقوق إعادة النشر مملوكة بالكامل للمشغّل",
    "licensed": "ترخيص مكتوب",
    "permission": "إذن موثق من صاحب الحقوق",
    "public_domain": "ملكية عامة موثقة",
}
DEFAULT_OPERATOR_RIGHTS_STATEMENT = ""


def new_rights_record(
    rights_basis="",
    evidence_reference="",
    original_contribution="",
    duplicate_review_note="",
    source_url="",
    attested=False,
):
    return {
        "rights_basis": str(rights_basis or "").strip(),
        "evidence_reference": str(evidence_reference or "").strip(),
        "original_contribution": str(original_contribution or "").strip(),
        "duplicate_review_note": str(duplicate_review_note or "").strip(),
        "source_url": str(source_url or "").strip(),
        "attested": attested is True,
        "attested_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if attested is True else "",
    }


def rights_record_errors(record, duplicate_detected=False):
    if not isinstance(record, dict):
        return ["لا يوجد سجل حقوق صالح لهذا الفيديو."]
    errors = []
    basis = str(record.get("rights_basis", "")).strip()
    if basis not in RIGHTS_BASIS_LABELS:
        errors.append("اختر أساساً واضحاً لحقوق استخدام الفيديو.")
    if record.get("attested") is not True:
        errors.append("يجب تأكيد أنك تملك حق النشر أو إذناً صالحاً.")
    contribution = str(record.get("original_contribution", "")).strip()
    if len(contribution) < 25:
        errors.append("اشرح الإضافة التحريرية الأصلية بوضوح (25 حرفاً على الأقل).")
    if basis in {"licensed", "permission", "public_domain"}:
        evidence = str(record.get("evidence_reference", "")).strip()
        if len(evidence) < 5:
            errors.append("أضف مرجع الترخيص أو الإذن أو إثبات الملكية العامة.")
    if duplicate_detected and len(str(record.get("duplicate_review_note", "")).strip()) < 25:
        errors.append("اشرح كيف يختلف هذا الفيديو فعلياً عن المحتوى المشابه في القناة.")
    return errors

def build_auto_rights_record(source_url="", transformations=()):
    """يسجل التحويلات كمسودة فقط؛ فالتحويل لا يثبت الملكية أو الإذن."""
    parts = [str(t).strip() for t in transformations if str(t).strip()]
    if not parts:
        return None
    contribution = "مسودة لتوثيق التحويلات فقط؛ لا تثبت الملكية أو الإذن: " + "؛ ".join(parts) + "."
    return new_rights_record(
        original_contribution=contribution,
        source_url=str(source_url or "").strip(),
        attested=False,
    )

def build_operator_rights_record(
    source_url="",
    transformations=(),
    basis="owned",
    statement="",
    evidence_reference="",
    duplicate_note="",
):
    """سجل مُقرّ مسبقاً من المشغل: يُوثّق الإقرار المحفوظ في الإعدادات ويؤرّخه تلقائياً.

    يرفع ValueError إذا كان الإقرار ناقصاً أو الأساس يتطلب مرجع إثبات غير متوفر.
    """
    basis = str(basis or "").strip()
    if basis not in RIGHTS_BASIS_LABELS:
        raise ValueError("أساس الحقوق غير معروف؛ استخدم إحدى القيم: " + ", ".join(RIGHTS_BASIS_LABELS))
    statement = str(statement or "").strip()
    if len(statement) < 25:
        raise ValueError("إقرار المشغل (RIGHTS_OPERATOR_STATEMENT) قصير؛ اكتب إقراراً واضحاً بملكية الحق أو الإذن.")
    if basis in {"licensed", "permission", "public_domain"} and len(str(evidence_reference or "").strip()) < 5:
        raise ValueError("هذا الأساس يتطلب مرجعاً حقيقياً لا يقل عن 5 أحرف في RIGHTS_OPERATOR_EVIDENCE. إذا كان الفيديو أصلياً وتملك حقوقه كاملة فاختر owned؛ أما الترخيص أو الإذن فيحتاج مرجعه.")
    parts = [str(t).strip() for t in transformations if str(t).strip()]
    contribution = statement
    if parts:
        contribution += " التحويلات المطبقة: " + "؛ ".join(parts) + "."
    return new_rights_record(
        rights_basis=basis,
        evidence_reference=str(evidence_reference or "").strip(),
        original_contribution=contribution,
        duplicate_review_note=str(duplicate_note or "").strip(),
        source_url=str(source_url or "").strip(),
        attested=True,
    )


def migrate_legacy_rights_gate_config(config):
    """Do not infer rights consent or rewrite saved rights-gate choices."""
    return False


def validate_automatic_rights_settings(settings):
    if not isinstance(settings, dict):
        return ["إعدادات التشغيل الآلي غير صالحة."]
    if settings.get("dry_run") or settings.get("review_before_upload"):
        return []
    errors = validate_operator_rights_settings(settings)
    if errors:
        return errors
    if str(settings.get("rights_gate_mode") or "strict").strip().lower() == "strict":
        return ["RIGHTS_GATE_MODE=strict يمنع الرفع الآلي؛ اختر operator_confirmed بإقرار صادق أو فعّل المراجعة اليدوية لكل فيديو."]
    return []


def validate_operator_rights_settings(settings):
    if not isinstance(settings, dict):
        return ["إعدادات الإقرار الآلي غير صالحة."]
    mode = str(settings.get("rights_gate_mode") or "strict").strip().lower()
    if mode == "strict":
        return []
    if mode != "operator_confirmed":
        return ["قيمة RIGHTS_GATE_MODE غير معروفة؛ استخدم strict أو operator_confirmed."]
    try:
        build_operator_rights_record(
            basis=str(settings.get("rights_operator_basis") or "owned").strip().lower(),
            statement=settings.get("rights_operator_statement", ""),
            evidence_reference=settings.get("rights_operator_evidence", ""),
        )
    except ValueError as error:
        return [str(error)]
    return []


def _read_manifest_file(path):
    with open(path, "r", encoding="utf-8") as source:
        manifest = json.load(source)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), dict):
        raise ValueError(f"بنية سجل الحقوق غير صالحة: {path}")
    return manifest


def load_rights_manifest(path):
    """يقرأ سجل الحقوق، ويسقط إلى نسخة احتياطية صالحة عند تلف الأساسي.

    بلا هذا السقوط، انقطاع كهرباء أثناء الكتابة يجعل كل حفظ لاحق يفشل ويوقف
    الرفع حتى يتدخل إنسان ويصلح الملف. غياب الملف الأساسي يبقى «سجلاً فارغاً»
    عمداً: النسخ الاحتياطية لا تُحيي سجلاً حذفه المشغّل بنفسه.
    """
    try:
        return _read_manifest_file(path)
    except FileNotFoundError:
        return {"version": 1, "items": {}}
    except (OSError, ValueError, TypeError) as primary_error:
        errors = [primary_error]
        for candidate in (f"{path}.bak1", f"{path}.bak2"):
            try:
                manifest = _read_manifest_file(candidate)
            except (OSError, ValueError, TypeError) as error:
                errors.append(error)
                continue
            logging.warning("تم استرداد سجل الحقوق من النسخة الاحتياطية: %s", candidate)
            return manifest
        raise ValueError(f"ملف سجل الحقوق غير صالح: {path}") from errors[-1]


def get_rights_record(source_id, path):
    manifest = load_rights_manifest(path)
    record = manifest["items"].get(str(source_id))
    return dict(record) if isinstance(record, dict) else None


def save_rights_record(source_id, record, path):
    manifest = load_rights_manifest(path)
    manifest["version"] = 1
    manifest.setdefault("items", {})[str(source_id)] = dict(record)
    # نسخة احتياطية دوّارة قبل الكتابة (حماية سجل الحقوق من التلف)
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            for i in range(2, 0, -1):
                src_bak, dst_bak = f"{path}.bak{i}", f"{path}.bak{i + 1}"
                if os.path.exists(src_bak):
                    os.replace(src_bak, dst_bak)
            shutil.copyfile(path, f"{path}.bak1")
    except OSError as error:
        logging.debug("تعذر إنشاء نسخة احتياطية لسجل الحقوق: %s", error)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".rights-manifest-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
        if os.name == "posix":
            try:
                directory = os.open(parent, os.O_RDONLY)
            except OSError:
                directory = None
            if directory is not None:
                try:
                    os.fsync(directory)
                except OSError:
                    pass
                finally:
                    os.close(directory)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _dhash(frame):
    if len(frame) != 72:
        return None
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(frame[offset + column] > frame[offset + column + 1])
    return f"{value:016x}"


def extract_video_fingerprint(path, duration, frame_count=8, timeout=12):
    ffmpeg = shutil.which("ffmpeg")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return None
    if not ffmpeg or not path or duration <= 0 or frame_count < 3:
        return None
    hashes = []
    for index in range(frame_count):
        timestamp = duration * (index + 1) / (frame_count + 1)
        command = [
            ffmpeg, "-nostdin", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(path),
            "-an", "-sn", "-dn", "-frames:v", "1", "-vf",
            "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        digest = _dhash(result.stdout)
        if digest and digest not in hashes:
            hashes.append(digest)
    return hashes if len(hashes) >= 3 else None


def compare_video_fingerprints(left, right, max_hamming=9):
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
        return None
    if len(left) < 3 or len(right) < 3:
        return None
    edges = []
    try:
        for left_index, left_hash in enumerate(left):
            a = int(str(left_hash), 16)
            for right_index, right_hash in enumerate(right):
                distance = (a ^ int(str(right_hash), 16)).bit_count()
                if distance <= max_hamming:
                    edges.append((distance, left_index, right_index))
    except (TypeError, ValueError):
        return None
    used_left = set()
    used_right = set()
    distances = []
    for distance, left_index, right_index in sorted(edges):
        if left_index in used_left or right_index in used_right:
            continue
        used_left.add(left_index)
        used_right.add(right_index)
        distances.append(distance)
    denominator = min(len(left), len(right))
    coverage = len(distances) / denominator
    required_matches = max(3, math.ceil(denominator * 0.5))
    return {
        "similar": len(distances) >= required_matches and coverage >= 0.5,
        "matched_frames": len(distances),
        "coverage": round(coverage, 3),
        "mean_hamming": round(sum(distances) / len(distances), 2) if distances else None,
    }


def find_video_fingerprint_matches(fingerprint, known_fingerprints, limit=3):
    if not fingerprint:
        return []
    matches = []
    for previous in known_fingerprints if isinstance(known_fingerprints, list) else []:
        if not isinstance(previous, dict):
            continue
        comparison = compare_video_fingerprints(fingerprint, previous.get("frames"))
        if not comparison or not comparison["similar"]:
            continue
        duration = previous.get("duration")
        matches.append({
            "match_type": "visual_fingerprint",
            "video_id": previous.get("video_id", ""),
            "title": previous.get("title", ""),
            "duration": duration,
            "duration_delta": None,
            "similarity": comparison["coverage"],
            "matched_frames": comparison["matched_frames"],
            "mean_hamming": comparison["mean_hamming"],
        })
    return sorted(matches, key=lambda item: (item["similarity"], item["matched_frames"]), reverse=True)[:limit]
