"""Tests for queries and NoteIndex-based selector resolution."""

from __future__ import annotations

import pytest

from fcp_midi.model.midi_model import MidiModel, NoteIndex, NoteRef
from fcp_midi.parser.selector import Selector
from fcp_midi.server.ops_context import MidiOpContext
from fcp_midi.server.resolvers import resolve_notes
from fcp_midi.server.queries import dispatch_query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(
    tracks: list[dict] | None = None,
    tempo: float = 120.0,
    title: str = "Test",
) -> MidiOpContext:
    """Build a MidiOpContext with some notes for testing.

    Each dict in `tracks` has:
      name: str
      notes: list[dict] with pitch, tick, dur, vel (optional channel)
    """
    model = MidiModel(title=title, ppqn=480, tempo=tempo)
    idx = NoteIndex()

    if tracks:
        for t in tracks:
            name = t["name"]
            ch = t.get("channel")
            model.add_track(name, channel=ch)
            for n in t.get("notes", []):
                model.add_note(
                    name,
                    pitch=n["pitch"],
                    abs_tick=n["tick"],
                    duration_ticks=n.get("dur", 480),
                    velocity=n.get("vel", 80),
                    channel=n.get("channel"),
                )
        idx.rebuild(model)

    return MidiOpContext(model=model, note_index=idx)


# ===========================================================================
# resolve_notes tests
# ===========================================================================

class TestResolveNotesNoSelectors:
    def test_no_selectors_returns_error(self):
        ctx = _make_ctx()
        result = resolve_notes([], ctx)
        assert isinstance(result, str)
        assert "No selectors" in result


class TestResolveNotesAll:
    def test_all_selector(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
            ]},
            {"name": "Bass", "notes": [
                {"pitch": 36, "tick": 0},
            ]},
        ])
        notes = resolve_notes([Selector(type="all", value="")], ctx)
        assert isinstance(notes, list)
        assert len(notes) == 3


class TestResolveNotesTrack:
    def test_track_selector(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
            ]},
            {"name": "Bass", "notes": [
                {"pitch": 36, "tick": 0},
            ]},
        ])
        notes = resolve_notes([Selector(type="track", value="Piano")], ctx)
        assert isinstance(notes, list)
        assert len(notes) == 2
        assert all(n.track_name == "Piano" for n in notes)

    def test_track_not_found(self):
        ctx = _make_ctx([{"name": "Piano", "notes": []}])
        result = resolve_notes([Selector(type="track", value="Drums")], ctx)
        assert isinstance(result, str)
        assert "not found" in result


class TestResolveNotesPitch:
    def test_pitch_selector(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 60, "tick": 480},
                {"pitch": 64, "tick": 960},
            ]},
        ])
        notes = resolve_notes([Selector(type="pitch", value="C4")], ctx)
        assert isinstance(notes, list)
        assert len(notes) == 2
        assert all(n.pitch == 60 for n in notes)


class TestResolveNotesRange:
    def test_range_selector(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},       # 1.1
                {"pitch": 62, "tick": 480},      # 1.2
                {"pitch": 64, "tick": 960},      # 1.3
                {"pitch": 65, "tick": 1920},     # 2.1
            ]},
        ])
        # @range:1.1-1.2 should include notes at 1.1 and 1.2
        notes = resolve_notes(
            [Selector(type="all", value=""), Selector(type="range", value="1.1-1.2")],
            ctx,
        )
        assert isinstance(notes, list)
        assert len(notes) == 2


class TestResolveNotesChannel:
    def test_channel_selector(self):
        ctx = _make_ctx([
            {"name": "Piano", "channel": 0, "notes": [
                {"pitch": 60, "tick": 0},
            ]},
            {"name": "Bass", "channel": 1, "notes": [
                {"pitch": 36, "tick": 0},
            ]},
        ])
        notes = resolve_notes([Selector(type="channel", value="1")], ctx)
        assert isinstance(notes, list)
        assert len(notes) == 1
        assert notes[0].channel == 1


class TestResolveNotesVelocity:
    def test_velocity_range_selector(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0, "vel": 40},
                {"pitch": 62, "tick": 480, "vel": 80},
                {"pitch": 64, "tick": 960, "vel": 120},
            ]},
        ])
        notes = resolve_notes(
            [Selector(type="all", value=""), Selector(type="velocity", value="70-100")],
            ctx,
        )
        assert isinstance(notes, list)
        assert len(notes) == 1
        assert notes[0].velocity == 80


