# CR6Comp.exe table-definition (TDF) rules

How `CR6Comp.exe` (CR6.14.2.0, 2025-03-04) builds the `-z` table-definition file (TDF) and the
`-l`/`-c` table list, reverse-engineered black-box (`tools/tdf_probe.py` against the real exe
under Wine) and cross-checked against `extracted_chm/Instructions/*.htm` and a Ghidra
decompilation of `FUN_004eab30` (table finalisation) and `FUN_004f4720`/`FUN_004f4400` (table/
pool bookkeeping). No decompiled source is pasted here; see `docs/binary.md` for the method.

## Confidence tags

- **oracle-confirmed** — reproduced by compiling a snippet with the real exe (`tools/tdf_probe.py`
  or the `-z`/-`l` output directly); the snippet is in the experiment log (`#12`) or inline.
- **chm-documented** — stated in `extracted_chm/Instructions/*.htm`; not independently varied.
- **oracle + chm** — both: black-box result matches the documented mechanism.
- **decompiled-only** — read from `FUN_004eab30` et al.; not (yet) isolated black-box.
- **guess** — inference/extrapolation, not directly observed. Flagged explicitly.

All experiments were run via `python3 tools/tdf_probe.py <snippet> --raw`, which compiles with
`-z out.tdf -l out.lst` and prints `opencrbasic.tdf.dump(...)`. `tools/tdf_probe.py` needed no
fixes; it worked as committed.

## 1. TDF binary format

Already decoded and round-tripped byte-identically by `src/opencrbasic/tdf.py`
(**oracle-confirmed**, `PROGRESS.md` 2026-09-25 entry). Summary: version byte `0x01`, then per
table: name (NUL-terminated), size (`u32` BE), time_type (`u8`), time_into (`NSec`: `s32,s32`
BE), interval (`NSec`), then fields until a `0` byte. Each field: type byte (`0x80` = read-only),
name, alias list (NUL-terminated strings, empty string ends the list), processing string, units
string, description string, begin index (`u32` BE), dimension (`u32` BE), sub-dimensions
(`u32` BE list, `0`-terminated).

Type codes seen (all **oracle-confirmed** via `Sample(...)`/`Public ... As ...` and matched to
`data/symbols.json`): `2`=UINT2, `6`=LONG, `7`=FP2, `9`=IEEE4, `11`=ASCII/STRING, `14`=NSEC,
`17`=BOOL8, `18`=IEEE8, `28`=BOOLEAN. Not directly observed in a table field: UINT1, UINT4,
FP3/FP4 (symbols exist in `data/symbols.json` but no field of that type was produced in these
experiments — **guess** that they encode the same way).

`time_type` was `14` (NSec) on every table in every experiment (Status, DataTableInfo, Public,
ConstTable-tables, and every user DataTable regardless of DataInterval/DataEvent/no-interval) —
**oracle-confirmed** constant across ~35 compiles, mechanism (why 14, whether it can differ) not
investigated further: **open question**.

## 2. Table order and presence

**oracle-confirmed**, from experiments `#1`–`#6`, `#20` below. Order in the TDF (and in `-l`):

1. `Status` — always present, always first.
2. User `DataTable`s, in **source declaration order**, independent of `CallTable` order
   (**oracle-confirmed**, exp `#51`: `DataTable(TableB,...)` declared before
   `DataTable(TableA,...)`, but `CallTable TableA` executed before `CallTable TableB` in the
   `Scan` — TDF/-l order was still `TableB, TableA`, i.e. declaration order).
3. `DataTableInfo` — present **only if at least one user `DataTable` exists** (exp `#1` and
   `#20`: a program with `Public X` and no `DataTable` produces only `Status`+`Public`, no
   `DataTableInfo`). Its field dimensions scale with the table count; see §5.
4. Named `ConstTable`s (`ConstTable(Name, Hidden)`), in declaration order, **only when the
   `TableName` parameter is given** (exp `#7`/`#8`, see §7).
5. `Public` — present **only if at least one `Public` variable exists** (exp `#20`: `Dim X As
   Float` alone, no `Public`, produces no `Public` table at all).

No other system tables were observed (`Settings`, alarms, etc. did not appear in any experiment;
none of these features were exercised, so their absence is **not** evidence they never appear —
**open question**).

## 3. Field addressing: `dim`, `subdims`, `begin` (general rule)

**oracle-confirmed**, from exp `#9`–`#11`, `#17`–`#19`. This rule governs every field in every
table (Status, DataTableInfo, Public, ConstTable-tables, and DataTable outputs) and is stated
once here because §§4–8 all depend on it.

