#!/usr/bin/env python3
"""Choose the verification gates for a change, from the files it touches.

    uv run --no-sync python tools/lsp_gate.py [--base REV] [--run] [--jobs N]

Run from the repository root (see docs/lsp-api.md "Test tiers"). The changed files are `git diff --name-only <base>...HEAD` plus the
working tree (staged, unstaged and untracked). The gates, strongest first:

* compiler or unclassified `src`/`tests`/`tools` files -> the full suite plus selfdiff;
* LSP-only files (analysis, sources, positions, editor modules, `lsp/**`, `tests/lsp/**`,
  `tools/lsp_*.py`) -> selfdiff plus `tests/lsp` (fast and slow tiers, once);
* any file in `[tool.pyrefly].project-includes` of pyproject.toml, or pyproject.toml itself ->
  `pyrefly check` (only selected here; its error count is not judged);
* only documentation changed -> a relative-link check (`--check-links FILE...`).

Without `--run` the commands are printed, one per line. With `--run` they run in order and the
every gate runs and the exit status is non-zero when any fails.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELFDIFF_BASE = "scratch/lsp/selfdiff-base.json"

COMPILER = ("src/opencrbasic/source.py", "src/opencrbasic/parser.py", "src/opencrbasic/lexer.py",
            "src/opencrbasic/preprocess.py", "src/opencrbasic/consteval.py",
            "src/opencrbasic/compiler.py", "src/opencrbasic/checker/**",
            "src/opencrbasic/tables*.py", "src/opencrbasic/tdf.py", "src/opencrbasic/timing.py",
            "src/opencrbasic/signature.py", "src/opencrbasic/mode.py",
            "src/opencrbasic/profiles.py", "src/opencrbasic/diagnostics.py",
            "src/opencrbasic/syntax.py", "src/opencrbasic/cli.py", "src/opencrbasic/data/**",
            "src/opencrbasic/__init__.py", "src/opencrbasic/__main__.py")
LSP_ONLY = ("src/opencrbasic/analysis.py", "src/opencrbasic/sources.py",
            "src/opencrbasic/positions.py", "src/opencrbasic/editing.py",
            "src/opencrbasic/hover.py", "src/opencrbasic/navigation.py",
            "src/opencrbasic/bindings.py", "src/opencrbasic/catalog.py",
            "src/opencrbasic/helpdocs.py", "src/opencrbasic/rename.py",
            "src/opencrbasic/formatting.py", "src/opencrbasic/quickfix.py",
            "src/opencrbasic/lsp/**", "tests/lsp/**", "tools/lsp_*.py")
DOCS = ("docs/**", "*.md")


def glob_regex(pattern: str) -> re.Pattern[str]:
    """`**/` spans any directories (or none), `**` anything, `*` and `?` stay in one segment."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, patterns) -> bool:
    return any(glob_regex(p).match(path) for p in patterns)


def pyrefly_includes(pyproject_text: str, force_fallback: bool = False) -> list[str]:
    """`[tool.pyrefly].project-includes`: through `tomllib` when present (Python 3.11+), else a
    small reader of the one-key array of strings the file uses."""
    if not force_fallback:
        try:
            import tomllib
        except ImportError:
            tomllib = None
        if tomllib is not None:
            table = tomllib.loads(pyproject_text).get("tool", {}).get("pyrefly", {})
            return list(table.get("project-includes", []))
    section = re.search(r"^\[tool\.pyrefly\][ \t]*(?:#[^\n]*)?\n(.*?)(?=^\[|\Z)", pyproject_text,
                        re.S | re.M)
    if not section:
        return []
    array = re.search(r"^project-includes\s*=\s*\[(.*?)\]", section.group(1), re.S | re.M)
    if not array:
        return []
    body = "\n".join(line.split("#", 1)[0] for line in array.group(1).splitlines())
    return [a or b for a, b in re.findall(r'"([^"]*)"|\'([^\']*)\'', body)]


