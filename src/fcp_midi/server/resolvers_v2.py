"""NoteIndex-based selector resolution for v2 (mido-native) architecture.

Replaces the simplified _resolve_selectors_v2 from ops_editing_v2.py with
proper NoteIndex-powered resolution. Supports all selector types:
  @track:NAME, @pitch:P, @range:M.B-M.B, @channel:N,
  @velocity:LO-HI, @all, @recent:N, and negation via @not:type:value.

Also houses the instrument/bank/velocity resolution helpers, which are
shared parameter-parsing utilities with no dependency on either model
generation (v1 Song or v2 MidiModel).
"""

from __future__ import annotations

from dataclasses import dataclass

from fcp_midi.lib.gm_instruments import instrument_to_program, program_to_instrument
from fcp_midi.lib.instrument_registry import InstrumentRegistry
from fcp_midi.lib.velocity_names import parse_velocity
from fcp_midi.model.midi_model import NoteIndex, NoteRef
from fcp_midi.parser.pitch import parse_pitch
from fcp_midi.parser.position import parse_position, _ticks_per_beat
from fcp_midi.parser.selector import Selector
from fcp_midi.server.formatter import format_result
from fcp_midi.server.ops_context_v2 import MidiOpContext, get_time_sigs


@dataclass
class InstrumentResolution:
    """Result of resolving an instrument from op params."""
    program: int | None
    instrument_name: str | None
    bank_msb: int | None
    bank_lsb: int | None
    is_drum_kit: bool = False


def resolve_bank(params: dict[str, str]) -> tuple[int | None, int | None] | str:
    """Parse ``bank:MSB[.LSB]`` param. Returns (msb, lsb) tuple or error string."""
    bank_str = params.get("bank")
    if not bank_str:
        return (None, None)
    try:
        parts = bank_str.split(".")
        bank_msb = int(parts[0])
        if not (0 <= bank_msb <= 127):
            return format_result(False, "Bank MSB must be 0-127")
        bank_lsb = None
        if len(parts) > 1:
            bank_lsb = int(parts[1])
            if not (0 <= bank_lsb <= 127):
                return format_result(False, "Bank LSB must be 0-127")
        return (bank_msb, bank_lsb)
    except ValueError:
        return format_result(False, f"Invalid bank value: {bank_str!r}")


_DRUM_NAMES = frozenset({"standard-kit", "drum-kit", "drums", "percussion"})


def resolve_instrument(
    params: dict[str, str],
    instrument_registry: InstrumentRegistry | None,
    bank_msb: int | None = None,
    bank_lsb: int | None = None,
) -> InstrumentResolution | str:
    """Resolve instrument from op params (program:N or instrument name).

    Returns InstrumentResolution or error string.
    """
    inst_name = params.get("instrument")
    raw_program = params.get("program")

    if raw_program is not None:
        try:
            program = int(raw_program)
            if not (0 <= program <= 127):
                return format_result(False, "Program must be 0-127")
        except ValueError:
            return format_result(False, f"Invalid program number: {raw_program!r}")
        # Reverse lookup for display name
        if inst_name is None:
            inst_name = program_to_instrument(program)
        return InstrumentResolution(
            program=program,
            instrument_name=inst_name,
            bank_msb=bank_msb,
            bank_lsb=bank_lsb,
        )

    if not inst_name:
        return InstrumentResolution(
            program=None,
            instrument_name=None,
            bank_msb=bank_msb,
            bank_lsb=bank_lsb,
        )

    normalized = inst_name.strip().lower().replace(" ", "-")
    is_drum_kit = normalized in _DRUM_NAMES

    if instrument_registry is not None:
        spec = instrument_registry.resolve(inst_name)
        if spec is not None:
            resolved_bank_msb = bank_msb
            resolved_bank_lsb = bank_lsb
            if resolved_bank_msb is None and spec.bank_msb != 0:
                resolved_bank_msb = spec.bank_msb
            if resolved_bank_lsb is None and spec.bank_lsb != 0:
                resolved_bank_lsb = spec.bank_lsb
            return InstrumentResolution(
                program=spec.program,
                instrument_name=inst_name,
                bank_msb=resolved_bank_msb,
                bank_lsb=resolved_bank_lsb,
                is_drum_kit=is_drum_kit,
            )
        elif not is_drum_kit:
            suggestion = instrument_registry.suggest(inst_name)
            msg = f"Unknown instrument: {inst_name!r}"
            if suggestion:
                msg += f"\n  {suggestion}"
            return format_result(False, msg)
    else:
        program = instrument_to_program(inst_name)
        if program is None and not is_drum_kit:
            return format_result(False, f"Unknown instrument: {inst_name!r}")
        return InstrumentResolution(
            program=program,
            instrument_name=inst_name,
            bank_msb=bank_msb,
            bank_lsb=bank_lsb,
            is_drum_kit=is_drum_kit,
        )

    # Drum kit fallback
    return InstrumentResolution(
        program=0,
        instrument_name=inst_name,
        bank_msb=bank_msb,
        bank_lsb=bank_lsb,
        is_drum_kit=True,
    )


def resolve_velocity(
    params: dict[str, str],
    key: str = "vel",
    default: str = "80",
) -> int | str:
    """Parse a velocity param, returning 1-127 or error string."""
    vel_str = params.get(key, default)
    try:
        return parse_velocity(vel_str)
    except ValueError as e:
        return format_result(False, f"Invalid velocity: {e}")


