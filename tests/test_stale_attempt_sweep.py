"""اختبارات تنظيف مجلدات محاولات التنزيل العالقة عند بدء التشغيل."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fb_youtube_uploader_v49_pro as app


def _make_attempt_dir(output_dir: Path, name: str, age_hours: float) -> Path:
    candidate = output_dir / name
    candidate.mkdir(parents=True)
    (candidate / "chunk.part").write_bytes(b"x")
    stale_ts = time.time() - age_hours * 3600
    os.utime(candidate, (stale_ts, stale_ts))
    return candidate


def test_removes_only_old_hidden_attempt_dirs(tmp_path):
    _make_attempt_dir(tmp_path, ".fb2yt-oldcrash", age_hours=30)
    _make_attempt_dir(tmp_path, ".fb2yt-active", age_hours=1)
    (tmp_path / "fb2yt-keep.mp4").write_bytes(b"v")

    removed = app.cleanup_stale_attempt_dirs(str(tmp_path))

    assert removed == 1
    assert not (tmp_path / ".fb2yt-oldcrash").exists()
    assert (tmp_path / ".fb2yt-active").exists()
    assert (tmp_path / "fb2yt-keep.mp4").exists()


def test_tolerates_missing_output_dir(tmp_path):
    missing = tmp_path / "not-created"
    assert app.cleanup_stale_attempt_dirs(str(missing)) == 0


def test_keeps_recent_dirs_when_nothing_is_stale(tmp_path):
    _make_attempt_dir(tmp_path, ".fb2yt-fresh", age_hours=0.1)
    assert app.cleanup_stale_attempt_dirs(str(tmp_path)) == 0
    assert (tmp_path / ".fb2yt-fresh").exists()
