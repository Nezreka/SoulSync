"""Tests for the ID-based fast paths + duration sanity gate added on
top of the fuzzy matcher in ``core/imports/album_matching.py``.

This is the "state-of-the-art" matching layer — bringing the auto-
import worker up to parity with what Picard / Beets / Roon do.

Algorithm (in order, each test pins one phase):

1. **MBID exact match** — file has ``MUSICBRAINZ_TRACKID`` tag, metadata
   source returns the same id → instant pair, full confidence, skip
   fuzzy scoring entirely.
2. **ISRC exact match** — file has ``ISRC`` tag, source returns the
   same id → same fast-path, slightly lower priority than MBID
   (multiple recordings can share an ISRC across remasters/regions).
3. **Duration sanity gate** — file's audio length must be within
   ``DURATION_TOLERANCE_MS`` of the candidate track's duration.
   Defends against the cross-disc / cross-release / wrong-edit problem
   the post-download integrity check used to catch only AFTER files
   were already moved.
4. **Fuzzy fallback** — files with no usable IDs and no duration veto
   fall through to the existing weighted scorer.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from core.imports.album_matching import (
    DURATION_TOLERANCE_MS,
    EXACT_MATCH_CONFIDENCE,
    duration_sanity_ok,
    find_exact_id_matches,
    match_files_to_tracks,
)


def _sim(a, b):
    return SequenceMatcher(None, (a or '').lower(), (b or '').lower()).ratio()


def _qrank(ext):
    ranks = {'.flac': 100, '.alac': 95, '.wav': 80, '.aac': 60,
             '.ogg': 50, '.opus': 50, '.m4a': 60, '.mp3': 30}
    return ranks.get((ext or '').lower(), 0)


def _tags(*, title='', artist='', album='', track=0, disc=1,
          isrc='', mbid='', duration_ms=0):
    return {
        'title': title, 'artist': artist, 'album': album,
        'track_number': track, 'disc_number': disc, 'year': '',
        'isrc': isrc, 'mbid': mbid, 'duration_ms': duration_ms,
    }


def _api_track(*, name='', track_number=0, disc_number=1,
               isrc='', mbid='', duration_ms=0, external_ids=None):
    out = {
        'name': name,
        'track_number': track_number,
        'disc_number': disc_number,
        'duration_ms': duration_ms,
        'artists': [],
    }
    if isrc:
        out['isrc'] = isrc
    if mbid:
        out['musicbrainz_id'] = mbid
    if external_ids:
        out['external_ids'] = external_ids
    return out


# ---------------------------------------------------------------------------
# find_exact_id_matches — direct unit tests
# ---------------------------------------------------------------------------


def test_mbid_exact_match_pairs_file_to_track():
    """File with MBID tag matches the track carrying the same MBID,
    even when title is completely wrong."""
    files = ['/a/scrambled.flac']
    file_tags = {
        '/a/scrambled.flac': _tags(
            title='Scrambled Filename', mbid='abc-123-mbid',
        ),
    }
    tracks = [
        _api_track(name='Real Track Name', mbid='abc-123-mbid'),
    ]
    result = find_exact_id_matches(files, file_tags, tracks)
    assert len(result['matches']) == 1
    assert result['matches'][0]['file'] == '/a/scrambled.flac'
    assert result['matches'][0]['match_type'] == 'mbid'
    assert result['matches'][0]['confidence'] == EXACT_MATCH_CONFIDENCE


def test_isrc_exact_match_pairs_file_to_track():
    files = ['/a/track.flac']
    file_tags = {
        '/a/track.flac': _tags(title='Foo', isrc='USRC11234567'),
    }
    tracks = [_api_track(name='Real', isrc='USRC11234567')]
    result = find_exact_id_matches(files, file_tags, tracks)
    assert len(result['matches']) == 1
    assert result['matches'][0]['match_type'] == 'isrc'


def test_isrc_normalization_strips_dashes_and_spaces():
    """File tag ``USRC11234567`` should match source ISRC ``US-RC1-12-34567``
    — same identifier, different formatting. Picard writes compact;
    some sources return hyphenated."""
    files = ['/a/f.flac']
    file_tags = {'/a/f.flac': _tags(isrc='USRC11234567')}
    tracks = [_api_track(name='X', isrc='US-RC1-12-34567')]
    result = find_exact_id_matches(files, file_tags, tracks)
    assert len(result['matches']) == 1


def test_mbid_takes_priority_over_isrc():
    """When both identifiers are present and they'd point at different
    tracks, MBID wins. ISRC can be shared across remasters; MBID is
    per-recording."""
    files = ['/a/f.flac']
    file_tags = {'/a/f.flac': _tags(isrc='SAME', mbid='real-mbid')}
    tracks = [
        _api_track(name='Wrong Recording', isrc='SAME', mbid='different-mbid'),
        _api_track(name='Right Recording', mbid='real-mbid'),
    ]
    result = find_exact_id_matches(files, file_tags, tracks)
    assert len(result['matches']) == 1
    assert result['matches'][0]['track']['name'] == 'Right Recording'
    assert result['matches'][0]['match_type'] == 'mbid'


def test_isrc_via_external_ids_dict_matches():
    """Spotify exposes ISRC under ``external_ids.isrc``, not as a
    top-level field. Matcher must check both shapes."""
    files = ['/a/f.flac']
    file_tags = {'/a/f.flac': _tags(isrc='USRC11234567')}
    tracks = [_api_track(name='X', external_ids={'isrc': 'USRC11234567'})]
    result = find_exact_id_matches(files, file_tags, tracks)
    assert len(result['matches']) == 1


def test_no_id_match_returns_empty():
    """File and track both have IDs, but they don't match → no exact
    match. (Caller falls back to fuzzy.)"""
    files = ['/a/f.flac']
    file_tags = {'/a/f.flac': _tags(mbid='different-id')}
    tracks = [_api_track(name='X', mbid='another-id')]
    result = find_exact_id_matches(files, file_tags, tracks)
    assert not result['matches']


def test_each_id_match_uses_track_at_most_once():
    """Two files with the same MBID — only the first one wins. Caller
    deals with the leftover (probably a duplicate/extra file)."""
    files = ['/a/first.flac', '/a/second.flac']
    file_tags = {
        '/a/first.flac': _tags(mbid='shared'),
        '/a/second.flac': _tags(mbid='shared'),
    }
    tracks = [_api_track(name='Track', mbid='shared')]
    result = find_exact_id_matches(files, file_tags, tracks)
    assert len(result['matches']) == 1
    assert len(result['used_files']) == 1


# ---------------------------------------------------------------------------
# duration_sanity_ok — direct unit tests
# ---------------------------------------------------------------------------


def test_duration_within_tolerance_passes():
    assert duration_sanity_ok(180_000, 180_000) is True
    assert duration_sanity_ok(180_000, 181_500) is True
    assert duration_sanity_ok(180_000, 180_000 - DURATION_TOLERANCE_MS) is True


def test_duration_outside_tolerance_fails():
    assert duration_sanity_ok(180_000, 180_000 + DURATION_TOLERANCE_MS + 1) is False
    assert duration_sanity_ok(180_000, 90_000) is False
    # The Mr. Morale Auntie-Diaries-vs-Rich-Interlude case from the bug
    # report: 281s file vs 103s expected — gross mismatch, must reject.
    assert duration_sanity_ok(281_000, 103_000) is False


def test_duration_missing_either_side_passes():
    """Don't reject when we can't confirm. Files with no length info
    (corrupt headers, etc.) defer to the fuzzy scorer."""
    assert duration_sanity_ok(0, 180_000) is True
    assert duration_sanity_ok(180_000, 0) is True
    assert duration_sanity_ok(0, 0) is True


# ---------------------------------------------------------------------------
# match_files_to_tracks — end-to-end with the new fast paths
# ---------------------------------------------------------------------------


def test_mbid_match_short_circuits_fuzzy_scoring():
    """File with MBID + completely wrong title still matches the right
    track via MBID. Demonstrates the fast-path bypassing fuzzy scoring."""
    files = ['/a/file.flac']
    file_tags = {
        '/a/file.flac': _tags(
            title='Completely Wrong Title',
            artist='Wrong Artist',
            track=99, disc=99,
            mbid='real-mbid',
        ),
    }
    tracks = [
        _api_track(name='Real Title', track_number=1, disc_number=1, mbid='real-mbid'),
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 1
    assert result['matches'][0]['match_type'] == 'mbid'
    assert result['matches'][0]['confidence'] == EXACT_MATCH_CONFIDENCE


def test_id_matched_files_excluded_from_fuzzy_phase():
    """File matched in phase 1 (exact ID) shouldn't be considered in
    phase 3 (fuzzy). Otherwise it could end up matched twice."""
    files = ['/a/exact.flac', '/a/fuzzy.flac']
    file_tags = {
        '/a/exact.flac': _tags(title='Track A', mbid='mbid-a'),
        '/a/fuzzy.flac': _tags(title='Track B', track=2, disc=1),
    }
    tracks = [
        _api_track(name='Track A', track_number=1, disc_number=1, mbid='mbid-a'),
        _api_track(name='Track B', track_number=2, disc_number=1),
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 2
    file_set = {m['file'] for m in result['matches']}
    assert file_set == {'/a/exact.flac', '/a/fuzzy.flac'}


def test_duration_gate_rejects_wrong_disc_collision_in_fuzzy_phase():
    """The Mr. Morale bug case re-cast as a duration veto. File has
    the audio length of the disc-2 track, API track is the disc-1 track
    with the same number. Pre-fix: would have matched on track_number
    alone. Post-fix: even after the disc-aware scoring, the duration
    gate stops it."""
    files = ['/a/track06.flac']
    file_tags = {
        '/a/track06.flac': _tags(
            title='', track=6, disc=1,  # wrong/missing disc tag
            duration_ms=281_000,  # actual audio is 4:41
        ),
    }
    tracks = [
        _api_track(
            name='Rich (Interlude)', track_number=6, disc_number=1,
            duration_ms=103_000,  # 1:43
        ),
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='Mr. Morale', similarity=_sim, quality_rank=_qrank,
    )
    # Duration gate rejects → file unmatched (correct).
    assert not result['matches']
    assert result['unmatched_files'] == ['/a/track06.flac']


def test_duration_gate_within_tolerance_allows_normal_match():
    """File and track durations agree within tolerance — match proceeds
    normally via fuzzy scoring."""
    files = ['/a/track.flac']
    file_tags = {
        '/a/track.flac': _tags(
            title='Father Time', track=5, disc=1, duration_ms=362_000,
        ),
    }
    tracks = [
        _api_track(
            name='Father Time', track_number=5, disc_number=1,
            duration_ms=363_500,  # 1.5s drift — within 3s tolerance
        ),
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 1


def test_no_durations_anywhere_falls_through_to_fuzzy():
    """Either side missing duration → gate doesn't apply, fuzzy
    scoring handles it. Catches files with corrupt audio headers."""
    files = ['/a/track.flac']
    file_tags = {
        '/a/track.flac': _tags(
            title='Father Time', track=5, disc=1, duration_ms=0,
        ),
    }
    tracks = [_api_track(name='Father Time', track_number=5, disc_number=1)]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 1


def test_deezer_seconds_duration_converted_to_ms():
    """Deezer's API returns ``duration`` in seconds, not ms. The matcher
    must convert before applying the tolerance check — otherwise a
    180-second track looks like a 180-millisecond track and fails the
    sanity gate against any real file."""
    files = ['/a/track.flac']
    file_tags = {
        '/a/track.flac': _tags(
            title='Song', track=1, disc=1, duration_ms=180_000,
        ),
    }
    # Deezer-style track — duration is 180 (seconds)
    tracks = [{
        'name': 'Song', 'track_number': 1, 'disc_number': 1,
        'duration': 180, 'artists': [], 'source': 'deezer',
    }]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    # 180 seconds → 180_000 ms → matches file's 180_000 ms within tolerance
    assert len(result['matches']) == 1


def test_track_duration_source_aware_dispatch():
    """`_track_duration_ms` must route via the `source` field — not
    fall back to magnitude heuristic — so providers with edge-case
    durations (sub-30s real tracks, intros, interludes) don't trigger
    false unit conversion."""
    from core.imports.album_matching import _track_duration_ms

    # Spotify-style — explicit ms field, treat as-is
    spotify_track = {'duration_ms': 180_000, 'source': 'spotify'}
    assert _track_duration_ms(spotify_track) == 180_000

    # Deezer-style — `duration` field in seconds, convert
    deezer_track = {'duration': 180, 'source': 'deezer'}
    assert _track_duration_ms(deezer_track) == 180_000

    # iTunes — duration_ms (their internal field is `trackTimeMillis`
    # but `_build_album_track_entry` normalises to `duration_ms`)
    itunes_track = {'duration_ms': 200_000, 'source': 'itunes'}
    assert _track_duration_ms(itunes_track) == 200_000

    # Source via _source alias also works (normalize_import_context legacy)
    legacy_source = {'duration_ms': 150_000, '_source': 'spotify'}
    assert _track_duration_ms(legacy_source) == 150_000


def test_track_duration_short_real_track_not_misconverted_with_known_source():
    """An actual sub-30s track on Spotify (intro/interlude/skit) —
    duration_ms is genuinely small. Source-aware dispatch must take
    spotify_ms_value as-is and NOT × 1000 it via the magnitude
    heuristic. Pre-fix this would have been hit by:

        20_000 ms (a 20-second intro) > 0 and < 30000 → converted to
        20_000_000 ms = 5.5 hours. Wrong.

    Post-fix: source='spotify' is in MS list, value taken as-is.
    """
    from core.imports.album_matching import _track_duration_ms

    short_intro = {'duration_ms': 20_000, 'source': 'spotify'}
    assert _track_duration_ms(short_intro) == 20_000


def test_track_duration_unknown_source_falls_back_to_heuristic():
    """No source field — apply the legacy magnitude heuristic so
    tests / mocks without source still work. < 30000 = seconds."""
    from core.imports.album_matching import _track_duration_ms

    no_source_seconds = {'duration': 180}  # heuristic: < 30000 → seconds
    assert _track_duration_ms(no_source_seconds) == 180_000

    no_source_ms = {'duration_ms': 200_000}  # heuristic: > 30000 → ms
    assert _track_duration_ms(no_source_ms) == 200_000


def test_album_track_entry_propagates_isrc_and_mbid_from_source():
    """Production-path guard: the metadata-source layer
    (`_build_album_track_entry`) must propagate ISRC + MBID from the
    raw track responses, otherwise the matcher's fast paths never fire
    in production even though they pass in unit tests.

    Spotify shape: ``external_ids.isrc`` (nested dict).
    iTunes shape: top-level ``isrc``.
    """
    from core.metadata.album_tracks import _build_album_track_entry

    spotify_shape = {
        'id': 'spotify-track',
        'name': 'Test',
        'external_ids': {'isrc': 'USRC11234567', 'mbid': 'mb-123'},
        'duration_ms': 200_000,
        'track_number': 1,
        'disc_number': 1,
    }
    entry = _build_album_track_entry(spotify_shape, {'name': 'Album'}, 'spotify')
    assert entry['isrc'] == 'USRC11234567'
    assert entry['musicbrainz_id'] == 'mb-123'

    itunes_shape = {
        'id': 'itunes-track',
        'name': 'Test',
        'isrc': 'USRC11234567',
        'duration_ms': 200_000,
        'track_number': 1,
        'disc_number': 1,
    }
    entry = _build_album_track_entry(itunes_shape, {'name': 'Album'}, 'itunes')
    assert entry['isrc'] == 'USRC11234567'

    # No identifiers — entry has empty strings (not None / missing keys),
    # so the matcher's `_track_identifier()` returns empty cleanly.
    bare_shape = {
        'id': 'bare', 'name': 'Test',
        'duration_ms': 200_000, 'track_number': 1, 'disc_number': 1,
    }
    entry = _build_album_track_entry(bare_shape, {'name': 'Album'}, 'unknown')
    assert entry['isrc'] == ''
    assert entry['musicbrainz_id'] == ''


def test_picard_tagged_library_full_album_via_mbid_only():
    """Realistic Picard-tagged library: every file has MBID, no useful
    title-disc-track agreement needed. Whole album should pair via the
    fast path on the first phase."""
    files = [f'/a/picard_{i}.flac' for i in range(1, 11)]
    file_tags = {
        f: _tags(
            title=f'mangled name {i}',  # title doesn't help
            track=99 - i, disc=99,      # position info is wrong
            mbid=f'mbid-{i}',
        )
        for i, f in enumerate(files, start=1)
    }
    tracks = [
        _api_track(
            name=f'Real Track {i}',
            track_number=i, disc_number=1,
            mbid=f'mbid-{i}',
        )
        for i in range(1, 11)
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 10
    # All matched via MBID, full confidence
    for m in result['matches']:
        assert m['match_type'] == 'mbid'
        assert m['confidence'] == EXACT_MATCH_CONFIDENCE
