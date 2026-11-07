# -*- coding: utf-8 -*-
"""اختبارات سقف ملفات المعرفات والمخزن المؤقت (v4.48.5)."""
import os

import pytest

import fb_youtube_uploader_v49_pro as app


@pytest.fixture()
def id_file(tmp_path):
    return str(tmp_path / "uploaded_ids.txt")


def test_trim_keeps_most_recent_entries(id_file):
    with open(id_file, "w", encoding="utf-8") as target:
        target.writelines(f"id{i}\n" for i in range(10))
    app._trim_id_file(id_file, max_entries=5)
    with open(id_file, "r", encoding="utf-8") as source:
        kept = [line.strip() for line in source if line.strip()]
    assert kept == ["id5", "id6", "id7", "id8", "id9"]


def test_trim_skips_small_files(id_file):
    with open(id_file, "w", encoding="utf-8") as target:
        target.write("a\nb\n")
    app._trim_id_file(id_file, max_entries=5)
    with open(id_file, "r", encoding="utf-8") as source:
        assert source.read() == "a\nb\n"


def test_append_dedupes_and_records(id_file):
    assert app._append_id(id_file, "vid1") is True
    assert app._append_id(id_file, "vid1") is False
    assert app._append_id(id_file, "vid2") is True
    with open(id_file, "r", encoding="utf-8") as source:
        assert [line.strip() for line in source if line.strip()] == ["vid1", "vid2"]


def test_load_populates_cache_and_append_reuses_it(id_file):
    with open(id_file, "w", encoding="utf-8") as target:
        target.write("vid1\n")
    app._load_id_set(id_file)
    assert "vid1" in app._ID_SET_CACHE[id_file]
    # الإلحاح يحدّث المخزن بدل إعادة القراءة من القرص.
    assert app._append_id(id_file, "vid2") is True
    assert app._ID_SET_CACHE[id_file] == frozenset({"vid1", "vid2"})


def test_append_invokes_trim(id_file, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_trim_id_file", lambda path, max_entries=None: calls.append(path))
    app._append_id(id_file, "vid1")
    assert calls == [id_file]


def test_trim_survives_unwritable_directory(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    try:
        app._trim_id_file(str(locked / "ids.txt"), max_entries=1)
    finally:
        os.chmod(locked, 0o700)
