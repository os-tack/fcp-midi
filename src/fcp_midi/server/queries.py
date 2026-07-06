"""Query handlers — read-only operations walking mido tracks directly."""

from __future__ import annotations

import mido

from fcp_midi.model.midi_model import (
    MidiModel,
    NoteIndex,
    NoteRef,
    delta_to_absolute,
    pair_notes,
)
from fcp_midi.model.timing import ticks_to_position
from fcp_midi.parser.pitch import parse_pitch, _MIDI_TO_NOTE
from fcp_midi.parser.position import parse_position, _ticks_per_beat
from fcp_midi.server.formatter import format_result
from fcp_midi.server.ops_context import (
    MidiOpContext,
    get_tempo_bpm,
    get_time_sigs,
    max_tick,
    suggest_track_name,
    display_channel,
)


def dispatch_query(q: str, ctx: MidiOpContext) -> str:
    """Route a query string to the appropriate v2 handler."""
    q = q.strip()
    parts = q.split(None, 1)
    command = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    if command == "map":
        return _query_map(ctx)
    elif command == "tracks":
        return _query_tracks(ctx)
    elif command == "events":
        return _query_events(args, ctx)
    elif command == "describe":
        return _query_describe(args, ctx)
    elif command == "stats":
        return _query_stats(ctx)
    elif command == "status":
        return _query_status(ctx)
    elif command == "find":
        return _query_find(args, ctx)
    elif command in ("tracker", "piano-roll"):
        return _query_tracker(args, ctx)
    elif command == "history":
        return _query_history(args, ctx)
    elif command == "instruments":
        return _query_instruments(args, ctx)
    else:
        return (
            f"! Unknown query: {command!r}\n"
            "  try: map, tracks, events, describe, stats, find, tracker, history, instruments"
        )


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

