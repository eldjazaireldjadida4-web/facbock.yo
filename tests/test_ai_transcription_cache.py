from __future__ import annotations

import json

import fb_youtube_uploader_v49_pro as app


def test_ai_metadata_cache_tracks_transcript_hash_without_storing_transcript(monkeypatch):
    state = {"ai_cache": {}}
    generated_transcripts = []
    worker = object.__new__(app.App)
    worker._log = lambda *_args, **_kwargs: None
    monkeypatch.setattr(app, "load_state", lambda: state)
    monkeypatch.setattr(app, "save_state", lambda updated: state.update(updated))
    monkeypatch.setattr(app, "top_performing_titles", lambda *_args, **_kwargs: [])

    def generate_metadata(*_args, transcript_text="", **_kwargs):
        generated_transcripts.append(transcript_text)
        return {"title": "Original title", "description": "Transcript-informed description", "tags": []}

    monkeypatch.setattr(app, "ai_generate_metadata", generate_metadata)
    item = {"id": "video-1", "page": "Page"}
    settings = {}

    first = worker._ai_metadata(
        item, {"title": "Original title", "desc": "", "transcript": "first transcript"}, settings
    )
    cached = worker._ai_metadata(
        item, {"title": "Original title", "desc": "", "transcript": "first transcript"}, settings
    )
    refreshed = worker._ai_metadata(
        item, {"title": "Original title", "desc": "", "transcript": "changed transcript"}, settings
    )

    assert generated_transcripts == ["first transcript", "changed transcript"]
    assert first["description"] == cached["description"] == refreshed["description"]
    assert "first transcript" not in json.dumps(state)
    assert "changed transcript" not in json.dumps(state)
    assert state["ai_cache"]["video-1"]["_transcript_hash"]
