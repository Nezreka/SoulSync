from database.music_database import MusicDatabase


def _names(artists):
    return {artist.similar_artist_name for artist in artists}


def test_top_similar_artists_can_exclude_active_server_library_artists(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Owned By Spotify ID",
        similar_artist_spotify_id="sp-owned",
        profile_id=1,
    )
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Owned By Deezer ID",
        similar_artist_deezer_id="dz-owned",
        profile_id=1,
    )
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Owned By MusicBrainz ID",
        similar_artist_musicbrainz_id="mb-owned",
        profile_id=1,
    )
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Owned By Name",
        similar_artist_spotify_id="sp-owned-name",
        profile_id=1,
    )
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Different Server Artist",
        similar_artist_spotify_id="sp-other-server",
        profile_id=1,
    )
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Fresh Artist",
        similar_artist_spotify_id="sp-fresh",
        profile_id=1,
    )

    with db._get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO artists (name, server_source, spotify_artist_id, deezer_id, musicbrainz_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("Library Alias", "navidrome", "sp-owned", None, None),
                ("Library Deezer Alias", "navidrome", None, "dz-owned", None),
                ("Library MusicBrainz Alias", "navidrome", None, None, "mb-owned"),
                ("owned by name", "navidrome", None, None, None),
                ("Different Server Artist", "plex", "sp-other-server", None, None),
            ],
        )
        conn.commit()

    artists = db.get_top_similar_artists(
        limit=20,
        profile_id=1,
        exclude_library_server="navidrome",
    )

    assert _names(artists) == {"Different Server Artist", "Fresh Artist"}


def test_top_similar_artists_can_require_musicbrainz_source(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="MB Artist",
        similar_artist_musicbrainz_id="mb-artist",
        profile_id=1,
    )
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Spotify Only",
        similar_artist_spotify_id="sp-artist",
        profile_id=1,
    )

    artists = db.get_top_similar_artists(limit=20, profile_id=1, require_source="musicbrainz")

    assert _names(artists) == {"MB Artist"}
    assert artists[0].similar_artist_musicbrainz_id == "mb-artist"


def test_top_similar_artists_keeps_existing_behavior_without_library_filter(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.add_or_update_similar_artist(
        source_artist_id="seed-1",
        similar_artist_name="Owned Artist",
        similar_artist_spotify_id="sp-owned",
        profile_id=1,
    )

    with db._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO artists (name, server_source, spotify_artist_id)
            VALUES (?, ?, ?)
            """,
            ("Owned Artist", "navidrome", "sp-owned"),
        )
        conn.commit()

    artists = db.get_top_similar_artists(limit=20, profile_id=1)

    assert _names(artists) == {"Owned Artist"}
