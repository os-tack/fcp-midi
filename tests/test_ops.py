"""Tests for op handlers — mido-native MidiModel operations."""

from __future__ import annotations

import os
import tempfile

import mido
import pytest

from fcp_midi.model.midi_model import (
    MidiModel,
    NoteIndex,
    delta_to_absolute,
    pair_notes,
)
from fcp_midi.parser.ops import parse_op
from fcp_midi.server.ops_context import MidiOpContext, get_time_sigs
from fcp_midi.server.ops_meta import (
    op_key_sig,
    op_marker,
    op_tempo,
    op_time_sig,
    op_title,
)
from fcp_midi.server.ops_music import (
    op_bend,
    op_cc,
    op_chord,
    op_mute,
    op_note,
    op_program,
    op_solo,
    op_track,
)
from fcp_midi.server.ops_editing import (
    op_copy,
    op_crescendo,
    op_modify,
    op_move,
    op_quantize,
    op_remove,
    op_repeat,
    op_transpose,
    op_velocity,
)


def _make_ctx(**kwargs) -> MidiOpContext:
    model = MidiModel(**kwargs)
    return MidiOpContext(model=model, note_index=NoteIndex())


def _make_ctx_with_track(track_name="Piano", program=0, **kwargs) -> MidiOpContext:
    ctx = _make_ctx(**kwargs)
    ctx.model.add_track(track_name, program=program)
    return ctx


# ===========================================================================
# ops_meta
# ===========================================================================

class TestOpTempo:
    def test_set_tempo(self):
        ctx = _make_ctx()
        op = parse_op("tempo 140")
        result = op_tempo(op, ctx)
        assert result.startswith("+")
        assert "140" in result

        # Verify conductor track has the updated tempo
        for msg in ctx.model.file.tracks[0]:
            if msg.type == "set_tempo":
                assert abs(mido.tempo2bpm(msg.tempo) - 140.0) < 0.1
                break

    def test_missing_tempo(self):
        ctx = _make_ctx()
        op = parse_op("tempo")
        result = op_tempo(op, ctx)
        assert result.startswith("!")

    def test_invalid_tempo(self):
        ctx = _make_ctx()
        op = parse_op("tempo abc")
        result = op_tempo(op, ctx)
        assert result.startswith("!")


class TestOpTimeSig:
    def test_set_time_sig(self):
        ctx = _make_ctx()
        op = parse_op("time-sig 3/4")
        result = op_time_sig(op, ctx)
        assert result.startswith("+")
        assert "3/4" in result

        for msg in ctx.model.file.tracks[0]:
            if msg.type == "time_signature":
                assert msg.numerator == 3
                assert msg.denominator == 4
                break

    def test_invalid_time_sig(self):
        ctx = _make_ctx()
        op = parse_op("time-sig waltz")
        result = op_time_sig(op, ctx)
        assert result.startswith("!")


class TestOpKeySig:
    def test_set_key_sig(self):
        ctx = _make_ctx()
        op = parse_op("key-sig D-major")
        result = op_key_sig(op, ctx)
        assert result.startswith("+")
        assert "D" in result

        found = False
        for msg in ctx.model.file.tracks[0]:
            if msg.type == "key_signature":
                assert msg.key == "D"
                found = True
        assert found

    def test_minor_key(self):
        ctx = _make_ctx()
        op = parse_op("key-sig Am")
        result = op_key_sig(op, ctx)
        assert result.startswith("+")

        for msg in ctx.model.file.tracks[0]:
            if msg.type == "key_signature":
                assert msg.key == "Am"
                break


class TestOpMarker:
    def test_add_marker(self):
        ctx = _make_ctx()
        op = parse_op('marker Chorus at:2.1')
        result = op_marker(op, ctx)
        assert result.startswith("+")
        assert "Chorus" in result

        # Verify marker is in conductor track
        markers = [
            msg for msg in ctx.model.file.tracks[0]
            if msg.type == "marker"
        ]
        assert len(markers) == 1
        assert markers[0].text == "Chorus"


class TestOpTitle:
    def test_set_title(self):
        ctx = _make_ctx()
        op = parse_op('title "My Song"')
        result = op_title(op, ctx)
        assert result.startswith("+")
        assert ctx.model.title == "My Song"

        for msg in ctx.model.file.tracks[0]:
            if msg.type == "track_name":
                assert msg.name == "My Song"
                break


# ===========================================================================
# ops_music
# ===========================================================================

