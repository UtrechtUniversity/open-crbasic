"""`positions.decoded_position` (LSP position -> decoded column) and the feature position context."""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest
from lsprotocol import types

from opencrbasic import positions
from opencrbasic.lsp.features import Ctx

from .lspclient import LspClient
from .test_features_server import make_client, slice_of  # noqa: F401  (fixture and helper)

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


# ---- encoding replacement: positions follow the editor text (LSP-R2b, review finding 3) -------

LATIN1 = {"debounceMs": 0, "encoding": "latin-1"}
PROGRAM = 'Public s As String * 20\nPublic oldName\nBeginProg\ns = "😀" : oldName=1\nEndProg\n'
ROW = 's = "😀" : oldName=1'


def _apply(line: str, rng: dict, new: str) -> str:
    """The editor line after replacing the UTF-16 range `rng` with `new`."""
    raw = line.encode("utf-16-le")
    a, b = rng["start"]["character"], rng["end"]["character"]
    return (raw[:2 * a] + new.encode("utf-16-le") + raw[2 * b:]).decode("utf-16-le")


def _started(make_client, tmp_path: Path, text: str) -> tuple[LspClient, Path]:
    path = tmp_path / "M.CR6"
    path.write_text(text, encoding="utf-8")
    client = make_client()
    client.initialize(tmp_path, LATIN1)
    client.open(path, text)
    client.at("textDocument/definition", path, 1, 8)     # waits for the first analysis
    return client, path


def test_completion_edit_after_emoji_applies_to_the_editor_text(make_client, tmp_path):
    # review finding 3's stdio reproducer: the emoji is two UTF-16 units, one byte under latin-1
    client, path = _started(make_client, tmp_path, PROGRAM)
    row = 's = "😀" : oldN'
    typed = PROGRAM.replace(ROW, row)
    client.change(path, typed, 2)
    answer = client.at("textDocument/completion", path, 3, positions.units(row, "utf-16"))
    edit = next(i["textEdit"] for i in answer["items"] if i["label"] == "oldName")
    assert slice_of(typed, edit["range"]) == "oldN"
    assert edit["newText"] == "oldName"
    assert _apply(row, edit["range"], edit["newText"]) == 's = "😀" : oldName'


def test_hover_and_highlights_after_emoji_use_editor_columns(make_client, tmp_path):
    client, path = _started(make_client, tmp_path, PROGRAM)
    hover = client.at("textDocument/hover", path, 3, 13)
    assert slice_of(PROGRAM, hover["range"]) == "oldName"
    assert hover["range"]["start"]["character"] == 11                  # not 10
    highlights = client.at("textDocument/documentHighlight", path, 3, 13)
    on_line = [h["range"] for h in highlights if h["range"]["start"]["line"] == 3]
    assert [slice_of(PROGRAM, r) for r in on_line] == ["oldName"]


def test_rename_edits_after_emoji_use_editor_columns(make_client, tmp_path):
    client, path = _started(make_client, tmp_path, PROGRAM)
    result = client.request("textDocument/rename", {
        "textDocument": {"uri": path.as_uri()}, "position": {"line": 3, "character": 13},
        "newName": "newName"})
    edits = [e for change in result["documentChanges"] for e in change["edits"]]
    on_line = [e["range"] for e in edits if e["range"]["start"]["line"] == 3]
    assert [slice_of(PROGRAM, r) for r in on_line] == ["oldName"]
    assert on_line[0]["start"]["character"] == 11
    assert all(e["newText"] == "newName" for e in edits)


def test_replacement_warning_range_is_the_whole_editor_line(make_client, tmp_path):
    first = "Public s As String * 20 ' 😀"
    text = first + "\nBeginProg\nEndProg\n"
    client, path = _started(make_client, tmp_path, text)
    publish = client.wait_diagnostics(path, lambda ds: any("replaced" in d["message"] for d in ds))
    diag = next(d for d in publish["diagnostics"] if "replaced" in d["message"])
    assert diag["range"] == {"start": {"line": 0, "character": 0},
                             "end": {"line": 0, "character": positions.units(first, "utf-16")}}


def test_ctx_maps_an_open_document_through_its_editor_text():
    path = Path("/nowhere/x.cr6")
    text = 's = "😀" : x\n'
    data = positions.EditorBytes(text.encode("latin-1", errors="replace"), text)
    ctx = Ctx("cr6", "utf-16", "latin-1", {path: data}, {})
    assert ctx.decoded(path, types.Position(0, 11)) == (0, 10)
    assert ctx.position(path, 0, 10) == types.Position(0, 11)
    assert ctx.columns(path, 0, 10, 11) == types.Range(types.Position(0, 11), types.Position(0, 12))


def test_compiler_and_editor_columns_per_character():
    line = 's = "😀" : x'
    # latin-1 replaces the emoji with one byte: x is at editor unit 11 and byte 10
    assert positions.compiler_column(line, 11, encoding="utf-16", file_encoding="latin-1") == 10
    assert positions.editor_character(line, 10, encoding="utf-16", file_encoding="latin-1") == 11
    # utf-8 keeps the emoji's four bytes: x is at byte 13
    assert positions.compiler_column(line, 11, encoding="utf-16", file_encoding="utf-8") == 13
    assert positions.editor_character(line, 13, encoding="utf-16", file_encoding="utf-8") == 11
    # inside the surrogate pair snaps to the emoji's start (unit 5, byte 5); past the end clamps
    assert positions.compiler_column(line, 6, encoding="utf-16", file_encoding="latin-1") == 5
    assert positions.compiler_column(line, 99, encoding="utf-16", file_encoding="latin-1") == len(line)
    # the replaced emoji is one byte (byte 5) for two units (5 and 6); byte 6 is the closing quote
    assert positions.editor_character(line, 5, encoding="utf-16", file_encoding="latin-1") == 5
    assert positions.editor_character(line, 6, encoding="utf-16", file_encoding="latin-1") == 7
    assert positions.editor_character(line, 99, encoding="utf-16", file_encoding="latin-1") == 12


def test_editor_bytes_keep_their_text_through_pickling():
    data = positions.EditorBytes(b"ab", "t")
    again = pickle.loads(pickle.dumps(data))
    assert again == b"ab" and again.text == "t"
