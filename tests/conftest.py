"""Shared test fixtures for MIDI FCP integration tests.

``Harness`` drives ``MidiAdapter`` through the real fcp-core dispatch
primitives (``parse_op``, ``SessionDispatcher``, ``FcpDomainAdapter``
methods) — the same objects ``create_fcp_server()`` wires together in
``main.py`` — rather than hand-constructing domain internals. It mirrors
the batch-atomicity/digest logic in ``fcp_core.server.create_fcp_server``
so tests exercise the same behavior the live MCP server provides, without
needing a stdio/FastMCP transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from fcp_core import EventLog, ParseError, SessionDispatcher, format_result, parse_op

from fcp_midi.adapter import MidiAdapter


class _AdapterHooks:
    """Bridges MidiAdapter to fcp_core's SessionHooks protocol."""

    def __init__(self, adapter: MidiAdapter) -> None:
        self._adapter = adapter

    def on_new(self, params: dict[str, str]) -> Any:
        title = params.pop("title", "Untitled")
        return self._adapter.create_empty(title, params)

    def on_open(self, path: str) -> Any:
        return self._adapter.deserialize(path)

    def on_save(self, model: Any, path: str) -> None:
        self._adapter.serialize(model, path)

    def on_rebuild_indices(self, model: Any) -> None:
        self._adapter.rebuild_indices(model)

    def get_digest(self, model: Any) -> str:
        return self._adapter.get_digest(model)


class Harness:
    """Test-only facade over the real adapter + session dispatch path."""

    def __init__(self) -> None:
        self.adapter = MidiAdapter()
        self.session: SessionDispatcher = SessionDispatcher(
            hooks=_AdapterHooks(self.adapter),
            event_log=EventLog(),
            reverse_event=self.adapter.reverse_event,
            replay_event=self.adapter.replay_event,
        )

    @property
    def model(self) -> Any:
        return self.session.model

    def execute_session(self, action: str) -> str:
        """Mirrors the {domain}_session tool."""
        text = self.session.dispatch(action)
        if self.session.model is not None:
            digest = self.adapter.get_digest(self.session.model)
            text = f"{text}\n{digest}" if digest else text
        return text

    def execute_query(self, q: str) -> str:
        """Mirrors the {domain}_query tool."""
        if self.session.model is None:
            return format_result(False, "No model loaded.")
        return self.adapter.dispatch_query(q, self.session.model)

    def execute_ops(self, ops: list[str]) -> list[str]:
        """Mirrors the {domain} (batch mutation) tool, including the
        take_snapshot/restore_snapshot batch-atomicity opt-in."""
        if self.session.model is None:
            return [format_result(
                False, "No model loaded. Use session 'new' or 'open' first.",
            )]

        take_snap = getattr(self.adapter, "take_snapshot", None)
        snapshot = take_snap(self.session.model) if take_snap else None

        results: list[str] = []
        for i, op_str in enumerate(ops):
            parsed = parse_op(op_str)
            if isinstance(parsed, ParseError):
                if snapshot is not None:
                    self.adapter.restore_snapshot(self.session.model, snapshot)
                    return [
                        f"! Batch failed at op {i + 1}: {op_str}. "
                        f"Error: {parsed.error}. "
                        f"State rolled back ({i} ops reverted)."
                    ]
                results.append(format_result(False, parsed.error))
                continue

            result = self.adapter.dispatch_op(
                parsed, self.session.model, self.session.event_log,
            )

            if not result.success and result.message and snapshot is not None:
                self.adapter.restore_snapshot(self.session.model, snapshot)
                return [
                    f"! Batch failed at op {i + 1}: {op_str}. "
                    f"Error: {result.message}. "
                    f"State rolled back ({i} ops reverted)."
                ]

            formatted = format_result(result.success, result.message, result.prefix)
            if formatted.strip():
                results.append(formatted)

        digest = self.adapter.get_digest(self.session.model)
        if digest:
            results.append(digest)
        return results


@pytest.fixture
def harness() -> Harness:
    """Provide a fresh Harness instance (no session started)."""
    return Harness()


@pytest.fixture
def harness_with_song(harness: Harness) -> Harness:
    """Provide a Harness with a song already created."""
    result = harness.execute_session(
        'new "Test Song" tempo:120 time-sig:4/4 key:C-major'
    )
    assert result.startswith("+")
    return harness


@pytest.fixture
def harness_with_piano(harness_with_song: Harness) -> Harness:
    """Provide a Harness with a song and a Piano track."""
    results = harness_with_song.execute_ops(
        ["track add Piano instrument:acoustic-grand-piano"]
    )
    assert any(r.startswith("+") for r in results)
    return harness_with_song
