# -*- coding: utf-8 -*-
"""معالجة الوسائط: فحص الفيديو، وتركيب الشعار عبر ffmpeg، وفحص توفر FFmpeg.

فُصل هذا القسم عن الواجهة الرئيسية (`fb_youtube_uploader_v49_pro.py`) ليكون
قابلاً للاختبار والاستيراد مستقلاً؛ تعتمد الواجهة على هذه الدوال عبر إعادة
التصدير، فلا يتغير أي سلوك خارجي.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import glob
import tempfile

from PIL import Image, ImageDraw, ImageFilter

from file_security import restrict_file_permissions
from safe_logging import redact_sensitive_text

import math
import re

import arabic_reshaper
from bidi.algorithm import get_display
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from PIL import ImageEnhance, ImageFont, ImageStat
from PIL import features
__all__ = [
    "LOGO_POSITIONS",
    "_ffmpeg_error_detail",
    "apply_logo_overlay",
    "build_overlay_filter",
    "create_logo_badge",
    "ffmpeg_available",
    "probe_video",
]

# ════════════════════════════════════════════════════
# VIDEO PROCESSOR — تركيب الشعار بـ ffmpeg
# ════════════════════════════════════════════════════
def ffmpeg_available():
    """يعيد True فقط إذا خرج ffmpeg بنجاح: نسخة معطوبة كانت تُعدّ «مثبتة»."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=10
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def probe_video(path):
    """يعيد (العرض، الارتفاع، المدة بالثواني) عبر ffprobe.

    المسار المطلق يمنع اسم ملف يبدأ بـ«-» من أن يُقرأ كخيار. وفشل ffprobe
    يُسجَّل بنصه بدل أن يختفي كـ«ملف بلا أبعاد».
    """
    try:
        target = os.path.abspath(os.fspath(path))
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", target],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logging.warning(
                "فشل ffprobe في قراءة %s (exit %s): %s",
                os.path.basename(target), result.returncode,
                (result.stderr or "")[-300:],
            )
            return 0, 0, 0.0
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        duration = float(data.get("format", {}).get("duration") or 0)
        return int(stream.get("width") or 0), int(stream.get("height") or 0), duration
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        logging.warning("تعذر تشغيل ffprobe: %s", error)
        return 0, 0, 0.0

LOGO_POSITIONS = {
    "أعلى اليمين": ("main_w-overlay_w-{m}", "{m}"),
    "أعلى اليسار": ("{m}", "{m}"),
    "أسفل اليمين": ("main_w-overlay_w-{m}", "main_h-overlay_h-{m}"),
    "أسفل اليسار": ("{m}", "main_h-overlay_h-{m}"),
}


def build_overlay_filter(position_name, margin, secondary_enabled=False,
                         secondary_position="أسفل اليسار"):
    x_template, y_template = LOGO_POSITIONS.get(
        position_name, LOGO_POSITIONS["أعلى اليمين"])
    x = x_template.format(m=int(margin))
    y = y_template.format(m=int(margin))
    if not secondary_enabled:
        return ("[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[base];"
                "[1:v]format=rgba[badge];"
                f"[base][badge]overlay=x='{x}':y='{y}':format=auto,format=yuv420p[vout]")
    sx_template, sy_template = LOGO_POSITIONS.get(
        secondary_position, LOGO_POSITIONS["أسفل اليسار"])
    sx = sx_template.format(m=int(margin))
    sy = sy_template.format(m=int(margin))
    return ("[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[base0];"
            "[1:v]format=rgba[primary_badge];"
            f"[base0][primary_badge]overlay=x='{x}':y='{y}':format=auto[primary];"
            "[2:v]format=rgba[secondary_badge];"
            f"[primary][secondary_badge]overlay=x='{sx}':y='{sy}':format=auto,format=yuv420p[vout]")


