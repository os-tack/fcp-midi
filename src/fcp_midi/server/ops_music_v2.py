"""V2 op handlers for music creation: note, chord, track, cc, bend, mute, solo, program.

These operate directly on MidiModel via mido messages,
replacing the Song-based event-sourcing approach.
"""

from __future__ import annotations

import mido

from fcp_midi.lib.cc_names import parse_cc_value
from fcp_midi.model.midi_model import (
    MidiModel,
    insert_message_at_tick,
)
from fcp_midi.model.timing import ticks_to_position
from fcp_midi.parser.chord import parse_chord
from fcp_midi.parser.duration import parse_duration
from fcp_midi.parser.ops import ParsedOp
from fcp_midi.parser.pitch import parse_pitch
from fcp_midi.parser.position import parse_position
from fcp_midi.server.formatter import format_result
from fcp_midi.server.ops_context_v2 import (
    MidiOpContext,
    display_channel,
    get_time_sigs,
    max_tick_v2,
    resolve_track_v2,
)
from fcp_midi.server.resolvers_v2 import (
    resolve_bank,
    resolve_instrument,
    resolve_velocity,
)


def _resolve_channel(params: dict[str, str]) -> int | None:
    """Parse ``ch`` param (1-indexed) to 0-indexed, or None if absent."""
    if "ch" in params:
        return int(params["ch"]) - 1
    return None


def _resolve_position(params: dict[str, str], ctx: MidiOpContext,
                       key: str = "at", default: str = "1.1") -> int | str:
    """Parse a position param, returning tick or error string."""
    at_str = params.get(key, default)
    time_sigs = get_time_sigs(ctx.model)
    try:
        return parse_position(
            at_str, time_sigs, ctx.model.ppqn,
            reference_tick=ctx.last_tick, song_end_tick=max_tick_v2(ctx.model),
        )
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")


