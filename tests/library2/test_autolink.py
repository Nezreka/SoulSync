"""Auto-linking finished downloads into Library v2 (post-processing hook)."""

from __future__ import annotations

import json

import pytest

from core.library2 import autolink as A


@pytest.fixture(autouse=True)
def _disable_unrelated_artwork_warmup(monkeypatch):
    """Keep DB-link tests from leaking provider futures into later modules."""
    monkeypatch.setattr(A, "_warm_new_artwork", lambda *_args, **_kwargs: None)


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    """Enable the feature flag and point get_database at the test DB."""
    from core.settings import config_manager

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


def test_deprecated_false_flag_cannot_disable_autolink(monkeypatch, legacy_db, imported_conn):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, "get",
                        lambda key, default=None: False if key == "features.library_v2" else default)
    assert A.link_download_into_library_v2(_context()) is not None


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


def test_retained_master_and_lossy_derivative_are_linked_with_provenance(
    lib2_enabled, imported_conn, tmp_path, monkeypatch,
):
    from core.quality.model import AudioQuality

    master = tmp_path / "Nonstop.flac"
    derivative = tmp_path / "Nonstop.opus"
    master.write_bytes(b"master")
    derivative.write_bytes(b"derivative")

    def quality(path):
        if str(path).endswith(".flac"):
            return AudioQuality("flac", sample_rate=96000, bit_depth=24)
        return AudioQuality("opus", bitrate=256, sample_rate=48000)

    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", quality)
    file_id = A.link_download_into_library_v2(_context(
        _final_processed_path=str(master),
        _companion_file_paths=[str(derivative)],
        _acquired_audio_quality=quality(master).to_dict(),
        _retention_transforms=[{
            "type": "lossy_copy", "source_replaced": False,
            "codec": "opus", "bitrate": "256",
            "output_quality": quality(derivative).to_dict(),
        }],
    ))

    rows = imported_conn.execute(
        """SELECT id, path, is_primary, file_role, derived_from_file_id,
                  acquired_quality_json, retention_json
             FROM lib2_track_files
            WHERE track_id=(SELECT track_id FROM lib2_track_files WHERE id=?)
              AND path IN (?,?) ORDER BY id""",
        (file_id, str(master), str(derivative)),
    ).fetchall()
    by_path = {row["path"]: row for row in rows}
    assert by_path[str(master)]["is_primary"] == 1
    assert by_path[str(master)]["file_role"] == "master"
    assert by_path[str(derivative)]["is_primary"] == 0
    assert by_path[str(derivative)]["file_role"] == "derivative"
    assert by_path[str(derivative)]["derived_from_file_id"] == file_id
    assert json.loads(by_path[str(master)]["acquired_quality_json"])["bit_depth"] == 24
    assert json.loads(by_path[str(derivative)]["retention_json"])[0]["type"] == "lossy_copy"


def test_downsampled_primary_is_marked_as_destructive_derivative(
    lib2_enabled, imported_conn, tmp_path, monkeypatch,
):
    from core.quality.model import AudioQuality

    retained = tmp_path / "Nonstop.flac"
    retained.write_bytes(b"cd quality")
    measured = AudioQuality("flac", sample_rate=44100, bit_depth=16)
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _path: measured)

    file_id = A.link_download_into_library_v2(_context(
        _final_processed_path=str(retained),
        _acquired_audio_quality=AudioQuality(
            "flac", sample_rate=96000, bit_depth=24,
        ).to_dict(),
        _retention_transforms=[{
            "type": "downsample_hires_flac", "source_replaced": True,
            "target_bit_depth": 16, "target_sample_rate": 44100,
            "output_quality": measured.to_dict(),
        }],
    ))

    row = imported_conn.execute(
        """SELECT file_role, acquired_quality_json, retention_json
             FROM lib2_track_files WHERE id=?""",
        (file_id,),
    ).fetchone()
    assert row["file_role"] == "derivative"
    assert json.loads(row["acquired_quality_json"])["sample_rate"] == 96000
    assert json.loads(row["retention_json"])[0]["source_replaced"] is True


