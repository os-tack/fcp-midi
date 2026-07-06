"""Comprehensive tests for the fcp_midi.parser package."""

from __future__ import annotations

import pytest

from fcp_midi.errors import ValidationError
from fcp_midi.model.types import Pitch, TimeSignature
from fcp_midi.parser.tokenizer import is_key_value, parse_key_value, tokenize
from fcp_midi.parser.pitch import parse_pitch
from fcp_midi.parser.duration import parse_duration
from fcp_midi.parser.position import parse_position
from fcp_midi.parser.chord import parse_chord
from fcp_midi.parser.selector import Selector, parse_selectors
from fcp_midi.parser.ops import ParsedOp, ParseError, parse_op


# =========================================================================
# Tokenizer
# =========================================================================

class TestTokenize:
    def test_simple_tokens(self):
        assert tokenize("note Piano C4") == ["note", "Piano", "C4"]

    def test_double_quoted_string(self):
        result = tokenize('note Piano "My Track" at:1.1')
        assert result == ["note", "Piano", "My Track", "at:1.1"]

    def test_single_quoted_string(self):
        result = tokenize("note Piano 'My Track' at:1.1")
        assert result == ["note", "Piano", "My Track", "at:1.1"]

    def test_key_value_preserved(self):
        result = tokenize("note Piano C4 at:1.1 dur:quarter vel:80")
        assert "at:1.1" in result
        assert "dur:quarter" in result
        assert "vel:80" in result

    def test_selector_preserved(self):
        result = tokenize("remove @track:Piano @range:1.1-2.1")
        assert "@track:Piano" in result
        assert "@range:1.1-2.1" in result

    def test_sharp_pitch_not_treated_as_comment(self):
        """Regression: shlex treated # as comment, eating C#4 and everything after."""
        result = tokenize("note Piano C#4 at:1.1 dur:quarter")
        assert result == ["note", "Piano", "C#4", "at:1.1", "dur:quarter"]

    def test_empty_string(self):
        assert tokenize("") == []

    def test_whitespace_only(self):
        assert tokenize("   ") == []

    def test_mixed_quotes(self):
        result = tokenize("""note "Track A" 'some value' plain""")
        assert result == ["note", "Track A", "some value", "plain"]


class TestIsKeyValue:
    def test_key_value(self):
        assert is_key_value("at:1.1") is True
        assert is_key_value("dur:quarter") is True

    def test_selector_not_key_value(self):
        assert is_key_value("@track:Piano") is False

    def test_arrow_not_key_value(self):
        assert is_key_value("->") is False
        assert is_key_value("A->B") is False

    def test_no_colon(self):
        assert is_key_value("plain") is False
        assert is_key_value("note") is False

    def test_midi_prefix(self):
        assert is_key_value("midi:60") is True

    def test_ticks_prefix(self):
        assert is_key_value("ticks:480") is True


class TestParseKeyValue:
    def test_simple(self):
        assert parse_key_value("at:1.1") == ("at", "1.1")

    def test_value_with_colon(self):
        # Only splits on first colon
        assert parse_key_value("url:http://example.com") == (
            "url",
            "http://example.com",
        )

    def test_empty_value(self):
        assert parse_key_value("key:") == ("key", "")


# =========================================================================
# Pitch
# =========================================================================

