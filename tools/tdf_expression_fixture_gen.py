"""Record or audit expression-source TDF observations from all four reference compilers.

Run from opencrbasic/: `uv run tools/tdf_expression_fixture_gen.py [--audit] [--no-cache]`.
Only user-table bytes are compared; the existing per-profile Status-table gap is separate.
"""

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src")]
import tdftest  # noqa: E402

from opencrbasic import profiles, tdf  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "tdf_expressions.json"
LOGGERS = ("cr6", "cr1000x", "cr300", "cr350")


def program(body, declarations="Public X,N,S As String * 8\n", extra_tables=""):
    return (
        declarations + extra_tables + "DataTable(A,1,10)\n" + body + "\nEndTable\n"
        "BeginProg\nScan(1,Sec,0,0)\nCallTable A\nNextScan\nEndProg\n"
    )


def samples(expressions):
    return "\n".join(f"Sample(1,{expression},String)" for expression in expressions)


CASES = {
    "substring_buffers": program(
        samples(
            [
                "Left(S,2)",
                "Left(S,200)",
                "Left(S,0)",
                "Left(S,N)",
                "Right(S,2)",
                "Mid(S,1,2)",
                "UpperCase(S)",
                "LowerCase(S)",
                "Trim(S)",
                "LTrim(S)",
                "RTrim(S)",
            ]
        )
    ),
    "nested_buffers": program(
        samples(
            [
                'Left("abc",2)',
                'Mid("abcdefgh",2,2)',
                "Left(Left(S,2),1)",
                'Left(S,2)+"ab"',
                "S+Left(S,2)",
                'Left(S+"abc",2)',
                "Left(42,2)",
            ]
        )
    ),
    "replace_buffers": program(
        samples(
            [
                'Replace(S,"a","abcdef")',
                'Replace(S,"ab","c")',
                'Replace(S,"a",S)',
                'Replace(Left(S,2),"a","abc")',
            ]
        )
    ),
    "format_buffers": program(
        samples(
            [
                'FormatFloat(X,"%2.2f")',
                'FormatLong(42,"%d")',
                'FormatLongLong("%lld")',
                "FormatLongLong(S)",
                "FormatFloat(X,S)",
                "IPInfo(0,0)",
            ]
        )
    ),
    "single_argument_index": program(samples(["Chr(65)", "Hex(255)", "Chr(N)", "Hex(X)"])),
    "empty_and_signed_buffers": program(
        samples(
            [
                'Left("",0)',
                'Trim("42")',
                "Chr(0)",
                "Chr(-1)",
                "Hex(-1)",
            ]
        )
    ),
    "empty_replace_buffer": program('Sample(1,Replace("","a",""),String)'),
    "string_array_source": program(samples(["Left(S(2),2)", "Trim(S(1))"]), "Public S(3) As String * 8\n"),
    "fieldnames_buffer": program('Sample(1,Left(S,2),String)\nFieldNames("Text,Ignored")\nSample(1,"next",String)'),
    "status_fields": program('Sample(1,status.bAtTeRy,IEEE4)\nAverage(1,Status.PanelTemp,FP2,0)\nSample(1,"x",String)'),
    "status_units_shadow": program("Sample(1,Status.Battery,IEEE4)", "Public Battery\nUnits Battery = test\n"),
    "status_duplicate": program("Sample(1,Status.Battery,IEEE4)\nSample(1,Status.Battery,IEEE4)"),
    "status_public_duplicate": program("Sample(1,Status.Battery,IEEE4)\nSample(1,Battery,IEEE4)", "Public Battery\n"),
    "structure_members": program(
        'Sample(1,V.value,FP2)\nSample(1,V.label,String)\nSample(1,"x",String)',
        "StructureType Kind\nValue As Float\nUnits Value = test\n"
        "Label As String * 8\nEndStructureType\nPublic V As Kind\n",
    ),
    "structure_duplicate": program(
        "Sample(1,V.Value,FP2)\nSample(1,V.Value,FP2)",
        "StructureType Kind\nValue As Float\nEndStructureType\nPublic V As Kind\n",
    ),
    "structure_rows": program(
        "Sample(1,V(1).Value,FP2)\nSample(1,V(2).Value,FP2)",
        "StructureType Kind\nValue As Float\nEndStructureType\nPublic V(2) As Kind\n",
    ),
    "table_string_sources": program(
        "Sample(1,B.S,String)\nSample(1,Left(B.S,2),String)",
        extra_tables="DataTable(B,1,10)\nSample(1,S,String)\nEndTable\n",
    ),
    "status_string_source": program("Sample(1,Status.StationName,String)"),
    "auto_string_buffer": program("DataInterval(0,5,Sec,10)\nSample(1,Left(S,2),String)\nSample(1,X,FP2)").replace(
        "DataTable(A,1,10)", "DataTable(A,1,-1)"
    ),
}
for size in (1, 3, 4, 7, 20, 60):
    CASES[f"string_width_{size}"] = program("Sample(1,Left(S,2),String)", f"Public S As String * {size}\n")


def user_tdf(tables, names):
    return tdf.serialize([table for table in tables if table.name.upper() in names]).hex()


def observe(case_id, source, logger, use_cache):
    profile = profiles.get(logger)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "t.CR6"
        path.write_text(source, encoding="utf-8")
        result = None
        for attempt in range(2):
            try:
                result = tdftest.run_oracle_tdf(
                    path, Path(directory), ROOT / "compiler" / profile.exe_name, use_cache=use_cache
                )
                break
            except tdftest.oracle.OracleError:
                if attempt:
                    raise
    assert result is not None  # (the second failed attempt re-raised)
    case = {
        "id": f"{logger}-{case_id}",
        "logger": logger,
        "source": source,
        "expected_exit": result["exit"],
        "expected_stdout": result["stdout"].rstrip("\n"),
    }
    if result["ok"] and (blob := result["tdf"]) is not None:
        names = re.findall(r"(?im)^\s*DataTable\s*\(\s*(\w+)", source)
        case["table_names"] = [name.upper() for name in names]
        case["expected_tdf"] = user_tdf(tdf.parse(blob)[1], case["table_names"])
    return case


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    recorded = json.loads(FIXTURE.read_text()) if args.audit else None
    result = {
        "observation_date": "2026-10-19",
        "compiler_sha256": {
            logger: hashlib.sha256((ROOT / "compiler" / profiles.get(logger).exe_name).read_bytes()).hexdigest()
            for logger in LOGGERS
        },
        "cases": [],
    }
    for case_id, source in CASES.items():
        for logger in LOGGERS:
            case = observe(case_id, source, logger, not args.no_cache)
            result["cases"].append(case)
            print(case["id"], "accepted" if case["expected_exit"] == 0 else "rejected", flush=True)
    if recorded is not None:
        different = sum(a != b for a, b in zip(result["cases"], recorded["cases"]))
        different += abs(len(result["cases"]) - len(recorded["cases"]))
        if result["compiler_sha256"] != recorded["compiler_sha256"]:
            raise SystemExit("Reference compiler identities changed")
        print(f"{len(result['cases'])} cases, {different} differing")
        return bool(different)
    FIXTURE.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"Recorded {len(result['cases'])} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
