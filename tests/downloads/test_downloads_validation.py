"""Tests for core/downloads/validation.py — SoundCloud preview filter.

The SoundCloud anonymous tier serves a ~30s preview clip for tracks
gated behind Go+ / login. ``filter_soundcloud_previews`` drops these
candidates before they reach the matcher, the modal cache, or the
manual-pick download path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.downloads import validation
from core.downloads.validation import (
    filter_soundcloud_previews,
    get_valid_candidates,
)


@dataclass
class _Track:
    duration_ms: int
    name: str = 'Song'
    artists: tuple[str, ...] = ('Artist',)


@dataclass
class _Candidate:
    username: str
    duration: Optional[int]  # milliseconds
    title: str = 'Song'
    artist: str = 'Artist'
    filename: str = 'candidate'


class _MatchingEngine:
    def score_track_match(self, **kwargs):
        return 0.99, 'core_title_match'

    def normalize_string(self, text):
        return (text or '').lower()


def test_drops_soundcloud_30s_preview_when_expected_long():
    """A 30s SC candidate against a 5-minute expected track is the
    canonical preview-snippet case — must be dropped."""
    expected = _Track(duration_ms=338_000)  # ~5:38
    cands = [
        _Candidate(username='soundcloud', duration=30_000, title='Preview'),
        _Candidate(username='soundcloud', duration=338_000, title='Real'),
    ]
    result = filter_soundcloud_previews(cands, expected)
    assert len(result) == 1
    assert result[0].title == 'Real'


def test_drops_under_half_expected_duration():
    """SC candidate at 100s against 300s expected = clearly truncated /
    wrong content. Must be dropped even if not at the 30s boundary."""
    expected = _Track(duration_ms=300_000)
    cand = _Candidate(username='soundcloud', duration=100_000)
    assert filter_soundcloud_previews([cand], expected) == []


def test_keeps_soundcloud_when_expected_track_is_short():
    """Genuinely short SC tracks (intros, sound effects, sub-minute
    songs) must pass through when the expected track is also short.
    Filter only kicks in when expected > 60s."""
    expected = _Track(duration_ms=45_000)  # 45s expected
    cand = _Candidate(username='soundcloud', duration=30_000)
    result = filter_soundcloud_previews([cand], expected)
    assert result == [cand]


def test_does_not_filter_non_soundcloud_sources():
    """A 30s candidate from another streaming source isn't a SoundCloud
    preview — leave it for the generic matching engine to score."""
    expected = _Track(duration_ms=338_000)
    yt = _Candidate(username='youtube', duration=30_000)
    tidal = _Candidate(username='tidal', duration=30_000)
    assert filter_soundcloud_previews([yt, tidal], expected) == [yt, tidal]


def test_returns_input_unchanged_without_expected_duration():
    """Without a Spotify-track / expected duration we can't reason
    about previews — pass everything through."""
    cands = [
        _Candidate(username='soundcloud', duration=30_000),
        _Candidate(username='soundcloud', duration=300_000),
    ]
    assert filter_soundcloud_previews(cands, None) == cands
    assert filter_soundcloud_previews(cands, _Track(duration_ms=0)) == cands


def test_empty_input_returns_empty_list():
    assert filter_soundcloud_previews([], _Track(duration_ms=200_000)) == []


def test_keeps_soundcloud_candidate_at_threshold():
    """Boundary check: 35s candidate against 200s expected — exactly
    at the 35s preview boundary, but 35s is also above
    expected*0.5 (100s) check (35 < 100, so still drops). Use a
    higher value to confirm the just-above threshold passes."""
    expected = _Track(duration_ms=200_000)  # 200s
    # 110s passes both checks: > 35s AND > 100s (half of 200s)
    cand = _Candidate(username='soundcloud', duration=110_000)
    assert filter_soundcloud_previews([cand], expected) == [cand]


def test_rejects_tidal_candidate_that_would_fail_integrity_duration(monkeypatch):
    """Structured sources should not download candidates that post-processing
    will immediately quarantine for the same duration mismatch."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _Track(duration_ms=338_000)
    wrong_tidal = _Candidate(username='tidal', duration=30_000)

    assert get_valid_candidates([wrong_tidal], expected, 'Artist Song') == []


