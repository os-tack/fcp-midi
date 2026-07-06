"""Op handlers for editing: remove, move, copy, transpose, velocity,
quantize, modify, repeat, crescendo/decrescendo.

These operate directly on MidiModel via mido messages. Selector
resolution uses resolve_notes from resolvers.py (NoteIndex-based).
"""

from __future__ import annotations

import mido

from fcp_midi.model.midi_model import (
    MidiModel,
    NoteRef,
    delta_to_absolute,
    insert_message_at_tick,
    remove_message_at_index,
)
from fcp_midi.model.timing import ticks_to_position
from fcp_midi.parser.duration import parse_duration
from fcp_midi.parser.ops import ParsedOp
from fcp_midi.parser.pitch import parse_pitch
from fcp_midi.parser.position import parse_position
from fcp_midi.parser.selector import Selector
from fcp_midi.lib.velocity_names import parse_velocity
from fcp_midi.server.formatter import format_result
from fcp_midi.server.ops_context import (
    MidiOpContext,
    get_time_sigs,
    max_tick,
)
from fcp_midi.server.resolvers import resolve_notes


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

def op_remove(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    count = 0
    for note in notes:
        removed = ctx.model.remove_note_at(note.track_name, note.pitch, note.abs_tick)
        if removed:
            count += 1

    ctx.note_index.rebuild(ctx.model)
    return format_result(True, f"Removed {count} note(s)")


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

def op_move(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    to_str = op.params.get("to")
    if not to_str:
        return format_result(False, "Missing to: parameter", "move @track:Piano to:3.1")

    time_sigs = get_time_sigs(ctx.model)
    try:
        to_tick = parse_position(
            to_str, time_sigs, ctx.model.ppqn,
            reference_tick=ctx.last_tick, song_end_tick=max_tick(ctx.model),
        )
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    min_tick_val = min(n.abs_tick for n in notes)
    delta = to_tick - min_tick_val

    # Collect note data first, then remove, then re-add
    note_data = [
        (n.track_name, n.pitch, n.abs_tick, n.duration_ticks, n.velocity, n.channel)
        for n in notes
    ]

    # Remove all old notes
    for n in notes:
        ctx.model.remove_note_at(n.track_name, n.pitch, n.abs_tick)

    # Re-add at new positions
    for track_name, pitch, old_tick, dur, vel, ch in note_data:
        new_tick = max(0, old_tick + delta)
        ctx.model.add_note(track_name, pitch, new_tick, dur, vel, ch)

    ctx.note_index.rebuild(ctx.model)
    pos = ticks_to_position(to_tick, time_sigs, ctx.model.ppqn)
    return format_result(True, f"Moved {len(notes)} note(s) to {pos}")


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------

def op_copy(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    to_str = op.params.get("to")
    if not to_str:
        return format_result(False, "Missing to: parameter")

    time_sigs = get_time_sigs(ctx.model)
    try:
        to_tick = parse_position(
            to_str, time_sigs, ctx.model.ppqn,
            reference_tick=ctx.last_tick, song_end_tick=max_tick(ctx.model),
        )
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    min_tick_val = min(n.abs_tick for n in notes)
    delta = to_tick - min_tick_val

    for n in notes:
        new_tick = max(0, n.abs_tick + delta)
        ctx.model.add_note(n.track_name, n.pitch, new_tick, n.duration_ticks, n.velocity, n.channel)

    ctx.note_index.rebuild(ctx.model)
    pos = ticks_to_position(to_tick, time_sigs, ctx.model.ppqn)
    return format_result(True, f"Copied {len(notes)} note(s) to {pos}")


# ---------------------------------------------------------------------------
# transpose
# ---------------------------------------------------------------------------

def op_transpose(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    semitones_str = op.target
    if not semitones_str:
        return format_result(False, "Missing semitone count", "transpose @track:Piano +5")

    try:
        semitones = int(semitones_str)
    except ValueError:
        return format_result(False, f"Invalid semitone value: {semitones_str!r}")

    # Collect note data, remove old, add transposed
    note_data = [
        (n.track_name, n.pitch, n.abs_tick, n.duration_ticks, n.velocity, n.channel)
        for n in notes
    ]

    for n in notes:
        ctx.model.remove_note_at(n.track_name, n.pitch, n.abs_tick)

    count = 0
    for track_name, pitch, tick, dur, vel, ch in note_data:
        new_pitch = pitch + semitones
        if 0 <= new_pitch <= 127:
            ctx.model.add_note(track_name, new_pitch, tick, dur, vel, ch)
            count += 1

    ctx.note_index.rebuild(ctx.model)
    direction = "up" if semitones > 0 else "down"
    return format_result(True, f"Transposed {count} note(s) {direction} {abs(semitones)} semitones")


# ---------------------------------------------------------------------------
# velocity
# ---------------------------------------------------------------------------

def op_velocity(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    delta_str = op.target
    if not delta_str:
        return format_result(False, "Missing velocity delta", "velocity @track:Piano +10")

    try:
        delta = int(delta_str)
    except ValueError:
        return format_result(False, f"Invalid velocity delta: {delta_str!r}")

    # Collect, remove, re-add with new velocity
    note_data = [
        (n.track_name, n.pitch, n.abs_tick, n.duration_ticks, n.velocity, n.channel)
        for n in notes
    ]

    for n in notes:
        ctx.model.remove_note_at(n.track_name, n.pitch, n.abs_tick)

    for track_name, pitch, tick, dur, vel, ch in note_data:
        new_vel = max(1, min(127, vel + delta))
        ctx.model.add_note(track_name, pitch, tick, dur, new_vel, ch)

    ctx.note_index.rebuild(ctx.model)
    return format_result(True, f"Adjusted velocity of {len(notes)} note(s) by {delta:+d}")


# ---------------------------------------------------------------------------
# quantize
# ---------------------------------------------------------------------------

def op_quantize(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    grid_str = op.params.get("grid", "quarter")
    try:
        grid_ticks = parse_duration(grid_str, ctx.model.ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid grid: {e}")

    # Collect, remove, re-add at quantized positions
    note_data = [
        (n.track_name, n.pitch, n.abs_tick, n.duration_ticks, n.velocity, n.channel)
        for n in notes
    ]

    for n in notes:
        ctx.model.remove_note_at(n.track_name, n.pitch, n.abs_tick)

    for track_name, pitch, tick, dur, vel, ch in note_data:
        new_tick = round(tick / grid_ticks) * grid_ticks
        ctx.model.add_note(track_name, pitch, new_tick, dur, vel, ch)

    ctx.note_index.rebuild(ctx.model)
    return format_result(True, f"Quantized {len(notes)} note(s) to {grid_str}")


# ---------------------------------------------------------------------------
# modify
# ---------------------------------------------------------------------------

def op_modify(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    mod_keys = {"pitch", "vel", "dur", "at", "ch"}
    has_mod = any(k in op.params for k in mod_keys)
    if not has_mod:
        return format_result(False, "No modification specified")

    # Parse new values upfront to fail early
    new_pitch_midi: int | None = None
    new_vel: int | None = None
    new_dur: int | None = None
    new_tick: int | None = None
    new_ch: int | None = None

    if "pitch" in op.params:
        try:
            p = parse_pitch(op.params["pitch"])
            new_pitch_midi = p.midi_number
        except ValueError as e:
            return format_result(False, f"Invalid pitch: {e}")

    if "vel" in op.params:
        try:
            new_vel = parse_velocity(op.params["vel"])
        except ValueError as e:
            return format_result(False, f"Invalid velocity: {e}")

    if "dur" in op.params:
        try:
            new_dur = parse_duration(op.params["dur"], ctx.model.ppqn)
        except ValueError as e:
            return format_result(False, f"Invalid duration: {e}")

    if "at" in op.params:
        time_sigs = get_time_sigs(ctx.model)
        try:
            new_tick = parse_position(
                op.params["at"], time_sigs, ctx.model.ppqn,
                reference_tick=ctx.last_tick, song_end_tick=max_tick(ctx.model),
            )
        except ValueError as e:
            return format_result(False, f"Invalid position: {e}")

    if "ch" in op.params:
        new_ch = int(op.params["ch"]) - 1

    # Collect, remove, re-add modified
    note_data = [
        (n.track_name, n.pitch, n.abs_tick, n.duration_ticks, n.velocity, n.channel)
        for n in notes
    ]

    for n in notes:
        ctx.model.remove_note_at(n.track_name, n.pitch, n.abs_tick)

    count = 0
    for track_name, pitch, tick, dur, vel, ch in note_data:
        ctx.model.add_note(
            track_name,
            new_pitch_midi if new_pitch_midi is not None else pitch,
            new_tick if new_tick is not None else tick,
            new_dur if new_dur is not None else dur,
            new_vel if new_vel is not None else vel,
            new_ch if new_ch is not None else ch,
        )
        count += 1

    ctx.note_index.rebuild(ctx.model)
    return format_result(True, f"Modified {count} note(s)")


# ---------------------------------------------------------------------------
# repeat
# ---------------------------------------------------------------------------

def op_repeat(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    count_str = op.params.get("count", "1")
    try:
        count = int(count_str)
    except ValueError:
        return format_result(False, f"Invalid count: {count_str!r}")

    min_tick_val = min(n.abs_tick for n in notes)
    max_end = max(n.abs_tick + n.duration_ticks for n in notes)
    span = max_end - min_tick_val

    to_str = op.params.get("to")
    if to_str:
        time_sigs = get_time_sigs(ctx.model)
        try:
            start_tick = parse_position(
                to_str, time_sigs, ctx.model.ppqn,
                reference_tick=ctx.last_tick, song_end_tick=max_tick(ctx.model),
            )
        except ValueError as e:
            return format_result(False, f"Invalid position: {e}")
    else:
        start_tick = max_end

    base_offset = start_tick - min_tick_val
    added = 0
    for i in range(count):
        current_offset = base_offset + i * span
        for n in notes:
            new_tick = n.abs_tick + current_offset
            ctx.model.add_note(
                n.track_name, n.pitch, new_tick,
                n.duration_ticks, n.velocity, n.channel,
            )
            added += 1

    ctx.note_index.rebuild(ctx.model)
    return format_result(True, f"Repeated {len(notes)} note(s) x{count} ({added} added)")


# ---------------------------------------------------------------------------
# crescendo / decrescendo
# ---------------------------------------------------------------------------

def op_crescendo(op: ParsedOp, ctx: MidiOpContext) -> str:
    notes = resolve_notes(op.selectors, ctx)
    if isinstance(notes, str):
        return notes
    if not notes:
        return format_result(False, "No notes matched selectors")

    from_str = op.params.get("from")
    to_str = op.params.get("to")
    if not from_str or not to_str:
        return format_result(
            False, "Missing from: and/or to: parameter",
            f"{op.verb} @track:NAME @range:M.B-M.B from:mp to:ff",
        )

    try:
        from_vel = parse_velocity(from_str)
    except ValueError as e:
        return format_result(False, f"Invalid from velocity: {e}")

    try:
        to_vel = parse_velocity(to_str)
    except ValueError as e:
        return format_result(False, f"Invalid to velocity: {e}")

    sorted_notes = sorted(notes, key=lambda n: n.abs_tick)
    n = len(sorted_notes)

    # Collect, remove, re-add with graduated velocity
    note_data = []
    for i, note in enumerate(sorted_notes):
        if n == 1:
            new_vel = to_vel
        else:
            new_vel = round(from_vel + (to_vel - from_vel) * i / (n - 1))
        new_vel = max(1, min(127, new_vel))
        note_data.append(
            (note.track_name, note.pitch, note.abs_tick,
             note.duration_ticks, new_vel, note.channel)
        )

    for note in sorted_notes:
        ctx.model.remove_note_at(note.track_name, note.pitch, note.abs_tick)

    for track_name, pitch, tick, dur, vel, ch in note_data:
        ctx.model.add_note(track_name, pitch, tick, dur, vel, ch)

    ctx.note_index.rebuild(ctx.model)
    return format_result(
        True,
        f"{op.verb.capitalize()} applied to {n} note(s) ({from_str} -> {to_str})",
    )
