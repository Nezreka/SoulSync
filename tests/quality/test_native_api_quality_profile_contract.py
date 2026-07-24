"""The native (modular) API is a complete Quality Profile client.

Audit finding P1-02: the database and the internal wishlist service both
understood ``quality_profile_id``, but the public modular contract did not — the
POST handler never read it and the serializer never returned it. A thin native
client (the future Library v2, or anything external) therefore could not express
or verify the assignment without reimplementing the internals.

Audit finding P2-04/P3-01: "not supplied" and "explicitly wrong" were treated
identically — an unknown id silently became the default instead of being
rejected.
"""

from __future__ import annotations

import pytest

from api.serializers import serialize_wishlist_track, serialize_watchlist_artist
from core.api_validation import parse_strict_bool, parse_strict_id, parse_strict_int
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _track(track_id="sp1"):
    return {
        "id": track_id,
        "name": "Song",
        "artists": [{"id": "ar1", "name": "Artist"}],
        "album": {"id": "al1", "name": "Album"},
    }


# ── serializers expose the effective assignment ──────────────────────────────

def test_wishlist_serializer_returns_the_quality_profile():
    out = serialize_wishlist_track({"id": 1, "track_id": "sp1", "quality_profile_id": 23})
    assert out["quality_profile_id"] == 23


def test_wishlist_serializer_field_filter_still_works():
    out = serialize_wishlist_track(
        {"id": 1, "track_id": "sp1", "quality_profile_id": 23},
        {"track_id", "quality_profile_id"},
    )
    assert out == {"track_id": "sp1", "quality_profile_id": 23}


def test_watchlist_serializer_exposes_every_provider_id():
    out = serialize_watchlist_artist({
        "id": 1,
        "artist_name": "Artist",
        "spotify_artist_id": "sp-1",
        "itunes_artist_id": "42",
        "deezer_artist_id": "77",
        "musicbrainz_artist_id": "mbid-1",
        "preferred_metadata_source": "deezer",
    })
    assert out["deezer_artist_id"] == "77"
    assert out["musicbrainz_artist_id"] == "mbid-1"
    assert out["source"] == "deezer"


# ── the DB tells valid from invalid instead of guessing ──────────────────────

def test_quality_profile_exists_distinguishes_missing_from_unknown(db):
    valid = db.create_quality_profile("Hi-Res", {})

    assert db.quality_profile_exists(valid) is True
    assert db.quality_profile_exists(999999) is False
    assert db.quality_profile_exists(None) is False
    assert db.quality_profile_exists("not-a-number") is False


def test_wishlist_add_rejects_an_explicitly_unknown_profile(db):
    assert db.add_to_wishlist(_track(), quality_profile_id=999999) is False
    assert db.get_wishlist_count(profile_id=1) == 0


def test_wishlist_add_without_a_profile_still_uses_the_default(db):
    assert db.add_to_wishlist(_track()) is True
    tracks = db.get_wishlist_tracks(profile_id=1)
    assert tracks and tracks[0]["quality_profile_id"] is not None


def test_wishlist_add_with_a_valid_profile_stores_it(db):
    valid = db.create_quality_profile("Hi-Res", {})
    assert db.add_to_wishlist(_track(), quality_profile_id=valid) is True
    assert db.get_wishlist_tracks(profile_id=1)[0]["quality_profile_id"] == valid


def test_duplicate_add_updates_the_stored_profile(db):
    first = db.create_quality_profile("Standard", {})
    second = db.create_quality_profile("Hi-Res", {})

    assert db.add_to_wishlist(_track(), quality_profile_id=first) is True
    db.add_to_wishlist(_track(), quality_profile_id=second, user_initiated=True)

    assert db.get_wishlist_tracks(profile_id=1)[0]["quality_profile_id"] == second


# ── strict JSON parsing (P3-01) ──────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    ("true", True), ("True", True), ("1", True), ("on", True),
    ("false", False), ("False", False), ("0", False), ("", False),
    (1, None), (0, None), (None, None), ([], None), ({}, None), ("maybe", None),
])
def test_parse_strict_bool(value, expected):
    assert parse_strict_bool(value) is expected


@pytest.mark.parametrize("value,expected", [
    (7, 7), ("7", 7), (" 7 ", 7), ("-7", -7),
    (True, None), (False, None), (7.5, None), (None, None),
    ("", None), ("  ", None), ("7a", None), ([7], None), ({"id": 7}, None),
])
def test_parse_strict_int(value, expected):
    assert parse_strict_int(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("sp-1", "sp-1"), (" sp-1 ", "sp-1"), (908622995, "908622995"),
    ("", None), ("   ", None), (None, None), (True, None), ([], None), ({}, None),
])
def test_parse_strict_id(value, expected):
    assert parse_strict_id(value) == expected
