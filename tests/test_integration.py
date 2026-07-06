"""End-to-end integration tests for the MIDI FCP server layer.

Tests exercise the full stack through the real fcp-core dispatch path
(``Harness`` in conftest.py — parse_op / SessionDispatcher /
MidiAdapter.dispatch_op/dispatch_query, with batch atomicity): session
management, track/note/chord operations, queries, undo/redo, and error
handling.
"""

from __future__ import annotations

from fcp_midi.model.midi_model import TrackRef, pair_notes

from tests.conftest import Harness


def _notes(ref: TrackRef):
    return pair_notes(ref.track, track_name=ref.name)


def _ccs(ref: TrackRef):
    return [m for m in ref.track if m.type == "control_change"]


def _bends(ref: TrackRef):
    return [m for m in ref.track if m.type == "pitchwheel"]


# -----------------------------------------------------------------------
# Session management
# -----------------------------------------------------------------------

class TestSessionNew:
    def test_create_song(self, harness: Harness) -> None:
        result = harness.execute_session('new "Test" tempo:120 time-sig:4/4')
        assert result.startswith("+")
        # NOTE: fcp-core's SessionDispatcher._handle_new echoes the title
        # from the same `params` dict the adapter hook already popped
        # "title" out of, so the confirmation message always says
        # "New session 'Untitled'." regardless of the actual title given.
        # This is a fcp-core bug (affects every domain built on it, not
        # fcp-midi specifically) — assert against the real model state.
        assert harness.model is not None
        assert harness.model.title == "Test"
        stats = harness.execute_query("stats")
        assert "Tempo: 120" in stats
        assert "Time sig: 4/4" in stats

    def test_create_with_key(self, harness: Harness) -> None:
        result = harness.execute_session('new "KeySong" tempo:90 key:G-minor')
        assert result.startswith("+")
        stats = harness.execute_query("stats")
        assert "Key: Gm" in stats

    def test_create_with_ppqn(self, harness: Harness) -> None:
        result = harness.execute_session('new "Hi Res" ppqn:960')
        assert result.startswith("+")
        assert harness.model is not None
        assert harness.model.ppqn == 960

    def test_no_song_ops_error(self, harness: Harness) -> None:
        results = harness.execute_ops(["note Piano C4 at:1.1 dur:quarter"])
        assert any("No model loaded" in r for r in results)

    def test_no_song_query_error(self, harness: Harness) -> None:
        result = harness.execute_query("map")
        assert "No model loaded" in result


# -----------------------------------------------------------------------
# Track management
# -----------------------------------------------------------------------