class TestParsePitch:
    def test_middle_c(self):
        p = parse_pitch("C4")
        assert p == Pitch(name="C", accidental="", octave=4, midi_number=60)

    def test_d_sharp_5(self):
        p = parse_pitch("D#5")
        assert p == Pitch(name="D", accidental="#", octave=5, midi_number=75)

    def test_b_flat_3(self):
        p = parse_pitch("Bb3")
        assert p == Pitch(name="B", accidental="b", octave=3, midi_number=58)

    def test_f_double_sharp_4(self):
        p = parse_pitch("F##4")
        assert p == Pitch(name="F", accidental="##", octave=4, midi_number=67)

    def test_c_double_flat_4(self):
        # Cbb4 = two semitones below C4 = MIDI 58
        p = parse_pitch("Cbb4")
        assert p.midi_number == 58

    def test_midi_60(self):
        p = parse_pitch("midi:60")
        assert p.midi_number == 60
        assert p.name == "C"
        assert p.accidental == ""
        assert p.octave == 4

    def test_midi_0(self):
        p = parse_pitch("midi:0")
        assert p.midi_number == 0
        assert p.name == "C"
        assert p.octave == -1

    def test_midi_127(self):
        p = parse_pitch("midi:127")
        assert p.midi_number == 127

    def test_a4_is_69(self):
        p = parse_pitch("A4")
        assert p.midi_number == 69

    def test_low_octave(self):
        p = parse_pitch("C0")
        assert p.midi_number == 12

    def test_negative_octave(self):
        p = parse_pitch("C-1")
        assert p.midi_number == 0

    def test_invalid_pitch(self):
        with pytest.raises(ValidationError):
            parse_pitch("X4")

    def test_invalid_midi(self):
        with pytest.raises(ValidationError):
            parse_pitch("midi:200")

    def test_no_octave(self):
        with pytest.raises(ValidationError):
            parse_pitch("C")

    def test_lowercase_note(self):
        p = parse_pitch("c4")
        assert p.name == "C"
        assert p.midi_number == 60

    def test_e_flat_3(self):
        p = parse_pitch("Eb3")
        assert p == Pitch(name="E", accidental="b", octave=3, midi_number=51)

    def test_g_sharp_2(self):
        p = parse_pitch("G#2")
        assert p == Pitch(name="G", accidental="#", octave=2, midi_number=44)


# =========================================================================
# Duration
# =========================================================================

class TestParseDuration:
    def test_whole(self):
        assert parse_duration("whole") == 1920

    def test_half(self):
        assert parse_duration("half") == 960

    def test_quarter(self):
        assert parse_duration("quarter") == 480

    def test_eighth(self):
        assert parse_duration("eighth") == 240

    def test_sixteenth(self):
        assert parse_duration("sixteenth") == 120

    def test_32nd(self):
        assert parse_duration("32nd") == 60

    def test_alias_1n(self):
        assert parse_duration("1n") == 1920

    def test_alias_2n(self):
        assert parse_duration("2n") == 960

    def test_alias_4n(self):
        assert parse_duration("4n") == 480

    def test_alias_8n(self):
        assert parse_duration("8n") == 240

    def test_alias_16n(self):
        assert parse_duration("16n") == 120

    def test_alias_32n(self):
        assert parse_duration("32n") == 60

    def test_dotted_quarter(self):
        assert parse_duration("dotted-quarter") == 720

    def test_dotted_half(self):
        assert parse_duration("dotted-half") == 1440

    def test_dotted_eighth(self):
        assert parse_duration("dotted-eighth") == 360

    def test_triplet_eighth(self):
        assert parse_duration("triplet-eighth") == 160

    def test_triplet_quarter(self):
        assert parse_duration("triplet-quarter") == 320

    def test_raw_ticks(self):
        assert parse_duration("ticks:360") == 360

    def test_raw_ticks_zero(self):
        assert parse_duration("ticks:0") == 0

    def test_custom_ppqn(self):
        assert parse_duration("quarter", ppqn=960) == 960
        assert parse_duration("whole", ppqn=960) == 3840

    def test_unknown_duration(self):
        with pytest.raises(ValidationError):
            parse_duration("bogus")

    def test_invalid_ticks(self):
        with pytest.raises(ValidationError):
            parse_duration("ticks:abc")

    def test_dotted_alias(self):
        # dotted-4n should work: 4n resolves to quarter, then dotted
        assert parse_duration("dotted-4n") == 720

    def test_triplet_alias(self):
        assert parse_duration("triplet-8n") == 160


# =========================================================================
# Position
# =========================================================================

