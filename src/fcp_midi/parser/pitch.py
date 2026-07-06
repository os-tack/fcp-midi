"""Pitch parser — converts string representations to Pitch dataclass instances.

Supported formats
-----------------
- Note name with octave: ``C4``, ``D#5``, ``Bb3``, ``F##4``
- MIDI number: ``midi:60``

Middle C = C4 = MIDI 60.
"""

from __future__ import annotations

import re

from fcp_midi.errors import ValidationError
from fcp_midi.model.types import Pitch

# Semitone offsets for natural notes (C-based)
_NOTE_OFFSETS: dict[str, int] = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}

# Accidental offsets
_ACCIDENTAL_OFFSETS: dict[str, int] = {
    "": 0,
    "#": 1,
    "b": -1,
    "##": 2,
    "bb": -2,
}

# Regex: note name (A-G), optional accidental (##, bb, #, b), octave (possibly negative)
_PITCH_RE = re.compile(r"^([A-Ga-g])(##|bb|#|b)?(-?\d+)$")

# Reverse lookup: MIDI number mod 12 → (name, accidental)
# Prefer sharps for black keys
_MIDI_TO_NOTE: list[tuple[str, str]] = [
    ("C", ""),
    ("C", "#"),
    ("D", ""),
    ("D", "#"),
    ("E", ""),
    ("F", ""),
    ("F", "#"),
    ("G", ""),
    ("G", "#"),
    ("A", ""),
    ("A", "#"),
    ("B", ""),
]


def parse_pitch(s: str) -> Pitch:
    """Parse a pitch string into a :class:`Pitch` instance.

    Parameters
    ----------
    s : str
        One of: ``"C4"``, ``"D#5"``, ``"Bb3"``, ``"F##4"``, ``"midi:60"``

    Returns
    -------
    Pitch

    Raises
    ------
    ValueError
        If the string cannot be parsed as a valid pitch.
    """
    # Handle midi:N format
    if s.startswith("midi:"):
        try:
            midi_number = int(s[5:])
        except ValueError:
            raise ValidationError(f"Invalid MIDI number in '{s}'")
        if midi_number < 0 or midi_number > 127:
            raise ValidationError(
                f"MIDI number {midi_number} out of range (0-127)"
            )
        return _pitch_from_midi(midi_number)

    # Handle note-name format
    m = _PITCH_RE.match(s)
    if not m:
        raise ValidationError(f"Cannot parse pitch: '{s}'")

    name = m.group(1).upper()
    accidental = m.group(2) or ""
    octave = int(m.group(3))

    note_offset = _NOTE_OFFSETS.get(name)
    if note_offset is None:
        raise ValidationError(f"Invalid note name: '{name}'")
    acc_offset = _ACCIDENTAL_OFFSETS.get(accidental)
    if acc_offset is None:
        raise ValidationError(f"Invalid accidental: '{accidental}'")

    midi_number = (octave + 1) * 12 + note_offset + acc_offset

    if midi_number < 0 or midi_number > 127:
        raise ValidationError(
            f"Computed MIDI number {midi_number} out of range (0-127) "
            f"for pitch '{s}'"
        )

    return Pitch(
        name=name,
        accidental=accidental,
        octave=octave,
        midi_number=midi_number,
    )


def _pitch_from_midi(midi_number: int) -> Pitch:
    """Create a Pitch from a raw MIDI number, using sharps for black keys."""
    note_in_octave = midi_number % 12
    octave = (midi_number // 12) - 1
    name, accidental = _MIDI_TO_NOTE[note_in_octave]
    return Pitch(
        name=name,
        accidental=accidental,
        octave=octave,
        midi_number=midi_number,
    )
