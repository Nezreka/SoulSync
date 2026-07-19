"""Auto-linking finished downloads into Library v2 (post-processing hook)."""

from __future__ import annotations

import json

import pytest

from core.library2 import autolink as A


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    """Enable the feature flag and point get_database at the test DB."""
    from config.settings import config_manager

    real_get = config_manager.get

    def fake_get(key, default=None):
        if key == "features.library_v2":
            return True
        return real_get(key, default)

    monkeypatch.setattr(config_manager, "get", fake_get)
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return legacy_db


def _context(**overrides):
    ctx = {
        "_final_processed_path": "/music/Drake/Scorpion/01 Nonstop.flac",
        "username": "usenet",
        "track_info": {
            "name": "Nonstop",
            "artists": [{"name": "Drake"}],
            "album": {"name": "Scorpion", "id": "sp-scorpion", "total_tracks": 25,
                      "album_type": "album"},
            "track_number": 1,
            "provider": "spotify",
            "id": "sp-track-nonstop",
        },
        "_embedded_id_tags": {"SPOTIFY_TRACK_ID": "sp-track-nonstop"},
    }
    ctx.update(overrides)
    return ctx


def test_disabled_flag_is_noop(monkeypatch, legacy_db, imported_conn):
    from config.settings import config_manager
    monkeypatch.setattr(config_manager, "get",
                        lambda key, default=None: False if key == "features.library_v2" else default)
    assert A.link_download_into_library_v2(_context()) is None


def test_links_new_album_track_and_file(lib2_enabled, imported_conn):
    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None

    row = imported_conn.execute(
        """SELECT t.title, t.spotify_id, al.title AS album, al.spotify_id AS album_sp,
                  tf.path, tf.source
             FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
            WHERE tf.id = ?""", (file_id,),
    ).fetchone()
    assert row["title"] == "Nonstop"
    assert row["spotify_id"] == "sp-track-nonstop"
    assert row["album"] == "Scorpion"
    assert row["album_sp"] == "sp-scorpion"
    assert row["source"] == "usenet"
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_wanted_tracks WHERE track_id=("
        "SELECT track_id FROM lib2_track_files WHERE id=?)",
        (file_id,),
    ).fetchone()[0] == 1
    # Reuses the existing Drake artist row (no duplicate artist).
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_artists WHERE name='Drake'").fetchone()["c"] == 1


def test_new_autolink_artist_is_not_monitored_without_watchlist(lib2_enabled, imported_conn):
    ctx = _context(_final_processed_path="/music/Newcomer/Debut/01 First.flac")
    ctx["track_info"] = {
        "name": "First",
        "artists": [{"name": "Newcomer", "id": "newcomer-sp"}],
        "album": {"name": "Debut", "id": "debut-sp", "total_tracks": 1,
                  "album_type": "single"},
        "track_number": 1,
        "provider": "spotify",
        "id": "first-sp",
    }
    ctx["_embedded_id_tags"] = {"SPOTIFY_TRACK_ID": "first-sp"}

    assert A.link_download_into_library_v2(ctx) is not None
    assert imported_conn.execute(
        "SELECT monitored FROM lib2_artists WHERE spotify_id='newcomer-sp'"
    ).fetchone()[0] == 0


def test_new_autolink_artist_inherits_real_watchlist_state(lib2_enabled, imported_conn):
    imported_conn.execute(
        """CREATE TABLE IF NOT EXISTS watchlist_artists(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               spotify_artist_id TEXT,
               artist_name TEXT NOT NULL,
               profile_id INTEGER NOT NULL DEFAULT 1)"""
    )
    imported_conn.execute(
        "INSERT INTO watchlist_artists(spotify_artist_id, artist_name, profile_id) "
        "VALUES('watched-sp', 'Watched Newcomer', 1)"
    )
    imported_conn.commit()
    ctx = _context(_final_processed_path="/music/Watched Newcomer/Debut/01 First.flac")
    ctx["track_info"] = {
        "name": "First",
        "artists": [{"name": "Watched Newcomer", "id": "watched-sp"}],
        "album": {"name": "Debut", "id": "watched-debut", "total_tracks": 1,
                  "album_type": "single"},
        "track_number": 1,
        "provider": "spotify",
        "id": "watched-first",
    }
    ctx["_embedded_id_tags"] = {"SPOTIFY_TRACK_ID": "watched-first"}

    assert A.link_download_into_library_v2(ctx) is not None
    assert imported_conn.execute(
        "SELECT monitored FROM lib2_artists WHERE spotify_id='watched-sp'"
    ).fetchone()[0] == 1


