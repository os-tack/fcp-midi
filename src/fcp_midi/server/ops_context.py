"""MidiOpContext and conductor-track helpers for v2 op handlers.

Provides the shared context object and utilities for reading/writing
metadata from the mido conductor track (track 0).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

import mido

from fcp_midi.lib.instrument_registry import InstrumentRegistry
from fcp_midi.model.midi_model import (
    MidiModel,
    NoteIndex,
    TrackRef,
    delta_to_absolute,
    insert_message_at_tick,
)
from fcp_midi.server.formatter import format_result


@dataclass
class MidiOpContext:
    """Shared state passed to every v2 op handler."""

    model: MidiModel
    note_index: NoteIndex
    instrument_registry: InstrumentRegistry | None = None
    last_tick: int = 0

    def snapshot(self) -> bytes:
        """Take a byte snapshot for undo."""
        return self.model.snapshot()


# ---------------------------------------------------------------------------
# Conductor-track helpers
# ---------------------------------------------------------------------------

class _FakeTimeSig:
    """Lightweight stand-in for Song.TimeSignature, used by parse_position."""

    def __init__(self, absolute_tick: int, numerator: int, denominator: int):
        self.absolute_tick = absolute_tick
        self.numerator = numerator
        self.denominator = denominator


def get_time_sigs(model: MidiModel) -> list[_FakeTimeSig]:
    """Extract time signatures from the conductor track."""
    result: list[_FakeTimeSig] = []
    if not model.file.tracks:
        return [_FakeTimeSig(0, 4, 4)]

    abs_tick = 0
    for msg in model.file.tracks[0]:
        abs_tick += msg.time
        if msg.type == "time_signature":
            result.append(
                _FakeTimeSig(abs_tick, msg.numerator, msg.denominator)
            )

    if not result:
        result.append(_FakeTimeSig(0, 4, 4))
    return result


def get_tempo_bpm(model: MidiModel) -> float:
    """Get the first tempo from the conductor track."""
    if model.file.tracks:
        for msg in model.file.tracks[0]:
            if msg.type == "set_tempo":
                return mido.tempo2bpm(msg.tempo)
    return 120.0


def max_tick(model: MidiModel) -> int:
    """Find the maximum tick (note end) across all tracks."""
    max_t = 0
    for ref in model.tracks.values():
        abs_tick = 0
        for msg in ref.track:
            abs_tick += msg.time
            if msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if abs_tick > max_t:
                    max_t = abs_tick
    return max_t


# ---------------------------------------------------------------------------
# Track resolution helpers
# ---------------------------------------------------------------------------

def resolve_track(model: MidiModel, name: str | None) -> TrackRef | str:
    """Resolve a track name, returning TrackRef or error string."""
    if not name:
        return format_result(False, "Missing track name")

    ref = model.get_track(name)
    if ref:
        return ref

    suggestion = suggest_track_name(model, name)
    return format_result(False, f"Track '{name}' not found", suggestion)


def suggest_track_name(model: MidiModel, name: str) -> str | None:
    """Fuzzy-match a track name and return a suggestion string."""
    if not model.tracks:
        return None

    existing = list(model.tracks.keys())
    matches = difflib.get_close_matches(name, existing, n=1, cutoff=0.4)
    if matches:
        return f"Did you mean '{matches[0]}'?"
    return f"Available tracks: {', '.join(existing)}"


def display_channel(ch_0indexed: int) -> int:
    """Convert internal 0-indexed channel to 1-indexed display."""
    return ch_0indexed + 1
