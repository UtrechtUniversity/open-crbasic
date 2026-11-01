"""References across entries and the comment-line rename refusal over real stdio transcripts
(LSP-R4a, host findings H-08 and H-10). Two entries include one library that declares the name
both use."""

from pathlib import Path

import pytest

from .test_edits_server import REQUEST_FAILED, error_of, project, started
from .test_features_server import make_client  # noqa: F401

pytestmark = pytest.mark.server

USE = 'Include "Lib.crb"\nBeginProg\nsharedValue = 1\nEndProg\n'
COMMENTED = "' sharedValue in a comment\n" + USE


def spots(locations: list[dict]) -> list[tuple[str, int, int, int]]:
    return sorted(
        (
            Path(l["uri"]).name,
            l["range"]["start"]["line"],
            l["range"]["start"]["character"],
            l["range"]["end"]["character"],
        )
        for l in locations
    )


def shared(make_client, tmp_path, a_text: str = USE):
    root = project(tmp_path, A_CR6=a_text, B_CR6=USE, Lib_crb="Public sharedValue\n")
    a, b, lib = root / "A.CR6", root / "B.CR6", root / "Lib.crb"
    client = started(make_client, root)
    client.open(a)
    client.open(b)
    for path in (a, b):  # each entry's program is fresh once this answers
        client.on_file("textDocument/documentSymbol", path)
    return client, a, b, lib


def references(client, path: Path, line: int, character: int, declaration: bool = True):
    return spots(
        client.at("textDocument/references", path, line, character, context={"includeDeclaration": declaration})
    )


def test_references_from_any_entry_list_every_entry_of_the_declaration(make_client, tmp_path):
    client, a, b, lib = shared(make_client, tmp_path)
    want = [("A.CR6", 2, 0, 11), ("B.CR6", 2, 0, 11), ("Lib.crb", 0, 7, 18)]
    for path, line, ch in ((a, 2, 3), (b, 2, 3), (lib, 0, 9)):
        assert references(client, path, line, ch) == want
        assert references(client, path, line, ch, declaration=False) == want[:2]
    assert client.shutdown() == 0


def test_definition_and_highlights_from_the_library(make_client, tmp_path):
    """Per-document features need no other entry: the library's own ranges are the same in both."""
    client, a, b, lib = shared(make_client, tmp_path)
    for path in (a, b, lib):
        line, ch = (0, 9) if path == lib else (2, 3)
        assert spots(client.at("textDocument/definition", path, line, ch)) == [("Lib.crb", 0, 7, 18)]
    marks = client.at("textDocument/documentHighlight", lib, 0, 9)
    assert [(h["range"]["start"]["line"], h["range"]["start"]["character"]) for h in marks] == [(0, 7)]
    assert client.shutdown() == 0


def test_rename_in_a_comment_line_names_the_cause(make_client, tmp_path):
    client, a, _, _ = shared(make_client, tmp_path, COMMENTED)
    for method, extra in (("textDocument/prepareRename", {}), ("textDocument/rename", {"newName": "renamedValue"})):
        err = error_of(client, method, a, 0, 4, **extra)
        assert err["code"] == REQUEST_FAILED and err["data"] == {"reason": "not_a_name"}
        assert "not in code any entry parses" in err["message"]
        assert "depends on the file" not in err["message"]
    assert client.shutdown() == 0