class TestOpNote:
    def test_add_note(self):
        ctx = _make_ctx_with_track()
        op = parse_op("note Piano C4 at:1.1 dur:quarter vel:100")
        result = op_note(op, ctx)
        assert result.startswith("+")
        assert "C4" in result

        notes = ctx.model.get_notes("Piano")
        assert len(notes) == 1
        assert notes[0].pitch == 60  # C4
        assert notes[0].velocity == 100
        assert notes[0].duration_ticks == 480

    def test_add_note_default_velocity(self):
        ctx = _make_ctx_with_track()
        op = parse_op("note Piano C4 at:1.1 dur:quarter")
        op_note(op, ctx)
        notes = ctx.model.get_notes("Piano")
        assert notes[0].velocity == 80  # default

    def test_add_note_bad_track(self):
        ctx = _make_ctx_with_track()
        op = parse_op("note Violin C4")
        result = op_note(op, ctx)
        assert result.startswith("!")

    def test_last_tick_advances(self):
        ctx = _make_ctx_with_track()
        op = parse_op("note Piano C4 at:1.1 dur:quarter")
        op_note(op, ctx)
        assert ctx.last_tick == 480  # 0 + 480


class TestOpChord:
    def test_add_chord(self):
        ctx = _make_ctx_with_track()
        op = parse_op("chord Piano Cmaj at:1.1 dur:quarter")
        result = op_chord(op, ctx)
        assert result.startswith("+")
        assert "3 notes" in result

        notes = ctx.model.get_notes("Piano")
        assert len(notes) == 3
        pitches = {n.pitch for n in notes}
        assert pitches == {60, 64, 67}  # C, E, G


class TestOpTrack:
    def test_add_track(self):
        ctx = _make_ctx()
        op = parse_op("track add Strings program:48")
        result = op_track(op, ctx)
        assert result.startswith("+")
        assert "Strings" in result
        assert "Strings" in ctx.model.tracks

    def test_remove_track(self):
        ctx = _make_ctx_with_track()
        op = parse_op("track remove Piano")
        result = op_track(op, ctx)
        assert result.startswith("+")
        assert "Piano" not in ctx.model.tracks

    def test_add_track_missing_name(self):
        ctx = _make_ctx()
        op = parse_op("track add")
        result = op_track(op, ctx)
        assert result.startswith("!")


class TestOpCC:
    def test_add_cc(self):
        ctx = _make_ctx_with_track()
        op = parse_op("cc Piano volume 100 at:1.1")
        result = op_cc(op, ctx)
        assert result.startswith("+")
        assert "volume" in result

        # Verify CC message is in track
        ref = ctx.model.tracks["Piano"]
        cc_msgs = [msg for msg in ref.track if msg.type == "control_change" and msg.control == 7]
        assert len(cc_msgs) == 1
        assert cc_msgs[0].value == 100


class TestOpBend:
    def test_add_bend(self):
        ctx = _make_ctx_with_track()
        op = parse_op("bend Piano 4096 at:1.1")
        result = op_bend(op, ctx)
        assert result.startswith("+")

        ref = ctx.model.tracks["Piano"]
        bends = [msg for msg in ref.track if msg.type == "pitchwheel"]
        assert len(bends) == 1
        assert bends[0].pitch == 4096

    def test_bend_out_of_range(self):
        ctx = _make_ctx_with_track()
        op = parse_op("bend Piano 9999 at:1.1")
        result = op_bend(op, ctx)
        assert result.startswith("!")


class TestOpMuteSolo:
    def test_mute_toggle(self):
        ctx = _make_ctx_with_track()
        op = parse_op("mute Piano")
        result = op_mute(op, ctx)
        assert "muted" in result
        assert ctx.model.tracks["Piano"].mute is True

        result = op_mute(op, ctx)
        assert "unmuted" in result
        assert ctx.model.tracks["Piano"].mute is False

    def test_solo_toggle(self):
        ctx = _make_ctx_with_track()
        op = parse_op("solo Piano")
        result = op_solo(op, ctx)
        assert "solo on" in result
        assert ctx.model.tracks["Piano"].solo is True


class TestOpProgram:
    def test_change_program(self):
        ctx = _make_ctx_with_track()
        op = parse_op("program Piano program:25")
        result = op_program(op, ctx)
        assert result.startswith("+")
        assert ctx.model.tracks["Piano"].program == 25

        # Verify the program_change message updated
        ref = ctx.model.tracks["Piano"]
        prog_msgs = [msg for msg in ref.track if msg.type == "program_change"]
        assert len(prog_msgs) == 1
        assert prog_msgs[0].program == 25


# ===========================================================================
# ops_editing
# ===========================================================================