class TestResolveNotesNegation:
    def test_negated_pitch(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
                {"pitch": 67, "tick": 960},
            ]},
        ])
        notes = resolve_notes(
            [
                Selector(type="track", value="Piano"),
                Selector(type="pitch", value="C4", negated=True),
            ],
            ctx,
        )
        assert isinstance(notes, list)
        assert len(notes) == 2
        assert all(n.pitch != 60 for n in notes)

    def test_negated_track(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [{"pitch": 60, "tick": 0}]},
            {"name": "Bass", "notes": [{"pitch": 36, "tick": 0}]},
        ])
        notes = resolve_notes(
            [
                Selector(type="all", value=""),
                Selector(type="track", value="Piano", negated=True),
            ],
            ctx,
        )
        assert isinstance(notes, list)
        assert len(notes) == 1
        assert notes[0].track_name == "Bass"


class TestResolveNotesCombined:
    def test_track_plus_pitch(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
            ]},
            {"name": "Bass", "notes": [
                {"pitch": 60, "tick": 0},
            ]},
        ])
        notes = resolve_notes(
            [
                Selector(type="track", value="Piano"),
                Selector(type="pitch", value="C4"),
            ],
            ctx,
        )
        assert isinstance(notes, list)
        assert len(notes) == 1
        assert notes[0].track_name == "Piano"
        assert notes[0].pitch == 60


class TestResolveNotesRecent:
    def test_recent_returns_last_note(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
                {"pitch": 67, "tick": 960},
            ]},
        ])
        notes = resolve_notes([Selector(type="recent", value="1")], ctx)
        assert isinstance(notes, list)
        assert len(notes) == 1
        assert notes[0].abs_tick == 960  # last note by tick


# ===========================================================================
# dispatch_query tests
# ===========================================================================

class TestQueryMap:
    def test_map_basic(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [{"pitch": 60, "tick": 0}]},
        ], title="My Song")
        result = dispatch_query("map", ctx)
        assert "My Song" in result
        assert "Piano" in result
        assert "tempo:120" in result

    def test_map_no_tracks(self):
        ctx = _make_ctx(title="Empty")
        result = dispatch_query("map", ctx)
        assert "No tracks" in result


class TestQueryTracks:
    def test_tracks_listing(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [{"pitch": 60, "tick": 0}]},
            {"name": "Bass", "notes": []},
        ])
        result = dispatch_query("tracks", ctx)
        assert "Piano" in result
        assert "Bass" in result
        assert "Tracks (2)" in result

    def test_tracks_empty(self):
        ctx = _make_ctx()
        result = dispatch_query("tracks", ctx)
        assert "No tracks" in result


class TestQueryDescribe:
    def test_describe_with_notes(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0, "vel": 80},
                {"pitch": 72, "tick": 480, "vel": 100},
            ]},
        ])
        result = dispatch_query("describe Piano", ctx)
        assert "Track: Piano" in result
        assert "Notes: 2" in result
        assert "Pitch range:" in result
        assert "Velocity range: 80-100" in result

    def test_describe_not_found(self):
        ctx = _make_ctx([{"name": "Piano", "notes": []}])
        result = dispatch_query("describe Drums", ctx)
        assert "not found" in result

    def test_describe_missing_name(self):
        ctx = _make_ctx()
        result = dispatch_query("describe", ctx)
        assert "Missing track name" in result


class TestQueryStats:
    def test_stats_basic(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
            ]},
        ], title="Stats Test")
        result = dispatch_query("stats", ctx)
        assert "Stats Test" in result
        assert "Tracks: 1" in result
        assert "Notes: 2" in result
        assert "Tempo: 120" in result


class TestQueryStatus:
    def test_status(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [{"pitch": 60, "tick": 0}]},
        ], title="Status Test")
        result = dispatch_query("status", ctx)
        assert "Status Test" in result
        assert "Tracks: 1" in result
        assert "Notes: 1" in result