class TestTrackOps:
    def test_add_track(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(
            ["track add Piano instrument:acoustic-grand-piano"]
        )
        assert any("+" in r and "Piano" in r for r in results)
        assert len(harness_with_song.model.tracks) == 1

        track = harness_with_song.model.get_track("Piano")
        assert track is not None
        assert track.program == 0

    def test_add_track_with_channel(self, harness_with_song: Harness) -> None:
        # ch:10 = MIDI channel 10 (1-indexed) = channel 9 (0-indexed, drums)
        results = harness_with_song.execute_ops(
            ["track add Drums instrument:standard-kit ch:10"]
        )
        assert any("+" in r for r in results)
        track = harness_with_song.model.get_track("Drums")
        assert track is not None
        assert track.channel == 9

    def test_remove_track(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["track remove Piano"])
        assert any("removed" in r.lower() for r in results)
        assert len(harness_with_piano.model.tracks) == 0

    def test_remove_nonexistent_track(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["track remove Guitar"])
        assert any("!" in r for r in results)

    def test_unknown_instrument(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(
            ["track add Bad instrument:nonexistent-thing"]
        )
        assert any("Unknown instrument" in r for r in results)


# -----------------------------------------------------------------------
# Note operations
# -----------------------------------------------------------------------

class TestNoteOps:
    def test_add_note(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["note Piano C4 at:1.1 dur:quarter vel:80"]
        )
        assert any("+" in r and "C4" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        notes = _notes(track)
        assert len(notes) == 1
        assert notes[0].pitch == 60  # C4
        assert notes[0].velocity == 80
        assert notes[0].duration_ticks == 480  # quarter at ppqn=480
        assert notes[0].abs_tick == 0  # 1.1 = tick 0

    def test_add_sharp_pitch(self, harness_with_piano: Harness) -> None:
        """Regression: C#4 was eaten by shlex treating # as comment."""
        results = harness_with_piano.execute_ops(
            ["note Piano C#4 at:1.1 dur:quarter vel:mf"]
        )
        assert any("+" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        note = _notes(track)[0]
        assert note.pitch == 61  # C#4
        assert note.velocity == 80  # mf

    def test_add_note_symbolic_velocity(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["note Piano E5 at:2.1 dur:half vel:ff"]
        )
        assert any("+" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        note = _notes(track)[0]
        assert note.velocity == 112  # ff = 112

    def test_add_note_default_values(self, harness_with_piano: Harness) -> None:
        """Note with minimal params uses defaults."""
        results = harness_with_piano.execute_ops(["note Piano G3"])
        assert any("+" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        note = _notes(track)[0]
        assert note.velocity == 80  # default
        assert note.duration_ticks == 480  # default quarter

    def test_invalid_pitch(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["note Piano X9 at:1.1 dur:quarter"]
        )
        assert any("!" in r for r in results)
        assert any("pitch" in r.lower() or "parse" in r.lower() for r in results)

    def test_unknown_track(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["note Guitar C4 at:1.1 dur:quarter"]
        )
        assert any("!" in r for r in results)
        # Should suggest the existing "Piano" track
        assert any("Piano" in r for r in results)


# -----------------------------------------------------------------------
# Chord operations
# -----------------------------------------------------------------------

class TestChordOps:
    def test_add_chord(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["chord Piano Cmaj at:2.1 dur:half vel:70"]
        )
        assert any("+" in r and "Cmaj" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        notes = _notes(track)
        assert len(notes) == 3  # C, E, G
        assert sorted(n.pitch for n in notes) == [60, 64, 67]  # C4, E4, G4

    def test_add_minor_chord(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["chord Piano Am at:1.1 dur:quarter"]
        )
        assert any("+" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        pitches = sorted(n.pitch for n in _notes(track))
        assert pitches == [69, 72, 76]  # A4, C5, E5 (root at octave 4)

    def test_add_seventh_chord(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["chord Piano G7 at:3.1 dur:quarter"]
        )
        assert any("+" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 4  # G, B, D, F


# -----------------------------------------------------------------------
# CC and Bend operations
# -----------------------------------------------------------------------

class TestCCBendOps:
    def test_add_cc(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["cc Piano volume 100 at:1.1"]
        )
        assert any("+" in r and "volume" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        ccs = _ccs(track)
        assert len(ccs) == 1
        assert ccs[0].control == 7  # volume
        assert ccs[0].value == 100

    def test_add_bend(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["bend Piano 4096 at:1.1"]
        )
        assert any("+" in r and "bend" in r.lower() for r in results)

        track = harness_with_piano.model.get_track("Piano")
        bends = _bends(track)
        assert len(bends) == 1
        assert bends[0].pitch == 4096

    def test_bend_center(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(
            ["bend Piano center at:1.1"]
        )
        assert any("+" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert _bends(track)[0].pitch == 0


# -----------------------------------------------------------------------
# Meta operations
# -----------------------------------------------------------------------

class TestMetaOps:
    def test_tempo(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(["tempo 140"])
        assert any("+" in r and "140" in r for r in results)

    def test_time_sig(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(["time-sig 3/4"])
        assert any("+" in r and "3/4" in r for r in results)

    def test_key_sig(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(["key-sig G-major"])
        assert any("+" in r and "G" in r for r in results)

    def test_marker(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(['marker Chorus at:5.1'])
        assert any("+" in r and "Chorus" in r for r in results)

    def test_title(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(['title "New Title"'])
        assert any("+" in r for r in results)
        assert harness_with_song.model.title == "New Title"


# -----------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------

class TestQueries:
    def test_tracks_query(self, harness_with_piano: Harness) -> None:
        result = harness_with_piano.execute_query("tracks")
        assert "Piano" in result
        assert "ch:" in result

    def test_stats_query(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
            "note Piano E4 at:1.2 dur:quarter",
        ])
        result = harness_with_piano.execute_query("stats")
        assert "Notes: 2" in result
        assert "Tracks: 1" in result

    def test_map_query(self, harness_with_piano: Harness) -> None:
        result = harness_with_piano.execute_query("map")
        assert "Test Song" in result
        assert "Piano" in result

    def test_describe_query(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
        ])
        result = harness_with_piano.execute_query("describe Piano")
        assert "Piano" in result
        assert "Notes: 1" in result

    def test_events_query(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
        ])
        result = harness_with_piano.execute_query("events Piano")
        assert "C4" in result or "C" in result

    def test_events_query_with_range(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
            "note Piano E4 at:5.1 dur:quarter",
        ])
        result = harness_with_piano.execute_query("events Piano 1.1-4.4")
        assert "C" in result
        # E4 at 5.1 should be excluded
        lines = result.split("\n")
        event_lines = [l for l in lines if "vel:" in l]
        assert len(event_lines) == 1

    def test_find_query(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
            "note Piano E4 at:2.1 dur:quarter",
        ])
        result = harness_with_piano.execute_query("find C4")
        assert "1" in result  # at least 1 found

    def test_status_query(self, harness_with_piano: Harness) -> None:
        result = harness_with_piano.execute_query("status")
        assert "Test Song" in result

    def test_piano_roll_alias(self, harness_with_piano: Harness) -> None:
        """'piano-roll' is a documented alias for 'tracker'."""
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter"])
        result = harness_with_piano.execute_query("piano-roll Piano 1.1-4.4")
        assert not result.startswith("!")

    def test_unknown_query(self, harness_with_piano: Harness) -> None:
        result = harness_with_piano.execute_query("foobar")
        assert "!" in result

    def test_unknown_track_query(self, harness_with_piano: Harness) -> None:
        result = harness_with_piano.execute_query("describe Guitar")
        assert "not found" in result.lower()


# -----------------------------------------------------------------------
# Checkpoint, Undo, Redo
# -----------------------------------------------------------------------

class TestUndoRedo:
    def test_checkpoint_and_undo(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter"])
        harness_with_piano.execute_session("checkpoint v1")
        harness_with_piano.execute_ops(["note Piano E5 at:3.1 dur:quarter"])

        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 2

        result = harness_with_piano.execute_session("undo")
        assert "+" in result

        # Undo restores the model via a byte snapshot — re-fetch the track.
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 1

    def test_redo_restores_note(self, harness_with_piano: Harness) -> None:
        """Redo after undo should restore the note with full data (v2's
        byte-snapshot undo/redo is exact, unlike v1's field-level replay)."""
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:90"])
        track = harness_with_piano.model.get_track("Piano")
        note = _notes(track)[0]
        original_pitch = note.pitch
        original_vel = note.velocity

        harness_with_piano.execute_session("undo")
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 0

        result = harness_with_piano.execute_session("redo")
        assert "+" in result
        track = harness_with_piano.model.get_track("Piano")
        restored = _notes(track)
        assert len(restored) == 1
        assert restored[0].pitch == original_pitch
        assert restored[0].velocity == original_vel

    def test_undo_note_removal_restores_note(self, harness_with_piano: Harness) -> None:
        """Undoing a note removal should restore the note with correct data."""
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:85"])
        track = harness_with_piano.model.get_track("Piano")
        note = _notes(track)[0]
        original_pitch = note.pitch
        original_vel = note.velocity
        original_tick = note.abs_tick
        original_dur = note.duration_ticks

        harness_with_piano.execute_ops(["remove @track:Piano"])
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 0

        result = harness_with_piano.execute_session("undo")
        assert "+" in result
        track = harness_with_piano.model.get_track("Piano")
        restored = _notes(track)
        assert len(restored) == 1
        assert restored[0].pitch == original_pitch
        assert restored[0].velocity == original_vel
        assert restored[0].abs_tick == original_tick
        assert restored[0].duration_ticks == original_dur

    def test_undo_track_removal_restores_track(self, harness_with_piano: Harness) -> None:
        """Undoing a track removal should restore the track with all its notes."""
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano E4 at:2.1 dur:quarter vel:80",
        ])
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 2

        harness_with_piano.execute_ops(["track remove Piano"])
        assert len(harness_with_piano.model.tracks) == 0

        result = harness_with_piano.execute_session("undo")
        assert "+" in result
        restored = harness_with_piano.model.get_track("Piano")
        assert restored is not None
        assert restored.name == "Piano"
        assert len(_notes(restored)) == 2

    def test_undo_to_checkpoint(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter"])
        harness_with_piano.execute_session("checkpoint v1")
        harness_with_piano.execute_ops([
            "note Piano E4 at:2.1 dur:quarter",
            "note Piano G4 at:3.1 dur:quarter",
        ])

        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 3

        result = harness_with_piano.execute_session("undo to:v1")
        assert "+" in result

        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 1

    def test_nothing_to_undo(self, harness_with_piano: Harness) -> None:
        result = harness_with_piano.execute_session("undo")
        # Should get the "track add" event from the fixture, but check
        # gracefully either way.
        assert "+" in result or "Nothing to undo" in result

    def test_nothing_to_redo(self, harness_with_piano: Harness) -> None:
        result = harness_with_piano.execute_session("redo")
        assert "Nothing to redo" in result


# -----------------------------------------------------------------------
# Batch atomicity
# -----------------------------------------------------------------------

class TestBatchAtomicity:
    def test_failed_op_rolls_back_batch(self, harness_with_piano: Harness) -> None:
        """If any op in a batch fails, the whole batch is rolled back."""
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 0

        results = harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
            "note BadTrack E4 at:1.1 dur:quarter",
        ])
        assert any("rolled back" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 0

    def test_successful_batch_commits(self, harness_with_piano: Harness) -> None:
        """A fully successful batch should commit all ops."""
        results = harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
            "note Piano E4 at:1.2 dur:quarter",
            "note Piano G4 at:1.3 dur:quarter",
        ])
        assert all("rolled back" not in r for r in results)
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 3

    def test_batch_rollback_preserves_prior_state(self, harness_with_piano: Harness) -> None:
        """A failed batch should not affect notes added before the batch."""
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter"])
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 1

        harness_with_piano.execute_ops([
            "note Piano E4 at:1.2 dur:quarter",
            "note BadTrack G4 at:1.3 dur:quarter",
        ])
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 1


# -----------------------------------------------------------------------
# Editing operations (selector-based)
# -----------------------------------------------------------------------

class TestEditingOps:
    def test_remove_by_track(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
            "note Piano E4 at:2.1 dur:quarter",
        ])
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 2

        results = harness_with_piano.execute_ops(["remove @track:Piano"])
        assert any("Removed 2" in r for r in results)
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 0

    def test_transpose(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter",
        ])
        results = harness_with_piano.execute_ops(["transpose +7 @track:Piano"])
        assert any("Transposed" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert _notes(track)[0].pitch == 67  # C4(60) + 7 = G4(67)

    def test_velocity_adjust(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
        ])
        results = harness_with_piano.execute_ops(["velocity +20 @track:Piano"])
        assert any("Adjusted" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert _notes(track)[0].velocity == 100

    def test_mute_toggle(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["mute Piano"])
        assert any("muted" in r.lower() for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert track.mute is True

        # Toggle back
        harness_with_piano.execute_ops(["mute Piano"])
        track = harness_with_piano.model.get_track("Piano")
        assert track.mute is False

    def test_solo_toggle(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["solo Piano"])
        assert any("solo" in r.lower() for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert track.solo is True


# -----------------------------------------------------------------------
# Error handling
# -----------------------------------------------------------------------

class TestErrors:
    def test_empty_op(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops([""])
        assert any("!" in r for r in results)

    def test_unknown_verb(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["foobar something"])
        assert any("!" in r or "Unknown verb" in r for r in results)

    def test_unknown_session_action(self, harness: Harness) -> None:
        result = harness.execute_session("foobar")
        assert "!" in result

    def test_fuzzy_track_suggestion(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["note Paino C4 at:1.1 dur:quarter"])
        joined = "\n".join(results)
        # Should suggest "Piano" as a close match
        assert "Piano" in joined


# -----------------------------------------------------------------------
# Full workflow test
# -----------------------------------------------------------------------

class TestFullWorkflow:
    def test_end_to_end(self, harness: Harness) -> None:
        # 1. Create song
        result = harness.execute_session('new "Integration Test" tempo:120 time-sig:4/4')
        assert result.startswith("+")
        assert harness.model is not None

        # 2. Add tracks
        results = harness.execute_ops([
            "track add Piano instrument:acoustic-grand-piano",
            "track add Bass instrument:acoustic-bass",
        ])
        assert all(any(c in r for c in ["+", "["]) for r in results)
        assert len(harness.model.tracks) == 2

        # 3. Add notes
        harness.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano E4 at:1.2 dur:quarter vel:80",
            "note Piano G4 at:1.3 dur:quarter vel:80",
        ])
        piano = harness.model.get_track("Piano")
        assert len(_notes(piano)) == 3

        # 4. Add chord
        harness.execute_ops(["chord Piano Cmaj at:2.1 dur:half vel:70"])
        piano = harness.model.get_track("Piano")
        assert len(_notes(piano)) == 6  # 3 + 3

        # 5. Add bass note
        harness.execute_ops(["note Bass C2 at:1.1 dur:half vel:f"])
        bass = harness.model.get_track("Bass")
        assert len(_notes(bass)) == 1

        # 6. Query tracks
        result = harness.execute_query("tracks")
        assert "Piano" in result
        assert "Bass" in result

        # 7. Query stats
        result = harness.execute_query("stats")
        assert "Notes: 7" in result
        assert "Tracks: 2" in result

        # 8. Query map
        result = harness.execute_query("map")
        assert "Integration Test" in result
        assert "Piano" in result
        assert "Bass" in result

        # 9. Checkpoint
        result = harness.execute_session("checkpoint v1")
        assert "+" in result

        # 10. Add more notes
        harness.execute_ops(["note Piano E5 at:3.1 dur:quarter"])
        piano = harness.model.get_track("Piano")
        assert len(_notes(piano)) == 7

        # 11. Undo — re-fetch the track, undo restores via byte snapshot
        result = harness.execute_session("undo")
        assert "+" in result
        piano = harness.model.get_track("Piano")
        assert len(_notes(piano)) == 6

        # 12. Redo — byte-snapshot redo is exact
        result = harness.execute_session("redo")
        assert "+" in result
        piano = harness.model.get_track("Piano")
        assert len(_notes(piano)) == 7


# -----------------------------------------------------------------------
# Modify verb
# -----------------------------------------------------------------------

class TestModifyOps:
    def test_modify_pitch(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        results = harness_with_piano.execute_ops(["modify @track:Piano pitch:E4"])
        assert any("Modified 1" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert _notes(track)[0].pitch == 64  # E4

    def test_modify_velocity(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        results = harness_with_piano.execute_ops(["modify @track:Piano vel:ff"])
        assert any("Modified 1" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert _notes(track)[0].velocity == 112  # ff

    def test_modify_duration(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        results = harness_with_piano.execute_ops(["modify @track:Piano dur:half"])
        assert any("Modified 1" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert _notes(track)[0].duration_ticks == 960  # half

    def test_modify_multiple_fields(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        results = harness_with_piano.execute_ops(
            ["modify @track:Piano pitch:D5 vel:100 dur:eighth"]
        )
        assert any("Modified 1" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        note = _notes(track)[0]
        assert note.pitch == 74  # D5
        assert note.velocity == 100
        assert note.duration_ticks == 240  # eighth

    def test_modify_no_match(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["modify @track:Piano pitch:E4"])
        assert any("No notes matched" in r for r in results)

    def test_modify_no_params(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter"])
        results = harness_with_piano.execute_ops(["modify @track:Piano"])
        assert any("No modification specified" in r for r in results)


# -----------------------------------------------------------------------
# Repeat verb
# -----------------------------------------------------------------------

class TestRepeatOps:
    def test_repeat_once_with_to(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano E4 at:1.2 dur:quarter vel:80",
        ])
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 2

        results = harness_with_piano.execute_ops(["repeat @track:Piano to:3.1 count:1"])
        assert any("Repeated" in r and "x1" in r for r in results)
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 4  # 2 original + 2 repeated

    def test_repeat_three_times_auto_append(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 1

        results = harness_with_piano.execute_ops(["repeat @track:Piano count:3"])
        assert any("x3" in r for r in results)
        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 4  # 1 original + 3 repeated

    def test_repeat_with_to_specified(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        results = harness_with_piano.execute_ops(["repeat @track:Piano to:5.1 count:2"])
        assert any("Repeated" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert len(_notes(track)) == 3  # 1 original + 2 repeated

    def test_repeat_no_match(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["repeat @track:Piano count:2"])
        assert any("No notes matched" in r for r in results)


# -----------------------------------------------------------------------
# Relative positions in integration
# -----------------------------------------------------------------------

class TestRelativePositions:
    def test_note_at_plus_quarter(self, harness_with_piano: Harness) -> None:
        # First note at 1.1 (tick 0), duration quarter (480 ticks)
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        # _last_tick after first note = 0 + 480 = 480
        # +quarter from reference 480 = 480 + 480 = 960
        results = harness_with_piano.execute_ops(
            ["note Piano E4 at:+quarter dur:quarter vel:80"]
        )
        assert any("+" in r and "E4" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        notes = sorted(_notes(track), key=lambda n: n.abs_tick)
        assert notes[1].abs_tick == 960

    def test_note_at_end(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano E4 at:2.1 dur:half vel:80",
        ])
        # end = max(note.tick + note.dur) = max(0+480, 1920+960) = 2880
        results = harness_with_piano.execute_ops(["note Piano G4 at:end dur:quarter vel:80"])
        assert any("+" in r and "G4" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        notes = sorted(_notes(track), key=lambda n: n.abs_tick)
        assert notes[-1].abs_tick == 2880


# -----------------------------------------------------------------------
# Bulk query (events * / events all)
# -----------------------------------------------------------------------

class TestBulkQuery:
    def test_events_star(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "track add Bass instrument:acoustic-bass",
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Bass E2 at:1.1 dur:half vel:80",
        ])
        result = harness_with_piano.execute_query("events *")
        assert "Piano" in result
        assert "Bass" in result

    def test_events_all(self, harness_with_piano: Harness) -> None:
        harness_with_piano.execute_ops([
            "track add Bass instrument:acoustic-bass",
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Bass E2 at:1.1 dur:half vel:80",
        ])
        result = harness_with_piano.execute_query("events all")
        assert "Piano" in result
        assert "Bass" in result


# -----------------------------------------------------------------------
# Range selector inclusive end
# -----------------------------------------------------------------------

class TestRangeInclusive:
    def test_note_at_end_beat_is_included(self, harness_with_piano: Harness) -> None:
        """A note exactly at the end beat of a range should be included."""
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano E4 at:1.2 dur:quarter vel:80",
        ])
        # Range 1.1-1.2 should include both notes (inclusive end)
        results = harness_with_piano.execute_ops(["remove @track:Piano @range:1.1-1.2"])
        assert any("Removed 2" in r for r in results)

    def test_note_after_end_beat_excluded(self, harness_with_piano: Harness) -> None:
        """A note after the end beat of a range should be excluded."""
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano E4 at:2.1 dur:quarter vel:80",
            "note Piano G4 at:2.2 dur:quarter vel:80",
        ])
        # Range 1.1-2.1 should include notes at 1.1 and 2.1 but not 2.2
        results = harness_with_piano.execute_ops(["remove @track:Piano @range:1.1-2.1"])
        assert any("Removed 2" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        remaining = _notes(track)
        assert len(remaining) == 1
        assert remaining[0].pitch == 67  # G4 at 2.2

    def test_events_query_range_inclusive(self, harness_with_piano: Harness) -> None:
        """Events query with range should also use inclusive end."""
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano E4 at:1.2 dur:quarter vel:80",
            "note Piano G4 at:2.1 dur:quarter vel:80",
        ])
        result = harness_with_piano.execute_query("events Piano 1.1-1.2")
        event_lines = [l for l in result.split("\n") if "vel:" in l]
        assert len(event_lines) == 2  # Both notes at 1.1 and 1.2


# -----------------------------------------------------------------------
# Crescendo / Decrescendo
# -----------------------------------------------------------------------

class TestCrescendoDecrescendo:
    def test_crescendo_linear_interpolation(self, harness_with_piano: Harness) -> None:
        """Crescendo should linearly interpolate velocity across matched notes."""
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano D4 at:1.2 dur:quarter vel:80",
            "note Piano E4 at:1.3 dur:quarter vel:80",
            "note Piano F4 at:1.4 dur:quarter vel:80",
        ])
        results = harness_with_piano.execute_ops(["crescendo @track:Piano from:p to:f"])
        assert any("Crescendo" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        vels = [n.velocity for n in sorted(_notes(track), key=lambda n: n.abs_tick)]
        # p=49, f=96; 4 notes: 49, 65, 81, 96 (linear interpolation rounded)
        assert vels[0] == 49   # from: p
        assert vels[-1] == 96  # to: f
        assert vels[0] < vels[1] < vels[2] < vels[3]

    def test_crescendo_single_note(self, harness_with_piano: Harness) -> None:
        """Crescendo with a single note sets velocity to 'to' value."""
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        results = harness_with_piano.execute_ops(["crescendo @track:Piano from:pp to:ff"])
        assert any("Crescendo" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        assert _notes(track)[0].velocity == 112  # ff

    def test_decrescendo(self, harness_with_piano: Harness) -> None:
        """Decrescendo from ff to pp should decrease velocities."""
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano D4 at:1.2 dur:quarter vel:80",
            "note Piano E4 at:1.3 dur:quarter vel:80",
        ])
        results = harness_with_piano.execute_ops(["decrescendo @track:Piano from:ff to:pp"])
        assert any("Decrescendo" in r for r in results)

        track = harness_with_piano.model.get_track("Piano")
        vels = [n.velocity for n in sorted(_notes(track), key=lambda n: n.abs_tick)]
        assert vels[0] == 112  # ff
        assert vels[-1] == 33  # pp
        assert vels[0] > vels[1] > vels[2]

    def test_crescendo_missing_params(self, harness_with_piano: Harness) -> None:
        """Crescendo without from/to should return error."""
        harness_with_piano.execute_ops(["note Piano C4 at:1.1 dur:quarter vel:80"])
        results = harness_with_piano.execute_ops(["crescendo @track:Piano from:pp"])
        assert any("!" in r for r in results)

        results = harness_with_piano.execute_ops(["crescendo @track:Piano to:ff"])
        assert any("!" in r for r in results)


# -----------------------------------------------------------------------
# Selector negation (@not:)
# -----------------------------------------------------------------------

class TestSelectorNot:
    def test_not_pitch_excludes_matching(self, harness_with_piano: Harness) -> None:
        """@not:pitch:C4 should exclude C4 notes from removal."""
        harness_with_piano.execute_ops([
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Piano D4 at:1.2 dur:quarter vel:80",
            "note Piano E4 at:1.3 dur:quarter vel:80",
        ])
        # Remove all Piano notes EXCEPT C4
        harness_with_piano.execute_ops(["remove @track:Piano @not:pitch:C4"])
        query = harness_with_piano.execute_query("events Piano")
        # C4 should remain
        assert "C" in query
        assert query.count("vel:") == 1

    def test_not_track_excludes_track(self, harness_with_piano: Harness) -> None:
        """@all @not:track:Piano should affect all except Piano."""
        harness_with_piano.execute_ops([
            "track add Bass instrument:acoustic-bass",
            "note Piano C4 at:1.1 dur:quarter vel:80",
            "note Bass C2 at:1.1 dur:quarter vel:80",
        ])
        harness_with_piano.execute_ops(["remove @all @not:track:Piano"])
        piano_events = harness_with_piano.execute_query("events Piano")
        bass_events = harness_with_piano.execute_query("events Bass")
        # Piano notes should survive
        assert "C" in piano_events
        # Bass should be empty
        assert "vel:" not in bass_events or "No" in bass_events


# -----------------------------------------------------------------------
# Custom instruments: raw program numbers, bank select
# -----------------------------------------------------------------------

class TestRawProgramNumbers:
    def test_track_add_with_raw_program(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(["track add Cello program:42"])
        assert any(r.startswith("+") for r in results)
        track = harness_with_song.model.get_track("Cello")
        assert track is not None
        assert track.program == 42
        # Should have reverse-looked-up the GM name into the response message
        assert any("cello" in r for r in results)

    def test_track_add_with_raw_program_no_gm_name(self, harness_with_song: Harness) -> None:
        """program:80 should still work even when name lookup succeeds."""
        results = harness_with_song.execute_ops(["track add Synth program:80"])
        assert any(r.startswith("+") for r in results)
        track = harness_with_song.model.get_track("Synth")
        assert track is not None
        assert track.program == 80

    def test_program_change_with_raw_number(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["program Piano program:73"])
        assert any(r.startswith("+") for r in results)
        track = harness_with_piano.model.get_track("Piano")
        assert track.program == 73
        assert any("flute" in r for r in results)

    def test_program_with_instrument_name_still_works(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["program Piano violin"])
        assert any(r.startswith("+") for r in results)
        track = harness_with_piano.model.get_track("Piano")
        assert track.program == 40

    def test_program_out_of_range(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(["track add Bad program:200"])
        assert any("0-127" in r for r in results)

    def test_program_invalid_value(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(["track add Bad program:abc"])
        assert any("Invalid program" in r for r in results)


class TestBankSelect:
    def test_track_with_bank_msb(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(
            ["track add Pad instrument:pad-2-warm bank:1"]
        )
        assert any(r.startswith("+") for r in results)
        track = harness_with_song.model.get_track("Pad")
        assert track.bank_msb == 1
        assert track.bank_lsb == 0

    def test_track_with_bank_msb_lsb(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(
            ["track add Strings instrument:string-ensemble-1 bank:3.12"]
        )
        assert any(r.startswith("+") for r in results)
        track = harness_with_song.model.get_track("Strings")
        assert track.bank_msb == 3
        assert track.bank_lsb == 12

    def test_program_change_with_bank(self, harness_with_piano: Harness) -> None:
        results = harness_with_piano.execute_ops(["program Piano violin bank:2.5"])
        assert any(r.startswith("+") for r in results)
        track = harness_with_piano.model.get_track("Piano")
        assert track.program == 40
        assert track.bank_msb == 2
        assert track.bank_lsb == 5

    def test_bank_select_display(self, harness_with_song: Harness) -> None:
        harness_with_song.execute_ops(
            ["track add Pad instrument:pad-2-warm bank:1.0"]
        )
        result = harness_with_song.execute_query("describe Pad")
        assert "bank:1.0" in result

    def test_bank_msb_out_of_range(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(
            ["track add Bad instrument:violin bank:200"]
        )
        assert any("0-127" in r for r in results)

    def test_bank_lsb_out_of_range(self, harness_with_song: Harness) -> None:
        results = harness_with_song.execute_ops(
            ["track add Bad instrument:violin bank:1.200"]
        )
        assert any("0-127" in r for r in results)


class TestInstrumentQuery:
    def test_instruments_query_returns_gm(self, harness_with_song: Harness) -> None:
        result = harness_with_song.execute_query("instruments")
        assert "acoustic-grand-piano" in result
        assert "violin" in result

    def test_instruments_query_with_filter(self, harness_with_song: Harness) -> None:
        result = harness_with_song.execute_query("instruments piano")
        assert "piano" in result
        assert "violin" not in result

    def test_instruments_query_no_match(self, harness_with_song: Harness) -> None:
        result = harness_with_song.execute_query("instruments zzz-nonexistent")
        assert "No instruments" in result
