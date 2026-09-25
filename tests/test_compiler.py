"""Unit tests for `opencrbasic.compiler`: `CompileResult.result_lines`'s rendering rules that are
not any one checker pass's concern -- the file-origin `<file>` prefix (`diagnostics.py`) and the
4096-byte error-message buffer (`docs/checker.md` "Error message buffer").
"""

from __future__ import annotations

from opencrbasic import profiles
from opencrbasic.compiler import CompileResult, _MESSAGE_BUFFER_LIMIT, _bounded_by_message_buffer
from opencrbasic.diagnostics import Diagnostics

PROFILE = profiles.get("cr6")


def _undefined_symbol_program(n: int) -> Diagnostics:
    diags = Diagnostics()
    for i in range(1, n + 1):
        diags.error(i + 3, f"Invalid symbol in expression:     X = Undef{i:04d}.")
    return diags


def test_message_buffer_keeps_every_line_under_the_limit():
    # Oracle-confirmed (`docs/checker.md`): a 150-line program with a distinct undefined-symbol
    # error on every line prints only 69 of them, then stops with no truncation marker -- the 70th
    # message's own `line N: <text>` (+ 2-byte CRLF) would push the running total over 4096 bytes.
    result = CompileResult("bufprobe.CR6", PROFILE, _undefined_symbol_program(150))
    lines = result.result_lines()
    assert lines[0] == "bufprobe.CR6 -- Compile Failed!"
    assert lines[1] == ""
    error_lines = lines[2:]
    assert len(error_lines) == 69
    assert error_lines[-1] == "line 72: Invalid symbol in expression:     X = Undef0069."


def test_message_buffer_does_not_truncate_a_small_error_list():
    result = CompileResult("t.CR6", PROFILE, _undefined_symbol_program(3))
    lines = result.result_lines()
    assert len(lines) == 2 + 3


def test_bounded_by_message_buffer_never_exceeds_the_limit_bytewise():
    lines = [f"line {i}: some message text repeated {i}" for i in range(1, 500)]
    kept = _bounded_by_message_buffer(lines)
    used = sum(len(line.encode("latin-1")) + 2 for line in kept)
    assert used <= _MESSAGE_BUFFER_LIMIT
    # the next dropped line (if any) would have exceeded it
    if len(kept) < len(lines):
        next_cost = len(lines[len(kept)].encode("latin-1")) + 2
        assert used + next_cost > _MESSAGE_BUFFER_LIMIT
