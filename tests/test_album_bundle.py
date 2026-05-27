"""Tests for ``core/download_plugins/album_bundle.py``.

The shared helpers used by both the torrent and usenet album-bundle
flows. Pins the pick heuristic, the atomic-copy invariant
(no partial files ever visible at the audio extension), the
collision-suffix logic, and the config-driven poll cadence so a
future tweak in either plugin can't break the contract.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from core.download_plugins.album_bundle import (
    ALBUM_PICK_MAX_BYTES,
    ALBUM_PICK_MIN_BYTES,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_POLL_TIMEOUT_SECONDS,
    atomic_copy_to_staging,
    copy_audio_files_atomically,
    get_poll_interval,
    get_poll_timeout,
    pick_best_album_release,
    quality_score,
    unique_staging_path,
)


# Minimal release-result shim — duck-types the fields the picker reads.
@dataclass
class _Release:
    title: str
    size: int
    seeders: Optional[int] = None
    grabs: Optional[int] = None


def _flac_quality_guess(title: str) -> str:
    """Stand-in for the plugin's title→quality function."""
    t = (title or '').lower()
    if 'flac' in t:
        return 'flac'
    if 'aac' in t:
        return 'aac'
    if 'ogg' in t:
        return 'ogg'
    return 'mp3'


# ---------------------------------------------------------------------------
# pick_best_album_release
# ---------------------------------------------------------------------------


def test_picker_returns_none_for_empty_input() -> None:
    assert pick_best_album_release([], _flac_quality_guess) is None


def test_picker_drops_singletons_when_albums_present() -> None:
    """Single-track torrents under 40 MB shouldn't beat an album-sized
    candidate even if the single has thousands of seeders."""
    single = _Release(title='Track [MP3]', size=10_000_000, seeders=10_000)
    album = _Release(title='Album [MP3]', size=120_000_000, seeders=5)
    assert pick_best_album_release([single, album], _flac_quality_guess) is album


def test_picker_prefers_flac_when_tied_on_seeders() -> None:
    flac = _Release(title='Album [FLAC]', size=400_000_000, seeders=50)
    mp3 = _Release(title='Album [MP3]', size=130_000_000, seeders=50)
    assert pick_best_album_release([flac, mp3], _flac_quality_guess) is flac


def test_picker_uses_grabs_when_seeders_is_none() -> None:
    """Usenet results have ``seeders=None`` — the picker should fall
    back to ``grabs`` so popularity still drives the ranking."""
    cold = _Release(title='Album A [MP3]', size=200_000_000, seeders=None, grabs=1)
    popular = _Release(title='Album B [MP3]', size=200_000_000, seeders=None, grabs=999)
    assert pick_best_album_release([cold, popular], _flac_quality_guess) is popular


def test_picker_falls_back_when_all_below_floor() -> None:
    """When every candidate is below the 40 MB album-size floor,
    return the most-seeded one rather than None — the user still
    wants a download attempt."""
    small_low = _Release(title='X', size=5_000_000, seeders=10)
    small_high = _Release(title='Y', size=8_000_000, seeders=200)
    assert pick_best_album_release([small_low, small_high], _flac_quality_guess) is small_high


def test_picker_size_floor_matches_constant() -> None:
    """If someone moves the constant the floor moves with it — pin
    the relationship to catch accidental literals creeping back in."""
    just_below = _Release(title='Below', size=ALBUM_PICK_MIN_BYTES - 1, seeders=999)
    just_above = _Release(title='Above', size=ALBUM_PICK_MIN_BYTES + 1, seeders=1)
    assert pick_best_album_release([just_below, just_above], _flac_quality_guess) is just_above


def test_picker_rejects_oversized_box_sets() -> None:
    """Anything past 3 GB drops out of the preferred pool — most likely
    a multi-disc box set with scans + bonus material, not what the
    user asked for."""
    sane = _Release(title='Album [FLAC]', size=400_000_000, seeders=10)
    box = _Release(title='Album Box [FLAC]', size=ALBUM_PICK_MAX_BYTES + 1_000_000, seeders=999)
    # Sane wins even with 100x fewer seeders, because box is outside
    # the preferred range.
    assert pick_best_album_release([sane, box], _flac_quality_guess) is sane


# ---------------------------------------------------------------------------
# quality_score
# ---------------------------------------------------------------------------


def test_quality_score_orders_formats() -> None:
    assert quality_score('Album [FLAC]', _flac_quality_guess) > quality_score('Album [MP3]', _flac_quality_guess)
    assert quality_score('Album [AAC]', _flac_quality_guess) > quality_score('Album [MP3]', _flac_quality_guess)
    assert quality_score('Bare title', _flac_quality_guess) == quality_score('Album [MP3]', _flac_quality_guess)


