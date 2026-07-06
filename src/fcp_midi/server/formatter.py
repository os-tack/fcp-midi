"""Compact response formatting for MIDI FCP tool outputs."""

from __future__ import annotations


def format_result(
    success: bool,
    message: str,
    suggestion: str | None = None,
) -> str:
    """Format a mutation result line.

    Success: ``+ message``
    Error:   ``! message`` with optional ``  try: suggestion``
    """
    if success:
        return f"+ {message}"
    line = f"! {message}"
    if suggestion:
        line += f"\n  try: {suggestion}"
    return line
