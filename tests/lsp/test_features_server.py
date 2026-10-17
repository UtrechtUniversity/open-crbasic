"""Language features over real stdio transcripts (LSP-03b/04b/05b-wire).

The shared server works on a temporary copy of `../crbasic/` (entry `Main.CR6`); tests that edit
use their own small project. Expected values are computed from the document text or from the core
modules applied to the same program, so these tests check the adapter (positions, encodings,
scheduling), not CRBasic rules.
"""

import shutil
import time
from pathlib import Path

import pytest

from opencrbasic import editing, navigation
from opencrbasic.analysis import AnalysisRequest, analyze

from .lspclient import LspClient

pytestmark = pytest.mark.server

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()
CHM = ROOT.parent / "extracted_chm"
FAST = {"debounceMs": 50, "analysisTimeoutMs": 60000}

EXPECTED_CAPABILITIES = {
    "completionProvider", "hoverProvider", "signatureHelpProvider", "documentSymbolProvider",
    "foldingRangeProvider", "documentLinkProvider", "definitionProvider", "referencesProvider",
    "documentHighlightProvider", "semanticTokensProvider", "textDocumentSync",
    "positionEncoding", "executeCommandProvider", "workspace", "renameProvider",
    "documentFormattingProvider", "documentRangeFormattingProvider", "codeActionProvider",
    "inlayHintProvider"}

UNICODE = """Public x As Long
Public y As Long
Public s As String * 20
Const k = "é😀" : Public z As Long
BeginProg
  Scan(1,Sec,0,0)
    s = "é😀" : y = x + 1
    z = y
  NextScan
EndProg
"""


# ---- helpers ------------------------------------------------------------------------------


def locate(text: str, needle: str, nth: int = 0, inside: int = 0) -> tuple[int, int]:
    """(line, Python character) of the `nth` occurrence of `needle`, `inside` characters in."""
    start = -1
    for _ in range(nth + 1):
        start = text.index(needle, start + 1)
    line = text.count("\n", 0, start)
    return line, start - (text.rfind("\n", 0, start) + 1) + inside


def units(text_line: str, chars: int, encoding: str) -> int:
    prefix = text_line[:chars]
    if encoding == "utf-8":
        return len(prefix.encode("utf-8"))
    if encoding == "utf-32":
        return len(prefix)
    return len(prefix.encode("utf-16-le")) // 2


def slice_of(text: str, rng: dict, encoding: str = "utf-16") -> str:
    """The text a wire range covers, decoded back from the negotiated encoding."""
    lines = text.split("\n")
    assert rng["start"]["line"] == rng["end"]["line"], rng
    line = lines[rng["start"]["line"]]
    boundaries = {units(line, n, encoding): n for n in range(len(line) + 1)}
    return line[boundaries[rng["start"]["character"]]:boundaries[rng["end"]["character"]]]