def _legacy_auto_import_context(*, source, artist, artist_id, album, album_id,
                                title, track_id, track_number, path):
    """The real producer shape from ``AutoImportWorker._process_matches``.

    The legacy ``spotify_*`` keys are intentionally provider-neutral aliases;
    the canonical provider lives at context level and the track has no nested
    album/provider object.
    """
    return {
        "_final_processed_path": path,
        "source": source,
        "_download_username": "auto_import",
        "spotify_artist": {"id": artist_id, "name": artist},
        "spotify_album": {
            "id": album_id,
            "name": album,
            "album_type": "album",
            "total_tracks": 2,
            "artists": [{"id": artist_id, "name": artist}],
        },
        "track_info": {
            "id": track_id,
            "name": title,
            "track_number": track_number,
            "disc_number": 1,
            "album_id": album_id,
            "artists": [{"name": artist}],
        },
        "original_search_result": {
            "title": title,
            "artist": artist,
            "album": album,
        },
        "_embedded_id_tags": {},
    }


def test_auto_import_uses_canonical_album_for_every_track(
        lib2_enabled, imported_conn):
    first = _legacy_auto_import_context(
        source="jiosaavn", artist="Auto Context Artist", artist_id="jio-art-1",
        album="One Real Album", album_id="jio-album-1", title="First Song",
        track_id="jio-track-1", track_number=1,
        path="/music/Auto Context Artist/One Real Album/01 First Song.flac",
    )
    second = _legacy_auto_import_context(
        source="jiosaavn", artist="Auto Context Artist", artist_id="jio-art-1",
        album="One Real Album", album_id="jio-album-1", title="Second Song",
        track_id="jio-track-2", track_number=2,
        path="/music/Auto Context Artist/One Real Album/02 Second Song.flac",
    )

    assert A.link_download_into_library_v2(first) is not None
    assert A.link_download_into_library_v2(second) is not None

    albums = imported_conn.execute(
        "SELECT title, spotify_id, external_ids FROM lib2_albums "
        "WHERE primary_artist_id=(SELECT id FROM lib2_artists "
        "WHERE name='Auto Context Artist')"
    ).fetchall()
    assert len(albums) == 1
    assert albums[0]["title"] == "One Real Album"
    assert albums[0]["spotify_id"] is None
    assert json.loads(albums[0]["external_ids"])["jiosaavn"] == "jio-album-1"
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=(SELECT id FROM "
        "lib2_albums WHERE title='One Real Album')"
    ).fetchone()[0] == 2


@pytest.mark.parametrize(
    ("source", "prefix"),
    [("jiosaavn", "jio"), ("qobuz", "qbz"), ("deezer", "987")],
)
def test_auto_import_keeps_provider_ids_in_their_canonical_namespace(
        lib2_enabled, imported_conn, source, prefix):
    context = _legacy_auto_import_context(
        source=source,
        artist=f"{source} Namespace Artist",
        artist_id=f"{prefix}-artist",
        album=f"{source} Namespace Album",
        album_id=f"{prefix}-album",
        title=f"{source} Namespace Track",
        track_id=f"{prefix}-track",
        track_number=1,
        path=f"/music/{source}/namespace.flac",
    )

    file_id = A.link_download_into_library_v2(context)
    assert file_id is not None
    row = imported_conn.execute(
        """SELECT ar.spotify_id AS artist_sp, ar.external_ids AS artist_ext,
                  al.spotify_id AS album_sp, al.external_ids AS album_ext,
                  t.spotify_id AS track_sp, t.external_ids AS track_ext
             FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id=tf.track_id
             JOIN lib2_albums al ON al.id=t.album_id
             JOIN lib2_artists ar ON ar.id=al.primary_artist_id
            WHERE tf.id=?""",
        (file_id,),
    ).fetchone()
    assert row["artist_sp"] is None
    assert row["album_sp"] is None
    assert row["track_sp"] is None
    assert json.loads(row["artist_ext"])[source] == f"{prefix}-artist"
    assert json.loads(row["album_ext"])[source] == f"{prefix}-album"
    assert json.loads(row["track_ext"])[source] == f"{prefix}-track"


