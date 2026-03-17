# fcp-midi

MCP server for semantic MIDI composition.

## What It Does

fcp-midi lets LLMs compose music by describing musical intent -- notes, chords, dynamics, tempo changes -- and renders it into standard MIDI files. Instead of manipulating raw bytes or MIDI events, the LLM works with operations like `note Bass E2 at:1.1 dur:quarter vel:90` and `crescendo @track:Lead vel:45-75 @range:3.1-6.4`. Built on the [FCP](https://github.com/os-tack/fcp) framework, powered by pretty-midi for serialization.

## Quick Example

```
midi_session('new "Voltage Drop" tempo:140 key:E-minor')

midi([
  'note Drums kick at:1.1 dur:eighth vel:100',
  'note Bass E2 at:1.1 dur:quarter vel:90',
  'chord Pad Em at:1.1 dur:whole vel:70',
  'crescendo @track:Lead vel:45-75 @range:3.1-6.4',
  'tempo 174 at:11.1',
])

midi_session('save as:./voltage_drop.mid')
```

### Available MCP Tools

| Tool | Purpose |
|------|---------|
| `midi(ops)` | Batch mutations -- notes, chords, tracks, tempo, dynamics, copy/transpose |
| `midi_query(q)` | Inspect the composition -- map, tracks, events, piano-roll, instruments, find |
| `midi_session(action)` | Lifecycle -- new, open, save, checkpoint, undo, redo |
| `midi_help()` | Full reference card |

### Hero Examples

**Plumber's Journey** -- Classic game theme faithfully recreated: 4 tracks, 288 notes, 16 seconds, 180 BPM.

**Voltage Drop** -- A drum-and-bass track with tempo acceleration (140 -> 155 -> 174 BPM), 5 tracks, breakbeats, sub-bass, arpeggios, and a signature DROP section. Also used in the FCP vs raw Python agent battle -- FCP produced 1,674 notes across 12 tracks in 87 seconds, compared to 694 notes from ~689 lines of hand-written Python.

See [`docs/examples/`](docs/examples/) for MIDI files and the full writeup.

## Installation

Requires Python >= 3.11.

```bash
pip install fcp-midi
```

### MCP Client Configuration

```json
{
  "mcpServers": {
    "midi": {
      "command": "uv",
      "args": ["run", "python", "-m", "fcp_midi"]
    }
  }
}
```

## Architecture

3-layer architecture:

```
MCP Server (Intent Layer)
  Parses op strings, dispatches to verb handlers
        |
Semantic Model
  Tracks, notes, chords, dynamics, tempo maps, markers
  In-memory composition graph with event sourcing
        |
Serialization (pretty-midi)
  Semantic model -> MIDI binary output
```

Key features:

- **Instrument library** -- GM instruments by name (`acoustic-grand-piano`, `synth-bass-1`, `standard-kit`)
- **Soundfont support** -- Load custom instrument banks
- **Beat addressing** -- `at:1.1` (bar 1, beat 1), `at:3.2.240` (bar 3, beat 2, tick 240)
- **Duration vocabulary** -- `whole`, `half`, `quarter`, `eighth`, `sixteenth`, `triplet`
- **Dynamics** -- `crescendo`, `diminuendo` across ranges
- **Copy/transpose** -- Duplicate and shift musical phrases

## Development

```bash
uv sync
uv run pytest       # 658 tests
uv run pytest -m "not slow"  # skip stress tests
uv run ruff check   # linting
uv run pyright      # type checking
```

## License

MIT

