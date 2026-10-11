from __future__ import annotations

import hashlib
from pathlib import Path

from opencrbasic import source
from opencrbasic.sources import DiskSource, OverlaySource, RecordingSource


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_disk_source_read_and_list(tmp_path):
    (tmp_path / "a.CR6").write_bytes(b"x")
    (tmp_path / "Lib.crb").write_bytes(b"y")
    (tmp_path / "sub").mkdir()
    disk = DiskSource()
    assert disk.read(tmp_path / "a.CR6") == b"x"
    assert disk.read(tmp_path / "missing") is None
    assert disk.read(tmp_path / "sub") is None
    assert sorted(disk.list_dir(tmp_path)) == ["Lib.crb", "a.CR6"]
    assert disk.list_dir(tmp_path / "nodir") is None
    assert disk.list_dir(tmp_path / "a.CR6") is None


def test_overlay_wins_and_appears_in_listing(tmp_path):
    (tmp_path / "a.CR6").write_bytes(b"disk")
    overlay = OverlaySource(DiskSource(), {tmp_path / "a.CR6": b"edited", tmp_path / "New.crb": b"n"})
    assert overlay.read(tmp_path / "a.CR6") == b"edited"
    assert overlay.read(tmp_path / "New.crb") == b"n"
    assert overlay.read(tmp_path / "other") is None
    names = overlay.list_dir(tmp_path)
    assert names == ["a.CR6", "New.crb"]


def test_overlay_in_directory_missing_on_disk(tmp_path):
    ghost = tmp_path / "ghost"
    overlay = OverlaySource(DiskSource(), {ghost / "Lib.crb": b"n"})
    assert overlay.list_dir(ghost) == ["Lib.crb"]
    assert overlay.list_dir(tmp_path / "other") is None


def test_overlay_keys_are_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    overlay = OverlaySource(DiskSource(), {Path("rel.crb"): b"r"})
    assert overlay.read(tmp_path / "rel.crb") == b"r"


def test_recording_source(tmp_path):
    (tmp_path / "a.CR6").write_bytes(b"x")
    rec = RecordingSource(OverlaySource(DiskSource(), {tmp_path / "o.crb": b"o"}))
    rec.read(tmp_path / "a.CR6")
    rec.read(tmp_path / "o.crb")
    rec.read(tmp_path / "gone.crb")
    rec.list_dir(tmp_path)
    rec.list_dir(tmp_path / "nodir")
    assert rec.reads == {tmp_path / "a.CR6": _sha(b"x"), tmp_path / "o.crb": _sha(b"o"),
                         tmp_path / "gone.crb": None}
    assert rec.listings == {tmp_path: ("a.CR6", "o.crb"), tmp_path / "nodir": None}


def test_include_resolution_is_case_insensitive_through_overlay(tmp_path):
    overlay = OverlaySource(DiskSource(), {tmp_path / "MyLib.CRB": b"x"})
    assert source._resolve_include_path("CPU:mylib.crb", tmp_path, overlay) == tmp_path / "MyLib.CRB"
    assert source._resolve_include_path("nope.crb", tmp_path, overlay) is None


def test_include_resolution_default_is_disk(tmp_path):
    (tmp_path / "Lib.crb").write_bytes(b"x")
    assert source._resolve_include_path("lib.CRB", tmp_path) == tmp_path / "Lib.crb"
    assert source._resolve_include_path("lib.CRB", tmp_path / "nodir") is None
    assert source._resolve_include_path("lib.CRB", None) is None


def test_load_program_records_include_paths(tmp_path):
    (tmp_path / "Lib.crb").write_bytes(b"Public A\n")
    (tmp_path / "m.CR6").write_bytes(b'Include("CPU:lib.crb")\nBeginProg\nEndProg\n')
    _, diags = source.load_program(tmp_path / "m.CR6", "CR6")
    assert diags.include_paths == {"CPU:lib.crb": tmp_path / "Lib.crb"}
