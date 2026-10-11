"""`positions.decoded_position` (LSP position -> decoded column) and the feature position context."""

from __future__ import annotations

import pytest
from lsprotocol import types

from opencrbasic import positions
from opencrbasic.lsp.features import Ctx

TEXT = "a€b😀c\nplain\r\nÿ'é\n"
DATA = TEXT.encode("utf-8")
ENCODINGS = positions.POSITION_ENCODINGS


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_round_trip_on_every_character_boundary(encoding):
    lines = positions.physical_lines(DATA)
    for number, raw in enumerate(lines, start=1):
        text = raw.decode("utf-8")
        boundaries = [0]
        for ch in text:
            boundaries.append(boundaries[-1] + len(ch.encode("utf-8")))
        for col in boundaries:
            line0, character = positions.lsp_position(lines, number, col, encoding=encoding)
            assert positions.decoded_position(lines, line0, character, encoding=encoding) \
                == (number, col)


def test_known_values():
    lines = positions.physical_lines(DATA)
    # a, euro (3 bytes, 1 unit), b, emoji (4 bytes, 2 UTF-16 units / 1 code point / 4 UTF-8 bytes), c
    assert positions.decoded_position(lines, 0, 3, encoding="utf-16") == (1, 5)
    assert positions.decoded_position(lines, 0, 5, encoding="utf-16") == (1, 9)
    assert positions.decoded_position(lines, 0, 4, encoding="utf-32") == (1, 9)
    assert positions.decoded_position(lines, 0, 8, encoding="utf-8") == (1, 5)
    assert positions.decoded_position(lines, 0, 9, encoding="utf-8") == (1, 9)


def test_inside_a_character_snaps_to_its_start():
    lines = positions.physical_lines(DATA)
    assert positions.decoded_position(lines, 0, 4, encoding="utf-16") == (1, 5)   # mid surrogate pair
    assert positions.decoded_position(lines, 0, 3, encoding="utf-8") == (1, 1)    # mid euro sign


def test_clamps_and_errors():
    lines = positions.physical_lines(DATA)
    assert positions.decoded_position(lines, 1, 99) == (2, 5)
    assert positions.decoded_position(lines, 1, -3) == (2, 0)
    assert positions.decoded_position(DATA, 0, 0) == (1, 0)           # bytes are accepted too
    with pytest.raises(IndexError):
        positions.decoded_position(lines, 9, 0)
    with pytest.raises(ValueError):
        positions.decoded_position(lines, 0, 0, encoding="utf-7")


def test_other_file_encoding():
    data = "é'x".encode("cp1252")
    lines = positions.physical_lines(data)
    assert positions.decoded_position(lines, 0, 2, encoding="utf-16", editor_encoding="cp1252") == (1, 2)


def test_ctx_clamps_a_line_past_the_end_and_converts_back():
    ctx = Ctx("cr6", "utf-16", "utf-8", {}, {})
    ctx._blobs["/nowhere/x.cr6"] = DATA
    from pathlib import Path
    path = Path("/nowhere/x.cr6")
    assert ctx.decoded(path, types.Position(99, 0)) == (3, 0)
    assert ctx.decoded(path, types.Position(0, 5)) == (0, 9)
    assert ctx.position(path, 0, 9) == types.Position(0, 5)
    assert ctx.position(path, 99, 3) == types.Position(3, 0)
    assert ctx.position(path, 0, None) == types.Position(0, 6)
    assert ctx.decoded(Path("/nowhere/missing.cr6"), types.Position(0, 0)) is None


def test_completion_reports_a_capped_list_as_incomplete(monkeypatch):
    from dataclasses import dataclass
    from pathlib import Path

    from opencrbasic import editing
    from opencrbasic.lsp import features

    path = Path("/nowhere/x.cr6")
    ctx = Ctx("cr6", "utf-16", "utf-8", {path: b"BeginProg\n"}, {})
    real = editing.completion_candidates

    @dataclass(frozen=True)
    class Capped:                       # stands for an `editing.Completion` with is_incomplete
        inner: object
        is_incomplete: bool

        def __getattr__(self, name):
            return getattr(self.inner, name)

    for flag in (True, False):
        monkeypatch.setattr(editing, "completion_candidates",
                            lambda *a, _f=flag, **k: Capped(real(*a, **k), _f))
        got = features.completion(ctx, None, None, path, types.Position(1, 0))
        assert got.is_incomplete is flag and got.items