# ---------------------------------------------------------------------------
# unique_staging_path
# ---------------------------------------------------------------------------


def test_unique_staging_path_returns_natural_when_clear(tmp_path: Path) -> None:
    src = tmp_path / 'src.flac'
    src.write_bytes(b'fLaC')
    staging = tmp_path / 'staging'
    staging.mkdir()
    assert unique_staging_path(staging, src) == staging / 'src.flac'


def test_unique_staging_path_suffixes_on_collision(tmp_path: Path) -> None:
    src = tmp_path / 'src.flac'
    src.write_bytes(b'fLaC')
    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / 'src.flac').write_bytes(b'existing')
    assert unique_staging_path(staging, src) == staging / 'src_1.flac'


def test_unique_staging_path_increments_suffix(tmp_path: Path) -> None:
    src = tmp_path / 'src.flac'
    src.write_bytes(b'fLaC')
    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / 'src.flac').write_bytes(b'1')
    (staging / 'src_1.flac').write_bytes(b'2')
    (staging / 'src_2.flac').write_bytes(b'3')
    assert unique_staging_path(staging, src) == staging / 'src_3.flac'


# ---------------------------------------------------------------------------
# atomic_copy_to_staging
# ---------------------------------------------------------------------------


def test_atomic_copy_lands_at_final_path(tmp_path: Path) -> None:
    src = tmp_path / 'src.flac'
    src.write_bytes(b'fLaC payload')
    dest = tmp_path / 'staging' / 'track.flac'
    dest.parent.mkdir()
    assert atomic_copy_to_staging(src, dest) is True
    assert dest.read_bytes() == b'fLaC payload'


def test_atomic_copy_leaves_no_tmp_files_after_success(tmp_path: Path) -> None:
    """The .tmp.<random> sidecar must be cleaned up by the rename —
    no orphan files left behind on a successful copy."""
    src = tmp_path / 'src.flac'
    src.write_bytes(b'data')
    dest = tmp_path / 'staging' / 'track.flac'
    dest.parent.mkdir()
    atomic_copy_to_staging(src, dest)
    tmp_files = list(dest.parent.glob('*.tmp.*'))
    assert tmp_files == []


def test_atomic_copy_never_exposes_partial_to_extension_scanner(tmp_path: Path) -> None:
    """Auto-Import filters by audio extension — the in-flight file
    must NEVER be visible at its final extension until the copy is
    complete. We probe this by scanning the staging dir in parallel
    with the copy and assert the audio file is either absent OR
    fully written.
    """
    src = tmp_path / 'src.flac'
    src.write_bytes(b'x' * (2 * 1024 * 1024))
    dest = tmp_path / 'staging' / 'track.flac'
    dest.parent.mkdir()

    stop = threading.Event()
    saw_partial = threading.Event()
    expected_size = src.stat().st_size

    def _scan_loop():
        while not stop.is_set():
            try:
                files = [p for p in dest.parent.iterdir() if p.suffix == '.flac']
            except FileNotFoundError:
                continue
            for fp in files:
                size = fp.stat().st_size
                if 0 < size < expected_size:
                    saw_partial.set()
                    return

    scanner = threading.Thread(target=_scan_loop, daemon=True)
    scanner.start()
    try:
        for i in range(5):
            target = dest.with_name(f'track_{i}.flac')
            atomic_copy_to_staging(src, target)
        # Give the scanner a moment to drain any final scan iteration.
        time.sleep(0.05)
    finally:
        stop.set()
        scanner.join(timeout=1.0)

    assert not saw_partial.is_set(), \
        "Scanner observed a partial audio file — atomic copy contract broken"


def test_copy_audio_files_atomically_skips_failures(tmp_path: Path) -> None:
    """One file failing to copy shouldn't stop the rest from being
    staged — partial results are better than a complete bailout."""
    src_a = tmp_path / 'a.flac'
    src_a.write_bytes(b'a')
    src_missing = tmp_path / 'does-not-exist.flac'   # never created
    src_c = tmp_path / 'c.flac'
    src_c.write_bytes(b'c')
    staging = tmp_path / 'staging'
    out = copy_audio_files_atomically([src_a, src_missing, src_c], staging)
    assert len(out) == 2
    landed = sorted(Path(p).name for p in out)
    assert landed == ['a.flac', 'c.flac']


