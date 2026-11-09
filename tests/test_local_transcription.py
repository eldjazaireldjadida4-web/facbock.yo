from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import local_transcription as transcription


def test_transcribes_locally_with_cpu_int8_and_voice_activity_detection(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    calls = {}

    class FakeModel:
        def transcribe(self, path, **kwargs):
            calls["path"] = path
            calls["kwargs"] = kwargs
            return iter([
                SimpleNamespace(text="  بداية مفيدة. "),
                SimpleNamespace(text=""),
                SimpleNamespace(text=" نهاية المقطع."),
            ]), SimpleNamespace(language="ar")

    def load_model(size):
        calls["model"] = size
        return FakeModel()

    monkeypatch.setattr(transcription, "_load_model", load_model)
    logs = []

    result = transcription.transcribe_video_file(video, log_cb=logs.append)

    assert result == "بداية مفيدة. نهاية المقطع."
    assert calls["model"] == "base"
    assert calls["kwargs"] == {"beam_size": 3, "vad_filter": True}
    assert "cpu" in logs[0].lower() or "محلياً" in logs[0]
    assert "ar" in logs[-1]
    assert result not in " ".join(logs)


def test_rejects_unsupported_model_and_missing_video(tmp_path):
    with pytest.raises(transcription.LocalTranscriptionError, match="غير مدعوم"):
        transcription.transcribe_video_file(tmp_path / "missing.mp4", "large")
    with pytest.raises(transcription.LocalTranscriptionError, match="غير موجود أو فارغ"):
        transcription.transcribe_video_file(tmp_path / "missing.mp4")


def test_cancellation_during_transcription_stops_without_returning_partial_text(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    stop_event = threading.Event()

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            def segments():
                yield SimpleNamespace(text="partial")
                stop_event.set()
                yield SimpleNamespace(text="should not be used")

            return segments(), SimpleNamespace(language="ar")

    monkeypatch.setattr(transcription, "_load_model", lambda _size: FakeModel())

    with pytest.raises(transcription.TranscriptionCancelled):
        transcription.transcribe_video_file(video, stop_event=stop_event)


def test_caps_transcript_length(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            return iter([SimpleNamespace(text="x" * (transcription.MAX_TRANSCRIPT_CHARS + 100))]), SimpleNamespace(language="en")

    monkeypatch.setattr(transcription, "_load_model", lambda _size: FakeModel())

    result = transcription.transcribe_video_file(video)

    assert len(result) == transcription.MAX_TRANSCRIPT_CHARS


def test_missing_faster_whisper_reports_setup_instructions(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    def fail(_model_name):
        raise ImportError("faster-whisper is unavailable")

    monkeypatch.setattr(transcription, "_load_model", fail)

    with pytest.raises(transcription.LocalTranscriptionError, match="setup.sh"):
        transcription.transcribe_video_file(video)
