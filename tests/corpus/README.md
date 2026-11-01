# Test corpora

| Directory | Contents | In git |
|---|---|---|
| `firnmelt/` | The FirnMelt station programs and libraries, a snapshot of `crbasic/` in [firnmelt-station](https://github.com/UtrechtUniversity/firnmelt-station) at monorepo commit `bd4c30c1` (2026-11-01). Tests pin results to these exact files, so refresh them only together with the expectations. | yes |
| `fuzz/` | Saved fuzz reproducers seeded from `firnmelt/`, replayed by `tools/fuzz_replay.py`. | yes |
| `chm/` | The 507 complete example programs in Campbell's CRBasic help. | no |
| `../../examples/Logger Programs/` | The 66 GEUS station programs used as a CR1000X corpus. | no |

The help examples and GEUS programs belong to others and are not redistributed
([docs/legal.md](../../docs/legal.md)). Tests that need them skip when they are absent. To run
those tests locally:

1. Extract the CRBasic help (the `.chm` installed with CRBasic Editor) into `extracted_chm/` at the
   repository root, for example with `7z x CRBasic.chm -oextracted_chm`.
2. `uv run python tools/extract_chm_examples.py` writes `tests/corpus/chm/`.
3. Put the GEUS programs, if you have them, in `examples/Logger Programs/`.
4. For the differential tools, copy the Campbell compilers into `compiler/` (README, "How it was
   built").

All of these paths are git-ignored.
