from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_artist


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
        library = [
            ("Library Alias", "navidrome", "sp-owned", None, None),
            ("Library Deezer Alias", "navidrome", None, "dz-owned", None),
            ("Library MusicBrainz Alias", "navidrome", None, None, "mb-owned"),
            ("owned by name", "navidrome", None, None, None),
            ("Different Server Artist", "plex", "sp-other-server", None, None),
        ]
        for index, (name, server, spotify, deezer, mbid) in enumerate(library):
            artist_id = seed_artist(conn, server_id=f"lib-{index}", name=name,
                                    server_source=server)
            conn.execute(
                "UPDATE lib2_artists SET spotify_id=?, musicbrainz_id=?,"
                "       external_ids=CASE WHEN ? IS NULL THEN external_ids"
                "                         ELSE json_set(external_ids,'$.deezer',?) END"
                " WHERE id=?",
                (spotify, mbid, deezer, deezer, artist_id))
            if name == "Different Server Artist":
                conn.execute(
                    "INSERT INTO lib2_media_server_mappings "
                    "(entity_type,entity_id,server_source,server_id) "
                    "VALUES('artist',?,'navidrome','nav-mapped')", (artist_id,),
                )
        conn.commit()

    artists = db.get_top_similar_artists(
        limit=20,
        profile_id=1,
        exclude_library_server="navidrome",
    )

    assert _names(artists) == {"Fresh Artist"}


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
            INSERT INTO lib2_artists (name, name_key, spotify_id,
                                      server_source, server_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Owned Artist", "owned artist", "sp-owned", "navidrome", "na-1"),
        )
        conn.commit()

    artists = db.get_top_similar_artists(limit=20, profile_id=1)

    assert _names(artists) == {"Owned Artist"}


def test_dial_shifts_candidate_selection_consensus_to_obscurity(tmp_path):
    # Best-in-class adventurousness: the DIAL drives which candidates the pool contains, not just
    # their order. A popular, heavily-recommended artist should lead at the safe end; an obscure,
    # barely-recommended deep cut should lead at the adventurous end.
    db = MusicDatabase(str(tmp_path / "music.db"))
    # HiConsensus: popular (pop 90), pointed to by 5 of your artists (occurrence sums to 5).
    for i in range(5):
        db.add_or_update_similar_artist(
            source_artist_id=f"seed-{i}", similar_artist_name="HiConsensus",
            similar_artist_spotify_id="sp-hi", popularity=90, profile_id=1)
    # DeepCut: obscure (pop 5), pointed to by just one artist (occurrence 1).
    db.add_or_update_similar_artist(
        source_artist_id="seed-0", similar_artist_name="DeepCut",
        similar_artist_spotify_id="sp-deep", popularity=5, profile_id=1)

    safe = db.get_top_similar_artists(limit=10, profile_id=1, adventurousness=0.0)
    adv = db.get_top_similar_artists(limit=10, profile_id=1, adventurousness=1.0)
    assert _names(safe) == _names(adv) == {"HiConsensus", "DeepCut"}   # same pool, different order
    assert safe[0].similar_artist_name == "HiConsensus"               # safe -> consensus pick first
    assert adv[0].similar_artist_name == "DeepCut"                    # adventurous -> obscure pick first


def test_dial_none_preserves_classic_rotation_order(tmp_path):
    # Every non-dial caller is unaffected — no adventurousness arg -> featured-rotation order.
    db = MusicDatabase(str(tmp_path / "music.db"))
    for i in range(3):
        db.add_or_update_similar_artist(
            source_artist_id=f"s-{i}", similar_artist_name="Popular",
            similar_artist_spotify_id="sp-pop", popularity=95, profile_id=1)
    db.add_or_update_similar_artist(
        source_artist_id="s-0", similar_artist_name="Obscure",
        similar_artist_spotify_id="sp-obs", popularity=2, profile_id=1)
    out = db.get_top_similar_artists(limit=10, profile_id=1)   # no dial
    # Classic order: higher occurrence first (Popular occ 3 > Obscure occ 1), popularity ignored.
    assert out[0].similar_artist_name == "Popular"