def create_logo_badge(logo_path, video_width, video_height, scale_pct=8,
                      opacity=100, cover_old=True, cover_width_pct=20,
                      cover_height_pct=19, log_cb=None, plate=True):
    """يبني شارة الشعار.

    plate=True: لوحة مستديرة داكنة خلف الشعار (تغطية كاملة للخلفية).
    plate=False مع cover_old=True: وضع «الشعار فوق الشعار» — شعارك يُكبَّر
    ليغطي منطقة شعار القناة الأصلي مباشرة، بلا لوحة، مع ظل خفيف فقط.
    """
    with Image.open(logo_path) as source:
        has_alpha = "A" in source.getbands() or "transparency" in source.info
        original_size = source.size
        logo = source.convert("RGBA")
    if log_cb and (original_size[0] < 256 or original_size[1] < 128):
        log_cb("دقة الشعار منخفضة؛ استخدم ملفاً أكبر من 512×256 بكسل للحصول على حواف أوضح.")
    if log_cb and not has_alpha:
        log_cb("ملف الشعار بلا شفافية؛ قد يظهر مستطيل خلفه. الأفضل PNG شفاف.")
    alpha_box = logo.getchannel("A").getbbox()
    if not alpha_box:
        raise ValueError("ملف الشعار شفاف بالكامل أو فارغ")
    logo = logo.crop(alpha_box)
    if logo.width < 1 or logo.height < 1:
        raise ValueError("ملف الشعار فارغ")

    padding = max(8, int(min(video_width, video_height) * 0.02))
    factor = 3

    if cover_old and not plate:
        cover_width = max(8, min(int(video_width * min(max(cover_width_pct, 8), 45) / 100),
                                 int(video_width * 0.45)))
        cover_height = max(8, min(int(video_height * min(max(cover_height_pct, 5), 35) / 100),
                                  int(video_height * 0.35)))
        # التوسيع بأسلوب «تغطية»: أكبر بعد يضمن إخفاء الشعار القديم خلف شعارك.
        scale = max(cover_width / logo.width, cover_height / logo.height)
        scale = max(0.05, min(scale, 8.0))
        logo = logo.resize(
            (max(1, round(logo.width * scale)), max(1, round(logo.height * scale))),
            Image.Resampling.LANCZOS)
        logo_alpha = logo.getchannel("A")
        logo_alpha = logo_alpha.point(
            lambda value: round(value * min(max(opacity, 5), 100) / 100))
        logo.putalpha(logo_alpha)

        width, height = logo.width * factor, logo.height * factor
        logo_up = logo.resize((width, height), Image.Resampling.LANCZOS)
        badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_alpha = logo_up.getchannel("A").filter(
            ImageFilter.GaussianBlur(factor * 4))
        shadow_alpha = shadow_alpha.point(lambda value: round(value * 0.55))
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow.putalpha(shadow_alpha)
        badge.alpha_composite(shadow, (0, factor * 2))
        badge.alpha_composite(logo_up, (0, 0))
        return badge.resize((logo.width, logo.height), Image.Resampling.LANCZOS)

    max_logo_width = max(24, int(video_width * min(max(scale_pct, 1), 60) / 100))
    max_logo_height = max(24, int(video_height * 0.16))
    logo.thumbnail((max_logo_width, max_logo_height), Image.Resampling.LANCZOS)

    if cover_old:
        badge_width = max(
            int(video_width * min(max(cover_width_pct, 8), 45) / 100),
            logo.width + padding * 2)
        badge_height = max(
            int(video_height * min(max(cover_height_pct, 5), 35) / 100),
            logo.height + padding * 2)
        badge_width = min(badge_width, max(1, int(video_width * 0.45)))
        badge_height = min(badge_height, max(1, int(video_height * 0.35)))
    else:
        badge_width = logo.width + padding * 2
        badge_height = logo.height + padding * 2

    content_width = max(1, badge_width - padding * 2)
    content_height = max(1, badge_height - padding * 2)
    ratio = min(content_width / logo.width, content_height / logo.height, 1.0)
    if ratio < 1:
        logo = logo.resize((max(1, round(logo.width * ratio)),
                            max(1, round(logo.height * ratio))),
                           Image.Resampling.LANCZOS)
    logo_alpha = logo.getchannel("A")
    logo_alpha = logo_alpha.point(
        lambda value: round(value * min(max(opacity, 5), 100) / 100))
    logo.putalpha(logo_alpha)

    width, height = badge_width * factor, badge_height * factor
    badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    logo = logo.resize((logo.width * factor, logo.height * factor),
                       Image.Resampling.LANCZOS)
    logo_x = (width - logo.width) // 2
    logo_y = (height - logo.height) // 2

    if cover_old:
        inset = max(2, padding // 3) * factor
        bounds = (inset, inset, width - inset - 1, height - inset - 1)
        radius = max(8, int(min(badge_width, badge_height) * 0.22)) * factor
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (bounds[0], bounds[1] + factor * 3, bounds[2], bounds[3] + factor * 3),
            radius=radius, fill=(0, 0, 0, 100))
        badge.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(factor * 3)))
        panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle(
            bounds, radius=radius, fill=(13, 18, 29, 255),
            outline=(255, 255, 255, 62), width=factor)
        badge.alpha_composite(panel)
    else:
        shadow_alpha = logo.getchannel("A").filter(
            ImageFilter.GaussianBlur(factor * 2))
        shadow_alpha = shadow_alpha.point(lambda value: round(value * 0.5))
        shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
        shadow.putalpha(shadow_alpha)
        badge.alpha_composite(shadow, (logo_x + factor, logo_y + factor * 2))

    badge.alpha_composite(logo, (logo_x, logo_y))
    return badge.resize((badge_width, badge_height), Image.Resampling.LANCZOS)


def _ffmpeg_error_detail(stderr):
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    return redact_sensitive_text("\n".join(lines[-4:]))[-500:]


