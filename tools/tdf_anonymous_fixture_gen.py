#!/usr/bin/env python3
"""Regenerate tests/fixtures/tdf_anonymous.json (docs/tdf-rules.md §9c) with the real compilers:
`python3 tools/tdf_anonymous_fixture_gen.py`. Each case is run on all four loggers; the expected value
is the user tables (name, size, fields) of the real TDF, `summarize()` below."""
import hashlib, json, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
import tdftest  # noqa: E402
from opencrbasic import profiles, tdf  # noqa: E402

FIX = HERE.parent / "tests" / "fixtures" / "tdf_anonymous.json"
LOGGERS = ["cr6", "cr1000x", "cr300", "cr350"]


def summarize(data: bytes):
    out = []
    for t in tdf.parse(data)[1]:
        if t.name not in ("Status", "DataTableInfo", "Public"):
            out.append([t.name, t.size, [[f.name, f.dimension, list(f.subdims), f.begin_index, f.processing]
                                         for f in t.fields]])
    return out


def prog(tables, pre="", calls=("A", "B")):
    return (pre + "Public X,Y,S As String * 8, Arr(3), Big(16)\n" + tables + "BeginProg\n  Scan(1,Sec,0,0)\n"
            + "".join(f"    CallTable {c}\n" for c in calls) + "  NextScan\nEndProg\n")


def tab(name, body):
    return f"DataTable({name},1,10)\n{body}EndTable\n"


B1 = tab("B", "  Sample(1,X,FP2)\n")
Q = '  Sample(1,"q",String)\n'
goes = (HERE.parent / "tests" / "corpus" / "chm" / "goestable_1.CR6").read_text()
CASES = {f"lit_len{n}": prog(tab("A", f'  Sample(1,"{"a" * n}",String)\n') + B1) for n in (0, 3, 4, 7, 8, 100)}
CASES.update({
    "lit_numeric_type": prog(tab("A", '  Sample(1,"ab",IEEE4)\n') + B1),
    "number_as_string": prog(tab("A", "  Sample(1,5,String)\n") + B1),
    "expr_as_string": prog(tab("A", "  Sample(1,X+1,String)\n") + B1),
    "concat_literals": prog(tab("A", '  Sample(1,"ab"+"cd",String)\n') + B1),
    "concat_variable": prog(tab("A", '  Sample(1,S+"cd",String)\n') + B1),
    "const_string": prog(tab("A", "  Sample(1,Z,String)\n") + B1, 'Const Z="abcde"\n'),
    "const_numeric": prog(tab("A", "  Sample(1,C,IEEE4)\n" + Q) + B1, "Const C=5\n"),
    "nan_true": prog(tab("A", "  Sample(1,NAN,IEEE4)\n  Sample(1,True,Boolean)\n" + Q) + B1),
    "function_numeric": prog(tab("A", "  Sample(1,Sqr(X),IEEE4)\n" + Q) + B1),
    "two_in_one_table": prog(tab("A", Q + '  Sample(1,"r",String)\n') + B1),
    "one_each_in_two_tables": prog(tab("A", Q) + tab("B", Q + "  Sample(1,2*X,FP2)\n")
                                   + tab("C", '  Sample(1,"zz",String)\n'), calls=("A", "B", "C")),
    "uncalled_tables": prog(tab("A", Q) + tab("B", Q), calls=("B",)),
    "literal_between_variables": prog(tab("A", Q + "  Sample(1,X,FP2)\n" + Q) + B1),
    "fieldnames_consumes_number": prog(tab("A", Q + '  FieldNames("First")\n  Sample(1,"r",String)\n') + B1),
    "fieldnames_two_names": prog(tab("A", '  Sample(1,"hello",String)\n  FieldNames("P,Q")\n') + B1),
    "instruction_suffix_one_number": prog(tab("A", "  Average(1,X+1,IEEE4,False)\n"
                                              "  Maximum(1,-X,IEEE4,False,True)\n" + Q) + B1),
    "fft_expression": prog(tab("A", "  FFT(X+1,FP2,16,1,Sec,1)\n  FFT(Y*2,FP2,16,1,Sec,1)\n" + Q) + B1),
    "fft_variable_no_number": prog(tab("A", "  FFT(Big(),FP2,16,1,Sec,1)\n" + Q) + B1),
    "cross_table_no_number": prog(tab("B", "  Sample(1,X,FP2)\n") + tab("A", "  Sample(1,B.X,FP2)\n" + Q),
                                  calls=("B", "A")),
    "alias_no_number": prog(tab("A", "  Sample(1,Al,IEEE4)\n" + Q) + B1, "Public Q(2)\nAlias Q(1)=Al\n"),
    "goestable_1": goes,
    "goestable_1_short_literal": goes.replace('"Forced RT: "', '"x"'),
})

if __name__ == "__main__":
    out = {"observation_date": "2026-10-19",
           "compiler_sha256": {lg: hashlib.sha256((tdftest.REPO / "compiler" / profiles.get(lg).exe_name).read_bytes()).hexdigest()
                               for lg in LOGGERS},
           "cases": []}
    for cid, src in CASES.items():
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.CR6"
            p.write_text(src)
            expected = {}
            for lg in LOGGERS:
                r = tdftest.run_oracle_tdf(p, Path(d), tdftest.REPO / "compiler" / profiles.get(lg).exe_name)
                if not r["ok"]:
                    sys.exit(f"{cid} {lg}: real compiler rejected: {r['stdout']}")
                expected[lg] = summarize(r["tdf"])
        out["cases"].append({"id": cid, "source": src, "expected": expected})
    FIX.write_text(json.dumps(out, indent=1))
    print(len(out["cases"]), "cases")
