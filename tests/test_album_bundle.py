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
    DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_POLL_TIMEOUT_SECONDS,
    atomic_copy_to_staging,
    copy_audio_files_atomically,
    album_title_relevance,
    get_completed_no_path_window_seconds,
    get_poll_interval,
    get_poll_timeout,
    pick_best_album_release,
    quality_score,
    resolve_reported_save_path,
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
# #730 — album-title relevance gate
# ---------------------------------------------------------------------------


def test_relevance_exact_match_is_full():
    assert album_title_relevance("David Bowie - Heroes (2017 Remaster) [FLAC]", "Heroes") == 1.0


def test_relevance_word_boundary_not_substring():
    # "Heroes" must NOT be satisfied by "Superheroes" (the substring trap).
    assert album_title_relevance("Various - Superheroes Soundtrack", "Heroes") == 0.0


def test_relevance_wrong_album_scores_zero():
    # The reporter's exact case: a "Heroes" request must not match a different
    # Bowie album that shares no title words.
    assert album_title_relevance("David Bowie - Scary Monsters and Super Creeps", "Heroes") == 0.0


def test_relevance_accent_folding():
    # Björk folds to bjork, not "bj rk" — so an accented request still matches.
    assert album_title_relevance("Bjork - Homogenic [FLAC]", "Björk Homogenic") == 1.0


def test_relevance_partial_word_coverage():
    # 1 of 2 album words present -> 0.5 (below the 0.6 floor).
    assert album_title_relevance("Artist - Dark Side [FLAC]", "Dark Moon") == 0.5


def test_relevance_no_album_name_is_neutral():
    # Can't gate on nothing — preserves old behavior for callers w/o a title.
    assert album_title_relevance("anything at all", "") == 1.0


def test_relevance_ignores_edition_suffix_on_album_name():
    """The RIGHT torrent must not be rejected just because the stored album
    name carries an edition/remaster/format suffix the title lacks. (Caught in
    review — the naive 'all album words' version wrongly rejected these.)"""
    floor = 0.6
    assert album_title_relevance("Tame Impala - Currents [FLAC]", "Currents (Deluxe)") >= floor
    assert album_title_relevance("David Bowie - Heroes [FLAC]", "Heroes (2017 Remaster)") >= floor
    assert album_title_relevance("Daft Punk - Discovery [FLAC]", "Discovery (Remastered Edition)") >= floor


def test_relevance_album_named_only_with_noise_or_number():
    # If the album name is JUST a noise/number word, don't strip it to nothing
    # and match everything — keep the literal word.
    assert album_title_relevance("Taylor Swift - 1989 [FLAC]", "1989") == 1.0
    assert album_title_relevance("Taylor Swift - Red [FLAC]", "1989") == 0.0
    assert album_title_relevance("Various - Deluxe [FLAC]", "Deluxe") == 1.0


def test_picker_refuses_wrong_album_falls_back():
    """The #730 scenario: a hugely-popular WRONG album must NOT be picked over
    a less-popular RIGHT one — and if nothing matches, return None so the
    caller falls back to per-track."""
    wrong_popular = _Release(title="David Bowie - Scary Monsters [FLAC]",
                             size=400_000_000, seeders=16000)
    right_quiet = _Release(title='David Bowie - "Heroes" 2017 Remaster [FLAC]',
                           size=400_000_000, seeders=10)
    picked = pick_best_album_release(
        [wrong_popular, right_quiet], _flac_quality_guess, album_name="Heroes")
    assert picked is right_quiet   # relevance beats raw popularity


def test_picker_returns_none_when_nothing_matches_album():
    # Only the wrong album is available -> refuse (None) -> per-track fallback.
    wrong = _Release(title="David Bowie - Scary Monsters [FLAC]",
                     size=400_000_000, seeders=16000)
    assert pick_best_album_release([wrong], _flac_quality_guess, album_name="Heroes") is None


def test_picker_without_album_name_unchanged():
    # No album_name passed -> no gating -> old popularity behavior intact.
    a = _Release(title="Whatever [FLAC]", size=400_000_000, seeders=5)
    b = _Release(title="Other [FLAC]", size=400_000_000, seeders=999)
    assert pick_best_album_release([a, b], _flac_quality_guess) is b


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


def test_get_completed_no_path_window_uses_default_when_unset() -> None:
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS
        assert get_completed_no_path_window_seconds() == DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS


