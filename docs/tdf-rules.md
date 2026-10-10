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
`17`=BOOL8, `18`=IEEE8, `28`=BOOLEAN, `3`=UINT4 (oracle 2026-10-10: `Sample(1,Y,UINT4)`). Not directly observed: UINT1,
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

3a. `CPIStatus` (8 fields: `BusLoad` FLOAT ro, six `LONG`s, `ModuleInfo` STRING dim 6400 subdims
   `[50,128]`) — **oracle-confirmed** (14 CHM programs plus synthetic probes): present iff the
   program uses a `CDM_*` instruction or `CPIFileSend`, positioned after `DataTableInfo` and
   *before* named `ConstTable`s. `CPIAddModule` alone does not add it; `CDM_A108` etc. as a
   module constant does not either.

3b. **Structure tables** (**oracle-confirmed**, 4 CHM programs plus probes, fixtures
   `struct_*` in `tests/fixtures/tdf_rules.json`): every `Public`/`Dim` variable of a
   `StructureType` becomes a table named after the variable (size = product of its dims, one field
   per member: type, `ReadOnly`, `Units` from the type; String members use the usual stored length),
   placed right after `Status`, *before* user `DataTable`s, in **reverse declaration order** across
   `Public` and `Dim` (even inside `BeginProg`). Such variables are not in `Public`. `DataTableInfo`
   is present whenever structure tables exist, with **zero fields** if there is no `DataTable`.
   Nested structure members give garbage tables (`z.sb`) and are not modelled.

3c. `CH400Status(...)` adds a 32-field `CH400Status` table (`_CH400_FIELDS`; temperature units are the
   raw bytes EF BF BD 'C') and, like structure tables, forces `DataTableInfo` (zero fields without
   `DataTable`); it sits right after `Status` (`ch400status_1.CR6`; its position relative to
   structure tables/`DataTable`s is untested).

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

## 9a. Structure sources and `FieldNames` details (oracle-confirmed 2026-10-04)

- `Sample/Average/Totalize/Maximum/Minimum(n, Struct[(i)], Type)` emit, per element (n of them) per
  member, one field: name = member (+`_Avg`/`_Tot`/`_Max`/`_Min`), `begin=1`, dim/subdims of the
  member, `ro`/units from the *member* (not always `ro`). `FieldNames("a,b,c")` names element k:
  `<name>_<member>`, with no instruction suffix; the name is **not** stripped (`"b "` gives
  `b _f`). Time-of-extreme fields of a structure source were not probed (not modelled).
- `FieldNames` `name:desc`: the description is appended raw (`Smp: d `), only the name is stripped
  (non-structure sources).
- When `FieldNames` splits an array source into scalar fields, each *named* field has `begin=1`
  (not `source begin + k`); the unnamed remainder keeps `begin = source begin + names`.
- `Sample(1, S(i).member, Float)` takes its units from the structure member.

- String sources (`Sample` of a `String` variable): `dim = reps * stored_len`,
  `subdims = [array dims..., stored_len]`, `begin = (index-1)*stored_len + 1` (bytes, oracle-
  confirmed). `-l` lists the *elements* for such a field (bytes divided by `stored_len`).
- `DataInterval(into, interval, unit, Lapses)` with `Lapses = 0` (4th argument, constant 0):
  **oracle-confirmed** (5 CHM programs): the TDF header has `interval=(0,0)` and the table is
  sized like a no-interval table, but `-l` still prints `Interval: <n> <UNIT>`.
- A one-element array source (`Public A(1)`) has `subdims=[]` in its output field (§3 collapse).

- **Alias-split output fields** (oracle-confirmed, fixture `alias_split_output_sources`, 5 CHM
  programs): an output instruction whose Source array has element `Alias`es follows the Public
  table's decomposition over the covered elements: each aliased element is a scalar field named
  `<alias><suffix>` (`begin=1`, the alias' units), each un-aliased run keeps `<source><suffix>`
  with `begin` = its first element and `dim` = its overlap length. `Minimum(...,True)` emits all
  `_Min` fields then all `_TMn` fields.
- A `DataInterval` table with `2*bytes > 1008` (i.e. fewer than 2 default records; boundary
  oracle-confirmed: b=504 keeps the interval, b=506 loses it) gets header
  `interval=(0,0)` and the no-interval size formula (`instructiontimes_2.CR6`, fixture
  `solo_interval_auto_2000_bytes`); `-l` still prints `Interval:`.

- Alias splitting only applies to a field whose `dim == Reps * element size` (fixture
  `alias_unsplit_when_dim_not_reps`, oracle-confirmed): `Covariance(6,A,IEEE4,False,21)` is one
  `dim=21` field named after the alias at the Source's first element (`First_Cov`; otherwise the
  ordinary name), never split. `String` elements count `stored_len` bytes: with `Alias S(2)` on
  `S(3) As String * 4`, the Public table and a `Sample(3,S,String)` give `S begin=1 dim=8`,
  `Second begin=1 dim=8 subdims=[8]`, `S begin=17 dim=8`.
- `FieldNames` names the *individual* outputs of the un-split field (fixture
  `alias_fieldnames_individual_outputs`): each named output is a scalar (`begin=1`, `subdims=[]`,
  also for `Sample(1,A(2),FP2)`); only the unnamed remainder then follows the alias decomposition
  (`Sample(6,A)` + 2 names + `Alias A(3)`: `F1`,`F2`,`Mid`,`A begin=4 dim=3`).
  Names are consumed per element across the instruction's output fields in order (fixtures
  `fieldnames_multifield_*`, `fieldnames_string_alias_remainder`): `Minimum(2,A,FP2,False,True)` +
  `"M1,M2,T1,T2"` is four scalar fields, with `"M1"` alone it is `M1`, the rest of `Min`
  (alias-split) and the whole `TMn` field. A named String element keeps its stored size
  (`dim=8 subdims=[8]`).