def test_persists_verification_and_acoustid_status(lib2_enabled, imported_conn):
    """Deep-dive A7/C4: the pipeline already computes these upstream (same
    context) — the autolink callback is the only place that can put them on
    the file row, otherwise the Info-tab verification badge stays empty for
    every autolink-created file (the normal case today)."""
    file_id = A.link_download_into_library_v2(
        _context(_verification_status="unverified", _acoustid_result="skip"))
    assert file_id is not None

    row = imported_conn.execute(
        "SELECT verification_status, acoustid_status, pipeline_result_json "
        "FROM lib2_track_files WHERE id=?", (file_id,),
    ).fetchone()
    assert row["verification_status"] == "unverified"
    assert row["acoustid_status"] == "skip"
    assert json.loads(row["pipeline_result_json"]) == {}


def test_acoustid_error_and_disabled_make_no_status_claim(lib2_enabled, imported_conn):
    """'error'/'disabled' aren't a pass or a skip — schema's acoustid_status
    should stay NULL rather than encode a made-up claim (only a hard FAIL
    would map to 'fail', and FAIL never reaches this callback: it quarantines
    the file and returns before record_download_provenance runs)."""
    file_id = A.link_download_into_library_v2(
        _context(_verification_status=None, _acoustid_result="disabled"))
    assert file_id is not None
    row = imported_conn.execute(
        "SELECT acoustid_status FROM lib2_track_files WHERE id=?", (file_id,),
    ).fetchone()
    assert row["acoustid_status"] is None


def test_persists_pipeline_result_json_detail(lib2_enabled, imported_conn):
    """AcoustID message + quality-profile fallback flags: real detail the
    pipeline computes and would otherwise discard once this call returns."""
    file_id = A.link_download_into_library_v2(_context(
        _verification_status="unverified",
        _acoustid_result="skip",
        _acoustid_message="no confident fingerprint match",
        _quality_fallback_downsample=True,
    ))
    assert file_id is not None
    row = imported_conn.execute(
        "SELECT pipeline_result_json FROM lib2_track_files WHERE id=?", (file_id,),
    ).fetchone()
    result = json.loads(row["pipeline_result_json"])
    assert result["acoustid_message"] == "no confident fingerprint match"
    assert result["quality_fallback"] == ["downsample"]


def test_version_mismatch_fallback_recorded_in_pipeline_result(lib2_enabled, imported_conn):
    file_id = A.link_download_into_library_v2(_context(
        _verification_status="force_imported",
        _version_mismatch_fallback="live",
    ))
    assert file_id is not None
    row = imported_conn.execute(
        "SELECT verification_status, pipeline_result_json FROM lib2_track_files WHERE id=?",
        (file_id,),
    ).fetchone()
    assert row["verification_status"] == "force_imported"
    assert json.loads(row["pipeline_result_json"])["version_mismatch_fallback"] == "live"


def test_relink_refreshes_verification_fields_without_duplicating_row(
        lib2_enabled, imported_conn):
    """The UPDATE branch (idempotent re-link of the same path) must carry the
    same fields as the INSERT branch, not just quality-probe columns."""
    first_id = A.link_download_into_library_v2(
        _context(_verification_status="unverified", _acoustid_result="skip"))
    second_id = A.link_download_into_library_v2(_context(
        _verification_status="verified", _acoustid_result="pass",
        _acoustid_message="matched",
    ))
    assert first_id == second_id
    row = imported_conn.execute(
        "SELECT verification_status, acoustid_status, pipeline_result_json "
        "FROM lib2_track_files WHERE id=?", (first_id,),
    ).fetchone()
    assert row["verification_status"] == "verified"
    assert row["acoustid_status"] == "pass"
    assert json.loads(row["pipeline_result_json"])["acoustid_message"] == "matched"