def apply_logo_overlay(input_path, logo_path, position_name="أعلى اليمين",
                       scale_pct=8, opacity=100, cover_old=True,
                       landscape_cover_width_pct=20, landscape_cover_height_pct=19,
                       portrait_cover_width_pct=25, portrait_cover_height_pct=12,
                       secondary_enabled=False, secondary_cover_old=False,
                       plate=True, log_cb=None):
    if not os.path.isfile(logo_path):
        if log_cb:
            log_cb("ملف الشعار غير موجود — أوقف الرفع حتى لا يُنشر الفيديو بلا شعار")
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        if log_cb:
            log_cb("تعذر تركيب الشعار: FFmpeg غير مثبت. ثبّته وأضفه إلى PATH قبل إعادة المحاولة.")
        return None
    try:
        video_width, video_height, _ = probe_video(input_path)
    except Exception as error:
        safe_error = redact_sensitive_text(error)
        if log_cb:
            log_cb(f"تعذر فحص الفيديو قبل تركيب الشعار: {safe_error}. تحقق من سلامة الملف وffprobe.")
        return None
    if video_width <= 0 or video_height <= 0:
        if log_cb:
            log_cb("تعذرت قراءة أبعاد الفيديو — أوقف الرفع")
        return None
    is_landscape = video_width >= video_height
    cover_width = landscape_cover_width_pct if is_landscape else portrait_cover_width_pct
    cover_height = landscape_cover_height_pct if is_landscape else portrait_cover_height_pct
    try:
        badge = create_logo_badge(
            logo_path, video_width, video_height, scale_pct, opacity, cover_old,
            cover_width, cover_height, log_cb, plate=plate)
        secondary_badge = None
        if secondary_enabled:
            secondary_width = min(35, cover_width + (8 if is_landscape else 5))
            secondary_height = max(5, min(15, round(cover_height * 0.45)))
            secondary_badge = create_logo_badge(
                logo_path, video_width, video_height,
                max(4, round(scale_pct * 0.65)), opacity, secondary_cover_old,
                secondary_width, secondary_height, log_cb, plate=plate)
    except Exception as error:
        logging.exception("تعذر تجهيز شارة الشعار")
        if log_cb:
            log_cb(f"تعذر تجهيز الشعار: {error}")
        return None

    margin = max(6, round(min(video_width, video_height) *
                          (0.025 if cover_old else 0.035)))
    secondary_position = "أسفل اليمين" if position_name == "أسفل اليسار" else "أسفل اليسار"
    out_path = None
    try:
        output_directory = os.path.dirname(os.path.abspath(input_path))
        descriptor, out_path = tempfile.mkstemp(
            prefix=f"{os.path.splitext(os.path.basename(input_path))[0]}_branded-",
            suffix=".mp4",
            dir=output_directory,
        )
        os.close(descriptor)
        with tempfile.TemporaryDirectory(prefix="fb-youtube-logo-") as temp_dir:
            badge_path = os.path.join(temp_dir, "logo_badge.png")
            badge.save(badge_path, format="PNG", optimize=True)
            cmd = [ffmpeg_bin, "-y", "-nostdin", "-i", input_path, "-i", badge_path]
            if secondary_badge is not None:
                secondary_path = os.path.join(temp_dir, "logo_badge_secondary.png")
                secondary_badge.save(secondary_path, format="PNG", optimize=True)
                cmd.extend(["-i", secondary_path])
            cmd.extend([
                "-filter_complex", build_overlay_filter(
                    position_name, margin, secondary_enabled, secondary_position),
                "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "20", "-c:a", "copy",
                "-movflags", "+faststart", out_path,
            ])
            if log_cb:
                message = "تركيب بطاقة الشعار الاحترافية"
                if secondary_enabled:
                    message += " مع العلامة الثانوية"
                log_cb(message + "…")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            detail = _ffmpeg_error_detail(proc.stderr)
            logging.warning("ffmpeg logo overlay failed: %s", detail or "no output")
            if log_cb:
                log_cb(f"فشل تركيب الشعار: {detail or 'لم يُنشأ ملف صالح'}. تحقق من FFmpeg والملف والشعار.")
            os.unlink(out_path)
            return None
        if os.name == "posix" and not restrict_file_permissions(out_path):
            os.unlink(out_path)
            if log_cb:
                log_cb("تعذر تقييد أذونات فيديو الشعار؛ لم يُستخدم الملف.")
            return None
        return out_path
    except Exception as error:
        if out_path and os.path.isfile(out_path) and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass
        safe_error = redact_sensitive_text(error)
        logging.warning("خطأ في تركيب الشعار: %s", safe_error)
        if log_cb:
            log_cb(f"تعذر تركيب الشعار: {safe_error}. تحقق من FFmpeg وصلاحيات المجلد.")
        return None


# ════════════════════════════════════════════════════
# THUMBNAILS / SHORTS CUT / ANALYTICS (v4.11.2)
# ════════════════════════════════════════════════════
THUMB_WIDTH, THUMB_HEIGHT = 1280, 720
THUMB_BUDGET_UNITS = 50  # thumbnails.set = 50 وحدة من الحصة العامة
THUMB_CANDIDATE_COUNT = 6     # عدد اللقطات المرشحة لاختيار أفضل صورة مصغّرة
THUMB_MIN_SPAN_SECONDS = 8.0  # أقل مدة يستحق الفحص المتعدد للقطات


def _contains_arabic(text):
    return any("\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f" or "\u08a0" <= char <= "\u08ff" for char in text)


