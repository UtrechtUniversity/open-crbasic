# Legal and provenance notes

**Human decision (2026-11-01): open source, published.** opencrbasic is MIT licensed and published
to PyPI; the VS Code extension and the agent skills are published from their own repositories. This
reverses the decision of 2026-10-11 ("internal use only, never published or redistributed"), which
had allowed Campbell's CRBasic help text into sources, tests and fixtures. When the project moved
to its own repository on 2026-11-01 the following were therefore kept out of this repository and
out of its git history:

- `CR6Comp.exe` and the other Campbell compilers (`compiler/` is git-ignored; see the README).
- The extracted CRBasic help (`extracted_chm/`, git-ignored; `helpDirectory` points at a local copy).
- The 507 CRBasic help example programs (`tests/corpus/chm/`, regenerated locally by
  `tools/extract_chm_examples.py`) and the GEUS station programs (`examples/`, git-ignored).
- Fuzz reproducers seeded from those programs (3,117 of 3,338) and fixture cases whose program text
  is mostly a help example or GEUS program (about 1,200 of 60,000 cases in 30 files). Tests that need
  them skip when the local copies are absent.

Kept, as interoperability facts: the instruction, function and symbol tables read from the exe
(`src/opencrbasic/data/`) and the compiler's short diagnostic strings, which existing tooling parses.
Short quotations of single help lines in comments and docs remain where they document a behaviour.

The notes below predate this decision and list what the maintainers should still confirm.

This project aims at interoperability with Campbell Scientific CR6 dataloggers. The maintainers
should confirm the points below; this is not legal advice.

- **No Campbell code is included.** The implementation is written from behaviour notes in `docs/`,
  the public CRBasic help, and black-box comparison with `CR6Comp.exe`. Decompiled code is used only
  to understand behaviour and is never copied into `src/`. The decompile dump and the exe itself are
  not part of the project.
- **Extracted data.** `src/opencrbasic/data/{instructions,functions,symbols}.json` hold names,
  parameter-type codes, and numeric identifiers read from the exe by `tools/extract_tables.py`.
  These are functional facts required for compatibility, but they are derived from the binary; an
  alternative is to ship only the extractor and have users run it against their own copy of the
  exe (free with Campbell's Device Configuration Utility / CRBasic Editor).
- **Message texts.** Diagnostics reproduce the exe's short message strings so existing tooling can
  parse them. Consider whether to keep them verbatim.
- **Licences to check.** The CRBasic Editor / LoggerNet licence terms regarding reverse engineering,
  and the applicable interoperability exceptions (e.g. EU Directive 2009/24/EC Art. 6, US DMCA
  §1201(f)). `extracted_chm/` is Campbell's documentation and must not be redistributed with the
  project.
