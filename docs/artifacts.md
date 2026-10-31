# Artifact matrix (M5)

`tools/artifact_matrix.py` runs the reference compiler and `crbc` on the same programs with the
same arguments and compares every output byte for byte: exit status, stdout, the message file and
every file an option writes, including whether it is written at all and whether it is empty. It is
the M5 gate ([PLAN](../PLAN.md#v10-milestones)).

```bash
uv run python tools/artifact_matrix.py --logger cr6 --jobs 6 --baseline tests/artifact_baseline.json --gate no-regressions
```

Corpus per profile: the 20 `crbasic/*.CR6` programs (with their sibling files), every tenth help
example (51), and five programs the reference rejects (`FAILING` in the tool: a bad declaration, a
bad table field, a missing `Include`, a broken assignment, a missing `IncludeSection`). Option sets:
none, an explicit message file, `-s`, `-t`, `-u`, `-z -l -c`, `-p`, `-i`, `-b`, and `-st -z`.
Reference runs are cached in `.oracle-cache/matrix/`; a run without a compiler verdict (a runner
failure) is a `reference failure` cell, never compared or cached.

Both sides get the same staged files (one manifest, also the cache key). stdout is compared as
bytes with line endings normalised (Wine doubles the CR of a piped stdout), files byte for byte. A
cell is `exact`, or lists each output that differs (`exit`, `stdout`, `<file> differs`,
`<file> missing`: the reference wrote it, `crbc` did not; `<file> extra`), each with a fingerprint
of opencrbasic's bytes. The baseline, `tests/artifact_baseline.json`, holds every cell of all four
profiles. `--gate no-regressions` needs a baseline that has the profile and fails on a cell not in
it, a baseline cell not run, or a difference, output fingerprint or reference failure that the
cell's baseline does not have. A cell that only loses differences is reported as `IMPROVED`
(refresh the baseline). Wine is
needed, so CI runs the saved reference results in `tests/fixtures/m5_artifacts.json` (76 runs,
`tests/test_m5_artifacts.py`) instead.

## Rules found by the matrix (round 74, oracle, CR6 unless noted)

- **Message file name.** Each compiler writes its own default: `CR6cmple.xxx`, `CR1000Xcmple.xxx`,
  `CR300cmple.xxx`, `CR350cmple.xxx`. `crbc` wrote `CR6cmple.xxx` for every profile.
- **`-z` on a failed compile** writes the TDF version byte alone (`0x01`) and no `-l`/`-c` file (35
  of 35 rejected corpus programs). A missing `Include` still gives the full TDF and lists.
- **`-b` stdout order.** `No CDM devices used in this program ` comes before the result on stdout,
  as in the message file. It is also printed for a program with CDM instructions that a pass-1
  error stops (`Public 1bad` before `CDM_SW5`), not after a pass-2 error.
- **`-p` with a missing include** ends after that `Include`/`IncludeSection` line; **`-i`** keeps the
  line as written and goes on. `-p` reads the `Const`s of an existing include for later `#If`s
  without writing its text (`Main.CR6`: `#If BatteryTempSensor > 0`).
- **A full message buffer ends pass 1.** When the first error the 4096-byte buffer cannot hold is a
  pass-1 error (`compiler.dropped_message`, `CompileResult.pass1_errors`), `-p`/`-i` output ends with
  that statement, at its first active occurrence by file and line, also inside an `#If` block or an include (`-p`
  then ends with the `Include` line). Pass-2 errors, which run after preprocessing, cut nothing.
- **Names starting with a digit** in `Public`/`Dim` and in routine parameters: `Variable name 1bad
  illegal, starts with a number.`, per name, nothing else for that item (all four loggers; `Static`
  differs). This was a first-error difference on a realistic mistake.

## Support

`supported`: the matrix is exact on that profile apart from the listed cases. `best effort`: kept
and measured, and the gate still pins its current differences, but a difference is not a v1.0
blocker. `excluded`: out of parity by decision.

| Output | CR6 | CR1000X | CR300 / CR350 |
|---|---|---|---|
| stdout, exit status | supported | supported | best effort (diagnostic differences, PLAN profile scope) |
| message file (default name, explicit name) | supported | supported | best effort |
| `-s` signature | supported | supported | best effort |
| `-u` | supported | supported | best effort |
| `-t` `[Times]` | supported for the oracle-fitted instruction set (`docs/cli.md`, `tools/timingtest.py`) | same | same |
| `-z` TDF | supported | best effort: the Status table is the CR6 one | best effort |
| `-l` / `-c` table list | supported | supported; `histogram4d_1` differs | best effort |
| `-p` / `-i` | supported | supported | best effort |
| `-b` message | supported | supported | not an option of these compilers (the next argument becomes the program) |
| `-b` image, `Creating CDM binary image ...` line | excluded ([PLAN](../PLAN.md#explicit-exclusions-from-parity)) | excluded | — |

Known differences on CR6 and CR1000X (in the baseline):
- `cdmcurrentdiff_1` with `-b`: the reference prints `No CDM devices used` for a program whose
  only error (on line 1, before `BeginProg`) opencrbasic classes as pass 2.
- `cdmvw300config2_1` with `-p`/`-i`: the message buffer fills; the first error that does not fit
  is on another line for opencrbasic, because its diagnostics past the buffer (never printed)
  differ.
