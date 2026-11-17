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
import tempfile

from PIL import Image, ImageDraw, ImageFilter

from file_security import restrict_file_permissions
from safe_logging import redact_sensitive_text

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
        return ("[1:v]format=rgba[badge];"
                f"[0:v][badge]overlay=x='{x}':y='{y}':format=auto[vout]")
    sx_template, sy_template = LOGO_POSITIONS.get(
        secondary_position, LOGO_POSITIONS["أسفل اليسار"])
    sx = sx_template.format(m=int(margin))
    sy = sy_template.format(m=int(margin))
    return ("[1:v]format=rgba[primary_badge];"
            f"[0:v][primary_badge]overlay=x='{x}':y='{y}':format=auto[primary];"
            "[2:v]format=rgba[secondary_badge];"
            f"[primary][secondary_badge]overlay=x='{sx}':y='{sy}':format=auto[vout]")


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