class TestParsePosition:
    def test_first_beat(self):
        # 1.1 = tick 0
        assert parse_position("1.1") == 0

    def test_second_beat(self):
        # 1.2 = tick 480 (in 4/4)
        assert parse_position("1.2") == 480

    def test_second_measure(self):
        # 2.1 in 4/4 = tick 1920
        assert parse_position("2.1") == 1920

    def test_measure_beat_tick(self):
        # 2.1.120 = 1920 + 0 + 120 = 2040
        assert parse_position("2.1.120") == 2040

    def test_raw_tick(self):
        assert parse_position("tick:1000") == 1000

    def test_raw_tick_zero(self):
        assert parse_position("tick:0") == 0

    def test_3_4_time(self):
        ts = [TimeSignature(absolute_tick=0, numerator=3, denominator=4)]
        # In 3/4, one measure = 3 * 480 = 1440
        assert parse_position("2.1", time_sigs=ts) == 1440

    def test_6_8_time(self):
        ts = [TimeSignature(absolute_tick=0, numerator=6, denominator=8)]
        # In 6/8, beat = ppqn/2 = 240, measure = 6 * 240 = 1440
        assert parse_position("1.2", time_sigs=ts) == 240
        assert parse_position("2.1", time_sigs=ts) == 1440

    def test_time_sig_change(self):
        # 2 measures of 4/4 then 3/4
        ts = [
            TimeSignature(absolute_tick=0, numerator=4, denominator=4),
            TimeSignature(absolute_tick=3840, numerator=3, denominator=4),
        ]
        # Measure 1 = 0, Measure 2 = 1920, Measure 3 = 3840 (still 4/4)
        # After tick 3840 it's 3/4, so measure 4 = 3840 + 1440 = 5280
        # Wait: measure 3 starts at tick 3840, which is where time sig changes.
        # The time sig at tick 3840 takes effect, so measure 3 = 3840 (4/4 length),
        # but the ts change is at 3840 = start of measure 3 in 4/4.
        # So measure 1: 0-1919 (4/4), measure 2: 1920-3839 (4/4)
        # At tick 3840 the time sig changes to 3/4.
        # Measure 3 starts at 3840 with 3/4 = 1440 ticks
        assert parse_position("3.1", time_sigs=ts) == 3840

    def test_invalid_format(self):
        with pytest.raises(ValidationError):
            parse_position("abc")

    def test_measure_zero(self):
        with pytest.raises(ValidationError):
            parse_position("0.1")

    def test_beat_zero(self):
        with pytest.raises(ValidationError):
            parse_position("1.0")

    def test_no_time_sigs_defaults_to_4_4(self):
        # Should work the same as 4/4
        assert parse_position("2.1", time_sigs=None) == 1920
        assert parse_position("2.1", time_sigs=[]) == 1920

    def test_relative_plus_quarter(self):
        # +quarter from tick 480 = 480 + 480 = 960
        assert parse_position("+quarter", ppqn=480, reference_tick=480) == 960

    def test_relative_minus_eighth(self):
        # -eighth from tick 960 = 960 - 240 = 720
        assert parse_position("-eighth", ppqn=480, reference_tick=960) == 720

    def test_relative_end(self):
        assert parse_position("end", song_end_tick=3840) == 3840

    def test_relative_plus_no_reference_raises(self):
        with pytest.raises(ValueError, match="Relative position requires reference"):
            parse_position("+quarter", ppqn=480)

    def test_end_no_song_context_raises(self):
        with pytest.raises(ValueError, match="'end' requires song context"):
            parse_position("end")

    def test_relative_plus_half(self):
        # +half from tick 0 = 0 + 960 = 960
        assert parse_position("+half", ppqn=480, reference_tick=0) == 960

    def test_relative_minus_clamps_to_zero(self):
        # -whole from tick 100 would be negative, clamps to 0
        assert parse_position("-whole", ppqn=480, reference_tick=100) == 0


