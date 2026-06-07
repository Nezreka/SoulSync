"""Direct-ID manual matching (Ashh: 'just slap MB ID in that search').

When the right release isn't in the top-8 fuzzy results, the user pastes the
exact ID. extract_direct_id detects it (pure); _search_service confirms it
via a direct lookup and returns just that entity, falling back to fuzzy
search if the paste only looks ID-ish.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.library.direct_id import extract_direct_id

MBID = "1af02ea7-3f00-40ca-804b-41e2dca7e4a9"


# ── pure detector ────────────────────────────────────────────────────────────

def test_bare_mbid_detected():
    assert extract_direct_id("musicbrainz", "album", MBID) == MBID
    assert extract_direct_id("musicbrainz", "artist", MBID.upper()) == MBID  # normalized


def test_mbid_in_url_detected():
    for url in (
        f"https://musicbrainz.org/release/{MBID}",
        f"https://musicbrainz.org/release/{MBID}/cover-art",
        f"  https://beta.musicbrainz.org/artist/{MBID}  ",
    ):
        assert extract_direct_id("musicbrainz", "album", url) == MBID


def test_plain_text_query_is_not_an_id():
    assert extract_direct_id("musicbrainz", "album", "Idols") is None
    assert extract_direct_id("musicbrainz", "album", "Yungblud Idols") is None
    assert extract_direct_id("musicbrainz", "album", "") is None
    assert extract_direct_id("musicbrainz", "album", "   ") is None


def test_loose_uuid_without_url_context_is_rejected():
    # A UUID embedded in free text (not the whole query, no MB URL) is NOT
    # treated as a direct ID — avoids hijacking a genuine search.
    assert extract_direct_id("musicbrainz", "album", f"album {MBID} deluxe") is None


def test_non_musicbrainz_services_have_no_direct_id_yet():
    assert extract_direct_id("spotify", "album", MBID) is None
    assert extract_direct_id("deezer", "track", "12345") is None


# ── _search_service direct dispatch ──────────────────────────────────────────

def _wire_mb(monkeypatch, **methods):
    import core.library.service_search as ss
    mb_client = MagicMock(**methods)
    worker = SimpleNamespace(mb_service=SimpleNamespace(mb_client=mb_client))
    monkeypatch.setattr(ss, "mb_worker", worker)
    return ss, mb_client


def test_pasted_mbid_returns_single_confirmed_release(monkeypatch):
    ss, mb_client = _wire_mb(monkeypatch)
    mb_client.get_release.return_value = {
        "id": MBID, "title": "Idols", "date": "2025-06-20",
        "artist-credit": [{"name": "Yungblud"}],
    }
    results = ss._search_service("musicbrainz", "album", MBID)

    assert len(results) == 1
    assert results[0]["id"] == MBID
    assert results[0]["name"] == "Idols"
    assert "Direct ID match" in results[0]["extra"]
    assert "Yungblud" in results[0]["extra"]
    mb_client.get_release.assert_called_once_with(MBID)
    mb_client.search_release.assert_not_called()   # never fuzzy-searched


def test_album_falls_back_to_release_group(monkeypatch):
    ss, mb_client = _wire_mb(
        monkeypatch,
        get_release=lambda mbid: None,
        get_release_group=lambda mbid: {"id": MBID, "title": "Idols", "artist-credit": []},
    )
    results = ss._search_service("musicbrainz", "album", MBID)
    assert len(results) == 1 and results[0]["name"] == "Idols"


def test_unresolvable_mbid_falls_through_to_fuzzy(monkeypatch):
    # ID-shaped but doesn't resolve → don't dead-end; run the normal search.
    ss, mb_client = _wire_mb(
        monkeypatch,
        get_release=lambda mbid: None,
        get_release_group=lambda mbid: None,
        search_release=lambda q, limit=8, strict=False: [
            {"id": "other", "title": "Idols (fuzzy)", "artist-credit": [], "date": "", "score": 90},
        ],
    )
    results = ss._search_service("musicbrainz", "album", MBID)
    assert len(results) == 1 and results[0]["id"] == "other"   # fuzzy result


def test_plain_query_skips_direct_lookup(monkeypatch):
    ss, mb_client = _wire_mb(
        monkeypatch,
        search_release=lambda q, limit=8, strict=False: [
            {"id": "r1", "title": "Idols", "artist-credit": [], "date": "", "score": 100},
        ],
    )
    results = ss._search_service("musicbrainz", "album", "Idols")
    assert results[0]["id"] == "r1"
    mb_client.get_release.assert_not_called()       # no wasted direct lookup