def _resolve_duration(params: dict[str, str], ppqn: int,
                       key: str = "dur", default: str = "quarter") -> int | str:
    """Parse a duration param, returning ticks or error string."""
    dur_str = params.get(key, default)
    try:
        return parse_duration(dur_str, ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid duration: {e}")


# ---------------------------------------------------------------------------
# note
# ---------------------------------------------------------------------------

def op_note(op: ParsedOp, ctx: MidiOpContext) -> str:
    ref = resolve_track_v2(ctx.model, op.target)
    if isinstance(ref, str):
        return ref

    pitch_str = op.params.get("pitch")
    if not pitch_str and "midi" in op.params:
        pitch_str = f"midi:{op.params['midi']}"
    if not pitch_str:
        return format_result(False, "Missing pitch for note")

    try:
        pitch = parse_pitch(pitch_str)
    except ValueError as e:
        return format_result(False, f"Invalid pitch: {e}")

    tick = _resolve_position(op.params, ctx)
    if isinstance(tick, str):
        return tick

    dur = _resolve_duration(op.params, ctx.model.ppqn)
    if isinstance(dur, str):
        return dur

    vel = resolve_velocity(op.params)
    if isinstance(vel, str):
        return vel

    ch = _resolve_channel(op.params)

    note_ref = ctx.model.add_note(
        ref.name, pitch.midi_number, tick, dur, vel,
        channel=ch if ch is not None else None,
    )
    ctx.last_tick = tick + dur

    dur_str = op.params.get("dur", "quarter")
    time_sigs = get_time_sigs(ctx.model)
    pos = ticks_to_position(tick, time_sigs, ctx.model.ppqn)
    return format_result(
        True,
        f"Note {pitch_str} at {pos} on {ref.name} (vel:{vel} dur:{dur_str})"
    )


# ---------------------------------------------------------------------------
# chord
# ---------------------------------------------------------------------------

def op_chord(op: ParsedOp, ctx: MidiOpContext) -> str:
    ref = resolve_track_v2(ctx.model, op.target)
    if isinstance(ref, str):
        return ref

    chord_str = op.params.get("chord")
    if not chord_str:
        return format_result(False, "Missing chord symbol")

    try:
        pitches = parse_chord(chord_str)
    except ValueError as e:
        return format_result(False, f"Invalid chord: {e}")

    tick = _resolve_position(op.params, ctx)
    if isinstance(tick, str):
        return tick

    dur = _resolve_duration(op.params, ctx.model.ppqn)
    if isinstance(dur, str):
        return dur

    vel = resolve_velocity(op.params)
    if isinstance(vel, str):
        return vel

    ch = _resolve_channel(op.params)

    for pitch in pitches:
        ctx.model.add_note(
            ref.name, pitch.midi_number, tick, dur, vel,
            channel=ch if ch is not None else None,
        )

    ctx.last_tick = tick + dur

    time_sigs = get_time_sigs(ctx.model)
    pos = ticks_to_position(tick, time_sigs, ctx.model.ppqn)
    return format_result(
        True,
        f"Chord {chord_str} ({len(pitches)} notes) at {pos} on {ref.name}"
    )


# ---------------------------------------------------------------------------
# track
# ---------------------------------------------------------------------------

def op_track(op: ParsedOp, ctx: MidiOpContext) -> str:
    sub = op.target  # "add" or "remove"

    if sub == "add":
        name = op.params.get("name")
        if not name:
            return format_result(False, "Missing track name",
                                 "track add MyTrack instrument:acoustic-grand-piano")

        bank = resolve_bank(op.params)
        if isinstance(bank, str):
            return bank
        bank_msb, bank_lsb = bank

        resolved = resolve_instrument(
            op.params, ctx.instrument_registry, bank_msb, bank_lsb,
        )
        if isinstance(resolved, str):
            return resolved

        program = resolved.program
        inst_name = resolved.instrument_name
        is_drum_kit = resolved.is_drum_kit

        if is_drum_kit and program is None:
            program = 0

        ch = _resolve_channel(op.params)
        if is_drum_kit and ch is None:
            ch = 9

        try:
            ctx.model.add_track(
                name=name,
                channel=ch,
                instrument=inst_name,
                program=program,
                bank_msb=resolved.bank_msb or 0,
                bank_lsb=resolved.bank_lsb or 0,
            )
        except ValueError as e:
            return format_result(False, str(e))

        track_ref = ctx.model.get_track(name)
        inst_display = inst_name or "no instrument"
        disp_ch = display_channel(track_ref.channel)
        return format_result(True, f"Track '{name}' added (ch:{disp_ch}, {inst_display})")

    elif sub == "remove":
        name = op.params.get("name")
        if not name:
            return format_result(False, "Missing track name for remove")

        ref = ctx.model.get_track(name)
        if not ref:
            from fcp_midi.server.ops_context_v2 import suggest_track_name_v2
            suggestion = suggest_track_name_v2(ctx.model, name)
            return format_result(False, f"Track '{name}' not found", suggestion)

        removed = ctx.model.remove_track(name)
        if removed:
            ctx.note_index.rebuild(ctx.model)
            return format_result(True, f"Track '{name}' removed")
        return format_result(False, f"Failed to remove track '{name}'")

    else:
        return format_result(False, f"Unknown track sub-command: {sub!r}",
                             "track add NAME or track remove NAME")


# ---------------------------------------------------------------------------
# cc
# ---------------------------------------------------------------------------

def op_cc(op: ParsedOp, ctx: MidiOpContext) -> str:
    ref = resolve_track_v2(ctx.model, op.target)
    if isinstance(ref, str):
        return ref

    cc_name = op.params.get("cc_name")
    cc_value_str = op.params.get("cc_value")
    if not cc_name or not cc_value_str:
        return format_result(False, "Missing CC name or value",
                             "cc Piano volume 100 at:1.1")

    try:
        cc_num, cc_val = parse_cc_value(cc_name, cc_value_str)
    except ValueError as e:
        return format_result(False, str(e))

    time_sigs = get_time_sigs(ctx.model)
    at_str = op.params.get("at", "1.1")
    try:
        tick = parse_position(at_str, time_sigs, ctx.model.ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    ch = _resolve_channel(op.params)
    channel = ch if ch is not None else ref.channel

    insert_message_at_tick(
        ref.track,
        mido.Message("control_change", channel=channel, control=cc_num, value=cc_val),
        tick,
    )

    pos = ticks_to_position(tick, time_sigs, ctx.model.ppqn)
    return format_result(True, f"CC {cc_name}={cc_val} at {pos} on {ref.name}")


# ---------------------------------------------------------------------------
# bend
# ---------------------------------------------------------------------------

def op_bend(op: ParsedOp, ctx: MidiOpContext) -> str:
    ref = resolve_track_v2(ctx.model, op.target)
    if isinstance(ref, str):
        return ref

    value_str = op.params.get("value", "0")
    if value_str.lower() == "center":
        value = 0
    else:
        try:
            value = int(value_str)
        except ValueError:
            return format_result(False, f"Invalid bend value: {value_str!r}")

    if value < -8192 or value > 8191:
        return format_result(False, f"Bend value out of range (-8192 to 8191): {value}")

    time_sigs = get_time_sigs(ctx.model)
    at_str = op.params.get("at", "1.1")
    try:
        tick = parse_position(at_str, time_sigs, ctx.model.ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    ch = _resolve_channel(op.params)
    channel = ch if ch is not None else ref.channel

    insert_message_at_tick(
        ref.track,
        mido.Message("pitchwheel", channel=channel, pitch=value),
        tick,
    )

    pos = ticks_to_position(tick, time_sigs, ctx.model.ppqn)
    return format_result(True, f"Pitch bend={value} at {pos} on {ref.name}")


# ---------------------------------------------------------------------------
# mute / solo
# ---------------------------------------------------------------------------

def op_mute(op: ParsedOp, ctx: MidiOpContext) -> str:
    ref = resolve_track_v2(ctx.model, op.target)
    if isinstance(ref, str):
        return ref

    ref.mute = not ref.mute
    state = "muted" if ref.mute else "unmuted"
    return format_result(True, f"Track '{ref.name}' {state}")


def op_solo(op: ParsedOp, ctx: MidiOpContext) -> str:
    ref = resolve_track_v2(ctx.model, op.target)
    if isinstance(ref, str):
        return ref

    ref.solo = not ref.solo
    state = "solo on" if ref.solo else "solo off"
    return format_result(True, f"Track '{ref.name}' {state}")


# ---------------------------------------------------------------------------
# program
# ---------------------------------------------------------------------------

def op_program(op: ParsedOp, ctx: MidiOpContext) -> str:
    ref = resolve_track_v2(ctx.model, op.target)
    if isinstance(ref, str):
        return ref

    bank = resolve_bank(op.params)
    if isinstance(bank, str):
        return bank
    bank_msb, bank_lsb = bank

    resolved = resolve_instrument(
        op.params, ctx.instrument_registry, bank_msb, bank_lsb,
    )
    if isinstance(resolved, str):
        return resolved

    if resolved.program is None and resolved.instrument_name is None:
        return format_result(False, "Missing instrument name or program number")

    # Find and replace the existing program_change message in the track
    new_program = resolved.program if resolved.program is not None else 0
    for i, msg in enumerate(ref.track):
        if msg.type == "program_change":
            ref.track[i] = mido.Message(
                "program_change", channel=ref.channel,
                program=new_program, time=msg.time,
            )
            break

    ref.program = new_program
    if resolved.bank_msb is not None:
        ref.bank_msb = resolved.bank_msb
    if resolved.bank_lsb is not None:
        ref.bank_lsb = resolved.bank_lsb

    return format_result(
        True,
        f"Track '{ref.name}' instrument set to {resolved.instrument_name} (program:{new_program})"
    )
