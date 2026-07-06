"""Chord parser — converts chord symbols to lists of Pitch instances.

Supports major, minor, seventh, diminished, augmented, sus, add9,
slash chords (inversions), and more.
"""

from __future__ import annotations

import re

from fcp_midi.errors import ValidationError
from fcp_midi.model.types import Pitch
from fcp_midi.parser.pitch import _NOTE_OFFSETS, _MIDI_TO_NOTE

# Chord quality → list of semitone intervals from root
_CHORD_INTERVALS: dict[str, list[int]] = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
    "m": [0, 3, 7],
    "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "min7": [0, 3, 7, 10],
    "m7": [0, 3, 7, 10],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "add9": [0, 4, 7, 14],
    "min7b5": [0, 3, 6, 10],
    "m7b5": [0, 3, 6, 10],
    "dim7": [0, 3, 6, 9],
    "9": [0, 4, 7, 10, 14],
    "min9": [0, 3, 7, 10, 14],
    "m9": [0, 3, 7, 10, 14],
    "6": [0, 4, 7, 9],
    "min6": [0, 3, 7, 9],
    "m6": [0, 3, 7, 9],
}

# Root-note regex: note letter + optional accidental
_ROOT_RE = re.compile(r"^([A-G])(#|b)?")

# For parsing root notes we need the full note list
_ALL_ROOTS: list[str] = [
    "C", "C#", "Db", "D", "D#", "Eb", "E", "F",
    "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B",
]

# Map root name to semitone offset from C
_ROOT_TO_SEMITONE: dict[str, int] = {
    "C": 0, "C#": 1, "Db": 1,
    "D": 2, "D#": 3, "Eb": 3,
    "E": 4,
    "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10,
    "B": 11,
}


def parse_chord(s: str, octave: int = 4) -> list[Pitch]:
    """Parse a chord symbol and return a list of :class:`Pitch` instances.

    Parameters
    ----------
    s : str
        Chord symbol, e.g. ``"Cmaj"``, ``"Am7"``, ``"Dm/F"``.
    octave : int
        Default octave for the root note (default 4).

    Returns
    -------
    list[Pitch]

    Raises
    ------
    ValueError
        If the chord cannot be parsed.
    """
    # Split slash chord
    slash_bass: str | None = None
    if "/" in s:
        chord_part, slash_part = s.rsplit("/", 1)
        slash_bass = slash_part
        s = chord_part

    # Extract root note
    root_name, root_accidental, remainder = _extract_root(s)
    root_semitone = _ROOT_TO_SEMITONE[root_name + root_accidental]

    # Determine quality from remainder
    quality = _match_quality(remainder)
    intervals = _CHORD_INTERVALS[quality]

    # Build pitches
    root_midi = (octave + 1) * 12 + root_semitone
    pitches: list[Pitch] = []
    for interval in intervals:
        midi_num = root_midi + interval
        pitches.append(_pitch_from_midi(midi_num))

    # Handle slash bass
    if slash_bass is not None:
        bass_name, bass_accidental, bass_remainder = _extract_root(slash_bass)
        if bass_remainder:
            raise ValidationError(f"Invalid bass note in slash chord: '{slash_bass}'")
        bass_semitone = _ROOT_TO_SEMITONE[bass_name + bass_accidental]
        # Place bass note below the root
        bass_midi = (octave + 1) * 12 + bass_semitone
        if bass_midi >= root_midi:
            bass_midi -= 12
        # Remove any existing note with the same pitch class from the chord
        bass_pc = bass_midi % 12
        pitches = [p for p in pitches if p.midi_number % 12 != bass_pc]
        # Insert bass at the front
        pitches.insert(0, _pitch_from_midi(bass_midi))

    return pitches


def _extract_root(s: str) -> tuple[str, str, str]:
    """Extract root note name, accidental, and the remaining quality string."""
    if len(s) < 1:
        raise ValidationError("Empty chord string")

    name = s[0].upper()
    if name not in _NOTE_OFFSETS:
        raise ValidationError(f"Invalid root note: '{s[0]}'")

    pos = 1
    accidental = ""
    if pos < len(s) and s[pos] in ("#", "b"):
        accidental = s[pos]
        pos += 1

    return name, accidental, s[pos:]


def _match_quality(remainder: str) -> str:
    """Match the quality string to a known chord type.

    Tries longest match first to handle e.g. ``"min7b5"`` before ``"min7"``
    before ``"min"``.
    """
    if not remainder:
        # Default to major
        return "maj"

    # Sort candidate qualities by length (longest first) for greedy matching
    candidates = sorted(_CHORD_INTERVALS.keys(), key=len, reverse=True)
    for quality in candidates:
        if remainder == quality:
            return quality

    # Also try some common synonyms
    if remainder in ("minor", "amin"):
        return "min"

    raise ValidationError(f"Unknown chord quality: '{remainder}'")


def _pitch_from_midi(midi_number: int) -> Pitch:
    """Create a Pitch from a raw MIDI number."""
    note_in_octave = midi_number % 12
    octave = (midi_number // 12) - 1
    name, accidental = _MIDI_TO_NOTE[note_in_octave]
    return Pitch(
        name=name,
        accidental=accidental,
        octave=octave,
        midi_number=midi_number,
    )
