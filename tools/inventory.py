"""Generate the CR6 evidence inventory (M4): per instruction, its builder, the reference messages
the builder and its helpers emit, unresolved calls, the opencrbasic code and the saved
observations that link to it, and an evidence state.

    uv run python tools/inventory.py      # writes docs/inventory-cr6.json and the notice table

It is an inventory, not a coverage percentage. Reads `scratch/re/CR6Comp.decomp.c` (local;
regenerate with `tools/ghidra/DumpAll.java`) and `../compiler/CR6Comp.exe`; both hashes are pinned
in the output. States, heuristics and limits: `docs/inventory.md`.
"""

import argparse
import ast
import bisect
import hashlib
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import pefile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))
from extract_tables import Image  # noqa: E402

HEAD = re.compile(r"^// ==== (\S+) @ ([0-9a-f]{8})\n", re.M)
CALL = re.compile(r"\b((?:thunk_)?FUN_[0-9a-f]{8})\(")
CORE = "FUN_0050aef0"   # the message formatter every reporter wraps (`docs/binary.md`)
WRAPPER = re.compile(r"(?:thunk_|\b)FUN_0050aef0\(([01]),param_1,")
# a string symbol is read whole, commas and all (`s_When_channel_<_0,_measurements_p_0065c788`)
SYMBOL = re.compile(r"\s*(?:s_\S*?_([0-9a-f]{8})|&DAT_([0-9a-f]{8}))\s*(?:,|$)")
INDIRECT = re.compile(r"\(\*+\(code \*+\)")
SPEC = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?(?:l|h)?[dsfgxXuc]")
# A helper reached from the builders of this many instructions or more is shared infrastructure
# (expression parsing, destination resolution); it is listed by name instead of expanded.
SHARED = 15
MIN_LITERAL = 10   # a Python template needs this many literal characters to identify a message
LINKS = 20         # evidence links kept per field (the count is always complete)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pattern(fmt: str) -> re.Pattern:
    return re.compile(".*?".join(re.escape(p) for p in SPEC.split(fmt)), re.S)


def templates() -> list[tuple[re.Pattern, str]]:
    """Every string the opencrbasic sources can emit, as a regex: Python literals and f-strings
    (`{...}` and `%` directives as wildcards) and rule-data strings; with its `module:line`."""
    out = []

    def add(text: str, where: str) -> None:
        parts = re.split(r"\{[^{}]*\}|\x00|" + SPEC.pattern, text)
        if sum(len(p) for p in parts) >= MIN_LITERAL:
            out.append((re.compile("(?s)" + ".*?".join(re.escape(p) for p in parts)), where))

    for path in sorted((ROOT / "src" / "opencrbasic").rglob("*.py")):
        rel = path.relative_to(ROOT / "src").as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.JoinedStr):
                add("".join(str(v.value) if isinstance(v, ast.Constant) else "\x00" for v in node.values),
                    f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                add(node.value, f"{rel}:{node.lineno}")

    def walk(node, rel):
        if isinstance(node, str):
            add(node, rel)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v, rel)
        elif isinstance(node, list):
            for v in node:
                walk(v, rel)

    for path in sorted((ROOT / "src" / "opencrbasic" / "data").rglob("*.json")):
        if not path.name.startswith("notices_"):
            walk(json.loads(path.read_text(encoding="utf-8")), path.relative_to(ROOT / "src").as_posix())
    return out


