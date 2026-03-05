"""MIDI FCP — File Context Protocol for MIDI composition.

Uses ``create_fcp_server()`` from fcp_core to wire up the MCP server
with the MIDI domain adapter.
"""

from fcp_core import create_fcp_server

from fcp_midi.adapter import MidiAdapter
from fcp_midi.server.verb_registry import VERBS

# Extra sections for the tool description (MIDI-specific reference)
_EXTRA_SECTIONS: dict[str, str] = {
    "Selectors": (
        "@track:NAME  @channel:N  @range:M.B-M.B  @pitch:PITCH\n"
        "  @velocity:N-M  @all  @recent  @recent:N  @not:TYPE:VALUE\n"
        "  Combine to intersect: @track:Piano @range:1.1-4.4"
    ),
    "Position": (
        "M.B (1.1 = start)  M.B.T (tick offset)  tick:N  +DUR  -DUR  end"
    ),
    "Duration": (
        "whole, half, quarter, eighth, sixteenth, 32nd\n"
        "  1n, 2n, 4n, 8n, 16n, 32n\n"
        "  dotted-quarter, triplet-eighth, ticks:N"
    ),
    "Pitch": (
        "C4, D#5, Bb3 (note+accidental+octave)  midi:60 (raw MIDI number)"
    ),
    "Chords": (
        "Cmaj, Am, Dm7, G7, Bdim, Faug, Csus4, Asus2\n"
        "  Cmaj7, Am7, Dm7b5, G9, Cm6, Cadd9, Dm/F (slash)"
    ),
    "Velocity": (
        "0-127 (numeric)  ppp, pp, p, mp, mf, f, ff, fff (dynamic names)"
    ),
    "CC Names": (
        "volume, pan, modulation, expression, sustain,\n"
        "  reverb, chorus, brightness, portamento, breath"
    ),
    "GM Instruments (examples)": (
        "acoustic-grand-piano, electric-piano-1, vibraphone\n"
        "  acoustic-guitar-nylon, electric-bass-finger, violin\n"
        "  trumpet, alto-sax, flute, string-ensemble-1\n"
        "  program:N (raw 0-127)  bank:MSB[.LSB]"
    ),
    "Response Prefixes": (
        "+  note/chord added     ~  event modified\n"
        "  *  track modified       -  event removed\n"
        "  !  meta event           @  bulk operation"
    ),
    "Conventions": (
        "- Positions are 1-based: measure 1, beat 1 = 1.1\n"
        "  - Channels are 1-indexed user-facing (ch:1 through ch:16)\n"
        "  - Channel 10 is drums (GM standard)\n"
        "  - Track names are unique identifiers\n"
        "  - Batch multiple ops in one call for efficiency\n"
        "  - Call midi_help after context truncation for full reference"
    ),
}

adapter = MidiAdapter()

mcp = create_fcp_server(
    domain="midi",
    adapter=adapter,
    verbs=VERBS,
    extra_sections=_EXTRA_SECTIONS,
    extensions=["mid", "midi"],
    name="midi-fcp",
    instructions="MIDI File Context Protocol. Call midi_help for the reference card.",
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