- An array source's field `subdims` is the *declared* shape whatever `reps` is
  (`Sample(3,A,FP2)` on `A(10)`: `[10]`; `Sample(24,M3D,FP2)`: `[2,3,4]`), and a multi-index
  `M(2,1)` has `begin` = row-major flat index (`4` for `M(2,3)`) (oracle-confirmed, fixture
  `source_array_declared_shape_and_flat_begin`, `dim_1`/`public_1` CHM).
- `DataInterval(into, 0, unit, lapses)` (interval 0): the header interval and the sizing weight are
  the enclosing `Scan`'s interval (`modemhangupendmodemhangup_1`, five ms-scan CardOut programs); `-l`
  prints that interval in the declared unit (`Interval: 50 MSEC`).

- Multi-dimensional `Alias` (`Alias w(2,2)=x` on `w(2,3)`): flat row-major element index, split
  like the 1-D case, run fields keep the full declared shape. A `Source` that names an element
  alias (`Sample(5, Ux, ...)`) is that element of the aliased array (alias-split as above, 4 CHM
  programs). `Units` on the declared array name applies to every split piece; time-of-extreme
  (`TMx`/`TMn`) fields carry the source's units (`cdmtccomp_1`).

- `WindVector` units (oracle-confirmed, all 5 Output options): the field's units are the speed
  source's for options 0-2 and `Deg` for 3-4; once `FieldNames` splits it, element units are
  option 0 `[spd,Deg,Deg]`, 1 `[spd,Deg]`, 2 `[spd,spd,Deg,Deg]`, 3 `[Deg]`, 4 `[Deg,Deg]`.

## 9b. FFT fields, multi-dimensional subscripts, `Table.Field` sources (oracle-confirmed 2026-10-08)

Evidence: `tests/fixtures/tdf_rules.json` cases `fft_layout`, `multidim_expression_subscript_begin`,
`other_table_field_source`, and CHM `fft_1`, `cdmfftfilt_1`, `goestable_1`.

- **`FFT(Source, DataType, N, Tint, Units, Option)`** emits one read-only field named
  `<Source>[<flat element index>]_FFT` (the index is appended only when > 1: `v(1)` -> `v_FFT`,
  `v(2)` -> `v2_FFT`, `m(2,3)` of `m(4,40)` -> `m43_FFT`). Type from `DataType` (FP2 7, IEEE4/Float 9,
  Long 6, IEEE8 18, UINT2 2), `begin=1`, units of the source. Processing and description are both
  `FFT,<N>,<Tint in seconds as %.4e>,<Option mod 100>` (`FFT,16,1.0000e-02,1`; hr/day/usec scale).
  Layout depends on `Option mod 10`, `half = N/2 + 1`: 0 and 2: `dim=2*half`, `subdims=[half,2]`;
  1, 3, 4: `dim=half`, `subdims=[half]`; 5 (inverse): `dim=N`, `subdims=[N]`; 6-9 (undocumented but
  accepted): `dim=2*half`, `subdims=[half]`. `dim=1` (N=1) takes the source's declared shape. Option
  100+k behaves as k; `N` must be a power of two (compiler error otherwise). `FieldNames` splits
  an FFT field like any other (remainder keeps `begin` and `subdims`).
- `-l` lists a rank-1 field longer than its subdim (options 6-9) with its index running on past the
  subdim: `(1)..(34)` for `dim=34`, `subdims=[17]`.
- **Multi-dimensional `Source` subscripts**: `begin` is the row-major flat index only when every
  subscript is a plain numeric literal or a named constant. Any expression (`g(2,8/2)`,
  `CC-5`, `-(-3)`, `4*1`) gives `begin=1`; 1-D subscripts always fold (`a(8/2)` -> `begin=4`).
  Parentheses around a literal are transparent. This is why CHM `cdmfftfilt_1`'s
  `Dest(1,FFT_LEN/2+2)` has `begin=1` and no out-of-bounds error.