- `subdims` (the TDF's sub-dimension list) is **empty** iff the field has exactly one logical
  dimension **and** that dimension's size is 1 (a true scalar). Otherwise `subdims` lists every
  dimension **in full**, including any dimension whose size happens to be 1 — dimensions are
  never dropped just because they equal 1 (confirmed for `DataTableName`: with 1 user table it is
  rank-2 `[1,24]`, not collapsed to `[24]`; contrast `SkippedRecord`, rank-1, which does collapse
  to scalar at count 1).
- `dim` is the field's **stored element count** — for a scalar, 1; for an array, the product of
  `subdims`; for a `DataTable` output field sourced from Reps < the source array's declared size,
  `dim` = Reps (**not** the source array's size — see next point).
- For a `DataTable` output field whose `Source` is an element of a declared array (e.g.
  `Sample(2, Arr3(1), FP2)` where `Arr3` is `Public Arr3(3)`), `subdims` is the **full declared
  shape of the source array** (`[3]`), independent of Reps, while `dim` = Reps (exp `#17`: Reps=1
  from a 3-element array gives `dim=1 subdims=[3]`; Reps=2 from the same array gives `dim=2
  subdims=[3]`; Reps=3, i.e. the whole array, gives `dim=3 subdims=[3]` — the general case
  degenerating to the familiar "whole array" TDF field). `begin` is the 1-based starting index of
  Source within the declared array (`Sample(1,Arr3(2),FP2)` → `begin=2`) (exp `#18`).
- `String * n` fields (any table) are always rank-1 with `subdims=[stored_len]`, where
  `stored_len` is the rounded-up storage length, not the declared `n` — see §8 for the rounding
  rule. The point here is only that String fields follow the same scalar-collapse/no-collapse rule
  (`String * 1` did **not** collapse to `subdims=[]`, because its stored length is 4, not 1).

## 4. Status table

Canonical minimal-program dump saved as `src/opencrbasic/data/status_table.json`
(**oracle-confirmed**, exp `#0`): `Public X As Float; BeginProg / Scan(1,Sec,0,0) / X=1 /
NextScan / EndProg`, no `DataTable`, no `SlowSequence`, no `SubScan`, no card/CPI use. 52 fields,
`time_type=14`, `into=(0,0)`, `interval=(0,0)`, table `size=1`.

What changes it (everything else tried left it byte-for-byte identical to the baseline):

- **`SlowSequence`** (**oracle-confirmed**, exp `#2`/`#3`): each `SlowSequence...EndSequence`
  block adds 4 fields, inserted next to their non-slow counterparts (not at the end):
  `SkippedSlowScan` (type 6, not read-only) right after `SkippedSystemScan`; `LastSlowScan` (type
  14, read-only) right after `LastSystemScan`; `SlowProcTime` (type 6, read-only, units `usec`)
  right after `SystemProcTime`; `MaxSlowProcTime` (type 6, not read-only, units `usec`) right
  after `MaxSystemProcTime`. With **one** `SlowSequence`, all four are scalars (`dim=1
  subdims=[]`). With **two or more** `SlowSequence`s, all four become rank-1 arrays of size =
  slow-sequence count (`dim=2 subdims=[2]` for two), by the §3 rule — one element per slow
  sequence, in declaration order.
- **`SubScan`** (**oracle-confirmed**, exp `#21`): no effect — `SubScan/NextSubScan` inside a
  `Scan` produced the identical 52-field Status table.
- **A second (or more) `Scan()` block** at the top level (**oracle-confirmed**, exp `#22`): no
  effect (compiles with a "Scan never reached at run time" warning since only one top-level scan
  runs; Status unchanged).
- **Number of `DataTable`s, `CardOut`/`TableFile`, `ConstTable`** (**oracle-confirmed**, exp `#4`,
  `#12`–`#16`): no effect on Status; these only affect `DataTableInfo` (§6) or add a `ConstTable`
  table (§7). `CardStatus` stays `dim=80` regardless of whether a card feature (`CardOut`,
  `TableFile`) is used.
- **CPI/CDM devices**: not exercised — the one attempt (`SDMCAN`) failed to compile on unrelated
  parameter syntax and wasn't pursued further. **Open question.**

## 5. Table header fields

`DataTable(Name, TrigVar, Size)` (**chm-documented**, `datatable.htm`): `Name` becomes the TDF
table name; `Size` becomes the table's TDF `size` field verbatim when it is a non-negative
integer (fixed-size table — **oracle-confirmed**, exp `#13`: `DataTable(Fixed,1,100)` → `size=100`
exactly, no rounding/adjustment observed). `Size=-1` triggers auto-allocation — see §9.

`interval`/`into` (both `NSec`, i.e. `(seconds, nanoseconds)`, nanoseconds always `0` in every
experiment):

- With `DataInterval(TIntoInt, Interval, Units, Lapses)` (**oracle + chm**, exp `#0` vs `#16`):
  `interval = Interval * unit_seconds`; `into = TIntoInt * unit_seconds` (`TIntoInt` and
  `Interval` share the `Units` argument's unit — confirmed with
  `DataInterval(30,1,Min,10)` → `into=(1800,0) interval=(60,0)`, i.e. 30 min = 1800 s, 1 min =
  60 s). `Lapses` had no observed effect on any TDF field (**open question** — presumably only
  affects a runtime skip-tolerance, not the definition).
- Without `DataInterval` (a table with only `Sample`/etc., no interval/event declaration) or with
  `DataEvent(...)` in place of `DataInterval`: `interval=(0,0)` and `into=(0,0)`
  (**oracle-confirmed**, exp `#5`, `#9`/`#22`). Per `datatable.htm`: without `DataInterval` "a
  time stamp is stored... with every record" — this matches the extra per-record byte cost
  measured for this class of table (§9).
- `OpenInterval`, `FillStop`, `CardOut(...)`, `TableFile(...)` (**oracle-confirmed**, exp
  `#10`/`#11`/`#14`/`#15`): none of these changed `time_type`, `into`, or `interval` in the TDF.
  `CardOut`/`TableFile` do change the auto-computed `size` (see §9); `FillStop` on a *fixed*-size
  table left `size` unchanged (still exactly the declared `N`).
- `DataEvent(RecBefore, StartTrig, EndTrig, RecAfter)` (**chm-documented**, `dataevent.htm`): only
  its effect on `interval`/`into` (both zero, as above) and on table size (§9) was checked; the
  `RecBefore`/`RecAfter` values were not varied.

## 6. `DataTableInfo`

Appears only when ≥1 user `DataTable` exists (§2). All 7 fields are rank-≥1 and scale with the
user-table count `n` by the §3 rule (**oracle-confirmed** for `n=1,2,3`, exp `#4`/`#6`/`#23`,
exact fit for all three):

| Field | Type | RO | `dim` | `subdims` (n tables) |
|---|---|---|---|---|
| `DataTableName` | ASCII | yes | `24n` | `[n, 24]` |
| `SkippedRecord` | LONG | no | `n` | `[]` if n=1, else `[n]` |
| `DataRecordSize` | LONG, units `records` | yes | `2n` | `[n, 2]` |
| `SecsPerRecord` | IEEE4 | yes | `n` | `[]` if n=1, else `[n]` |
| `DataFillDays` | IEEE4, units `days` | yes | `2n` | `[n, 2]` |
| `DataFilled` | ASCII | yes | `64n` | `[n, 2, 32]` |
| `LastPublish` | NSEC | yes | `n` | `[]` if n=1, else `[n]` |

Table itself: `size=1`, `interval=(0,0)`.

## 7. `ConstTable`

`ConstTable`/`EndConstTable` has 3 documented forms (`consttableendconsttable.htm`). Tested:

- **Bare `ConstTable ... EndConstTable`** (Option 1, no `TableName`) — **oracle-confirmed**, exp
  `#7`: produces **no TDF table at all**. Its `Const`s are presumably only exposed through the
  keyboard-display/terminal `C` command's own menu system, not through PakBus, so they never
  reach the `-z` output. `Units` on a bare-`ConstTable` `Const` is also rejected at compile time
  (`Can't assign units to A, it must be declared Public or referenced as a source in a Data
  Table.`) — bare-ConstTable constants are not addressable as ordinary fields at all.
- **Named `ConstTable(Name, Hidden)`** (Options 2/3) — **oracle-confirmed**, exp `#8`: produces an
  ordinary TDF table named `Name`, positioned after `DataTableInfo` and before `Public` (§2),
  containing each `Const` in declaration order (type inferred from the literal: integer literal →
  LONG (type 6), float literal → IEEE4 (type 9); not tested with a string constant) **plus one
  extra field appended automatically**: `ApplyAndRestart` (type 28/BOOLEAN, not read-only) — this
  is the documented flag that triggers recompilation with the edited values.
- `Hidden` (0/1) was only tried as `0`; effect on the TDF not checked (**open question** — likely
  only affects display, not the table definition).

## 8. `Public` table

**oracle-confirmed**, exp `#0`, `#4`, `#20`, `#24`–`#27`.

- Present only if ≥1 `Public` variable exists (§2); order = declaration order.
- `Dim` variables never appear (exp `#20`, `#24`: `Dim D As Float` alongside `Public`s — `D` is
  absent from the `Public` table and from the `-l` listing).
- Type codes: `Float`→9 (IEEE4), `Long`→6, `Boolean`→28, `String * n`→11 (ASCII).
- **`String * n` storage length**: `dim = (n div 4 + 1) * 4`, i.e. the smallest multiple of 4
  strictly greater than `n` (room for the string plus a NUL, rounded up to a 4-byte boundary).
  Confirmed for `n` = 1,2,3,4,5,10,12,13,500 → `dim` = 4,4,4,8,8,12,16,16,504 (exp `#25`, plus the
  original `Big As String * 500` case).
- Arrays: `Public Arr(3) As Float` → one field `dim=3 subdims=[3]` (not split into `Arr(1)`,
  `Arr(2)`, `Arr(3)` in the TDF — the `-l` table list *display* expands array fields into one
  line per element, but the underlying TDF field is singular; **do not confuse `-l` output with
  the TDF**). 2-D: `Public Grid(2,3) As Float` → `dim=6 subdims=[2,3]` when nothing is aliased
  out of it (exp `#26`).
- **`Alias`** (**oracle-confirmed**, exp `#26`/`#28`): `Alias Var = NewName` **replaces** the
  field's name outright in both the `Public` table and (per `alias.htm`) as the root name for any
  `DataTable` field derived from it — the TDF shows no separate "aliases" list entry for this
  case (`aliases=[]`); the original name is not retained as a secondary name anywhere in the TDF.
  Aliasing a **single element of a multi-element array** splits the array's one TDF field into up
  to three: a prefix field (original array name, `begin=1`, `dim`=elements before the aliased
  one, `subdims`=the array's full declared shape per §3), the aliased element as its own scalar
  field (the alias name, `begin=1`, `dim=1`, `subdims=[]` — its `begin` is **reset to 1**, i.e.
  self-relative, not the element's position in the original array), and a suffix field (original
  array name again, `begin`=aliased-index+1, `dim`=elements after it, same full `subdims`).
  Aliasing element 1 of a 5-element array yields only 2 fields (no empty prefix); aliasing element
  3 of a 5-element array yields all 3 (exp `#28`: `Arr5`(begin=1,dim=2,subdims=[5]), `Middle`
  (begin=1,dim=1,subdims=[]), `Arr5`(begin=4,dim=2,subdims=[5])).
- `Units X = Label` sets the field's `units` string; it does not otherwise change type/dim.

## 9. Output-instruction fields

One TDF field per output instruction call inside a `DataTable`, in the order the instructions
appear in source (**oracle-confirmed** throughout, exp `#29`–`#38`). Default field name =
`<Source>_<Suffix>`; `processing` and `description` were **identical strings in every
experiment** (no case produced different processing vs. description text) — treat them as one
value with two copies unless a counter-example turns up (**open question** for why the TDF has
two fields for this). Type = the instruction's `DataType` argument, mapped per §1.

| Instruction | Suffix | Tag (`processing`) | Fields produced | Notes |
|---|---|---|---|---|
| `Sample` | *(none)* | `Smp` | 1 | `dim`/`subdims`/`begin` per §3 |
| `Average` | `_Avg` | `Avg` | 1 | not separately varied beyond default |
| `Maximum`/`Minimum`, `Time=False` | `_Max`/`_Min` | `Max`/`Min` | 1 | |
| `Maximum`/`Minimum`, `Time=True` | `_Max`+`_TMx` / `_Min`+`_TMn` | `Max`/`TMx`, `Min`/`TMn` | 2 | timestamp field is type 14 (NSEC), read-only, immediately follows the value field |
| `Totalize` | `_Tot` | `Tot` | 1 | |
| `StdDev` | `_Std` | `Std` | 1 | |
| `Median` | `_Med` | `Med` | 1 | `MaxN` param not reflected in the TDF string |
| `Moment` | `_MMT` | `MMT` | 1 | `Order` param **not** reflected in the TDF string (unlike `Histogram`, below) |
| `SampleMaxMin` | `_SMM` | `SMM` | 1 | default name collides if called twice on the same Source in one table (`Duplicate field name ..._SMM`) — needs `FieldNames` to disambiguate, as CHM's own example does |
| `Histogram` | `_Hst` | `Hst,<Bins>,<WtVal>,<LoLim>,<UpLim>` (each numeric arg formatted `%.4e`) | 2 | field 1 = the `Sample`/source field (from the table's own `Sample` line, not Histogram itself); field 2 = `<BinSelect>_Hst`, `dim=Bins` |
| `WindVector` | `_WVc` | `WVc` | 1 | Field name = `<Speed/East arg>_WVc`; `dim` = f(`OutputOpt`): 0→3 (speed, dir, SD — **oracle-confirmed**), 1→2 (speed, dir — **oracle-confirmed**), 2→4, 3→1, 4→2 (**chm-documented**, not independently run). `SensorType` (polar/orthogonal) did not change `dim` in the one comparison made. |
| `Covariance` | `_Cov` | `Cov` | 1 | `dim = NumOfCov` as given (e.g. 21 for 6 aligned channels — the caller computes N(N+1)/2 itself; the instruction does not derive it) |
| `FieldNames("Name:Desc")` | — | `<original tag>:<Desc>` | — | **renames** the preceding output field to `Name` (splitting a Reps>1 field into one uniquely-named scalar field per repetition when given one `Name:Desc` per rep — exp `#33`: `Sample(3,TCTemp(),FP2)` + 3-part `FieldNames` produced 3 separate scalar fields `AirTemp`/`WaterTemp`/`SoilTemp`, not one `dim=3` field); appends `:Desc` to **both** the `processing` and `description` TDF strings (both still identical to each other) |
| `ETsz` | — | not tested | — | **open question**; CHM syntax recorded above the experiment log |

`Reps > 1` without `FieldNames`: one field, `dim=Reps`, per §3 (e.g. `Maximum(3,Temp(),FP2,...)` →
one `Temp_Max` field, `dim=3`).

## 10. Auto-allocated table size (`Size = -1`)

The hardest part; **partially decompiled, black-box-fit with high confidence for the interval-
table case, lower confidence for the no-interval/DataEvent case**. Mechanism read from
`FUN_004eab30`: for every table whose `Size` is `-1`, the compiler (1) finalises `Status`,
`DataTableInfo`, `Public`, any named `ConstTable`, and any *fixed*-size `DataTable` **first**,
consuming their record bytes out of a shared memory pool; (2) for the remaining auto tables,
accumulates a rate `Σ(bytes_per_record_i / interval_seconds_i)`; (3) queries the pool's remaining
free byte count and divides by that rate to get a **duration** (seconds) shared by **all** auto
tables; (4) sets each auto table's record count = `floor(duration / interval_seconds_i)`. This is
the "equal duration, not equal record count" rule below (**decompiled**, corroborating the
black-box fit exactly).

### Interval-based tables (have `DataInterval`)

**oracle-confirmed**, exp `#5`/`#6`/`#12`/`#13`/`#20`/`#23`/`#39`–`#45`. Record count:

```
records = floor( D / interval_seconds )
D = (Pool - reserved(n_tables)) / Σ_j bytes_per_record_j / interval_seconds_j   [sum over auto tables]
```

Fitted from field-width data (single auto table, `interval=1 s`, varying `DataType`):

| DataType | bytes/record | `size` | implied `Pool` bound |
|---|---|---|---|
| FP2 | 2 | 2,059,767 | `[4119534, 4119536)` |
| UINT2 | 2 | 2,059,767 | same |
| IEEE4 | 4 | 1,029,883 | `[4119532, 4119536)` |
| LONG | 4 | 1,029,883 | same |
| BOOLEAN | 4 | 1,029,883 | same (BOOLEAN, type 28, is 4 bytes/record — *not* the same as BOOL8) |
| IEEE8 | 8 | 514,941 | `[4119528, 4119536)` |
| BOOL8 ×2 reps | 1×2=2 | 2,059,767 | same as FP2 (BOOL8 = 1 byte/element) |
| String * 10 (12 stored) | 12 | 343,294 | `[4119528, 4119540)` |

Intersection of all 8 bounds: **Pool(1 table) ∈ {4,119,534, 4,119,535} bytes** — i.e. the formula
`records = floor(Pool / bytes_per_record)` fits all 8 independent field-width measurements to
within integer-rounding, essentially exactly (R² ≈ 1 in the sense that every measurement is
consistent with a single shared constant). The single-table `size` was observed to be
**independent of the actual `interval_seconds`** (1 s and 60 s gave the identical `size=2,059,767`
for a 1-field FP2 table) — expected from the formula above: for one table the sum
`Σ bytes/interval` is just `bytes/interval`, so `D = Pool*interval/bytes`, and
`records = D/interval = Pool/bytes` — `interval` cancels exactly, matching the observation.

**Multiple auto tables share the pool by equal duration, not equal record count**
(**oracle-confirmed**): two FP2 tables at 1 s and 300 s intervals got 2,050,333 and 6,834 records
— ratio 300.02, i.e. both got ~2,050,333 seconds of buffered history. Three identical FP2 tables
at 1 s each got 684,856 records each (≈ 2,059,568/3, not simply Pool/3/bytes, see below).

**Reserved pool shrinks by a fixed amount per *additional* auto table** (**oracle-confirmed**,
consistent across two independent experiments): computing effective `Pool` for n=2 (asymmetric
intervals) and n=3 (three identical 1 s tables) via the duration formula gives `Pool(2) ≈
4,114,335` and `Pool(3) ≈ 4,109,139`, i.e.

```
Pool(n auto tables) ≈ Pool(1) - 5198 * (n - 1)      [bytes; fitted 5197-5201 across 2 experiments]
```

Plausible source (**decompiled, not fully reconciled**): `DataTableInfo`'s own record grows by
~120 bytes per extra user table (§6), far too small to explain 5198 bytes alone; the finalisation
loop has a `local_18` (reserved) accumulator with other per-table terms not fully traced. Treat
5198 as an **empirical constant**, not a derived one.

**`CardOut`/`TableFile` add extra records to the auto-computed size** (**oracle-confirmed**, exp
`#14`/`#15`/`#44`): FP2 (2 bytes/record): `+80` records; IEEE4 (4 bytes/record): `+40` records.
Both cases: `extra_bytes = 160` exactly. Fitted rule:

```
extra_records = 160 / bytes_per_record        [when CardOut or TableFile is present]
```

`datatable.htm` states cards/interval tables store "a time stamp... on a fixed interval, about
once per 1K of memory" for reconstruction; a matching `(property << 4) / bytes_per_record + 1`
term exists in the decompiled finalisation code for tables with a nonzero "card" property, but it
was not algebraically reconciled to the clean `160/bytes` fit above — recorded as **open
question** (mechanism decompiled-only, magnitude oracle-confirmed).

### Tables without `DataInterval` (plain trigger, or `DataEvent`) — when it is the *only* auto table

**oracle-confirmed but not reconciled to the interval-table formula above** — behaves as a
**separate regime** when no `DataInterval`-based auto table coexists in the program, matching
`datatable.htm`'s statement that these tables store an explicit timestamp with *every* record
(extra per-record bytes) rather than the periodic-checkpoint scheme interval tables use. **This
regime does NOT apply once a real `DataInterval` auto table is also present — see below.** Single
such auto table, alone in the program:

| DataType | bytes/record | `size` |
|---|---|---|
| FP2, no DataInterval at all | 2 | 232,674 |
| FP2, `DataEvent(0,Trig,0,0)`, `Scan(1,Sec,...)` | 2 | 232,674 |
| FP2, `DataEvent(0,Trig,0,0)`, `Scan(2,Sec,...)` | 2 | 232,674 (**scan interval has no effect**) |
| IEEE4, `DataEvent(0,Trig,0,0)` | 4 | 209,406 |
| IEEE8, `DataEvent(0,Trig,0,0)` | 8 | 174,504 |

Fitting `records = floor(Pool2 / (bytes_per_record + overhead))` to each pair of the 3 rows gives
**`overhead = 16` bytes exactly** every time (FP2/IEEE4 solve: 16.0; FP2/IEEE8 solve: 16.0) —
a strong fit across 3 independent field widths. Back-substituted `Pool2` ranges
4,188,096–4,188,132 across the 3 rows (consistent within floor-rounding: the spread shrinks
exactly as expected for `bytes+16` up to 24). `Pool2` does not equal the interval-table `Pool(1)`
(4,119,534) — plausibly a different remaining-free-memory snapshot at a different point in
compilation. Scan-interval independence (confirmed by direct A/B, exp `#43`) rules out "assumed
rate = program scan rate" as the mechanism. Confidence: oracle-confirmed *values and overhead
constant*, decompiled-only / unresolved *why `Pool2` differs from `Pool(1)`*.

### Event/no-interval table *sharing a program* with a real `DataInterval` auto table

**oracle-confirmed; mechanism now resolved for the weighting rule, absolute pool size still
open.** One `DataInterval` FP2/1s auto table plus one `DataEvent` FP2 auto table, both
`Size=-1`, in the *same* program (exp `#52`): **both** tables got `size=208,811` — identical to
each other, and roughly 10x smaller than the `DataInterval` table would get alone (2,059,767
solo). This rules out simple independent pools (which would predict ~2,059,767 and ~232,674
respectively, per the two standalone regimes above).

Follow-up (exp `#53`) changed `Interval1`'s `DataInterval` to 5 s (keeping `Event1` as
`DataEvent`, both still FP2/`Size=-1`): `Interval1 size=45,450`, `Event1 size=227,250` — ratio
**exactly 5.0**. Substituting into the standard equal-duration formula (§10 top) with `Event1`'s
weighting interval fixed at **1 s**: `D = records_Interval1 * 5 = 227,250` and
`records_Event1 = D / 1 = 227,250` — matches exactly. **Resolved**: when a
`DataEvent`/no-interval auto table shares a program with >=1 real `DataInterval` auto table, it
*does* join the same equal-duration pool as an ordinary participant, using a **fixed assumed
weighting interval of 1 second** (not the program's `Scan` interval, not the other table's
interval — confirmed by varying the latter from 1 s to 5 s and seeing the ratio track it
exactly). **Not resolved**: the absolute `Pool_shared` implied by the two mixed experiments does
not agree (`Pool_shared ~ 835,244` for the 1 s/1 s case vs. `~545,400` for the 5 s/event case,
both computed as `D * Sum(bytes/interval)`) — i.e. the equal-duration *ratio* rule is solid but
the *absolute pool size* when an event table is present is not the same constant as the pure-
`DataInterval` case (`Pool(1)`) and is not yet reconciled to a single formula. Flagged as **open
question**.

### Not investigated

`SlowSequence` tables' effect on auto-sizing, `OpenInterval`'s effect on auto-sizing (its header
fields were unaffected — §5 — but size wasn't separately isolated with OpenInterval alone vs.
Cardout), `WorstCase` trigger modifier, fixed + auto tables mixed with more than 2 tables, the
`DataTableInfo`/`Public` contribution broken out field-by-field. Given as **best current formula
with fit statistics** per the task brief, not a closed-form derivation.

## 11. Experiment log

Every experiment was run as `python3 tools/tdf_probe.py <snippet.cr6> [--raw]` from
`opencrbasic/tools/`, snippet written to a temp file, compiled with
`CR6Comp.exe -z out.tdf -l out.lst` under Wine. Only the parts that matter are shown; full
snippets are reconstructable from the description (all are plain CRBasic, no tricks).

| # | Snippet (key lines) | Key result |
|---|---|---|
| 0 | `Public X As Float` only, no DataTable | Status (52 fields, see `data/status_table.json`) + Public(X); no DataTableInfo |
| 1 | + `DataTable(Test,1,-1) DataInterval(0,1,Sec,10) Sample(1,X,FP2)` | order Status,Test,DataTableInfo,Public; `Test size=2059767` |
| 2 | #1 + one `SlowSequence...EndSequence` (PanelTemp) | Status +4 fields (`SkippedSlowScan` etc.), all scalar |
| 3 | #1 + two `SlowSequence` blocks | same 4 fields, all `dim=2 subdims=[2]` |
| 4 | #1 + `ConstTable / Const A=1 / Const B=2.5 / EndConstTable` (bare) | `Can't assign units...` (Units on bare-ConstTable const rejected); without Units line: compiles, **no ConstTable table appears** |
| 5 | #1 with `DataTable(Test,Trig,-1) DataEvent(0,Trig,0,0)` instead of DataInterval | `interval=(0,0)`; `size=232674` |
| 6 | Two `DataTable`s, `DataInterval` 1 s and 5 min, both `Sample(1,_,FP2)` | sizes 2,050,333 / 6,834; DataTableInfo dims ×2 |
| 7 | Bare `ConstTable` (no name), no user table | no ConstTable table in TDF |
| 8 | `ConstTable(MyConsts,0) Const A=1 Const B=2.5 EndConstTable` + 1 DataTable | table `MyConsts` after `DataTableInfo`, before `Public`; fields `A`(LONG),`B`(IEEE4),`ApplyAndRestart`(BOOLEAN) |
| 9 | `DataTable(Test,1,-1) Sample(1,X,FP2)`, no DataInterval/DataEvent | `interval=(0,0)`; `size=232674` (matches #5) |
| 10 | #1 + `OpenInterval` right after DataInterval | no header/size change vs #1 |
| 11 | #1 + `CardOut(0,-1)` | `size=2059847` (+80 vs #1's 2059767) |
| 12 | `DataTable(Fixed,1,100) DataInterval(...) Sample(1,X,FP2)` | `size=100` exactly |
| 13 | `DataTable(Test,1,50) FillStop DataInterval(...) Sample(1,X,FP2)` | `size=50` exactly |
| 14 | #1 + `TableFile("CRD:test_",64,-1,0,1,Day,0,0)` | `size=2059847`, identical to #11 |
| 15 | (folded into #11/#14) | |
| 16 | `DataInterval(30,1,Min,10)` | `into=(1800,0) interval=(60,0)` |
| 17 | `Public Arr3(3)`; `Sample(1,Arr3(1),FP2)` vs `Sample(2,Arr3(1),FP2)` | `dim=1 subdims=[3]` vs `dim=2 subdims=[3]` |
| 18 | `Sample(1,Arr3(2),FP2)` | `begin=2 dim=1 subdims=[3]` |
| 19 | (folds into §3 general rule, covered by #17/#28) | |
| 20 | `Dim X As Float` only, no Public, no DataTable | Status only (no Public, no DataTableInfo) |
| 21 | `Scan(30,Min,3,0) / Battery / SubScan(15,mSec,10000) / VoltDiff / NextSubScan / NextScan` | Status identical (52 fields) to #0 |
| 22 | Two consecutive top-level `Scan...NextScan` blocks | Status identical; warning "Scan never reached at run time" |
| 23 | Three `DataTable`s, all FP2, 1 s interval | sizes 684,856 each; DataTableInfo dims ×3 |
| 24 | `Public F,L,B,S(10),Big(500),Arr(3)` + `Dim D` + `Units F` + `Alias F=MyFloat` | `-l`: `MyFloat` first (Units shown), no `D`; TDF: `MyFloat`(9),`L`(6),`B`(28),`S`(11,dim12),`Big`(11,dim504),`Arr`(9,dim3) |
| 25 | `Public S1..S13 As String*{1,2,3,4,5,12,13}` | dims 4,4,4,8,8,16,16 |
| 26 | `Public Grid(2,3)`; `Alias Grid(1,1)=Cell11`; `Units Cell11=degC` | Public: `Cell11`(scalar,units) then `Grid`(begin=2,dim=5,subdims=[2,3]) |
| 27 | (folds into #26) | |
| 28 | `Public Arr5(5)`; `Alias Arr5(3)=Middle` | Public: `Arr5`(begin=1,dim=2,subdims=[5]), `Middle`(begin=1,dim=1,subdims=[]), `Arr5`(begin=4,dim=2,subdims=[5]) |
| 29 | `Sample`,`Sample`,`Maximum(F=False)`,`Minimum(F=False)`,`Average`,`Totalize`,`StdDev` on one table | suffixes `_Max,_Min,_Avg,_Tot,_Std`, tags `Max,Min,Avg,Tot,Std` |
| 30 | `Maximum(...,True)`,`Minimum(...,True)` (Time=True) | 4 fields: `_Max,_TMx,_Min,_TMn`; `_TMx`/`_TMn` type 14 |
| 31 | `Sample(3,Temp(),FP2)`, `Maximum(3,Temp(),FP2,False,False)` | one field each, `dim=3 subdims=[3]` |
| 32 | `WindVector(1,WS_ms,WindDir,IEEE4,0,0,0,0)` vs `...,0,0,1,1)` | `WS_ms_WVc` dim=3 vs dim=2 |
| 33 | `Median(3,Temp(),60,FP2,False)`; `Moment(3,Temp(),2,FP2,False)` | suffixes `_Med`/`_MMT`, tags `Med`/`MMT` (Order=2 not encoded) |
| 34 | `Sample(1,Tblock(),FP2)`; `Histogram(Tblock(),FP2,0,2,1,100,10,30)` | `Tblock_Hst` dim=2, tag `Hst,2,1.0000e+02,1.0000e+01,3.0000e+01` |
| 35 | `SampleMaxMin` called twice on same Source, no FieldNames | `Duplicate field name WindDir_SMM` compile error |
| 36 | same, with `FieldNames("WindDir_smpMax")`/`("WindDir_smpMin")` | fields renamed, tag stays `SMM` |
| 37 | `Sample(3,TCTemp(),FP2) FieldNames("AirTemp:Air,WaterTemp:Water,SoilTemp:Soil")` | 3 scalar fields `AirTemp,WaterTemp,SoilTemp`, tags `Smp:Air` etc. |
| 38 | `Covariance(6,aligned_data(1),IEEE4,FALSE,21)` on a 6-element aliased array | `Uz_Cov` dim=21, tag `Cov` |
| 39 | Single table, `Sample(1,X,IEEE4)` | `size=1,029,883` |
| 40 | Single table, `Sample(1,X,Long)` | `size=1,029,883` (same as IEEE4) |
| 41 | Single table, `Sample(1,X,IEEE8)` | `size=514,941` |
| 42 | Single table, `Sample(1,X,Boolean)` | `size=1,029,883` (BOOLEAN=4 bytes) |
| 43 | Single table, `Sample(2,X(),BOOL8)` | `size=2,059,767` (BOOL8=1 byte/element) |
| 44 | Single table, `Sample(1,X,UINT2)` | `size=2,059,767` (same as FP2) |
| 45 | Single table, `Sample(1,X As String*10,String)` | `size=343,294` (12 bytes stored) |
| 46 | Single table, `Sample(2,X(2),FP2)` | `size=1,029,883` (2 fields×2 bytes = same total bytes/record as one 4-byte field) |
| 47 | 3 identical FP2/1s tables | `size=684,856` each |
| 48 | DataEvent table, `Scan(2,Sec,...)` instead of `Scan(1,Sec,...)` | `size=232,674`, unchanged from `Scan(1,Sec,...)` |
| 49 | DataEvent table, `Sample(1,X,IEEE4)` | `size=209,406` |
| 50 | `DataTable(Test,1,-1) DataInterval(...) CardOut(0,-1) Sample(1,X,IEEE4)` | `size=1,029,923` (+40 vs #39's 1,029,883) |
| 51 | `DataTable(TableB,...) Sample(Y)`, `DataTable(TableA,...) Sample(X)` declared in that order, but `CallTable TableA` then `CallTable TableB` in the Scan | TDF order `TableB, TableA` — **declaration order**, not call order |
| 52 | `DataTable(Interval1,1,-1) DataInterval(0,1,Sec,10) Sample(1,X,FP2)` + `DataTable(Event1,Trig,-1) DataEvent(0,Trig,0,0) Sample(1,Y,FP2)`, both auto | both `size=208,811` (identical) |
| 53 | same as #52 but `DataInterval(0,5,Sec,10)` on `Interval1` | `Interval1 size=45,450`, `Event1 size=227,250` (ratio 5.0) |
| 54 | DataEvent auto table, `Sample(1,X,IEEE8)` (solo, no DataInterval table) | `size=174,504` |

## 12. Open questions (summary)

1. `time_type` — always observed as `14`; whether/when it differs is unknown.
2. ~~Table order vs. `CallTable` order~~ — **resolved**, exp `#51`: declaration order, confirmed
   by decoupling it from call order.
3. Whether `Settings`/other system tables ever appear in the TDF (alarms, PakBus ports, etc.).
4. `ConstTable`'s `Hidden` parameter's effect on the TDF (only tried as `0`).
5. `processing` vs `description` — always identical text in every experiment; is there a case
   where they differ?
6. `ETsz`, `SampleMaxMin` `DisableVar` semantics, string-typed `Const` in a named `ConstTable`
   (one syntax attempt, `Const A As String * 20 = "hello"`, was rejected at compile time as a
   syntax error — the correct declaration form for a string constant wasn't found).
7. The exact source of the ~5198-bytes-per-extra-auto-table pool reduction for pure-
   `DataInterval` tables (§10) — magnitude is solid (2 independent experiments agree), mechanism
   isn't traced past "some term in the finalisation loop's reserved accumulator".
8. The exact mechanism (and generality) of the `CardOut`/`TableFile` `+160/bytes_per_record`
   record bonus — decompiled code suggests a `(property<<4)/bytes+1`-shaped term tied to
   periodic-timestamp checkpointing, not algebraically reconciled to the clean black-box fit.
9. The no-DataInterval/`DataEvent`-only sizing regime (no `DataInterval` table present): the
   `overhead=16 bytes` fit is now 3-point-confirmed (FP2/IEEE4/IEEE8, exp `#5`/`#49`/`#54`), but
   why its implied `Pool2` (~4,188,1xx) differs from the pure-interval `Pool(1)` (4,119,534) is
   unexplained.
10. **Now partly resolved** (exp `#52`/`#53`): when a `DataEvent`/no-interval auto table shares a
    program with a real `DataInterval` auto table, it joins the *same* equal-duration pool with a
    fixed assumed weighting interval of 1 s (confirmed by varying the other table's interval and
    watching the ratio track exactly). Still open: the absolute pool size in that mixed case
    doesn't match either single-regime pool constant (§10) — needs a 3rd mixed data point (e.g. a
    3rd, larger interval on the `DataInterval` table, or 2 event tables + 1 interval table) to
    pin down.
11. `SlowSequence` auto-table sizing, `WorstCase` trigger modifier, `Alias` "swath" form
    (`Alias VariableName(1) = AliasName(5)` aliasing multiple elements on one line) — not tested.
12. UINT1, UINT4, FP3, FP4 field type-code bytes in the TDF — not produced in any experiment
    (UINT1 requires Reps divisible by 2 and a UINT1-typed source, not attempted).