def _clean_thumbnail_title(text):
    text = re.sub(r"https?://\S+", " ", str(text or ""), flags=re.IGNORECASE)
    text = re.sub(r"(?<!\S)#[^\s#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _thumbnail_uses_raqm():
    try:
        return features.check("raqm")
    except (AttributeError, ValueError):
        return False


def _thumbnail_display_text(text):
    rtl = _contains_arabic(text)
    if not rtl or _thumbnail_uses_raqm():
        return text, rtl
    return get_display(arabic_reshaper.reshape(text)), True


def _thumbnail_text_width(text, font):
    display, rtl = _thumbnail_display_text(text)
    if rtl and _thumbnail_uses_raqm():
        try:
            return font.getlength(display, direction="rtl", language="ar")
        except (TypeError, ValueError):
            pass
    return font.getlength(display)


def _ellipsize_thumbnail_text(text, max_width, font):
    text = text.rstrip()
    while text and _thumbnail_text_width(text + "…", font) > max_width:
        text = text[:-1].rstrip()
    return text + "…" if text else "…"


def _wrap_text(text, max_width, font=None, max_lines=3):
    text = _clean_thumbnail_title(text)
    if not text or max_width <= 0 or max_lines <= 0:
        return []
    font = font or _load_caption_font(54)
    lines, current = [], ""
    truncated = False
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if _thumbnail_text_width(candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            truncated = True
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []
    if truncated or _thumbnail_text_width(lines[-1], font) > max_width:
        lines[-1] = _ellipsize_thumbnail_text(lines[-1], max_width, font)
    return lines[:max_lines]


_FONT_COVERAGE_CACHE = {}


def _font_covers_text(path, text):
    """يتحقق أن الخط يرسم كل حروف النص؛ يخزّن جدول المحارف لكل خط مرة واحدة."""
    cmap = _FONT_COVERAGE_CACHE.get(path)
    if cmap is None:
        try:
            from fontTools.ttLib import TTFont
            cmap = TTFont(path, fontNumber=0).getBestCmap() or {}
        except Exception:
            cmap = {}
        _FONT_COVERAGE_CACHE[path] = cmap
    if not cmap:
        return False
    return all(ord(char) in cmap or char.isspace() for char in text)


def _load_caption_font(size, text=None):
    windows_fonts = os.path.join(os.environ.get("WINDIR", r"C:\\Windows"), "Fonts")
    candidates = (
        os.path.join(windows_fonts, "arialbd.ttf"),
        os.path.join(windows_fonts, "tahomabd.ttf"),
        os.path.join(windows_fonts, "calibrib.ttf"),
        os.path.join(windows_fonts, "seguisb.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "DejaVuSans-Bold.ttf",
        "Arial Bold.ttf",
        "arial.ttf",
    )
    for name in candidates:
        if text and not _font_covers_text(name, text):
            continue
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    if text:
        for name in candidates:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap_thumbnail_exact(text, max_width, font, max_lines=2):
    """يغلّف النص دون حذف؛ يعيد [] إذا لم يتسع في max_lines أسطر بهذا الخط."""
    text = _clean_thumbnail_title(text)
    if not text or max_width <= 0 or max_lines <= 0:
        return []
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if _thumbnail_text_width(candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            return []
    if current and len(lines) < max_lines:
        lines.append(current)
    if lines and all(_thumbnail_text_width(line, font) <= max_width for line in lines):
        return lines
    return []


def _fit_thumbnail_lines(text, max_width, width, max_lines=2):
    """يختار أكبر حجم خط يُدخل العنوان كاملاً في الأسطر المسموحة، ثم يتراجع للقص بحذف."""
    start = max(36, round(width * 0.052))
    floor = max(30, round(width * 0.030))
    size = start
    while size > floor:
        font = _load_caption_font(size, text=text)
        lines = _wrap_thumbnail_exact(text, max_width, font, max_lines)
        if lines:
            return lines, font
        size = max(floor, round(size * 0.90))
    font = _load_caption_font(floor, text=text)
    lines = _wrap_thumbnail_exact(text, max_width, font, max_lines)
    if lines:
        return lines, font
    return _wrap_text(text, max_width, font=font, max_lines=max_lines), font


def _draw_thumbnail_title(image, title):
    text = _clean_thumbnail_title(title)
    if not text:
        return image
    width, height = image.size
    rtl = _contains_arabic(text)
    margin = max(48, round(width * 0.065))
    accent = max(7, round(width * 0.007))
    text_width = width - margin * 2 - accent - 24
    lines, font = _fit_thumbnail_lines(text, text_width, width, max_lines=2)
    if not lines:
        return image
    font_size = getattr(font, "size", 54)
    ascent, descent = font.getmetrics()
    line_height = max(round((ascent + descent) * 1.04),
                      round(font_size * 1.30), round(height * 0.09))
    top_pad = max(15, round(height * 0.026))
    bottom_pad = max(18, round(height * 0.03))
    band_height = min(height, top_pad + line_height * len(lines) + bottom_pad)
    band_top = height - band_height
    band = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    band_draw = ImageDraw.Draw(band)
    for y in range(band_height):
        progress = y / max(1, band_height - 1)
        alpha = round(22 + 208 * progress ** 1.45)
        band_draw.line((0, y, width, y), fill=(7, 12, 22, alpha))
    result = image.convert("RGBA")
    result.alpha_composite(band, (0, band_top))
    draw = ImageDraw.Draw(result)
    accent_x = width - margin - accent if rtl else margin
    draw.rounded_rectangle(
        (accent_x, band_top + top_pad, accent_x + accent, height - bottom_pad),
        radius=max(3, accent // 2), fill=(239, 73, 78, 245),
    )
    placements = []
    y = band_top + top_pad + max(0, (line_height - (ascent + descent)) // 2)
    right_edge = width - margin - accent - 22
    for line in lines:
        display, line_rtl = _thumbnail_display_text(line)
        text_rtl = rtl or line_rtl
        if text_rtl and _thumbnail_uses_raqm():
            placements.append(((right_edge, y), display,
                               {"anchor": "ra", "direction": "rtl", "language": "ar"}))
        elif text_rtl:
            line_width = font.getlength(display)
            placements.append(((right_edge - line_width, y), display, {"anchor": "la"}))
        else:
            placements.append(((margin + accent + 22, y), display, {"anchor": "la"}))
        y += line_height
    shadow_layer = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    for (pos, display, anchor_kwargs) in placements:
        shadow_draw.text(
            (pos[0] + 2, pos[1] + 4), display, font=font, fill=(0, 0, 0, 215),
            stroke_width=2, stroke_fill=(0, 0, 0, 215), **anchor_kwargs,
        )
    result.alpha_composite(shadow_layer.filter(ImageFilter.GaussianBlur(5)), (0, band_top))
    draw = ImageDraw.Draw(result)
    for (pos, display, anchor_kwargs) in placements:
        draw.text(
            pos, display, font=font, fill=(255, 255, 255, 255),
            stroke_width=2, stroke_fill=(0, 0, 0, 170), **anchor_kwargs,
        )
    return result


def render_thumbnail_title_from_base(base_path, title, out_path=None):
    if not os.path.isfile(base_path) or os.path.islink(base_path):
        return None
    owned_output = False
    if out_path is None:
        out_path = _temporary_thumbnail_path("fb-youtube-preview-title-")
        owned_output = True
    else:
        out_path = os.path.abspath(os.fspath(out_path))
        if (
            os.path.islink(out_path)
            or not os.path.isfile(out_path)
            or not os.path.basename(out_path).startswith("fb-youtube-preview-")
        ):
            return None
    success = False
    try:
        with Image.open(base_path) as source:
            image = source.convert("RGB")
        image = _draw_thumbnail_title(image, title)
        image.convert("RGB").save(out_path, "PNG")
        if os.path.getsize(out_path) <= 0:
            raise OSError("لم يُنشأ ملف معاينة صالح.")
        success = True
        return out_path
    except Exception as error:
        logging.warning("تعذر تحديث عنوان الصورة المصغرة: %s", redact_sensitive_text(error))
        return None
    finally:
        if owned_output and not success and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass


def _temporary_png_path(prefix="fb-youtube-thumbnail-"):
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".png")
    os.close(fd)
    os.remove(path)
    return path


def _temporary_thumbnail_path(prefix):
    descriptor, path = tempfile.mkstemp(prefix=prefix, suffix=".png")
    os.close(descriptor)
    return path


def _thumbnail_frame_score(path):
    """يعيد درجة ملاءمة الإطار (حدة × إضاءة)؛ 0 عند تعذر قراءة الصورة أو إضاءة شديدة الشحوب/الظلمة."""
    try:
        with Image.open(path) as source:
            gray = source.convert("L").resize((160, 90))
            brightness = ImageStat.Stat(gray).mean[0]
            edges = gray.filter(ImageFilter.FIND_EDGES)
            sharpness = ImageStat.Stat(edges).mean[0]
    except Exception:
        return 0.0
    if brightness < 40.0 or brightness > 225.0:
        return 0.0
    closeness = 1.0 - min(1.0, abs(brightness - 118.0) / 118.0)
    return sharpness * (0.35 + 0.65 * closeness)


def _extract_thumbnail_candidates(video_path, duration, tmp_dir):
    """يستخرج لقطات مرشحة موزّعة على معظم مدة الفيديو في تمريرة ffmpeg واحدة."""
    duration = float(duration or 0.0)
    if duration < THUMB_MIN_SPAN_SECONDS:
        return []
    start = duration * 0.10
    span = duration * 0.80
    pattern = os.path.join(tmp_dir, "frame%03d.png")
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.2f}", "-i", video_path,
           "-frames:v", str(THUMB_CANDIDATE_COUNT),
           "-vf", (f"fps={THUMB_CANDIDATE_COUNT / span:.5f},"
                   f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={THUMB_WIDTH}:{THUMB_HEIGHT}"),
           pattern]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        logging.warning("تعذر استخراج لقطات الصورة المصغرة: %s", error)
        return []
    if proc.returncode != 0:
        logging.warning("فشل استخراج لقطات الصورة المصغرة: %s", (proc.stderr or "")[-500:])
        return []
    candidates = []
    for path in sorted(glob.glob(os.path.join(tmp_dir, "frame*.png"))):
        try:
            if os.path.getsize(path) > 0:
                candidates.append(path)
        except OSError:
            continue
    return candidates


def _pick_best_frame(video_path, duration, log_cb=None):
    """يختار أوضح وأفضل إضاءة من لقطات مرشحة؛ يعيد مسار PNG مؤقت أو None."""
    tmp_dir = tempfile.mkdtemp(prefix="fb-youtube-frames-")
    try:
        candidates = _extract_thumbnail_candidates(video_path, duration, tmp_dir)
        if not candidates:
            return None
        scored = sorted(candidates, key=_thumbnail_frame_score, reverse=True)
        best = scored[0] if _thumbnail_frame_score(scored[0]) > 0 else None
        if best is None:
            return None
        fd, kept = tempfile.mkstemp(prefix="fb-youtube-frame-best-", suffix=".png")
        os.close(fd)
        try:
            shutil.copyfile(best, kept)
        except OSError:
            try:
                os.unlink(kept)
            except OSError:
                pass
            raise
        return kept
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _enhance_frame(image):
    """يرفع التباين والتشبع والحدة قليلاً لتظهر الصورة أوضح في شبكة YouTube."""
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Color(image).enhance(1.10)
    image = ImageEnhance.Sharpness(image).enhance(1.25)
    return image


def _capture_fallback_frame(video_path, duration, out_path, log_cb=None):
    """المسار القديم: لقطة واحدة من ربع المدة؛ يعيد True عند النجاح."""
    at = min(max((duration or 0) * 0.25, 1.0), max((duration or 0) - 0.5, 0.0))
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}",
           "-i", video_path, "-frames:v", "1",
           "-vf", f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=increase,crop={THUMB_WIDTH}:{THUMB_HEIGHT}",
           out_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        proc = None
        logging.warning("فشل التقاط الصورة المصغرة: %s", error)
    if proc is None or proc.returncode != 0 or not os.path.isfile(out_path):
        logging.warning("فشل التقاط الصورة المصغرة: %s", (getattr(proc, "stderr", "") or "")[-500:])
        if log_cb:
            log_cb("فشل توليد الصورة المصغرة — سيُرفع الفيديو بدونها")
        return False
    return True


def generate_thumbnail(video_path, title, out_path=None, log_cb=None, *, allow_empty_output=False):
    """يلتقط أفضل لقطة ويكتب العنوان؛ يستخدم ملفاً فريداً ولا يستبدل ملفات موجودة."""
    if not ffmpeg_available():
        if log_cb:
            log_cb("تعذر توليد الصورة المصغرة: FFmpeg غير مثبت. ثبّته وأضفه إلى PATH.")
        return None
    owned_output = False
    if out_path is None:
        out_path = _temporary_thumbnail_path("fb-youtube-thumbnail-")
        owned_output = True
    else:
        out_path = os.path.abspath(os.fspath(out_path))
        if os.path.lexists(out_path):
            if (
                not allow_empty_output
                or os.path.islink(out_path)
                or not os.path.isfile(out_path)
                or os.path.getsize(out_path) != 0
            ):
                if log_cb:
                    log_cb("تعذر حفظ الصورة المصغرة: ملف الإخراج موجود مسبقاً ولم يُستبدل.")
                return None
        else:
            try:
                descriptor = os.open(
                    out_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                os.close(descriptor)
                owned_output = True
            except OSError as error:
                if log_cb:
                    log_cb(f"تعذر إنشاء ملف الصورة المصغرة: {redact_sensitive_text(error)}")
                return None
    success = False
    try:
        _, _, duration = probe_video(video_path)
        frame_path = _pick_best_frame(video_path, duration, log_cb=log_cb)
        if frame_path is not None:
            try:
                with Image.open(frame_path) as source_image:
                    thumbnail = _enhance_frame(source_image.convert("RGB"))
            finally:
                try:
                    os.remove(frame_path)
                except OSError:
                    pass
            thumbnail.save(out_path, "PNG")
        else:
            if not _capture_fallback_frame(video_path, duration, out_path, log_cb=log_cb):
                return None
            with Image.open(out_path) as source_image:
                thumbnail = _enhance_frame(source_image.convert("RGB"))
            thumbnail.save(out_path, "PNG")
        if _clean_thumbnail_title(title):
            thumbnail = _draw_thumbnail_title(thumbnail, title)
            thumbnail.convert("RGB").save(out_path, "PNG")
        if not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            raise OSError("لم يُنشأ ملف صورة مصغّرة صالح.")
        if os.name == "posix" and not restrict_file_permissions(out_path):
            raise OSError("تعذر تقييد أذونات ملف الصورة المصغّرة.")
        success = True
        return out_path
    except Exception as error:
        safe_error = redact_sensitive_text(error)
        logging.warning("خطأ في توليد الصورة المصغرة: %s", safe_error)
        if log_cb:
            log_cb(f"تعذر توليد الصورة المصغرة: {safe_error}. تحقق من الملف وFFmpeg ثم أعد المحاولة.")
        return None
    finally:
        if owned_output and not success and out_path and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                logging.warning("تعذر حذف الصورة المصغّرة الجزئية: %s", redact_sensitive_text(error))


def create_thumbnail_preview(video_path, title, log_cb=None):
    base_path = _temporary_thumbnail_path("fb-youtube-preview-base-")
    preview_path = _temporary_thumbnail_path("fb-youtube-preview-")
    if not generate_thumbnail(
        video_path, "", out_path=base_path, log_cb=log_cb, allow_empty_output=True
    ):
        remove_thumbnail_preview(base_path, preview_path)
        return None, None
    if not render_thumbnail_title_from_base(base_path, title, out_path=preview_path):
        remove_thumbnail_preview(base_path, preview_path)
        return None, None
    return base_path, preview_path


def remove_thumbnail_preview(*paths):
    temp_root = os.path.realpath(tempfile.gettempdir())
    allowed_prefixes = ("fb-youtube-preview-", "fb-youtube-thumbnail-", "fb-youtube-thumb-")
    for path in set(paths):
        if not path:
            continue
        candidate = os.path.abspath(os.fspath(path))
        if os.path.islink(candidate) or not os.path.basename(candidate).startswith(allowed_prefixes):
            continue
        if os.path.realpath(os.path.dirname(candidate)) != temp_root:
            continue
        try:
            os.unlink(candidate)
        except FileNotFoundError:
            pass
        except OSError as error:
            logging.warning("تعذر حذف صورة معاينة مؤقتة: %s", redact_sensitive_text(error))


YOUTUBE_THUMB_MAX_BYTES = 2_000_000  # حد YouTube للصور المصغرة 2MB


def _thumbnail_upload_path(path):
    """يعيد مساراً ضمن حد 2MB، أو None إذا تعذّر تجهيزه.

    كان يعيد مسار JPEG فارغاً أو أكبر من الحد عند فشل التحويل، فيُرفع ملف
    تالف ويظهر سبب مضلّل بدل رسالة التحويل الحقيقية.
    """
    try:
        if os.path.getsize(path) <= YOUTUBE_THUMB_MAX_BYTES:
            return path
    except OSError:
        return None
    descriptor, jpg_path = tempfile.mkstemp(prefix="fb-youtube-thumb-", suffix=".jpg")
    os.close(descriptor)
    try:
        for quality in (90, 80, 70):
            with Image.open(path) as source:
                source.convert("RGB").save(jpg_path, "JPEG", quality=quality, optimize=True)
            if os.path.getsize(jpg_path) <= YOUTUBE_THUMB_MAX_BYTES:
                return jpg_path
    except Exception as error:
        logging.warning("تعذر تحويل الصورة المصغرة إلى JPEG: %s", redact_sensitive_text(error))
    try:
        os.unlink(jpg_path)
    except OSError:
        pass
    return None


def _http_error_reason(error):
    """يستخرج سبب خطأ Google API القابل للقراءة بدل رسالة عامة."""
    try:
        data = json.loads(error.resp.get("content", "{}") or "{}")
        errs = (data.get("error") or {}).get("errors") or []
        reason = errs[0].get("reason") if errs else (data.get("error") or {}).get("status")
        if reason:
            reasons = {
                "quotaExceeded": "نفدت حصة YouTube Data API اليومية",
                "dailyLimitExceeded": "نفدت حصة YouTube Data API اليومية",
                "rateLimitExceeded": "تجاوز معدل الطلبات — أعد المحاولة لاحقاً",
                "forbidden": "تتطلب قناة موثّقة أو صلاحية حساب كافية",
                "unauthorized": "صلاحية OAuth غير كافية لهذا الحساب",
                "invalidThumbnailImageFile": "صيغة الصورة غير مقبولة",
                "thumbnailImageTooLarge": "حجم الصورة يتجاوز 2MB",
            }
            return reasons.get(reason, reason)
    except Exception:
        pass
    return "سبب غير معروف — راجع errors.log"


def upload_thumbnail(youtube, video_id, path, log_cb=None):
    """يرفع صورة مصغرة للفيديو (50 وحدة). الفشل يحذّر فقط ولا يوقف الرفع."""
    if not os.path.isfile(path):
        if log_cb:
            log_cb("ملف الصورة المصغرة مفقود")
        return False
    upload_path = _thumbnail_upload_path(path)
    if not upload_path:
        if log_cb:
            log_cb("تعذر تجهيز الصورة المصغرة ضمن حد YouTube (2MB)؛ اكتمل رفع الفيديو بدونها.")
        return False
    converted = upload_path != path
    try:
        youtube.thumbnails().set(videoId=video_id,
                                 media_body=MediaFileUpload(upload_path)).execute()
        if log_cb:
            if converted:
                log_cb(f"رُفعت الصورة المصغرة للفيديو {video_id} (صيغة JPEG ضمن حد 2MB)")
            else:
                log_cb(f"رُفعت الصورة المصغرة للفيديو {video_id}")
        return True
    except HttpError as error:
        logging.warning("فشل رفع الصورة المصغرة %s: %s", video_id, error)
        if log_cb:
            log_cb(f"تعذر رفع الصورة المصغرة ({_http_error_reason(error)}) — تابعنا الرفع")
        return False
    finally:
        if converted:
            try:
                os.remove(upload_path)
            except OSError:
                pass


SHORTS_CLIP_SECONDS = 58
SHORTS_MIN_SOURCE = 95  # أقل مدة للمقطع الأصلي حتى يستحق القص


def _audio_rms_per_second(video_path):
    """يعيد {ثانية: متوسط RMS} عبر ffmpeg astats.

    ``None`` يعني أن ffmpeg نفسه فشل أو تجاوز المهلة — لا أن الفيديو صامت؛
    الخلط بينهما كان يُسجّل «لا صوت» ويترك ميزة القص معطلة بلا سبب ظاهر.
    """
    try:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-i", video_path, "-vn",
               "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
               "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        logging.warning("انتهت مهلة تحليل الصوت (900 ثانية)؛ لم تُنشأ نسخة Shorts.")
        return None
    except OSError as error:
        logging.warning("تعذر تشغيل ffmpeg لتحليل الصوت: %s", error)
        return None
    if proc.returncode != 0:
        logging.warning(
            "فشل ffmpeg في تحليل الصوت (exit %s): %s",
            proc.returncode, (proc.stderr or "")[-300:],
        )
        return None
    samples, current_t = [], None
    for raw in (proc.stderr or "").splitlines() + (proc.stdout or "").splitlines():
        line = raw.strip()
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            current_t = float(m.group(1))
            continue
        if "RMS_level" in line and current_t is not None:
            m2 = re.search(r"RMS_level=(-?[0-9.]+|-inf)", line)
            if m2:
                try:
                    val = float(m2.group(1))
                except ValueError:
                    val = -91.0
                if val == float("-inf"):
                    val = -91.0
                samples.append((int(current_t), val))
                current_t = None
    buckets = {}
    for sec, val in samples:
        buckets.setdefault(sec, []).append(val)
    return {sec: sum(vals) / len(vals) for sec, vals in buckets.items()}


def find_hot_segment(rms_by_second, clip_seconds=SHORTS_CLIP_SECONDS,
                     min_source=SHORTS_MIN_SOURCE, source_duration=None):
    """نافذة انزلاقية بطول الـ Short تختار أعلى متوسط صوت. يعيد (بداية، مدة) أو None."""
    span = int(source_duration) if source_duration else (max(rms_by_second) + 1 if rms_by_second else 0)
    if span < min_source or span < clip_seconds:
        return None
    last_start = span - clip_seconds
    best_s, best_avg = 0, -1e9
    for s in range(0, last_start + 1):
        window = [rms_by_second[i] for i in range(s, s + clip_seconds) if i in rms_by_second]
        if not window:
            continue
        avg = sum(window) / len(window)
        if avg > best_avg:
            best_avg, best_s = avg, s
    if best_avg <= -1e8:
        return None
    return best_s, clip_seconds


def cut_shorts_clip(source_path, start, duration, out_path=None, log_cb=None):
    """يقص نسخة مؤقتة فريدة ولا يستبدل أي ملف موجود."""
    source_path = os.path.abspath(os.fspath(source_path or ""))
    if os.path.islink(source_path) or not os.path.isfile(source_path):
        if log_cb:
            log_cb("تعذر قص Shorts: ملف المصدر غير موجود أو ليس ملفاً عادياً.")
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        if log_cb:
            log_cb("تعذر قص Shorts: FFmpeg غير مثبت. ثبّته وأضفه إلى PATH.")
        return None
    try:
        start_value = float(start)
        duration_value = float(duration)
    except (TypeError, ValueError, OverflowError):
        if log_cb:
            log_cb("تعذر قص Shorts: مدة البداية أو القص غير صالحة.")
        return None
    if not math.isfinite(start_value) or not math.isfinite(duration_value) or start_value < 0 or duration_value <= 0:
        if log_cb:
            log_cb("تعذر قص Shorts: مدة البداية أو القص غير صالحة.")
        return None
    owned_output = False
    if out_path is None:
        output_directory = os.path.dirname(source_path)
        prefix = f"{os.path.splitext(os.path.basename(source_path))[0]}_shorts-"
        try:
            descriptor, out_path = tempfile.mkstemp(
                prefix=prefix, suffix=".mp4", dir=output_directory
            )
            os.close(descriptor)
            owned_output = True
        except OSError as error:
            if log_cb:
                log_cb(f"تعذر إنشاء ملف Shorts مؤقت: {redact_sensitive_text(error)}")
            return None
    else:
        out_path = os.path.abspath(os.fspath(out_path))
        if os.path.lexists(out_path):
            if log_cb:
                log_cb("تعذر قص Shorts: ملف الإخراج موجود مسبقاً ولم يُستبدل.")
            return None
        owned_output = True
    success = False
    try:
        cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", str(start_value), "-t", str(duration_value), "-i", source_path,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-movflags", "+faststart", out_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            detail = redact_sensitive_text((proc.stderr or "").strip())[-300:]
            logging.warning("فشل قص نسخة Shorts: %s", detail or "لم يُنشأ ملف صالح")
            if log_cb:
                log_cb(f"فشل قص Shorts: {detail or 'لم يُنشأ ملف صالح'}. تحقق من FFmpeg والملف ثم أعد المحاولة.")
            return None
        if os.name == "posix" and not restrict_file_permissions(out_path):
            if log_cb:
                log_cb("تعذر تقييد أذونات ملف Shorts؛ لم يُستخدم.")
            return None
        success = True
        return out_path
    except Exception as error:
        safe_error = redact_sensitive_text(error)
        logging.warning("خطأ في قص نسخة Shorts: %s", safe_error)
        if log_cb:
            log_cb(f"خطأ في قص Shorts: {safe_error}. تحقق من FFmpeg والملف ثم أعد المحاولة.")
        return None
    finally:
        if owned_output and not success and out_path and not os.path.islink(out_path):
            try:
                os.unlink(out_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                logging.warning("تعذر حذف ملف Shorts الجزئي: %s", redact_sensitive_text(error))