def test_get_completed_no_path_window_honours_override() -> None:
    """Users whose SAB is slow to write ``storage`` (large box sets,
    slow disks) can widen the tolerance without touching code."""
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = 300
        assert get_completed_no_path_window_seconds() == 300.0


def test_get_completed_no_path_window_falls_back_on_garbage() -> None:
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = ''
        assert get_completed_no_path_window_seconds() == DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS
        cm.get.return_value = 0
        assert get_completed_no_path_window_seconds() == DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS


# ---------------------------------------------------------------------------
# resolve_reported_save_path — downloader→local path translation. The arr
# remote-path problem: SAB reports its own container path, SoulSync mounts
# the same files elsewhere.
# ---------------------------------------------------------------------------


def _cfg(values: dict):
    """Build a config_manager.get-shaped callable from a dict."""
    def _get(key, default=None):
        return values.get(key, default)
    return _get


def test_resolve_returns_reported_path_verbatim_when_readable(tmp_path: Path) -> None:
    """If the client's path is already readable here (mounts mirror the
    client), return it unchanged — no translation needed."""
    album = tmp_path / "MyAlbum"
    album.mkdir()
    # config_get should never even be consulted on the happy path.
    assert resolve_reported_save_path(str(album), config_get=_cfg({})) == str(album)


def test_resolve_uses_explicit_prefix_mapping(tmp_path: Path) -> None:
    """Sonarr/Radarr-style remote path mapping: SAB's prefix is rewritten
    to a SoulSync-visible root."""
    (tmp_path / "MyAlbum").mkdir()
    cfg = _cfg({'download_source.usenet_path_mappings': [
        {'from': '/data/downloads/music', 'to': str(tmp_path)},
    ]})
    resolved = resolve_reported_save_path('/data/downloads/music/MyAlbum', config_get=cfg)
    assert resolved == str(tmp_path / "MyAlbum")


def test_resolve_basename_fallback_against_download_root(tmp_path: Path) -> None:
    """Zero-config shared-volume case: the album folder shows up under
    SoulSync's own download root with the same name SAB reported."""
    (tmp_path / "MyAlbum").mkdir()
    cfg = _cfg({'soulseek.download_path': str(tmp_path)})
    resolved = resolve_reported_save_path('/data/downloads/music/MyAlbum', config_get=cfg)
    assert resolved == str(tmp_path / "MyAlbum")


def test_resolve_mapping_takes_priority_over_basename(tmp_path: Path) -> None:
    """An explicit mapping that resolves wins over the basename scan."""
    mapped_root = tmp_path / "mapped"
    dl_root = tmp_path / "dl"
    (mapped_root / "MyAlbum").mkdir(parents=True)
    (dl_root / "MyAlbum").mkdir(parents=True)
    cfg = _cfg({
        'download_source.usenet_path_mappings': [
            {'from': '/data/downloads/music', 'to': str(mapped_root)},
        ],
        'soulseek.download_path': str(dl_root),
    })
    resolved = resolve_reported_save_path('/data/downloads/music/MyAlbum', config_get=cfg)
    assert resolved == str(mapped_root / "MyAlbum")


def test_resolve_returns_reported_unchanged_when_nothing_found(tmp_path: Path) -> None:
    """No readable path, no mapping hit, no basename match → return the
    original so the caller's 'no audio' error still surfaces."""
    cfg = _cfg({'soulseek.download_path': str(tmp_path)})  # empty root
    reported = '/data/downloads/music/Missing'
    assert resolve_reported_save_path(reported, config_get=cfg) == reported


def test_resolve_handles_empty_and_none(tmp_path: Path) -> None:
    assert resolve_reported_save_path('', config_get=_cfg({})) == ''
    assert resolve_reported_save_path(None, config_get=_cfg({})) is None


def test_resolve_skips_mapping_when_target_missing_then_tries_basename(tmp_path: Path) -> None:
    """A mapping whose translated path doesn't exist must not short-circuit
    — fall through to the basename scan."""
    (tmp_path / "MyAlbum").mkdir()
    cfg = _cfg({
        'download_source.usenet_path_mappings': [
            {'from': '/data/downloads/music', 'to': '/nope/not/mounted'},
        ],
        'soulseek.download_path': str(tmp_path),
    })
    resolved = resolve_reported_save_path('/data/downloads/music/MyAlbum', config_get=cfg)
    assert resolved == str(tmp_path / "MyAlbum")


