"""Watchlist creation is provider-neutral instead of guessing from the id shape.

Audit finding P1-05: the native contract carried only ``artist_id``, so a
numeric Deezer/Discogs id was filed as iTunes, a MusicBrainz UUID as Spotify,
and Amazon was missing from the update/remove mappings. Two different artists
that happen to share a name could also silently overwrite each other's row.
"""

from __future__ import annotations

import pytest

from core.watchlist_sources import infer_source, normalize_source, source_column
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _row(db, artist_name):
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist_artists WHERE artist_name = ?", (artist_name,)
        ).fetchone()
        return dict(row) if row else None


# ── the registry itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("spotify", "spotify"), ("Spotify", "spotify"), (" SPOTIFY ", "spotify"),
    ("itunes", "itunes"), ("apple", "itunes"), ("itunes_link", "itunes"),
    ("deezer", "deezer"), ("musicbrainz", "musicbrainz"), ("amazon", "amazon"),
    ("beatport", None), ("", None), (None, None), (7, None),
])
def test_normalize_source(value, expected):
    assert normalize_source(value) == expected


def test_every_known_source_maps_to_a_column():
    for source in ("spotify", "itunes", "deezer", "discogs", "musicbrainz", "amazon"):
        assert source_column(source), source


def test_infer_source_is_the_documented_legacy_guess():
    assert infer_source("12345") == "itunes"
    assert infer_source("37i9dQZF1DXcBWIGoYBM5M") == "spotify"


# ── create: explicit provider wins over the guess ────────────────────────────

@pytest.mark.parametrize("source,column", [
    ("deezer", "deezer_artist_id"),
    ("discogs", "discogs_artist_id"),
    ("itunes", "itunes_artist_id"),
    ("amazon", "amazon_artist_id"),
])
def test_numeric_id_is_filed_under_the_stated_provider(db, source, column):
    assert db.add_artist_to_watchlist("12345", "Numeric Artist", source=source) is True
    row = _row(db, "Numeric Artist")
    assert row[column] == "12345"
    assert row["itunes_artist_id"] in (None, "12345")
    if column != "itunes_artist_id":
        assert row["itunes_artist_id"] is None


def test_musicbrainz_uuid_is_not_filed_as_spotify(db):
    mbid = "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"
    assert db.add_artist_to_watchlist(mbid, "MB Artist", source="musicbrainz") is True
    row = _row(db, "MB Artist")
    assert row["musicbrainz_artist_id"] == mbid
    assert row["spotify_artist_id"] is None


def test_unknown_source_is_rejected_rather_than_defaulting_to_spotify(db):
    assert db.add_artist_to_watchlist("abc", "Nope", source="myspace") is False
    assert _row(db, "Nope") is None


def test_omitted_source_still_uses_the_legacy_inference(db):
    assert db.add_artist_to_watchlist("37i9dQZF1DXcBWIGoYBM5M", "Legacy") is True
    assert _row(db, "Legacy")["spotify_artist_id"] == "37i9dQZF1DXcBWIGoYBM5M"


# ── linking vs. collision ────────────────────────────────────────────────────

def test_same_artist_from_a_second_provider_links_onto_one_row(db):
    db.add_artist_to_watchlist("sp-1", "Shared Name", source="spotify")
    db.add_artist_to_watchlist("dz-1", "Shared Name", source="deezer")

    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist_artists WHERE artist_name = ?", ("Shared Name",)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["spotify_artist_id"] == "sp-1"
    assert rows[0]["deezer_artist_id"] == "dz-1"


def test_two_different_artists_with_the_same_name_stay_separate(db):
    """Same provider, different id — that is a DIFFERENT artist, not a link."""
    db.add_artist_to_watchlist("sp-1", "John Williams", source="spotify")
    db.add_artist_to_watchlist("sp-2", "John Williams", source="spotify")

    with db._get_connection() as conn:
        ids = [r["spotify_artist_id"] for r in conn.execute(
            "SELECT spotify_artist_id FROM watchlist_artists WHERE artist_name = ? ORDER BY id",
            ("John Williams",),
        ).fetchall()]
    assert ids == ["sp-1", "sp-2"]


# ── quality profile validation at the direct DB boundary (P2-04) ─────────────

def test_explicitly_unknown_quality_profile_is_rejected(db):
    assert db.add_artist_to_watchlist(
        "sp-9", "Bad Profile", source="spotify", quality_profile_id=999999
    ) is False
    assert _row(db, "Bad Profile") is None


def test_omitted_quality_profile_still_resolves_to_the_default(db):
    assert db.add_artist_to_watchlist("sp-8", "Default Profile", source="spotify") is True
    assert _row(db, "Default Profile")["quality_profile_id"] is not None


# ── amazon is no longer forgotten in lookup / remove ─────────────────────────

def test_amazon_artist_can_be_found_and_removed(db):
    db.add_artist_to_watchlist("az-1", "Amazon Artist", source="amazon")

    assert db.is_artist_in_watchlist("az-1") is True
    assert db.remove_artist_from_watchlist("az-1") is True
    assert _row(db, "Amazon Artist") is None
