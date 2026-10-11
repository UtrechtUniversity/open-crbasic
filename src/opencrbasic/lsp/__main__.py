"""`python -m opencrbasic.lsp`: stdio language server. Imports stay inside the guard so spawned
analysis workers (which re-import the main module) do not load pygls."""

if __name__ == "__main__":
    from .server import main

    main()
