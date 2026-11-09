from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Callable

SUPPORTED_MODELS = ("tiny", "base", "small")
MAX_TRANSCRIPT_CHARS = 8000


class LocalTranscriptionError(RuntimeError):
    pass


class TranscriptionCancelled(Exception):
    pass


@functools.lru_cache(maxsize=2)
def _load_model(model_name: str):
    from faster_whisper import WhisperModel

    cpu_threads = max(1, min(os.cpu_count() or 2, 8))
    return WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        num_workers=1,
    )


def transcribe_video_file(
    media_path: str | os.PathLike[str],
    model_name: str = "base",
    log_cb: Callable[[str], None] | None = None,
    stop_event=None,
) -> str:
    selected_model = str(model_name or "base").strip().lower()
    if selected_model not in SUPPORTED_MODELS:
        raise LocalTranscriptionError("نموذج التفريغ غير مدعوم؛ اختر tiny أو base أو small.")

    try:
        path = Path(media_path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise LocalTranscriptionError("ملف الفيديو غير موجود أو فارغ للتفريغ المحلي.")
    except LocalTranscriptionError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise LocalTranscriptionError("تعذر قراءة ملف الفيديو للتفريغ المحلي.") from error
    if stop_event is not None and stop_event.is_set():
        raise TranscriptionCancelled()

    if log_cb:
        log_cb(f"يبدأ التفريغ الصوتي محلياً بنموذج {selected_model}; قد يُنزّل النموذج عند أول استخدام.")

    try:
        model = _load_model(selected_model)
        segments, info = model.transcribe(str(path), beam_size=3, vad_filter=True)
        transcript_parts = []
        transcript_length = 0
        for segment in segments:
            if stop_event is not None and stop_event.is_set():
                raise TranscriptionCancelled()
            text = str(getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            remaining = MAX_TRANSCRIPT_CHARS - transcript_length
            if remaining <= 0:
                break
            if transcript_parts:
                transcript_parts.append(" ")
                transcript_length += 1
                remaining -= 1
            transcript_parts.append(text[:remaining])
            transcript_length += min(len(text), remaining)
            if len(text) > remaining:
                break
        transcript = "".join(transcript_parts).strip()
    except (TranscriptionCancelled, LocalTranscriptionError):
        raise
    except ImportError as error:
        raise LocalTranscriptionError(
            "حزمة faster-whisper غير مثبتة؛ شغّل setup.sh أو setup.bat ثم أعد المحاولة."
        ) from error
    except Exception as error:
        raise LocalTranscriptionError(
            f"تعذر التفريغ المحلي ({type(error).__name__}). سيُستخدم وصف المصدر المتاح."
        ) from error

    if transcript and log_cb:
        language = str(getattr(info, "language", "") or "غير معروف")
        log_cb(f"اكتمل التفريغ المحلي ({len(transcript)} حرفاً، اللغة المكتشفة: {language}).")
    return transcript