- **`Sample(n, Table.Field, ...)` where `Table` is an earlier DataTable**: the field is named like the
  source field (`HR_DATA.battery_voltage_Avg` -> `battery_voltage_Avg`, `Average` of it ->
  `x_Avg_Avg`), `begin=1` whatever subscript is given, `subdims` is the source field's shape, and
  units are *not* inherited (`''`) -- except when the field name is also a `Public` variable (then
  that variable's units).

### DataType argument and indexed sources (oracle-confirmed 2026-10-10)

- The `DataType` argument of every output instruction is resolved by constant value, not by
  spelling: `FP2+0` and a named `Const DT = FP2` give type 7 like `FP2` (predefined data-type
  symbols equal the TDF codes; UINT4 is 3). FFT, Average, Maximum, Sample, Covariance probed.
- A Source that is an array element other than the first appends its flat 1-based index to the
  field name for `FFT`, `Covariance`, `Histogram` and `WindVector` (`Covariance(3,A(2),..)` ->
  `A2_Cov`, `B(2,2)` of a 2x4 array -> `B6_Cov`); `Average`/`Median`/`Moment`/`StdDev`/`Maximum`/
  `Totalize`/`SampleMaxMin` carry no index (they use `begin`). When the element has an `Alias`
  the field is named after the alias with no index (`AA_Cov`, `WA_WVc`).

## 10. Auto-allocated table size (`Size = -1`)

**Fully decompiled and exact** (2026-09-26 pass, superseding the "partially decompiled, lower
confidence for the mixed-regime pool magnitude" note this section previously carried; see
`docs/binary.md` for the method). Mechanism read from `FUN_004eab30` (the table finalisation loop)
and `FUN_004f4400`/`thunk_FUN_004f4400` (the per-table probe/commit allocator it calls), then
verified two independent ways: a 4-point synthetic oracle grid (one `DataInterval` auto table
competing against one `DataEvent` auto table, varying the event table's enclosing-`Scan` weight and
byte width) and all 8 real `crbasic/` corpus programs that exercise this regime — both agree on the
same formula and the same pool constant to the byte, and `tools/tdftest.py --group repo` is now
**16/16 TDF byte-identical** (previously 8/16; the other 8 differed only in this section's sizes).

For every table whose `Size` is `-1` **and** that is `CallTable`d somewhere in the compiled program
(table-struct flags dword at offset `+0x1c`, bit `0x1000` — set by the `CallTable`-reference
resolver `FUN_004f6540`, and checked by the exact `Table <Name>, is declared but never called.`
warning path in `FUN_004eab30` itself, so this is the same flag §13's "uncalled → size=1" rule
already describes), the compiler: (1) finalises `Status`, `DataTableInfo`, `Public`, any named
`ConstTable`, and any *fixed*-size `DataTable` **first** (these do not consume the auto-size pool
at all — their finalisation call is reached through a code path that never touches the loop's
reserved-bytes accumulator; matches §13's "fixed/const/slowseq don't reduce the pool" finding
exactly); (2) for every called auto table, gives it a **default record count**
(`_table_default_records` in `tables.py`): `floor((1024 − 16) / bytes_per_record)` for a table with
a real `DataInterval`, or exactly `1` for a `DataEvent`/no-interval table — the flag `thunk_
FUN_004f4400` branches on for this (`param_5[6]`, i.e. table-struct offset `+0x18`) is *not* "has
CardOut/TableFile" as an earlier reading of this section guessed from its later reuse in the
CardOut-bonus term (§10 "CardOut"/"TableFile" below) — it is set for every `DataInterval` table
regardless of CardOut, oracle-confirmed by a synthetic A/B test (a `DataEvent` auto table heavily
squeezed by a competing `DataInterval` table shrinks to a record count *below* its own
`floor(1008/bytes)`, e.g. `171` for a 4-byte table, never bumped up to it — the competing
`DataInterval` table's *own* size, by contrast, reliably is); (3) accumulates two running sums over
every active (called, auto) table: a **reserved-bytes** total (`_table_reserved_bytes`: starting at
a fixed `4096`, `t8 = default_records·bytes_per_record + 16` per table, then `(floor(2048/t8) +
(1 if default_records==1 else 0) + (1 if t8<1024 else 3)) · t8` added for each) and a **rate**
(`_table_rate_contrib`: `t8 / (default_records · weight_i)`, `weight_i` = a `DataInterval` table's
own interval, or a `DataEvent`/no-interval table's *enclosing* `Scan`/`SlowSequence` interval — see
"scan-interval weighting" below); (4) `pool = M − reserved`, `duration = pool / rate`, where `M`
(`_MIXED_POOL` in `tables.py`) is a single fitted constant, `4,194,139` — the compiler's own
free-memory query (`FUN_00508f40(1)`) at the point this regime's finalisation runs; (5) **one pass,
no iteration**: every active table's final size is `max(floor(duration / weight_i),
default_records)` — the "cap" a table with a real `DataInterval` can hit is exactly its own
step-2 default (`floor(1008/bytes)`), applied as a plain per-table floor on the *single* shared
`duration`, not a removal-and-recompute water-fill (the earlier, approximate implementation's
model); a `CardOut`/`TableFile` table gets `160/bytes_per_record` extra records added on top (§10
"CardOut"/"TableFile" below), unchanged from the pure-`DataInterval` regime's own rule. This is
*not* the same pool constant as `_POOL1` (§10 "Interval-based tables", the pure-`DataInterval`-only
regime) or `_EVENT_ALONE_POOL2` (§10 "Tables without `DataInterval`", the solo-event-alone regime)
— the compiler's free-memory snapshot genuinely differs between these three code paths (open
question 9 below), and `tables.py` keeps each regime's own fitted constant rather than assuming
they coincide.

### Interval-based tables (have `DataInterval`)

**2026-10-04**: a *single* interval auto table follows the mixed-regime formula (§10 top) with
`_MIXED_POOL = 4,194,140` (was 4,194,139: the exact b=2 point needs `M >= 4194139.43`) and exact
rational arithmetic (no float truncation: `2059767`, not `2059766`). 14 oracle points, b = 2..2000:
all exact except b=52 (79236 vs 79235) and CHM `tga_1` (b=128: 32139 vs 32138): for `t8 < 1024`
the fitted reserved bytes look near-constant (~7144) instead of `4096 + 3*t8`; unresolved. Two or
more pure-interval tables still use the older `_POOL1 - 5198*(n-1)` fit (the mixed formula breaks
`Shunt_tester.CR6`, which is 2 interval tables).

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

**2026-10-04 correction**: the table of exact widths below is reproduced by the *mixed-regime
formula* with `n=1` and a solo pool `M = 4,194,298` (`_SOLO_POOL`):
`size = floor((M - 4096 - reserved) / (bytes+16))`, `reserved = (floor(2048/t8) + 1 + segs) * t8`,
`t8 = bytes+16`. Fitted on 23 oracle points (2 to 4004 bytes/record; M is bounded to
`[4194298, 4194304)`) and, with `n>1` event tables and *no* `DataInterval` auto table, by the same
formula with the same `M` (`fileread_1.CR6`: two 24-byte tables, 52324 each); `_MIXED_POOL`
(4,194,139) applies once an interval auto table is present. The old `bytes+16` / `Pool2` model failed above ~100 bytes (e.g. two
`String*50` fields, 104 bytes: 34899, not 34901).

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

**oracle-confirmed; the weighting rule is corrected below (2026-09-25) — exp `#52`/`#53`'s "fixed
1 second" reading was a confound, not the real rule. Absolute pool size in this regime is still
only an approximate fit, not a closed form.**

One `DataInterval` FP2/1s auto table plus one `DataEvent` FP2 auto table, both `Size=-1`, in the
*same* program (exp `#52`): **both** tables got `size=208,811` — identical to each other, and
roughly 10x smaller than the `DataInterval` table would get alone (2,059,767 solo). This rules out
simple independent pools (which would predict ~2,059,767 and ~232,674 respectively, per the two
standalone regimes above). Follow-up (exp `#53`) changed `Interval1`'s `DataInterval` to 5 s
(keeping `Event1` as `DataEvent`, both still FP2/`Size=-1`): `Interval1 size=45,450`, `Event1
size=227,250` — ratio **exactly 5.0**, read at the time as "`Event1`'s weight is a fixed 1 s".

**Corrected (2026-09-25, oracle-confirmed against the real `crbasic/` corpus): a `DataEvent`/no-
interval auto table's weight is not a flat 1 second — it is the interval, in seconds, of the
nearest enclosing `Scan` or `SlowSequence`'s own `Scan` block that contains its `CallTable`
statement.** Exp `#52`/`#53` both happened to use `Scan(1,Sec,...)`, which is why "1 second"
fit — it was the enclosing scan's interval, not a hard-coded constant. Proof: `FinApp_tester.CR6`
has a single top-level `Scan(5,Sec,0,0)` (both `StationStatus` and `FinApp_Data` called from it);
the real compiler gives `StationStatus size=941`, `FinApp_Data size=56,472`. Reproducing this
exact scenario synthetically (`DataTable(Station,1,-1) DataInterval(0,300,Sec,10)` +
`DataTable(Event0,Trig,-1)` with no `DataInterval`, both `CallTable`d inside one
`Scan(5,Sec,0,0)`) gives the **identical** `941`/`56,472` byte-for-byte; the same synthetic
program with `Scan(1,Sec,...)` instead gives `252`/`56,513` — a completely different pair,
confirming the enclosing scan's interval (not the DataInterval table's own interval, not a flat
1 s) sets the event table's weight. Cross-checked against `Main.CR6`: its `StationStatus` sits in
the top `Scan(MainScanSecs=15,Sec,...)` while all six event tables (`Acclima_Data`, `TString_Data`,
`SnowVue_Data`, `SnowVue_Diagnostics`, `PT1000_Data`, `FinApp_Data`) are `CallTable`d inside
`SlowSequence` blocks whose own `Scan(SeqTickSecs=5,Sec,...)` differs from the top scan — matching
the rule's "nearest enclosing `Scan`" wording, not "the program's main scan". `tables.py`
implements this as `_scan_weights` (a flat pass over the statement list tracking a `Scan`/
`NextScan` stack); `SubScan` is treated as transparent (untested — extrapolated from §4's "`SubScan`
has no effect [on Status]" finding, not independently verified for auto-sizing).

**Capping — resolved exactly (2026-09-26, decompiled and oracle-confirmed), superseding this
section's earlier "water-filling"/iterative-removal reading.** `FUN_004eab30` computes **one**
shared `duration` from **every** active auto table in a single pass (§10 top, step 4) — it never
removes a table and recomputes `duration` for the survivors. What looked like water-filling was
this single-pass floor applied uniformly: a table's final size is `max(floor(duration/weight),
default_records)`, and `default_records` is `floor(1008/bytes_per_record)` for a `DataInterval`
table but exactly `1` for a `DataEvent`/no-interval table (§10 top, step 2) — so in practice only a
`DataInterval` table's own floor is ever high enough to matter; a squeezed `DataEvent` table's
`default_records=1` floor is a no-op and it is free to shrink far below what
`floor(1008/bytes_per_record)` would give it (oracle-confirmed by a synthetic test: a `DataEvent`
table heavily disadvantaged by a competing `DataInterval` table settles at `171` records, nowhere
near `floor(1008/4)=252`). `StationStatus` (4 bytes/record) hitting its own floor of exactly
`floor(1008/4)=252` in `Preflight_tester.CR6`/`SnowVue_tester.CR6`/`TString_tester.CR6` — the
finding that originally read as "water-filling" — is this same single-pass floor, not evidence of
iteration; no case in the 8-program corpus or the synthetic grid below needed more than one pass to
reproduce exactly.

**The surviving tables' pool magnitude — resolved exactly (2026-09-26).** `pool = M − reserved`
(§10 top, step 3–4): `reserved` accumulates a per-table term (`_table_reserved_bytes`, decompiled
from `FUN_004eab30`'s own `local_18 +=` line, see §10 top) over *every* active auto table
regardless of type, starting from a fixed `4096`; `M` (`_MIXED_POOL`) is a single global constant,
`4,194,139`. Both were pinned down together by a 4-point synthetic oracle grid (one `DataInterval`
auto table, weight 1 s, 4 bytes/record, competing against one `DataEvent` auto table whose weight
and byte width were varied: `(weight=6000,bytes=4)`, `(3000,4)`, `(6000,8)`, `(12000,4)`, giving
`DataInterval`-table sizes `1,028,527`/`1,027,685`/`1,028,357`/`1,028,949` and `DataEvent`-table
sizes `171`/`342`/`171`/`85`) solved exactly (integer/`Fraction` arithmetic, not floating-point
curve-fitting) for the two unknowns, then independently re-derived from all 8 real `crbasic/`
corpus "mixed regime" programs (`StationStatus`/`Shunt_Data` vs. their competing event tables) —
**both derivations agree on `M=4,194,139` exactly**, and every one of the 16 sizes across all 8
corpus programs (both the `DataInterval` table and every `DataEvent` table, several sharing an
identical size per program as §10's original "all events collapse to one size" finding already
showed) now matches the real compiler byte-for-byte (`tools/tdftest.py --group repo`: 16/16, up
from 8/16). This supersedes the old `pool ≈ Pool1 · B_event / (B_event + 15·n_event)` curve fit
(removed — it was accurate to only ~0.1–3%, not exact, and its single free constant did not
generalise across byte widths the way the real per-table `_table_reserved_bytes` term does).

### Not investigated

`OpenInterval`'s effect on auto-sizing (its header fields were unaffected — §5 — but size wasn't
separately isolated with OpenInterval alone vs. Cardout), `WorstCase` trigger modifier (its own
sub-tables *do* set the table-struct flags bit `0x1000` in `FUN_004f1340`'s decompilation, i.e. the
same bit this session confirmed means "called" — suggesting `WorstCase` sub-tables are always
treated as called, but this was not oracle-tested), `SlowSequence` count's own effect on auto-
sizing beyond the scan-weighting already covered above, the `DataTableInfo`/`Public` contribution
broken out field-by-field, and the mixed-regime pool magnitude open question directly above. Given
as **best current formula with fit statistics** per the task brief, not a closed-form derivation.

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
| 55 | `DataTable(Test,1,100) Sample(1,X,FP2)`, no `CallTable Test` anywhere | `Table Test, is declared but never called.` warning; `size=1` (not `100`) |
| 56 | Same as `#55` but `DataTable(Test,1,-1) DataInterval(...) ...`, still no `CallTable` | `size=1` (not the auto-formula result); `interval=(0,0) into=(0,0)` too, despite the `DataInterval` |
| 57 | `#1`'s single auto table + a second, unrelated `DataTable(Fixed,1,1000)`/`2000` (fixed, called) alongside it | auto table's `size` unchanged at `2,059,767` (fixed tables don't shrink the pool) |
| 58 | `#1`'s single auto table + a bare-named `ConstTable(Extra,0)` / a `SlowSequence` block | auto table's `size` unchanged at `2,059,767` |
| 59 | `ConstTable(MyConsts,0) Const A=False Const B=True Const C=1 Const D=1.5 Const E=&B10 Const F=&H1F Const G=-5 Const H="hello"` | types LONG,LONG,LONG,IEEE4,LONG,LONG,LONG,ASCII(dim=80) |
| 60 | `Const H = "<121 chars>"` in a named `ConstTable` | `ConstTable field H too large to edit, max length is 80.` |
| 61 | `Const E=-5`, `Const F=-5.0`, `Const G=2+3`, `Const H=10/2`, `Const I=10/3` in a named `ConstTable` | types LONG, IEEE4, LONG, IEEE4, IEEE4 (`/` always IEEE4, even `10/2`) |
| 62 | `Public IsIridiumStarted As Boolean` + `ReadOnly IsIridiumStarted` (`SBDIridiumLib.crb`) | `Public` field has the `0x80` read-only flag |
| 63 | 2 and 7 user `DataTable`s (`Shunt_tester.CR6`, `Preflight_tester.CR6`): `DataTableInfo`'s `-l` listing | `DataRecordSize`/`DataFillDays` list as `[n,2]` (e.g. `(1,1) (1,2) (2,1) (2,2)`); `DataFilled` lists as a flat `(1,1)..(1,2n)` instead — TDF bytes for `DataFilled` are still `subdims=[n,2,32]`, only the `-l` rendering differs |
| 64 | `Average(2,FinApp_Neutrons(),IEEE4,...) FieldNames("Neutrons")`, `FinApp_Neutrons` a 2-element array (`SbdPayload_tester.CR6`) | 2 fields: `Neutrons` (renamed, `begin=1,dim=1`) + `FinApp_Neutrons_Avg` (default name, `begin=2,dim=1,subdims=[2]`) |
| 65 | Single no-interval auto table alone, `Sample(1,X,FP2)+Sample(1,Y,FP2)` (2 fields, 4B) vs. `Sample(1,X,IEEE4)` (1 field, 4B) | both `size=209,406` — total bytes/record is what matters, not field count |
| 66 | Single no-interval auto table alone, 3×`Sample(_,_,FP2)` (6B) | `size=190,368` (matches `PortSleep_Scans`'s `Long`+`FP2`, also 6B) |
| 67 | 1 `DataInterval(1,Sec)` auto table + `e` no-interval auto tables (`e`=1..5), all FP2/1s-equivalent | all `e` event tables get an identical size for a given `e` (`835244,659568,596616,564160,544320` records for `e`=1..5 respectively — this data is *not wrong*, but the "e→pool" lookup once fitted to it was: see §10 "Event/no-interval table sharing a program", §13) |
| 68 | `DataTable(Station,1,-1) DataInterval(0,300,Sec,10) Sample(...)` (4B) + `DataTable(Event0,Trig,-1) Sample(29,Arr(1),FP2)` (58B), both `CallTable`d inside one `Scan(5,Sec,0,0)` | `Station size=941 Event0 size=56472` — **byte-for-byte identical** to `FinApp_tester.CR6`'s real `StationStatus`/`FinApp_Data`, confirming the synthetic reduction is faithful |
| 69 | same as `#68` but `Scan(1,Sec,0,0)` instead of `Scan(5,Sec,0,0)` | `Station size=252 Event0 size=56513` — a *different* pair from `#68`, proving the event table's weight tracks its enclosing scan's interval, not a constant |
| 70 | `#69`'s Station+one-58B-event scenario, varying only the event table's own byte width (2..250B) at `Scan(1,Sec,...)`, `Station`(4B,300s) fixed | `Station` size is `348` (24B event, uncapped) down to a floor of exactly `252` for every event width from 50B up through 250B tried — `252 = floor((1024-16)/4)`, i.e. `Station`'s own single-table default; confirms the water-filling cap, not a continued decline |
| 71 | `#70`'s scenario at `bytes`=2,6,24 with `n` identical copies of that one event table, `n`=1..6 | `Station`/event sizes shrink smoothly with `n` (e.g. 24B: `Station`=1739,870,580,434,347,289 for `n`=1..6) and `D` inferred from `Station` and from the (uniform) event size agree closely at every `n` when all event tables share one byte width — the divergence in `#70`-style *mixed*-byte-width cases (Preflight/SnowVue/TString's real configurations) comes from differing byte widths among competitors, not table count itself |
| 72 | Reproducing `Preflight_tester.CR6`'s exact 6-event-table configuration (`24,196,16,12,6,58` B, all `Scan(5,Sec,...)`, `Station` 4B/300s) synthetically | `Station=252 events=10223` (all six identical) — **byte-for-byte identical** to the real program, confirming the synthetic model captures the "all events collapse to one size, `Station` capped at its own default" shape exactly |

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
10. **Resolved (2026-09-26, superseding the "fixed 1 s" reading and the "still open" pool-magnitude
    note both above)**: a `DataEvent`/no-interval auto table's weight is the interval of its
    nearest enclosing `Scan`/`SlowSequence` block, not a flat 1 s (`FinApp_tester.CR6`'s real
    `Scan(5,Sec,...)` proves the enclosing-scan reading — see §10 "Event/no-interval table sharing
    a program"). A `DataInterval` table whose naive share would undercut its own single-table
    default (`floor(1008/bytes)`) is pinned there via a plain per-table floor on one shared
    `duration` (**oracle-confirmed exactly**, `StationStatus`=252 in three real corpus programs) —
    a `DataEvent`/no-interval table's own default is `1`, not `floor(1008/bytes)`, so this floor is
    a no-op for it and it can shrink far below that value (oracle-confirmed by a synthetic A/B
    test). The pool magnitude itself is now an exact, decompiled formula (`_table_reserved_bytes`/
    `_MIXED_POOL=4,194,139`, §10 "Event/no-interval table sharing a program"), independently
    derived from a 4-point synthetic oracle grid and from all 8 real corpus "mixed regime"
    programs, agreeing to the byte in both derivations; `tools/tdftest.py --group repo` is 16/16 TDF
    byte-identical (up from 8/16, all 8 of the previous gaps were exactly this).
11. `SlowSequence` count's own effect on auto-sizing (only its Status-table field effect is
    checked — §4), `WorstCase` trigger modifier's effect on auto-sizing (decompiled: its sub-tables
    get the same "called" flag bit `0x1000` as an ordinary `CallTable`d table — §10 top — but this
    was not oracle-tested), `Alias` "swath" form (`Alias VariableName(1) = AliasName(5)` aliasing
    multiple elements on one line) — not tested.
12. UINT1, FP3, FP4 field type-code bytes in the TDF — not produced in any experiment
    (UINT1 requires Reps divisible by 2 and a UINT1-typed source, not attempted).
13. The exact struct-offset semantics of `FUN_004eab30`'s per-table rate/weight fields (this
    session identified offset `+0x1c` bit `0x1000` = "called" with certainty — corroborated by the
    literal `is declared but never called.` warning string next to the check — and the general
    two-phase probe/water-fill shape, but the call site's argument-to-parameter binding for
    `FUN_004f4400` could not be pinned down precisely enough to read off which struct offset holds
    which of {bytes_per_record, weight, buffer-bytes-so-far} with full confidence beyond what the
    black-box behaviour already independently confirms).

