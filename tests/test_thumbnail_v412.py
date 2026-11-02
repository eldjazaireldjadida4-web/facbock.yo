"""اختبارات تحسينات الصورة المصغّرة في v4.12.0."""
import os

import pytest
from PIL import Image, ImageDraw, ImageStat

import fb_youtube_uploader_v49_pro as app


def test_frame_score_prefers_bright_detailed_frame(tmp_path):
    sharp = tmp_path / "sharp.png"
    blank = tmp_path / "blank.png"
    img = Image.new("RGB", (160, 90), (30, 30, 30))
    draw = ImageDraw.Draw(img)
    for x in range(0, 160, 16):
        for y in range(0, 90, 16):
            if (x // 16 + y // 16) % 2 == 0:
                draw.rectangle((x, y, min(x + 15, 159), min(y + 15, 89)), fill=(220, 220, 220))
    img.save(sharp)
    Image.new("RGB", (160, 90), (10, 10, 10)).save(blank)
    assert app._thumbnail_frame_score(str(sharp)) > 0
    assert app._thumbnail_frame_score(str(blank)) == 0.0


def test_frame_score_rejects_unreadable_file(tmp_path):
    assert app._thumbnail_frame_score(str(tmp_path / "missing.png")) == 0.0


def test_fit_thumbnail_lines_returns_largest_font_without_truncation():
    width = 1280
    max_width = width - 2 * max(48, round(width * 0.065)) - max(7, round(width * 0.007)) - 24
    text = "تاريخ الجزائر في الكشافة الجزائرية عام 1961 تقرير خاص من الأرشيف"
    lines, font = app._fit_thumbnail_lines(text, max_width, width, max_lines=2)
    assert lines
    assert "…" not in "".join(lines)
    for line in lines:
        assert app._thumbnail_text_width(line, font) <= max_width


def test_fit_thumbnail_lines_falls_back_to_ellipsis_for_very_long_text():
    width = 1280
    max_width = 520
    text = ("كلمة " * 200).strip()
    lines, font = app._fit_thumbnail_lines(text, max_width, width, max_lines=2)
    assert lines
    assert len(lines) <= 2
    assert "…" in lines[-1] or all(
        app._thumbnail_text_width(line, font) <= max_width for line in lines)


def test_font_loader_prefers_font_that_covers_mixed_text():
    font = app._load_caption_font(54, text="عنوان — 2026 A")
    assert font is not None
    assert font.getlength("—") > 0


def test_draw_thumbnail_title_mixed_text_no_exception():
    image = Image.new("RGB", (1280, 720), (40, 40, 40))
    result = app._draw_thumbnail_title(image, "تقرير خاص — من الأرشيف 2026 A")
    assert result.size == (1280, 720)


def test_extract_candidates_skips_short_videos(tmp_path):
    assert app._extract_thumbnail_candidates("x.mp4", 5.0, str(tmp_path)) == []


def test_pick_best_frame_real_ffmpeg(tmp_path):
    if not app.ffmpeg_available():
        pytest.skip("ffmpeg غير متوفر")
    video = tmp_path / "v.mp4"
    # 12 ثانية: أربع ثوانٍ سوداء ثم مشهد متحرك — يجب تجنّب الإطار الأسود.
    os.system(
        "ffmpeg -y -hide_banner -loglevel error "
        "-f lavfi -i color=black:size=320x240:rate=10:duration=4 "
        "-f lavfi -i testsrc=size=320x240:rate=10:duration=8 "
        "-filter_complex [0:v][1:v]concat=n=2:v=1:a=0 "
        f"-c:v libx264 -preset ultrafast '{video}'"
    )
    frame = app._pick_best_frame(str(video), 12.0)
    assert frame is not None
    try:
        with Image.open(frame) as img:
            gray = img.convert("L")
        assert ImageStat.Stat(gray).mean[0] > 25.0
    finally:
        os.remove(frame)


class _FakeThumbs:
    def __init__(self, calls):
        self.calls = calls

    def set(self, videoId, media_body=None):
        filename = media_body._filename if media_body else None
        size = os.path.getsize(filename) if filename and os.path.exists(filename) else None
        self.calls.append((videoId, filename, size))
        return self

    def execute(self):
        return {"ok": True}


class _FakeYouTube:
    def __init__(self, calls):
        self._thumbs = _FakeThumbs(calls)

    def thumbnails(self):
        return self._thumbs


def test_upload_thumbnail_converts_oversized_png_to_jpeg(tmp_path):
    import random
    random.seed(7)
    img = Image.effect_noise((1280, 720), 40).convert("RGB")
    path = tmp_path / "big.png"
    img.save(path, "PNG")
    assert path.stat().st_size > app.YOUTUBE_THUMB_MAX_BYTES
    calls = []
    fake = _FakeYouTube(calls)
    assert app.upload_thumbnail(fake, "vid123", str(path)) is True
    assert len(calls) == 1
    uploaded, uploaded_size = calls[0][1], calls[0][2]
    assert uploaded.endswith(".jpg")
    assert uploaded_size is not None and uploaded_size <= app.YOUTUBE_THUMB_MAX_BYTES
    assert not os.path.exists(uploaded)  # الملف المؤقت حُذف بعد الرفع


def test_upload_thumbnail_small_png_unchanged(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (320, 180), (5, 5, 5)).save(path, "PNG")
    calls = []
    fake = _FakeYouTube(calls)
    assert app.upload_thumbnail(fake, "vid", str(path)) is True
    assert calls[0][1] == str(path) and calls[0][2] == path.stat().st_size