def classify(paths, includes=()) -> list[tuple[str, str]]:
    """The gates for changed `paths` (relative to the repository root): [(label, command)]."""
    local = [p.replace("\\", "/").removeprefix("opencrbasic/") for p in paths]
    tiers = {"compiler": False, "lsp": False, "docs": False}
    for p in local:
        if matches(p, LSP_ONLY):
            tiers["lsp"] = True
        elif matches(p, DOCS) and not matches(p, ("tests/**", "src/**", "tools/**")):
            tiers["docs"] = True
        elif p == "pyproject.toml" or p.startswith("scratch/"):
            continue
        else:       # compiler modules, the tests and tools around them, and anything unclassified
            tiers["compiler"] = True
    gates: list[tuple[str, str]] = []
    if tiers["compiler"]:
        gates.append(("full suite (compiler or unclassified change; includes every LSP tier)",
                      "uv run --no-sync pytest -q -n {jobs}"))
        gates.append(("selfdiff", f"uv run --no-sync python tools/selfdiff.py --compare {SELFDIFF_BASE}"))
    elif tiers["lsp"]:
        gates.append(("selfdiff", f"uv run --no-sync python tools/selfdiff.py --compare {SELFDIFF_BASE}"))
        gates.append(("LSP tests, fast and slow tiers once",
                      "uv run --no-sync pytest tests/lsp -q -n {jobs}"))
    py_changed = [p for p in local if p == "pyproject.toml" or matches(p, includes)]
    if py_changed:
        gates.append(("pyrefly (LSP modules; error count not judged here)",
                      "uv run --no-sync pyrefly check"))
    if tiers["docs"] and not (tiers["compiler"] or tiers["lsp"]):
        markdown = sorted(p for p in local if matches(p, DOCS) and p.endswith(".md"))
        if markdown:    # evidence assets (images, JSON, logs) are docs but have no links to read
            gates.append(("documentation links", "uv run --no-sync python tools/lsp_gate.py "
                          f"--check-links {' '.join(markdown)}"))
    return gates


def changed_files(base: str) -> list[str]:
    """Repository-relative paths changed since `base` (merge base with HEAD) or in the tree."""
    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
        return [line for line in out.stdout.splitlines() if line]
    names = set(git("diff", "--name-only", f"{base}...HEAD"))
    names |= set(git("diff", "--name-only", "HEAD"))
    names |= set(git("ls-files", "--others", "--exclude-standard", "--full-name"))
    return sorted(names)


def heading_anchors(text: str) -> set[str]:
    anchors = set()
    for m in re.finditer(r"^#{1,6}\s+(.*?)\s*#*\s*$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", m.group(1).lower().replace("`", "")).strip().replace(" ", "-")
        anchors.add(slug)
    return anchors


def check_links(files) -> list[str]:
    """Relative markdown links (and `#anchors`) in `files` that do not resolve."""
    broken = []
    for name in files:
        path = Path(name)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for m in re.finditer(r"\]\(([^)\s]+)\)", text):
            target = m.group(1)
            if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (path.parent / file_part) if file_part else path
            if not dest.exists():
                broken.append(f"{name}: {target} (no such file)")
            elif anchor and dest.suffix == ".md" and anchor.lower() not in heading_anchors(
                    dest.read_text(encoding="utf-8")):
                broken.append(f"{name}: {target} (no such heading)")
    return broken


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="HEAD", help="revision the change starts from (default HEAD: working tree only)")
    ap.add_argument("--run", action="store_true", help="run the gates instead of printing them")
    ap.add_argument("--jobs", type=int, default=4, help="pytest-xdist workers (-n)")
    ap.add_argument("--check-links", nargs="+", metavar="FILE", help="check relative links of these markdown files and exit")
    args = ap.parse_args(argv)
    if args.check_links:
        broken = check_links(args.check_links)
        print("\n".join(broken) if broken else f"links ok ({len(args.check_links)} files)")
        return 1 if broken else 0
    pyproject = ROOT / "pyproject.toml"
    includes = pyrefly_includes(pyproject.read_text(encoding="utf-8")) if pyproject.exists() else []
    paths = changed_files(args.base)
    gates = classify(paths, includes)
    failed: list[str] = []
    print(f"changed files: {len(paths)}")
    for label, command in gates:
        command = command.format(jobs=args.jobs)
        print(f"# {label}\n{command}")
        if args.run and subprocess.run(command, shell=True, cwd=ROOT).returncode:
            print(f"FAILED: {command}")
            failed.append(command)
    if not gates:
        print("# no gate selected")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