def test_simple_download_is_materialized_from_its_filename(lib2_enabled, imported_conn):
    """Root cause behind the "Quarantine Approve later flagged as Orphan"
    report (docs/library-v2-issues.md §7): a Simple Download's
    ``search_result`` carries ``is_simple_download`` but no title/artist, and
    ``track_info`` is structurally empty ``{}`` (see
    tests/imports/test_import_pipeline.py). autolink's early-exit guard
    (``not direct_track_id and not direct_album_id and (not title or not
    artist_name)``) used to skip the file unconditionally — with NO quarantine
    involved at all. The legacy/history write path
    (core/imports/side_effects.py::record_download_provenance) has no such
    guard and records the download regardless, so the import "succeeded" from
    the user's point of view while lib2 never learned the file exists, and a
    later orphan scan (core/repair_jobs/orphan_file_detector.py) — which only
    knows active lib2 subjects — reported it as an orphan.

    Decision of 26 July 2026 (status §18) is option 1, materialize: derive a
    fallback identity so the file becomes a real, visible catalogue row."""
    ctx = _context(
        track_info={},
        search_result={"is_simple_download": True,
                       "filename": "Some Artist - Some Song.flac"},
    )
    file_id = A.link_download_into_library_v2(ctx)
    assert file_id is not None

    row = imported_conn.execute(
        """SELECT t.title, al.title AS album, ar.name AS artist
             FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
             JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE tf.id = ?""",
        (file_id,),
    ).fetchone()
    assert row["artist"] == "Some Artist"
    assert row["title"] == "Some Song"


def test_simple_download_prefers_the_files_own_tags(lib2_enabled, imported_conn,
                                                    monkeypatch, tmp_path):
    """Embedded tags are ground truth after the import pipeline wrote them;
    the filename is only the fallback's fallback."""
    monkeypatch.setattr(
        A, "read_tag_snapshot",
        lambda _path: {"title": "Nonstop", "artist": "Drake", "album": "Scorpion",
                       "track_number": 1},
    )
    ctx = _context(
        track_info={},
        search_result={"is_simple_download": True, "filename": "junk-name.flac"},
    )
    file_id = A.link_download_into_library_v2(ctx)
    assert file_id is not None

    row = imported_conn.execute(
        """SELECT t.title, ar.name AS artist FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
             JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE tf.id = ?""",
        (file_id,),
    ).fetchone()
    assert (row["artist"], row["title"]) == ("Drake", "Nonstop")
    # Reuses the seeded Drake row rather than minting a second one.
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_artists WHERE name='Drake'").fetchone()["c"] == 1


def test_untitled_simple_download_still_becomes_visible(lib2_enabled, imported_conn):
    """No tags, no "Artist - Title" filename: the row is still materialized —
    an invisible file is exactly what makes the orphan detector fire."""
    ctx = _context(
        _final_processed_path="/music/loose/mystery.flac",
        track_info={},
        search_result={"is_simple_download": True, "filename": "mystery.flac"},
    )
    file_id = A.link_download_into_library_v2(ctx)
    assert file_id is not None

    row = imported_conn.execute(
        """SELECT t.title, ar.name AS artist FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
             JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE tf.id = ?""",
        (file_id,),
    ).fetchone()
    assert row["title"] == "mystery"
    assert row["artist"] == A.UNKNOWN_ARTIST


def test_materialized_simple_download_is_not_monitored(lib2_enabled, imported_conn):
    """A guessed identity must never turn into acquisition intent."""
    ctx = _context(
        track_info={},
        search_result={"is_simple_download": True,
                       "filename": "Some Artist - Some Song.flac"},
    )
    file_id = A.link_download_into_library_v2(ctx)

    row = imported_conn.execute(
        """SELECT ar.monitored AS artist_monitored, al.monitored AS album_monitored,
                  t.monitored AS track_monitored
             FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
             JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE tf.id = ?""",
        (file_id,),
    ).fetchone()
    assert (row["artist_monitored"], row["album_monitored"],
            row["track_monitored"]) == (0, 0, 0)


