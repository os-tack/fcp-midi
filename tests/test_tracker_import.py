"""Tests for tracker block-mode import via MidiAdapter."""

from __future__ import annotations

import pytest

from fcp_core import EventLog as CoreEventLog, ParsedOp as GenericParsedOp, parse_op

from fcp_midi.adapter import MidiAdapter, SnapshotEvent
from fcp_midi.model.midi_model import MidiModel, pair_notes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def adapter() -> MidiAdapter:
    return MidiAdapter()


@pytest.fixture
def model(adapter: MidiAdapter) -> MidiModel:
    return adapter.create_empty("Test Song", {"tempo": "120", "time-sig": "4/4"})


@pytest.fixture
def model_with_piano(adapter: MidiAdapter, model: MidiModel) -> MidiModel:
    log = CoreEventLog()
    _dispatch(adapter, model, log, "track add Piano instrument:acoustic-grand-piano")
    return model


def _dispatch(adapter: MidiAdapter, model: MidiModel, log: CoreEventLog, raw: str):
    op = parse_op(raw)
    return adapter.dispatch_op(op, model, log)


# ---------------------------------------------------------------------------
# Block-mode import tests
# ---------------------------------------------------------------------------

class TestTrackerImportBlockMode:
    def test_import_basic(self, adapter, model_with_piano):
        log = CoreEventLog()
        model = model_with_piano

        # Start tracker block
        r1 = _dispatch(adapter, model, log, "tracker Piano import at:1.1 res:16th")
        assert r1.success

        # Add step lines (duration-in-token format)
        r2 = _dispatch(adapter, model, log, "Step 01: [C4_v100_4], [E4_v90_4]")
        assert r2.success

        # End tracker block
        r3 = _dispatch(adapter, model, log, "tracker end")
        assert r3.success
        assert "Imported 2 notes" in r3.message

        # Verify notes were added
        ref = model.get_track("Piano")
        notes = pair_notes(ref.track, track_name="Piano")
        assert len(notes) == 2
        pitches = {n.pitch for n in notes}
        assert 60 in pitches  # C4
        assert 64 in pitches  # E4

    def test_import_undo(self, adapter, model_with_piano):
        log = CoreEventLog()
        model = model_with_piano

        # Import some notes (duration-in-token format)
        _dispatch(adapter, model, log, "tracker Piano import at:1.1 res:16th")
        _dispatch(adapter, model, log, "Step 01: [C4_v100_4]")
        r = _dispatch(adapter, model, log, "tracker end")
        assert r.success

        # Verify notes exist
        ref = model.get_track("Piano")
        notes = pair_notes(ref.track, track_name="Piano")
        assert len(notes) == 1

        # Undo via the snapshot event
        # The flush logged one SnapshotEvent
        last_event = log._events[-1]
        assert isinstance(last_event, SnapshotEvent)
        adapter.reverse_event(last_event, model)

        # Notes should be gone
        ref = model.get_track("Piano")
        notes = pair_notes(ref.track, track_name="Piano")
        assert len(notes) == 0


class TestTrackerErrors:
    def test_end_without_start(self, adapter, model_with_piano):
        log = CoreEventLog()
        r = _dispatch(adapter, model_with_piano, log, "tracker end")
        assert not r.success
        assert "without prior" in r.message

    def test_import_bad_step(self, adapter, model_with_piano):
        log = CoreEventLog()
        model = model_with_piano

        _dispatch(adapter, model, log, "tracker Piano import at:1.1 res:16th")
        _dispatch(adapter, model, log, "this is not a valid step line")
        r = _dispatch(adapter, model, log, "tracker end")
        assert not r.success
        assert "Bad step line" in r.message

    def test_import_track_not_found(self, adapter, model_with_piano):
        log = CoreEventLog()
        r = _dispatch(adapter, model_with_piano, log, "tracker Drums import at:1.1 res:16th")
        assert not r.success
        assert "not found" in r.message

    def test_nested_tracker_blocks(self, adapter, model_with_piano):
        log = CoreEventLog()
        model = model_with_piano

        _dispatch(adapter, model, log, "tracker Piano import at:1.1 res:16th")
        r = _dispatch(adapter, model, log, "tracker Piano import at:2.1 res:16th")
        assert not r.success
        assert "Nested" in r.message
