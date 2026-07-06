"""Timing conversions between ticks, measure.beat positions, and seconds.

All public helpers accept sorted lists of ``TimeSignature`` / ``TempoChange``
from the Song model.
"""

from __future__ import annotations

from fcp_midi.model.types import TempoChange, TimeSignature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ticks_per_beat(ppqn: int, denominator: int) -> int:
    """Ticks for one beat given *denominator*.

    For 4/4 a beat is a quarter note => ppqn.
    For 6/8 a beat is an eighth note  => ppqn // 2.
    General: ppqn * 4 // denominator.
    """
    return ppqn * 4 // denominator


def _ticks_per_measure(ppqn: int, numerator: int, denominator: int) -> int:
    """Total ticks in one measure with the given time signature."""
    return numerator * ticks_per_beat(ppqn, denominator)


# ---------------------------------------------------------------------------
# Position <-> Ticks
# ---------------------------------------------------------------------------

def position_to_ticks(
    position: str,
    time_sigs: list[TimeSignature],
    ppqn: int,
) -> int:
    """Convert a human position string to absolute ticks.

    Accepted formats:
    - ``"M.B"``     — 1-based measure and beat  (e.g. ``"2.3"``)
    - ``"M.B.T"``   — with a tick subdivision    (e.g. ``"1.1.120"``)
    - ``"tick:N"``  — raw absolute tick          (e.g. ``"tick:960"``)
    """
    # Raw tick shortcut
    if position.startswith("tick:"):
        return int(position[5:])

    parts = position.split(".")
    measure_1 = int(parts[0])   # 1-based
    beat_1 = int(parts[1])      # 1-based
    sub_ticks = int(parts[2]) if len(parts) >= 3 else 0

    # Walk through time signatures, accumulating ticks measure-by-measure
    # until we reach the target measure.
    target_measure_0 = measure_1 - 1  # 0-based measure index

    # Build a sorted list of (start_tick, numerator, denominator, start_measure_0)
    sigs = sorted(time_sigs, key=lambda ts: ts.absolute_tick)
    if not sigs:
        sigs = [TimeSignature(absolute_tick=0, numerator=4, denominator=4)]

    accumulated_tick = 0
    current_measure_0 = 0

    for i, sig in enumerate(sigs):
        # Determine number of measures under this signature before the next one
        next_tick = sigs[i + 1].absolute_tick if i + 1 < len(sigs) else None
        tpm = _ticks_per_measure(ppqn, sig.numerator, sig.denominator)

        if next_tick is not None:
            measures_in_region = (next_tick - sig.absolute_tick) // tpm
        else:
            # Last signature — extends to infinity
            measures_in_region = None  # unbounded

        if measures_in_region is not None and current_measure_0 + measures_in_region <= target_measure_0:
            # Target is beyond this region
            accumulated_tick = sig.absolute_tick + measures_in_region * tpm
            current_measure_0 += measures_in_region
        else:
            # Target falls inside this region
            measures_needed = target_measure_0 - current_measure_0
            accumulated_tick = sig.absolute_tick + measures_needed * tpm
            # Now add the beat offset (1-based)
            beat_tick = ticks_per_beat(ppqn, sig.denominator)
            accumulated_tick += (beat_1 - 1) * beat_tick + sub_ticks
            return accumulated_tick

    # Fallback (should not normally be reached)
    return accumulated_tick


def ticks_to_position(
    tick: int,
    time_sigs: list[TimeSignature],
    ppqn: int,
) -> str:
    """Convert an absolute tick to a ``"M.B"`` position string (1-based)."""
    sigs = sorted(time_sigs, key=lambda ts: ts.absolute_tick)
    if not sigs:
        sigs = [TimeSignature(absolute_tick=0, numerator=4, denominator=4)]

    current_measure_0 = 0

    for i, sig in enumerate(sigs):
        next_tick = sigs[i + 1].absolute_tick if i + 1 < len(sigs) else None
        tpm = _ticks_per_measure(ppqn, sig.numerator, sig.denominator)

        region_start = sig.absolute_tick
        if next_tick is not None and tick >= next_tick:
            # Tick is beyond this region
            measures_in_region = (next_tick - region_start) // tpm
            current_measure_0 += measures_in_region
            continue

        # Tick falls within this region
        offset = tick - region_start
        measures_here = offset // tpm
        remainder = offset % tpm
        beat_tick = ticks_per_beat(ppqn, sig.denominator)
        beat_0 = remainder // beat_tick
        sub = remainder % beat_tick

        measure_1 = current_measure_0 + measures_here + 1
        beat_1 = beat_0 + 1

        if sub:
            return f"{measure_1}.{beat_1}.{sub}"
        return f"{measure_1}.{beat_1}"

    # Fallback
    return "1.1"


# ---------------------------------------------------------------------------
# Ticks <-> Seconds
# ---------------------------------------------------------------------------

def ticks_to_seconds(
    tick: int,
    tempo_map: list[TempoChange],
    ppqn: int,
) -> float:
    """Convert absolute *tick* to seconds using tempo map."""
    tempos = sorted(tempo_map, key=lambda t: t.absolute_tick)
    if not tempos:
        tempos = [TempoChange(absolute_tick=0, bpm=120.0)]

    seconds = 0.0

    for i, tc in enumerate(tempos):
        next_tick = tempos[i + 1].absolute_tick if i + 1 < len(tempos) else None
        secs_per_tick = 60.0 / (tc.bpm * ppqn)

        if next_tick is not None and tick >= next_tick:
            # Entire region before the next tempo change
            seconds += (next_tick - tc.absolute_tick) * secs_per_tick
        else:
            # Tick falls in this region (or this is the last tempo entry)
            seconds += (tick - tc.absolute_tick) * secs_per_tick
            return seconds

    return seconds


def seconds_to_ticks(
    seconds: float,
    tempo_map: list[TempoChange],
    ppqn: int,
) -> int:
    """Convert *seconds* to the nearest absolute tick using tempo map."""
    tempos = sorted(tempo_map, key=lambda t: t.absolute_tick)
    if not tempos:
        tempos = [TempoChange(absolute_tick=0, bpm=120.0)]

    remaining = seconds

    for i, tc in enumerate(tempos):
        secs_per_tick = 60.0 / (tc.bpm * ppqn)
        next_tick = tempos[i + 1].absolute_tick if i + 1 < len(tempos) else None

        if next_tick is not None:
            region_duration = (next_tick - tc.absolute_tick) * secs_per_tick
            if remaining > region_duration:
                remaining -= region_duration
                continue

        # This region covers the remaining seconds
        ticks_in_region = remaining / secs_per_tick
        return round(tc.absolute_tick + ticks_in_region)

    # Fallback
    return 0