def test_a_download_with_no_identity_at_all_is_still_skipped(lib2_enabled,
                                                             imported_conn):
    """The fallback needs *some* filename to work from; without one there is
    nothing to materialize and the old skip remains correct."""
    ctx = _context(track_info={}, search_result={})
    ctx["_final_processed_path"] = ""
    assert A.link_download_into_library_v2(ctx) is None


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


def test_autolink_projects_wanted_state_under_the_admin_user_profile(
        lib2_enabled, imported_conn):
    """SYNC-04: `recompute_wanted`'s `profile_id` is the USER profile that owns
    the monitoring intent, not the quality profile.

    This test used to assert the opposite — that a default *quality* profile of
    2 should file the wanted row under profile 2 — and so pinned the namespace
    confusion as the contract. With the two ids distinct, that row lands under a
    profile no Library-v2 consumer reads: `track_wanted_states` raises "stale",
    the admin status reports the track missing, and `list_cutoff_unmet` never
    offers the upgrade. The quality profile still governs quality, through the
    track's own `quality_profile_id` cascade."""
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
        """SELECT wt.profile_id, wt.track_id FROM lib2_wanted_tracks wt
             JOIN lib2_track_files tf ON tf.track_id = wt.track_id
            WHERE tf.id = ?""", (file_id,),
    ).fetchone()
    from core.library2 import ADMIN_PROFILE_ID
    assert row["profile_id"] == ADMIN_PROFILE_ID
    # ...and the consumer that reads the admin scope can actually see it.
    from core.library2.wanted import track_wanted_states
    assert track_wanted_states(
        imported_conn, [row["track_id"]], profile_id=ADMIN_PROFILE_ID
    ).keys() == {row["track_id"]}
    # The quality profile is still the live default; it just is not the owner
    # of the monitoring intent.
    from core.library2.profile_lookup import default_quality_profile_id
    assert default_quality_profile_id(imported_conn) == 2


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
    artist_id = A._find_or_create_artist(
        imported_conn, "Aubrey Graham", spotify_id="sp1", source="spotify"
    )
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
        imported_conn, "Overseas Artist", spotify_id="sp-overseas",
        source="spotify")
    row = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()
    assert row["spotify_id"] == "sp-overseas"


def test_find_or_create_artist_never_overwrites_an_existing_spotify_id(
        lib2_enabled, imported_conn):
    """Backfill only fills a NULL — a row that already carries a provider id
    must never be overwritten by a second, possibly-wrong one arriving
    through a plain name match."""
    A._find_or_create_artist(
        imported_conn, "Drake", spotify_id="sp-wrong", source="spotify"
    )
    row = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE name='Drake'").fetchone()
    assert row["spotify_id"] == "sp1"


def test_new_artist_persists_the_spotify_id_it_was_created_with(
        lib2_enabled, imported_conn):
    artist_id = A._find_or_create_artist(
        imported_conn, "Brand New Artist", spotify_id="sp-new", source="spotify")
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
    assert A._provider_namespace("sp-new", None) is None          # never guess


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


def test_unknown_namespace_never_cross_matches_same_opaque_external_id(
        lib2_enabled, imported_conn):
    deezer = imported_conn.execute(
        "INSERT INTO lib2_artists(name, external_ids) VALUES(?, ?)",
        ("Opaque Deezer", json.dumps({"deezer": "shared-opaque"})),
    ).lastrowid
    qobuz = imported_conn.execute(
        "INSERT INTO lib2_artists(name, external_ids) VALUES(?, ?)",
        ("Opaque Qobuz", json.dumps({"qobuz": "shared-opaque"})),
    ).lastrowid

    assert A._find_or_create_artist(
        imported_conn, "Different spelling", spotify_id="shared-opaque", source="qobuz",
    ) == qobuz
    unknown = A._find_or_create_artist(
        imported_conn, "Opaque Unknown", spotify_id="shared-opaque", source=None,
    )
    assert unknown not in {deezer, qobuz}


