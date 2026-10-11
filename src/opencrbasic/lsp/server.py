"""pygls adapter: lifecycle, document sync, configuration, diagnostics and language features.

No language rules: everything about CRBasic comes from `opencrbasic.analysis` through `Session`
and from the core feature modules through `features`. Capabilities advertised: full text document
sync (open/close/change/save), published diagnostics, completion (trigger `.`), hover, signature
help (triggers `(` `,`), document symbols, folding ranges, document links, definition,
references, document highlights, full semantic tokens, prepareRename/rename, document and range
formatting, quick-fix code actions, parameter-name inlay hints and the commands
`opencrbasic.showCompilerOutput` and `opencrbasic.showTableFields`. Each has a stdio transcript test.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

EXPECTED_API_VERSION = 1       # the `analysis.API_VERSION` this server was written for
SECTION = "opencrbasic"
WATCH_ID = "opencrbasic-watch"
DELETE_PATTERN = "**/*"         # VS Code reports a deleted folder only as its own URI
CONFIG_PULL_TIMEOUT = 10.0
COMMAND_COMPILER_OUTPUT = "opencrbasic.showCompilerOutput"
COMMAND_TABLE_FIELDS = "opencrbasic.showTableFields"


def _git_revision(src_dir: Path) -> str | None:
    """HEAD of the checkout `src_dir` (the imported package directory) belongs to, or None when
    `src_dir` is not that checkout's `src/opencrbasic` or `opencrbasic/src/opencrbasic` (a wheel
    installed inside some other repository)."""
    try:
        out = subprocess.run(["git", "-C", str(src_dir), "rev-parse", "--show-toplevel",
                              "--short", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = out.stdout.split()
    if out.returncode != 0 or len(lines) != 2:
        return None
    top, rev = Path(lines[0]), lines[1]
    try:
        here = src_dir.resolve()
        ours = any((top / sub).resolve() == here
                   for sub in ("src/opencrbasic", "opencrbasic/src/opencrbasic"))
    except OSError:
        return None
    return rev if ours else None


def startup_line() -> str:
    import opencrbasic
    from .. import analysis

    where = Path(opencrbasic.__file__).resolve()
    rev = _git_revision(where.parent)
    return (f"opencrbasic-lsp: opencrbasic={where} api_version={analysis.API_VERSION} "
            f"git={rev or 'unknown'}")


def check_api_version() -> str | None:
    """None when the facade matches; else the refusal message."""
    from .. import analysis

    if analysis.API_VERSION != EXPECTED_API_VERSION:
        return (f"opencrbasic-lsp: refusing to start: opencrbasic.analysis.API_VERSION is "
                f"{analysis.API_VERSION}, this server was written for {EXPECTED_API_VERSION}")
    return None


def build_server():
    from lsprotocol import types
    from pygls.exceptions import JsonRpcException
    from pygls.lsp.server import LanguageServer
    from pygls.uris import to_fs_path

    from .. import __version__
    from .. import commands, formatting
    from ..sources import DiskSource, OverlaySource
    from . import features
    from .config import Config, parse_config, watch_pattern
    from .session import CHANGED, CREATED, DELETED, Diag, Session

    server = LanguageServer(SECTION, __version__,
                            text_document_sync_kind=types.TextDocumentSyncKind.Full)
    severities = {"error": types.DiagnosticSeverity.Error,
                  "warning": types.DiagnosticSeverity.Warning,
                  "information": types.DiagnosticSeverity.Information}

    def publish(uri: str, version: int | None, diags: list[Diag]) -> None:
        server.text_document_publish_diagnostics(types.PublishDiagnosticsParams(
            uri=uri, version=version,
            diagnostics=[types.Diagnostic(
                range=types.Range(types.Position(d.line, 0), types.Position(d.end_line, d.end)),
                message=d.message, severity=severities[d.severity], source=d.source)
                for d in diags]))

    session = Session(publish)
    state: dict[str, Any] = {"init_options": None}
    background: set[asyncio.Task] = set()

    def send_message(message: str) -> None:
        if not state.get("shutdown"):
            server.window_log_message(types.LogMessageParams(
                type=types.MessageType.Warning, message=f"opencrbasic-lsp: {message}"))

    session.on_message = send_message

    def fs_path(uri: str) -> Path:
        fs = to_fs_path(uri)
        if fs is None:                      # not a `file:` URI (was a TypeError from Path(None))
            raise ValueError(f"not a file URI: {uri!r}")
        return Path(fs).absolute()

    def folders_of(params: types.InitializeParams) -> list[Path]:
        if params.workspace_folders:
            return [fs_path(f.uri) for f in params.workspace_folders]
        if params.root_uri:
            return [fs_path(params.root_uri)]
        if params.root_path:
            return [Path(params.root_path).absolute()]
        return []

    def supports_configuration() -> bool:
        workspace = server.client_capabilities.workspace
        return bool(workspace and workspace.configuration)

    async def pull_settings() -> Any:
        try:
            result = await asyncio.wait_for(server.workspace_configuration_async(
                types.ConfigurationParams(items=[types.ConfigurationItem(section=SECTION)])),
                CONFIG_PULL_TIMEOUT)
            return result[0] if result else None
        except Exception as exc:
            session.log(f"workspace/configuration failed: {type(exc).__name__}: {exc}")
            return None

    async def reload(settings: Any = None, pull: bool = False, first: bool = False) -> None:
        base: Config = Config() if first else session.config
        if pull and supports_configuration():
            settings = await pull_settings()
        if first or settings is not None:
            # Every reload rebuilds from the defaults, so a key removed from the settings returns
            # to its default; no settings at all (a failed pull) keeps the current configuration.
            cfg, warnings = parse_config(state["init_options"], Config())
            cfg, more = parse_config(settings, cfg)
        else:
            cfg, warnings, more = base, [], []
        for warning in warnings + more:
            session.log(warning)
        before = (base.inlay_parameter_names, base.help_directory)
        await session.configure(cfg, state.get("folders") if first else None)
        if not first and before != (cfg.inlay_parameter_names, cfg.help_directory):
            refresh_inlay_hints()

    @server.feature(types.INITIALIZE)
    def on_initialize(params: types.InitializeParams) -> None:
        state["init_options"] = params.initialization_options
        session.position_encoding = position_encoding()
        state["folders"] = folders_of(params)

    @server.feature(types.INITIALIZED)
    async def on_initialized(params: types.InitializedParams) -> None:
        # Also an Info log message: clients show stderr at error level, which would mark a good start as an error.
        server.window_log_message(types.LogMessageParams(type=types.MessageType.Info,
                                                         message=startup_line()))
        await reload(pull=True, first=True)
        task = asyncio.get_running_loop().create_task(register_watchers())
        background.add(task)
        task.add_done_callback(background.discard)

    async def register_watchers() -> None:
        """Ask the client to report changes to closed program and library files (dynamic
        registration, only when the client supports it). A failure is logged, never fatal: the
        server still handles open documents and `didChangeConfiguration`."""
        workspace = server.client_capabilities.workspace
        support = workspace.did_change_watched_files if workspace else None
        if not (support and support.dynamic_registration):
            session.log("client does not support watched-file registration; changes to closed "
                        "files are noticed when a document is saved, opened or closed")
            return
        try:
            await asyncio.wait_for(server.client_register_capability_async(
                types.RegistrationParams(registrations=[types.Registration(
                    id=WATCH_ID, method=types.WORKSPACE_DID_CHANGE_WATCHED_FILES,
                    register_options=types.DidChangeWatchedFilesRegistrationOptions(
                        watchers=[types.FileSystemWatcher(glob_pattern=watch_pattern()),
                                  types.FileSystemWatcher(glob_pattern=DELETE_PATTERN,
                                                          kind=types.WatchKind.Delete)]))])),
                CONFIG_PULL_TIMEOUT)
        except Exception as exc:
            session.log(f"watched-file registration failed: {type(exc).__name__}: {exc}")

    kinds = {types.FileChangeType.Created: CREATED, types.FileChangeType.Changed: CHANGED,
             types.FileChangeType.Deleted: DELETED}

    @server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
    async def on_watched_files(params: types.DidChangeWatchedFilesParams) -> None:
        await session.files_changed([(fs_path(e.uri), kinds[e.type]) for e in params.changes
                                     if e.type in kinds])

    @server.feature(types.WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS)
    async def on_folders(params: types.DidChangeWorkspaceFoldersParams) -> None:
        await session.set_folders([fs_path(f.uri) for f in params.event.added or []],
                                  [fs_path(f.uri) for f in params.event.removed or []])

    @server.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)
    async def on_config(params: types.DidChangeConfigurationParams) -> None:
        settings = params.settings
        if isinstance(settings, dict) and isinstance(settings.get(SECTION), dict):
            settings = settings[SECTION]
        await reload(settings, pull=True)

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def on_open(params: types.DidOpenTextDocumentParams) -> None:
        doc = params.text_document
        session.did_open(fs_path(doc.uri), doc.uri, doc.version, doc.text)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def on_change(params: types.DidChangeTextDocumentParams) -> None:
        changes = params.content_changes
        if not changes or hasattr(changes[-1], "range"):
            session.log("ignoring a non-full document change (full sync is advertised)")
            return
        session.did_change(fs_path(params.text_document.uri), params.text_document.version,
                           changes[-1].text)

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def on_save(params: types.DidSaveTextDocumentParams) -> None:
        session.did_save(fs_path(params.text_document.uri))

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    def on_close(params: types.DidCloseTextDocumentParams) -> None:
        session.did_close(fs_path(params.text_document.uri))

    @server.feature(types.SHUTDOWN)
    def on_shutdown(*_: Any) -> None:
        state["shutdown"] = True
        session.close()

    # ---- language features -------------------------------------------------------------

    def position_encoding() -> str:
        value = getattr(server.workspace, "position_encoding", None)
        return str(getattr(value, "value", value) or "utf-16")

    async def make_ctx() -> features.Ctx:
        await session.configured()
        return features.Ctx(session.config.logger, position_encoding(), session.config.encoding,
                            session.blobs(), {p: d.uri for p, d in session.docs.items()},
                            session.help_provider())

    async def prepared(uri: str, wait: bool):
        """(path, Analysed or None, AnalysisResult or None) for a request about `uri`."""
        await session.configured()
        path = fs_path(uri)
        ana = await session.analysed_for(path, wait)
        return path, ana, await session.load(ana)

    def guarded(name: str, empty: Any):
        """A request never fails: an internal error is logged and answered with `empty`."""
        def wrap(handler):
            async def run(params):
                try:
                    return await handler(params)
                except (asyncio.CancelledError, JsonRpcException):
                    raise
                except Exception:
                    session.log(f"{name} failed:\n{traceback.format_exc()}")
                    return empty() if callable(empty) else empty
            run.__name__ = handler.__name__
            return run
        return wrap

    @server.feature(types.TEXT_DOCUMENT_COMPLETION,
                    types.CompletionOptions(trigger_characters=["."]))
    @guarded("completion", lambda: types.CompletionList(False, []))
    async def on_completion(params: types.CompletionParams):
        path, ana, result = await prepared(params.text_document.uri, wait=False)
        return await asyncio.to_thread(features.completion, await make_ctx(), ana, result, path,
                                       params.position)

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    @guarded("hover", None)
    async def on_hover(params: types.HoverParams):
        path, ana, result = await prepared(params.text_document.uri, wait=False)
        return await asyncio.to_thread(features.hover, await make_ctx(), ana, result, path,
                                       params.position)

    @server.feature(types.TEXT_DOCUMENT_SIGNATURE_HELP,
                    types.SignatureHelpOptions(trigger_characters=["(", ","]))
    @guarded("signatureHelp", None)
    async def on_signature_help(params: types.SignatureHelpParams):
        return await asyncio.to_thread(features.signature_help, await make_ctx(),
                                       fs_path(params.text_document.uri), params.position)

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
    @guarded("documentSymbol", list)
    async def on_document_symbol(params: types.DocumentSymbolParams):
        path, ana, result = await prepared(params.text_document.uri, wait=True)
        return await asyncio.to_thread(features.document_symbols, await make_ctx(), ana, result, path)

    @server.feature(types.TEXT_DOCUMENT_FOLDING_RANGE)
    @guarded("foldingRange", list)
    async def on_folding_range(params: types.FoldingRangeParams):
        return await asyncio.to_thread(features.folding_ranges, await make_ctx(),
                                       fs_path(params.text_document.uri))

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_LINK)
    @guarded("documentLink", list)
    async def on_document_link(params: types.DocumentLinkParams):
        path, ana, result = await prepared(params.text_document.uri, wait=True)
        return await asyncio.to_thread(features.document_links, await make_ctx(), ana, result, path)

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    @guarded("definition", list)
    async def on_definition(params: types.DefinitionParams):
        path, ana, result = await prepared(params.text_document.uri, wait=True)
        return await asyncio.to_thread(features.definition, await make_ctx(), ana, result, path,
                                       params.position)

    @server.feature(types.TEXT_DOCUMENT_REFERENCES)
    @guarded("references", list)
    async def on_references(params: types.ReferenceParams):
        """The request file's program (as for every read-only feature, possibly stale) plus every
        other entry with a current program that reads the request file or a declaration's file
        (LSP-R4a); entries without a current program are skipped."""
        path, ana, result = await prepared(params.text_document.uri, wait=True)
        if result is None:
            return []
        ctx = await make_ctx()
        home = next((p for p, e in session.entries.items() if e.analysed is ana), Path())
        analyses: dict[int, tuple] = {id(ana): (home, ana, result)}

        async def add(entries) -> list:
            for e in entries:
                if e.analysed is not None and id(e.analysed) not in analyses:
                    loaded = await session.load(e.analysed)
                    if loaded is not None:
                        analyses[id(e.analysed)] = (e.path, e.analysed, loaded)
            return [(a, r) for _, a, r in sorted(analyses.values(), key=lambda t: str(t[0]))]

        pairs = await add(session.current_readers(path))
        files = await asyncio.to_thread(features.declaration_files, ctx, pairs, path,
                                        params.position)
        for f in sorted(files):
            pairs = await add(session.current_readers(f))
        return await asyncio.to_thread(features.references_across, ctx, pairs, path,
                                       params.position, params.context.include_declaration)

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)
    @guarded("documentHighlight", list)
    async def on_document_highlight(params: types.DocumentHighlightParams):
        path, ana, result = await prepared(params.text_document.uri, wait=True)
        return await asyncio.to_thread(features.document_highlights, await make_ctx(), ana, result,
                                       path, params.position)

    @server.feature(types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL, features.LEGEND)
    @guarded("semanticTokens", lambda: types.SemanticTokens(data=[]))
    async def on_semantic_tokens(params: types.SemanticTokensParams):
        path, ana, result = await prepared(params.text_document.uri, wait=True)
        return await asyncio.to_thread(features.semantic_tokens, await make_ctx(), ana, result, path)

    # ---- rename, formatting, quick fixes (LSP-07-wire) ---------------------------------

    class Refused(JsonRpcException):
        """A refusal of an edit request: an LSP error carrying the reason (`data.reason`)."""
        CODE = int(types.LSPErrorCodes.RequestFailed)

    class QuietRefusals(logging.Filter):
        """pygls logs every exception a handler raises with its traceback; a refusal is an
        expected answer, not a server error."""

        def filter(self, record: logging.LogRecord) -> bool:
            return not (record.exc_info and isinstance(record.exc_info[1], Refused))

    logging.getLogger("pygls.protocol.json_rpc").addFilter(QuietRefusals())
    report_default = server.report_server_error

    def report_server_error(error: Exception, source: Any) -> None:
        if not isinstance(error, Refused):
            report_default(error, source)

    server.report_server_error = report_server_error  # type: ignore[method-assign]

    def refuse(what: str, reason: str, message: str) -> Refused:
        return Refused(f"{what}: {message}", data={"reason": reason})

    def context_now() -> features.Ctx:
        """The request context from the documents as they are now (no await in between)."""
        return features.Ctx(session.config.logger, position_encoding(), session.config.encoding,
                            session.blobs(), {p: d.uri for p, d in session.docs.items()},
                            session.help_provider())

    def locked(entries, function, *args):
        """Runs in a thread: `function` with the core-call guard of every entry's program held
        (always taken in entry-path order)."""
        with contextlib.ExitStack() as stack:
            for entry in entries:
                stack.enter_context(entry.analysed.guard)
            return function(*args)

    async def current_entries(path: Path, what: str):
        """(entries reading `path`, their `Analysed`) once none is pending; a refusal when no
        entry reads the file or one of them has no program that matches the open documents."""
        await session.configured()
        entries = await session.entries_reading(path)
        if not entries:
            raise refuse(what, "no_entry", "no configured entry program includes this file")
        for entry in entries:
            if not session.current(entry):
                raise refuse(what, "pending", f"the analysis of {entry.path.name} is not current "
                             "(pending, failed or timed out); try again when it has finished")
        return entries

    async def prepare_or_rename(uri: str, position, new_name: str | None, what: str):
        """Starts with the entries reading the request file; while the declaration or an edit is
        in a file some entry outside the set reads, adds the entries reading it and recomputes,
        so every entry the rename changes is checked (LSP-R3a)."""
        path = fs_path(uri)
        files = {path}
        while True:
            entries = {}
            for f in sorted(files):
                for e in await current_entries(f, what):
                    entries[e.path] = e
            entries = [entries[k] for k in sorted(entries)]
            found, hashes, versions, touched = await rename_with(entries, path, position, new_name,
                                                                 what)
            if touched <= files:
                break
            files |= touched
        for p, digest in hashes.items():        # an open document must still be what was renamed
            doc = session.docs.get(p)
            if doc is not None and (doc.version != versions.get(p)
                                    or hashlib.sha256(doc.data).hexdigest() != digest):
                raise refuse(what, "stale", f"{p.name} changed while the rename was computed")
        return found

    async def rename_with(entries, path: Path, position, new_name: str | None, what: str):
        """One core call over `entries`: (result, hashes, document versions, touched files)."""
        unknown = session.unknown_entries()
        if unknown:
            raise refuse(what, "unanalysed", f"the analysis of {unknown[0].path.name} failed or "
                         "has not finished, so it is unknown whether it depends on this file")
        results = [await session.load(e.analysed) for e in entries]
        if any(r is None for r in results):
            raise refuse(what, "no_program", "a parsed program is not available")
        # no await from the currency check to the snapshot: the edit fits these documents
        if not all(session.current(e) for e in entries):
            raise refuse(what, "pending", "a document changed while the request was prepared")
        ctx = context_now()
        versions = {p: d.version for p, d in session.docs.items()}
        source = OverlaySource(DiskSource(), session.blobs())      # buffers as the analyses read them
        if new_name is None:
            found, refusal, touched = await asyncio.to_thread(
                locked, entries, features.prepare_rename, ctx, results, path, position, source)
            hashes = {}
        else:
            found, refusal, hashes = await asyncio.to_thread(
                locked, entries, features.rename_edit, ctx, results, path, position, new_name,
                versions, source)
            touched = set(hashes)
        if refusal is not None:
            raise refuse(what, refusal.code, refusal.message)
        return found, hashes, versions, {Path(p) for p in touched}

    @server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
    @guarded("prepareRename", None)
    async def on_prepare_rename(params: types.PrepareRenameParams):
        return await prepare_or_rename(params.text_document.uri, params.position, None,
                                       "Cannot rename")

    @server.feature(types.TEXT_DOCUMENT_RENAME, types.RenameOptions(prepare_provider=True))
    @guarded("rename", None)
    async def on_rename(params: types.RenameParams):
        return await prepare_or_rename(params.text_document.uri, params.position,
                                       params.new_name, "Cannot rename")

    def show_info(message: str) -> None:
        server.window_show_message(types.ShowMessageParams(
            type=types.MessageType.Info, message=f"opencrbasic: {message}"))

    async def format_document(params, where):
        path = fs_path(params.text_document.uri)
        try:
            entries = await current_entries(path, "Cannot format")
            for entry in entries:
                why = formatting.format_refusal(await session.load(entry.analysed))
                if why:
                    raise refuse("Cannot format", "errors", f"{entry.path.name}: {why}")
        except Refused as exc:
            show_info(exc.message)
            return []
        if not all(session.current(e) for e in entries):
            show_info("Cannot format: a document changed while the request was prepared")
            return []
        ctx = context_now()
        return await asyncio.to_thread(features.formatting_edits, ctx, path, params.options, where)

    @server.feature(types.TEXT_DOCUMENT_FORMATTING)
    @guarded("formatting", list)
    async def on_formatting(params: types.DocumentFormattingParams):
        return await format_document(params, None)

    @server.feature(types.TEXT_DOCUMENT_RANGE_FORMATTING)
    @guarded("rangeFormatting", list)
    async def on_range_formatting(params: types.DocumentRangeFormattingParams):
        return await format_document(params, params.range)

    @server.feature(types.TEXT_DOCUMENT_CODE_ACTION,
                    types.CodeActionOptions(code_action_kinds=[types.CodeActionKind.QuickFix]))
    @guarded("codeAction", list)
    async def on_code_action(params: types.CodeActionParams):
        only = params.context.only
        if only and types.CodeActionKind.QuickFix not in only:
            return []
        await session.configured()
        path = fs_path(params.text_document.uri)
        entries = session.fresh_contributors(path)
        sources = []
        for entry in entries:
            result = await session.load(entry.analysed)
            if result is not None:
                sources.append((entry.path.name, result))
        if not sources or [id(e) for e in session.fresh_contributors(path)] != [id(e) for e in entries]:
            return []
        doc = session.docs.get(path)
        ctx = context_now()
        return await asyncio.to_thread(
            locked, entries, features.code_actions, ctx, sources, path,
            list(params.context.diagnostics), doc.version if doc else None, session.display)

    def refresh(supported, send) -> None:
        if supported and not state.get("shutdown"):
            pending = send(None)
            pending.add_done_callback(lambda f: f.cancelled() or f.exception())   # answer unused

    def refresh_semantic_tokens() -> None:
        """New programs change the tokens of unchanged documents: ask the client to re-request."""
        workspace = server.client_capabilities.workspace
        support = workspace.semantic_tokens if workspace else None
        refresh(support and support.refresh_support, server.workspace_semantic_tokens_refresh)

    def refresh_inlay_hints() -> None:
        workspace = server.client_capabilities.workspace
        support = workspace.inlay_hint if workspace else None
        refresh(support and support.refresh_support, server.workspace_inlay_hint_refresh)

    def on_new_program() -> None:
        refresh_semantic_tokens()
        refresh_inlay_hints()

    session.on_analysed = on_new_program

    # ---- inlay hints and commands (LSP-09c) ----------------------------------------------

    @server.feature(types.TEXT_DOCUMENT_INLAY_HINT)
    @guarded("inlayHint", list)
    async def on_inlay_hint(params: types.InlayHintParams):
        await session.configured()
        if not session.config.inlay_parameter_names:
            return []
        path, ana, result = await prepared(params.text_document.uri, wait=True)
        if result is None or not session.fresh(path, ana):    # a stale program has stale lines
            return []
        ctx = await make_ctx()
        first, last = ctx.decoded(path, params.range.start), ctx.decoded(path, params.range.end)
        if first is None or last is None:
            return []
        end = last[0] - 1 if params.range.end.character == 0 and last[0] > first[0] else last[0]
        return await asyncio.to_thread(features.inlay_hints, ctx, ana, result, path, first[0], end)

    async def command_entry(args: tuple, what: str):
        """(document path, entry) for a command argument: the document URI. A `.crb` takes the
        first sorted entry that reads it, as the other features do."""
        if len(args) != 1 or not isinstance(args[0], str):
            raise refuse(what, "bad_arguments", "expected one argument: the document URI")
        try:
            path = fs_path(args[0])
        except ValueError as exc:
            raise refuse(what, "bad_arguments", str(exc))
        await session.configured()
        reading = await session.entries_reading(path)
        if path in session.entries:
            return path, session.entries[path]
        if reading:
            return path, reading[0]
        raise refuse(what, "no_entry", "no configured entry program includes this file")

    @server.command(COMMAND_COMPILER_OUTPUT)
    async def on_compiler_output(*args):
        _, entry = await command_entry(args, "Cannot show compiler output")
        out = await asyncio.to_thread(commands.compiler_output, entry.path,
                                      session.config.logger, session.blobs())
        return session.display(out.text)

    @server.command(COMMAND_TABLE_FIELDS)
    async def on_table_fields(*args):
        what = "Cannot show table fields"
        _, entry = await command_entry(args, what)
        if not session.current(entry):
            raise refuse(what, "pending", f"the analysis of {entry.path.name} is not current "
                         "(pending, failed or timed out); try again when it has finished")
        result = await session.load(entry.analysed)
        if result is None:
            raise refuse(what, "no_program", "a parsed program is not available")
        tables = await asyncio.to_thread(locked, [entry], commands.table_fields, result)
        return session.display(commands.render_table_fields(tables))


    server.session = session  # type: ignore[attr-defined]
    server.state = state  # type: ignore[attr-defined]
    return server


def main() -> None:
    print(startup_line(), file=sys.stderr, flush=True)
    refusal = check_api_version()
    if refusal:
        print(refusal, file=sys.stderr, flush=True)
        sys.exit(2)
    server = build_server()
    try:
        server.start_io()
    finally:
        server.session.close()  # type: ignore[attr-defined]
    if not server.state.get("shutdown"):  # type: ignore[attr-defined]
        sys.exit(1)             # LSP: `exit` without a prior `shutdown`
