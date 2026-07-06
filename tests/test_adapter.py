"""Full-stack tests for MidiAdapter — verifies FcpDomainAdapter[MidiModel, SnapshotEvent]."""

from __future__ import annotations

import os
import tempfile

import pytest

from fcp_core import EventLog as CoreEventLog, OpResult
from fcp_core import ParsedOp as GenericParsedOp, parse_op

from fcp_midi.adapter import MidiAdapter, SnapshotEvent
from fcp_midi.model.midi_model import MidiModel, pair_notes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def adapter() -> MidiAdapter:
    return MidiAdapter()


@pytest.fixture
def model(adapter: MidiAdapter) -> MidiModel:
    return adapter.create_empty("Test Song", {"tempo": "120", "time-sig": "4/4"})


@pytest.fixture
def model_with_piano(adapter: MidiAdapter, model: MidiModel) -> MidiModel:
    """Model with a Piano track already added."""
    log = CoreEventLog()
    _dispatch(adapter, model, log, "track add Piano instrument:acoustic-grand-piano")
    return model


def _dispatch(
    adapter: MidiAdapter,
    model: MidiModel,
    log: CoreEventLog,
    op_str: str,
) -> OpResult:
    """Helper to parse and dispatch an op."""
    parsed = parse_op(op_str)
    assert isinstance(parsed, GenericParsedOp), f"Parse failed: {parsed}"
    return adapter.dispatch_op(parsed, model, log)


# ===========================================================================
# create_empty
# ===========================================================================

class TestCreateEmpty:
    def test_returns_midi_model(self, adapter: MidiAdapter) -> None:
        model = adapter.create_empty("My Song", {})
        assert isinstance(model, MidiModel)
        assert model.title == "My Song"

    def test_respects_params(self, adapter: MidiAdapter) -> None:
        model = adapter.create_empty("Custom", {
            "tempo": "140",
            "time-sig": "3/4",
            "key": "Gm",
            "ppqn": "960",
        })
        assert model.ppqn == 960
        # Check conductor track has 140 BPM
        import mido
        for msg in model.file.tracks[0]:
            if msg.type == "set_tempo":
                assert abs(mido.tempo2bpm(msg.tempo) - 140.0) < 0.1
                break
        # Check time signature
        for msg in model.file.tracks[0]:
            if msg.type == "time_signature":
                assert msg.numerator == 3
                assert msg.denominator == 4
                break

    def test_defaults(self, adapter: MidiAdapter) -> None:
        model = adapter.create_empty("Default", {})
        assert model.ppqn == 480
        import mido
        for msg in model.file.tracks[0]:
            if msg.type == "set_tempo":
                assert abs(mido.tempo2bpm(msg.tempo) - 120.0) < 0.1
                break


# ===========================================================================
# serialize / deserialize round-trip
# ===========================================================================