# =========================================================================
# Chord
# =========================================================================

class TestParseChord:
    def test_c_major(self):
        pitches = parse_chord("Cmaj")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 64, 67]  # C4, E4, G4

    def test_a_minor(self):
        pitches = parse_chord("Am", octave=3)
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [57, 60, 64]  # A3, C4, E4

    def test_a_min_alias(self):
        pitches = parse_chord("Amin", octave=3)
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [57, 60, 64]

    def test_a_minor_7(self):
        pitches = parse_chord("Am7", octave=3)
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [57, 60, 64, 67]  # A3, C4, E4, G4

    def test_d_minor_slash_f(self):
        pitches = parse_chord("Dm/F", octave=4)
        midi_numbers = [p.midi_number for p in pitches]
        # Dm = [D4(62), F4(65), A4(69)]
        # Bass F should be below D4 → F3(53)
        # Remove F4 from chord, insert F3 at front
        assert midi_numbers[0] == 53  # F3 bass
        assert 62 in midi_numbers  # D4
        assert 69 in midi_numbers  # A4

    def test_c7(self):
        pitches = parse_chord("C7")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 64, 67, 70]  # C4, E4, G4, Bb4

    def test_cmaj7(self):
        pitches = parse_chord("Cmaj7")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 64, 67, 71]  # C4, E4, G4, B4

    def test_cmin7(self):
        pitches = parse_chord("Cmin7")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 67, 70]

    def test_cm7(self):
        pitches = parse_chord("Cm7")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 67, 70]

    def test_cdim(self):
        pitches = parse_chord("Cdim")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 66]

    def test_caug(self):
        pitches = parse_chord("Caug")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 64, 68]

    def test_csus2(self):
        pitches = parse_chord("Csus2")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 62, 67]

    def test_csus4(self):
        pitches = parse_chord("Csus4")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 65, 67]

    def test_cadd9(self):
        pitches = parse_chord("Cadd9")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 64, 67, 74]

    def test_cmin7b5(self):
        pitches = parse_chord("Cmin7b5")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 66, 70]

    def test_cm7b5(self):
        pitches = parse_chord("Cm7b5")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 66, 70]

    def test_cdim7(self):
        pitches = parse_chord("Cdim7")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 66, 69]

    def test_c9(self):
        pitches = parse_chord("C9")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 64, 67, 70, 74]

    def test_cmin9(self):
        pitches = parse_chord("Cmin9")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 67, 70, 74]

    def test_cm9(self):
        pitches = parse_chord("Cm9")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 67, 70, 74]

    def test_c6(self):
        pitches = parse_chord("C6")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 64, 67, 69]

    def test_cmin6(self):
        pitches = parse_chord("Cmin6")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 67, 69]

    def test_cm6(self):
        pitches = parse_chord("Cm6")
        midi_numbers = [p.midi_number for p in pitches]
        assert midi_numbers == [60, 63, 67, 69]

    def test_sharp_root(self):
        pitches = parse_chord("F#maj")
        midi_numbers = [p.midi_number for p in pitches]
        # F#4 = 66, A#4 = 70, C#5 = 73
        assert midi_numbers == [66, 70, 73]

    def test_flat_root(self):
        pitches = parse_chord("Bbmaj")
        midi_numbers = [p.midi_number for p in pitches]
        # Bb4 = 70, D5 = 74, F5 = 77
        assert midi_numbers == [70, 74, 77]

    def test_invalid_root(self):
        with pytest.raises(ValidationError):
            parse_chord("Xmaj")

    def test_unknown_quality(self):
        with pytest.raises(ValidationError):
            parse_chord("Cxyz")


# =========================================================================
# Selector
# =========================================================================

