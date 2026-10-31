"""The CR6 evidence inventory and its coverage notices (M4, `docs/inventory.md`)."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from opencrbasic import checks, profiles
from opencrbasic.compiler import compile_file

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = json.loads((ROOT / "docs" / "inventory-cr6.json").read_text(encoding="utf-8"))
NOTICES = json.loads((ROOT / "src" / "opencrbasic" / "data" / "notices_cr6.json").read_text(encoding="utf-8"))
STATES = {"tested", "partially tested", "unknown"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inventory_is_pinned_to_the_current_inputs():
    # regenerate with `tools/inventory.py` when the binary or the instruction table changes
    assert INVENTORY["binary"]["sha256"] == _sha(ROOT.parent / "compiler" / "CR6Comp.exe")
    assert INVENTORY["instructions_json_sha256"] == _sha(ROOT / "src" / "opencrbasic" / "data" / "instructions.json")
    assert {k: NOTICES[k] for k in ("binary", "decompile", "instructions_json_sha256")} == \
        {k: INVENTORY[k] for k in ("binary", "decompile", "instructions_json_sha256")}


def test_inventory_covers_every_instruction_with_a_state():
    names = [r["name"] for r in json.loads((ROOT / "src" / "opencrbasic" / "data" / "instructions.json").read_text())]
    assert [e["name"] for e in INVENTORY["instructions"]] == names
    for e in INVENTORY["instructions"]:
        assert e["state"] in STATES
        assert all(m["state"] in STATES for m in e["messages"])
        if e["builder"] is None or not e["observations"]["count"]:
            assert e["state"] == "unknown"
        if e["state"] == "tested":   # at least one message, every one resolved and tested
            assert e["messages"] and all(m["text"] is not None for m in e["messages"])


def test_notice_table_is_the_unimplemented_inventory_messages():
    expected = {e["name"].upper(): sorted({m["text"] for m in e["messages"] if m["text"] and not m["implemented"]})
                for e in INVENTORY["instructions"]}
    assert NOTICES["missing"] == {k: v for k, v in expected.items() if v}


def _compile(tmp_path, body, logger="cr6"):
    program = tmp_path / "t.CR6"
    program.write_text(f"Public X\nBeginProg\n  Scan(1,Sec,0,0)\n{body}\n  NextScan\nEndProg\n")
    return compile_file(program, profiles.get(logger))


def test_notices_name_the_missing_rules_once_per_instruction(tmp_path):
    result = _compile(tmp_path, "    VoltSE(X,1,mV5000,U1,1,0,15000,1,0)\n" * 2)
    found = [n for n in checks.notices(result.program_ast, result.profile) if n["instruction"].upper() == "VOLTSE"]
    assert len(found) == 1 and found[0]["line"] == 4
    assert found[0]["rules"] == NOTICES["missing"]["VOLTSE"]
    assert result.ok and not result.findings   # information only: the verdict is unchanged


def test_no_notices_outside_cr6(tmp_path):
    result = _compile(tmp_path, "    VoltSE(X,1,mV5000,1,1,0,15000,1,0)", logger="cr1000x")
    assert checks.notices(result.program_ast, result.profile) == []


# CR6 replay false accepts listed by identity since rounds 34/35 (PROGRESS "Next steps", M1): the
# reference's first error is one of the rules a notice names. The other listed ones fail on a
# message opencrbasic emits for other triggers, one the inventory does not reach, or are CR1000X
# reproducers (`docs/inventory.md` "Coverage of the listed false accepts").
NOTICED = ["7b4b2177d56b", "4156d89a014d", "72f95eac103f", "77636b10567c",
           "7f9efa8af42c", "c060e9f1402f", "d12785e05faf", "52138d7bd179", "f61f825392f2"]
SPEC = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?(?:l|h)?[dsfgxXuc]")


@pytest.mark.parametrize("identity", NOTICED)
def test_listed_false_accepts_get_a_notice_naming_their_rule(identity):
    record_path = next((ROOT / "tests" / "corpus" / "fuzz" / "cr6").rglob(f"{identity}.json"))
    record = json.loads(record_path.read_text())
    result = compile_file(record_path.with_suffix(".CR6"), profiles.get("cr6"))
    first = next(line.split(": ", 1)[1] for line in record["theirs"].splitlines() if line.startswith("line "))
    rules = [r for n in checks.notices(result.program_ast, result.profile) for r in n["rules"]]
    assert result.ok and any(re.fullmatch(".*?".join(map(re.escape, SPEC.split(r))), first.strip(), re.S)
                             for r in rules), (first, rules)


@pytest.mark.parametrize("instruction, message", [
    ("SCAN", "Scan Interval parameter must be a non-negative integer"),
    ("SCAN", "Scan BufferOption parameter must be a non-negative integer"),
    ("EXITDO", "Exit Do without DO."),
    ("EXITFOR", "Exit For without FOR."),
    ("EXITSCAN", "ExitScan not within a Scan."),
])
def test_templated_messages_are_not_noticed(instruction, message):
    # emitted from f-strings (`instr_rules.py`, `structure.py`); found by the Codex M4 review
    assert message not in NOTICES["missing"].get(instruction, [])


def test_string_symbols_with_commas_resolve():
    # `s_When_channel_<_0,_measurements_p_0065c788` (Codex M4 follow-up review)
    brhalf = next(e for e in INVENTORY["instructions"] if e["name"].upper() == "BRHALF")
    assert "When channel < 0, measurements per excitation must equal reps." in [m["text"] for m in brhalf["messages"]]
