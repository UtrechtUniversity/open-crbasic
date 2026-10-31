# opencrbasic 1.0.0 release record (M7)

Internal release ([legal notes](legal.md): internal use only, never published or redistributed).
Tag `opencrbasic-v1.0.0` (local, annotated) on the commit that adds this file; its parent is
`9fa407a4`. The v1.0 milestones and their exit criteria: [PLAN](../PLAN.md#v10-milestones).

## Versions and configuration

- Package `opencrbasic` 1.0.0 = `opencrbasic.__version__` = VS Code extension 1.0.0;
  `analysis.API_VERSION` 2, `--report` schema 2 (policy: README "Versions").
- Host: macOS 27.2, Apple Silicon (`macOS-27.2-arm64-arm-64bit-Mach-O`); Python 3.15.0; Wine 11.17 with prefix
  `~/.wine-cr6` for the reference compilers; uv-managed environment from `uv.lock`; Node v26.0.0,
  npm 11.12.1 for the client.
- Hashes below are from `uv run python tools/release_manifest.py` (one SHA-256 per corpus over its
  sorted relative paths and file hashes).

| Profile | Reference compiler | SHA-256 |
|---|---|---|
| cr6 | `CR6Comp.exe` CR6.14.2.0 | `f6a6efca455f88225fe4f46732409ccf96d8cd4f9201b717746c4dc69ba14f55` |
| cr1000x | `cr1000xcomp.exe` CR1000X.8.2.0 | `9c327f8bae2e55546e5c7a35c931d2f5c21516ed121612e869e1c9c8a6072175` |
| cr300 | `cr300comp.exe` CR300.Std.11.02 | `755329ee63309bfe8299fc65523e5e231ce18138cfb59209a79b4eb2c04275a1` |
| cr350 | `cr350comp.exe` CR350.Std.1.07 | `741ed2701391b8d570a9cf64220c656d90e3caf06c4c60266815c9d0deec5381` |

| Corpus | Files | SHA-256 |
|---|---:|---|
| crbasic (repository programs) | 33 | `7a4c90894ea99ef153d98ae16af6ae0fb355d3440206df0b3166bcf29caf8dd5` |
| CRBasic help examples | 507 | `1ac651835fb4b888a82e22dde1956d1bdbf68cade2467ca4ff31227a23deb9eb` |
| GEUS programs | 92 | `d2eb8d5d26ad5ba64dd7f1a4db1bd0822677947bae88484190517790153e1d1e` |
| fuzz replay reproducers | 6680 | `ba8df6be0623069e370e5555dbeff2259eb9575352f3bd99f74effc521e3ce1c` |
| fixtures | 583 | `7b6c092b24797197c99d37048bf4f00e68f1c1bf3f54438720abee0993778d32` |

| File | SHA-256 |
|---|---|
| `tests/replay_baseline.json` | `a5a1b8708dd18e62032bcb6b9dd38e21e2477aa1dc9be4e4e70cc08a82451409` |
| `tests/artifact_baseline.json` | `786b151a276f3a31d437d1864222b3b8165874c1414da18ebbeb4b0ab59e373f` |
| `docs/inventory-cr6.json` | `b4e7e844fe0a909af2d1d51095890995ada995616e4dbc1893d2f433a7b504ee` |
| `src/opencrbasic/data/notices_cr6.json` | `0e03d06bcabc8d58722d76379774be99ec4de6ffd688d9762c8dbb49a0ca790b` |
| `uv.lock` | `c17ea90da44c9b9fa5236c3a4a49471a4cebe4832abf9695bbf474affbbd8b7c` |
| `vscode/package-lock.json` | `6119e59f90f6096f8ce1df730ffec58d34b82e744c69113e3e3e644bfbb4fc75` |

## Commands and results (2026-10-31, from `opencrbasic/` unless noted)

| Command | Result |
|---|---|
| `uv run pytest -q -n 8 -m 'not server'` | 63,950 passed, 311 skipped, 1,283 strict xfail |
| `uv run pytest -q -m server -n 4` | 174 passed |
| `uv run python tools/fixture_audit.py --jobs 4 <every fixture in the case format>` | raw: 546 fixtures, 60,205 cases, 43 differing, 18 without a verdict. Filtered from that audit, because the tool stages none of their inputs (their own tests compare with the saved reference): `m5_artifacts.json` (arguments), `r66_include_stop.json` (encoded include files), `source_buffer.json` (`include` key). The other 543 fixtures, 60,034 cases: 0 differing, 12 without a reference verdict (`unclosed_structure.json`, reference failures) (`scratch/m7/audit.log`) |
| `uv run python tools/difftest.py --group all --logger <profile>` | CR6 and CR1000X: repository 20/20 exact, help examples 506/507 exact (one reference crash, `pointervariable_2`); CR300 9 + 11 same verdict / 505 exact, CR350 10 + 10 / 505 (two reference crashes); no verdict mismatch, no implementation exception |
| `uv run python tools/difftest.py --group geus --logger cr1000x` | 66/66 exact |
| `uv run python tools/fuzz_replay.py --recorded --baseline tests/replay_baseline.json --gate no-regressions` | exit 0 (`scratch/m7/replay.log`, after `fuzz_replay.py --rerecord-stale`); 3,338 reproducers, 2,634 exact; CR6/CR1000X false accepts 17/8, all outside the relevant set (listed below); 19 without a reference verdict, never counted: 18 recorded reference crashes and `75f68395c4e8` (CR6), whose recording predates a `crbasic/` change and whose fresh reference run crashes too (exit 5, page fault), so it cannot be re-recorded |
| `uv run python tools/first_error_rate.py <replay json> scratch/r30_replay_candidate.json` | on the reproducers shared with round 30: CR6 94.2% (round 30: 88.1%), CR1000X 91.8% (78.1%), CR300 92.7% (91.7%), CR350 92.1% (91.3%) |
| `uv run python tools/artifact_matrix.py --logger <profile> --baseline tests/artifact_baseline.json --gate no-regressions` | exit 0 on all four after the CR300/CR350 baseline refresh (`scratch/m7/matrix-gates.log`; the first run, `gates.log`, exited 1 on those two before it); exact cells of 760: CR6 757, CR1000X 685, CR300 538, CR350 547; CR300/CR350 keep 76 `-b` reference-failure cells each (not an option there) |
| `uv run python tools/tdftest.py --group all` (CR6) | TDF, table sizes and table list byte-identical 212/212; 6 accepted help examples for which the reference writes no artifact (`fileencrypt_1`, `portsconfig_1`, `scannextscan_1`, `scannextscan_2`, `sdmcan_1`, `sdmx50_1`) and 1 reference fault |
| `uv run python tools/timingtest.py --group all` | CR6: repository 20/20, help examples 506/507 (`pointervariable_2` is a reference crash) |
| `uv run --no-sync python tools/lsp_install_check.py` | 15 of 15 checks passed (wheel and sdist, `crbc` and `crbc-lsp` from a fresh install, core install without `pygls`; `scratch/m7/install.log`) |
| `npm ci` (in `vscode/`), `uv run --no-sync python tools/vsix_check.py` | 11 of 11 checks passed (`scratch/m7/vsix.log`); `npm audit` reports 6 vulnerabilities (4 moderate, 2 high) in the client's dependency tree, not changed (`scratch/m7/npm-audit.log`, full output) |
| `cd ../scripts && uv run verify.py`, also with `CR6COMP=opencrbasic` | `BUILD  Pyrefly 514 files pass \| host tests 159 pass \| CRBasic 21/21 compiled, 6 warning(s) \| repository pass` (both) |

No hardware tests: compilation and these comparisons do not establish logger runtime behaviour.

## Known failures and limits

- **Replay false accepts outside the relevant set** (M1 gate scope, PROGRESS "Next steps"): round 34
  `072c74da6af5`, `51f39a5f2393`, `7b4b2177d56b`, `870ca9a9562e`, `e8c854e3493d`, `a0b2b0b059b3`,
  `ec6b11bc3176`, `022d98dd3efc`, `4156d89a014d`, `6ac24bd775f1`, `72f95eac103f`, `77636b10567c`,
  `7f9efa8af42c`, `c060e9f1402f`, `d12785e05faf`, `f2f6e5d8b8e6`; round 35 `0e23c61229f4`,
  `39a03364be83`, `52138d7bd179`, `577a59ecb08c`, `778ea45e64b7`, `9c6367a2c598`, `ecc509dd06de`,
  `f61f825392f2`, `fa923ecef6f4`. Nine of the CR6 ones get a coverage notice naming their rule
  ([inventory](inventory.md#coverage-of-the-listed-false-accepts)).
- **Relevant area, CR6/CR1000X (an exception to the M1 gate):** a `Sub` name that is also another
  routine's parameter, used as a value, compiles where the reference rejects it (round 71).
- **Replay without a verdict:** 19 reproducers (18 reference crashes, `75f68395c4e8` above).
- **First error:** the remaining differences have a recorded decision in the
  [first-error review](first-error-review.md); the open candidates of rounds 44-55 are in PROGRESS
  "Next steps" item 6 (CDM PipeLine placement unprobed for ten instructions, a few fuzz ordering
  effects).
- **Artifacts** ([artifacts](artifacts.md)): CR1000X/CR300/CR350 TDF use the CR6 Status table (best
  effort); the CR300/CR350 baselines were refreshed in round 75 with 23 improvements and 11 cells
  whose wrong output changed bytes (`-i` and two `-p`), accepted, not shown to be better; CR6 known cells `cdmcurrentdiff_1 -b` and `cdmvw300config2_1 -p/-i`; CR1000X
  `histogram4d_1` table list; `-t` limited to the oracle-fitted instruction set.
- **CR300/CR350:** best effort throughout; their strict xfails are in the fixtures.
- **Editor:** host-tested in VS Code on macOS (T-01 to T-18); the M6 check and notice display is not
  host-tested; Windows and Linux not claimed; help text CR6 only.
- **Coverage notices** match by message text: a message assembled from pieces under 10 characters
  that no observation shows can be noticed although modelled ([inventory](inventory.md#states)).

## Exclusions

- The CDM binary image of `-b` and its `Creating CDM binary image ...` line (human decision
  2026-10-12, [PLAN](../PLAN.md#explicit-exclusions-from-parity)).
- Later-diagnostic order, wording and cascades (human decision 2026-10-24, README "Accepted
  differences").
- A CR1000X evidence inventory (M4 is scoped to CR6).