class TestParseSelectors:
    def test_track_selector(self):
        result = parse_selectors(["@track:Piano"])
        assert len(result) == 1
        assert result[0] == Selector(type="track", value="Piano")

    def test_channel_selector(self):
        result = parse_selectors(["@channel:0"])
        assert result[0] == Selector(type="channel", value="0")

    def test_range_selector(self):
        result = parse_selectors(["@range:1.1-2.1"])
        assert result[0] == Selector(type="range", value="1.1-2.1")

    def test_pitch_selector(self):
        result = parse_selectors(["@pitch:C4"])
        assert result[0] == Selector(type="pitch", value="C4")

    def test_velocity_selector(self):
        result = parse_selectors(["@velocity:60-100"])
        assert result[0] == Selector(type="velocity", value="60-100")

    def test_all_selector(self):
        result = parse_selectors(["@all"])
        assert result[0] == Selector(type="all", value="")

    def test_recent_selector(self):
        result = parse_selectors(["@recent"])
        assert result[0] == Selector(type="recent", value="")

    def test_recent_with_count(self):
        result = parse_selectors(["@recent:5"])
        assert result[0] == Selector(type="recent", value="5")

    def test_multiple_selectors(self):
        result = parse_selectors(
            ["@track:Piano", "@range:1.1-2.1", "non-selector"]
        )
        assert len(result) == 2
        assert result[0].type == "track"
        assert result[1].type == "range"

    def test_no_selectors(self):
        result = parse_selectors(["note", "Piano", "C4"])
        assert result == []

    def test_mixed_tokens(self):
        result = parse_selectors(["remove", "@all", "at:1.1"])
        assert len(result) == 1
        assert result[0].type == "all"

    def test_not_pitch_selector(self):
        result = parse_selectors(["@not:pitch:C4"])
        assert len(result) == 1
        assert result[0].type == "pitch"
        assert result[0].value == "C4"
        assert result[0].negated is True

    def test_not_track_selector(self):
        result = parse_selectors(["@not:track:Piano"])
        assert len(result) == 1
        assert result[0].type == "track"
        assert result[0].value == "Piano"
        assert result[0].negated is True

    def test_mixed_positive_and_negated(self):
        result = parse_selectors(["@track:Piano", "@not:pitch:C4"])
        assert len(result) == 2
        assert result[0].negated is False
        assert result[1].negated is True
        assert result[1].type == "pitch"
        assert result[1].value == "C4"

    def test_positive_selectors_not_negated(self):
        result = parse_selectors(["@track:Piano", "@range:1.1-4.4"])
        assert all(not s.negated for s in result)


# =========================================================================
# Ops
# =========================================================================

