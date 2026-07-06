"""Op handlers for metadata: tempo, time-sig, key-sig, marker, title.

These operate directly on the MidiModel conductor track (track 0)
via mido messages, replacing the Song-based event-sourcing approach.
"""

from __future__ import annotations

import re

import mido

from fcp_midi.model.midi_model import (
    MidiModel,
    delta_to_absolute,
    insert_message_at_tick,
    parse_key_signature,
    remove_message_at_index,
    to_mido_key,
)
from fcp_midi.model.timing import ticks_to_position
from fcp_midi.parser.ops import ParsedOp
from fcp_midi.parser.position import parse_position
from fcp_midi.server.formatter import format_result
from fcp_midi.server.ops_context import MidiOpContext, get_time_sigs


def op_tempo(op: ParsedOp, ctx: MidiOpContext) -> str:
    bpm_str = op.target
    if not bpm_str:
        return format_result(False, "Missing BPM value", "tempo 120")

    try:
        bpm = float(bpm_str)
    except ValueError:
        return format_result(False, f"Invalid BPM: {bpm_str!r}")

    time_sigs = get_time_sigs(ctx.model)
    at_str = op.params.get("at", "1.1")
    try:
        tick = parse_position(at_str, time_sigs, ctx.model.ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    conductor = ctx.model.file.tracks[0]

    # If at tick 0, replace the existing set_tempo; otherwise insert new
    if tick == 0:
        for i, msg in enumerate(conductor):
            if msg.type == "set_tempo":
                conductor[i] = mido.MetaMessage(
                    "set_tempo", tempo=mido.bpm2tempo(bpm), time=msg.time
                )
                break
        else:
            insert_message_at_tick(
                conductor,
                mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)),
                tick,
            )
    else:
        insert_message_at_tick(
            conductor,
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)),
            tick,
        )

    pos = ticks_to_position(tick, time_sigs, ctx.model.ppqn)
    return format_result(True, f"Tempo {bpm:.0f} BPM at {pos}")


def op_time_sig(op: ParsedOp, ctx: MidiOpContext) -> str:
    ts_str = op.target
    if not ts_str:
        return format_result(False, "Missing time signature", "time-sig 3/4")

    match = re.match(r"^(\d+)/(\d+)$", ts_str)
    if not match:
        return format_result(False, f"Invalid time signature: {ts_str!r}", "time-sig 3/4")

    num = int(match.group(1))
    denom = int(match.group(2))

    time_sigs = get_time_sigs(ctx.model)
    at_str = op.params.get("at", "1.1")
    try:
        tick = parse_position(at_str, time_sigs, ctx.model.ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    conductor = ctx.model.file.tracks[0]

    # If at tick 0, replace the existing time_signature
    if tick == 0:
        for i, msg in enumerate(conductor):
            if msg.type == "time_signature":
                conductor[i] = mido.MetaMessage(
                    "time_signature",
                    numerator=num,
                    denominator=denom,
                    clocks_per_click=24,
                    notated_32nd_notes_per_beat=8,
                    time=msg.time,
                )
                break
        else:
            insert_message_at_tick(
                conductor,
                mido.MetaMessage(
                    "time_signature",
                    numerator=num,
                    denominator=denom,
                    clocks_per_click=24,
                    notated_32nd_notes_per_beat=8,
                ),
                tick,
            )
    else:
        insert_message_at_tick(
            conductor,
            mido.MetaMessage(
                "time_signature",
                numerator=num,
                denominator=denom,
                clocks_per_click=24,
                notated_32nd_notes_per_beat=8,
            ),
            tick,
        )

    return format_result(True, f"Time signature {num}/{denom}")


def op_key_sig(op: ParsedOp, ctx: MidiOpContext) -> str:
    ks_str = op.target
    if not ks_str:
        return format_result(False, "Missing key signature", "key-sig C-major")

    # Parse key and mode from the input string ("C-major", "G-minor", "Dm")
    # and convert to mido's compact key_signature format ("C", "Am", "Bbm").
    key, mode = parse_key_signature(ks_str)
    mido_key = to_mido_key(key, mode)

    time_sigs = get_time_sigs(ctx.model)
    at_str = op.params.get("at", "1.1")
    try:
        tick = parse_position(at_str, time_sigs, ctx.model.ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    conductor = ctx.model.file.tracks[0]

    # If at tick 0, replace existing key_signature if present
    if tick == 0:
        replaced = False
        for i, msg in enumerate(conductor):
            if msg.type == "key_signature":
                conductor[i] = mido.MetaMessage(
                    "key_signature", key=mido_key, time=msg.time
                )
                replaced = True
                break
        if not replaced:
            insert_message_at_tick(
                conductor,
                mido.MetaMessage("key_signature", key=mido_key),
                tick,
            )
    else:
        insert_message_at_tick(
            conductor,
            mido.MetaMessage("key_signature", key=mido_key),
            tick,
        )

    return format_result(True, f"Key signature {key} {mode}")


def op_marker(op: ParsedOp, ctx: MidiOpContext) -> str:
    text = op.target
    if not text:
        return format_result(False, "Missing marker text", 'marker "Chorus" at:5.1')

    time_sigs = get_time_sigs(ctx.model)
    at_str = op.params.get("at", "1.1")
    try:
        tick = parse_position(at_str, time_sigs, ctx.model.ppqn)
    except ValueError as e:
        return format_result(False, f"Invalid position: {e}")

    conductor = ctx.model.file.tracks[0]
    insert_message_at_tick(
        conductor,
        mido.MetaMessage("marker", text=text),
        tick,
    )

    pos = ticks_to_position(tick, time_sigs, ctx.model.ppqn)
    return format_result(True, f"Marker '{text}' at {pos}")


def op_title(op: ParsedOp, ctx: MidiOpContext) -> str:
    title = op.target
    if not title:
        return format_result(False, "Missing title", 'title "My Song"')

    conductor = ctx.model.file.tracks[0]

    # Replace existing track_name in conductor
    for i, msg in enumerate(conductor):
        if msg.type == "track_name":
            conductor[i] = mido.MetaMessage(
                "track_name", name=title, time=msg.time
            )
            break

    ctx.model.title = title
    return format_result(True, f"Title set to '{title}'")