class TestOpRemove:
    def test_remove_notes(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480)
        ctx.model.add_note("Piano", 64, 480, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("remove @track:Piano @pitch:C4")
        result = op_remove(op, ctx)
        assert result.startswith("+")
        assert "1 note" in result

        notes = ctx.model.get_notes("Piano")
        assert len(notes) == 1
        assert notes[0].pitch == 64

    def test_remove_no_match(self):
        ctx = _make_ctx_with_track()
        op = parse_op("remove @track:Piano @pitch:C4")
        result = op_remove(op, ctx)
        assert result.startswith("!")


class TestOpMove:
    def test_move_notes(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("move @track:Piano to:2.1")
        result = op_move(op, ctx)
        assert result.startswith("+")

        notes = ctx.model.get_notes("Piano")
        assert len(notes) == 1
        # 2.1 in 4/4 = tick 1920
        assert notes[0].abs_tick == 1920


class TestOpCopy:
    def test_copy_notes(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("copy @track:Piano to:2.1")
        result = op_copy(op, ctx)
        assert result.startswith("+")

        notes = ctx.model.get_notes("Piano")
        assert len(notes) == 2
        ticks = sorted(n.abs_tick for n in notes)
        assert ticks == [0, 1920]


class TestOpTranspose:
    def test_transpose_up(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("transpose +5 @track:Piano")
        result = op_transpose(op, ctx)
        assert result.startswith("+")
        assert "up" in result

        notes = ctx.model.get_notes("Piano")
        assert len(notes) == 1
        assert notes[0].pitch == 65

    def test_transpose_out_of_range(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 125, 0, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("transpose +10 @track:Piano")
        result = op_transpose(op, ctx)
        # Note would be 135 > 127, so it should be skipped
        assert "0 note" in result


class TestOpVelocity:
    def test_velocity_adjust(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480, velocity=80)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("velocity +20 @track:Piano")
        result = op_velocity(op, ctx)
        assert result.startswith("+")

        notes = ctx.model.get_notes("Piano")
        assert notes[0].velocity == 100

    def test_velocity_clamp(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480, velocity=120)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("velocity +20 @track:Piano")
        op_velocity(op, ctx)
        notes = ctx.model.get_notes("Piano")
        assert notes[0].velocity == 127  # clamped


class TestOpQuantize:
    def test_quantize_to_quarter(self):
        ctx = _make_ctx_with_track()
        # Note slightly off the quarter grid
        ctx.model.add_note("Piano", 60, 50, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("quantize @track:Piano grid:quarter")
        result = op_quantize(op, ctx)
        assert result.startswith("+")

        notes = ctx.model.get_notes("Piano")
        assert notes[0].abs_tick == 0  # quantized to 0


class TestOpModify:
    def test_modify_velocity(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480, velocity=80)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("modify @track:Piano vel:120")
        result = op_modify(op, ctx)
        assert result.startswith("+")

        notes = ctx.model.get_notes("Piano")
        assert notes[0].velocity == 120

    def test_modify_pitch(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("modify @track:Piano pitch:E4")
        result = op_modify(op, ctx)
        assert result.startswith("+")

        notes = ctx.model.get_notes("Piano")
        assert notes[0].pitch == 64  # E4


class TestOpRepeat:
    def test_repeat_once(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480)
        ctx.model.add_note("Piano", 64, 480, 480)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("repeat @track:Piano count:1")
        result = op_repeat(op, ctx)
        assert result.startswith("+")
        assert "2 added" in result

        notes = ctx.model.get_notes("Piano")
        assert len(notes) == 4


class TestOpCrescendo:
    def test_crescendo(self):
        ctx = _make_ctx_with_track()
        ctx.model.add_note("Piano", 60, 0, 480, velocity=50)
        ctx.model.add_note("Piano", 64, 480, 480, velocity=50)
        ctx.model.add_note("Piano", 67, 960, 480, velocity=50)
        ctx.note_index.rebuild(ctx.model)

        op = parse_op("crescendo @track:Piano from:pp to:ff")
        result = op_crescendo(op, ctx)
        assert result.startswith("+")
        assert "3 note" in result

        notes = sorted(ctx.model.get_notes("Piano"), key=lambda n: n.abs_tick)
        # pp=33, ff=112
        assert notes[0].velocity == 33
        assert notes[2].velocity == 112
        # Middle note should be between
        assert 33 < notes[1].velocity < 112


# ===========================================================================
# Round-trip: handler → save → load → verify
# ===========================================================================

class TestRoundTrip:
    def test_note_round_trip(self):
        ctx = _make_ctx_with_track()
        op = parse_op("note Piano C4 at:1.1 dur:quarter vel:100")
        op_note(op, ctx)

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            ctx.model.save(path)
            loaded = MidiModel.load(path)
            notes = loaded.get_notes("Piano")
            assert len(notes) == 1
            assert notes[0].pitch == 60
            assert notes[0].velocity == 100
        finally:
            os.unlink(path)

    def test_meta_round_trip(self):
        ctx = _make_ctx()
        op_tempo(parse_op("tempo 90"), ctx)
        op_time_sig(parse_op("time-sig 6/8"), ctx)
        op_title(parse_op('title "Test Song"'), ctx)

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            ctx.model.save(path)
            loaded = MidiModel.load(path)

            assert loaded.title == "Test Song"

            # Check tempo
            for msg in loaded.file.tracks[0]:
                if msg.type == "set_tempo":
                    assert abs(mido.tempo2bpm(msg.tempo) - 90.0) < 0.1
                elif msg.type == "time_signature":
                    assert msg.numerator == 6
                    assert msg.denominator == 8
        finally:
            os.unlink(path)
