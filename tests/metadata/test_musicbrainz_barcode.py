"""Unit tests for MusicBrainz barcode lookup and release-group original date resolution.

Verifies:
1. MusicBrainzClient.search_release_by_barcode builds the correct Lucene query.
2. _find_best_release prioritizes an exact barcode match over text-only candidates.
3. Album consistency writes ORIGINALDATE and ORIGINALYEAR from release-group first-release-date.
4. _process_musicbrainz_source resolves via barcode when present in metadata.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import pytest

from core.musicbrainz_client import MusicBrainzClient
from core.album_consistency import _find_best_release, _score_release
from core.metadata import source as ms


# ── 1. MusicBrainzClient barcode query tests ──────────────────────────────

@pytest.fixture
def mb_client():
    c = MusicBrainzClient("SoulSync", "2")
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"releases": [{"id": "mbid-123", "title": "Jazz", "barcode": "00602527779340"}]})
    c.session = MagicMock()
    c.session.get = MagicMock(return_value=resp)
    return c


def test_search_release_by_barcode_query(mb_client):
    results = mb_client.search_release_by_barcode("00602527779340")
    assert len(results) == 1
    assert results[0]["id"] == "mbid-123"

    _, kwargs = mb_client.session.get.call_args
    assert kwargs["params"]["query"] == "barcode:00602527779340"
    assert kwargs["params"]["fmt"] == "json"


def test_search_release_by_barcode_cleans_non_digits(mb_client):
    mb_client.search_release_by_barcode("006-025-277-79340 ")
    _, kwargs = mb_client.session.get.call_args
    assert kwargs["params"]["query"] == "barcode:00602527779340"


def test_search_release_by_barcode_empty(mb_client):
    assert mb_client.search_release_by_barcode("") == []
    assert mb_client.search_release_by_barcode(None) == []
    assert not mb_client.session.get.called


# ── 2. Release scoring with barcode ──────────────────────────────────────

def test_score_release_gives_bonus_for_matching_barcode():
    release_with_bc = {
        "media": [{"position": 1, "format": "Digital Media", "tracks": [{"id": "t1"}, {"id": "t2"}]}],
        "status": "Official",
        "country": "XW",
        "barcode": "00602527779340",
        "date": "2011-06-27",
    }
    release_other = {
        "media": [{"position": 1, "format": "Digital Media", "tracks": [{"id": "t1"}, {"id": "t2"}]}],
        "status": "Official",
        "country": "XW",
        "barcode": "11111111111111",
        "date": "2011-06-27",
    }

    score_match = _score_release(release_with_bc, expected_track_count=2, target_barcode="00602527779340")
    score_other = _score_release(release_other, expected_track_count=2, target_barcode="00602527779340")

    assert score_match >= score_other + 50


# ── 3. _find_best_release barcode prioritization ─────────────────────────

def test_find_best_release_picks_barcode_match():
    # Setup candidate: Reissue has matching barcode; Vinyl has different barcode
    reissue_bc = "00602527779340"
    reissue_rel = {
        "id": "rel-reissue-2011",
        "title": "Jazz",
        "status": "Official",
        "country": "XW",
        "barcode": reissue_bc,
        "date": "2011-06-27",
        "artist-credit": [{"name": "Queen", "artist": {"id": "art-queen", "name": "Queen"}}],
        "media": [{"position": 1, "format": "Digital Media", "tracks": [{"id": f"t{i}"} for i in range(12)]}],
        "release-group": {"id": "rg-queen-jazz", "first-release-date": "1978-11-10", "primary-type": "Album"},
    }
    other_rel = {
        "id": "rel-vinyl-1978",
        "title": "Jazz",
        "status": "Official",
        "country": "GB",
        "barcode": "5099912345678",
        "date": "1978-11-10",
        "artist-credit": [{"name": "Queen", "artist": {"id": "art-queen", "name": "Queen"}}],
        "media": [{"position": 1, "format": "Vinyl", "tracks": [{"id": f"t{i}"} for i in range(12)]}],
        "release-group": {"id": "rg-queen-jazz", "first-release-date": "1978-11-10", "primary-type": "Album"},
    }

    mock_client = SimpleNamespace(
        search_release_by_barcode=Mock(return_value=[{"id": "rel-reissue-2011"}]),
        search_release=Mock(return_value=[{"id": "rel-vinyl-1978"}]),
        get_release=Mock(side_effect=lambda rid, **kw: reissue_rel if rid == "rel-reissue-2011" else other_rel),
    )
    mock_service = SimpleNamespace(
        mb_client=mock_client,
        match_release=Mock(return_value=None),
        match_artist=Mock(return_value={"mbid": "art-queen"}),
    )

    chosen = _find_best_release("Jazz", "Queen", track_count=12, mb_service=mock_service, barcode=reissue_bc)
    assert chosen is not None
    assert chosen["id"] == "rel-reissue-2011"
    assert chosen["release-group"]["first-release-date"] == "1978-11-10"


# ── 4. _process_musicbrainz_source barcode resolution ───────────────────

class DummyConfig:
    def get(self, key, default=None):
        return default


def test_process_musicbrainz_source_uses_barcode(monkeypatch):
    monkeypatch.setattr(ms, "mb_release_cache", {})
    monkeypatch.setattr(ms, "mb_release_detail_cache", {})

    reissue_rel = {
        "id": "rel-reissue-2011",
        "title": "Jazz",
        "status": "Official",
        "artist-credit": [{"name": "Queen", "artist": {"id": "art-queen", "name": "Queen"}}],
        "release-group": {"id": "rg-queen-jazz", "first-release-date": "1978-11-10", "primary-type": "Album"},
        "media": [{"position": 1, "format": "Digital Media", "tracks": [{"id": "t1", "position": 1, "title": "Mustapha", "recording": {"id": "rec-1"}}]}]
    }

    client = SimpleNamespace(
        search_release_by_barcode=Mock(return_value=[{"id": "rel-reissue-2011"}]),
        get_release=Mock(return_value=reissue_rel),
        get_recording=Mock(return_value={"isrcs": [], "artist-credit": reissue_rel["artist-credit"]}),
        get_artist=Mock(return_value={"genres": []}),
    )
    service = SimpleNamespace(
        mb_client=client,
        match_release=Mock(return_value=None),
        match_recording=Mock(return_value={"mbid": "rec-1"}),
        match_artist=Mock(return_value={"mbid": "art-queen"}),
    )
    runtime = SimpleNamespace(mb_worker=SimpleNamespace(mb_service=service))

    metadata = {
        "title": "Mustapha",
        "artist": "Queen",
        "album": "Jazz",
        "track_number": 1,
        "upc": "00602527779340",
    }
    state = ms._blank_post_process_state()
    ms._process_musicbrainz_source(state, metadata, DummyConfig(), runtime, "Mustapha", "Queen")

    assert state["release_mbid"] == "rel-reissue-2011"
    assert state["release_year"] == "1978"  # Extracted from first-release-date (1978-11-10)
    assert state["id_tags"]["ORIGINALDATE"] == "1978-11-10"
    assert state["id_tags"]["ORIGINALYEAR"] == "1978"


def test_album_level_tags_include_originalyear():
    from core.album_consistency import _ALBUM_LEVEL_TAGS, _ID3_TXXX_MAP
    assert "ORIGINALDATE" in _ALBUM_LEVEL_TAGS
    assert "ORIGINALYEAR" in _ALBUM_LEVEL_TAGS
    assert _ID3_TXXX_MAP.get("ORIGINALYEAR") == "originalyear"


def test_resolve_album_release_passes_barcode(monkeypatch):
    import core.album_consistency as ac
    mock_find = Mock(return_value={"id": "rel-bc-winner", "title": "Jazz"})
    monkeypatch.setattr(ac, "_find_best_release", mock_find)
    monkeypatch.setattr("core.metadata.album_mbid_cache.lookup", lambda a, ar: None)
    monkeypatch.setattr("core.metadata.album_mbid_cache.record", lambda a, ar, m: True)

    ac._resolve_album_release("Jazz", "Queen", 12, Mock(), barcode="00602527779340")
    assert mock_find.called
    _, kwargs = mock_find.call_args
    assert kwargs.get("barcode") == "00602527779340"

