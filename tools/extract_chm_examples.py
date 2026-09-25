"""Extract complete CRBasic example programs from extracted_chm/ help pages.

Pages document one instruction each (see Instructions/portset.htm and friends): a "Syntax"
section, then a `<p><b>Example:</b></p>` marker followed by one or more example programs (each
program is one or more `<p>` blocks using `<br/>` for line breaks and `&nbsp;` for indentation;
several examples on one page are separated by `<h3>` headings), then "Remarks"/"Parameters"
sections under further `<h2>` headings.

A handful of pages outside Instructions/ (see extracted_chm/Info/sampleprogram.htm) embed a full
program without that `<b>Example:</b>` marker at all; for those we fall back to treating the
whole page (up to the first `<h2>`) as one implicit example.

Only extracts programs that contain both `BeginProg` and `EndProg`; fragments are skipped.

Usage (from opencrbasic/): python3 tools/extract_chm_examples.py
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

OPENCRBASIC_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = OPENCRBASIC_ROOT.parent
CHM_ROOT = REPO_ROOT / "extracted_chm"
CHM_SUBDIRS = ["Instructions", "Info", "parameters", "terms"]
OUT_DIR = OPENCRBASIC_ROOT / "tests" / "corpus" / "chm"

EXAMPLE_MARKER_RE = re.compile(r"^Examples?\s*:?$", re.IGNORECASE)

# A paragraph is treated as the start of code (rather than English prose introducing it) if it
# already has an internal line break (it came from a multi-line <p> using <br/>) or its first
# word is one that only appears at statement-start in CRBasic source.
_CODE_START_WORDS = {
    "'", "public", "dim", "const", "beginprog", "datatable", "alias", "units", "slowsequence",
    "sub", "function", "if", "for", "do", "while", "sequencefile", "consttable", "include",
    "declare", "select", "'declare",
}
_FIRST_WORD_RE = re.compile(r"^[A-Za-z_#][A-Za-z0-9_]*|^'")


def _looks_like_code(paragraph: str) -> bool:
    # A paragraph whose only "\n" is trailing whitespace from the closing </p> tag sitting on
    # its own source line (no actual <br/> inside it) is not code by virtue of that alone.
    stripped = paragraph.strip()
    if not stripped:
        return False
    if "\n" in stripped:
        return True
    if stripped.startswith("'"):
        return True
    m = _FIRST_WORD_RE.match(stripped)
    if not m:
        return False
    return m.group(0).lower() in _CODE_START_WORDS


class _ExampleParser(HTMLParser):
    """Walks a help page and collects example-program paragraph groups.

    `implicit_start`: if True, behaves as though a marker paragraph had just been seen at the
    top of the document (used as a fallback for pages without the `<b>Example:</b>` marker).
    """

    def __init__(self, implicit_start: bool = False):
        super().__init__(convert_charrefs=True)
        self.state = "in_example" if implicit_start else "before"
        self.in_p = False
        self.p_buffer: list[str] = []
        self.groups: list[list[str]] = []
        self.current_group: list[str] | None = [] if implicit_start else None
        if implicit_start:
            self.groups.append(self.current_group)

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "p":
            self.in_p = True
            self.p_buffer = []
        elif t == "br":
            if self.in_p:
                self.p_buffer.append("\n")
        elif t == "h2":
            if self.state == "in_example":
                self.state = "done"
        elif t == "h3":
            if self.state == "in_example":
                self.current_group = []
                self.groups.append(self.current_group)

    def handle_endtag(self, tag):
        if tag.lower() == "p" and self.in_p:
            self.in_p = False
            text = "".join(self.p_buffer).replace("\xa0", " ")
            self._finish_paragraph(text)

    def handle_data(self, data):
        if self.in_p:
            self.p_buffer.append(data)

    def _finish_paragraph(self, text: str) -> None:
        if self.state == "before":
            if EXAMPLE_MARKER_RE.match(text.strip()):
                self.state = "in_example"
                self.current_group = []
                self.groups.append(self.current_group)
            return
        if self.state == "in_example":
            if self.current_group is None:
                self.current_group = []
                self.groups.append(self.current_group)
            self.current_group.append(text)


def _extract_programs_from_groups(groups: list[list[str]]) -> list[str]:
    programs = []
    for group in groups:
        start = None
        for i, paragraph in enumerate(group):
            if _looks_like_code(paragraph):
                start = i
                break
        if start is None:
            continue
        joined = "\n".join(group[start:])
        if "BeginProg" not in joined or "EndProg" not in joined:
            continue
        lines = joined.split("\n")
        # Drop anything trailing the last EndProg (fallback pages can pick up a closing prose
        # paragraph after the code paragraph run).
        last_endprog = max(i for i, line in enumerate(lines) if "EndProg" in line)
        lines = lines[: last_endprog + 1]
        # Trim leading/trailing blank lines; keep internal blank lines and indentation.
        while lines and lines[0].strip() == "":
            lines.pop(0)
        while lines and lines[-1].strip() == "":
            lines.pop()
        lines = [line.rstrip() for line in lines]
        text = "\n".join(lines)
        if "BeginProg" in text and "EndProg" in text:
            programs.append(text)
    return programs


def extract_examples(html: str) -> list[str]:
    """Return the text of each complete example program found in one help page's HTML."""
    parser = _ExampleParser(implicit_start=False)
    parser.feed(html)
    parser.close()
    programs = _extract_programs_from_groups(parser.groups)
    if programs:
        return programs
    # Fallback: no `<b>Example:</b>` marker found (e.g. Info/sampleprogram.htm) - try treating
    # the whole page as one implicit example run.
    fallback_parser = _ExampleParser(implicit_start=True)
    fallback_parser.feed(html)
    fallback_parser.close()
    return _extract_programs_from_groups(fallback_parser.groups)


def _safe_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", path.stem)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    pages_with_examples = 0
    for subdir in CHM_SUBDIRS:
        dir_path = CHM_ROOT / subdir
        if not dir_path.is_dir():
            continue
        for page in sorted(dir_path.glob("*.htm")):
            html = page.read_text(encoding="utf-8", errors="replace")
            if "BeginProg" not in html:
                continue
            programs = extract_examples(html)
            if not programs:
                continue
            pages_with_examples += 1
            stem = _safe_stem(page)
            for n, program in enumerate(programs, start=1):
                out_path = OUT_DIR / f"{stem}_{n}.CR6"
                out_path.write_text(program + "\n", encoding="utf-8")
                total += 1
    print(f"Extracted {total} example programs from {pages_with_examples} pages into {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
