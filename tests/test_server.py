"""Tests for server layer: formatter and reference card."""

from __future__ import annotations

from fcp_midi.server.formatter import format_result
from fcp_midi.server.reference_card import REFERENCE_CARD


# -----------------------------------------------------------------------
# Formatter tests
# -----------------------------------------------------------------------

class TestFormatResult:
    def test_success(self) -> None:
        r = format_result(True, "done")
        assert r.startswith("+")
        assert "done" in r

    def test_failure(self) -> None:
        r = format_result(False, "oops")
        assert r.startswith("!")
        assert "oops" in r

    def test_failure_with_hint(self) -> None:
        r = format_result(False, "oops", "try this")
        assert "try" in r


# -----------------------------------------------------------------------
# Reference card
# -----------------------------------------------------------------------

class TestReferenceCard:
    def test_non_empty(self) -> None:
        assert len(REFERENCE_CARD) > 100

    def test_contains_verbs(self) -> None:
        for verb in ("note", "chord", "track", "cc", "bend", "tempo",
                     "time-sig", "key-sig", "marker", "remove", "move",
                     "copy", "transpose", "velocity", "quantize", "modify",
                     "repeat", "crescendo", "decrescendo", "mute", "solo",
                     "program", "title"):
            assert verb in REFERENCE_CARD, f"Verb {verb!r} not found in reference card"

    def test_dispatch_verbs_covered_by_registry(self) -> None:
        """Every verb in the dispatch table must appear in the verb registry."""
        from fcp_midi.server.verb_registry import VERB_MAP
        dispatch_verbs = {
            "note", "chord", "track", "cc", "bend", "tempo",
            "time-sig", "key-sig", "marker", "title",
            "remove", "move", "copy", "transpose", "velocity",
            "quantize", "mute", "solo", "program", "modify",
            "repeat", "crescendo", "decrescendo",
        }
        for verb in dispatch_verbs:
            assert verb in VERB_MAP, f"Verb {verb!r} missing from verb registry"

    def test_contains_not_selector(self) -> None:
        assert "@not:" in REFERENCE_CARD
