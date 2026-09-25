# CR6Comp.exe command line (CR6.14.2.0, 2025-03-04)

Source: decompiled `main` (Ghidra, function at 0x424f60) and runs under Wine on 2026-09-25.

```
CR6Comp.exe [options] program.CR6 [messagefile]
```

- Options are parsed before the positionals. Each option is one `-` followed by letters; several
  flags may share one dash (`-st`). An option that takes a file name takes either the rest of the
  same argument (`-zout.tdf`) or the next argument (`-z out.tdf`). **An option expecting a file
  consumes the next argument even if it is the program name**, and will overwrite it.
- No positional: prints ` -- Compile Failed!` / `No Program File`, exit 1.
- One positional: messages go to `CR6cmple.xxx` in the working directory (and stdout).
- Two positionals: messages go to the named file instead of `CR6cmple.xxx`.
- Three or more positionals: silent exit 1.
- Unknown letter: prints `illegal option!` and continues.

| Option | Argument | Effect (observed / decompiled) |
|---|---|---|
| `-b` | file | Write the CDM (CPI device) binary image. Prints `No CDM devices used in this program ` if none. |
| `-c` | file | Write the table list (same writer as `-l`; identical output observed). |
| `-d` | none | Print `<exe path> VERSION:CR6.14.2.0  DATE:03/04/2025` and exit 0 without compiling. |
| `-i` | file | As `-p`, with an internal flag set (difference not yet determined). |
| `-l` | file | Write the table list: per table name, `Interval`, and fields with `Units`. Includes system tables (`DataTableInfo`, ConstTable, `Public`). |
| `-m` | none | Allocates a 1 KiB buffer and sets a flag; effect not yet determined. |
| `-p` | file | Write the preprocessed source (includes expanded, conditional compilation resolved, comments stripped). |
| `-s` | none | After success, also print `ProgSignature =  <n>.` (two spaces). The value is the PakBus signature (seed 0xAAAA) of the main file's bytes, continued over each included file's bytes in expansion order (`src/opencrbasic/signature.py`; matches the exe on 5 repo programs). |
| `-t` | none | Write a `[Times]` section to the message file with per-line timing estimates (`line 205: Scan: 0`, `line 212: PanelTemp = 1100`). |
| `-u` | none | Sets a flag; effect not yet determined. |
| `-v` | none | Print the version line, then compile normally. The version line is also written to the message file under `[Version]`. |
| `-z` | file | Write the binary table definition file (TDF): version byte `0x01`, then the PakBus table-definition structure (Status table first). |

Message file layout for a success with `-s -t`:

```
[Times]
line 205: Scan: 0
line 212: PanelTemp = 1100
p.cr6 -- Compiled in SequentialMode.
ProgSignature =  53316.
```

stdout carries only the result line(s), not `[Times]`.

Result line: `<program name as given> -- Compiled in SequentialMode.` or `PipelineMode.`; on failure
`<name> -- Compile Failed!`, a blank line, then one line per error `line N: <message>`; warnings
after a success are `Warning: <message>`. The message file uses CRLF.
