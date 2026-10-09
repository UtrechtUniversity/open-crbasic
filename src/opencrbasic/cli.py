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
not write CR6cmple.xxx).
Output goes to stdout and to the message file (CR6cmple.xxx by default, CRLF line endings);
exit status 0 on success, 1 on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__, profiles


def _parse(argv: list[str]):
    opts = {"s": False, "v": False, "d": False, "z": None, "l": None, "c": None, "p": None,
            "i": False, "t": False, "b": None, "u": False, "logger": None, "message_file": True}
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


_P1_HIDERS = ("Invalid symbol in expression", "Could not find variables")


def _hides_times(program_ast, diags, main) -> bool:
    """Errors that suppress `[Times]` entries though our checker raises them after pass 1
    (`docs/cli.md` "-t"): undefined/invalid symbols, a missing include extension, and prose lines ahead of
    `BeginProg` (`Expr illegally occurs before BeginProg.`)."""
    for d in diags.errors:
        if (d.message.startswith(_P1_HIDERS) or "must have valid program" in d.message
                or d.message == "Expr illegally occurs before BeginProg."):
            return True
    return False


def _pass1_ok(program_ast, profile, fallback: bool) -> bool:
    """Whether `checker/__init__.py`'s own pass 1 (structure/expressions/required-parameter
    checks) finds nothing wrong -- the same condition that module uses to gate pass 2 (instruction
    channel/range validation). `-t`'s `[Times]` entries are oracle-confirmed to follow *this* gate,
    not `result.ok`: `dhcprenew_1.CR6` (a CHM corpus example) fails to compile on a pass-2-only
    channel-range error yet still gets a full `[Times]` section including the very instruction
    that errored, while `readonly_1.CR6` (a pass-1 parse error, a malformed multi-line `TCDiff`
    call) gets none at all, though it has a `Scan` and a `PanelTemp` call before the error
    (`docs/cli.md` "-t"). This re-runs pass 1 standalone by calling `checker/structure.py`'s and
    friends' own public pass-1 functions (unmodified) rather than editing that package, which a
    concurrent workstream owns and actively changes (its pass-1 entry point has already been
    renamed once, `check` -> `check_pass1`, during this session); `getattr` tries both spellings,
    and any failure -- an incompatible signature, a missing module -- falls back to `fallback`
    (the caller's own `result.ok`), so a future rename degrades to the simpler, still-correct-most-
    of-the-time rule rather than breaking `-t` outright.
    """
    from . import consteval
    from .diagnostics import Diagnostics

    try:
        from .checker import expressions, instr_rules, structure
        structure_check = getattr(structure, "check_pass1", None) or structure.check
        diags = Diagnostics()
        env = consteval.Env(profile, dict(program_ast.consts))
        structure_check(program_ast, profile, diags, env)
        expressions.check(program_ast, profile, diags, env)
        instr_rules.check(program_ast, profile, diags)
    except Exception:
        return fallback
    return not diags.errors


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

    result = compile_file(program, profile, display_name=program.name, warn_unused=opts["u"])
    lines = result.result_lines(show_signature=opts["s"])
    print("\n".join(lines))

    # `-t` (`[Times]`) and `-b` (CDM image) both need the parsed program; re-parsed once, shared,
    # only when asked for (docs/cli.md: this does not affect `result`, which already parsed its
    # own copy inside `compile_file`).
    program_ast = None
    if opts["t"] or opts["b"]:
        from . import parser as parser_mod
        try:
            program_ast, _ = parser_mod.parse_file(program, profile=profile)
        except Exception:
            program_ast = None

    times_entries: list[str] = []
    if opts["t"] and program_ast is not None and _pass1_ok(program_ast, profile, result.ok) and not _hides_times(program_ast, result.diagnostics, program.name):
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
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