def test_new_autolink_artist_uses_live_default_profile(lib2_enabled, imported_conn):
    conn = lib2_enabled._get_connection()
    try:
        conn.execute("UPDATE quality_profiles SET is_default=0")
        conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
            conn.execute(f"UPDATE {table} SET quality_profile_id=2 WHERE quality_profile_id=1")
        conn.execute("DELETE FROM quality_profiles WHERE id=1")

        artist_id = A._find_or_create_artist(conn, "Dynamic Default Artist")
        profile_id = conn.execute(
            "SELECT quality_profile_id FROM lib2_artists WHERE id=?", (artist_id,)
        ).fetchone()[0]
        conn.rollback()
    finally:
        conn.close()

    assert profile_id == 2


def test_autolink_projects_wanted_state_under_the_live_default_profile(
        lib2_enabled, imported_conn):
    """G8: the pipeline has no request-scoped profile, so recompute_wanted
    must resolve the live default profile the same way artist/album/track
    creation already does — never hardcode profile_id=1 (§1 invariant),
    which would silently orphan the wanted row once profile 1 is deleted."""
    conn = lib2_enabled._get_connection()
    try:
        conn.execute("UPDATE quality_profiles SET is_default=0")
        conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
            conn.execute(f"UPDATE {table} SET quality_profile_id=2 WHERE quality_profile_id=1")
        conn.execute("DELETE FROM quality_profiles WHERE id=1")
        conn.commit()
    finally:
        conn.close()

    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None

    row = imported_conn.execute(
        """SELECT wt.profile_id FROM lib2_wanted_tracks wt
             JOIN lib2_track_files tf ON tf.track_id = wt.track_id
            WHERE tf.id = ?""", (file_id,),
    ).fetchone()
    assert row["profile_id"] == 2


def test_attaches_file_to_materialized_missing_track(lib2_enabled, imported_conn):
    """A fileless provider-tracklist row (wanted/missing) gains the file instead
    of a duplicate track being created."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, origin) "
        "VALUES(?, 'Scorpion', 'album', 'sp-scorpion', 'discography')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Nonstop', 1, 1)", (album_id,))
    conn.commit()
    conn.close()

    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None
    # Still exactly one Scorpion album and one Nonstop track.
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'").fetchone()["c"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_tracks WHERE title='Nonstop'").fetchone()["c"] == 1


def test_feat_annotated_title_fills_the_wanted_slot_instead_of_duplicating(
        lib2_enabled, imported_conn):
    """G4: the finished download's title ("One Dance") often doesn't spell
    out the featured-artist annotation the wanted-row's title carries
    ("One Dance (feat. Wizkid & Kyla)") — or vice versa. Without
    dedup_title_key (the same normalization the importer already uses for
    §39), an exact-title match misses this, a duplicate track row gets
    created with the file, and the original wanted-row keeps re-downloading
    the same song forever."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, origin) "
        "VALUES(?, 'Scorpion', 'album', 'sp-scorpion', 'discography')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Nonstop (feat. Wizkid & Kyla)', 1, 1)", (album_id,))
    conn.commit()
    conn.close()

    # The download's own title/tags never mention the feature — a very common
    # real-world spelling difference between a single's tags and the album
    # tracklist. No spotify_track_id on the wanted row either, so the fix must
    # come from the title-normalization fallback, not the ID fast-path.
    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchone()["c"] == 1
    row = imported_conn.execute(
        "SELECT t.title FROM lib2_track_files tf JOIN lib2_tracks t ON t.id=tf.track_id "
        "WHERE tf.id=?", (file_id,),
    ).fetchone()
    assert row["title"] == "Nonstop (feat. Wizkid & Kyla)"


