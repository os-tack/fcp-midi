"""Small shared value types with no dependency on either the v1 Song model
or the v2 MidiModel — used by the parser and by timing conversions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Pitch:
    name: str  # "C", "D", ..., "B"
    accidental: str  # "", "#", "b", "##", "bb"
    octave: int  # 4 = middle C octave
    midi_number: int  # 0-127, computed from name+accidental+octave


@dataclass
class TempoChange:
    absolute_tick: int
    bpm: float


@dataclass
class TimeSignature:
    absolute_tick: int
    numerator: int
    denominator: int  # actual value (4, not power-of-2)