@pytest.fixture
def make_client():
    clients: list[LspClient] = []

    def factory(**kwargs) -> LspClient:
        client = LspClient(**kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.kill()
        time.sleep(0.1)
        assert "Traceback" not in client.stderr_text, client.stderr_text


def copy_crbasic(tmp_path: Path) -> Path:
    root = tmp_path / "crbasic"
    shutil.copytree(CRBASIC, root, ignore=shutil.ignore_patterns("*.log"))
    return root


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """One server on a copy of crbasic/, Main.CR6 open, the first analysis finished."""
    root = copy_crbasic(tmp_path_factory.mktemp("shared"))
    client = LspClient()
    client.initialize(root, {**FAST, "entries": ["Main.CR6"]}, semantic_refresh=True)
    main = root / "Main.CR6"
    client.open(main)
    text = main.read_text(encoding="utf-8")
    # the first result-based request waits for the first analysis
    assert client.on_file("textDocument/documentSymbol", main)
    result = analyze(AnalysisRequest(main, "cr6"))
    yield client, root, main, text, result
    client.kill()
    assert "Traceback" not in client.stderr_text, client.stderr_text


def small_project(tmp_path: Path, text: str, name: str = "Small.CR6", **files: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / name).write_text(text, encoding="utf-8")
    for fname, content in files.items():
        (root / fname.replace("_", ".")).write_text(content, encoding="utf-8")
    return root


def started(make_client, root: Path, options=None, **init) -> LspClient:
    client = make_client()
    client.initialize(root, {**FAST, **(options or {})}, **init)
    return client


# ---- capabilities -------------------------------------------------------------------------


def test_advertised_capabilities(make_client, tmp_path):
    root = small_project(tmp_path, UNICODE)
    client = make_client()
    result = client.initialize(root, FAST)
    caps = result["capabilities"]
    assert set(caps) == EXPECTED_CAPABILITIES, set(caps) ^ EXPECTED_CAPABILITIES
    assert caps["completionProvider"]["triggerCharacters"] == ["."]
    assert caps["signatureHelpProvider"]["triggerCharacters"] == ["(", ","]
    legend = caps["semanticTokensProvider"]["legend"]
    assert legend["tokenTypes"] == list(navigation.TOKEN_TYPES)
    assert legend["tokenModifiers"] == list(navigation.TOKEN_MODIFIERS)
    assert caps["semanticTokensProvider"]["full"] is True
    assert caps["positionEncoding"] == "utf-16"
    assert client.shutdown() == 0


# ---- outline, folding, links, tokens on Main.CR6 ------------------------------------------


def wire_names(symbols: list[dict]) -> list:
    return [(s["name"], wire_names(s.get("children") or [])) for s in symbols]


def core_names(symbols) -> list:
    return [(s.name or s.kind, core_names(s.children)) for s in symbols]


def test_document_symbols_match_the_core_outline(shared):
    client, root, main, text, result = shared
    got = client.on_file("textDocument/documentSymbol", main)
    assert wire_names(got) == core_names(editing.document_symbols(result, main, "cr6"))
    assert any(s.get("children") for s in got)
    first = got[0]
    assert first["range"]["start"]["line"] <= first["selectionRange"]["start"]["line"]
    assert first["kind"] in range(1, 27)


def test_folding_ranges_match_the_core(shared):
    client, root, main, text, result = shared
    got = client.on_file("textDocument/foldingRange", main)
    want = editing.folding_ranges(text, "cr6")
    assert [(f["startLine"], f["endLine"], f.get("kind")) for f in got] \
        == [(f.start_line, f.end_line, f.kind) for f in want]
    assert got and {f.get("kind") for f in got} <= {"region", "comment"}


def test_document_links_cover_the_file_name(shared):
    client, root, main, text, result = shared
    got = client.on_file("textDocument/documentLink", main)
    want = [lnk for lnk in editing.include_links(result) if lnk.target is not None]
    assert len(got) == len(want) >= 20
    names = {slice_of(text, g["range"]) for g in got}
    assert "CPU:StationConfig.crb" in names
    targets = {Path(g["target"].removeprefix("file://")).name for g in got}
    assert "StationConfig.crb" in targets and "PowerProfileLib.crb" in targets
    for g in got:
        assert Path(g["target"].removeprefix("file://")).is_file()


def test_semantic_tokens_decode_to_declared_names(shared):
    client, root, main, text, result = shared
    got = client.on_file("textDocument/semanticTokens/full", main)["data"]
    want = navigation.semantic_tokens(result, main)
    assert len(got) == 5 * len(want) and want
    line = start = 0
    lines = text.split("\n")
    for i, tok in enumerate(want):
        dl, ds, length, kind, mods = got[5 * i:5 * i + 5]
        line, start = line + dl, (ds if dl else start + ds)
        assert (line, start, length) == (tok.line, tok.start, tok.length)    # Main.CR6 is ASCII
        assert navigation.TOKEN_TYPES[kind] == tok.type
        assert [m for j, m in enumerate(navigation.TOKEN_MODIFIERS) if mods >> j & 1] == list(tok.modifiers)
        assert lines[line][start:start + length].isidentifier() or lines[line][start] == '"'


def test_semantic_token_refresh_is_requested_after_an_analysis(shared):
    client, *_ = shared
    assert client.server_requests("workspace/semanticTokens/refresh")


def test_no_refresh_request_when_the_client_does_not_support_it(make_client, tmp_path):
    root = small_project(tmp_path, UNICODE)
    client = started(make_client, root)
    client.open(root / "Small.CR6", UNICODE)
    assert client.on_file("textDocument/documentSymbol", root / "Small.CR6")
    assert client.server_requests("workspace/semanticTokens/refresh") == []


# ---- hover, definition, references, highlights on Main.CR6 -------------------------------


def test_hover_instruction_and_declared_name(shared):
    client, root, main, text, result = shared
    line, col = locate(text, "BeginProg", inside=3)
    got = client.at("textDocument/hover", main, line, col)
    assert "beginprog" in got["contents"]["value"].lower() and got["contents"]["kind"] == "markdown"
    assert slice_of(text, got["range"]) == "BeginProg"
    # a declared name: the first Public of the file, used again further down
    decl_line, decl_col = locate(text, "Public ")
    name = text.split("\n")[decl_line][decl_col + 7:].split()[0].split("(")[0].rstrip(",")
    hovered = client.at("textDocument/hover", main, decl_line, decl_col + 8)
    assert hovered and name in hovered["contents"]["value"]


def test_hover_outside_code_is_null(shared):
    client, root, main, text, result = shared
    line, col = locate(text, "'")
    assert client.at("textDocument/hover", main, line, col + 1) is None


def first_cross_file_name(result, main: Path):
    """A name declared in an included library and referenced in Main.CR6."""
    nav = navigation.Navigation(result)
    for ref in nav.index.references:
        for decl in ref.resolved:
            if decl.scope == "global" and decl.file != main.name and decl.file.endswith(".crb") \
                    and ref.file.lower() == "main.cr6" and ref.status == "resolved":
                return ref.name
    return None


def test_definition_across_files(shared):
    client, root, main, text, result = shared
    name = first_cross_file_name(result, main)
    assert name, "no cross-file name found in Main.CR6"
    lines = text.split("\n")
    for ln, row in enumerate(lines):
        code = row.split("'")[0]
        pos = code.find(name)
        if pos >= 0 and (pos == 0 or not (code[pos - 1].isalnum() or code[pos - 1] in "_.")) \
                and not code.lstrip().upper().startswith(("PUBLIC", "DIM", "INCLUDE")):
            got = client.at("textDocument/definition", main, ln, pos + 1)
            if got:
                break
    else:
        pytest.fail(f"no use of {name} gave a definition")
    assert len(got) >= 1
    target = Path(got[0]["uri"].removeprefix("file://"))
    assert target.suffix.lower() == ".crb" and target.parent == root
    assert slice_of(target.read_text(encoding="utf-8"), got[0]["range"]).lower() == name.lower()


def test_references_and_highlights_agree_with_the_core(shared):
    client, root, main, text, result = shared
    name = first_cross_file_name(result, main)
    ln, col = next((ln, row.split("'")[0].find(name)) for ln, row in enumerate(text.split("\n"))
                   if row.split("'")[0].find(name) >= 0
                   and navigation.definition(result, main, ln, row.split("'")[0].find(name) + 1))
    refs = client.at("textDocument/references", main, ln, col + 1, context={"includeDeclaration": True})
    want = navigation.references(result, main, ln, col + 1, True)
    assert len(refs) == len(want) >= 2
    no_decl = client.at("textDocument/references", main, ln, col + 1,
                        context={"includeDeclaration": False})
    assert len(no_decl) < len(refs)
    lights = client.at("textDocument/documentHighlight", main, ln, col + 1)
    core = navigation.document_highlights(result, main, ln, col + 1)
    assert [(h["range"]["start"]["line"], h["range"]["start"]["character"], h["kind"]) for h in lights] \
        == [(h.line, h.start, {"text": 1, "read": 2, "write": 3}[h.kind]) for h in core]
    for h in lights:
        assert slice_of(text, h["range"]).lower() == name.lower()


def test_crb_document_uses_the_first_including_entry(shared):
    client, root, main, text, result = shared
    lib = root / "ShuntLib.crb"
    client.open(lib)
    symbols = client.on_file("textDocument/documentSymbol", lib)
    assert symbols, "the .crb should get its outline from Main.CR6's program"
    tokens = client.on_file("textDocument/semanticTokens/full", lib)["data"]
    assert tokens and len(tokens) % 5 == 0
    links = client.on_file("textDocument/documentLink", lib)
    assert isinstance(links, list)
    client.close(lib)


def test_request_for_a_file_no_entry_includes_answers_empty(shared):
    client, root, main, text, result = shared
    stray = root / "Stray.crb"
    stray.write_text("Public stray_v As Long\n")
    client.open(stray)
    assert client.on_file("textDocument/documentSymbol", stray) == []
    assert client.at("textDocument/definition", stray, 0, 8) in ([], None)
    assert client.on_file("textDocument/semanticTokens/full", stray)["data"] == []
    assert client.on_file("textDocument/foldingRange", stray) == []
    client.close(stray)


def test_unknown_document_answers_without_error(shared):
    client, root, main, text, result = shared
    ghost = root / "Ghost.CR6"
    assert client.at("textDocument/hover", ghost, 0, 0) is None
    assert client.at("textDocument/completion", ghost, 0, 0)["items"] == []
    assert client.at("textDocument/signatureHelp", ghost, 0, 0) is None
    assert client.on_file("textDocument/foldingRange", ghost) == []


# ---- completion and signature help on incomplete edits ------------------------------------

EDIT = """Public x As Long
Public Volts As Float
BeginProg
  Scan(1,Sec,0,0)
    x = Vo
    VoltSe(Volts,1,
  NextScan
EndProg
"""


def test_completion_on_an_incomplete_edit(make_client, tmp_path):
    root = small_project(tmp_path, EDIT)
    entry = root / "Small.CR6"
    client = started(make_client, root)
    client.open(entry, EDIT)
    assert client.on_file("textDocument/documentSymbol", entry)          # analysis finished
    line, col = locate(EDIT, "= Vo", inside=4)
    got = client.at("textDocument/completion", entry, line, col)
    labels = [i["label"] for i in got["items"]]
    assert "VoltSe" in labels and "VoltDiff" in labels and "Volts" in labels
    assert labels.index("Volts") < labels.index("VoltSe")       # program names first
    volt = next(i for i in got["items"] if i["label"] == "VoltSe")
    assert volt["kind"] == 3 and "parameters" in volt["detail"]
    assert slice_of(EDIT, volt["textEdit"]["range"]) == "Vo"
    assert volt["textEdit"]["newText"] == "VoltSe"


@pytest.mark.parametrize("table, expected", [("Public", {"x", "Volts"}), ("Status", {"Battery"})])
def test_completion_after_a_dot_offers_table_fields(make_client, tmp_path, table, expected):
    text = EDIT.replace("x = Vo", f"x = {table}.")
    root = small_project(tmp_path, text)
    entry = root / "Small.CR6"
    client = started(make_client, root)
    client.open(entry, text)
    assert client.on_file("textDocument/documentSymbol", entry)           # analysis finished
    line, col = locate(text, f"{table}.", inside=len(table) + 1)
    got = client.at("textDocument/completion", entry, line, col, context={
        "triggerKind": 2, "triggerCharacter": "."})
    assert expected <= {i["label"] for i in got["items"]}
    assert {i["kind"] for i in got["items"]} == {5}
    assert all(i["textEdit"]["range"]["start"] == i["textEdit"]["range"]["end"] for i in got["items"])


def test_signature_help_in_an_unclosed_call(make_client, tmp_path):
    root = small_project(tmp_path, EDIT)
    entry = root / "Small.CR6"
    client = started(make_client, root)
    client.open(entry, EDIT)
    line, col = locate(EDIT, "VoltSe(Volts,1,", inside=len("VoltSe(Volts,1,"))
    got = client.at("textDocument/signatureHelp", entry, line, col)
    sig = got["signatures"][0]
    assert sig["label"].startswith("VoltSe(") and len(sig["parameters"]) == 9
    assert got["activeParameter"] == sig["activeParameter"] == 2
    label_start, label_end = sig["parameters"][2]["label"]
    assert sig["label"][label_start:label_end] == "arg3"
    outside = client.at("textDocument/signatureHelp", entry, 0, 3)
    assert outside is None


@pytest.mark.skipif(not CHM.is_dir(), reason="extracted_chm not present")
def test_help_directory_gives_help_text_in_hover_and_signature(make_client, tmp_path):
    root = small_project(tmp_path, EDIT)
    entry = root / "Small.CR6"
    client = started(make_client, root, {"helpDirectory": str(CHM)})
    client.open(entry, EDIT)
    line, col = locate(EDIT, "VoltSe(", inside=2)
    hov = client.at("textDocument/hover", entry, line, col)
    assert "help page" in hov["contents"]["value"]
    sig = client.at("textDocument/signatureHelp", entry, line, col + 5)
    assert sig["signatures"][0]["parameters"][0]["label"] != [0, 0]
    assert sig["signatures"][0]["parameters"][0]["documentation"]["value"]


def test_help_directory_setting_is_optional_and_relative_to_the_folder(make_client, tmp_path):
    root = small_project(tmp_path, EDIT)
    entry = root / "Small.CR6"
    (root / "help" / "Instructions").mkdir(parents=True)
    client = started(make_client, root, {"helpDirectory": "help"})
    client.open(entry, EDIT)
    line, col = locate(EDIT, "VoltSe(", inside=2)
    hov = client.at("textDocument/hover", entry, line, col)
    assert "No help page for this name" in hov["contents"]["value"]
    # a configuration change back to none
    client.settings = {"helpDirectory": ""}
    client.notify("workspace/didChangeConfiguration", {"settings": {}})
    hov = client.wait_for(lambda: _hover_says(client, entry, line, col, "No help directory configured"),
                          what="hover without a help directory")
    assert hov


def _hover_says(client, entry, line, col, text) -> bool:
    got = client.at("textDocument/hover", entry, line, col)
    return bool(got) and text in got["contents"]["value"]


# ---- encodings ----------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8", "utf-32"])
def test_positions_follow_the_negotiated_encoding(make_client, tmp_path, encoding):
    root = small_project(tmp_path, UNICODE)
    entry = root / "Small.CR6"
    client = make_client()
    offered = [encoding] + [e for e in ("utf-16", "utf-8") if e != encoding]
    result = client.initialize(root, FAST, position_encodings=offered)
    assert result["capabilities"]["positionEncoding"] == encoding
    client.open(entry, UNICODE)
    lines = UNICODE.split("\n")
    use_line, use_col = locate(UNICODE, "y = x + 1", inside=0)
    wire_col = units(lines[use_line], use_col, encoding)
    got = client.at("textDocument/definition", entry, use_line, wire_col + 0)
    assert len(got) == 1 and got[0]["range"]["start"] == {"line": 1, "character": 7}
    refs = client.at("textDocument/references", entry, use_line, wire_col,
                     context={"includeDeclaration": True})
    on_line = [r["range"] for r in refs if r["range"]["start"]["line"] == use_line]
    assert [slice_of(UNICODE, r, encoding) for r in on_line] == ["y"]
    assert on_line[0]["start"]["character"] == wire_col
    lights = client.at("textDocument/documentHighlight", entry, use_line, wire_col)
    assert {slice_of(UNICODE, h["range"], encoding) for h in lights} == {"y"}
    hov = client.at("textDocument/hover", entry, use_line, wire_col)
    assert slice_of(UNICODE, hov["range"], encoding) == "y"
    # a token on the same line after the non-ASCII text, and one in a later colon statement
    tokens = client.on_file("textDocument/semanticTokens/full", entry)["data"]
    seen, line, start = [], 0, 0
    for i in range(0, len(tokens), 5):
        dl, ds, length, *_ = tokens[i:i + 5]
        line, start = line + dl, (ds if dl else start + ds)
        row = lines[line]
        boundaries = {units(row, n, encoding): n for n in range(len(row) + 1)}
        seen.append((line, row[boundaries[start]:boundaries[start + length]]))
    assert (3, "z") in seen and (0, "x") in seen
    assert client.shutdown() == 0


def test_completion_range_is_in_negotiated_units(make_client, tmp_path):
    text = 'Public x As Long\nBeginProg\n  Scan(1,Sec,0,0)\n    x = Len("é😀") + Vo\n  NextScan\nEndProg\n'
    root = small_project(tmp_path, text)
    entry = root / "Small.CR6"
    for encoding in ("utf-16", "utf-8"):
        client = make_client()
        client.initialize(root, FAST, position_encodings=[encoding])
        client.open(entry, text)
        line, col = locate(text, "+ Vo", inside=4)
        wire = units(text.split("\n")[line], col, encoding)
        got = client.at("textDocument/completion", entry, line, wire)
        item = next(i for i in got["items"] if i["label"] == "VoltSe")
        assert slice_of(text, item["textEdit"]["range"], encoding) == "Vo"
        client.shutdown()


# ---- analysis scheduling ------------------------------------------------------------------


def test_requests_during_a_pending_first_analysis(make_client, tmp_path):
    root = small_project(tmp_path, EDIT)
    entry = root / "Small.CR6"
    client = make_client(env={"OPENCRBASIC_LSP_TEST_SLEEP": "Small:3"})
    client.initialize(root, {**FAST, "featureWaitMs": 200})
    client.open(entry, EDIT)
    line, col = locate(EDIT, "BeginProg", inside=2)
    began = time.monotonic()
    hov = client.at("textDocument/hover", entry, line, col)           # text based: no waiting
    assert hov and "beginprog" in hov["contents"]["value"].lower()
    assert client.at("textDocument/completion", entry, line, col)["items"]
    assert client.on_file("textDocument/foldingRange", entry) is not None
    assert time.monotonic() - began < 2.5, "text-based requests must not wait for the analysis"
    # a result-based request has no program yet: it waits for the first analysis, then answers
    symbols = client.on_file("textDocument/documentSymbol", entry)
    assert symbols and time.monotonic() - began >= 2.5


def test_stale_program_is_used_while_a_newer_analysis_is_pending(make_client, tmp_path):
    root = small_project(tmp_path, EDIT)
    entry = root / "Small.CR6"
    client = make_client(env={"OPENCRBASIC_LSP_TEST_SLEEP": "Small:2.5"})
    client.initialize(root, {**FAST, "featureWaitMs": 200})
    client.open(entry, EDIT)
    first = client.on_file("textDocument/documentSymbol", entry)
    assert first
    edited = "Public added_name As Long\n" + EDIT
    client.change(entry, edited, 2)
    began = time.monotonic()
    stale = client.on_file("textDocument/documentSymbol", entry)
    waited = time.monotonic() - began
    assert 0.15 <= waited < 2.0, waited                     # bounded wait, then the last program
    assert [s["name"] for s in stale] == [s["name"] for s in first]
    # text based requests see the new text at once, and names from the old program
    line, col = locate(edited, "x = Vo", inside=6)
    got = client.at("textDocument/completion", entry, line, col)
    assert "Volts" in [i["label"] for i in got["items"]]
    # once the new analysis lands the outline has the new declaration
    fresh = client.wait_for(
        lambda: (lambda s: s if any(i["name"] == "added_name" for i in s) else None)(
            client.on_file("textDocument/documentSymbol", entry)), what="fresh outline")
    assert fresh


def test_feature_requests_survive_a_worker_crash(make_client, tmp_path):
    root = small_project(tmp_path, EDIT)
    entry = root / "Small.CR6"
    client = make_client(env={"OPENCRBASIC_LSP_TEST_CRASH": "Small"})
    client.initialize(root, FAST)
    client.open(entry, EDIT)
    client.wait_diagnostics(entry, lambda d: any("terminated" in x["message"] for x in d))
    assert client.on_file("textDocument/documentSymbol", entry) == []
    assert client.at("textDocument/definition", entry, 3, 3) == []
    line, col = locate(EDIT, "BeginProg", inside=2)
    assert client.at("textDocument/hover", entry, line, col)
    assert client.at("textDocument/completion", entry, line, col)["items"]
    assert client.shutdown() == 0


# ---- diagnostics use the negotiated encoding (review finding 3) ---------------------------

DIAG_LINE = "x = Missing ' é😀"
DIAG_TEXT = ("Public x As Long\nBeginProg\n  Scan(1,Sec,0,0)\n    " + DIAG_LINE
             + "\n  NextScan\nEndProg\n")


@pytest.mark.parametrize("encoding, expected", [("utf-16", 17), ("utf-8", 20), ("utf-32", 16)])
def test_diagnostic_end_uses_the_negotiated_encoding(make_client, tmp_path, encoding, expected):
    root = small_project(tmp_path, DIAG_TEXT)
    entry = root / "Small.CR6"
    client = make_client()
    result = client.initialize(root, FAST, position_encodings=[encoding, "utf-16"])
    assert result["capabilities"]["positionEncoding"] == encoding
    client.open(entry, DIAG_TEXT)
    params = client.wait_diagnostics(entry, lambda d: any(x["range"]["end"]["line"] == 3 for x in d))
    ends = [x["range"]["end"]["character"] for x in params["diagnostics"]
            if x["range"]["end"]["line"] == 3]
    assert ends and set(ends) == {4 + expected}, ends
