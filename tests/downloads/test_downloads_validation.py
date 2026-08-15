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
from core.downloads.validation import filter_soundcloud_previews, get_valid_candidates


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

    def filter_results_by_quality_preference(self, cands):
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
