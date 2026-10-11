"""pygls adapter: lifecycle, document sync, configuration and diagnostics publishing.

No language rules: everything about CRBasic comes from `opencrbasic.analysis` through `Session`.
Capabilities advertised: full text document sync (open/close/change/save) and published
diagnostics, nothing else.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

EXPECTED_API_VERSION = 1       # the `analysis.API_VERSION` this server was written for
SECTION = "opencrbasic"
CONFIG_PULL_TIMEOUT = 10.0


def _git_revision(src_dir: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(src_dir), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    rev = out.stdout.strip()
    return rev if out.returncode == 0 and rev else None


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
    from pygls.lsp.server import LanguageServer
    from pygls.uris import to_fs_path

    from .. import __version__
    from .config import Config, parse_config
    from .session import Diag, Session

    server = LanguageServer(SECTION, __version__,
                            text_document_sync_kind=types.TextDocumentSyncKind.Full)
    severities = {"error": types.DiagnosticSeverity.Error,
                  "warning": types.DiagnosticSeverity.Warning,
                  "information": types.DiagnosticSeverity.Information}

    def publish(uri: str, version: int | None, diags: list[Diag]) -> None:
        server.text_document_publish_diagnostics(types.PublishDiagnosticsParams(
            uri=uri, version=version,
            diagnostics=[types.Diagnostic(
                range=types.Range(types.Position(d.line, 0), types.Position(d.line, d.end)),
                message=d.message, severity=severities[d.severity], source=d.source)
                for d in diags]))

    session = Session(publish)
    state: dict[str, Any] = {"init_options": None}

    def fs_path(uri: str) -> Path:
        return Path(to_fs_path(uri)).absolute()

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
        cfg, warnings = parse_config(state["init_options"] if first else None, base)
        if pull and supports_configuration():
            settings = await pull_settings()
        cfg, more = parse_config(settings, cfg)
        for warning in warnings + more:
            session.log(warning)
        await session.configure(cfg, state.get("folders") if first else None)

    @server.feature(types.INITIALIZE)
    def on_initialize(params: types.InitializeParams) -> None:
        state["init_options"] = params.initialization_options
        state["folders"] = folders_of(params)

    @server.feature(types.INITIALIZED)
    async def on_initialized(params: types.InitializedParams) -> None:
        await reload(pull=True, first=True)

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
