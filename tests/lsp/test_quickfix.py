"""LSP-07c: protocol-independent quick fixes (`opencrbasic.quickfix`). Message families and ranking:
docs/lsp-api.md "Quick fixes (LSP-07c)"."""

import random
import time
from pathlib import Path

import pytest

from opencrbasic import bindings, navigation
from opencrbasic import quickfix as Q
from opencrbasic.analysis import AnalysisRequest, analyze

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT / "tests" / "corpus" / "firnmelt").resolve()

HEAD = "Public Temp, Humidity\nConst Kfoo = 5\nSub DoThing(p As Long)\n  Temp = p\nEndSub\n"


def prog(body: str) -> str:
    return HEAD + f"BeginProg\n  Scan(1,Sec,0,0)\n    {body}\n  NextScan\nEndProg\n"


def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def run(entry: Path, logger: str = "cr6", overlays=None):
    return analyze(AnalysisRequest(entry, logger, overlays=overlays or {}))


def actions_for(result, entry: Path, logger: str = "cr6"):
    out = []
    for d in result.diagnostics:
        out.extend((d, a) for a in Q.code_actions(result, entry, d, logger))
    return out


def fixed(entry: Path, action) -> bytes:
    return Q.apply_edits(entry.read_bytes(), action.edits)


def verdict_after(tmp_path: Path, entry: Path, action, logger: str = "cr6"):
    return run(entry, logger, {entry: fixed(entry, action)})


# ---------------------------------------------------------------- distance

def test_edit_distance():
    assert Q.edit_distance("Tmep", "Temp") == 1                   # a transposition is one edit
    assert Q.edit_distance("abc", "ABC") == 0                     # case-insensitive
    assert Q.edit_distance("Kfo", "Kfoo") == 1
    assert Q.edit_distance("", "ab") == 2
    assert Q.edit_distance("ca", "abc") == 2                      # unrestricted DL (restricted gives 3)
    assert Q.edit_distance("Humiditty", "Humidity") == 1


# ---------------------------------------------------------------- undefined symbol

