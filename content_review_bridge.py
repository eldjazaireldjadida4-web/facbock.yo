from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ALLOWED_BACKENDS = {"openai-moderation", "gemini", "g4f"}
MAX_REVIEW_WINDOWS = 10_000
MAX_TRANSCRIPT_ENTRIES = 1_000_000
MAX_REPORT_BYTES = 5 * 1024 * 1024


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metadata_fingerprint(title: str, caption: str) -> str:
    payload = json.dumps(
        {"title": str(title or ""), "caption": str(caption or "")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_review_caption(description: str, tags=None) -> str:
    if isinstance(tags, str):
        tags = tags.split(",")
    effective_tags = [
        str(tag).strip() for tag in (tags or ["Facebook", "Reel"])
        if tag is not None and str(tag).strip()
    ]
    return json.dumps(
        {"description": str(description or ""), "youtube_tags": effective_tags},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _has_complete_clear_evidence(report: dict) -> bool:
    window_count = report.get("review_windows")
    transcript_count = report.get("transcript_entries")
    if (
        type(window_count) is not int
        or not 1 <= window_count <= MAX_REVIEW_WINDOWS
        or type(transcript_count) is not int
        or not 1 <= transcript_count <= MAX_TRANSCRIPT_ENTRIES
        or report.get("backend") not in ALLOWED_BACKENDS
    ):
        return False
    for key in ("blocked_windows", "manual_review_windows", "ai_violations"):
        values = report.get(key)
        if not isinstance(values, list) or values:
            return False
    expected_indices = set(range(window_count))
    ai_review = report.get("ai_review")
    if not isinstance(ai_review, list) or len(ai_review) != window_count:
        return False
    ai_indices = set()
    for item in ai_review:
        if not isinstance(item, dict) or type(item.get("index")) is not int:
            return False
        if item["index"] in ai_indices or item.get("violation") is not False:
            return False
        if not isinstance(item.get("reason", ""), str):
            return False
        ai_indices.add(item["index"])
    if ai_indices != expected_indices:
        return False
    local_review = report.get("local_review")
    if not isinstance(local_review, dict) or local_review.get("mode") != "block":
        return False
    local_segments = local_review.get("segments")
    if not isinstance(local_segments, list) or len(local_segments) != window_count:
        return False
    local_indices = set()
    for item in local_segments:
        if not isinstance(item, dict) or type(item.get("index")) is not int:
            return False
        if item["index"] in local_indices or item.get("status") != "safe":
            return False
        local_indices.add(item["index"])
    return local_indices == expected_indices


def validate_report(report, video_path: str, title: str, caption: str) -> tuple[bool, str]:
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        return False, "missing_or_invalid_report"
    if report.get("review_complete") is not True:
        return False, "review_incomplete"
    if report.get("status") != "clear":
        return False, str(report.get("status") or "review_required")
    if not _has_complete_clear_evidence(report):
        return False, "review_evidence_incomplete"
    try:
        video_hash = _sha256_file(video_path)
    except OSError:
        return False, "video_unavailable"
    if report.get("video_sha256") != video_hash:
        return False, "video_changed_after_review"
    if report.get("metadata_sha256") != metadata_fingerprint(title, caption):
        return False, "metadata_changed_after_review"
    return True, "clear"


def _store_report(report: dict, destination: Path) -> None:
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".safety-review-", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary_path, destination)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def _review_command(python_path, video, title, caption):
    """أمر المراجعة بصيغة لا تفسّر قيمة تبدأ بـ«-» كخيار.

    argparse يقبل ``--title=-x`` في كل الأحوال، ويستهلك ``-x`` كخيار إذا
    جاء منفصلاً — فيفشل الطفل ويُحجب الفيديو نهائياً.
    """
    command = [str(python_path), "-m", "scripts.upload_content_review"]
    for name, value in (("--video", video), ("--title", title or ""), ("--caption", caption or "")):
        text = str(value)
        command.extend([f"{name}={text}"] if text.startswith("-") else [name, text])
    return command


def run_review(
    video_path: str,
    title: str,
    caption: str,
    *,
    cutter_root: str,
    python_executable: str = "",
    timeout: int = 5400,
) -> tuple[dict | None, str]:
    if type(timeout) not in (int, float) or timeout <= 0:
        return None, "content_review_timeout_invalid"
    root = Path(cutter_root).expanduser().resolve()
    script = root / "scripts" / "upload_content_review.py"
    if not script.is_file():
        return None, "cutter_review_script_not_found"
    video = Path(video_path).expanduser().resolve()
    if not video.is_file():
        return None, "video_unavailable"
    python_executable = str(python_executable or "").strip()
    if python_executable:
        python_path = Path(python_executable).expanduser().resolve()
        if not python_path.is_file():
            return None, "content_review_python_not_found"
    else:
        candidates = [
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
        ]
        python_path = next((item for item in candidates if item.is_file()), Path(sys.executable))
    python_path = python_path.resolve()
    destination = video.with_name(video.name + ".safety-review.json")
    command = _review_command(python_path, video, title, caption)
    try:
        with tempfile.TemporaryDirectory(prefix=".content-review-", dir=str(video.parent)) as folder:
            report_path = Path(folder) / "review.json"
            command.extend(["--report", str(report_path)])
            try:
                result = subprocess.run(
                    command,
                    cwd=str(root),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=timeout,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                return None, "content_review_timed_out"
            except OSError:
                return None, "content_review_could_not_start"
            if result.returncode != 0:
                return None, "content_review_process_failed"
            try:
                if report_path.stat().st_size > MAX_REPORT_BYTES:
                    return None, "content_review_report_too_large"
                with report_path.open("r", encoding="utf-8") as source:
                    report = json.load(source)
            except (OSError, json.JSONDecodeError):
                return None, "content_review_report_unavailable"
            if not isinstance(report, dict):
                return None, "missing_or_invalid_report"
            _, status = validate_report(report, str(video), title, caption)
            _store_report(report, destination)
            return report, status
    except OSError:
        return None, "content_review_report_save_failed"
