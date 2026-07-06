"""Tests for timing conversions: position <-> ticks <-> seconds."""

from fcp_midi.model.types import TempoChange, TimeSignature
from fcp_midi.model.timing import (
    position_to_ticks,
    seconds_to_ticks,
    ticks_per_beat,
    ticks_to_position,
    ticks_to_seconds,
)

PPQN = 480


# ---------------------------------------------------------------------------
# ticks_per_beat
# ---------------------------------------------------------------------------


class TestTicksPerBeat:
    def test_quarter_note(self):
        # 4/4 => beat = quarter note => ppqn
        assert ticks_per_beat(480, 4) == 480

    def test_eighth_note(self):
        # 6/8 => beat = eighth note => ppqn // 2
        assert ticks_per_beat(480, 8) == 240

    def test_half_note(self):
        # x/2 => beat = half note => ppqn * 2
        assert ticks_per_beat(480, 2) == 960


# ---------------------------------------------------------------------------
# position_to_ticks — basic 4/4
# ---------------------------------------------------------------------------


class TestPositionToTicks44:
    """4/4 at ppqn=480 => measure = 1920 ticks, beat = 480 ticks."""

    sigs = [TimeSignature(absolute_tick=0, numerator=4, denominator=4)]

    def test_measure_1_beat_1(self):
        assert position_to_ticks("1.1", self.sigs, PPQN) == 0

    def test_measure_1_beat_2(self):
        assert position_to_ticks("1.2", self.sigs, PPQN) == 480

    def test_measure_2_beat_1(self):
        assert position_to_ticks("2.1", self.sigs, PPQN) == 1920

    def test_measure_3_beat_3(self):
        # (2 full measures * 1920) + (2 beats * 480)
        assert position_to_ticks("3.3", self.sigs, PPQN) == 2 * 1920 + 2 * 480


# ---------------------------------------------------------------------------
# position_to_ticks — M.B.T format
# ---------------------------------------------------------------------------


class TestPositionToTicksMBT:
    sigs = [TimeSignature(absolute_tick=0, numerator=4, denominator=4)]

    def test_with_sub_ticks(self):
        # "1.1.120" => 0 + 0 + 120 = 120
        assert position_to_ticks("1.1.120", self.sigs, PPQN) == 120

    def test_measure_2_with_sub_ticks(self):
        # "2.1.60" => 1920 + 0 + 60 = 1980
        assert position_to_ticks("2.1.60", self.sigs, PPQN) == 1980


# ---------------------------------------------------------------------------
# position_to_ticks — raw tick format
# ---------------------------------------------------------------------------


class TestPositionToTicksRaw:
    sigs = [TimeSignature(absolute_tick=0, numerator=4, denominator=4)]

    def test_tick_colon(self):
        assert position_to_ticks("tick:960", self.sigs, PPQN) == 960

    def test_tick_zero(self):
        assert position_to_ticks("tick:0", self.sigs, PPQN) == 0


# ---------------------------------------------------------------------------
# position_to_ticks — time signature changes
# ---------------------------------------------------------------------------


class TestPositionToTicksTimeSigChange:
    def test_change_mid_song(self):
        """First 2 measures in 4/4 (1920 each), then switch to 3/4 (1440 each)."""
        sigs = [
            TimeSignature(absolute_tick=0, numerator=4, denominator=4),
            TimeSignature(absolute_tick=3840, numerator=3, denominator=4),
        ]
        # Measure 1: tick 0    (4/4, 1920 ticks)
        # Measure 2: tick 1920 (4/4, 1920 ticks)
        # Measure 3: tick 3840 (3/4, 1440 ticks) — first measure in new sig
        # Measure 4: tick 5280 (3/4, 1440 ticks)
        assert position_to_ticks("1.1", sigs, PPQN) == 0
        assert position_to_ticks("3.1", sigs, PPQN) == 3840
        assert position_to_ticks("4.1", sigs, PPQN) == 5280

    def test_beat_in_new_time_sig(self):
        sigs = [
            TimeSignature(absolute_tick=0, numerator=4, denominator=4),
            TimeSignature(absolute_tick=3840, numerator=3, denominator=4),
        ]
        # Measure 3, beat 2 in 3/4 => 3840 + 480 = 4320
        assert position_to_ticks("3.2", sigs, PPQN) == 4320