def _query_map(ctx: MidiOpContext) -> str:
    model = ctx.model
    lines: list[str] = [f"Song: {model.title}"]

    tempo = get_tempo_bpm(model)
    time_sigs = get_time_sigs(model)
    ts = time_sigs[0] if time_sigs else None
    ts_str = f"{ts.numerator}/{ts.denominator}" if ts else "4/4"

    # Key signature from conductor
    ks_str = ""
    if model.file.tracks:
        for msg in model.file.tracks[0]:
            if msg.type == "key_signature":
                ks_str = msg.key
                break

    meta_parts = [f"tempo:{tempo:.0f}", ts_str]
    if ks_str:
        meta_parts.append(ks_str)
    meta_parts.append(f"ppqn:{model.ppqn}")
    lines.append(f"  {' | '.join(meta_parts)}")

    # Markers from conductor
    markers = _extract_markers(model)
    if markers:
        markers_str = ", ".join(
            f"{text}@{ticks_to_position(tick, time_sigs, model.ppqn)}"
            for tick, text in markers
        )
        lines.append(f"  Markers: {markers_str}")

    # Track summaries
    if model.tracks:
        lines.append(f"  Tracks ({len(model.tracks)}):")
        for i, name in enumerate(model.track_order, 1):
            ref = model.tracks.get(name)
            if not ref:
                continue
            notes = pair_notes(ref.track, track_name=name)
            n_notes = len(notes)
            inst = _instrument_name(ref.program) or "---"
            flags = ""
            if ref.mute:
                flags += " [M]"
            if ref.solo:
                flags += " [S]"
            lines.append(
                f"    {i}. {name} ch:{display_channel(ref.channel)} {inst} ({n_notes} notes){flags}"
            )
    else:
        lines.append("  No tracks.")

    lines.append(f"  {model.get_digest()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# tracks
# ---------------------------------------------------------------------------

def _query_tracks(ctx: MidiOpContext) -> str:
    model = ctx.model
    if not model.tracks:
        return "No tracks."

    lines: list[str] = []
    for i, name in enumerate(model.track_order, 1):
        ref = model.tracks.get(name)
        if not ref:
            continue
        notes = pair_notes(ref.track, track_name=name)
        n_notes = len(notes)
        n_cc = _count_messages(ref.track, "control_change")
        n_pb = _count_messages(ref.track, "pitchwheel")
        inst = _instrument_name(ref.program) or "no instrument"
        bank_info = ""
        if ref.bank_msb or ref.bank_lsb:
            bank_info = f" bank:{ref.bank_msb}.{ref.bank_lsb}"
        flags = ""
        if ref.mute:
            flags += " [MUTED]"
        if ref.solo:
            flags += " [SOLO]"
        lines.append(
            f"  {i}. {name} (ch:{display_channel(ref.channel)}) "
            f"{inst}{bank_info} | {n_notes} notes, {n_cc} cc, {n_pb} bend{flags}"
        )

    header = f"Tracks ({len(lines)}):"
    return "\n".join([header] + lines)


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def _query_events(args: str, ctx: MidiOpContext) -> str:
    parts = args.strip().split()
    if not parts:
        return "! Missing track name.\n  try: events Piano or events Piano 1.1-4.4"

    track_name = parts[0]
    model = ctx.model
    time_sigs = get_time_sigs(model)
    ppqn = model.ppqn

    start_tick = None
    end_tick = None
    if len(parts) > 1:
        range_str = parts[1]
        range_parts = range_str.split("-")
        if len(range_parts) == 2:
            try:
                start_tick = parse_position(range_parts[0], time_sigs, ppqn)
                end_tick = parse_position(range_parts[1], time_sigs, ppqn)
                # Make end inclusive of the beat
                ts = time_sigs[0] if time_sigs else None
                denom = ts.denominator if ts else 4
                end_tick += _ticks_per_beat(denom, ppqn)
            except ValueError as e:
                return f"! Invalid range: {e}"

    if track_name in ("*", "all"):
        sections = []
        for name in model.track_order:
            ref = model.tracks.get(name)
            if ref:
                header = f"--- {name} ---"
                body = _format_track_events(ref.track, name, time_sigs, ppqn, start_tick, end_tick)
                sections.append(f"{header}\n{body}")
        return "\n".join(sections) if sections else "No tracks."

    ref = model.get_track(track_name)
    if not ref:
        suggestion = suggest_track_name(model, track_name)
        msg = f"Track '{track_name}' not found"
        if suggestion:
            msg += f"\n  try: {suggestion}"
        return f"! {msg}"

    return _format_track_events(ref.track, track_name, time_sigs, ppqn, start_tick, end_tick)


def _format_track_events(
    track: mido.MidiTrack,
    track_name: str,
    time_sigs: list,
    ppqn: int,
    start_tick: int | None = None,
    end_tick: int | None = None,
) -> str:
    """Format note/cc/bend events from a mido track."""
    lines: list[str] = []
    notes = pair_notes(track, track_name=track_name)

    for n in sorted(notes, key=lambda x: x.abs_tick):
        if start_tick is not None and n.abs_tick < start_tick:
            continue
        if end_tick is not None and n.abs_tick >= end_tick:
            continue
        pos = ticks_to_position(n.abs_tick, time_sigs, ppqn)
        pitch_str = _pitch_name(n.pitch)
        lines.append(f"  {pos}  {pitch_str:6s} vel:{n.velocity:3d} dur:{n.duration_ticks}")

    # CCs and pitch bends
    abs_tick = 0
    for msg in track:
        abs_tick += msg.time
        if start_tick is not None and abs_tick < start_tick:
            continue
        if end_tick is not None and abs_tick >= end_tick:
            continue
        if msg.type == "control_change":
            pos = ticks_to_position(abs_tick, time_sigs, ppqn)
            lines.append(f"  {pos}  CC{msg.control:3d}={msg.value:3d}")
        elif msg.type == "pitchwheel":
            pos = ticks_to_position(abs_tick, time_sigs, ppqn)
            lines.append(f"  {pos}  Bend={msg.pitch}")

    if not lines:
        return f"No events on {track_name} in range."

    header = f"Events on {track_name}:"
    return "\n".join([header] + lines)


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------

def _query_describe(args: str, ctx: MidiOpContext) -> str:
    track_name = args.strip()
    if not track_name:
        return "! Missing track name.\n  try: describe Piano"

    model = ctx.model
    ref = model.get_track(track_name)
    if not ref:
        suggestion = suggest_track_name(model, track_name)
        msg = f"Track '{track_name}' not found"
        if suggestion:
            msg += f"\n  try: {suggestion}"
        return f"! {msg}"

    time_sigs = get_time_sigs(model)
    ppqn = model.ppqn
    notes = pair_notes(ref.track, track_name=track_name)
    n_notes = len(notes)
    inst = _instrument_name(ref.program) or "none"
    prog = ref.program

    bank_str = ""
    if ref.bank_msb or ref.bank_lsb:
        bank_str = f" bank:{ref.bank_msb}.{ref.bank_lsb}"

    n_cc = _count_messages(ref.track, "control_change")
    n_pb = _count_messages(ref.track, "pitchwheel")

    lines = [
        f"Track: {track_name}",
        f"  Channel: {display_channel(ref.channel)}",
        f"  Instrument: {inst} (program:{prog}){bank_str}",
        f"  Notes: {n_notes}",
        f"  CCs: {n_cc}",
        f"  Pitch bends: {n_pb}",
        f"  Muted: {ref.mute}",
        f"  Solo: {ref.solo}",
    ]

    if notes:
        sorted_by_pitch = sorted(notes, key=lambda n: n.pitch)
        low_midi = sorted_by_pitch[0].pitch
        high_midi = sorted_by_pitch[-1].pitch
        lines.append(f"  Pitch range: {_pitch_name(low_midi)} - {_pitch_name(high_midi)}")

    if notes:
        vels = [n.velocity for n in notes]
        lines.append(f"  Velocity range: {min(vels)}-{max(vels)}")

    if notes:
        sorted_by_time = sorted(notes, key=lambda n: n.abs_tick)
        first_pos = ticks_to_position(sorted_by_time[0].abs_tick, time_sigs, ppqn)
        last_end = max(n.abs_tick + n.duration_ticks for n in notes)
        last_pos = ticks_to_position(last_end, time_sigs, ppqn)
        lines.append(f"  Time span: {first_pos} - {last_pos}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def _query_stats(ctx: MidiOpContext) -> str:
    model = ctx.model
    time_sigs = get_time_sigs(model)
    ppqn = model.ppqn

    n_tracks = len(model.tracks)
    n_notes = 0
    n_cc = 0
    n_pb = 0
    for ref in model.tracks.values():
        n_notes += len(pair_notes(ref.track))
        n_cc += _count_messages(ref.track, "control_change")
        n_pb += _count_messages(ref.track, "pitchwheel")

    total_events = n_notes + n_cc + n_pb
    tempo = get_tempo_bpm(model)
    ts = time_sigs[0] if time_sigs else None
    ts_str = f"{ts.numerator}/{ts.denominator}" if ts else "4/4"

    ks_str = "none"
    if model.file.tracks:
        for msg in model.file.tracks[0]:
            if msg.type == "key_signature":
                ks_str = msg.key
                break

    max_t = max_tick(model)

    # Duration in seconds
    duration_secs = max_t / ppqn * (60.0 / tempo) if tempo > 0 else 0
    minutes = int(duration_secs) // 60
    seconds = duration_secs - minutes * 60

    # Estimate measures
    if ts:
        tpm = ppqn * 4 * ts.numerator // ts.denominator
        measures = max_t // tpm if tpm else 0
    else:
        measures = max_t // (ppqn * 4)

    lines = [
        f"Song: {model.title}",
        f"  Tempo: {tempo:.0f} BPM",
        f"  Time sig: {ts_str}",
        f"  Key: {ks_str}",
        f"  PPQN: {ppqn}",
        f"  Tracks: {n_tracks}",
        f"  Notes: {n_notes}",
        f"  CCs: {n_cc}",
        f"  Pitch bends: {n_pb}",
        f"  Total events: {total_events}",
        f"  Duration: {minutes}:{seconds:05.2f}",
        f"  Measures: {measures}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _query_status(ctx: MidiOpContext) -> str:
    title = ctx.model.title
    n_tracks = len(ctx.model.tracks)
    n_notes = sum(
        len(pair_notes(ref.track)) for ref in ctx.model.tracks.values()
    )
    return f"Session: {title}\n  Tracks: {n_tracks}\n  Notes: {n_notes}"


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------

def _query_find(args: str, ctx: MidiOpContext) -> str:
    pitch_str = args.strip()
    if not pitch_str:
        return "! Missing pitch.\n  try: find C4"

    try:
        pitch = parse_pitch(pitch_str)
    except ValueError as e:
        return f"! Invalid pitch: {e}"

    time_sigs = get_time_sigs(ctx.model)
    ppqn = ctx.model.ppqn

    notes = ctx.note_index.by_pitch.get(pitch.midi_number, [])
    if not notes:
        return f"No notes matching {pitch_str}."

    lines = [f"Found {len(notes)} note(s) matching {pitch_str}:"]
    for note in sorted(notes, key=lambda n: n.abs_tick):
        pos = ticks_to_position(note.abs_tick, time_sigs, ppqn)
        lines.append(f"  {pos}  {note.track_name}  vel:{note.velocity}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------

def _query_tracker(args: str, ctx: MidiOpContext) -> str:
    parts = args.strip().split()
    if len(parts) < 2:
        return "! Usage: tracker TRACK M.B-M.B [res:RESOLUTION]\n  try: tracker Piano 1.1-8.4"

    track_name = parts[0]
    model = ctx.model
    time_sigs = get_time_sigs(model)
    ppqn = model.ppqn

    range_str = parts[1]
    range_parts = range_str.split("-")
    if len(range_parts) != 2:
        return "! Invalid range format.\n  try: tracker Piano 1.1-8.4"

    try:
        start_tick = parse_position(range_parts[0], time_sigs, ppqn)
        end_tick = parse_position(range_parts[1], time_sigs, ppqn)
    except ValueError as e:
        return f"! Invalid range: {e}"

    # Parse optional res: parameter
    resolution: str | None = None
    for p in parts[2:]:
        if p.startswith("res:"):
            resolution = p[4:]

    # --- Multi-track path: * or comma-separated names ---
    if track_name == "*" or "," in track_name:
        if track_name == "*":
            names = list(model.track_order)
        else:
            names = [n.strip() for n in track_name.split(",") if n.strip()]

        # Resolve each track
        track_data: list[tuple[str, list, bool]] = []
        missing: list[str] = []
        for tname in names:
            ref = model.get_track(tname)
            if not ref:
                missing.append(tname)
                continue
            notes = pair_notes(ref.track, track_name=tname)
            is_drum = ref.channel == 9
            track_data.append((tname, notes, is_drum))

        if missing and not track_data:
            return f"! No matching tracks found. Missing: {', '.join(missing)}"

        if not track_data:
            return "! No tracks to display."

        from fcp_midi.server.tracker_format import format_tracker_multi
        result = format_tracker_multi(
            track_data, time_sigs, ppqn, start_tick, end_tick, resolution,
        )
        if missing:
            result += f"\n! Tracks not found: {', '.join(missing)}"
        return result

    # --- Single-track path (unchanged) ---
    ref = model.get_track(track_name)
    if not ref:
        suggestion = suggest_track_name(model, track_name)
        msg = f"Track '{track_name}' not found"
        if suggestion:
            msg += f"\n  try: {suggestion}"
        return f"! {msg}"

    notes = pair_notes(ref.track, track_name=track_name)

    # Look up instrument name for header display
    if ref.channel == 9:
        instrument = "drums"
    else:
        instrument = _instrument_name(ref.program)

    from fcp_midi.server.tracker_format import format_tracker
    return format_tracker(notes, track_name, time_sigs, ppqn, start_tick, end_tick, resolution, instrument=instrument)


# ---------------------------------------------------------------------------
# history (simple — no EventLog dependency)
# ---------------------------------------------------------------------------

def _query_history(args: str, ctx: MidiOpContext) -> str:
    """Show recent operations. In the v2 architecture, history is tracked
    externally by the adapter/session. This returns a placeholder."""
    return "History not available in v2 model (managed by session layer)."


# ---------------------------------------------------------------------------
# instruments
# ---------------------------------------------------------------------------

def _query_instruments(args: str, ctx: MidiOpContext) -> str:
    if ctx.instrument_registry is None:
        return "! Instrument registry not available."

    filter_str = args.strip().lower() if args.strip() else None
    source_filter = None

    if filter_str and filter_str.startswith("source:"):
        source_filter = filter_str[7:]
        filter_str = None

    specs = ctx.instrument_registry.list_instruments(source=source_filter)
    if filter_str:
        specs = [s for s in specs if filter_str in s.name]

    if not specs:
        return "No instruments found."

    lines = [f"Instruments ({len(specs)}):"]
    for s in specs:
        bank_str = ""
        if s.bank_msb != 0 or s.bank_lsb != 0:
            bank_str = f" bank:{s.bank_msb}.{s.bank_lsb}"
        source_str = f" [{s.source}]" if s.source != "gm" else ""
        lines.append(f"  {s.name}  program:{s.program}{bank_str}{source_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pitch_name(midi_number: int) -> str:
    """Convert MIDI number to a pitch name like C4, F#3."""
    note_in_oct = midi_number % 12
    octave = (midi_number // 12) - 1
    name, acc = _MIDI_TO_NOTE[note_in_oct]
    return f"{name}{acc}{octave}"


def _instrument_name(program: int) -> str | None:
    """Look up GM instrument name. Returns None if not found."""
    try:
        from fcp_midi.lib.gm_instruments import program_to_instrument
        return program_to_instrument(program)
    except (ImportError, KeyError):
        return None


def _count_messages(track: mido.MidiTrack, msg_type: str) -> int:
    """Count messages of a given type in a track."""
    return sum(1 for msg in track if msg.type == msg_type)


def _extract_markers(model: MidiModel) -> list[tuple[int, str]]:
    """Extract markers from the conductor track as (abs_tick, text) pairs."""
    markers: list[tuple[int, str]] = []
    if not model.file.tracks:
        return markers
    abs_tick = 0
    for msg in model.file.tracks[0]:
        abs_tick += msg.time
        if msg.type == "marker":
            markers.append((abs_tick, msg.text))
    markers.sort(key=lambda m: m[0])
    return markers