## 13. Corrections and additions from `src/opencrbasic/tables.py` (2026-09-25)

Found while implementing `tables.build_tables` against the `crbasic/` corpus (16 programs) and
cross-checked with `tools/tdftest.py`. `FP3`/`FP4` (open question 12, candidate) turned out not to
exist at all: `Sample(1,X,FP3)`/`FP4` fail with `Invalid symbol in expression: FP3.` — not CRBasic
keywords, so questions about their TDF encoding are moot.

- **A table never `CallTable`d gets `size=1`, overriding *any* declared `Size` — fixed or `-1`**
  (**oracle-confirmed**, new experiments `bool8_1`→n/a, `fixeduncalled`, `callparens`): a table
  with `Size=500` (fixed) and no `CallTable` anywhere in the compiled program (after `#If`
  resolution) compiles with the warning `Table Test, is declared but never called.` and TDF
  `size=1` — not `500`. This is a **precondition on every rule in §10**, not previously stated: the
  auto-size formulas only apply to *called* auto tables; an uncalled auto table is also `size=1`,
  not run through the pool formula at all. `CallTable(Test)` (parenthesised) counts as a call
  identically to `CallTable Test`. This resolves a real case in the corpus: `Main.CR6`'s
  `Shunt_Data` table is declared unconditionally (via `IncludeSection("Definitions", ...)`) but
  its only `CallTable Shunt_Data` is inside `#If ShuntSequenceEnabled Then` — false by default — so
  it compiles to `size=1` regardless of its `DataInterval(0,60,Sec,10)`.