def test_undefined_symbol_read(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Temp = Humiditty + Kfoo"))
    result = run(entry)
    assert not result.ok
    acts = actions_for(result, entry)
    assert acts, result.rendered_lines
    d, top = acts[0]
    assert d.message.startswith("Invalid symbol in expression: ")
    assert top.title == "Change to `Humidity`"
    assert len(top.edits) == 1 and top.edits[0].new_text == "Humidity"
    after = verdict_after(tmp_path, entry, top)
    assert after.ok, after.rendered_lines


def test_undefined_symbol_nearest_first_and_bounded(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Temp = Tmep + 1"))
    result = run(entry)
    titles = [a.title for _, a in actions_for(result, entry)]
    assert titles[0] == "Change to `Temp`"
    assert len(titles) <= Q.MAX_ACTIONS
    assert titles == [a.title for _, a in actions_for(run(entry), entry)]        # deterministic
    for _, a in actions_for(result, entry):
        assert Q.edit_distance("Tmep", a.edits[0].new_text) <= 2


def test_undefined_symbol_constant_and_catalog(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Temp = Humidity + Kfo"))
    (_, top), *_ = actions_for(run(entry), entry)
    assert top.edits[0].new_text == "Kfoo"
    assert verdict_after(tmp_path, entry, top).ok
    entry = write(tmp_path, "b.CR6", prog("Temp = Humidity + NAM"))
    titles = [a.title for _, a in actions_for(run(entry), entry)]
    assert "Change to `NAN`" in titles


def test_write_target_offers_only_assignable_names(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Tmep = 3"))
    result = run(entry)
    acts = actions_for(result, entry)
    assert [a.edits[0].new_text for _, a in acts][0] == "Temp"
    assert all(a.edits[0].new_text not in ("Kfoo",) for _, a in acts)
    assert verdict_after(tmp_path, entry, acts[0][1]).ok


def test_local_scope_is_respected(tmp_path):
    text = ("Public Glob, paras\nSub S1(par1 As Long)\n  Glob = par1\nEndSub\n"
            "BeginProg\n  Scan(1,Sec,0,0)\n    S1(1)\n    Glob = pars\n  NextScan\nEndProg\n")
    entry = write(tmp_path, "a.CR6", text)
    names = [a.edits[0].new_text for _, a in actions_for(run(entry), entry)]
    assert names[0] == "paras" and "par1" not in names     # S1's parameter is not visible here


# ---------------------------------------------------------------- undefined Sub / instruction

def test_misspelled_call_statement_user_sub(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("DoThng(Temp)"))
    result = run(entry)
    d, top = actions_for(result, entry)[0]
    assert d.message.startswith("Invalid, or out of place expression: ")
    assert top.edits[0].new_text == "DoThing"
    assert verdict_after(tmp_path, entry, top).ok


def test_misspelled_instruction_catalog(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Batery(Temp)"))
    result = run(entry)
    (d, top), *_ = actions_for(result, entry)
    assert top.edits[0].new_text == "Battery"
    after = verdict_after(tmp_path, entry, top)
    assert after.ok, after.rendered_lines


def test_explicit_call_offers_only_subs(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Call DoThng(Temp)"))
    result = run(entry)
    assert [d.message for d in result.diagnostics] == ["DoThng is not declared as a Subroutine."]
    acts = actions_for(result, entry)
    assert [a.edits[0].new_text for _, a in acts] == ["DoThing"]
    assert verdict_after(tmp_path, entry, acts[0][1]).ok


def test_sub_not_defined_without_any_sub(tmp_path):
    text = "Public Temp\nBeginProg\n  Scan(1,Sec,0,0)\n    Call Nope(Temp)\n  NextScan\nEndProg\n"
    entry = write(tmp_path, "a.CR6", text)
    result = run(entry)
    assert [d.message for d in result.diagnostics] == ["Sub Nope not defined."]
    assert actions_for(result, entry) == []                  # no Sub to offer


def test_sub_not_defined_message_family(tmp_path):
    # `Sub %s not defined.` carries the name; the fix is the declared Sub nearest to it.
    class D:
        message = "Sub DoThng not defined."
        line = 8
        span = (8, 8)
        path = None
    entry = write(tmp_path, "a.CR6", prog("DoThng(Temp)"))
    result = run(entry)
    d = D()
    d.path = entry
    acts = Q.code_actions(result, entry, d, "cr6")
    assert [a.edits[0].new_text for a in acts] == ["DoThing"]


def test_unknown_instruction_directive_has_no_action(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Temp = Kfoo\n    #Frob"))
    result = run(entry)
    msgs = [d.message for d in result.diagnostics]
    assert any(m.startswith("Unknown instruction: ") for m in msgs)
    assert actions_for(result, entry) == []


# ---------------------------------------------------------------- includes

def test_missing_include_near_name(tmp_path):
    write(tmp_path, "Lib.crb", "Public FromLib\n")
    write(tmp_path, "Other.crb", "Public Other\n")
    entry = write(tmp_path, "a.CR6", 'Include "Libb.crb"\n' + prog("FromLib = 1"))
    result = run(entry)
    assert any(d.message == "Cannot open include file  Libb.crb." for d in result.diagnostics)
    acts = actions_for(result, entry)
    assert [a.title for _, a in acts] == ["Change to `Lib.crb`"]
    edit = acts[0][1].edits[0]
    assert (edit.line, edit.start, edit.end) == (0, 9, 17)
    after = verdict_after(tmp_path, entry, acts[0][1])
    assert after.ok and not after.diagnostics, after.rendered_lines


def test_missing_include_case_and_drive_forms(tmp_path):
    write(tmp_path, "Lib.crb", "Public FromLib\n")
    entry = write(tmp_path, "a.CR6", 'Include "CPU:lbi.CRB"\n' + prog("FromLib = 1"))
    acts = actions_for(run(entry), entry)
    assert acts and acts[0][1].edits[0].new_text == "Lib.crb"
    acts[0][1].edits[0]
    assert fixed(entry, acts[0][1]).decode().startswith('Include "CPU:Lib.crb"')
    assert verdict_after(tmp_path, entry, acts[0][1]).ok


def test_missing_include_overlay_listing(tmp_path):
    entry = write(tmp_path, "a.CR6", 'Include "Libb.crb"\n' + prog("Temp = 1"))
    overlay = {tmp_path / "Lib.crb": b"Public FromLib\n"}          # unsaved buffer, not on disk
    result = run(entry, overlays=overlay)
    acts = actions_for(result, entry)
    assert [a.edits[0].new_text for _, a in acts] == ["Lib.crb"]


def test_missing_include_no_near_name(tmp_path):
    write(tmp_path, "Unrelated.crb", "Public X\n")
    entry = write(tmp_path, "a.CR6", 'Include "Lib.crb"\n' + prog("Temp = 1"))
    assert actions_for(run(entry), entry) == []


# ---------------------------------------------------------------- scope of the edit

def test_edit_in_included_file(tmp_path):
    lib = write(tmp_path, "Lib.crb", "Sub Libby()\n  Temp = Humiditty\nEndSub\n")
    entry = write(tmp_path, "a.CR6", HEAD.replace("Sub DoThing", "Sub DoThing2") +
                  'Include "Lib.crb"\nBeginProg\n  Scan(1,Sec,0,0)\n    Libby()\n  NextScan\nEndProg\n')
    result = run(entry)
    assert not result.ok
    here = [(d, a) for d in result.diagnostics for a in Q.code_actions(result, lib, d, "cr6")]
    assert here and here[0][1].edits[0].path == lib
    assert here[0][1].edits[0].new_text == "Humidity"
    assert actions_for(result, entry) == []             # nothing for the entry file's own text
    after = run(entry, overlays={lib: Q.apply_edits(lib.read_bytes(), here[0][1].edits)})
    assert after.ok, after.rendered_lines


def test_unrelated_and_rangeless_diagnostics_give_nothing(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Temp = 1\n    Temp = 2") + "")
    result = run(entry)
    assert result.ok and actions_for(result, entry) == []

    class D:
        message = "Invalid symbol in expression: x."
        line = 8
        span = None
        path = entry
    assert Q.code_actions(result, entry, D(), "cr6") == []
    assert Q.code_actions(result, entry, D, "cr6") == []


def test_wrong_file_gets_nothing(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Temp = Humiditty"))
    other = write(tmp_path, "b.CR6", "x\n")
    result = run(entry)
    assert all(Q.code_actions(result, other, d, "cr6") == [] for d in result.diagnostics)


def test_does_not_delete_code(tmp_path):
    entry = write(tmp_path, "a.CR6", prog("Temp = Humiditty + Tmep"))
    result = run(entry)
    for _, a in actions_for(result, entry):
        for e in a.edits:
            assert e.new_text and e.end > e.start


# ---------------------------------------------------------------- CR300 wording

def test_cr300_not_defined_message(tmp_path):
    entry = write(tmp_path, "a.CR300", prog("Temp = Humiditty"))
    result = run(entry, "cr300")
    assert any(d.message == "Humiditty is not defined." for d in result.diagnostics)
    acts = actions_for(result, entry, "cr300")
    assert acts and acts[0][1].edits[0].new_text == "Humidity"
    assert verdict_after(tmp_path, entry, acts[0][1], "cr300").ok


# ---------------------------------------------------------------- misspelling sweep

def _misspell(rng: random.Random, word: str) -> str:
    for _ in range(20):
        i = rng.randrange(len(word))
        op = rng.choice(("delete", "swap", "substitute", "insert"))
        if op == "delete":
            out = word[:i] + word[i + 1:]
        elif op == "swap" and i + 1 < len(word) and word[i] != word[i + 1]:
            out = word[:i] + word[i + 1] + word[i] + word[i + 2:]
        elif op == "substitute":
            out = word[:i] + rng.choice("abcdefghijklmnopqrstuvwxyz") + word[i + 1:]
        elif op == "insert":
            out = word[:i] + rng.choice("abcdefghijklmnopqrstuvwxyz") + word[i:]
        else:
            continue
        if out.upper() != word.upper():
            return out
    return word + "x"


def sweep(seed: str = "lsp-07c", budget: float = 25.0):
    """One seeded misspelled identifier per `crbasic/*.CR6` program. Returns per-program rows
    `(name, original, misspelled, outcome, rank)`; outcome is exact | case | other | none."""
    rows: list[tuple[str, str, str, str, int | None]] = []
    start = time.monotonic()
    for entry in sorted(CRBASIC.glob("*.CR6")):
        if time.monotonic() - start > budget:
            break
        rng = random.Random(f"{seed}:{entry.name}")
        base = analyze(AnalysisRequest(entry, "cr6"))
        if base.program is None:
            continue
        ix = bindings.build(base.program, "cr6")
        nav = navigation.navigation(base, ix)
        nav._build()
        data = entry.read_bytes()
        lines = data.replace(b"\r\n", b"\n").split(b"\n")
        picks = []
        for ref in ix.references:
            site = nav._site_of.get(id(ref))
            if (site is None or site.path is None or Path(site.path).name != entry.name
                    or ref.status != "resolved" or len(ref.name) < 4
                    or ref.role not in (bindings.READ, bindings.WRITE, bindings.CALL)):
                continue
            if lines[site.line][site.start:site.end].decode("latin-1") != ref.name:
                continue
            picks.append(site)
        if not picks:
            continue
        site = rng.choice(picks)
        original = lines[site.line][site.start:site.end].decode("latin-1")
        mis = _misspell(rng, original)
        edit = Q.Edit(entry, site.line, site.start, site.end, mis)
        mutated = Q.apply_edits(data.replace(b"\r\n", b"\n"), [edit])
        result = analyze(AnalysisRequest(entry, "cr6", overlays={entry: mutated}))
        got = [a for d in result.diagnostics for a in Q.code_actions(result, entry, d, "cr6")
               if (a.edits[0].line, a.edits[0].start) == (site.line, site.start)]
        if not got:
            rows.append((entry.name, original, mis, "none", None))
        else:
            texts = [a.edits[0].new_text for a in got]
            rank = next((i for i, t in enumerate(texts) if t.lower() == original.lower()), None)
            if texts[0] == original:
                outcome = "exact"
            elif texts[0].lower() == original.lower():
                outcome = "case"
            else:
                outcome = "other"
            rows.append((entry.name, original, mis, outcome, rank))
    return rows


@pytest.mark.slow
def test_misspelling_sweep():
    t0 = time.monotonic()
    rows = sweep()
    elapsed = time.monotonic() - t0
    assert rows
    top = sum(1 for r in rows if r[3] in ("exact", "case"))
    exact = sum(1 for r in rows if r[3] == "exact")
    print(f"\nSWEEP programs={len(rows)} top_exact={exact} top_case_insensitive={top} "
          f"in_top5_case_insensitive={sum(1 for r in rows if r[4] is not None)} elapsed={elapsed:.1f}s")
    for r in rows:
        print("  ", r)
    assert exact >= 0.8 * len(rows)
    assert elapsed < 40


def test_read_position_offers_no_statement_words(tmp_path):
    # review F15: `Do`, `For`, `Scan` have a catalog symbol/function entry beside the statement word
    entry = write(tmp_path, "a.CR6", prog("Temp = Fo + 1"))
    titles = [a.title for _, a in actions_for(run(entry), entry)]
    assert not {"Change to `Do`", "Change to `FOR`"} & set(titles), titles
    entry = write(tmp_path, "b.CR6", prog("Temp = Humiditty + 1"))
    assert actions_for(run(entry), entry)[0][1].title == "Change to `Humidity`"


def test_read_position_offers_only_the_declared_name_not_statement_words(tmp_path):
    entry = write(tmp_path, "c.CR6", "Public Fo2, x\nBeginProg\n  Scan(1,Sec,0,0)\n"
                  "    x = Fo + 1\n  NextScan\nEndProg\n")
    assert [a.title for _, a in actions_for(run(entry), entry)] == ["Change to `Fo2`"]
