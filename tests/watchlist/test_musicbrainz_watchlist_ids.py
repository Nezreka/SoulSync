from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_artist


def test_watchlist_artist_can_store_musicbrainz_match(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_artist_to_watchlist(
        "mb-artist-1",
        "MusicBrainz Artist",
        profile_id=1,
        source="musicbrainz",
    )

    artists = db.get_watchlist_artists(profile_id=1)

    assert len(artists) == 1
    assert artists[0].artist_name == "MusicBrainz Artist"
    assert artists[0].musicbrainz_artist_id == "mb-artist-1"
    assert artists[0].spotify_artist_id is None


def test_watchlist_musicbrainz_match_can_be_added_to_existing_artist(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_artist_to_watchlist("sp-artist-1", "Linked Artist", profile_id=1, source="spotify")
    assert db.add_artist_to_watchlist("mb-artist-1", "Linked Artist", profile_id=1, source="musicbrainz")

    artists = db.get_watchlist_artists(profile_id=1)

    assert len(artists) == 1
    assert artists[0].spotify_artist_id == "sp-artist-1"
    assert artists[0].musicbrainz_artist_id == "mb-artist-1"


def test_watchlist_musicbrainz_match_supports_presence_and_removal(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-artist-1", "Removable Artist", profile_id=1, source="spotify")
    artist = db.get_watchlist_artists(profile_id=1)[0]

    assert db.update_watchlist_musicbrainz_id(artist.id, "mb-artist-1")
    assert db.is_artist_in_watchlist("mb-artist-1", profile_id=1)
    assert db.remove_artist_from_watchlist("mb-artist-1", profile_id=1)
    assert db.get_watchlist_artists(profile_id=1) == []


def test_watchlist_musicbrainz_match_backfills_from_library_by_name(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-artist-1", "Library Matched Artist", profile_id=1, source="spotify")
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id="library-artist-1",
                                name="Library Matched Artist")
        conn.execute("UPDATE lib2_artists SET musicbrainz_id=? WHERE id=?",
                     ("mb-library-1", artist_id))
        conn.commit()

    assert db.backfill_watchlist_musicbrainz_ids_from_library(profile_id=1) == 1

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.musicbrainz_artist_id == "mb-library-1"


def test_watchlist_musicbrainz_match_backfills_from_library_by_linked_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-artist-1", "Different Watchlist Name", profile_id=1, source="spotify")
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id="library-artist-1",
                                name="Canonical Library Name")
        conn.execute(
            "UPDATE lib2_artists SET spotify_id=?, musicbrainz_id=? WHERE id=?",
            ("sp-artist-1", "mb-library-1", artist_id))
        conn.commit()

    assert db.backfill_watchlist_musicbrainz_ids_from_library(profile_id=1) == 1

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.musicbrainz_artist_id == "mb-library-1"


def test_watchlist_musicbrainz_match_backfills_across_the_ascii_fold(tmp_path):
    """The name match went through LOWER(), which folds A-Z only: a watchlist
    "BJÖRK" never met the library's "Björk". The catalogue's stored fold does."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-bjork", "BJÖRK", profile_id=1, source="spotify")
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id="library-bjork", name="Björk")
        conn.execute("UPDATE lib2_artists SET musicbrainz_id=? WHERE id=?",
                     ("mb-bjork", artist_id))
        conn.commit()

    assert db.backfill_watchlist_musicbrainz_ids_from_library(profile_id=1) == 1
    assert db.get_watchlist_artists(profile_id=1)[0].musicbrainz_artist_id == "mb-bjork"


def test_watchlist_musicbrainz_backfill_matches_a_provider_without_a_column(tmp_path):
    """Deezer lives in `external_ids`; the link must resolve there too."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("dz-77", "Watchlist Spelling", profile_id=1, source="deezer")
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id="library-dz", name="Library Spelling")
        conn.execute(
            "UPDATE lib2_artists SET musicbrainz_id=?, external_ids=? WHERE id=?",
            ("mb-dz", '{"deezer": "dz-77"}', artist_id))
        conn.commit()

    assert db.backfill_watchlist_musicbrainz_ids_from_library(profile_id=1) == 1
    assert db.get_watchlist_artists(profile_id=1)[0].musicbrainz_artist_id == "mb-dz"