# ---------------------------------------------------------------------------
# poll_album_download — lifted poll loop for both torrent + usenet plugins.
# ---------------------------------------------------------------------------


from core.download_plugins.album_bundle import (
    DEFAULT_TRANSIENT_MISS_THRESHOLD,
    TransientMissCounter,
    get_transient_miss_threshold,
    poll_album_download,
)


# ---------------------------------------------------------------------------
# TransientMissCounter — shared retry-counter used by every poll loop.
# ---------------------------------------------------------------------------


def test_counter_starts_at_zero_and_uses_default_threshold():
    """No config override → uses DEFAULT_TRANSIENT_MISS_THRESHOLD,
    starts at zero misses."""
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = DEFAULT_TRANSIENT_MISS_THRESHOLD
        counter = TransientMissCounter()
        assert counter.threshold == DEFAULT_TRANSIENT_MISS_THRESHOLD
        assert counter.misses == 0


def test_counter_honors_explicit_threshold_over_config():
    """Explicit threshold takes precedence over the config-driven default."""
    counter = TransientMissCounter(threshold=3)
    assert counter.threshold == 3


def test_counter_record_miss_returns_false_until_threshold():
    """record_miss returns True only on the iteration that pushes
    the count to threshold — earlier calls return False so the caller
    knows to keep polling."""
    counter = TransientMissCounter(threshold=3)
    assert counter.record_miss() is False  # 1
    assert counter.record_miss() is False  # 2
    assert counter.record_miss() is True   # 3 → at threshold


def test_counter_reset_zeros_count():
    """A successful read between transient misses resets the counter
    so isolated network blips don't accumulate toward the threshold."""
    counter = TransientMissCounter(threshold=3)
    counter.record_miss()
    counter.record_miss()
    counter.reset()
    assert counter.misses == 0
    # After reset we should need a full threshold of fresh misses again.
    assert counter.record_miss() is False
    assert counter.record_miss() is False
    assert counter.record_miss() is True


def test_get_transient_miss_threshold_uses_default_when_unset():
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = DEFAULT_TRANSIENT_MISS_THRESHOLD
        assert get_transient_miss_threshold() == DEFAULT_TRANSIENT_MISS_THRESHOLD


def test_get_transient_miss_threshold_honors_config_override():
    """Users with very slow servers (huge multi-disc box sets, slow
    disks) need to bump the tolerance window."""
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = 20
        assert get_transient_miss_threshold() == 20


def test_get_transient_miss_threshold_falls_back_on_garbage():
    """Non-positive / non-numeric config values fall back to the
    default — same defensive pattern as get_poll_interval."""
    with patch('core.download_plugins.album_bundle.config_manager') as cm:
        cm.get.return_value = 'oops'
        assert get_transient_miss_threshold() == DEFAULT_TRANSIENT_MISS_THRESHOLD
        cm.get.return_value = 0
        assert get_transient_miss_threshold() == DEFAULT_TRANSIENT_MISS_THRESHOLD
        cm.get.return_value = -3
        assert get_transient_miss_threshold() == DEFAULT_TRANSIENT_MISS_THRESHOLD


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
    incomplete_path: Optional[str] = None


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


def test_poll_tolerates_completed_with_late_save_path_arrival() -> None:
    """Regression for #721 (Forty Licks stuck at 61%).

    SAB History flips ``status`` to 'Completed' a few seconds before
    its post-processing pipeline writes the final ``storage`` field.
    Pre-fix the poll returned ``None`` on the first such read, the
    bundle plugin marked the batch failed, and the UI froze on the
    last 'downloading' emit. Now the poll tolerates up to
    ``transient_miss_threshold`` consecutive "completed but no
    save_path" reads, so SAB has a window to finish writing the
    path. When it lands, return it normally."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    sequence = iter([
        # Queue phase — SAB still downloading.
        _Status(state='downloading', progress=0.61),
        # History phase — flipped to Completed but storage not yet
        # populated. Pre-fix this branch returned None immediately.
        _Status(state='completed', save_path=None, progress=1.0),
        _Status(state='completed', save_path=None, progress=1.0),
        # SAB finished post-process; storage now set.
        _Status(state='completed', save_path='/dl/forty-licks', progress=1.0),
    ])
    result = poll_album_download(
        get_status=lambda: next(sequence),
        title='Forty Licks',
        emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=5,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )

    assert result == '/dl/forty-licks'
    # No terminal failed emit — bundle plugin will continue to
    # staging, not error out.
    assert 'failed' not in [c[0] for c in calls]


def test_poll_gives_up_when_completed_with_no_save_path_persists() -> None:
    """If SAB stays on 'Completed' but ``storage`` never lands past
    the threshold, fail loudly with an explicit error pointing at
    the missing save_path field — instead of silently sitting on
    the last 'downloading' UI emit until the 6-hour deadline."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    result = poll_album_download(
        get_status=lambda: _Status(state='completed', save_path=None, progress=1.0),
        title='Forty Licks',
        emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=3,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=600.0,
    )

    assert result is None
    failed_calls = [c for c in calls if c[0] == 'failed']
    assert len(failed_calls) == 1
    err = failed_calls[0][1].get('error', '').lower()
    assert 'save_path' in err or 'success but never' in err