def test_copy_audio_files_atomically_creates_staging_dir(tmp_path: Path) -> None:
    src = tmp_path / 'a.flac'
    src.write_bytes(b'a')
    staging = tmp_path / 'nested' / 'staging' / 'dir'
    out = copy_audio_files_atomically([src], staging)
    assert len(out) == 1
    assert staging.exists()


# ---------------------------------------------------------------------------
# Config-driven poll cadence
# ---------------------------------------------------------------------------


def test_get_poll_interval_uses_default_when_unset() -> None:
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = DEFAULT_POLL_INTERVAL_SECONDS
        assert get_poll_interval() == DEFAULT_POLL_INTERVAL_SECONDS


def test_get_poll_interval_honours_override() -> None:
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = 5
        assert get_poll_interval() == 5.0


def test_get_poll_interval_falls_back_on_garbage() -> None:
    """Non-numeric / non-positive values fall back to the default
    rather than crashing the poll loop."""
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = 'not-a-number'
        assert get_poll_interval() == DEFAULT_POLL_INTERVAL_SECONDS
        cm.get.return_value = -1
        assert get_poll_interval() == DEFAULT_POLL_INTERVAL_SECONDS


def test_get_poll_timeout_uses_default_when_unset() -> None:
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = DEFAULT_POLL_TIMEOUT_SECONDS
        assert get_poll_timeout() == DEFAULT_POLL_TIMEOUT_SECONDS


def test_get_poll_timeout_honours_override() -> None:
    """Users with slow trackers / large box sets can extend the
    deadline without touching code."""
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = 86_400   # 24h
        assert get_poll_timeout() == 86_400.0


def test_get_poll_timeout_falls_back_on_garbage() -> None:
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = ''
        assert get_poll_timeout() == DEFAULT_POLL_TIMEOUT_SECONDS
        cm.get.return_value = 0
        assert get_poll_timeout() == DEFAULT_POLL_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# poll_album_download — lifted poll loop for both torrent + usenet plugins.
# ---------------------------------------------------------------------------


from core.download_plugins.album_bundle import poll_album_download


@dataclass
class _Status:
    """Duck-typed sibling of UsenetStatus / TorrentStatus — only the
    fields poll_album_download reads."""
    state: str
    save_path: Optional[str] = None
    progress: float = 0.0
    downloaded: int = 0
    download_speed: int = 0
    error: Optional[str] = None


class _ScriptedClock:
    """Deterministic monotonic-time + sleep stand-in for poll tests.

    Each call to ``sleep(n)`` advances ``now`` by ``n`` seconds with
    no real wall-clock delay. Lets us run multi-iteration poll
    scenarios in milliseconds and assert on the exact iteration count
    each branch took."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls = 0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.sleep_calls += 1


def _make_emit_recorder():
    """Collects (state, kwargs) tuples so tests can assert on the
    emit sequence the UI would see."""
    calls = []
    def _emit(state: str, **fields) -> None:
        calls.append((state, fields))
    return _emit, calls


def test_poll_returns_save_path_on_completed_state() -> None:
    """Happy path — adapter says 'completed' with a save_path on the
    first poll; function returns the path and emits a single
    'downloading' (NOT a terminal 'failed') so the caller can chain
    'staging' / 'staged' next."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    status = _Status(state='completed', save_path='/dl/album', progress=1.0)

    result = poll_album_download(
        get_status=lambda: status,
        title='Album X',
        emit=emit,
        complete_states=frozenset(['completed']),
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )

    assert result == '/dl/album'
    states = [c[0] for c in calls]
    assert 'failed' not in states
    assert 'downloading' in states


def test_poll_tolerates_transient_missing_during_sab_handoff() -> None:
    """SAB removes a job from the queue before adding it to history.
    Pre-fix: one None read = give up + log 'disappeared from client'
    even though SAB was healthy and just mid-handoff. Now we tolerate
    up to ``transient_miss_threshold`` consecutive None reads before
    declaring the job gone. Recovery to a real status MUST reset the
    miss counter."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    sequence = iter([None, None, None,
                     _Status(state='completed', save_path='/sab/done')])
    result = poll_album_download(
        get_status=lambda: next(sequence),
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=5,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )
    assert result == '/sab/done'
    assert 'failed' not in [c[0] for c in calls]


def test_poll_gives_up_after_threshold_consecutive_misses() -> None:
    """When the job genuinely is gone (user deleted it from SAB), the
    transient tolerance still has a floor — after N misses, fail
    explicitly and emit a terminal 'failed' so the UI doesn't freeze."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    result = poll_album_download(
        get_status=lambda: None,
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=3,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=600.0,
    )
    assert result is None
    failed_calls = [c for c in calls if c[0] == 'failed']
    assert len(failed_calls) == 1
    assert 'Disappeared' in failed_calls[0][1].get('error', '')