def test_disc_and_track_number_slot_fills_when_titles_dont_normalize_equal(
        lib2_enabled, imported_conn):
    """G4 fallback: even when dedup_title_key doesn't collapse the titles to
    the same key (a genuine title-spelling drift beyond feat.-annotations),
    the (disc, track_number) slot still wins over minting a duplicate row."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, origin) "
        "VALUES(?, 'Scorpion', 'album', 'sp-scorpion', 'discography')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?, 'Nonstop (Radio Edit)', 1, 1, 1)", (album_id,))
    conn.commit()
    conn.close()

    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchone()["c"] == 1


def test_find_or_create_artist_matches_by_spotify_id_despite_name_drift(
        lib2_enabled, imported_conn):
    """G8: a provider identity is a stronger signal than a name string — the
    canonical example is a kanji vs. romaji release credit for the same
    artist, where SQLite's ASCII-only lower() can't even prove two spellings
    differ only by casing. Drake's row already carries spotify_id='sp1' from
    the legacy import; a completely different credit string with that same id
    must still resolve to the one existing row instead of minting a
    duplicate."""
    before = imported_conn.execute("SELECT COUNT(*) c FROM lib2_artists").fetchone()["c"]
    artist_id = A._find_or_create_artist(imported_conn, "Aubrey Graham", spotify_id="sp1")
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    assert artist_id == drake_id
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_artists").fetchone()["c"] == before


def test_find_or_create_artist_backfills_spotify_id_on_name_match(
        lib2_enabled, imported_conn):
    """A name-matched row without a known provider id gets one attached, so
    the NEXT finished download for the same artist can take the indexed
    ID-match path instead of the O(n) name scan."""
    imported_conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, quality_profile_id) "
        "VALUES('Overseas Artist', 'Overseas Artist', 1)")

    artist_id = A._find_or_create_artist(
        imported_conn, "Overseas Artist", spotify_id="sp-overseas")
    row = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()
    assert row["spotify_id"] == "sp-overseas"


def test_find_or_create_artist_never_overwrites_an_existing_spotify_id(
        lib2_enabled, imported_conn):
    """Backfill only fills a NULL — a row that already carries a provider id
    must never be overwritten by a second, possibly-wrong one arriving
    through a plain name match."""
    A._find_or_create_artist(imported_conn, "Drake", spotify_id="sp-wrong")
    row = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE name='Drake'").fetchone()
    assert row["spotify_id"] == "sp1"


def test_new_artist_persists_the_spotify_id_it_was_created_with(
        lib2_enabled, imported_conn):
    artist_id = A._find_or_create_artist(
        imported_conn, "Brand New Artist", spotify_id="sp-new")
    row = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()
    assert row["spotify_id"] == "sp-new"


def test_non_spotify_provider_artist_id_stays_out_of_spotify_column():
    """artists[0]['id'] is populated by non-Spotify clients too (JioSaavn,
    Amazon, …) with their own provider-local ids — never trust it into the
    spotify_id column unless the result itself is Spotify's. §62.4 upgrades
    the old drop-it gate: the id is KEPT, but under its own namespace."""
    ti = {"provider": "jiosaavn", "artists": [{"name": "Some Artist", "id": "jio-123"}]}
    assert A._primary_artist_provider_id(ti) == "jio-123"
    assert A._provider_namespace("jio-123", "jiosaavn") == "jiosaavn"
    assert A._provider_namespace("1239706770", None) is None       # numeric ≠ spotify
    assert A._provider_namespace("1239706770", "spotify") is None  # shape wins
    assert A._provider_namespace("sp-new", None) == "spotify"


def test_end_to_end_autolink_reuses_artist_matched_purely_by_spotify_id(
        lib2_enabled, imported_conn):
    """Full pipeline wiring: a finished download whose track_info spells the
    artist differently from the library row, but carries the same Spotify
    artist id, must attach to the existing Drake artist/album tree instead of
    creating a second 'Aubrey Graham' artist with its own duplicate Scorpion
    album."""
    ctx = _context(track_info={
        "name": "Nonstop",
        "artists": [{"name": "Aubrey Graham", "id": "sp1"}],
        "album": {"name": "Scorpion", "id": "sp-scorpion", "total_tracks": 25,
                  "album_type": "album"},
        "track_number": 1,
        "provider": "spotify",
        "id": "sp-track-nonstop",
    })
    before = imported_conn.execute("SELECT COUNT(*) c FROM lib2_artists").fetchone()["c"]
    file_id = A.link_download_into_library_v2(ctx)
    assert file_id is not None
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_artists").fetchone()["c"] == before
    row = imported_conn.execute(
        """SELECT ar.name FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
             JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE tf.id=?""", (file_id,)).fetchone()
    assert row["name"] == "Drake"


def test_linking_file_graduates_discography_album_to_library(lib2_enabled, imported_conn):
    """Attaching a real file to a provider-only release must flip its origin —
    'My Library' filters on origin/monitored, so an unmonitored discography row
    with a file would otherwise be invisible despite the file existing."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, "
        "origin, monitored) VALUES(?, 'Scorpion', 'album', 'sp-scorpion', 'discography', 0)",
        (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.commit()
    conn.close()

    assert A.link_download_into_library_v2(_context()) is not None
    row = imported_conn.execute(
        "SELECT origin FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
    assert row["origin"] == "library"


def test_idempotent_relink_updates_not_duplicates(lib2_enabled, imported_conn):
    first = A.link_download_into_library_v2(_context())
    second = A.link_download_into_library_v2(_context())
    assert first == second
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_track_files WHERE path LIKE '%Nonstop%'"
    ).fetchone()["c"] == 1


def test_direct_entity_link_beats_name_heuristics(lib2_enabled, imported_conn):
    """A grab that started from Library v2 carries the server-resolved entity
    (audit P1-16). The file must land on THAT track even when the download's
    metadata names something the heuristics would match elsewhere."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Care Package', 'album')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Nonstop', 1, 1)", (album_id,))
    target_track = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    file_id = A.link_download_into_library_v2(_context(
        lib2_entity={"track_id": target_track, "album_id": album_id,
                     "quality_profile_id": 1}))
    assert file_id is not None
    row = imported_conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE id=?", (file_id,)).fetchone()
    assert row["track_id"] == target_track
    # No new Scorpion album was created from the metadata (heuristics skipped).
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'").fetchone()["c"] == 0


def test_retry_track_info_entity_beats_name_heuristics(lib2_enabled, imported_conn):
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Retry Target', 'album')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
        (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Canonical Song', 1, 1)", (album_id,))
    target_track = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    context = _context()
    context["track_info"]["lib2_entity"] = {
        "track_id": target_track,
        "album_id": album_id,
        "quality_profile_id": 1,
    }
    file_id = A.link_download_into_library_v2(context)

    row = imported_conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE id=?", (file_id,)).fetchone()
    assert row["track_id"] == target_track


@pytest.mark.parametrize("as_json", [False, True])
def test_wishlist_source_info_track_id_beats_name_heuristics(
        lib2_enabled, imported_conn, as_json):
    """Mirrored Wishlist rows carry lib2 identity in source_info rather than
    the manual-grab lib2_entity envelope; both must converge on one target."""
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Wishlist Target', 'album')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
        (album_id, artist_id))
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Wishlist Exact Track', 1, 1)", (album_id,))
    target_track = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    source_info = {
        "source": "library_v2",
        "lib2_track_id": target_track,
        "lib2_album_id": album_id,
        "quality_profile_id": 1,
    }
    context = _context()
    context["track_info"]["source_info"] = (
        json.dumps(source_info) if as_json else source_info
    )
    file_id = A.link_download_into_library_v2(context)

    row = imported_conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE id=?", (file_id,)
    ).fetchone()
    assert row["track_id"] == target_track
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_albums WHERE title='Scorpion'"
    ).fetchone()[0] == 0


def test_explicit_entity_wins_over_wishlist_source_info(lib2_enabled, imported_conn):
    tracks = imported_conn.execute(
        "SELECT id, album_id FROM lib2_tracks ORDER BY id LIMIT 2"
    ).fetchall()
    explicit, wishlist = tracks[0], tracks[1]
    context = _context(lib2_entity={
        "track_id": explicit["id"],
        "album_id": explicit["album_id"],
    })
    context["track_info"]["source_info"] = {
        "lib2_track_id": wishlist["id"],
        "lib2_album_id": wishlist["album_id"],
    }

    file_id = A.link_download_into_library_v2(context)

    row = imported_conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE id=?", (file_id,)
    ).fetchone()
    assert row["track_id"] == explicit["id"]


def test_direct_album_link_creates_track_inside_that_album(lib2_enabled, imported_conn):
    conn = lib2_enabled._get_connection()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Care Package', 'album')", (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album_id, artist_id))
    conn.commit()
    conn.close()

    file_id = A.link_download_into_library_v2(_context(
        lib2_entity={"album_id": album_id, "quality_profile_id": 1}))
    assert file_id is not None
    row = imported_conn.execute(
        """SELECT t.album_id FROM lib2_track_files tf
           JOIN lib2_tracks t ON t.id = tf.track_id WHERE tf.id=?""",
        (file_id,)).fetchone()
    assert row["album_id"] == album_id


def test_stale_entity_falls_back_to_heuristics(lib2_enabled, imported_conn):
    """The named track was deleted between grab and import — fall back to the
    heuristic path rather than dropping the link entirely."""
    file_id = A.link_download_into_library_v2(_context(
        lib2_entity={"track_id": 999999, "album_id": 999999,
                     "quality_profile_id": 1}))
    assert file_id is not None
    row = imported_conn.execute(
        """SELECT t.title FROM lib2_track_files tf
           JOIN lib2_tracks t ON t.id = tf.track_id WHERE tf.id=?""",
        (file_id,)).fetchone()
    assert row["title"] == "Nonstop"


# ---------------------------------------------------------------------------
# §62.4/§62.6 Stufe 4+5: provider-namespace-aware ids at the lib2 boundary
# ---------------------------------------------------------------------------

def test_numeric_deezer_id_is_not_stored_as_spotify_id(lib2_enabled, imported_conn):
    """A Deezer-provided download (numeric ids, provider marker) must not
    poison lib2 spotify_id columns — the id belongs in external_ids.deezer."""
    ctx = _context()
    ctx["track_info"] = {
        "name": "Nonstop",
        "artists": [{"name": "Drake", "id": "12345"}],
        "album": {"name": "Scorpion", "id": "42695001", "total_tracks": 25,
                  "album_type": "album"},
        "track_number": 1,
        "provider": "deezer",
        "id": "999111",
    }
    ctx["_embedded_id_tags"] = {}

    assert A.link_download_into_library_v2(ctx) is not None

    album = imported_conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE title='Scorpion'"
    ).fetchone()
    assert album["spotify_id"] is None
    assert json.loads(album["external_ids"])["deezer"] == "42695001"
    artist = imported_conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_artists WHERE name='Drake'"
    ).fetchone()
    assert artist["spotify_id"] in (None, "sp1")   # legacy id may exist; never 12345
    assert artist["spotify_id"] != "12345"
    track = imported_conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_tracks WHERE title='Nonstop'"
    ).fetchone()
    assert track["spotify_id"] is None
    assert json.loads(track["external_ids"])["deezer"] == "999111"


def test_deezer_album_id_matches_existing_row_by_external_ids(
        lib2_enabled, imported_conn):
    """A second Deezer download for the same album must find the row via
    external_ids.deezer instead of creating a duplicate."""
    ctx = _context()
    ctx["track_info"] = {
        "name": "Nonstop",
        "artists": [{"name": "Drake"}],
        "album": {"name": "Scorpion", "id": "42695001", "total_tracks": 25,
                  "album_type": "album"},
        "track_number": 1, "provider": "deezer", "id": "999111",
    }
    ctx["_embedded_id_tags"] = {}
    A.link_download_into_library_v2(ctx)

    ctx2 = _context()
    ctx2["_final_processed_path"] = "/music/Drake/Scorpion/02 Elevate.flac"
    ctx2["track_info"] = {
        "name": "Elevate",
        "artists": [{"name": "Drake"}],
        # Retagged variant title — only the deezer id can match it up.
        "album": {"name": "Scorpion (Intl. Edition)", "id": "42695001",
                  "total_tracks": 25, "album_type": "album"},
        "track_number": 2, "provider": "deezer", "id": "999112",
    }
    ctx2["_embedded_id_tags"] = {}
    A.link_download_into_library_v2(ctx2)

    count = imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title LIKE 'Scorpion%'"
    ).fetchone()["c"]
    assert count == 1


def test_unmarked_numeric_id_is_not_persisted_but_matches_poisoned_rows(
        lib2_enabled, imported_conn):
    """No provider marker + non-Spotify-shaped id: never write it to
    spotify_id — but a pre-existing (poisoned) row carrying it as spotify_id
    must still match so today's libraries keep linking (§62.4c)."""
    aid = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id) "
        "VALUES(?, 'Numeric Legacy', 'album', '1239706770')", (aid,))
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) "
        "SELECT id, ?, 'primary' FROM lib2_albums WHERE title='Numeric Legacy'", (aid,))
    imported_conn.commit()

    ctx = _context()
    ctx["track_info"] = {
        "name": "Some Cut",
        "artists": [{"name": "Drake"}],
        "album": {"name": "Totally Different Spelling", "id": "1239706770",
                  "total_tracks": 33, "album_type": "album"},
        "track_number": 1,
        "id": "77001",
    }
    ctx["_embedded_id_tags"] = {}
    A.link_download_into_library_v2(ctx)

    # Matched the poisoned row by value — no new album row.
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Totally Different Spelling'"
    ).fetchone()["c"] == 0
    # And the id was NOT laundered into any new spotify_id column.
    rows = imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE spotify_id='1239706770'"
    ).fetchone()["c"]
    assert rows == 1
