"""`crbc`: a drop-in for CR6Comp.exe (and the CR1000X/CR300/CR350 compilers).

    crbc [options] program [messagefile]

CR6Comp-compatible options (docs/cli.md): -d, -v, -s, -z FILE (binary TDF), -l FILE / -c FILE
(table list), -p FILE / -i FILE (preprocessed source; -i also expands Include/IncludeSection),
-t (the `[Times]` section), -b FILE (CDM binary image; writes the "No CDM devices..." message
when the program uses none). -m/-u are accepted (matching the exe's own option set) but have no
implemented effect: -m is oracle-confirmed to have none observable from the CLI either; -u is
documented, not implemented (docs/cli.md "-u"). Extra long options: --logger
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
            "i": False, "t": False, "b": None, "logger": None, "message_file": True}
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
                elif ch in "mu":
                    pass
                else:
                    print("illegal option!")
                j += 1
            i += 1
            continue
        positional.append(arg)
        i += 1
    return opts, positional


def _write_message_file(path: Path, lines: list[str]) -> None:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1", "replace"))


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

    result = compile_file(program, profile, display_name=program.name)
    lines = result.result_lines(show_signature=opts["s"])
    print("\n".join(lines))
    if opts["message_file"]:
        header = ["[Version]", version_line] if version_line else []
        _write_message_file(message_path, header + lines)
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
