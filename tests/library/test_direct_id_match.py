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


def test_non_musicbrainz_services_without_a_url_shape():
    assert extract_direct_id("spotify", "album", MBID) is None
    assert extract_direct_id("spotify", "album", "12345") is None


def test_deezer_album_url_detected():
    from core.library.direct_id import extract_deezer_link
    url = "https://www.deezer.com/us/album/620787111"
    assert extract_direct_id("deezer", "album", url) == "620787111"
    assert extract_deezer_link(url) == ("album", "620787111")


def test_deezer_track_url_with_locale():
    from core.library.direct_id import extract_deezer_link
    url = "https://www.deezer.com/en/track/2914419581"
    assert extract_direct_id("deezer", "track", url) == "2914419581"
    assert extract_deezer_link(url) == ("track", "2914419581")


def test_deezer_bare_numeric_id():
    assert extract_direct_id("deezer", "album", "620787111") == "620787111"
    assert extract_direct_id("deezer", "album", "12") is None  # too short
    assert extract_direct_id("deezer", "album", "Raccoons") is None


def test_deezer_short_link_rejected():
    from core.library.direct_id import extract_deezer_link
    assert extract_deezer_link("https://link.deezer.com/s/abc") is None
    assert extract_direct_id("deezer", "album", "https://link.deezer.com/s/abc") is None


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


# ── Deezer URL / id dispatch ─────────────────────────────────────────────────

_DEEZER_ALBUM = {
    "id": 620787111,
    "title": "Raccoons (Gaudi & Don Letts Remix)",
    "cover_medium": "https://e.example/cover.jpg",
    "artist": {"name": "Caravan Palace"},
    "tracks": {"data": [{"id": 2914419581, "title": "Raccoons (Gaudi & Don Letts Remix)"}]},
}
_DEEZER_TRACK = {
    "id": 2914419581,
    "title": "Raccoons (Gaudi & Don Letts Remix)",
    "artist": {"name": "Caravan Palace"},
    "album": {
        "id": 620787111,
        "title": "Raccoons (Gaudi & Don Letts Remix)",
        "cover_medium": "https://e.example/cover.jpg",
    },
}


def _stub_deezer_get(monkeypatch, by_kind):
    import core.library.service_search as ss

    def fake_get(kind, entity_id):
        return by_kind.get((kind, str(entity_id)))

    monkeypatch.setattr(ss, "_deezer_get", fake_get)
    return ss


def test_pasted_deezer_album_url_returns_that_album(monkeypatch):
    ss = _stub_deezer_get(monkeypatch, {("album", "620787111"): _DEEZER_ALBUM})
    results = ss._search_service(
        "deezer", "album", "https://www.deezer.com/us/album/620787111",
    )
    assert len(results) == 1
    assert results[0]["id"] == "620787111"
    assert results[0]["name"] == "Raccoons (Gaudi & Don Letts Remix)"
    assert "Direct ID match" in results[0]["extra"]
    assert "Caravan Palace" in results[0]["extra"]


def test_deezer_track_url_while_matching_album_returns_parent_album(monkeypatch):
    ss = _stub_deezer_get(monkeypatch, {
        ("track", "2914419581"): _DEEZER_TRACK,
        ("album", "620787111"): _DEEZER_ALBUM,
    })
    results = ss._search_service(
        "deezer", "album", "https://www.deezer.com/track/2914419581",
    )
    assert len(results) == 1
    assert results[0]["id"] == "620787111"


def test_unresolvable_deezer_url_falls_through_to_search(monkeypatch):
    import core.library.service_search as ss
    monkeypatch.setattr(ss, "_deezer_get", lambda kind, eid: None)

    class _Resp:
        def json(self):
            return {"data": [{"id": 1, "title": "fuzzy", "artist": {"name": "X"},
                              "cover_medium": None}]}

    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
    monkeypatch.setattr("core.deezer_throttle.wait_for_slot", lambda: True)
    results = ss._search_service(
        "deezer", "album", "https://www.deezer.com/album/999",
    )
    assert len(results) == 1 and results[0]["id"] == "1"
