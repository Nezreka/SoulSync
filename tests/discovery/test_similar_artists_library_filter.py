from database.music_database import MusicDatabase


def _names(artists):
    return {artist.similar_artist_name for artist in artists}


def _own_seeds(db, *source_ids):
    """Give each seed id a library artist to belong to.

    only artists you chose can seed a recommendation, so a fixture seed that
    belongs to nobody is correctly ignored — these rows are what make the seeds
    real.
    """
    with db._get_connection() as conn:
        conn.executemany(
            "INSERT INTO artists (name, server_source, spotify_artist_id) VALUES (?, ?, ?)",
            [(f"Seed Owner {sid}", "navidrome", sid) for sid in source_ids],
        )
        conn.commit()


def test_top_similar_artists_can_exclude_active_server_library_artists(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _own_seeds(db, "seed-1")
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
    _own_seeds(db, "seed-1")
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
    _own_seeds(db, "seed-1")
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


def test_dial_shifts_candidate_selection_consensus_to_obscurity(tmp_path):
    # Best-in-class adventurousness: the DIAL drives which candidates the pool contains, not just
    # their order. A popular, heavily-recommended artist should lead at the safe end; an obscure,
    # barely-recommended deep cut should lead at the adventurous end.
    db = MusicDatabase(str(tmp_path / "music.db"))
    _own_seeds(db, *[f"seed-{i}" for i in range(5)])
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
    _own_seeds(db, *[f"s-{i}" for i in range(3)])
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


# ── browsing is not a preference (issue #1284) ───────────────────────────────
# opening the artist map caches similar artists for whoever you looked up
# (core/artists/map.py writes the rows below). those rows used to seed the
# discovery pool, which feeds discovery weekly, seasonal mix and the hero
# slider — so one curious look-up quietly became a taste signal.

def _browse_write(db, source_artist_id, name, spotify_id=None):
    """Exactly the call the artist map makes when it caches a browse."""
    db.add_or_update_similar_artist(
        source_artist_id=source_artist_id,
        similar_artist_name=name,
        similar_artist_spotify_id=spotify_id,
        similarity_rank=1,
        profile_id=1,
        image_url=None,
        genres=None,
        popularity=0,
    )


def _watchlist(db, name, spotify_id=None, profile_id=1):
    with db._get_connection() as conn:
        row_id = conn.execute(
            "INSERT INTO watchlist_artists (artist_name, spotify_artist_id, profile_id) VALUES (?, ?, ?)",
            (name, spotify_id, profile_id),
        ).lastrowid
        conn.commit()
    return row_id


def test_browsed_artist_does_not_seed_recommendations(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _watchlist(db, "Deliberate Band", "sp-watch")
    _own_seeds(db, "sp-lib")

    db.add_or_update_similar_artist(source_artist_id="sp-watch", similar_artist_name="From Watchlist",
                                    similar_artist_spotify_id="sp-a", profile_id=1)
    db.add_or_update_similar_artist(source_artist_id="sp-lib", similar_artist_name="From Library",
                                    similar_artist_spotify_id="sp-b", profile_id=1)
    _browse_write(db, "sp-looked-up-once", "From A Curious Browse", "sp-c")

    assert _names(db.get_top_similar_artists(limit=50, profile_id=1)) == {"From Watchlist", "From Library"}


def test_placeholder_source_id_does_not_seed_recommendations(tmp_path):
    # the download path stores 'from_sync_modal' when it cannot name the artist;
    # 50 such rows were seeding a real install's pool under that fake id.
    db = MusicDatabase(str(tmp_path / "music.db"))
    _own_seeds(db, "sp-lib")
    db.add_or_update_similar_artist(source_artist_id="sp-lib", similar_artist_name="Real",
                                    similar_artist_spotify_id="sp-real", profile_id=1)
    _browse_write(db, "from_sync_modal", "Placeholder Rec", "sp-fake")

    assert _names(db.get_top_similar_artists(limit=50, profile_id=1)) == {"Real"}


def test_watchlist_artist_with_no_provider_id_still_seeds(tmp_path):
    # the scanner keys a row by the watchlist ROW id when the artist matched no
    # provider. that key is deliberate too — dropping it would delete real
    # watchlist artists from discovery and look exactly like the fix working.
    db = MusicDatabase(str(tmp_path / "music.db"))
    row_id = _watchlist(db, "No Ids Band")
    db.add_or_update_similar_artist(source_artist_id=str(row_id), similar_artist_name="Kept",
                                    similar_artist_spotify_id="sp-kept", profile_id=1)
    _browse_write(db, "sp-stranger", "Dropped", "sp-dropped")

    assert _names(db.get_top_similar_artists(limit=50, profile_id=1)) == {"Kept"}


def test_another_profiles_library_does_not_seed_your_recommendations(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO artists (name, server_source, spotify_artist_id, owner_profile_id) VALUES (?, ?, ?, ?)",
            ("Their Artist", "jellyfin", "sp-theirs", 2),
        )
        conn.commit()
    db.add_or_update_similar_artist(source_artist_id="sp-theirs", similar_artist_name="Theirs",
                                    similar_artist_spotify_id="sp-x", profile_id=1)

    assert _names(db.get_top_similar_artists(limit=50, profile_id=1)) == set()


def test_watchlisted_recommendations_are_still_excluded(tmp_path):
    # the exclusion moved out of a LEFT JOIN and into resolved sets — it has to
    # keep matching on name and on each provider id, not just on spotify.
    db = MusicDatabase(str(tmp_path / "music.db"))
    _own_seeds(db, "sp-lib")
    _watchlist(db, "By Name")
    with db._get_connection() as conn:
        conn.execute("INSERT INTO watchlist_artists (artist_name, itunes_artist_id, profile_id) VALUES (?, ?, ?)",
                     ("Itunes Watch", "it-watch", 1))
        conn.execute("INSERT INTO watchlist_artists (artist_name, deezer_artist_id, profile_id) VALUES (?, ?, ?)",
                     ("Deezer Watch", "dz-watch", 1))
        conn.commit()

    db.add_or_update_similar_artist(source_artist_id="sp-lib", similar_artist_name="by name",
                                    similar_artist_spotify_id="sp-1", profile_id=1)
    db.add_or_update_similar_artist(source_artist_id="sp-lib", similar_artist_name="Different Name",
                                    similar_artist_itunes_id="it-watch", profile_id=1)
    db.add_or_update_similar_artist(source_artist_id="sp-lib", similar_artist_name="Also Different",
                                    similar_artist_deezer_id="dz-watch", profile_id=1)
    db.add_or_update_similar_artist(source_artist_id="sp-lib", similar_artist_name="Survivor",
                                    similar_artist_spotify_id="sp-2", profile_id=1)

    assert _names(db.get_top_similar_artists(limit=50, profile_id=1)) == {"Survivor"}


def test_empty_id_columns_do_not_match_each_other(tmp_path):
    # a watchlist artist with an empty id column must not exclude every
    # recommendation that also has an empty one.
    db = MusicDatabase(str(tmp_path / "music.db"))
    _own_seeds(db, "sp-lib")
    with db._get_connection() as conn:
        conn.execute("INSERT INTO watchlist_artists (artist_name, spotify_artist_id, profile_id) VALUES (?, ?, ?)",
                     ("Blank Id Watch", "", 1))
        conn.commit()
    db.add_or_update_similar_artist(source_artist_id="sp-lib", similar_artist_name="Blank Id Rec",
                                    similar_artist_spotify_id="", profile_id=1)

    assert _names(db.get_top_similar_artists(limit=50, profile_id=1)) == {"Blank Id Rec"}