def test_compatibility_placeholders_are_never_persisted_as_provider_ids(
        lib2_enabled, imported_conn):
    file_id = A.link_download_into_library_v2(_context(
        _final_processed_path="/music/Placeholder/Release/01 Song.flac",
        _embedded_id_tags={},
        track_info={
            "id": "lib2-track:stable-token", "name": "Placeholder Song",
            "provider": "library_v2",
            "artists": [{"id": "explicit_artist", "name": "Placeholder Artist"}],
            "album": {"id": "lib2-album:stable-token", "name": "Placeholder Album"},
            "track_number": 1,
        },
    ))
    row = imported_conn.execute(
        """SELECT ar.spotify_id artist_sp, ar.external_ids artist_ext,
                  al.spotify_id album_sp, al.external_ids album_ext,
                  t.spotify_id track_sp, t.external_ids track_ext
             FROM lib2_track_files f JOIN lib2_tracks t ON t.id=f.track_id
             JOIN lib2_albums al ON al.id=t.album_id
             JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE f.id=?""",
        (file_id,),
    ).fetchone()
    assert not any((row["artist_sp"], row["album_sp"], row["track_sp"]))
    assert all(json.loads(row[key]) == {} for key in
               ("artist_ext", "album_ext", "track_ext"))


def test_stale_direct_context_falls_back_to_real_qualified_provider_ids(
        lib2_enabled, imported_conn):
    artist_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, external_ids) VALUES(?, ?)",
        ("Canonical Qobuz Artist", json.dumps({"qobuz": "qb-artist"})),
    ).lastrowid
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, external_ids) VALUES(?,?,?)",
        (artist_id, "Canonical Qobuz Album", json.dumps({"qobuz": "qb-album"})),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'primary')",
        (album_id, artist_id),
    )
    track_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, external_ids) VALUES(?,?,?)",
        (album_id, "Canonical Qobuz Track", json.dumps({"qobuz": "qb-track"})),
    ).lastrowid
    imported_conn.commit()

    file_id = A.link_download_into_library_v2(_context(
        _final_processed_path="/music/qobuz/stale-direct.flac",
        _embedded_id_tags={},
        track_info={
            "id": "lib2-track:stale", "name": "Incoming Track Spelling",
            "artists": [{"id": "explicit_artist", "name": "Incoming Artist Spelling",
                         "provider_ids": {"qobuz": "qb-artist"}}],
            "album": {"id": "lib2-album:stale", "name": "Incoming Album Spelling",
                      "provider_ids": {"qobuz": "qb-album"}},
            "source_info": {
                "source": "library_v2", "lib2_track_id": 999999,
                "lib2_album_id": 999999, "metadata_source": "qobuz",
                "track_provider_ids": {"qobuz": "qb-track"},
                "album_provider_ids": {"qobuz": "qb-album"},
            },
        },
    ))

    assert imported_conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE id=?", (file_id,),
    ).fetchone()["track_id"] == track_id


def test_simple_download_never_adopts_the_source_result_id(lib2_enabled, imported_conn):
    """A Simple Download's ``search_result.id`` is the *source's* result token
    (Soulseek/usenet), not a music-provider identity. ``ti`` falls back to that
    dict, so adopting its id would poison ``spotify_id`` — §62.4 / guide §2.5:
    provider ids are always qualified."""
    ctx = _context(
        track_info={},
        search_result={"is_simple_download": True, "id": "slsk-result-991",
                       "filename": "Some Artist - Some Song.flac"},
        _embedded_id_tags={},
    )
    file_id = A.link_download_into_library_v2(ctx)

    row = imported_conn.execute(
        """SELECT t.spotify_id, t.external_ids, al.spotify_id AS album_sp
             FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
            WHERE tf.id = ?""",
        (file_id,),
    ).fetchone()
    assert row["spotify_id"] is None
    assert row["album_sp"] is None
    assert "slsk-result-991" not in (row["external_ids"] or "")


