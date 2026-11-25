"""A waiting batch's tracks, kept small until the batch starts (#2242).

A wishlist run makes one batch per album and hands every one of them to a
small pool (three album workers), so most of them wait. Each waiting job held
its tracks as full python dicts: about 9 KB a track, 128 MB for a 13.6k track
wishlist, and a second run stacked on the first doubled it. A waiting batch
only needs its tracks back when a worker picks it up, so they wait as one
compressed pickle (a few KB per album) and are unpacked by the worker.

Pickle, not json: it keeps the shared references inside a track (track_data
and spotify_data are one dict) and any set or tuple exactly as they were. It
never leaves this process, nothing reads it but ``unpack_tracks``.
"""

from __future__ import annotations

import pickle
import zlib
from collections.abc import Sequence
from typing import Any

from utils.logging_config import get_logger

logger = get_logger("downloads.queued_tracks")


class PackedTracks(Sequence):
    """A track list stored compressed. Reads unpack it; the worker unpacks it
    once with ``unpack()`` and works on the real list."""

    __slots__ = ("_blob", "_count")

    def __init__(self, tracks):
        tracks = list(tracks)
        self._count = len(tracks)
        self._blob = zlib.compress(pickle.dumps(tracks, protocol=pickle.HIGHEST_PROTOCOL), 1)

    def unpack(self) -> list:
        # our own bytes from __init__ above, never read from outside the process
        return pickle.loads(zlib.decompress(self._blob))  # noqa: S301

    @property
    def packed_size(self) -> int:
        return len(self._blob)

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, index):
        return self.unpack()[index]

    def __iter__(self):
        return iter(self.unpack())

    def __repr__(self) -> str:
        return f"PackedTracks({self._count} tracks, {len(self._blob)} bytes)"


def pack_tracks(tracks) -> Any:
    """``tracks`` as a ``PackedTracks``; the list itself when it can't be
    pickled (a waiting batch then costs what it always did, it still runs)."""
    if isinstance(tracks, PackedTracks) or not isinstance(tracks, list):
        return tracks
    try:
        return PackedTracks(tracks)
    except Exception as exc:  # noqa: BLE001 - packing only saves memory
        logger.debug("queued batch tracks kept unpacked: %s", exc)
        return tracks


def unpack_tracks(tracks) -> Any:
    """The real list for a worker, whatever the queue handed it."""
    if isinstance(tracks, PackedTracks):
        return tracks.unpack()
    return tracks


__all__ = ["PackedTracks", "pack_tracks", "unpack_tracks"]