- **Fixed-size `DataTable`s, named `ConstTable`s, and `SlowSequence` blocks do not reduce the
  auto-size pool** (**oracle-confirmed**, experiments `fixed1000`/`fixed2000` vs. baseline,
  `constbare`, `slowseq`): a single `Size=-1` `DataInterval`/1s/FP2 auto table gets the same
  `size=2,059,767` whether or not the program also has a `DataTable(Fixed,1,1000)` (or `2000`), a
  `ConstTable(Extra,0)`, or a `SlowSequence`/`NextScan` block. This narrows §10's "reserved(n)"
  term specifically to *auto* (`Size=-1`, called) table count — nothing else measured moves it.
- **`ConstTable` `Const` type inference** (§7 open question 6, **oracle-confirmed**, experiments
  `consttypes`/`negconst`): integer literals, `&B`/`&H` literals, `True`/`False`, and predefined
  symbols fold to LONG (type 6); a literal with a decimal point folds to IEEE4 (type 9). Folding
  preserves this through unary `+`/`-` and through `+`/`-`/`*` of two LONGs (`Const E = -5` → LONG;
  `Const G = 2+3` → LONG), but `/` is **always** IEEE4 even when the result is a whole number
  (`Const H = 10/2` → IEEE4, not LONG). A **string** `Const` (`Const H = "hello"`) *is* valid syntax
  (the doc's one failed attempt used the wrong form, `As String * n =`) and always produces an
  ASCII field of **fixed `dim=80`**, regardless of the literal's length — a longer literal doesn't
  grow it; one over 80 chars fails to compile with `ConstTable field H too large to edit, max
  length is 80.`. `\`/`MOD`/`AND`/`OR`/`XOR`/comparisons/`<<`/`>>` are assumed LONG (integer/
  boolean-shaped ops); `^` and anything else default to IEEE4 — **not independently tested**, our
  corpus's `ConstTable` bodies never use an expression more complex than a literal or a bare
  predefined-symbol reference.
- **`ReadOnly Var1, Var2, ...`** (CHM `readonly.htm`, **oracle-confirmed** via
  `SBD_tester.CR6`/`SBDIridiumLib.crb`'s `Public IsIridiumStarted As Boolean = False` /
  `ReadOnly IsIridiumStarted`): sets the `0x80` read-only flag on that Public variable's TDF
  field(s). Not previously covered by §8.
- **`DataTableInfo`'s `DataFilled` field: `-l` display quirk, not a subdims correction**
  (**oracle-confirmed** at `n`=1, 2, 7, via `Shunt_tester.CR6` and `Preflight_tester.CR6`): the
  **TDF bytes** genuinely encode `subdims=[n,2,32]` exactly as §6 already stated (confirmed by
  parsing the raw `-z` output, not just reading `-l`). But the `-l` **table list** renders this one
  field differently from every other rank-3 field: instead of the `[n,2]` Cartesian product
  `DataRecordSize`/`DataFillDays` get (`(1,1) (1,2) (2,1) (2,2)` for `n`=2), `DataFilled` shows a
  flat run `(1,1) (1,2) (1,3) (1,4)` — first index pinned at `1`, second running `1..2n`. Mechanism
  not understood (a legacy quirk in the exe's own lister, not decompiled); reproduced exactly by
  special-casing this one field's listing, not derived.
- **`FieldNames` with fewer names than `Reps`** (§9 extends `Reps > 1` case, **oracle-confirmed**
  via `SbdPayload_tester.CR6`: `Average(2,FinApp_Neutrons(),IEEE4,...) FieldNames("Neutrons")` where
  `FinApp_Neutrons` is declared with exactly 2 elements): renames only the first `len(names)`
  elements to individual scalar fields (`begin` advancing one at a time, `dim=1`, `subdims=[]`) and
  leaves the **remaining** `Reps - len(names)` elements as one field under the **original default
  name** (`FinApp_Neutrons_Avg` here), `begin` continuing on from the named ones, `dim=` the
  remainder count, and **`subdims` unchanged** (the source array's full declared shape, not
  collapsed even when the remainder's `dim` is 1 — consistent with §3's "Source is an element of a
  declared array" rule). The `-l` listing's index for that remainder field reflects its `begin`
  (`FinApp_Neutrons_Avg(2)`, not `(1)`) — see next point.
- **`-l` array-field index display uses `begin`, not just `subdims`** (extends §3/§8's alias-
  prefix/suffix description, **oracle-confirmed** by the `FieldNames`-remainder case above and
  re-derived from the already-documented alias prefix/suffix `begin` values): the displayed index
  tuples for a field are the `dim` row-major coordinates of `subdims` starting at the 1-based
  linear position `begin`, not always `1..dim` from the start. This was already implied by the
  alias-splitting §8 description (`Arr5(begin=4,dim=2,...)` must list as `Arr5(4)`, `Arr5(5)`, not
  `Arr5(1)`, `Arr5(2)`) but not stated as a general `-l` rendering rule until this implementation
  needed it for the `FieldNames`-remainder case.
- **Auto-size, mixed regime — fully resolved (2026-09-26, supersedes the "pool magnitude still
  open" note previously here)**: the corpus's real programs are almost all in the hardest sub-case
  — 1 `DataInterval` auto table (`StationStatus`/`Shunt_Data`, interval 60–300 s) plus 1–7
  no-interval/event auto tables. An earlier reading of this data (quoted below for the record)
  diagnosed a byte-width effect on the interval table's pool share that turned out to be a
  **confound with the event table's weight**, not a real byte-width effect — see §10 "Event/
  no-interval table sharing a program" for the corrected "weight = enclosing scan interval" rule.
  With that confound removed, the exact mechanism (single shared `duration`, a per-table
  `default_records` floor that is `floor(1008/bytes)` for a `DataInterval` table but `1` for a
  `DataEvent` table, and a fully decompiled `reserved`/`pool` formula with one fitted constant,
  `_MIXED_POOL=4,194,139`) reproduces all 8 real corpus "mixed regime" programs byte-for-byte —
  `tools/tdftest.py --group repo` is 16/16 TDF byte-identical, up from 8/16. Superseded the earlier
  `Pool1·B_event/(B_event+15·n_event)` curve fit (accurate to only ~0.1–3%, never exact) entirely;
  see §10 for the full derivation and both the synthetic and corpus evidence. Original (2026-09-24)
  note, kept for the record: "in every such program, all the event-style tables get the identical
  size, regardless of their own `bytes_per_record`... `StationStatus`'s own size, read back through
  `size × bytes × interval` as a single-table-equivalent pool, does not land on a value that's a
  clean function of the event count alone... (*wider* event table → *larger* interval-table pool)"
  — the "all events identical" observation stands (it follows structurally from every event table
  in one program sharing the same enclosing-scan weight), but the pool-vs-byte-width reading was
  the scan-interval confound above, not a real relationship to chase further.
- **`Const X As <Type> = value` in a named `ConstTable`** (**oracle-confirmed**,
  `mqttpublishconsttable_1.CR6`: `Const myDouble As Double = 987.654321`): an explicit type
  overrides the literal-based inference above -- `Double` gives IEEE8 (type 18), not the IEEE4 a
  bare `987.654321` literal would otherwise infer. `tables.py` checks `ConstDecl.type` first,
  falling back to literal inference only when it is absent.
- **`DataInterval(TIntoInt, 0, Units, Lapses)` — an explicit `Interval` of `0`** (new, **oracle-
  observed but not reconciled**, `modemhangupendmodemhangup_1.CR6`: `DataInterval(0,0,Sec,10)`
  inside a program with `Scan(60,Sec,0,0)`): the table's TDF `interval` comes out as `(60,0)`, not
  `(0,0)` — i.e. an explicit `0` interval falls back to the program's own `Scan` interval, not to
  the "no `DataInterval`" `(0,0)` rule §5 already documents for when the statement is absent
  entirely. **Not implemented**: `tables.py` still emits `(0,0)` for this case, since resolving
  "the" Scan interval requires knowing which `Scan`/`SubScan` block encloses the table's
  `CallTable` (ambiguous with multiple `Scan`s, e.g. a main scan plus `SlowSequence`s) — out of
  this implementation's current scope (`_build_data_table` has no access to the enclosing `Scan`).
  Not observed in the `crbasic/` corpus (every real `DataInterval` there gives a nonzero
  `Interval`).
- **Single no-`DataInterval`/`DataEvent` auto table, alone in the program (§10's "Tables without
  `DataInterval`" regime): exact sizes now known for 4 byte-widths, but no single `overhead`
  reconciles them** (**oracle-confirmed**, experiments `event_2fp2`/`event_1ieee4`/`event_3fp2` in
  addition to the doc's existing `#5`/`#49`/`#54`): `bytes_per_record → size` is exactly
  `{2: 232674, 4: 209406, 6: 190368, 8: 174504}` (confirmed the total-*bytes* is what matters, not
  field count: a 2-field `Sample`+`Sample` FP2 table and a 1-field `IEEE4` table, both 4 B/record,
  give the identical `209,406`). An exhaustive search over integer `overhead` in `0..40` against
  `records = floor(Pool2/(bytes+overhead))` finds **no** `Pool2` consistent with all four points
  simultaneously (the doc's `overhead=16` fit was only ever checked pairwise) — `tables.py` uses
  this table as an exact lookup for these four widths and the `overhead=16`/`Pool2=4,188,120`
  formula only as a fallback for an unseen width.

## Storage limit (observed 2026-10-03, not implemented)

CR300 `Unable to allocate enough data table space to manage flash write cycle limits.` /
`Not enough storage area for DataTables.` (oracle, `cr300comp.exe`; reproducer
`tests/corpus/fuzz/cr300/false_accept/80cdb831bd9a.CR6`). Not modelled because the rule below is
incomplete and a partial model would false-reject.

- One called table with fixed `N` records and no `DataInterval` fails when
  `N * (16 + payload) > 10,444,800`; payload FP2 2, IEEE4/Long 4, `String*n` `(n div 4 + 1) * 4`.
  Boundaries: one FP2 field 580266/580267, two FP2 fields 522240/522241, `String*4` 435200/435201.
  Independent of scan interval (100 mSec to 1 Hr).
- Not monotonic: one FP2 field fails from 580267 to 119304187 and passes again from 119304188
  (`N * 18` within ~8300 bytes of 2^31); `-999999999999` alone passes.
- Shared budget with an unmeasured per-table overhead (~12-24 KB): two FP2 tables of 290133
  records fail; a fixed table plus a `-1` table also failed.
- A table with `DataInterval` has a much larger budget (5,059,161 records passed); a table never
  called is not checked; `Maximum` with time stamps failed at 580266 (record size has terms not
  listed above).
- CR350 thresholds are roughly five times larger (not pinned). CR6/CR1000X report `Out of
  memory.` for large tables instead (separate, unimplemented family).
