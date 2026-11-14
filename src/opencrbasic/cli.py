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
not write the message file), --strict (opencrbasic checks, `checks.py`, on stderr only), --report FILE
(JSON report, `docs/checker.md` "Check configuration and report file"), --expand FILE (expand
`#Define`/`#Macro` macros into FILE and compile FILE instead, `macros.py`), --config FILE and
--check CODE=SEVERITY (check configuration; both only act together with --strict or --report).
Output goes to stdout and to the message file (`<LOGGER>cmple.xxx` by default, e.g. CR6cmple.xxx;
CRLF line endings);
exit status 0 on success, 1 on failure, and with --strict 3 when the program compiles but a
check reports an error, 4 when it compiles but the check configuration is invalid.
"""

import io
import json
import re
import sys
from pathlib import Path
from typing import Literal, TypedDict, cast

from . import __version__, profiles
from . import syntax as S


class _Options(TypedDict):
    s: bool
    v: bool
    d: bool
    z: str | None
    l: str | None
    c: str | None
    p: str | None
    i: bool
    t: bool
    b: str | None
    u: bool
    logger: str | None
    message_file: bool
    strict: bool
    report: str | None
    config: str | None
    expand: str | None
    check: list[str]


def _parse(argv: list[str]) -> tuple[_Options, list[str]]:
    opts: _Options = {
        "s": False,
        "v": False,
        "d": False,
        "z": None,
        "l": None,
        "c": None,
        "p": None,
        "i": False,
        "t": False,
        "b": None,
        "u": False,
        "logger": None,
        "message_file": True,
        "strict": False,
        "report": None,
        "config": None,
        "expand": None,
        "check": [],
    }
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            if arg in ("--logger", "--report", "--config", "--expand", "--check"):
                value = argv[i + 1] if i + 1 < len(argv) else ""
                i += 2
            elif arg.split("=", 1)[0] in ("--logger", "--report", "--config", "--expand", "--check"):
                arg, value = arg.split("=", 1)
                i += 1
            else:
                value = None
            if value is not None:
                if arg == "--check":
                    opts["check"].append(value)
                else:
                    opts[cast(Literal["logger", "report", "config", "expand"], arg[2:])] = value  # noqa: F821 -- ruff reads the Literal strings as names
                continue
            if arg == "--no-message-file":
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
                    rest = letters[j + 1 :]
                    if rest:
                        value = rest
                    else:
                        i += 1
                        value = argv[i] if i < len(argv) else ""
                    key = cast(Literal["z", "l", "c", "p", "b"], "p" if ch == "i" else ch)
                    opts[key] = value
                    if ch == "i":
                        opts["i"] = True
                    break
                if ch in "svd":
                    opts[cast(Literal["s", "v", "d"], ch)] = True  # noqa: F821
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
    "Message file layout").
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
        if isinstance(st, S.SubDef):
            in_sub = True
        if in_sub or isinstance(st, S.Assignment):
            keep.add((st.file, st.line))
        if S.ends_routine(st):
            in_sub = False
    for d in diags.errors:
        if (
            d.message.startswith(_P1_HIDERS)
            or "must have valid program" in d.message
            or d.message == "Expr illegally occurs before BeginProg."
        ):
            return True
        if (
            d.message.startswith(_SYMBOL)
            and (begin is None or d.file != begin.file or d.line < begin.line)
            and (d.file, d.line) not in keep
        ):
            return True
    return False


# CR300/CR350 compile in one pass; the errors below come from an instruction's own argument checks
# and do not hide `[Times]` (CHM corpus, oracle 2026-10-12: every other error kind does; the settling
# range, tests/fixtures/timing_r16.json `cr300_measure`, oracle 2026-10-24).
_SINGLE_PASS_KEEPS = re.compile(
    r"FileManage invalid drive|Invalid file type option|Illegal Semaphore|"
    r"Invalid drive or no drive specified|IF or SELECT without EndIf|Could not find \S+\.BytesPerRecord|"
    r"Illegal dimension|exceeds number of valid channels|Settling time -?\d+ too (short|long)|Variable \S+ out of bounds|is not a (Status or )?Settings Field Name|Could not find DataTable (?!variables)|"
    r"^ExChan exceeds number of excitation channels\.$|"
    r"^Invalid PulseCount (PConfig option|reps)\.$|"
    r"^(C[12]|SE[1-4]|P_SW) is not a valid (low level AC input|pulse count) port\.$|"
    r"^Invalid channel number\.$|"
    r"^Invalid channel: -?\d+\.$|^Reps exceeds valid channels\.$"
)


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

    if argv is None:  # a process run: stdout carries the source bytes, like the message file
        cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="latin-1", errors="replace")
        argv = sys.argv[1:]
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
    # each compiler names its own message file: CR6cmple.xxx, CR1000Xcmple.xxx, ... (artifact matrix, M5)
    message_path = (
        Path(positional[1])
        if len(positional) == 2
        else Path(f"{(profile or profiles.get('cr6')).name.upper()}cmple.xxx")
    )
    if not positional:
        lines = [" -- Compile Failed!", "", "No Program File"]
        print("\n".join(lines))
        if opts["message_file"]:
            _write_message_file(message_path, lines)
        return 1

    program = Path(positional[0])
    if profile is None:
        profile = profiles.for_file(program)
        if len(positional) < 2:
            message_path = Path(f"{profile.name.upper()}cmple.xxx")
    if not program.is_file():
        lines = [f"{positional[0]} -- Compile Failed!", "", "No Program File"]
        print("\n".join(lines))
        if opts["message_file"]:
            _write_message_file(message_path, lines)
        if opts["report"]:
            from . import checks

            _write_report(opts["report"], checks.missing_program_report(program.name, profile.name, opts["strict"]))
        return 1

    if opts["expand"]:
        from . import macros

        try:
            expanded = macros.expand(program.read_bytes().decode("latin-1"))
        except macros.MacroError as e:
            lines = [f"{positional[0]} -- Compile Failed!", "", f"line {e.line}: {e}"]
            print("\n".join(lines))
            if opts["message_file"]:
                _write_message_file(message_path, lines)
            return 1
        program = Path(opts["expand"])
        program.write_bytes(expanded.encode("latin-1", "replace"))

    result = compile_file(program, profile, display_name=program.name, warn_unused=opts["u"], provenance=False)
    if opts["p"]:
        from . import preprocess
        from .compiler import dropped_message

        dropped = dropped_message(result) if result.pass1_errors else None
        stop = (
            None
            if dropped is None or not dropped.line
            else (
                re.sub(r"^[A-Za-z]+:", "", dropped.file or program.name).replace("\\", "/").rsplit("/", 1)[-1],
                dropped.line,
            )
        )
        try:
            Path(opts["p"]).write_bytes(preprocess.preprocess(program, profile, expand_includes=opts["i"], stop=stop))
        except OSError:
            pass
    lines = result.result_lines(show_signature=opts["s"])

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
    if (
        opts["t"]
        and program_ast is not None
        and _pass1_ok(result, profile)
        and not _hides_times(program_ast, result.diagnostics, program.name)
    ):
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
        if not uses_cdm or result.pass1_errors:
            # a pass-1 error stops the compile before any CDM device is registered (oracle, CR6,
            # round 74, `docs/artifacts.md`)
            cdm_message = "No CDM devices used in this program "
            print(cdm_message)  # before the result, as in the message file (artifact matrix, M5)
    print("\n".join(lines))

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

    if not result.ok and opts["z"]:
        # a failed compile still writes the TDF version byte, and no table list (`docs/artifacts.md`)
        Path(opts["z"]).write_bytes(b"\x01")
    if result.ok:
        if opts["z"] and result.tables:
            from . import tdf

            Path(opts["z"]).write_bytes(tdf.serialize(result.tables))
        for key in ("l", "c"):
            if (listing := opts[key]) and result.table_list is not None:
                Path(listing).write_bytes(result.table_list.encode("latin-1", "replace"))
    status = 0 if result.ok else 1
    if opts["strict"] or opts["report"]:
        status = _run_checks(opts, result, program, status)
    return status


def _write_report(path: str, report: dict) -> None:
    try:
        Path(path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"opencrbasic: cannot write report: {e}", file=sys.stderr)


def _run_checks(opts: _Options, result, program: Path, status: int) -> int:
    """opencrbasic checks (`checks.py`), after the reference output is final: stderr and the
    report file only, and the exit status moves from 0 only (3: a check error, 4: invalid check
    configuration, `--strict` only). Checks run only when the reference accepted the program."""
    from . import checkconfig, checks

    config_file, error = None, None
    try:
        config_file = Path(opts["config"]) if opts["config"] else checkconfig.find(program)
        config = checkconfig.load(config_file, checks.DEFAULT_SEVERITY) if config_file else checkconfig.Config()
        cli = checkconfig.parse_overrides(opts["check"], checks.DEFAULT_SEVERITY)
        if result.ok:
            checks.analyze(result, program, config, cli=cli)
    except checkconfig.ConfigError as e:
        error = str(e)
        print(f"opencrbasic: configuration error: {error}", file=sys.stderr)
    if opts["strict"]:
        for f in result.findings:
            print(f.text(program.name), file=sys.stderr)
    if opts["report"]:
        _write_report(opts["report"], checks.report(result, program.name, opts["strict"], config_file, error))
    if status == 0 and opts["strict"]:
        if error:
            return 4
        if any(f.severity == "error" for f in result.findings):
            return 3
    return status


if __name__ == "__main__":
    sys.exit(main())
