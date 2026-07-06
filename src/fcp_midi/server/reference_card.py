"""MIDI FCP reference card — generated from verb registry + static sections.

Uses ``VerbRegistry`` from fcp_core for structured verb management,
with domain-specific static sections for MIDI reference.
"""

from __future__ import annotations

from fcp_core import VerbRegistry

from fcp_midi.server.verb_registry import VERBS


# Build a VerbRegistry instance from the MIDI verb list
_registry = VerbRegistry()
_registry.register_many(VERBS)


_EXTRA_SECTIONS: dict[str, str] = {
    "SELECTORS": """\
  @track:NAME        Notes on a specific track
  @channel:N         Notes on MIDI channel N
  @range:M.B-M.B    Notes in range (inclusive start and end beats)
  @pitch:PITCH       Notes matching a pitch (e.g. C4)
  @velocity:N-M      Notes with velocity in range
  @all               All notes in the song
  @recent            Last event from the log
  @recent:N          Last N events from the log
  @not:TYPE:VALUE    Negate a selector (exclude matches)

  Combine selectors to intersect: @track:Piano @range:1.1-4.4
  Negate to exclude: @track:Piano @not:pitch:C4""",
    "POSITION SYNTAX": """\
  M.B                Measure.Beat (1-based): 1.1 = start
  M.B.T              With tick offset: 1.1.120
  tick:N             Raw absolute tick
  +DUR               Relative: reference tick + duration
  -DUR               Relative: reference tick - duration
  end                Song end (last note end tick)""",
    "DURATION SYNTAX": """\
  whole, half, quarter, eighth, sixteenth, 32nd
  1n, 2n, 4n, 8n, 16n, 32n
  dotted-quarter, triplet-eighth
  ticks:N            Raw tick count""",
    "PITCH SYNTAX": """\
  C4, D#5, Bb3       Note + accidental + octave
  midi:60            Raw MIDI number (60 = middle C)""",
    "CHORD SYMBOLS": """\
  Cmaj, Am, Dm7, G7, Bdim, Faug, Csus4, Asus2
  Cmaj7, Am7, Dm7b5, G9, Cm6, Cadd9
  Dm/F               Slash chord (inversion)""",
    "VELOCITY SYNTAX": """\
  0-127              Numeric value
  ppp, pp, p, mp, mf, f, ff, fff   Dynamic names""",
    "CC NAMES": """\
  volume, pan, modulation, expression, sustain,
  reverb, chorus, brightness, portamento, breath""",
    "QUERY COMMANDS": """\
  map                Song overview
  tracks             List all tracks
  events TRACK|*|all [M.B-M.B]  Events on a track (or all)
  describe TRACK     Detailed track info
  stats              Song statistics
  status             Session status
  find PITCH         Search notes by pitch
  tracker TRACK M.B-M.B [res:RES]  Tracker step view (single track)
  tracker Track1,Track2 M.B-M.B [res:RES]  Multi-track combined view (read-only)
  tracker * M.B-M.B [res:RES]     All tracks combined view (read-only)
  instruments [FILTER] List available instruments""",
    "SESSION ACTIONS": """\
  new "Title" [tempo:N] [time-sig:N/D] [key:K] [ppqn:N]
  open ./file.mid
  save
  save as:./path.mid
  checkpoint NAME
  undo [to:NAME]
  redo""",
    "GM INSTRUMENTS (EXAMPLES)": """\
  acoustic-grand-piano, electric-piano-1, vibraphone
  acoustic-guitar-nylon, electric-bass-finger, violin
  trumpet, alto-sax, flute, string-ensemble-1""",
    "CUSTOM INSTRUMENTS": """\
  program:N           Raw MIDI program number (0-127)
  bank:MSB            Bank select (MSB only)
  bank:MSB.LSB        Bank select (MSB and LSB)
  instruments [FILTER] Query available instruments""",
    "EXAMPLE WORKFLOW": """\
  1. midi_session('new "My Song" tempo:120 time-sig:4/4 key:C-major')
  2. midi(['track add Piano instrument:acoustic-grand-piano'])
  3. midi(['track add Bass instrument:acoustic-bass'])
  4. midi(['note Piano C4 at:1.1 dur:quarter vel:mf',
          'note Piano E4 at:1.2 dur:quarter vel:mf',
          'chord Piano Cmaj at:2.1 dur:half vel:f'])
  5. midi(['note Bass C2 at:1.1 dur:half vel:f'])
  6. midi_query('map')
  7. midi_session('checkpoint v1')
  8. midi_session('save as:./my-song.mid')""",
}


def _build_reference_card() -> str:
    """Build the reference card from the verb registry and static sections."""
    lines: list[str] = []

    # Group verbs by category, preserving insertion order with custom titles
    categories = {
        "music": "NOTES, CHORDS & TRACKS",
        "meta": "TEMPO, TIME & KEY",
        "editing": "EDITING (SELECTOR-BASED)",
        "state": "TRACK STATE",
    }
    for cat_key, cat_title in categories.items():
        cat_verbs = [v for v in _registry.verbs if v.category == cat_key]
        if not cat_verbs:
            continue
        lines.append(f"{cat_title}:")
        for v in cat_verbs:
            lines.append(f"  {v.syntax}")
        lines.append("")

    # Static sections
    for title, content in _EXTRA_SECTIONS.items():
        lines.append(f"{title}:")
        lines.append(content)
        lines.append("")

    # Remove trailing empty lines
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


# Build the card at module load time
REFERENCE_CARD = _build_reference_card()
