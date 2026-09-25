# opencrbasic

An open-source, native re-implementation of Campbell Scientific's CR6 CRBasic pre-compiler
(`CR6Comp.exe`), for Linux, macOS and Windows without Wine. Work in progress; see `PROGRESS.md`
for what works today and `PLAN.md` for the design.

```bash
cd opencrbasic
python3 -m opencrbasic.cli Program.CR6      # (once implemented) CR6Comp-compatible output
```

Documentation of the original compiler's behaviour is in `docs/`. Provenance and licensing
considerations: `docs/legal.md`.
