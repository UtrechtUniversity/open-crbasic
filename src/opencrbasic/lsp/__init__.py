"""Language server for CRBasic (LSP-01b). Protocol adapter only: no language rules live here.

This package's `__init__` must stay free of third-party imports: analysis worker processes import
`opencrbasic.lsp.worker` and must not load pygls. See `docs/lsp-api.md` "Language server".
"""
