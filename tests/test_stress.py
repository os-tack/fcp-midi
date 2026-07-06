"""Stress tests for scale and performance validation.

All tests are marked @pytest.mark.slow and can be run with:
    pytest tests/test_stress.py -v
"""

from __future__ import annotations

import os
import tempfile

import pytest

from fcp_midi.model.midi_model import MidiModel, NoteIndex, pair_notes

from tests.conftest import Harness


def _model_with_n_notes(n: int, tracks: int = 1) -> MidiModel:
    """Create a model with *n* notes spread across *tracks* tracks."""
    model = MidiModel(title="Stress", tempo=120.0, time_sig=(4, 4))
    names = [f"Track{i}" for i in range(tracks)]
    for name in names:
        model.add_track(name)

    pitches = [60, 62, 64, 65, 67]  # C4 D4 E4 F4 G4
    for i in range(n):
        name = names[i % tracks]
        pitch = pitches[i % len(pitches)]
        model.add_note(name, pitch, abs_tick=i * 480, duration_ticks=480, velocity=80)
    return model


# -----------------------------------------------------------------------
# NoteIndex scale
# -----------------------------------------------------------------------

@pytest.mark.slow
class TestNoteIndexScale:
    # Capped at 1000: MidiModel.add_note's insert_message_at_tick does a
    # linear scan from the start of the track, so appending n notes in
    # increasing-tick order is O(n) per call / O(n^2) total — at n=5000
    # that alone takes ~15s. Real sessions build up via individual DSL
    # ops across a whole composition, not a single track this dense.
    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_rebuild_consistency(self, n: int) -> None:
        """NoteIndex.rebuild produces correct state at various scales."""
        model = _model_with_n_notes(n)
        idx = NoteIndex()
        idx.rebuild(model)

        assert len(idx.by_track["Track0"]) == n

    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_rebuild_matches_direct_pairing(self, n: int) -> None:
        """NoteIndex.rebuild matches pair_notes() called directly at scale."""
        model = _model_with_n_notes(n)

        idx = NoteIndex()
        idx.rebuild(model)

        ref = model.get_track("Track0")
        direct = pair_notes(ref.track, track_name="Track0")

        assert len(idx.by_track["Track0"]) == len(direct)
        assert {n.abs_tick for n in idx.by_pitch[60]} == {
            n.abs_tick for n in direct if n.pitch == 60
        }


# -----------------------------------------------------------------------
# Many tracks
# -----------------------------------------------------------------------

@pytest.mark.slow
class TestManyTracks:
    @pytest.mark.parametrize("track_count", [10, 25, 50])
    def test_many_tracks(self, track_count: int) -> None:
        """Model with many tracks handles correctly."""
        model = _model_with_n_notes(track_count * 10, tracks=track_count)
        idx = NoteIndex()
        idx.rebuild(model)

        assert len(model.tracks) == track_count
        for i in range(track_count):
            assert len(idx.by_track[f"Track{i}"]) == 10


# -----------------------------------------------------------------------
# Deep undo (byte-snapshot log)
# -----------------------------------------------------------------------

@pytest.mark.slow
class TestDeepUndo:
    @pytest.mark.parametrize("depth", [50, 200, 500])
    def test_deep_undo(self, depth: int) -> None:
        """Undo/redo works correctly at depth via the real dispatch path."""
        harness = Harness()
        harness.execute_session('new "Deep" tempo:120 time-sig:4/4')
        harness.execute_ops(["track add Piano instrument:acoustic-grand-piano"])

        for i in range(depth):
            harness.execute_ops([f"note Piano C4 at:tick:{i * 480} dur:quarter"])

        track = harness.model.get_track("Piano")
        assert len(pair_notes(track.track, track_name="Piano")) == depth

        # Undo all `depth` note-add events (the "track add" is a separate,
        # earlier event not counted in `depth`).
        for _ in range(depth):
            result = harness.execute_session("undo")
            assert "+" in result or "Nothing to undo" in result

        track = harness.model.get_track("Piano")
        assert len(pair_notes(track.track, track_name="Piano")) == 0

        # Redo all
        for _ in range(depth):
            result = harness.execute_session("redo")
            assert "+" in result or "Nothing to redo" in result

        track = harness.model.get_track("Piano")
        assert len(pair_notes(track.track, track_name="Piano")) == depth


# -----------------------------------------------------------------------
# Serialization round-trip at scale
# -----------------------------------------------------------------------

@pytest.mark.slow
class TestSerializationScale:
    @pytest.mark.parametrize("n", [100, 500])
    def test_round_trip(self, n: int) -> None:
        """Serialize and deserialize preserves note count."""
        model = _model_with_n_notes(n)

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name

        try:
            model.save(path)
            loaded = MidiModel.load(path)

            original_notes = sum(
                len(pair_notes(ref.track, track_name=name))
                for name, ref in model.tracks.items()
            )
            loaded_notes = sum(
                len(pair_notes(ref.track, track_name=name))
                for name, ref in loaded.tracks.items()
            )
            assert loaded_notes == original_notes
        finally:
            os.unlink(path)

    def test_multi_tempo_round_trip(self) -> None:
        """Multi-tempo song serializes without error."""
        harness = Harness()
        harness.execute_session('new "MultiTempo" tempo:120 time-sig:4/4')
        harness.execute_ops(["track add Piano instrument:acoustic-grand-piano"])
        harness.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
            "tempo 140 at:2.1",
            "tempo 100 at:3.1",
        ])

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name

        try:
            harness.model.save(path)
            import mido
            mid = mido.MidiFile(path)
            tempo_msgs = []
            for track in mid.tracks:
                for msg in track:
                    if msg.type == "set_tempo":
                        tempo_msgs.append(msg)
            # Should have the initial tempo + 2 additional
            assert len(tempo_msgs) >= 3
        finally:
            os.unlink(path)


# -----------------------------------------------------------------------
# Batch scale through the real dispatch path
# -----------------------------------------------------------------------

@pytest.mark.slow
class TestBatchScale:
    def test_batch_100_notes(self) -> None:
        """Add 100 notes in a single batch through the real adapter/session
        dispatch path (Harness), including batch-atomicity snapshotting."""
        harness = Harness()
        harness.execute_session('new "Batch" tempo:120')
        harness.execute_ops(["track add Piano instrument:acoustic-grand-piano"])

        ops = []
        for i in range(100):
            measure = i // 4 + 1
            beat = i % 4 + 1
            ops.append(f"note Piano C4 at:{measure}.{beat} dur:quarter vel:80")

        results = harness.execute_ops(ops)
        errors = [r for r in results if r.startswith("!")]
        assert len(errors) == 0

        track = harness.model.get_track("Piano")
        assert len(pair_notes(track.track, track_name="Piano")) == 100