class TestParseOp:
    def test_note_op(self):
        result = parse_op("note Piano C4 at:1.1 dur:quarter vel:80")
        assert isinstance(result, ParsedOp)
        assert result.verb == "note"
        assert result.target == "Piano"
        assert result.params["pitch"] == "C4"
        assert result.params["at"] == "1.1"
        assert result.params["dur"] == "quarter"
        assert result.params["vel"] == "80"

    def test_chord_op(self):
        result = parse_op("chord Piano Cmaj at:1.1 dur:half")
        assert isinstance(result, ParsedOp)
        assert result.verb == "chord"
        assert result.target == "Piano"
        assert result.params["chord"] == "Cmaj"
        assert result.params["at"] == "1.1"
        assert result.params["dur"] == "half"

    def test_track_add(self):
        result = parse_op("track add Strings instrument:violin ch:1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "track"
        assert result.target == "add"
        assert result.params["name"] == "Strings"
        assert result.params["instrument"] == "violin"
        assert result.params["ch"] == "1"

    def test_track_remove(self):
        result = parse_op("track remove Drums")
        assert isinstance(result, ParsedOp)
        assert result.verb == "track"
        assert result.target == "remove"
        assert result.params["name"] == "Drums"

    def test_cc_op(self):
        result = parse_op("cc Piano volume 100 at:1.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "cc"
        assert result.target == "Piano"
        assert result.params["cc_name"] == "volume"
        assert result.params["cc_value"] == "100"
        assert result.params["at"] == "1.1"

    def test_bend_op(self):
        result = parse_op("bend Piano 4000 at:2.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "bend"
        assert result.target == "Piano"
        assert result.params["value"] == "4000"
        assert result.params["at"] == "2.1"

    def test_tempo_op(self):
        result = parse_op("tempo 140 at:5.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "tempo"
        assert result.target == "140"
        assert result.params["at"] == "5.1"

    def test_time_sig_op(self):
        result = parse_op("time-sig 3/4 at:5.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "time-sig"
        assert result.target == "3/4"

    def test_key_sig_op(self):
        result = parse_op("key-sig Gmajor at:1.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "key-sig"
        assert result.target == "Gmajor"

    def test_marker_op(self):
        result = parse_op('marker "Verse 1" at:1.1')
        assert isinstance(result, ParsedOp)
        assert result.verb == "marker"
        assert result.target == "Verse 1"

    def test_title_op(self):
        result = parse_op('title "My Song"')
        assert isinstance(result, ParsedOp)
        assert result.verb == "title"
        assert result.target == "My Song"

    def test_remove_with_selectors(self):
        result = parse_op("remove @track:Piano @range:1.1-2.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "remove"
        assert len(result.selectors) == 2
        assert result.selectors[0].type == "track"
        assert result.selectors[1].type == "range"

    def test_move_op(self):
        result = parse_op("move @recent to:3.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "move"
        assert result.params["to"] == "3.1"
        assert len(result.selectors) == 1

    def test_copy_op(self):
        result = parse_op("copy @track:Piano @range:1.1-4.1 to:5.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "copy"
        assert result.params["to"] == "5.1"
        assert len(result.selectors) == 2

    def test_transpose_op(self):
        result = parse_op("transpose @track:Piano +5")
        assert isinstance(result, ParsedOp)
        assert result.verb == "transpose"
        assert result.target == "+5"
        assert result.selectors[0].type == "track"

    def test_velocity_op(self):
        result = parse_op("velocity @all -10")
        assert isinstance(result, ParsedOp)
        assert result.verb == "velocity"
        assert result.target == "-10"

    def test_quantize_op(self):
        result = parse_op("quantize @track:Piano grid:eighth strength:80")
        assert isinstance(result, ParsedOp)
        assert result.verb == "quantize"
        assert result.params["grid"] == "eighth"
        assert result.params["strength"] == "80"

    def test_mute_op(self):
        result = parse_op("mute Piano")
        assert isinstance(result, ParsedOp)
        assert result.verb == "mute"
        assert result.target == "Piano"

    def test_solo_op(self):
        result = parse_op("solo Drums")
        assert isinstance(result, ParsedOp)
        assert result.verb == "solo"
        assert result.target == "Drums"

    def test_program_op(self):
        result = parse_op("program Piano Harpsichord at:1.1")
        assert isinstance(result, ParsedOp)
        assert result.verb == "program"
        assert result.target == "Piano"
        assert result.params["instrument"] == "Harpsichord"

    def test_empty_string(self):
        result = parse_op("")
        assert isinstance(result, ParseError)

    def test_quoted_track_name(self):
        result = parse_op('note "Lead Synth" C4 at:1.1')
        assert isinstance(result, ParsedOp)
        assert result.target == "Lead Synth"
        assert result.params["pitch"] == "C4"

    def test_unknown_verb(self):
        result = parse_op("frobnicate something")
        assert isinstance(result, ParsedOp)
        assert result.verb == "frobnicate"
        assert result.target == "something"

    def test_verb_case_insensitive(self):
        result = parse_op("NOTE Piano C4")
        assert isinstance(result, ParsedOp)
        assert result.verb == "note"

    def test_note_without_key_values(self):
        result = parse_op("note Piano C4")
        assert isinstance(result, ParsedOp)
        assert result.verb == "note"
        assert result.target == "Piano"
        assert result.params["pitch"] == "C4"