# ---------------------------------------------------------------------------
# ticks_to_position — round-trips
# ---------------------------------------------------------------------------


class TestTicksToPosition:
    sigs = [TimeSignature(absolute_tick=0, numerator=4, denominator=4)]

    def test_zero(self):
        assert ticks_to_position(0, self.sigs, PPQN) == "1.1"

    def test_one_beat(self):
        assert ticks_to_position(480, self.sigs, PPQN) == "1.2"

    def test_measure_2(self):
        assert ticks_to_position(1920, self.sigs, PPQN) == "2.1"

    def test_with_sub_ticks(self):
        pos = ticks_to_position(120, self.sigs, PPQN)
        assert pos == "1.1.120"

    def test_round_trip(self):
        for pos in ["1.1", "1.2", "2.1", "3.3", "5.4"]:
            tick = position_to_ticks(pos, self.sigs, PPQN)
            assert ticks_to_position(tick, self.sigs, PPQN) == pos

    def test_round_trip_with_sub(self):
        tick = position_to_ticks("1.1.120", self.sigs, PPQN)
        assert ticks_to_position(tick, self.sigs, PPQN) == "1.1.120"


class TestTicksToPositionTimeSigChange:
    def test_across_sig_change(self):
        sigs = [
            TimeSignature(absolute_tick=0, numerator=4, denominator=4),
            TimeSignature(absolute_tick=3840, numerator=3, denominator=4),
        ]
        assert ticks_to_position(0, sigs, PPQN) == "1.1"
        assert ticks_to_position(3840, sigs, PPQN) == "3.1"
        assert ticks_to_position(5280, sigs, PPQN) == "4.1"


# ---------------------------------------------------------------------------
# ticks_to_seconds — single tempo
# ---------------------------------------------------------------------------


class TestTicksToSeconds:
    def test_120bpm_one_beat(self):
        # At 120 BPM, one quarter = 0.5s, so tick 480 = 0.5s
        tempos = [TempoChange(absolute_tick=0, bpm=120.0)]
        result = ticks_to_seconds(480, tempos, PPQN)
        assert abs(result - 0.5) < 1e-9

    def test_120bpm_two_beats(self):
        tempos = [TempoChange(absolute_tick=0, bpm=120.0)]
        result = ticks_to_seconds(960, tempos, PPQN)
        assert abs(result - 1.0) < 1e-9

    def test_zero_tick(self):
        tempos = [TempoChange(absolute_tick=0, bpm=120.0)]
        assert ticks_to_seconds(0, tempos, PPQN) == 0.0


# ---------------------------------------------------------------------------
# ticks_to_seconds — tempo changes
# ---------------------------------------------------------------------------


class TestTicksToSecondsTempoChange:
    def test_tempo_change_mid_song(self):
        """First 960 ticks at 120 BPM (= 1.0s), then 60 BPM."""
        tempos = [
            TempoChange(absolute_tick=0, bpm=120.0),
            TempoChange(absolute_tick=960, bpm=60.0),
        ]
        # At tick 960: 1.0s elapsed
        assert abs(ticks_to_seconds(960, tempos, PPQN) - 1.0) < 1e-9

        # At tick 960 + 480: 1.0s + (480 ticks at 60 BPM)
        # 60 BPM => 1 beat/sec => 480 ticks/sec => 480 ticks = 1.0s
        result = ticks_to_seconds(1440, tempos, PPQN)
        assert abs(result - 2.0) < 1e-9


# ---------------------------------------------------------------------------
# seconds_to_ticks — round-trips
# ---------------------------------------------------------------------------


class TestSecondsToTicks:
    def test_basic_round_trip(self):
        tempos = [TempoChange(absolute_tick=0, bpm=120.0)]
        for tick in [0, 480, 960, 1920]:
            secs = ticks_to_seconds(tick, tempos, PPQN)
            result = seconds_to_ticks(secs, tempos, PPQN)
            assert result == tick

    def test_round_trip_with_tempo_change(self):
        tempos = [
            TempoChange(absolute_tick=0, bpm=120.0),
            TempoChange(absolute_tick=960, bpm=60.0),
        ]
        for tick in [0, 480, 960, 1440, 1920]:
            secs = ticks_to_seconds(tick, tempos, PPQN)
            result = seconds_to_ticks(secs, tempos, PPQN)
            assert result == tick, f"tick={tick}, secs={secs}, result={result}"