def test_keeps_tidal_candidate_inside_integrity_duration_tolerance(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _Track(duration_ms=338_000)
    tidal = _Candidate(username='tidal', duration=340_000)

    result = get_valid_candidates([tidal], expected, 'Artist Song')

    assert result == [tidal]


def test_rejects_torrent_title_match_from_wrong_artist(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _Track(duration_ms=180_000, name='The Man I Need', artists=('Olivia Dean',))
    wrong_artist = _Candidate(
        username='torrent',
        duration=None,
        title='The Man I Need',
        artist='Tinkabelle',
    )

    assert get_valid_candidates([wrong_artist], expected, 'Olivia Dean The Man I Need') == []


def test_keeps_torrent_title_match_from_expected_artist(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _Track(duration_ms=180_000, name='The Man I Need', artists=('Olivia Dean',))
    correct_artist = _Candidate(
        username='torrent',
        duration=None,
        title='The Man I Need',
        artist='Olivia Dean',
    )

    result = get_valid_candidates([correct_artist], expected, 'Olivia Dean The Man I Need')

    assert result == [correct_artist]


def test_keeps_torrent_title_match_when_artist_is_indexer_fallback(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _Track(duration_ms=180_000, name='The Man I Need', artists=('Olivia Dean',))
    candidate = _Candidate(
        username='torrent',
        duration=None,
        title='The Man I Need',
        artist='Indexer',
    )
    candidate._source_metadata = {'indexer': 'Indexer'}

    result = get_valid_candidates([candidate], expected, 'Olivia Dean The Man I Need')

    assert result == [candidate]


class _DispatchEngine:
    """Records which rows hit the streaming scorer vs the Soulseek filename matcher."""

    def __init__(self):
        self.slskd_usernames = []

    def score_track_match(self, **kwargs):
        return 0.99, 'core_title_match'

    def normalize_string(self, text):
        return (text or '').lower()

    def find_best_slskd_matches_enhanced(self, spotify_track, results, max_peer_queue=0):
        self.slskd_usernames.append([getattr(r, 'username', None) for r in results])
        return list(results)


class _SoulseekQuality:
    def __init__(self):
        self.batches = []

    def filter_results_by_quality_preference(self, cands, profile_id=None):
        self.batches.append([getattr(c, 'username', None) for c in cands])
        return list(cands)


def _peer_and_stream_hits():
    from core.download_plugins.types import TrackResult

    def _tr(**over):
        base = dict(
            username='alice',
            filename='Artist/Album/01 - Song.flac',
            size=1,
            bitrate=1411,
            duration=180_000,
            quality='flac',
            free_upload_slots=1,
            upload_speed=1,
            queue_length=0,
            artist='Artist',
            title='Song',
        )
        base.update(over)
        return TrackResult(**base)

    peer = _tr()
    youtube = _tr(
        username='youtube', filename='aaaaaaaaaaa||Song', quality='opus', bitrate=160,
    )
    tidal = _tr(
        username='tidal', filename='tid||Artist - Song', quality='flac', bitrate=1411,
    )
    return peer, youtube, tidal


def test_soulseek_first_pool_does_not_run_p2p_matcher_on_streaming(monkeypatch):
    """Best-quality hybrid: a Soulseek peer first must not score Tidal/YouTube as paths."""
    engine = _DispatchEngine()
    slsk = _SoulseekQuality()

    class _Orch:
        def client(self, name):
            return slsk if name == 'soulseek' else None

    monkeypatch.setattr(validation, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([], True),
    )
    peer, youtube, tidal = _peer_and_stream_hits()
    result = get_valid_candidates(
        [peer, youtube, tidal], _Track(duration_ms=180_000), 'Artist Song',
    )
    assert peer in result
    assert youtube in result
    assert tidal in result
    assert engine.slskd_usernames == [['alice']]
    assert slsk.batches == [['alice']]


def test_exact_path_identity_recovers_low_generic_score_without_rescuing_sibling(monkeypatch):
    class LowScoreEngine(_DispatchEngine):
        def find_best_slskd_matches_enhanced(self, spotify_track, results, max_peer_queue=0):
            for row in results:
                row.confidence = 0.45
            return []

    slsk = _SoulseekQuality()

    class Orch:
        def client(self, name):
            return slsk

    monkeypatch.setattr(validation, 'matching_engine', LowScoreEngine())
    monkeypatch.setattr(validation, 'download_orchestrator', Orch())
    target = _Track(duration_ms=180_000, name='SexyBack', artists=('Justin Timberlake',))
    candidate, _, _ = _peer_and_stream_hits()
    candidate.filename = ('Justin Timberlake/FutureSex+LoveSounds/'
                          'Justin Timberlake - FutureSex+LoveSounds - 02 - SexyBack.flac')
    sibling, _, _ = _peer_and_stream_hits()
    sibling.filename = 'Justin Timberlake/FutureSex+LoveSounds/03 - My Love.flac'

    result = get_valid_candidates([candidate, sibling], target, 'Justin Timberlake SexyBack')

    assert result == [candidate]
    assert candidate.confidence == 0.45


def test_exact_path_identity_recovery_requires_the_artist_in_the_path(monkeypatch):
    """The engine's short-title guard sinks exact titles under fuzzy-artist
    folders on purpose; recovery must not undo it."""
    class LowScoreEngine(_DispatchEngine):
        def find_best_slskd_matches_enhanced(self, spotify_track, results, max_peer_queue=0):
            for row in results:
                row.confidence = 0.30
            return []

    slsk = _SoulseekQuality()

    class Orch:
        def client(self, name):
            return slsk

    monkeypatch.setattr(validation, 'matching_engine', LowScoreEngine())
    monkeypatch.setattr(validation, 'download_orchestrator', Orch())
    target = _Track(duration_ms=180_000, name='Team', artists=('Lorde',))
    other_artist, _, _ = _peer_and_stream_hits()
    other_artist.filename = 'Lord Huron/Strange Trails/05 - Team.flac'

    assert get_valid_candidates([other_artist], target, 'Lorde Team') == []


def test_soulseek_first_pool_still_duration_gates_tidal(monkeypatch):
    engine = _DispatchEngine()
    slsk = _SoulseekQuality()

    class _Orch:
        def client(self, name):
            return slsk if name == 'soulseek' else None

    monkeypatch.setattr(validation, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    expected = _Track(duration_ms=338_000)
    peer, _, tidal = _peer_and_stream_hits()
    peer.duration = 338_000
    tidal.duration = 30_000
    result = get_valid_candidates([peer, tidal], expected, 'Artist Song')
    assert peer in result
    assert tidal not in result
    assert engine.slskd_usernames == [['alice']]


def test_failed_streaming_does_not_drop_soulseek_rows(monkeypatch):
    """Tidal-first + all streaming rejected must still score the Soulseek hit."""
    engine = _DispatchEngine()
    slsk = _SoulseekQuality()

    class _Orch:
        def client(self, name):
            return slsk if name == 'soulseek' else None

    monkeypatch.setattr(validation, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    expected = _Track(duration_ms=338_000)
    peer, _, tidal = _peer_and_stream_hits()
    peer.duration = 338_000
    tidal.duration = 30_000
    result = get_valid_candidates([tidal, peer], expected, 'Artist Song')
    assert peer in result
    assert tidal not in result
    assert engine.slskd_usernames == [['alice']]


# ---------------------------------------------------------------------------
# Version-marker positions (#1640) + strict duration pre-check
# ---------------------------------------------------------------------------


def _deezer_expected(**over):
    base = dict(duration_ms=214_000, name='Live Again', artists=('HALO',))
    base.update(over)
    return _Track(**base)


def _deezer_candidate(**over):
    base = dict(username='deezer_dl', duration=214_000,
                title='Live Again', artist='HALO')
    base.update(over)
    return _Candidate(**base)


def test_live_in_canonical_title_is_not_a_version_marker(monkeypatch):
    """#1640: 'Live Again' is the canonical track title, not a request for a
    live recording. The expected-side version flip must only fire for
    version markers in qualifying positions (suffix/parenthetical), so a
    correct original whose title merely formats the keyword differently
    ('LiveAgain') is not rejected as version_conflict."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected()
    correct = _deezer_candidate(title='LiveAgain')

    result = get_valid_candidates([correct], expected, 'HALO Live Again')

    assert result == [correct]


def test_saturday_night_live_is_not_a_version_marker(monkeypatch):
    """A keyword at the end of the canonical title with no separator is not
    a version qualifier — 'Saturday Night Live' must not flip
    expected_is_version either."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected(name='Saturday Night Live')
    correct = _deezer_candidate(title='Saturday Night Live')

    result = get_valid_candidates([correct], expected,
                                  'Artist Saturday Night Live')

    assert result == [correct]


def test_parenthesized_live_still_marks_a_version(monkeypatch):
    """'Song (Live)' IS a version request: non-live candidates must still be
    rejected, and the live-marked one must pass."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected(name='Song (Live)', artists=('Band',))
    live = _deezer_candidate(title='Song (Live)', artist='Band')
    studio = _deezer_candidate(title='Song', artist='Band')

    assert get_valid_candidates([live], expected, 'Band Song') == [live]
    assert get_valid_candidates([studio], expected, 'Band Song') == []


def test_dash_live_still_marks_a_version(monkeypatch):
    """'Song - Live at Wembley' IS a version request: the dash-qualifier
    keeps flipping expected_is_version."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected(name='Song \u2013 Live at Wembley',
                                artists=('Band',))
    live = _deezer_candidate(title='Song (Live at Wembley)', artist='Band')
    studio = _deezer_candidate(title='Song', artist='Band')

    assert get_valid_candidates([live], expected, 'Band Song') == [live]
    assert get_valid_candidates([studio], expected, 'Band Song') == []


def test_wrong_version_still_penalized_when_original_wanted(monkeypatch):
    """Expecting the original 'Live Again' (canonical title): a candidate
    carrying a real version marker the expected lacks ('Extended Mix')
    is still a wrong version and must be rejected."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected()
    extended = _deezer_candidate(title='Live Again (Extended Mix)')

    assert get_valid_candidates([extended], expected, 'HALO Live Again') == []


def test_live_recording_of_canonical_live_title_is_still_a_wrong_version(monkeypatch):
    """Hostile-review hole: expecting the canonical original 'Live Again', a
    candidate 'Live Again (Live)' is a genuinely different recording — the
    qualifying marker must penalize it even though the expected title has
    the word 'live' too. The plain 'Live Again' still passes."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected()
    live_recording = _deezer_candidate(title='Live Again (Live)')
    original = _deezer_candidate(title='Live Again')

    assert get_valid_candidates([live_recording], expected, 'HALO Live Again') == []
    assert get_valid_candidates([original], expected, 'HALO Live Again') == [original]


def test_multiword_keyword_in_parens_still_marks_expected_version(monkeypatch):
    """'Song (Sped Up)' IS a version request: the multi-word keyword in a
    qualifying position flips expected_is_version; the unmarked candidate
    is rejected and the marked one passes."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected(name='Song (Sped Up)', artists=('Band',))
    sped = _deezer_candidate(title='Song (Sped Up)', artist='Band')
    studio = _deezer_candidate(title='Song', artist='Band')

    assert get_valid_candidates([sped], expected, 'Band Song') == [sped]
    assert get_valid_candidates([studio], expected, 'Band Song') == []


def test_nested_parens_live_still_marks_expected_version(monkeypatch):
    """'Song (feat. X) (Live)' IS a version request even with an earlier
    parenthetical group: the trailing (Live) group qualifies."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected(name='Song (feat. X) (Live)', artists=('Band',))
    live = _deezer_candidate(title='Song (Live)', artist='Band')
    studio = _deezer_candidate(title='Song', artist='Band')

    assert get_valid_candidates([live], expected, 'Band Song') == [live]
    assert get_valid_candidates([studio], expected, 'Band Song') == []


def test_unparsed_artist_separator_does_not_create_a_version_marker(monkeypatch):
    """Hostile-review BLOCK 2: SoundCloud keeps an unparsed 'HALO: ' artist
    prefix in the title and ':' is a version separator — but the canonical
    'live' is not a qualifier (same occurrence count as the expected title),
    so the correct original must not be rejected. Base accepted this."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected()
    correct = _deezer_candidate(username='soundcloud', title='HALO: Live Again')

    result = get_valid_candidates([correct], expected, 'HALO Live Again')

    assert result == [correct]


def test_type_beat_chaff_still_penalized(monkeypatch):
    """The raw word check's raison d'etre: 'Drake Type Beat' is never the
    real 'Hotline Bling' — bare-suffix chaff that never qualifies as a
    marker is still penalized when the expected title lacks the word."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected(name='Hotline Bling', artists=('Drake',),
                                duration_ms=267_000)
    chaff = _deezer_candidate(username='soundcloud', duration=200_000,
                              title='Drake Type Beat', artist='Some Producer')

    assert get_valid_candidates([chaff], expected, 'Drake Hotline Bling') == []


def test_user_pinned_duration_tolerance_stays_symmetric(monkeypatch):
    """A user-pinned post_processing.duration_tolerance_seconds is honoured
    symmetrically (mirrors file_integrity): with 5s pinned, a candidate 7s
    longer than catalog is rejected pre-download — no 15s longer-side
    allowance."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    real_get = validation.config_manager.get

    def _fake_get(key, default=None):
        if key == 'post_processing.duration_tolerance_seconds':
            return 5
        return real_get(key, default)

    monkeypatch.setattr(validation.config_manager, 'get', _fake_get)
    expected = _deezer_expected()
    longer = _deezer_candidate(duration=221_000)

    assert get_valid_candidates([longer], expected, 'HALO Live Again') == []


def test_strict_precheck_inherits_longer_version_allowance(monkeypatch):
    """#1640 secondary: the strict pre-download check for deezer_dl used
    3s/5s while post-download integrity grants 15s on the longer side
    (_LONGER_VERSION_TOLERANCE_S, #937). A candidate 7s longer than the
    catalog duration must pass the pre-check, matching what the import
    would accept."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected()
    longer = _deezer_candidate(duration=221_000)

    result = get_valid_candidates([longer], expected, 'HALO Live Again')

    assert result == [longer]


def test_strict_precheck_still_rejects_shorter_candidates(monkeypatch):
    """The longer-side allowance must not loosen the short side: a candidate
    14s shorter than catalog is still rejected pre-download."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected()
    shorter = _deezer_candidate(duration=200_000)

    assert get_valid_candidates([shorter], expected, 'HALO Live Again') == []


def test_strict_precheck_still_rejects_much_longer_candidates(monkeypatch):
    """A candidate 96s longer (the 5:10 extended mix) is beyond even the
    15s longer-version allowance — still rejected."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    expected = _deezer_expected()
    extended = _deezer_candidate(duration=310_000)

    assert get_valid_candidates([extended], expected, 'HALO Live Again') == []
