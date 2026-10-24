"""`crbc`: a drop-in for CR6Comp.exe (and the CR1000X/CR300/CR350 compilers).

    crbc [options] program [messagefile]

CR6Comp-compatible options (docs/cli.md): -d, -v, -s, -z FILE (binary TDF), -l FILE / -c FILE
(table list), -p FILE / -i FILE (preprocessed source; -i also expands Include/IncludeSection),
-t (the `[Times]` section), -b FILE (CDM binary image; writes the "No CDM devices..." message
when the program uses none), -u (adds `%s declared but not used.` warnings for an unreferenced
Public/Dim/Sub-or-Function-parameter, and for an uncalled Sub; `checker/unused.py`). -m is
accepted (matching the exe's own option set) but has no implemented effect: oracle-confirmed to
have none observable from the CLI either. Extra long options: --logger
{cr6,cr1000x,cr300,cr350} (default: from the file extension, else cr6) and --no-message-file (do
not write CR6cmple.xxx), --strict (opencrbasic checks, `checks.py`, on stderr only).
Output goes to stdout and to the message file (CR6cmple.xxx by default, CRLF line endings);
exit status 0 on success, 1 on failure, and with --strict 3 when the program compiles but a
check reports an error.
"""

import re
import sys
from pathlib import Path

from . import __version__, profiles
from . import syntax as S


def _parse(argv: list[str]):
    opts = {"s": False, "v": False, "d": False, "z": None, "l": None, "c": None, "p": None,
            "i": False, "t": False, "b": None, "u": False, "logger": None, "message_file": True,
            "strict": False}
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            if arg == "--logger":
                opts["logger"] = argv[i + 1]
                i += 2
                continue
            if arg.startswith("--logger="):
                opts["logger"] = arg.split("=", 1)[1]
            elif arg == "--no-message-file":
                opts["message_file"] = False
            elif arg == "--strict":
                opts["strict"] = True
            elif arg in ("--help", "-h"):
                print(__doc__)
                sys.exit(0)
            else:
                print("illegal option!")
            i += 1
            continue
        if arg.startswith("-") and len(arg) > 1 and not positional:
            letters = arg[1:]
            j = 0
            while j < len(letters):
                ch = letters[j].lower()
                if ch in "zlcpib":
                    rest = letters[j + 1:]
                    if rest:
                        value = rest
                    else:
                        i += 1
                        value = argv[i] if i < len(argv) else ""
                    opts["p" if ch == "i" else ch] = value
                    if ch == "i":
                        opts["i"] = True
                    break
                if ch in "svd":
                    opts[ch] = True
                elif ch == "t":
                    opts["t"] = True
                elif ch == "u":
                    opts["u"] = True
                elif ch == "m":
                    pass
                else:
                    print("illegal option!")
                j += 1
            i += 1
            continue
        positional.append(arg)
        i += 1
    return opts, positional


def _message_bytes(lines: list[str]) -> bytes:
    """`lines` joined the way the message file actually wants them: CRLF throughout, except the
    blank line straight after a `-- Compile Failed!` header, which the real exe writes as a bare
    LF (oracle-confirmed on both a plain failing compile and a `-t`/`-b` one, `docs/cli.md`
    "Message file layout" -- previously assumed to be a uniform CRLF join, which this replaces).
    """
    if len(lines) >= 2 and lines[1] == "" and lines[0].endswith("Failed!"):
        return (lines[0] + "\r\n\n" + "\r\n".join(lines[2:]) + "\r\n").encode("latin-1", "replace")
    return ("\r\n".join(lines) + "\r\n").encode("latin-1", "replace")


def _write_message_file(path: Path, lines: list[str]) -> None:
    path.write_bytes(_message_bytes(lines))


_P1_HIDERS = ("Could not find variables",)
_SYMBOL = "Invalid symbol in expression"


def _hides_times(program_ast, diags, main) -> bool:
    """Errors that suppress `[Times]` entries though our checker raises them after pass 1
    (`docs/cli.md` "-t"): undefined/invalid symbols, a missing include extension, and prose lines ahead of
    `BeginProg` (`Expr illegally occurs before BeginProg.`). An undefined symbol hides them only
    ahead of `BeginProg` and outside a `Sub`/`Function` body and an assignment (a table
    instruction's argument, a prose line): `X = Nope` in the program or a table body,
    `Randomize(Nope)` in a `Sub` or the scan still print (oracle, round 9)."""
    stmts = program_ast.statements
    begin = next((st for st in stmts if getattr(st, "keyword", "").upper() == "BEGINPROG"), None)
    keep: set[tuple[str, int]] = set()  # lines whose symbol error does not hide
    in_sub = False
    for st in stmts:
        kw = getattr(st, "keyword", "").upper()
        if isinstance(st, S.SubDef):
            in_sub = True
        if in_sub or isinstance(st, S.Assignment):
            keep.add((st.file, st.line))
        if S.ends_routine(st):
            in_sub = False
    for d in diags.errors:
        if (d.message.startswith(_P1_HIDERS) or "must have valid program" in d.message
                or d.message == "Expr illegally occurs before BeginProg."):
            return True
        if d.message.startswith(_SYMBOL) and (begin is None or d.file != begin.file
                                              or d.line < begin.line) and (d.file, d.line) not in keep:
            return True
    return False