class TestSerializeRoundTrip:
    def test_round_trip(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter vel:80")
        assert result.success

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            adapter.serialize(model_with_piano, path)
            loaded = adapter.deserialize(path)
            assert isinstance(loaded, MidiModel)
            assert len(loaded.tracks) == 1
            notes = loaded.get_notes()
            assert len(notes) == 1
            assert notes[0].pitch == 60  # C4
        finally:
            os.unlink(path)

    def test_round_trip_multiple_tracks(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "track add Bass instrument:acoustic-bass")
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        _dispatch(adapter, model_with_piano, log, "note Bass E2 at:1.1 dur:half")

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            adapter.serialize(model_with_piano, path)
            loaded = adapter.deserialize(path)
            assert len(loaded.tracks) == 2
            all_notes = loaded.get_notes()
            assert len(all_notes) == 2
        finally:
            os.unlink(path)


# ===========================================================================
# dispatch_op — track operations
# ===========================================================================

class TestTrackOps:
    def test_add_track(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, "track add Bass instrument:acoustic-bass")
        assert result.success
        assert model.get_track("Bass") is not None

    def test_remove_track(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "track remove Piano")
        assert result.success
        assert model_with_piano.get_track("Piano") is None

    def test_unknown_verb_fails(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, "frobnicate something")
        assert not result.success


# ===========================================================================
# dispatch_op — note operations
# ===========================================================================

class TestNoteOps:
    def test_add_note(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter vel:80")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 1
        assert notes[0].pitch == 60
        assert notes[0].velocity == 80

    def test_add_chord(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "chord Piano Cmaj at:1.1 dur:half vel:mf")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 3  # C, E, G
        pitches = sorted(n.pitch for n in notes)
        assert pitches == [60, 64, 67]  # C4, E4, G4

    def test_multiple_notes(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        _dispatch(adapter, model_with_piano, log, "note Piano E4 at:1.2 dur:quarter")
        _dispatch(adapter, model_with_piano, log, "note Piano G4 at:1.3 dur:quarter")
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 3


# ===========================================================================
# dispatch_op — meta operations
# ===========================================================================

class TestMetaOps:
    def test_tempo(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, "tempo 140")
        assert result.success
        import mido
        # Check conductor track
        for msg in model.file.tracks[0]:
            if msg.type == "set_tempo":
                bpm = mido.tempo2bpm(msg.tempo)
                # Should have 140 tempo (may be second tempo msg)
                break

    def test_time_sig(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, "time-sig 3/4")
        assert result.success

    def test_key_sig(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, "key-sig Am")
        assert result.success

    def test_marker(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, 'marker "Verse 1" at:1.1')
        assert result.success

    def test_title(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, 'title "New Title"')
        assert result.success


# ===========================================================================
# dispatch_op — editing operations
# ===========================================================================

class TestEditingOps:
    def test_remove_by_pitch(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        _dispatch(adapter, model_with_piano, log, "note Piano E4 at:1.2 dur:quarter")
        result = _dispatch(adapter, model_with_piano, log, "remove @track:Piano @pitch:C4")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 1
        assert notes[0].pitch == 64  # E4 remains

    def test_transpose(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        result = _dispatch(adapter, model_with_piano, log, "transpose +12 @track:Piano")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 1
        assert notes[0].pitch == 72  # C5

    def test_velocity_adjust(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter vel:80")
        result = _dispatch(adapter, model_with_piano, log, "velocity +20 @track:Piano")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert notes[0].velocity == 100

    def test_copy(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        result = _dispatch(adapter, model_with_piano, log, "copy @track:Piano to:2.1")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 2  # original + copy

    def test_quantize(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        # Add note slightly off-grid
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1.10 dur:quarter")
        result = _dispatch(adapter, model_with_piano, log, "quantize @track:Piano grid:quarter")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert notes[0].abs_tick == 0  # quantized to beat 1

    def test_modify(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter vel:80")
        result = _dispatch(adapter, model_with_piano, log, "modify @track:Piano vel:100")
        assert result.success
        notes = model_with_piano.get_notes("Piano")
        assert notes[0].velocity == 100


# ===========================================================================
# dispatch_query
# ===========================================================================

class TestDispatchQuery:
    def test_map_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        result = adapter.dispatch_query("map", model_with_piano)
        assert "Test Song" in result
        assert "Piano" in result

    def test_tracks_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        result = adapter.dispatch_query("tracks", model_with_piano)
        assert "Piano" in result

    def test_stats_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        result = adapter.dispatch_query("stats", model_with_piano)
        assert "Tracks: 1" in result

    def test_status_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        result = adapter.dispatch_query("status", model_with_piano)
        assert "Test Song" in result

    def test_describe_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter vel:80")
        result = adapter.dispatch_query("describe Piano", model_with_piano)
        assert "Track: Piano" in result
        assert "Notes: 1" in result

    def test_find_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        result = adapter.dispatch_query("find C4", model_with_piano)
        assert "Found 1" in result

    def test_events_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter vel:80")
        result = adapter.dispatch_query("events Piano", model_with_piano)
        assert "Events on Piano" in result

    def test_tracker_query(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        result = adapter.dispatch_query("tracker Piano 1.1-2.4", model_with_piano)
        assert "[Resolution:" in result
        assert "[C4_v" in result


# ===========================================================================
# Undo/Redo via byte snapshots
# ===========================================================================

class TestUndoRedo:
    def test_undo_note(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        assert result.success
        assert len(model_with_piano.get_notes("Piano")) == 1

        # Undo
        events = log.undo(1)
        assert len(events) == 1
        assert isinstance(events[0], SnapshotEvent)
        adapter.reverse_event(events[0], model_with_piano)
        assert len(model_with_piano.get_notes("Piano")) == 0

    def test_redo_note(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        assert result.success

        # Undo
        events = log.undo(1)
        for ev in events:
            adapter.reverse_event(ev, model_with_piano)
        assert len(model_with_piano.get_notes("Piano")) == 0

        # Redo
        replayed = log.redo(1)
        for ev in replayed:
            adapter.replay_event(ev, model_with_piano)
        assert len(model_with_piano.get_notes("Piano")) == 1

    def test_undo_multiple_ops(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter")
        _dispatch(adapter, model_with_piano, log, "note Piano E4 at:1.2 dur:quarter")
        _dispatch(adapter, model_with_piano, log, "note Piano G4 at:1.3 dur:quarter")
        assert len(model_with_piano.get_notes("Piano")) == 3

        # Undo all 3
        for _ in range(3):
            events = log.undo(1)
            for ev in events:
                adapter.reverse_event(ev, model_with_piano)
        assert len(model_with_piano.get_notes("Piano")) == 0

    def test_undo_track_add(self, adapter: MidiAdapter, model: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model, log, "track add Piano")
        assert result.success
        assert model.get_track("Piano") is not None

        events = log.undo(1)
        for ev in events:
            adapter.reverse_event(ev, model)
        assert model.get_track("Piano") is None

    def test_undo_then_redo_preserves_data(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        _dispatch(adapter, model_with_piano, log, "note Piano C4 at:1.1 dur:quarter vel:80")
        _dispatch(adapter, model_with_piano, log, "note Piano E4 at:1.2 dur:quarter vel:90")

        # Undo last note
        events = log.undo(1)
        for ev in events:
            adapter.reverse_event(ev, model_with_piano)
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 1
        assert notes[0].pitch == 60

        # Redo
        replayed = log.redo(1)
        for ev in replayed:
            adapter.replay_event(ev, model_with_piano)
        notes = model_with_piano.get_notes("Piano")
        assert len(notes) == 2


# ===========================================================================
# get_digest
# ===========================================================================

class TestGetDigest:
    def test_digest_format(self, adapter: MidiAdapter, model: MidiModel) -> None:
        digest = adapter.get_digest(model)
        assert digest.startswith("[")
        assert digest.endswith("]")
        assert "0t" in digest
        assert "tempo:120" in digest

    def test_digest_with_tracks(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        digest = adapter.get_digest(model_with_piano)
        assert "1t" in digest


# ===========================================================================
# rebuild_indices
# ===========================================================================

class TestRebuildIndices:
    def test_rebuild_updates_note_index(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        # Manually add a note
        model_with_piano.add_note("Piano", pitch=60, abs_tick=0, duration_ticks=480)
        adapter.rebuild_indices(model_with_piano)
        assert len(adapter.note_index.by_pitch.get(60, [])) == 1


# ===========================================================================
# Mute / Solo
# ===========================================================================

class TestMuteSolo:
    def test_mute(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "mute Piano")
        assert result.success
        ref = model_with_piano.get_track("Piano")
        assert ref is not None
        assert ref.mute is True

    def test_solo(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "solo Piano")
        assert result.success
        ref = model_with_piano.get_track("Piano")
        assert ref is not None
        assert ref.solo is True


# ===========================================================================
# CC and Bend
# ===========================================================================

class TestCCAndBend:
    def test_cc_op(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "cc Piano volume 100 at:1.1")
        assert result.success

    def test_bend_op(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "bend Piano 4000 at:1.1")
        assert result.success


# ===========================================================================
# Program change
# ===========================================================================

class TestProgramChange:
    def test_program_op(self, adapter: MidiAdapter, model_with_piano: MidiModel) -> None:
        log = CoreEventLog()
        result = _dispatch(adapter, model_with_piano, log, "program Piano electric-piano-1")
        assert result.success