def test_simple_download_keeps_an_embedded_spotify_id(lib2_enabled, imported_conn):
    """An id read off the file itself IS a qualified identity and must survive
    the fallback path."""
    ctx = _context(
        track_info={},
        search_result={"is_simple_download": True,
                       "filename": "Some Artist - Some Song.flac"},
    )
    ctx["_embedded_id_tags"] = {"SPOTIFY_TRACK_ID": "sp-embedded-1"}
    file_id = A.link_download_into_library_v2(ctx)

    assert imported_conn.execute(
        """SELECT t.spotify_id FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id WHERE tf.id=?""",
        (file_id,),
    ).fetchone()["spotify_id"] == "sp-embedded-1"


# --- iss29-D13: artist lookup must not scan the table per download --------
#
# `_find_or_create_artist` runs once per finished download. Its "fast path",
# `WHERE lower(name) = ?`, is not fast: EXPLAIN QUERY PLAN reports SCAN, not
# SEARCH, because no index covers `lower(name)`. Worse, SQLite's `lower()` is
# ASCII-only, so every Cyrillic/Greek/CJK/Turkish name misses it and falls
# through to a second, Python-side scan of the whole table. Measured on
# 100k artists: ~169 ms per download (8 ms SQL scan + 160 ms Python scan).
# An indexed normalized key answers the same question in ~0.004 ms.


def _artists_conn(tmp_path, rows=()):
    """A bare lib2 schema plus whatever artists the test needs."""
    import sqlite3 as _sqlite3

    from core.library2.schema import ensure_library_v2_schema

    conn = _sqlite3.connect(str(tmp_path / "artists.db"))
    conn.row_factory = _sqlite3.Row
    ensure_library_v2_schema(conn)
    for name in rows:
        conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES(?, ?)",
                     (name, name))
    conn.commit()
    return conn


@pytest.mark.parametrize("stored,looked_up", [
    ("Любэ", "Любэ"),            # Cyrillic: sqlite lower() is a no-op here
    ("ЛЮБЭ", "любэ"),            # ...and cannot case-fold it either
    ("Μέλισσες", "ΜΈΛΙΣΣΕΣ"),    # Greek, with the accent SQLite leaves alone
    ("宇多田ヒカル", "宇多田ヒカル"),  # CJK: no case at all, still has to match
    ("Sigur Rós", "SIGUR RÓS"),  # Latin-1 supplement inside an ASCII name
    ("Drake", "drake"),          # the plain ASCII case must keep working
    ("Aphex  Twin", "Aphex Twin"),  # normalize_name collapses whitespace
])
def test_existing_artist_is_found_without_scanning_the_table(tmp_path, stored, looked_up):
    conn = _artists_conn(tmp_path, [stored, "Filler One", "Filler Two"])
    try:
        statements = []
        conn.set_trace_callback(statements.append)
        artist_id = A._find_or_create_artist(conn, looked_up, create=False)
        conn.set_trace_callback(None)

        assert artist_id is not None, f"{looked_up!r} did not match stored {stored!r}"
        assert not [s for s in statements if "FROM lib2_artists" in s and "WHERE" not in s], (
            "fell back to a full-table scan: " + repr(statements)
        )
    finally:
        conn.close()


def test_new_artist_row_carries_its_normalized_key(tmp_path):
    from core.library2.importer import normalize_name

    conn = _artists_conn(tmp_path)
    try:
        artist_id = A._find_or_create_artist(conn, "ЛЮБЭ")
        row = conn.execute("SELECT name, name_key FROM lib2_artists WHERE id=?",
                           (artist_id,)).fetchone()
        assert row["name"] == "ЛЮБЭ", "display name must stay untouched"
        assert row["name_key"] == normalize_name("ЛЮБЭ")
    finally:
        conn.close()


