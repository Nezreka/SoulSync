from database.music_database import MusicDatabase


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
        conn.execute(
            """
            INSERT INTO artists (id, name, musicbrainz_id)
            VALUES (?, ?, ?)
            """,
            ("library-artist-1", "Library Matched Artist", "mb-library-1"),
        )
        conn.commit()

    assert db.backfill_watchlist_musicbrainz_ids_from_library(profile_id=1) == 1

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.musicbrainz_artist_id == "mb-library-1"


def test_watchlist_musicbrainz_match_backfills_from_library_by_linked_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_artist_to_watchlist("sp-artist-1", "Different Watchlist Name", profile_id=1, source="spotify")
    with db._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO artists (id, name, spotify_artist_id, musicbrainz_id)
            VALUES (?, ?, ?, ?)
            """,
            ("library-artist-1", "Canonical Library Name", "sp-artist-1", "mb-library-1"),
        )
        conn.commit()

    assert db.backfill_watchlist_musicbrainz_ids_from_library(profile_id=1) == 1

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.musicbrainz_artist_id == "mb-library-1"
