"""FcpDomainAdapter implementation for mido-native MIDI — bridges fcp-core to MidiModel.

Satisfies ``FcpDomainAdapter[MidiModel, SnapshotEvent]``. Uses byte snapshots
for undo/redo instead of fine-grained event sourcing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fcp_core import EventLog as CoreEventLog
from fcp_core import OpResult, ParsedOp as GenericParsedOp

from fcp_midi.lib.instrument_registry import InstrumentRegistry
from fcp_midi.model.midi_model import MidiModel, NoteIndex
from fcp_midi.parser.ops import ParsedOp as DomainParsedOp, ParseError, parse_op as domain_parse_op
from fcp_midi.server.formatter import format_result
from fcp_midi.server.ops_context import MidiOpContext
from fcp_midi.server.queries import dispatch_query


# ---------------------------------------------------------------------------
# SnapshotEvent — simple event type for byte-snapshot undo/redo
# ---------------------------------------------------------------------------

@dataclass
class SnapshotEvent:
    """Event type for byte-snapshot undo/redo."""

    type: str = "snapshot"
    before: bytes = b""
    after: bytes = b""
    summary: str = ""


# ---------------------------------------------------------------------------
# MidiAdapter
# ---------------------------------------------------------------------------

class MidiAdapter:
    """FcpDomainAdapter implementation for mido-native MIDI composition.

    Satisfies ``FcpDomainAdapter[MidiModel, SnapshotEvent]``.
    """

    def __init__(self) -> None:
        self.note_index: NoteIndex = NoteIndex()
        self.instrument_registry: InstrumentRegistry = InstrumentRegistry()
        self._last_tick: int = 0
        # Tracker block-mode import state
        self._tracker_buffer: list[str] | None = None
        self._tracker_header: str | None = None

    # -- FcpDomainAdapter protocol methods --

    def create_empty(self, title: str, params: dict[str, str]) -> MidiModel:
        """Create a new empty MidiModel from session params."""
        tempo = 120.0
        time_sig = (4, 4)
        key: str | None = None
        ppqn = 480

        if "tempo" in params:
            try:
                tempo = float(params["tempo"])
            except ValueError:
                pass

        if "time-sig" in params:
            m = re.match(r"^(\d+)/(\d+)$", params["time-sig"])
            if m:
                time_sig = (int(m.group(1)), int(m.group(2)))

        if "key" in params:
            key = params["key"]

        if "ppqn" in params:
            try:
                ppqn = int(params["ppqn"])
            except ValueError:
                pass

        model = MidiModel(
            title=title,
            ppqn=ppqn,
            tempo=tempo,
            time_sig=time_sig,
            key=key,
        )
        self.note_index = NoteIndex()
        self._last_tick = 0
        return model

    def serialize(self, model: MidiModel, path: str) -> None:
        """Serialize — model.save(path)."""
        model.save(path)

    def deserialize(self, path: str) -> MidiModel:
        """Deserialize — MidiModel.load(path)."""
        model = MidiModel.load(path)
        self.note_index.rebuild(model)
        return model

    def rebuild_indices(self, model: MidiModel) -> None:
        """Rebuild NoteIndex."""
        self.note_index.rebuild(model)

    def get_digest(self, model: MidiModel) -> str:
        """Return a compact state fingerprint."""
        return model.get_digest()

    def dispatch_op(
        self,
        op: GenericParsedOp,
        model: MidiModel,
        log: CoreEventLog,
    ) -> OpResult:
        """Execute a parsed operation on the model.

        1. Check for tracker block-mode intercept
        2. Take byte snapshot (for undo)
        3. Build MidiOpContext
        4. Re-parse through domain parser
        5. Dispatch to handler
        6. Rebuild note index
        7. Log snapshot event
        8. Return OpResult
        """
        raw = op.raw.strip()

        # -- Tracker block-mode intercept --
        # Accumulating step lines into buffer
        if self._tracker_buffer is not None:
            if raw.lower().startswith("tracker") and "end" in raw.lower():
                return self._flush_tracker(model, log)
            # Nested tracker blocks are forbidden
            if raw.lower().startswith("tracker") and "import" in raw.lower():
                return OpResult(success=False, message="Nested tracker blocks are not allowed")
            self._tracker_buffer.append(raw)
            return OpResult(success=True, message="", prefix="~")

        # Start a new tracker block
        if raw.lower().startswith("tracker") and "import" in raw.lower():
            return self._start_tracker(raw, model)

        # -- End tracker-end without prior import --
        if raw.lower().startswith("tracker") and "end" in raw.lower():
            return OpResult(success=False, message="'tracker end' without prior 'tracker ... import'")

        # Take pre-op snapshot
        before = model.snapshot()

        # Build context
        ctx = MidiOpContext(
            model=model,
            note_index=self.note_index,
            instrument_registry=self.instrument_registry,
            last_tick=self._last_tick,
        )

        # Re-parse through domain parser
        domain_parsed = domain_parse_op(op.raw)
        if isinstance(domain_parsed, ParseError):
            return OpResult(success=False, message=f"Parse error: {domain_parsed.error}")

        # Dispatch to handler
        try:
            result_str = self._dispatch(domain_parsed, ctx)
        except (ValueError, KeyError) as exc:
            return OpResult(success=False, message=f"Error: {exc}")

        # Sync state back
        self._last_tick = ctx.last_tick

        # Rebuild index
        self.note_index.rebuild(model)

        # Log snapshot for undo
        after = model.snapshot()
        log.append(SnapshotEvent(before=before, after=after, summary=op.raw))

        # Parse the result string into OpResult
        if result_str.startswith("!"):
            return OpResult(success=False, message=result_str.lstrip("! "))
        prefix = ""
        if len(result_str) > 1 and result_str[1] == " ":
            prefix = result_str[0]
        message = result_str[2:] if prefix else result_str
        return OpResult(success=True, message=message, prefix=prefix)

    def dispatch_query(self, query: str, model: MidiModel) -> str:
        """Execute query via dispatch_query."""
        ctx = MidiOpContext(
            model=model,
            note_index=self.note_index,
            instrument_registry=self.instrument_registry,
            last_tick=self._last_tick,
        )
        return dispatch_query(query, ctx)

    def reverse_event(self, event: SnapshotEvent, model: MidiModel) -> None:
        """Undo — restore from before-snapshot."""
        model.restore(event.before)
        self.note_index.rebuild(model)

    def replay_event(self, event: SnapshotEvent, model: MidiModel) -> None:
        """Redo — restore from after-snapshot."""
        model.restore(event.after)
        self.note_index.rebuild(model)

    def take_snapshot(self, model: MidiModel) -> bytes:
        """Byte snapshot for batch atomicity (fcp-core rolls the whole
        batch back to this if any op in it fails)."""
        return model.snapshot()

    def restore_snapshot(self, model: MidiModel, snapshot: bytes) -> None:
        """Restore the model from a batch-atomicity snapshot."""
        model.restore(snapshot)
        self.note_index.rebuild(model)

    # -- Tracker block-mode helpers --

    def _start_tracker(self, raw: str, model: MidiModel) -> OpResult:
        """Begin accumulating tracker step lines."""
        # Parse: "tracker TRACK import at:POS [res:RES]"
        parts = raw.split()
        if len(parts) < 3:
            return OpResult(success=False, message="Usage: tracker TRACK import at:POS [res:RES]")

        track_name = parts[1]
        ref = model.get_track(track_name)
        if ref is None:
            return OpResult(success=False, message=f"Track '{track_name}' not found")

        self._tracker_buffer = []
        self._tracker_header = raw
        return OpResult(success=True, message="", prefix="~")

    def _flush_tracker(self, model: MidiModel, log: CoreEventLog) -> OpResult:
        """Process accumulated tracker step lines and import notes."""
        from fcp_midi.server.tracker_format import (
            parse_step_line,
            pair_tracker_events,
            _ticks_per_step,
        )
        from fcp_midi.server.ops_context import get_time_sigs
        from fcp_midi.parser.position import parse_position

        header = self._tracker_header or ""
        buffer = self._tracker_buffer or []
        self._tracker_buffer = None
        self._tracker_header = None

        # Parse header: "tracker TRACK import at:POS [res:RES]"
        parts = header.split()
        track_name = parts[1] if len(parts) > 1 else ""
        ref = model.get_track(track_name)
        if ref is None:
            return OpResult(success=False, message=f"Track '{track_name}' not found")

        # Extract params
        at_pos: str | None = None
        resolution = "16th"
        for p in parts:
            if p.startswith("at:"):
                at_pos = p[3:]
            elif p.startswith("res:"):
                resolution = p[4:]

        time_sigs = get_time_sigs(model)
        ppqn = model.ppqn

        if at_pos is None:
            return OpResult(success=False, message="Missing at: parameter for tracker import")

        try:
            start_tick = parse_position(at_pos, time_sigs, ppqn)
        except ValueError as e:
            return OpResult(success=False, message=f"Invalid position: {e}")

        try:
            tps = _ticks_per_step(resolution, ppqn)
        except ValueError as e:
            return OpResult(success=False, message=str(e))

        # Take before-snapshot
        before = model.snapshot()

        # Parse step lines
        steps: list[tuple[int, list[tuple[int, int, int]]]] = []
        for line in buffer:
            line = line.strip()
            if not line:
                continue
            try:
                step_data = parse_step_line(line)
                steps.append(step_data)
            except ValueError as e:
                # Restore and report error
                return OpResult(success=False, message=f"Bad step line: {e}")

        if not steps:
            return OpResult(success=False, message="No step lines in tracker block")

        # Pair ON/OFF events into notes
        paired = pair_tracker_events(steps, start_tick, tps)

        # Add notes to model
        for pitch, velocity, abs_tick, duration in paired:
            model.add_note(track_name, pitch, abs_tick, duration, velocity)

        # Rebuild index
        self.note_index.rebuild(model)

        # Log snapshot for undo
        after = model.snapshot()
        log.append(SnapshotEvent(
            before=before,
            after=after,
            summary=f"tracker import {track_name} ({len(paired)} notes)",
        ))

        return OpResult(
            success=True,
            message=f"Imported {len(paired)} notes from {len(steps)} steps",
            prefix="+",
        )

    # -- Internal dispatch --

    def _dispatch(self, op: DomainParsedOp, ctx: MidiOpContext) -> str:
        """Route a domain-parsed op to the appropriate handler."""
        from fcp_midi.server.ops_music import (
            op_note, op_chord, op_track, op_cc, op_bend,
            op_mute, op_solo, op_program,
        )
        from fcp_midi.server.ops_meta import (
            op_tempo, op_time_sig, op_key_sig, op_marker, op_title,
        )
        from fcp_midi.server.ops_editing import (
            op_remove, op_move, op_copy, op_transpose, op_velocity,
            op_quantize, op_modify, op_repeat, op_crescendo,
        )

        handlers = {
            "note": op_note,
            "chord": op_chord,
            "track": op_track,
            "cc": op_cc,
            "bend": op_bend,
            "tempo": op_tempo,
            "time-sig": op_time_sig,
            "key-sig": op_key_sig,
            "marker": op_marker,
            "title": op_title,
            "remove": op_remove,
            "move": op_move,
            "copy": op_copy,
            "transpose": op_transpose,
            "velocity": op_velocity,
            "quantize": op_quantize,
            "mute": op_mute,
            "solo": op_solo,
            "program": op_program,
            "modify": op_modify,
            "repeat": op_repeat,
            "crescendo": op_crescendo,
            "decrescendo": op_crescendo,
        }
        handler = handlers.get(op.verb)
        if handler is None:
            return format_result(False, f"Unknown verb: {op.verb!r}")

        return handler(op, ctx)