def test_poll_completed_no_path_window_is_longer_than_miss_window() -> None:
    """#721 follow-up: the completed-but-no-save_path window must be
    DECOUPLED from (and far longer than) the transient-miss window. SAB
    can take 2+ minutes to write ``storage``; the old code reused the
    5-poll (~10s) miss window here and false-failed real completions.
    With a small miss threshold but the default long no-path window, a
    download that takes 8 completed-no-path polls before ``storage``
    lands must still succeed."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    sequence = iter(
        [_Status(state='completed', save_path=None, progress=1.0)] * 8
        + [_Status(state='completed', save_path='/dl/late', progress=1.0)]
    )
    result = poll_album_download(
        get_status=lambda: next(sequence),
        title='Slow SAB',
        emit=emit,
        complete_states=frozenset(['completed']),
        transient_miss_threshold=3,   # vanished-job window stays short
        # completed_no_path_threshold left to default (~120s / interval).
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=600.0,
    )
    assert result == '/dl/late'
    assert 'failed' not in [c[0] for c in calls]


def test_poll_falls_back_to_incomplete_path_after_window_exhausted() -> None:
    """When SAB reports the job completed but the final save_path NEVER
    lands (some SAB versions / no post-process move), the files are
    still physically on disk in the in-progress dir. Rather than failing
    a download that actually succeeded, the poll falls back to the
    adapter's ``incomplete_path`` as a last resort once the window is
    exhausted — no terminal 'failed' emit."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    result = poll_album_download(
        get_status=lambda: _Status(
            state='completed', save_path=None,
            incomplete_path='/sab/incomplete/album', progress=1.0,
        ),
        title='No Storage Field',
        emit=emit,
        complete_states=frozenset(['completed']),
        completed_no_path_threshold=3,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=600.0,
    )
    assert result == '/sab/incomplete/album'
    assert 'failed' not in [c[0] for c in calls]


def test_poll_fails_when_no_path_and_no_incomplete_path() -> None:
    """Last resort only fires when there's actually a path to scan.
    With neither a final save_path nor an incomplete_path, the poll
    still fails loudly after the window so the UI doesn't freeze."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    result = poll_album_download(
        get_status=lambda: _Status(state='completed', save_path=None,
                                   incomplete_path=None, progress=1.0),
        title='Truly Pathless',
        emit=emit,
        complete_states=frozenset(['completed']),
        completed_no_path_threshold=3,
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=600.0,
    )
    assert result is None
    failed_calls = [c for c in calls if c[0] == 'failed']
    assert len(failed_calls) == 1
    err = failed_calls[0][1].get('error', '').lower()
    assert 'save_path' in err or 'success but never' in err


def test_poll_uses_save_path_from_earlier_downloading_emit_if_completed_lacks_one() -> None:
    """Sticky save_path: when an earlier ``downloading`` status carried
    a non-empty ``save_path`` (qBit shows the target dir mid-download),
    that value is remembered. A later ``completed`` read with an empty
    save_path still resolves cleanly because the sticky value applies.
    Important so torrent clients (which set save_path from the start)
    don't trip the completed-no-path retry."""
    clock = _ScriptedClock()
    emit, calls = _make_emit_recorder()
    sequence = iter([
        _Status(state='downloading', save_path='/qb/album-target', progress=0.5),
        _Status(state='completed', save_path=None, progress=1.0),
    ])
    result = poll_album_download(
        get_status=lambda: next(sequence),
        title='Some Album',
        emit=emit,
        complete_states=frozenset(['completed']),
        sleep=clock.sleep, monotonic=clock.monotonic,
        poll_interval=2.0, timeout=60.0,
    )

    assert result == '/qb/album-target'
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