# CR300/CR350 compile in one pass; the errors below come from an instruction's own argument checks
# and do not hide `[Times]` (CHM corpus, oracle 2026-10-12: every other error kind does; the settling
# range, tests/fixtures/timing_r16.json `cr300_measure`, oracle 2026-10-24).
_SINGLE_PASS_KEEPS = re.compile(
    r"FileManage invalid drive|Invalid file type option|Illegal Semaphore|"
    r"Invalid drive or no drive specified|IF or SELECT without EndIf|Could not find \S+\.BytesPerRecord|"
    r"Illegal dimension|exceeds number of valid channels|Settling time -?\d+ too (short|long)|Variable \S+ out of bounds|is not a (Status or )?Settings Field Name|Could not find DataTable (?!variables)")


def _pass1_ok(result, profile=None) -> bool:
    """`-t`'s `[Times]` entries follow the checker's own pass-1 gate, not `result.ok`:
    `dhcprenew_1.CR6` (a CHM corpus example) fails on a pass-2-only channel-range error yet still
    gets a full `[Times]` section, while `readonly_1.CR6` (a pass-1 parse error) gets none
    (`docs/cli.md` "-t"). The checker records that gate as `result.pass1_errors`."""
    if profile is not None and profile.single_pass:
        return not any(not _SINGLE_PASS_KEEPS.search(d.message) for d in result.diagnostics.errors)
    return result.ok if result.pass1_errors is None else not result.pass1_errors


def main(argv: list[str] | None = None) -> int:
    from .compiler import compile_file

    argv = sys.argv[1:] if argv is None else argv
    opts, positional = _parse(argv)
    profile = profiles.get(opts["logger"]) if opts["logger"] else None
    version_line = None
    if opts["v"] or opts["d"]:
        p = profile or profiles.get("cr6")
        version_line = f"crbc {__version__} VERSION:{p.version}  DATE:{p.date}"
        print(version_line)
        if opts["d"]:
            return 0
    if len(positional) > 2:
        return 1
    message_path = Path(positional[1]) if len(positional) == 2 else Path("CR6cmple.xxx")
    if not positional:
        lines = [" -- Compile Failed!", "", "No Program File"]
        print("\n".join(lines))
        if opts["message_file"]:
            _write_message_file(message_path, lines)
        return 1

    program = Path(positional[0])
    profile = profile or profiles.for_file(program)
    if not program.is_file():
        lines = [f"{positional[0]} -- Compile Failed!", "", "No Program File"]
        print("\n".join(lines))
        if opts["message_file"]:
            _write_message_file(message_path, lines)
        return 1

    if opts["p"]:
        from . import preprocess
        try:
            Path(opts["p"]).write_bytes(
                preprocess.preprocess(program, profile, expand_includes=opts["i"])
            )
        except OSError:
            pass

    result = compile_file(program, profile, display_name=program.name, warn_unused=opts["u"],
                          provenance=False)
    lines = result.result_lines(show_signature=opts["s"])
    print("\n".join(lines))

    # `-t` (`[Times]`) and `-b` (CDM image) both need the parsed program; re-parsed once, shared,
    # only when asked for (docs/cli.md: this does not affect `result`, which already parsed its
    # own copy inside `compile_file`).
    program_ast = None
    if opts["t"] or opts["b"]:
        from . import parser as parser_mod
        try:
            program_ast, _ = parser_mod.parse_file(program, profile=profile, provenance=False)
        except Exception:
            program_ast = None

    times_entries: list[str] = []
    if opts["t"] and program_ast is not None and _pass1_ok(result, profile) and not _hides_times(program_ast, result.diagnostics, program.name):
        from . import timing
        try:
            times_entries = timing.times_lines(program_ast, profile, program.name)
        except Exception:
            times_entries = []

    cdm_message = None
    if opts["b"]:
        uses_cdm = False
        if program_ast is not None:
            for stmt in program_ast.statements:
                kw = getattr(stmt, "keyword", "")
                if kw.startswith("CDM") or kw in ("CPIADDMODULE2", "CPISPEED2", "CPIFILESEND"):
                    uses_cdm = True
                    break
        try:
            Path(opts["b"]).write_bytes(b"")  # the image format itself is not implemented
        except OSError:
            pass
        if not uses_cdm:
            cdm_message = "No CDM devices used in this program "
            print(cdm_message)

    if opts["message_file"]:
        body = b""
        if version_line:
            body += ("[Version]\r\n" + version_line + "\r\n").encode("latin-1", "replace")
        if opts["t"]:
            times_blob = "[Times]" + "".join(f"\r\n{e}" for e in times_entries)
            body += times_blob.encode("latin-1", "replace")
            if times_entries:
                body += b"\r\n"
        if cdm_message is not None:
            body += (cdm_message + "\r\n").encode("latin-1", "replace")
        body += _message_bytes(lines)
        Path(message_path).write_bytes(body)

    if result.ok:
        if opts["z"] and result.tables:
            from . import tdf
            Path(opts["z"]).write_bytes(tdf.serialize(result.tables))
        for key in ("l", "c"):
            if opts[key] and result.table_list is not None:
                Path(opts[key]).write_bytes(result.table_list.encode("latin-1", "replace"))
    if not result.ok:
        return 1
    if opts["strict"]:
        from . import checks
        findings = checks.run(result.program_ast, profile)
        for f in findings:
            print(f.text(program.name), file=sys.stderr)
        if any(f.severity == "error" for f in findings):
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
