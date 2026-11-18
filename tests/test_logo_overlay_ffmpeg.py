# -*- coding: utf-8 -*-
"""اختبار انحدار حقيقي عبر ffmpeg: أبعاد فردية في إدخال الشعار.

يعيد إنتاج فشل «Terminating thread with return code -22 (Invalid argument)»
الذي حدث على فيديو 851×1277: كان مرشّح التراكب يخرج أبعاداً فردية فترفضها
libx264. مع scale=trunc(iw/2)*2 يجب أن ينجح التركيب ويخرج ملف صالح.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

import media_processing


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg غير متوفر في هذه البيئة"
)
def test_apply_logo_overlay_succeeds_on_odd_source_dimensions(tmp_path):
    source = tmp_path / "odd_dimensions.mp4"
    logo = tmp_path / "logo.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=851x1277:rate=30:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", str(source)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=red:size=256x128", "-frames:v", "1", str(logo)],
        check=True, capture_output=True,
    )

    output = media_processing.apply_logo_overlay(
        str(source), str(logo), log_cb=lambda _m: None)

    assert output is not None, "تركيب الشعار فشل على أبعاد فردية"
    assert os.path.isfile(output) and os.path.getsize(output) > 0
    width, height, _ = media_processing.probe_video(output)
    assert (width, height) == (850, 1276)
