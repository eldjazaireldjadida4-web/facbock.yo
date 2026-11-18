"""اختبارات وحدة معالجة الوسائط المستقلة (الشعار وفحص الفيديو)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_media_processing():
    spec = importlib.util.spec_from_file_location(
        "media_processing_standalone", PROJECT_ROOT / "media_processing.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_media_processing_imports_standalone_without_the_gui_module():
    module = _load_media_processing()
    for name in ("probe_video", "ffmpeg_available", "build_overlay_filter",
                 "create_logo_badge", "apply_logo_overlay", "_ffmpeg_error_detail",
                 "LOGO_POSITIONS"):
        assert hasattr(module, name), name


def test_build_overlay_filter_places_primary_and_secondary_badges():
    module = _load_media_processing()
    primary = module.build_overlay_filter("أعلى اليمين", 20)
    assert "[1:v]format=rgba[badge]" in primary
    assert "[base][badge]overlay=x='main_w-overlay_w-20':y='20':format=auto,format=yuv420p[vout]" in primary

    both = module.build_overlay_filter(
        "أعلى اليمين", 20, secondary_enabled=True, secondary_position="أسفل اليسار"
    )
    assert "[2:v]format=rgba[secondary_badge]" in both




def test_build_overlay_filter_normalizes_odd_dimensions_to_even_yuv420p():
    module = _load_media_processing()
    f = module.build_overlay_filter("أعلى اليمين", 20)
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in f
    assert f.endswith("format=yuv420p[vout]")
    both = module.build_overlay_filter(
        "أعلى اليمين", 20, secondary_enabled=True, secondary_position="أسفل اليسار")
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in both
    assert both.endswith("format=yuv420p[vout]")


def test_build_overlay_filter_falls_back_to_top_right_for_unknown_position():
    module = _load_media_processing()
    fallback = module.build_overlay_filter("موضع غير معروف", 8)
    assert "overlay=x='main_w-overlay_w-8':y='8'" in fallback


def test_overlay_filter_enforces_even_dimensions_yuv420p():
    module = _load_media_processing()
    single = module.build_overlay_filter("أعلى اليمين", 20)
    assert "[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[base];" in single
    assert "[base][badge]overlay=" in single
    assert single.endswith("format=yuv420p[vout]")

    both = module.build_overlay_filter(
        "أعلى اليمين", 20, secondary_enabled=True, secondary_position="أسفل اليسار"
    )
    assert "[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[base0];" in both
    assert both.endswith("format=yuv420p[vout]")


def test_probe_video_reports_zeros_for_a_missing_file_without_raising():
    module = _load_media_processing()
    missing = Path(__file__) / "لا-يوجد-هذا-الملف.mp4"
    assert module.probe_video(missing) == (0, 0, 0.0)


def test_ffmpeg_error_detail_keeps_tail_and_redacts_secrets():
    module = _load_media_processing()
    stderr = (
        "ffmpeg version 8.0.1\n"
        " token=private-token\n"
        " Conversion failed!\n"
    )
    detail = module._ffmpeg_error_detail(stderr)
    assert detail
    assert "private-token" not in detail
    assert "Conversion failed!" in detail
