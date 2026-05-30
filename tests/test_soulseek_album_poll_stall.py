"""Regression for the Soulseek album-bundle poll hanging when the peer stalls.

`_poll_album_bundle_downloads` waits for every transfer in the selected folder
to reach a terminal state (completed or failed). A transfer the peer stalls on —
stuck InProgress/Queued, or dropped by slskd — is never failed, never completed,
and never marked "completed-but-unresolved", so it used to block both the
all-terminal finish check AND the #715 grace exit, and the poll spun to the full
~6h timeout (the Slipknot hang).

The fix adds a bundle-level stall guard: if NOTHING progresses (no transfer
reaches terminal AND no pending transfer's byte count moves) for `_stall_grace`
seconds, the stuck transfers are marked failed so the bundle resolves with what
completed. These tests drive the real poll with a fake clock + scripted slskd
states.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.soulseek_client import SoulseekClient


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _dl(username, filename, state, *, size=100, transferred=100):
    return SimpleNamespace(
        username=username, filename=filename, state=state,
        size=size, transferred=transferred,
    )


class _StubClient:
    """Minimal stand-in for SoulseekClient with just what the poll touches."""

    def __init__(self, states, resolvable):
        self._states = states          # list of download-lists, one per poll; last repeats
        self._call = 0
        self._resolvable = set(resolvable)

    def get_all_downloads(self):
        i = min(self._call, len(self._states) - 1)
        self._call += 1
        return self._states[i]

    def _resolve_downloaded_album_file(self, filename):
        base = os.path.basename((filename or "").replace("\\", "/"))
        if base in self._resolvable or filename in self._resolvable:
            return Path(f"/staged/{base}")
        return None


def _run_poll(stub, transfer_keys, *, timeout=7200.0, interval=2.0):
    clock = _Clock()
    emits = []
    with patch("core.soulseek_client.time", clock), \
         patch("core.soulseek_client.run_async", lambda x: x), \
         patch("core.soulseek_client.get_poll_timeout", lambda: timeout), \
         patch("core.soulseek_client.get_poll_interval", lambda: interval):
        result = SoulseekClient._poll_album_bundle_downloads(
            stub, transfer_keys, lambda phase, **kw: emits.append((phase, kw))
        )
    return result, clock, emits


def _keys(*names, user="peer"):
    """Build {(user, name): TrackResult-ish} preserving order."""
    return {(user, n): SimpleNamespace(filename=n) for n in names}


def test_stalled_peer_gives_up_and_returns_completed_subset():
    tk = _keys("01.flac", "02.flac", "03.flac")
    # 01 completes (resolvable); 02/03 stuck InProgress with FROZEN byte counts.
    frozen = [
        _dl("peer", "01.flac", "Completed", transferred=100, size=100),
        _dl("peer", "02.flac", "InProgress", transferred=50, size=100),
        _dl("peer", "03.flac", "InProgress", transferred=30, size=100),
    ]
    stub = _StubClient([frozen], resolvable={"01.flac"})
    result, clock, _ = _run_poll(stub, tk, timeout=7200.0, interval=2.0)

    # Resolved with the one completed track instead of hanging to the deadline.
    assert result == [Path("/staged/01.flac")]
    # Gave up around the stall window (~180s), nowhere near the 7200s timeout.
    assert clock.now < 600.0


def test_progressing_bundle_is_not_falsely_stalled():
    tk = _keys("01.flac", "02.flac")
    # 02 keeps downloading more bytes each poll, then completes — must NOT trip
    # the stall guard even though it takes a while.
    states = [
        [_dl("peer", "01.flac", "Completed"), _dl("peer", "02.flac", "InProgress", transferred=10, size=100)],
        [_dl("peer", "01.flac", "Completed"), _dl("peer", "02.flac", "InProgress", transferred=40, size=100)],
        [_dl("peer", "01.flac", "Completed"), _dl("peer", "02.flac", "InProgress", transferred=80, size=100)],
        [_dl("peer", "01.flac", "Completed"), _dl("peer", "02.flac", "Completed", transferred=100, size=100)],
    ]
    stub = _StubClient(states, resolvable={"01.flac", "02.flac"})
    result, _clock, _ = _run_poll(stub, tk, timeout=7200.0, interval=2.0)

    assert set(result) == {Path("/staged/01.flac"), Path("/staged/02.flac")}


def test_all_transfers_stalled_returns_empty():
    tk = _keys("01.flac", "02.flac")
    frozen = [
        _dl("peer", "01.flac", "InProgress", transferred=10, size=100),
        _dl("peer", "02.flac", "Queued", transferred=0, size=100),
    ]
    stub = _StubClient([frozen], resolvable=set())
    result, clock, _ = _run_poll(stub, tk, timeout=7200.0, interval=2.0)

    assert result == []          # nothing completed → empty (caller falls back)
    assert clock.now < 600.0     # didn't spin to the deadline


def test_dropped_transfers_also_stall_out():
    """slskd dropping the transfers entirely (dl=None) must also trip the guard,
    not hang — there's no byte progress and nothing terminal."""
    tk = _keys("01.flac", "02.flac")
    stub = _StubClient([[]], resolvable=set())  # get_all_downloads returns nothing
    result, clock, _ = _run_poll(stub, tk, timeout=7200.0, interval=2.0)

    assert result == []
    assert clock.now < 600.0


def test_clean_finish_unaffected():
    tk = _keys("01.flac", "02.flac")
    done = [_dl("peer", "01.flac", "Completed"), _dl("peer", "02.flac", "Succeeded")]
    stub = _StubClient([done], resolvable={"01.flac", "02.flac"})
    result, clock, _ = _run_poll(stub, tk)
    assert set(result) == {Path("/staged/01.flac"), Path("/staged/02.flac")}
    assert clock.now < 10.0      # resolves on the first couple polls