class TestQueryFind:
    def test_find_matching(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0, "vel": 80},
                {"pitch": 60, "tick": 960, "vel": 100},
                {"pitch": 64, "tick": 480, "vel": 80},
            ]},
        ])
        result = dispatch_query("find C4", ctx)
        assert "Found 2 note(s)" in result

    def test_find_no_match(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [{"pitch": 60, "tick": 0}]},
        ])
        result = dispatch_query("find A5", ctx)
        assert "No notes matching" in result

    def test_find_missing_pitch(self):
        ctx = _make_ctx()
        result = dispatch_query("find", ctx)
        assert "Missing pitch" in result


class TestQueryEvents:
    def test_events_for_track(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0, "vel": 80},
                {"pitch": 64, "tick": 480, "vel": 90},
            ]},
        ])
        result = dispatch_query("events Piano", ctx)
        assert "Events on Piano" in result
        assert "vel:" in result

    def test_events_track_not_found(self):
        ctx = _make_ctx([{"name": "Piano", "notes": []}])
        result = dispatch_query("events Drums", ctx)
        assert "not found" in result

    def test_events_missing_name(self):
        ctx = _make_ctx()
        result = dispatch_query("events", ctx)
        assert "Missing track name" in result

    def test_events_all_tracks(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [{"pitch": 60, "tick": 0}]},
            {"name": "Bass", "notes": [{"pitch": 36, "tick": 0}]},
        ])
        result = dispatch_query("events *", ctx)
        assert "--- Piano ---" in result
        assert "--- Bass ---" in result


class TestQueryTracker:
    def test_tracker_basic(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0, "dur": 480},
                {"pitch": 64, "tick": 0, "dur": 480},
            ]},
        ])
        result = dispatch_query("tracker Piano 1.1-2.4", ctx)
        assert "[Resolution:" in result
        assert "[Track: Piano" in result
        assert "[C4_v" in result
        assert "[E4_v" in result

    def test_tracker_missing_args(self):
        ctx = _make_ctx()
        result = dispatch_query("tracker", ctx)
        assert "Usage:" in result

    def test_tracker_track_not_found(self):
        ctx = _make_ctx([{"name": "Piano", "notes": []}])
        result = dispatch_query("tracker Drums 1.1-4.4", ctx)
        assert "not found" in result

    def test_tracker_with_resolution(self):
        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0, "dur": 240},
            ]},
        ])
        result = dispatch_query("tracker Piano 1.1-2.4 res:8th", ctx)
        assert "[Resolution: 8th]" in result


class TestQueryHistory:
    def test_history_placeholder(self):
        ctx = _make_ctx()
        result = dispatch_query("history", ctx)
        assert "not available" in result.lower() or "session" in result.lower()


class TestQueryUnknown:
    def test_unknown_command(self):
        ctx = _make_ctx()
        result = dispatch_query("foobar", ctx)
        assert "Unknown query" in result
        assert "try:" in result


# ===========================================================================
# Editing ops still work with resolve_notes
# ===========================================================================

class TestEditingOpsWithResolvers:
    """Verify editing ops still work after switching to resolve_notes."""

    def test_remove_via_resolver(self):
        from fcp_midi.parser.ops import ParsedOp
        from fcp_midi.server.ops_editing import op_remove

        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
            ]},
        ])
        op = ParsedOp(
            verb="remove",
            raw="remove @track:Piano @pitch:C4",
            selectors=[Selector(type="track", value="Piano"), Selector(type="pitch", value="C4")],
        )
        result = op_remove(op, ctx)
        assert "Removed 1" in result
        remaining = ctx.model.get_notes("Piano")
        assert len(remaining) == 1
        assert remaining[0].pitch == 64

    def test_transpose_via_resolver(self):
        from fcp_midi.parser.ops import ParsedOp
        from fcp_midi.server.ops_editing import op_transpose

        ctx = _make_ctx([
            {"name": "Piano", "notes": [
                {"pitch": 60, "tick": 0},
                {"pitch": 64, "tick": 480},
            ]},
        ])
        op = ParsedOp(
            verb="transpose",
            raw="transpose +12 @track:Piano",
            target="+12",
            selectors=[Selector(type="track", value="Piano")],
        )
        result = op_transpose(op, ctx)
        assert "Transposed 2" in result
        notes = ctx.model.get_notes("Piano")
        assert notes[0].pitch == 72
        assert notes[1].pitch == 76
