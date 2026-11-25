"""Server transcripts for the editor features of the VS Code side bar and editor: unused hints,
reference-count code lenses, on-type case correction, the `auto` logger and the
`opencrbasic.instructions`, `opencrbasic.instructionHelp` and `opencrbasic.dataTables` commands."""

from opencrbasic import commands

from .test_server import FAST, make_client, project, started  # noqa: F401

HINTS = {**FAST, "unusedHints": True}

MAIN = """Public counter As Long
Public spare As Long
Include("CPU:Lib.crb")
DataTable(Hourly,True,-1)
  DataInterval(0,60,Min,10)
  Sample(1,counter,Long)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    counter = counter + lib_v
    CallTable Hourly
  NextScan
EndProg
"""
LIB = "Public lib_v As Long\n"


def _main(tmp_path, name="Main.CR6", text=MAIN):
    root = project(tmp_path, **{name: text, "Lib.crb": LIB})
    return root, root / name


def test_unused_hints_fade_exactly_the_unused_name(make_client, tmp_path):
    root, main = _main(tmp_path)
    client = started(make_client, root, HINTS)
    client.open(main, MAIN)
    params = client.wait_diagnostics(main, lambda d: bool(d))
    (diag,) = params["diagnostics"]
    assert diag["message"] == "Variable spare is declared but never used."
    assert diag["source"] == "opencrbasic (unused)" and diag["severity"] == 4 and diag["tags"] == [1]
    assert diag["range"] == {"start": {"line": 1, "character": 7}, "end": {"line": 1, "character": 12}}
    # a program with an error gets no unused hints (its references may be incomplete)
    client.change(main, MAIN.replace("counter + lib_v", "counter + nothing"), 2)
    params = client.wait_diagnostics(main, lambda d: any(x["severity"] == 1 for x in d), after=1)
    assert all(d["source"] != "opencrbasic (unused)" for d in params["diagnostics"])
    assert client.shutdown() == 0


def test_unused_hints_can_be_turned_off(make_client, tmp_path):
    root, main = _main(tmp_path)
    client = started(make_client, root, {**HINTS, "unusedHints": False})
    client.open(main, MAIN)
    params = client.wait_diagnostics(main)
    assert params["diagnostics"] == []
    assert client.shutdown() == 0


def test_code_lens_counts_match_find_references(make_client, tmp_path):
    root, main = _main(tmp_path)
    client = started(make_client, root)
    uri = client.open(main, MAIN)
    client.wait_diagnostics(main)
    lenses = client.request("textDocument/codeLens", {"textDocument": {"uri": uri}})
    by_line = {lens["range"]["start"]["line"]: lens for lens in lenses}
    assert set(by_line) == {0, 1, 3}  # counter, spare, Hourly
    for line, lens in by_line.items():
        start = lens["range"]["start"]
        refs = client.request(
            "textDocument/references",
            {"textDocument": {"uri": uri}, "position": start, "context": {"includeDeclaration": False}},
        )
        n = len(refs)
        assert lens["command"]["title"] == f"{n} reference{'' if n == 1 else 's'}", line
        assert lens["command"]["command"] == "opencrbasic.showReferences"
        assert lens["command"]["arguments"] == [uri, start]
    assert by_line[1]["command"]["title"] == "0 references"
    client.settings = {**FAST, "codeLens": {"references": False}}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_for(
        lambda: client.request("textDocument/codeLens", {"textDocument": {"uri": uri}}) == [], 30, "lenses off"
    )
    assert client.shutdown() == 0


def _typed(client, uri, line, character, ch):
    return client.request(
        "textDocument/onTypeFormatting",
        {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "ch": ch,
            "options": {"tabSize": 2, "insertSpaces": True},
        },
    )


def test_case_correction_on_type(make_client, tmp_path):
    root, main = _main(tmp_path)
    client = started(make_client, root)
    uri = client.open(main, MAIN)
    client.wait_diagnostics(main)
    typed = MAIN.replace("    counter = counter + lib_v", '    COUNTER = callTABLE "voltse" ')
    client.change(main, typed, 2)
    # a space after `COUNTER` (line 9, column 11): the declaration's spelling
    assert _typed(client, uri, 9, 12, " ") == [
        {"range": {"start": {"line": 9, "character": 4}, "end": {"line": 9, "character": 11}}, "newText": "counter"}
    ]
    # an instruction gets its catalog spelling; nothing inside a string
    assert _typed(client, uri, 9, 24, " ")[0]["newText"] == "CallTable"
    assert _typed(client, uri, 9, 33, " ") == []
    # Enter after a keyword respells the end of the line above
    client.change(main, MAIN.replace("BeginProg", "beginprog"), 3)
    assert _typed(client, uri, 8, 2, "\n") == [
        {"range": {"start": {"line": 7, "character": 0}, "end": {"line": 7, "character": 9}}, "newText": "BeginProg"}
    ]
    client.settings = {**FAST, "caseCorrection": False}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_for(lambda: _typed(client, uri, 8, 2, "\n") == [], 30, "case correction off")
    assert client.shutdown() == 0


def test_auto_logger_takes_the_profile_from_the_extension(make_client, tmp_path):
    root, main = _main(tmp_path, "Main.CR300")
    client = started(make_client, root)
    uri = client.open(main, MAIN)
    client.wait_diagnostics(main)
    listed = client.execute("opencrbasic.instructions", uri)
    assert [i["name"] for i in listed] == [i.name for i in commands.instruction_list("cr300")]
    assert [i["name"] for i in listed] != [i.name for i in commands.instruction_list("cr6")]
    volt = next(i for i in listed if i["name"].upper() == "VOLTSE")
    assert volt["kind"] == "instruction" and len(volt["params"]) == len(volt["optional"]) > 0
    assert client.shutdown() == 0


def test_instruction_help_is_the_hover_text(make_client, tmp_path):
    root, main = _main(tmp_path)
    client = started(make_client, root)
    uri = client.open(main, MAIN)
    client.wait_diagnostics(main)
    md = client.execute("opencrbasic.instructionHelp", uri, "voltse")
    assert md.startswith("**VoltSe** (instruction)") and "```crbasic" in md
    assert client.execute("opencrbasic.instructionHelp", uri, "NoSuchThing") is None
    assert client.shutdown() == 0


def test_data_tables_lists_fields_and_the_declaration(make_client, tmp_path):
    root, main = _main(tmp_path)
    client = started(make_client, root)
    uri = client.open(main, MAIN)
    client.wait_diagnostics(main)
    answer = client.execute("opencrbasic.dataTables", uri)
    assert answer["compiled"] is True
    hourly = next(t for t in answer["tables"] if t["name"] == "Hourly")
    assert hourly["interval"] == "60 MIN"
    assert hourly["location"] == {
        "uri": uri,
        "range": {"start": {"line": 3, "character": 10}, "end": {"line": 3, "character": 16}},
    }
    assert [f["name"] for f in hourly["fields"]] == ["counter"]
    assert hourly["fields"][0]["process"] == "Smp"
    assert client.shutdown() == 0
