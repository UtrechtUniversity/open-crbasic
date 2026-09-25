# Legal and provenance notes

This project aims at interoperability with Campbell Scientific CR6 dataloggers. Before publishing
it, the maintainers should confirm the points below; this is not legal advice.

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
