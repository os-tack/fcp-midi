"""MidiModel — mido-native source of truth for MIDI compositions.

Wraps a mido.MidiFile with a thin index (TrackRef) for fast label-based
lookup. All timing inside mido tracks uses delta ticks; conversion
utilities bridge between absolute ticks (used by FCP ops) and deltas.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO

import mido


# ---------------------------------------------------------------------------
# TrackRef — thin index entry
# ---------------------------------------------------------------------------

@dataclass
class TrackRef:
    """Points into a mido.MidiTrack inside the MidiFile."""

    track: mido.MidiTrack
    name: str
    channel: int
    program: int = 0
    bank_msb: int = 0
    bank_lsb: int = 0
    mute: bool = False
    solo: bool = False


# ---------------------------------------------------------------------------
# NoteRef — thin index entry for note pairs
# ---------------------------------------------------------------------------

@dataclass
class NoteRef:
    """Index entry pointing at a note_on/note_off pair inside a track."""

    track_name: str
    note_on_idx: int       # index of note_on in track message list
    note_off_idx: int      # index of note_off in track message list
    abs_tick: int           # absolute tick of note_on
    duration_ticks: int     # note_off tick - note_on tick
    pitch: int              # MIDI note number (0-127)
    velocity: int           # velocity (1-127)
    channel: int            # MIDI channel (0-15)


# ---------------------------------------------------------------------------
# Tick conversion utilities
# ---------------------------------------------------------------------------

def absolute_to_delta(
    messages: list[tuple[int, mido.Message]],
) -> list[mido.Message]:
    """Convert (absolute_tick, message) pairs to messages with delta times.

    The input list is sorted by absolute tick before processing so callers
    don't need to pre-sort.
    """
    sorted_msgs = sorted(messages, key=lambda pair: pair[0])
    prev = 0
    result: list[mido.Message] = []
    for abs_tick, msg in sorted_msgs:
        msg = msg.copy(time=abs_tick - prev)
        result.append(msg)
        prev = abs_tick
    return result


def delta_to_absolute(
    track: mido.MidiTrack,
) -> list[tuple[int, mido.Message]]:
    """Walk a track converting delta times to (absolute_tick, message) pairs."""
    result: list[tuple[int, mido.Message]] = []
    abs_tick = 0
    for msg in track:
        abs_tick += msg.time
        result.append((abs_tick, msg))
    return result


def insert_message_at_tick(
    track: mido.MidiTrack,
    msg: mido.Message,
    absolute_tick: int,
) -> None:
    """Insert *msg* at *absolute_tick*, adjusting surrounding deltas."""
    # Walk the track to find the insertion point.
    cumulative = 0
    for i, existing in enumerate(track):
        next_cumulative = cumulative + existing.time
        if next_cumulative > absolute_tick:
            # Insert before this message.
            delta_before = absolute_tick - cumulative
            new_msg = msg.copy(time=delta_before)
            # Adjust the existing message's delta.
            existing.time = next_cumulative - absolute_tick
            track.insert(i, new_msg)
            return
        cumulative = next_cumulative

    # Append at end (before end_of_track if present).
    delta = absolute_tick - cumulative
    new_msg = msg.copy(time=delta)

    # If the last message is end_of_track, insert before it.
    if track and track[-1].type == "end_of_track":
        eot = track[-1]
        eot_abs = cumulative  # cumulative already includes eot delta
        # Actually eot.time was already counted in cumulative above,
        # so eot sits at `cumulative`. We need to insert before it.
        # Remove eot, append msg, re-add eot.
        track.pop()
        # Recalculate: cumulative was computed INCLUDING eot.time.
        # So the tick just before eot is cumulative - eot.time.
        pre_eot_tick = cumulative - eot.time
        new_msg = msg.copy(time=absolute_tick - pre_eot_tick)
        track.append(new_msg)
        # eot now sits at the same absolute tick it was at, or after
        # the inserted message if that's later.
        eot_new_delta = max(0, eot_abs - absolute_tick)
        track.append(mido.MetaMessage("end_of_track", time=eot_new_delta))
    else:
        track.append(new_msg)


def remove_message_at_index(track: mido.MidiTrack, index: int) -> None:
    """Remove message at *index*, giving its delta to the next message."""
    if index < 0 or index >= len(track):
        raise IndexError(f"Track index {index} out of range (len={len(track)})")
    removed = track[index]
    # Give the removed message's delta to the next message.
    if index + 1 < len(track):
        track[index + 1] = track[index + 1].copy(
            time=track[index + 1].time + removed.time
        )
    del track[index]


# ---------------------------------------------------------------------------
# Key signature parsing
# ---------------------------------------------------------------------------

def parse_key_signature(ks_str: str) -> tuple[str, str]:
    """Split a DSL key string ("C-major", "G-minor", "Dm", "F#") into
    (key, mode). ``mode`` is "major" or "minor"."""
    parts = ks_str.replace("-", " ").split()
    key = parts[0] if parts else "C"
    mode = parts[1] if len(parts) > 1 else "major"

    # Handle shorthand: "Dm" -> key="D", mode="minor"
    if len(parts) == 1 and mode == "major":
        if len(key) >= 2 and key.endswith("m") and key[-2] not in ("#", "b"):
            mode = "minor"
            key = key[:-1]
        elif len(key) >= 3 and key.endswith("m") and key[-2] in ("#", "b"):
            mode = "minor"
            key = key[:-1]

    return key, mode


def to_mido_key(key: str, mode: str) -> str:
    """Convert (key, mode) into mido's compact key_signature format
    ("C", "Am", "F#", "Bbm")."""
    return key + "m" if mode == "minor" else key


# ---------------------------------------------------------------------------
# Track metadata extraction
# ---------------------------------------------------------------------------

def _extract_track_metadata(track: mido.MidiTrack) -> dict:
    """Scan a track for program_change, bank select CCs, and track_name."""
    meta: dict = {
        "name": "",
        "channel": 0,
        "program": 0,
        "bank_msb": 0,
        "bank_lsb": 0,
    }
    for msg in track:
        if msg.type == "track_name":
            meta["name"] = msg.name
        elif msg.type == "program_change":
            meta["program"] = msg.program
            meta["channel"] = msg.channel
        elif msg.type == "control_change":
            if msg.control == 0:  # Bank Select MSB
                meta["bank_msb"] = msg.value
                meta["channel"] = msg.channel
            elif msg.control == 32:  # Bank Select LSB
                meta["bank_lsb"] = msg.value
                meta["channel"] = msg.channel
        elif hasattr(msg, "channel") and not msg.is_meta:
            # Pick up channel from first channel message if not yet set
            if meta["channel"] == 0 and msg.channel != 0:
                meta["channel"] = msg.channel
    return meta


# ---------------------------------------------------------------------------
# Note pairing utility
# ---------------------------------------------------------------------------

def pair_notes(track: mido.MidiTrack, track_name: str = "") -> list[NoteRef]:
    """Walk a track and pair note_on/note_off messages into NoteRefs.

    Handles:
    - Standard note_on + note_off pairs
    - note_on with velocity=0 treated as note_off (MIDI running status)
    - Overlapping notes on same pitch+channel (FIFO pairing)
    """
    # Build absolute-tick view with original indices
    abs_tick = 0
    indexed: list[tuple[int, int, mido.Message]] = []  # (index, abs_tick, msg)
    for i, msg in enumerate(track):
        abs_tick += msg.time
        indexed.append((i, abs_tick, msg))

    # Pending note_ons: (pitch, channel) -> list of (index, abs_tick, velocity)
    pending: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
    results: list[NoteRef] = []

    for idx, tick, msg in indexed:
        if msg.type == "note_on" and msg.velocity > 0:
            pending[(msg.note, msg.channel)].append((idx, tick, msg.velocity))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (msg.note, msg.channel)
            if pending[key]:
                on_idx, on_tick, vel = pending[key].pop(0)  # FIFO
                results.append(NoteRef(
                    track_name=track_name,
                    note_on_idx=on_idx,
                    note_off_idx=idx,
                    abs_tick=on_tick,
                    duration_ticks=tick - on_tick,
                    pitch=msg.note,
                    velocity=vel,
                    channel=msg.channel,
                ))

    # Sort by onset tick, then pitch
    results.sort(key=lambda n: (n.abs_tick, n.pitch))
    return results


# ---------------------------------------------------------------------------
# NoteIndex — fast lookup by pitch, channel, velocity
# ---------------------------------------------------------------------------

class NoteIndex:
    """Multi-index over NoteRefs for fast selector resolution."""

    def __init__(self) -> None:
        self.all: list[NoteRef] = []
        self.by_track: dict[str, list[NoteRef]] = defaultdict(list)
        self.by_pitch: dict[int, list[NoteRef]] = defaultdict(list)
        self.by_channel: dict[int, list[NoteRef]] = defaultdict(list)

    def rebuild(self, model: MidiModel) -> None:
        """Scan all tracks in *model*, pair notes, populate indices."""
        self.all.clear()
        self.by_track.clear()
        self.by_pitch.clear()
        self.by_channel.clear()

        for name, ref in model.tracks.items():
            notes = pair_notes(ref.track, track_name=name)
            self.all.extend(notes)
            self.by_track[name].extend(notes)
            for n in notes:
                self.by_pitch[n.pitch].append(n)
                self.by_channel[n.channel].append(n)

    def by_velocity_range(self, lo: int, hi: int) -> list[NoteRef]:
        """Return notes with velocity in [lo, hi] inclusive."""
        return [n for n in self.all if lo <= n.velocity <= hi]


# ---------------------------------------------------------------------------
# MidiModel
# ---------------------------------------------------------------------------

class MidiModel:
    """Source of truth: mido.MidiFile + thin index.

    Track 0 is always the conductor track (tempo, time sig, key sig, markers).
    Instrument tracks start at index 1 in file.tracks.
    """

    def __init__(
        self,
        title: str = "Untitled",
        ppqn: int = 480,
        tempo: float = 120.0,
        time_sig: tuple[int, int] = (4, 4),
        key: str | None = None,
    ) -> None:
        self.file = mido.MidiFile(ticks_per_beat=ppqn, type=1)
        self.tracks: dict[str, TrackRef] = {}  # label -> TrackRef
        self.track_order: list[str] = []  # insertion order of labels
        self.title = title

        # Track 0: conductor track
        conductor = mido.MidiTrack()
        conductor.append(mido.MetaMessage("track_name", name=title, time=0))
        conductor.append(
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0)
        )
        num, den = time_sig
        conductor.append(
            mido.MetaMessage(
                "time_signature",
                numerator=num,
                denominator=den,
                clocks_per_click=24,
                notated_32nd_notes_per_beat=8,
                time=0,
            )
        )
        if key:
            mido_key = to_mido_key(*parse_key_signature(key))
            conductor.append(
                mido.MetaMessage("key_signature", key=mido_key, time=0)
            )
        conductor.append(mido.MetaMessage("end_of_track", time=0))
        self.file.tracks.append(conductor)

    # -- Properties ---------------------------------------------------------

    @property
    def ppqn(self) -> int:
        return self.file.ticks_per_beat

    # -- Track CRUD ---------------------------------------------------------

    def add_track(
        self,
        name: str,
        channel: int | None = None,
        instrument: str | None = None,
        program: int | None = None,
        bank_msb: int = 0,
        bank_lsb: int = 0,
    ) -> str:
        """Create a new instrument track and return its label (name)."""
        if name in self.tracks:
            raise ValueError(f"Track '{name}' already exists")

        if channel is None:
            channel = self._next_channel()

        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))

        # Bank select (before program change per MIDI spec)
        if bank_msb or bank_lsb:
            if bank_msb:
                track.append(
                    mido.Message(
                        "control_change",
                        channel=channel,
                        control=0,
                        value=bank_msb,
                        time=0,
                    )
                )
            if bank_lsb:
                track.append(
                    mido.Message(
                        "control_change",
                        channel=channel,
                        control=32,
                        value=bank_lsb,
                        time=0,
                    )
                )

        # Program change
        prog = program if program is not None else 0
        track.append(
            mido.Message(
                "program_change", channel=channel, program=prog, time=0
            )
        )

        track.append(mido.MetaMessage("end_of_track", time=0))
        self.file.tracks.append(track)

        ref = TrackRef(
            track=track,
            name=name,
            channel=channel,
            program=prog,
            bank_msb=bank_msb,
            bank_lsb=bank_lsb,
        )
        self.tracks[name] = ref
        self.track_order.append(name)
        return name

    def remove_track(self, name: str) -> TrackRef | None:
        """Remove a track by label. Returns the removed TrackRef or None."""
        ref = self.tracks.pop(name, None)
        if ref is None:
            return None
        if ref.track in self.file.tracks:
            self.file.tracks.remove(ref.track)
        if name in self.track_order:
            self.track_order.remove(name)
        return ref

    def get_track(self, name: str) -> TrackRef | None:
        """Look up a track by label."""
        return self.tracks.get(name)

    # -- Note CRUD ----------------------------------------------------------

    def add_note(
        self,
        track_name: str,
        pitch: int,
        abs_tick: int,
        duration_ticks: int,
        velocity: int = 80,
        channel: int | None = None,
    ) -> NoteRef:
        """Insert a note_on/note_off pair into a track. Returns a NoteRef."""
        ref = self.tracks.get(track_name)
        if ref is None:
            raise KeyError(f"Track '{track_name}' not found")
        if channel is None:
            channel = ref.channel

        note_on = mido.Message(
            "note_on", channel=channel, note=pitch, velocity=velocity,
        )
        note_off = mido.Message(
            "note_off", channel=channel, note=pitch, velocity=0,
        )
        off_tick = abs_tick + duration_ticks

        insert_message_at_tick(ref.track, note_on, abs_tick)
        insert_message_at_tick(ref.track, note_off, off_tick)

        # Build a NoteRef by scanning for the actual indices
        notes = pair_notes(ref.track, track_name=track_name)
        for n in notes:
            if n.pitch == pitch and n.abs_tick == abs_tick and n.velocity == velocity:
                return n

        # Fallback — should not happen but return a best-effort ref
        return NoteRef(
            track_name=track_name,
            note_on_idx=-1,
            note_off_idx=-1,
            abs_tick=abs_tick,
            duration_ticks=duration_ticks,
            pitch=pitch,
            velocity=velocity,
            channel=channel,
        )

    def remove_note_at(
        self,
        track_name: str,
        pitch: int,
        abs_tick: int,
    ) -> NoteRef | None:
        """Remove the first note matching *pitch* at *abs_tick*. Returns the removed NoteRef."""
        ref = self.tracks.get(track_name)
        if ref is None:
            raise KeyError(f"Track '{track_name}' not found")

        notes = pair_notes(ref.track, track_name=track_name)
        for n in notes:
            if n.pitch == pitch and n.abs_tick == abs_tick:
                # Remove note_off first (higher index) to avoid shifting
                remove_message_at_index(ref.track, n.note_off_idx)
                # After removing note_off, note_on_idx is still valid
                # only if note_on_idx < note_off_idx (always true)
                remove_message_at_index(ref.track, n.note_on_idx)
                return n
        return None

    def remove_note(self, track_name: str, note_ref: NoteRef) -> NoteRef | None:
        """Remove a note by its NoteRef (delegates to remove_note_at)."""
        return self.remove_note_at(track_name, note_ref.pitch, note_ref.abs_tick)

    def get_notes(self, track_name: str | None = None) -> list[NoteRef]:
        """Return paired NoteRefs from one track or all tracks."""
        if track_name is not None:
            ref = self.tracks.get(track_name)
            if ref is None:
                raise KeyError(f"Track '{track_name}' not found")
            return pair_notes(ref.track, track_name=track_name)

        all_notes: list[NoteRef] = []
        for name, ref in self.tracks.items():
            all_notes.extend(pair_notes(ref.track, track_name=name))
        all_notes.sort(key=lambda n: (n.abs_tick, n.pitch))
        return all_notes

    # -- Channel auto-assignment --------------------------------------------

    def _next_channel(self) -> int:
        """Auto-assign next available channel, skipping 9 (drums)."""
        used = {ref.channel for ref in self.tracks.values()}
        for ch in [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15]:
            if ch not in used:
                return ch
        return 0  # fallback

    # -- Snapshot / Restore (undo/redo) -------------------------------------

    def snapshot(self) -> bytes:
        """Serialize current state to bytes via BytesIO."""
        buf = BytesIO()
        self.file.save(file=buf)
        return buf.getvalue()

    def restore(self, data: bytes) -> None:
        """Restore from a byte snapshot, rebuilding the index."""
        buf = BytesIO(data)
        self.file = mido.MidiFile(file=buf)
        self.rebuild_index()

    def rebuild_index(self) -> None:
        """Scan all tracks and rebuild self.tracks / self.track_order.

        Track 0 is the conductor — skipped for the index.
        Extracts title from conductor track_name.
        """
        self.tracks.clear()
        self.track_order.clear()

        # Extract title from conductor track
        if self.file.tracks:
            for msg in self.file.tracks[0]:
                if msg.type == "track_name":
                    self.title = msg.name
                    break

        # Index instrument tracks (1..N)
        for track in self.file.tracks[1:]:
            meta = _extract_track_metadata(track)
            name = meta["name"]
            if not name:
                # Generate a fallback name
                name = f"Track {len(self.tracks) + 1}"
            # Avoid duplicate labels
            base_name = name
            counter = 2
            while name in self.tracks:
                name = f"{base_name} {counter}"
                counter += 1
            ref = TrackRef(
                track=track,
                name=name,
                channel=meta["channel"],
                program=meta["program"],
                bank_msb=meta["bank_msb"],
                bank_lsb=meta["bank_lsb"],
            )
            self.tracks[name] = ref
            self.track_order.append(name)

    # -- Save / Load --------------------------------------------------------

    def save(self, path: str) -> None:
        """Write the MIDI file to disk."""
        self.file.save(path)

    @classmethod
    def load(cls, path: str) -> MidiModel:
        """Load a MIDI file and rebuild the index."""
        model = cls.__new__(cls)
        model.file = mido.MidiFile(filename=path)
        model.tracks = {}
        model.track_order = []
        model.title = "Untitled"
        model.rebuild_index()
        return model

    # -- Digest -------------------------------------------------------------

    def get_digest(self) -> str:
        """Compact state fingerprint for mutation responses."""
        n_tracks = len(self.tracks)

        # Count note_on messages as note estimate
        n_notes = 0
        for ref in self.tracks.values():
            for msg in ref.track:
                if msg.type == "note_on" and msg.velocity > 0:
                    n_notes += 1

        # Tempo from conductor track
        tempo_bpm = 120.0
        ts_str = "4/4"
        ks_str = ""

        if self.file.tracks:
            for msg in self.file.tracks[0]:
                if msg.type == "set_tempo":
                    tempo_bpm = mido.tempo2bpm(msg.tempo)
                elif msg.type == "time_signature":
                    ts_str = f"{msg.numerator}/{msg.denominator}"
                elif msg.type == "key_signature":
                    ks_str = msg.key

        parts = [
            f"{n_tracks}t",
            f"{n_notes}n",
            f"tempo:{tempo_bpm:.0f}",
            ts_str,
        ]
        if ks_str:
            parts.append(ks_str)
        return f"[{' '.join(parts)}]"