def observations() -> list[dict]:
    """Saved CR6 reference outputs: `{ref, source, siblings, path, outputs}` per fixture case or
    replay reproducer. A fixture case without a `logger` counts as CR6."""
    found: dict[tuple, dict] = {}

    def walk(node, fixture, where):
        if isinstance(node, dict):
            if isinstance(node.get("source"), str) and node.get("logger", "cr6") == "cr6":
                outputs = [node[k] for k in ("expected_stdout", "theirs", "stdout") if isinstance(node.get(k), str)]
                files = node.get("siblings") or node.get("files") or {}
                if outputs:
                    case = node.get("id", where)
                    found[(fixture, case)] = {
                        "ref": f"tests/fixtures/{fixture}#{case}", "source": node["source"], "path": None,
                        "siblings": {k: v for k, v in files.items() if isinstance(v, str)}
                        if isinstance(files, dict) else None, "outputs": outputs}
                return
            for key, value in node.items():
                walk(value, fixture, f"{where}.{key}" if where else key)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, fixture, f"{where}[{i}]")

    for path in sorted((ROOT / "tests" / "fixtures").glob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")), path.name, "")
    for path in sorted((ROOT / "tests" / "corpus" / "fuzz" / "cr6").rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        program = path.with_suffix(".CR6")
        if record.get("theirs") and program.exists():
            rel = program.relative_to(ROOT).as_posix()
            found[(rel, "")] = {"ref": rel, "source": program.read_text(encoding="latin-1"),
                                "siblings": {}, "path": program, "outputs": [record["theirs"]]}
    return list(found.values())


def message_text(line: str) -> str:
    """A reference stdout line without its location and `Warning:` prefix."""
    return re.sub(r"^(?:line \d+: |<[^>]*> line \d+: |[^:]*: line \d+: )?(?:Warning: )?", "", line).strip()


def code_text(source: str) -> str:
    """CRBasic source without string literals and `'` comments, for finding instruction uses."""
    return re.sub(r"'[^\n]*", "", re.sub(r'"[^"\n]*"', '""', source))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--decomp", type=Path, default=ROOT / "scratch" / "re" / "CR6Comp.decomp.c")
    ap.add_argument("--exe", type=Path, default=ROOT.parent / "compiler" / "CR6Comp.exe")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "inventory-cr6.json")
    ap.add_argument("--notices", type=Path, default=ROOT / "src" / "opencrbasic" / "data" / "notices_cr6.json")
    args = ap.parse_args()

    pe = pefile.PE(str(args.exe), fast_load=True)
    base = getattr(pe.OPTIONAL_HEADER, "ImageBase")
    img = Image(args.exe)

    def target(va: int) -> int:
        code = pe.get_data(va - base, 5)
        while code[0] == 0xE9:  # jmp rel32
            va += 5 + int.from_bytes(code[1:5], "little", signed=True)
            code = pe.get_data(va - base, 5)
        return va

    src = args.decomp.read_text(errors="replace")
    newlines = [m.start() for m in re.finditer("\n", src)]

    def line_of(offset: int) -> int:
        return bisect.bisect_right(newlines, offset - 1) + 1

    heads = list(HEAD.finditer(src))
    bodies: dict[str, tuple[int, str]] = {}   # name -> (offset of the body, body)
    by_addr: dict[int, str] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(src)
        bodies[m.group(1)] = (m.end(), src[m.end():end])
        by_addr.setdefault(int(m.group(2), 16), m.group(1))

    # reporters: every function that hands its first parameter to the formatter, and their thunks
    reporters: dict[str, str] = {}
    for name, (_, body) in bodies.items():
        if name.removeprefix("thunk_") != CORE and (w := WRAPPER.search(body)):
            kind = "error" if w.group(1) == "1" else "warning"
            reporters[name.removeprefix("thunk_")] = reporters["thunk_" + name.removeprefix("thunk_")] = kind
    report_call = re.compile(r"\b(" + "|".join(map(re.escape, sorted(reporters, key=len, reverse=True)))
                             + r")\(([^;]*?)\);")

    def messages(name: str) -> list[dict]:
        at, body = bodies.get(name, (0, ""))
        found: list[dict] = []
        for m in report_call.finditer(body):
            s = SYMBOL.match(m.group(2))
            arg = m.group(2).split(",")[0].strip()
            text = img.cstr(int(s.group(1) or s.group(2), 16)) if s else None
            item = {"kind": reporters[m.group(1)], "text": text, "line": line_of(at + m.start())}
            if text is None:
                item["unresolved"] = arg or "(no argument)"
            if not any(f["text"] == text and f.get("unresolved") == item.get("unresolved") for f in found):
                found.append(item)
        return found

    def callees(name: str) -> list[tuple[str, int]]:
        at, body = bodies.get(name, (0, ""))
        own = {name, name.removeprefix("thunk_")}
        out, seen = [], set()
        for m in CALL.finditer(body):
            c = m.group(1)
            if c in bodies and c not in own and c not in reporters and c.removeprefix("thunk_") != CORE \
                    and c not in seen:
                seen.add(c)
                out.append((c, line_of(at + m.start())))
        return out

    records = json.loads((ROOT / "src" / "opencrbasic" / "data" / "instructions.json").read_text())
    builder_of = {r["name"]: by_addr.get(target(int(r["compile_fn"], 16))) for r in records}

    def reach(name: str, stop: set[str]) -> tuple[dict[str, tuple[str, int]], set[str]]:
        """Helpers reachable from `name` without passing through `stop` (each with its first call
        site), and the `stop` members it hits."""
        sites: dict[str, tuple[str, int]] = {}
        hit, todo = set(), [name]
        while todo:
            caller = todo.pop()
            for c, line in callees(caller):
                key = c.removeprefix("thunk_")
                if key in stop:
                    hit.add(key)
                elif c not in sites:
                    sites[c] = (caller, line)
                    todo.append(c)
        return sites, hit

    users: dict[str, int] = defaultdict(int)   # helper -> instructions whose builder reaches it
    for b in builder_of.values():
        if b:
            for f in {c.removeprefix("thunk_") for c in reach(b, set())[0]}:
                users[f] += 1
    shared = {f for f, k in users.items() if k >= SHARED}

    tmpl = templates()
    source_text = "\n".join([*(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "opencrbasic").rglob("*.py")),
                             *(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "opencrbasic" / "data").rglob("*.json")
                               if not p.name.startswith("notices_"))])
    observed = observations()
    lines: dict[str, set[int]] = defaultdict(set)   # observed message text -> observation ids
    for i, obs in enumerate(observed):
        for text in obs["outputs"]:
            for line in text.splitlines():
                lines[message_text(line)].add(i)
    code = [code_text("\n".join([obs["source"], *(obs["siblings"] or {}).values()])) for obs in observed]

    seen_cache: dict[str, set[int]] = {}

    def seen_in(fmt: str) -> set[int]:
        if fmt not in seen_cache:
            rx = pattern(fmt)
            seen_cache[fmt] = set().union(*(ids for line, ids in lines.items() if rx.fullmatch(line)))
        return seen_cache[fmt]

    from opencrbasic import profiles
    from opencrbasic.compiler import compile_file
    cr6 = profiles.get("cr6")
    emitted: dict[str, str | None] = {}

    def emitter(fmt: str) -> str | None:
        """Where opencrbasic emits `fmt`: a source template matching it, or else a saved program on
        which opencrbasic prints it as the reference does (at most three tried)."""
        if fmt in emitted:
            return emitted[fmt]
        sample = SPEC.sub("\x00", fmt).strip()
        where = next((w for rx, w in tmpl if rx.fullmatch(sample)), None)
        pieces = [q.strip() for q in SPEC.split(fmt) if len(q.strip()) >= MIN_LITERAL]
        if where is None and pieces and all(q in source_text for q in pieces):
            where = "fragments in the sources"   # a message assembled from pieces
        if where is None:
            rx = pattern(fmt)
            # a case whose sibling files are not a plain map cannot be replayed faithfully
            for i in [i for i in sorted(seen_in(fmt)) if observed[i]["siblings"] is not None][:3]:
                obs = observed[i]
                with tempfile.TemporaryDirectory() as tmp:
                    path = obs["path"] or Path(tmp) / "t.CR6"
                    if obs["path"] is None:
                        path.write_text(obs["source"], encoding="latin-1", errors="replace")
                        for name, text in obs["siblings"].items():
                            (Path(tmp) / name).write_text(text, encoding="latin-1", errors="replace")
                    result = compile_file(path, cr6)
                if any(rx.fullmatch(message_text(line)) for line in result.result_lines()):
                    where = f"reproduced on {obs['ref']}"
                    break
        emitted[fmt] = where
        return where

    def evidence(msg: dict, uses: set[int]) -> dict:
        """One message with its state, by the rules in `docs/inventory.md`."""
        if msg["text"] is None:
            return {**msg, "state": "unknown", "implemented": None, "observed": []}
        where = emitter(msg["text"])
        seen = sorted(seen_in(msg["text"]) & uses)
        state = "tested" if where and seen else "partially tested" if where or seen else "unknown"
        return {**msg, "state": state, "implemented": where, "observed": [observed[i]["ref"] for i in seen[:3]]}

    python = {p.relative_to(ROOT / "src").as_posix(): p.read_text(encoding="utf-8").splitlines()
              for p in sorted((ROOT / "src" / "opencrbasic").rglob("*.py"))}

    def indirect_sites(fn: str) -> list[dict]:
        at, body = bodies[fn]
        return [{"function": fn, "line": line_of(at + m.start())} for m in INDIRECT.finditer(body)]

    out = []
    for r in records:
        name = r["name"]
        builder = builder_of[name]
        word = re.compile(rf"(?<![\w.]){re.escape(name)}\b(?!\s*=)", re.I)
        uses = {i for i, text in enumerate(code) if word.search(text)}
        literal = re.compile(rf"[\"'`]{re.escape(name)}[\"'`(]", re.I)
        links = [f"{p}:{n}" for p, text in python.items() for n, line in enumerate(text, 1) if literal.search(line)]
        entry: dict = {"name": name, "compile_fn": r["compile_fn"], "builder": builder,
                       "messages": [], "helpers": [], "shared_helpers": [], "unresolved_calls": []}
        if builder is not None:
            sites, hit = reach(builder, shared)
            for fn in [builder] + sorted(sites):
                entry["messages"] += [evidence({**m, "via": None if fn == builder else fn}, uses)
                                      for m in messages(fn)]
                entry["unresolved_calls"] += indirect_sites(fn)
            entry["helpers"] = [{"name": c, "called_from": caller, "line": line}
                                for c, (caller, line) in sorted(sites.items())]
            entry["shared_helpers"] = sorted(hit)
        msgs = entry["messages"]
        entry["state"] = ("unknown" if builder is None or not uses else
                          "tested" if msgs and all(m["state"] == "tested" for m in msgs) else "partially tested")
        entry["python"] = {"count": len(links), "sites": links[:LINKS]}
        entry["observations"] = {"count": len(uses), "refs": [observed[i]["ref"] for i in sorted(uses)[:LINKS]]}
        out.append(entry)

    everywhere = set(range(len(observed)))
    infrastructure = {fn: {"instructions": users[fn],
                           "messages": [evidence(m, everywhere) for m in messages(fn if fn in bodies else "thunk_" + fn)]}
                      for fn in sorted(shared)}

    pins = {
        "generator": "tools/inventory.py",
        "binary": {"exe": "CR6Comp.exe", "sha256": sha256(args.exe)},
        "decompile": {"file": "scratch/re/CR6Comp.decomp.c", "sha256": sha256(args.decomp)},
        "instructions_json_sha256": sha256(ROOT / "src" / "opencrbasic" / "data" / "instructions.json"),
    }
    args.out.write_text(json.dumps({**pins, "shared_helper_threshold": SHARED,
                                    "reporters": dict(sorted(reporters.items())),
                                    "instructions": out, "shared_helpers": infrastructure},
                                   indent=1) + "\n", encoding="utf-8")
    # a notice names each resolved message of the instruction's builder or own helpers that
    # opencrbasic neither emits from a template nor reproduces (`docs/inventory.md`)
    missing = {e["name"].upper(): sorted({m["text"] for m in e["messages"] if m["text"] and not m["implemented"]})
               for e in out}
    args.notices.write_text(json.dumps({**pins, "missing": {k: v for k, v in missing.items() if v}},
                                       indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