def test_poll_emits_terminal_failed_on_explicit_failed_state() -> None:
    """Adapter says 'failed' (real failure, not transient). Function
    returns None AND emits 'failed' with the adapter's error message."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    status = _Status(state='failed', error='par2 unrecoverable')
    result = poll_album_download(
        get_status=lambda: status,
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )
    assert result is None
    failed_calls = [c for c in calls if c[0] == 'failed']
    assert len(failed_calls) == 1
    assert failed_calls[0][1].get('error') == 'par2 unrecoverable'


def test_poll_emits_terminal_failed_on_timeout() -> None:
    """When the deadline passes without success or explicit failure,
    emit 'failed' once so the UI exits the 'downloading' state."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    status = _Status(state='downloading', progress=0.5)
    result = poll_album_download(
        get_status=lambda: status,
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=10.0,
    )
    assert result is None
    failed_calls = [c for c in calls if c[0] == 'failed']
    assert len(failed_calls) == 1
    assert 'timed out' in failed_calls[0][1].get('error', '').lower()


def test_poll_treats_default_error_state_as_transient_not_terminal() -> None:
    """The adapter state-map's default-fallback for unmapped strings
    is 'error' (real-world: SAB's 'Pp' state used to land here and
    cause the poll to infinite-loop because 'error' wasn't in the
    failed set and wasn't in the complete set). Now: treat as a
    transient miss so the poll recovers when the unmapped state
    transitions to a known one. If it stays unmapped for the threshold
    of consecutive polls, emit terminal failed."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    sequence = iter([
        _Status(state='error'),
        _Status(state='error'),
        _Status(state='completed', save_path='/done'),
    ])
    result = poll_album_download(
        get_status=lambda: next(sequence),
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=5,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )
    assert result == '/done'
    assert 'failed' not in [c[0] for c in calls]


def test_poll_gives_up_when_default_error_state_persists() -> None:
    """If the adapter keeps returning the unmapped 'error' state past
    the threshold, fail rather than burning the full 6-hour timeout."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    result = poll_album_download(
        get_status=lambda: _Status(state='error'),
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=3,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=600.0,
    )
    assert result is None
    failed_calls = [c for c in calls if c[0] == 'failed']
    assert len(failed_calls) == 1
    assert 'unmapped' in failed_calls[0][1].get('error', '').lower()


def test_poll_shutdown_returns_none_without_terminal_emit() -> None:
    """Process shutdown is a clean exit — don't paint failure on the
    UI; the app is going away anyway."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    result = poll_album_download(
        get_status=lambda: _Status(state='downloading', progress=0.5),
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        is_shutdown=lambda: True,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )
    assert result is None
    assert 'failed' not in [c[0] for c in calls]


def test_poll_torrent_seeding_counts_as_complete() -> None:
    """Torrent plugin passes ``complete_states={'seeding', 'completed'}``
    because qBit / Transmission flip the torrent to 'seeding' on
    completion (files already on disk + share ratio progress). Same
    poll function must accept either state as terminal success."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    status = _Status(state='seeding', save_path='/dl/album.torrent')
    result = poll_album_download(
        get_status=lambda: status,
        title='Album X', emit=emit,
        complete_states=frozenset(['seeding', 'completed']),
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )
    assert result == '/dl/album.torrent'


def test_poll_save_path_captured_across_iterations() -> None:
    """save_path can appear mid-poll (e.g. once SAB moves the slot
    out of the queue and into history). The last non-empty save_path
    seen during the run is what we return on terminal success — even
    if the final status read happens to have it cleared."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    sequence = iter([
        _Status(state='downloading', progress=0.4),
        _Status(state='downloading', save_path='/late/path', progress=0.9),
        _Status(state='completed', progress=1.0),
    ])
    result = poll_album_download(
        get_status=lambda: next(sequence),
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )
    assert result == '/late/path'


def test_poll_get_status_exception_treated_as_transient_miss() -> None:
    """Adapter raising (network blip, JSON decode error) shouldn't
    blow up the poll thread — caught, logged, counted as a transient
    miss alongside None returns."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    counter = {'n': 0}
    def _raising_then_success():
        counter['n'] += 1
        if counter['n'] <= 2:
            raise RuntimeError('network blip')
        return _Status(state='completed', save_path='/recovered')
    result = poll_album_download(
        get_status=_raising_then_success,
        title='Album X', emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=5,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )
    assert result == '/recovered'
    assert 'failed' not in [c[0] for c in calls]
