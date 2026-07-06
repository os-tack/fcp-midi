# fcp-midi

## Project Overview
MCP server that lets LLMs compose MIDI music through semantic operation strings.
See `docs/` for design documents and specifications.

## Architecture
Mido-native — a `mido.MidiFile` is the source of truth throughout, with no
parallel semantic model between the op handlers and the file format:

1. **MCP Server** - `src/fcp_midi/server/` - Parses op strings, resolves refs,
   dispatches, via `fcp_core.create_fcp_server()` + `MidiAdapter`
   (`src/fcp_midi/adapter.py`)
2. **MidiModel** - `src/fcp_midi/model/midi_model.py` - Wraps `mido.MidiFile`;
   op handlers read/write mido messages directly on the tracks. A `NoteIndex`
   gives fast selector lookups (by track/pitch/channel/velocity). Undo/redo
   and batch atomicity are byte snapshots of the file (`MidiModel.snapshot()`
   / `.restore()`), not event replay.

## Key Directories
- `src/fcp_midi/model/` - MidiModel (mido-backed), NoteIndex, timing conversions
- `src/fcp_midi/parser/` - Operation string parser, pitch/duration/position/chord grammar
- `src/fcp_midi/server/` - MCP server wiring, op handlers, queries, verb registry
- `src/fcp_midi/adapter.py` - `MidiAdapter`, the `FcpDomainAdapter[MidiModel, SnapshotEvent]`
  bridging fcp-core to MidiModel
- `src/fcp_midi/lib/` - GM instrument/drum tables, chord library, soundfont loading

## Commands
- `uv run pytest` - Run tests (620; `-m "not slow"` for the fast ~604-test subset)
- `uv run python -m fcp_midi` - Start the MCP server

## Conventions
- Python 3.11+
- uv for package management
- Tests co-located in `tests/`
- pytest for testing
- mido for MIDI I/O (`MidiModel.save()`/`.load()`)