def resolve_notes_v2(
    selectors: list[Selector],
    ctx: MidiOpContext,
) -> list[NoteRef] | str:
    """Resolve selectors into NoteRefs using the NoteIndex.

    Returns a list of NoteRefs matching the selectors, or an error string.
    """
    if not selectors:
        return format_result(
            False,
            "No selectors specified",
            "Use @track:NAME, @range:M.B-M.B, @pitch:P, @all, etc.",
        )

    positive = [s for s in selectors if not s.negated]
    negated = [s for s in selectors if s.negated]

    track_name: str | None = None
    pitch_midi: int | None = None
    range_start: int | None = None
    range_end: int | None = None
    channel: int | None = None
    vel_low: int | None = None
    vel_high: int | None = None
    use_all = False
    use_recent: int | None = None

    time_sigs = get_time_sigs(ctx.model)
    ppqn = ctx.model.ppqn
    idx = ctx.note_index

    for sel in positive:
        if sel.type == "track":
            track_name = sel.value
        elif sel.type == "channel":
            try:
                channel = int(sel.value)
            except ValueError:
                return format_result(False, f"Invalid channel: {sel.value!r}")
        elif sel.type == "range":
            range_parts = sel.value.split("-")
            if len(range_parts) != 2:
                return format_result(
                    False, f"Invalid range: {sel.value!r}", "@range:1.1-4.4"
                )
            try:
                range_start = parse_position(range_parts[0], time_sigs, ppqn)
                range_end = parse_position(range_parts[1], time_sigs, ppqn)
                ts = time_sigs[0] if time_sigs else None
                denom = ts.denominator if ts else 4
                range_end += _ticks_per_beat(denom, ppqn)
            except ValueError as e:
                return format_result(False, f"Invalid range position: {e}")
        elif sel.type == "pitch":
            try:
                p = parse_pitch(sel.value)
                pitch_midi = p.midi_number
            except ValueError as e:
                return format_result(False, f"Invalid pitch: {e}")
        elif sel.type == "velocity":
            vel_parts = sel.value.split("-")
            if len(vel_parts) != 2:
                return format_result(
                    False, f"Invalid velocity range: {sel.value!r}"
                )
            try:
                vel_low = int(vel_parts[0])
                vel_high = int(vel_parts[1])
            except ValueError:
                return format_result(
                    False, f"Invalid velocity values: {sel.value!r}"
                )
        elif sel.type == "all":
            use_all = True
        elif sel.type == "recent":
            use_recent = int(sel.value) if sel.value else 1

    # @recent: return the last N notes by absolute tick
    if use_recent is not None:
        all_notes = sorted(idx.all, key=lambda n: n.abs_tick, reverse=True)
        return all_notes[:use_recent]

    # Pick starting set via the most specific NoteIndex lookup
    if use_all or (not positive and negated):
        notes = list(idx.all)
    elif track_name:
        ref = ctx.model.get_track(track_name)
        if not ref:
            return format_result(False, f"Track '{track_name}' not found")
        notes = list(idx.by_track.get(track_name, []))
    elif pitch_midi is not None:
        notes = list(idx.by_pitch.get(pitch_midi, []))
    elif channel is not None:
        notes = list(idx.by_channel.get(channel, []))
    else:
        notes = list(idx.all)

    # Apply remaining positive filters
    if track_name and not use_all:
        # Already filtered by track as primary lookup — skip
        pass
    elif track_name:
        notes = [n for n in notes if n.track_name == track_name]

    if pitch_midi is not None and track_name:
        # track was primary, still need pitch filter
        notes = [n for n in notes if n.pitch == pitch_midi]
    elif pitch_midi is not None:
        # pitch was primary — skip
        pass

    if channel is not None and (track_name or pitch_midi is not None):
        # channel wasn't primary, filter it
        notes = [n for n in notes if n.channel == channel]

    if range_start is not None and range_end is not None:
        notes = [n for n in notes if range_start <= n.abs_tick < range_end]

    if vel_low is not None and vel_high is not None:
        notes = [n for n in notes if vel_low <= n.velocity <= vel_high]

    # Apply negated selectors
    if negated and notes:
        for sel in negated:
            result = _apply_negation(sel, notes, time_sigs, ppqn)
            if isinstance(result, str):
                return result
            notes = result

    return notes


def _apply_negation(
    sel: Selector,
    notes: list[NoteRef],
    time_sigs: list,
    ppqn: int,
) -> list[NoteRef] | str:
    """Subtract notes matching a single negated selector."""
    if sel.type == "track":
        return [n for n in notes if n.track_name != sel.value]
    elif sel.type == "pitch":
        try:
            p = parse_pitch(sel.value)
            return [n for n in notes if n.pitch != p.midi_number]
        except ValueError as e:
            return format_result(False, f"Invalid pitch: {e}")
    elif sel.type == "channel":
        try:
            ch = int(sel.value)
            return [n for n in notes if n.channel != ch]
        except ValueError:
            return format_result(False, f"Invalid channel: {sel.value!r}")
    elif sel.type == "range":
        range_parts = sel.value.split("-")
        if len(range_parts) != 2:
            return format_result(False, f"Invalid range: {sel.value!r}")
        try:
            start = parse_position(range_parts[0], time_sigs, ppqn)
            end = parse_position(range_parts[1], time_sigs, ppqn)
            ts = time_sigs[0] if time_sigs else None
            denom = ts.denominator if ts else 4
            end += _ticks_per_beat(denom, ppqn)
            return [n for n in notes if not (start <= n.abs_tick < end)]
        except ValueError as e:
            return format_result(False, f"Invalid range: {e}")
    elif sel.type == "velocity":
        vel_parts = sel.value.split("-")
        if len(vel_parts) != 2:
            return format_result(False, f"Invalid velocity range: {sel.value!r}")
        try:
            lo = int(vel_parts[0])
            hi = int(vel_parts[1])
            return [n for n in notes if not (lo <= n.velocity <= hi)]
        except ValueError:
            return format_result(False, f"Invalid velocity: {sel.value!r}")
    return notes