def test_schema_migration_backfills_the_key_for_existing_rows(tmp_path):
    """Installs that predate the column must not need a re-import to benefit."""
    import sqlite3 as _sqlite3

    from core.library2.importer import normalize_name
    from core.library2.schema import ensure_library_v2_schema

    path = str(tmp_path / "old.db")
    conn = _sqlite3.connect(path)
    conn.row_factory = _sqlite3.Row
    ensure_library_v2_schema(conn)
    # Simulate a pre-migration install: drop the key back to NULL.
    conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES('ЛЮБЭ', 'ЛЮБЭ')")
    conn.execute("UPDATE lib2_artists SET name_key=NULL")
    conn.commit()
    conn.close()

    conn = _sqlite3.connect(path)
    conn.row_factory = _sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
    try:
        assert conn.execute(
            "SELECT name_key FROM lib2_artists WHERE name='ЛЮБЭ'"
        ).fetchone()["name_key"] == normalize_name("ЛЮБЭ")
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='index' AND name='idx_lib2_artists_name_key'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_rows_without_a_key_are_still_matched(tmp_path):
    """A write path that never learned about `name_key` must not vanish.

    The Python scan stays as a backstop, but scoped to the unkeyed rows only —
    so it costs nothing on a migrated database and still finds anything a
    direct SQL insert (tests, ad-hoc repair) left behind.
    """
    conn = _artists_conn(tmp_path, ["Filler"])
    try:
        conn.execute(
            "INSERT INTO lib2_artists(name, sort_name, name_key) VALUES('ЛЮБЭ','ЛЮБЭ',NULL)")
        conn.commit()
        assert A._find_or_create_artist(conn, "любэ", create=False) is not None
    finally:
        conn.close()


def test_reimport_onto_a_deleted_path_reactivates_its_row(lib2_enabled, imported_conn):
    """FI-02: delete a file from the library, download the same track again.

    The delete keeps the row as history (`file_state='deleted'`, ADR-03) and the
    re-import lands on the very same path, so autolink takes its UPDATE branch —
    which refreshed the quality columns and left the row retired. The bytes were
    at the destination but the catalogue said the file was gone: the
    registration gate found no active row, `primary_file_row` skipped it and the
    library scan excluded it, while the exception recovery still read the
    returned id as success. `retire_replaced_files` deliberately skips the
    keep_path, so nothing downstream brought it back either."""
    from core.library2.track_files import set_file_state

    file_id = A.link_download_into_library_v2(_context())
    assert file_id is not None
    set_file_state(imported_conn, int(file_id), "deleted")
    imported_conn.execute(
        "UPDATE lib2_track_files SET missing_since=CURRENT_TIMESTAMP,"
        " missing_scan_count=3 WHERE id=?", (file_id,))
    imported_conn.commit()

    again = A.link_download_into_library_v2(_context())
    assert again == file_id

    row = imported_conn.execute(
        "SELECT file_state, missing_since, missing_scan_count, is_primary"
        "  FROM lib2_track_files WHERE id=?", (file_id,)).fetchone()
    assert row["file_state"] == "active"
    assert row["missing_since"] is None
    assert row["missing_scan_count"] == 0
    assert row["is_primary"] == 1


def test_reimport_onto_an_active_path_leaves_its_state_alone(
        lib2_enabled, imported_conn):
    """The reactivation must not become a blanket state write: an already
    active row keeps everything the normal update path gives it."""
    file_id = A.link_download_into_library_v2(_context())
    before = imported_conn.execute(
        "SELECT file_state, is_primary FROM lib2_track_files WHERE id=?",
        (file_id,)).fetchone()

    assert A.link_download_into_library_v2(_context()) == file_id

    after = imported_conn.execute(
        "SELECT file_state, is_primary FROM lib2_track_files WHERE id=?",
        (file_id,)).fetchone()
    assert dict(after) == dict(before)
