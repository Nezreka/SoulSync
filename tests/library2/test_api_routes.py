"""Flask-level tests for the Library v2 API (api/library_v2.py).

The core modules have their own unit tests; these cover the route layer's
own logic — artwork URL rewriting, monitor/profile cascades incl. the
consolidated-duplicate guard, delete cleanup, and input validation — against
a real (temp) SQLite schema with a fake MusicDatabase for the mirror calls.
"""

from __future__ import annotations

import sqlite3

import pytest

flask = pytest.importorskip("flask")


class FakeDB:
    """MusicDatabase stand-in: real sqlite connection + recorded mirror calls."""

    def __init__(self, path: str):
        self.database_path = path
        self.wishlist_adds = []
        self.wishlist_removes = []
        self.watchlist_adds = []
        self.watchlist_removes = []

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- wishlist/watchlist mirror surface (recorded, always succeeds) -------
    def add_to_wishlist(self, payload, source_type="unknown", source_info=None,
                        user_initiated=False, profile_id=1, quality_profile_id=None,
                        raise_on_error=False):
        self.wishlist_adds.append({
            "id": payload.get("id"), "profile_id": profile_id,
            "quality_profile_id": quality_profile_id, "source_type": source_type,
            "user_initiated": user_initiated,
        })
        return True

    def remove_from_wishlist(self, track_id, profile_id=1, raise_on_error=False):
        self.wishlist_removes.append({"id": track_id, "profile_id": profile_id})
        return True

    def add_artist_to_watchlist(self, ext_id, name, profile_id, source,
                                raise_on_error=False):
        self.watchlist_adds.append({"ext_id": ext_id, "profile_id": profile_id})
        return True

    def remove_artist_from_watchlist(self, ext_id, profile_id,
                                     raise_on_error=False):
        self.watchlist_removes.append({"ext_id": ext_id, "profile_id": profile_id})
        return True


@pytest.fixture
def api(tmp_path):
    """A test client over a seeded lib2 DB. Yields (client, FakeDB, ids)."""
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)

    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id, monitored) "
                "VALUES('Drake','Drake','sp-drake',0)")
    artist_id = cur.lastrowid

    def _album(title, album_type, monitored=0):
        cur.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type, monitored) "
            "VALUES(?,?,?,?)", (artist_id, title, album_type, monitored))
        album_id = cur.lastrowid
        cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                    (album_id, artist_id))
        return album_id

    views_id = _album("Views", "album")
    single_id = _album("One Dance", "single")
    ep_id = _album("Best EP", "ep")

    def _track(album_id, title, monitored=0, spotify_id=None, canonical=None):
        cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, monitored, "
            "spotify_id, canonical_track_id) VALUES(?,?,1,?,?,?)",
            (album_id, title, monitored, spotify_id, canonical))
        track_id = cur.lastrowid
        cur.execute("INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id) "
                    "VALUES(?,?)", (track_id, artist_id))
        return track_id

    # Canonical pair: the album version owns the file, the single variant was
    # consolidated away (no file, canonical link to the album version).
    album_track = _track(views_id, "One Dance", spotify_id="sp-t1")
    single_track = _track(single_id, "One Dance", canonical=album_track)
    ep_track = _track(ep_id, "EP Song", spotify_id="sp-t2")
    cur.execute("INSERT INTO lib2_track_files(track_id, path, format, bitrate) "
                "VALUES(?, '/m/one-dance.flac', 'flac', 1000)", (album_track,))
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    # ADR-01: lib2 writes are admin-only (profile 1). Tests flip this to a
    # non-admin id to probe the rejection path.
    db.active_profile = 1
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: (
            True if key == "features.library_v2" else default),
        config_manager=None,
        profile_id_getter=lambda: db.active_profile,
    )
    ids = {"artist": artist_id, "views": views_id, "single": single_id,
           "ep": ep_id, "album_track": album_track,
           "single_track": single_track, "ep_track": ep_track}
    yield app.test_client(), db, ids


def _conn(db: FakeDB) -> sqlite3.Connection:
    return db._get_connection()


def test_eps_get_local_artwork_urls(api):
    """Every release group — including EPs — must point at the local artwork
    endpoint, never at a raw DB image_url (which may be a media-server URL)."""
    client, _db, ids = api
    data = client.get(f"/api/library/v2/artists/{ids['artist']}").get_json()
    assert data["success"] is True
    for group in ("albums", "eps", "singles"):
        for entry in data["artist"][group]:
            assert entry["image_url"] == f"/api/library/v2/artwork/album/{entry['id']}"


def test_acquisition_request_resolves_server_owned_profiles_and_is_idempotent(api):
    client, _db, ids = api
    payload = {
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "manual:views:1",
        "quality_profile_id": 999,
    }

    first = client.post(
        "/api/library/v2/acquisition/requests", json=payload)
    second = client.post(
        "/api/library/v2/acquisition/requests", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    first_data = first.get_json()
    second_data = second.get_json()
    assert first_data["request"]["id"] == second_data["request"]["id"]
    assert first_data["request"]["profile_id"] == 1
    assert first_data["request"]["quality_profile_id"] == 1
    assert first_data["request"]["status"] == "searching"
    assert second_data["created"] is False


def test_non_admin_cannot_create_acquisition_request(api):
    client, db, ids = api
    db.active_profile = 2

    response = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "forbidden",
    })

    assert response.status_code == 403


def test_acquisition_evaluation_returns_only_public_candidates_and_reasons(api):
    client, db, ids = api
    created = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "evaluate-views",
    }).get_json()
    request_id = created["request"]["id"]
    conn = db._get_connection()
    try:
        from core.acquisition.candidates import register_candidate
        good, _ = register_candidate(
            conn,
            request_id=request_id,
            source="usenet",
            protocol="usenet",
            content_scope="release_bundle",
            server_ref="ssc1-secret-good",
            title="Drake - Views",
            guid="good",
            facts={
                "artist": "Drake", "release_title": "Views",
                "format": "flac", "bit_depth": 24,
                "sample_rate": 96000, "track_count": 1,
            },
        )
        bad, _ = register_candidate(
            conn,
            request_id=request_id,
            source="usenet",
            protocol="usenet",
            content_scope="release_bundle",
            server_ref="ssc1-secret-bad",
            title="Other - Views",
            guid="bad",
            facts={"artist": "Other", "release_title": "Views", "format": "flac"},
        )
        conn.commit()
    finally:
        conn.close()

    evaluated = client.post(
        f"/api/library/v2/acquisition/requests/{request_id}/evaluate",
        json={"automatic": False},
    )

    assert evaluated.status_code == 200
    data = evaluated.get_json()
    assert {item["id"] for item in data["candidates"]} == {good.id, bad.id}
    rejected = next(item for item in data["candidates"] if item["id"] == bad.id)
    assert rejected["decision"]["accepted"] is False
    assert "artist_mismatch" in {
        reason["code"] for reason in rejected["decision"]["rejections"]}
    assert "server_ref" not in str(data)
    assert "ssc1-secret" not in str(data)

    listed = client.get(
        f"/api/library/v2/acquisition/requests/{request_id}/candidates"
    ).get_json()
    assert "server_ref" not in str(listed)


def test_wanted_materialize_endpoint_is_shadow_only_and_idempotent(api):
    client, db, ids = api
    conn = db._get_connection()
    try:
        from core.library2.wanted import recompute_wanted
        conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (ids["album_track"],))
        conn.execute(
            """INSERT INTO lib2_monitor_rules(
                   entity_type, entity_id, profile_id, monitored, provenance)
               VALUES('track', ?, 1, 1, 'user_explicit')
               ON CONFLICT(entity_type, entity_id, profile_id) DO UPDATE SET
                   monitored=1, provenance='user_explicit'""",
            (ids["album_track"],),
        )
        recompute_wanted(conn, profile_id=1, track_ids=[ids["album_track"]])
        # Fixture rows were inserted after schema ensure; backfill their shadow
        # recording/edition rows now, as production importer does.
        from core.library2.editions import backfill_editions
        backfill_editions(conn.cursor())
        conn.commit()
    finally:
        conn.close()

    first = client.post(
        "/api/library/v2/acquisition/wanted/materialize",
        json={"track_ids": [ids["album_track"]]},
    ).get_json()
    second = client.post(
        "/api/library/v2/acquisition/wanted/materialize",
        json={"track_ids": [ids["album_track"]]},
    ).get_json()

    assert first["success"] is True and first["shadow"] is True
    assert len(first["requests"]) == 1
    assert first["requests"][0]["created"] is True
    assert second["requests"][0]["created"] is False
    assert first["requests"][0]["request"]["id"] == second["requests"][0]["request"]["id"]


def test_monitor_album_mirrors_with_active_profile(api):
    client, db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": True}).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_albums WHERE id=?",
                            (ids["ep"],)).fetchone()[0] == 1
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["ep_track"],)).fetchone()[0] == 1
    # The wishlist mirror carries the admin profile (the only profile that
    # may write to Library v2 per ADR-01) and the track's quality profile.
    assert db.wishlist_adds, "monitoring a fileless track must queue it"
    assert all(a["profile_id"] == 1 for a in db.wishlist_adds)
    assert all(a["quality_profile_id"] == 1 for a in db.wishlist_adds)


def test_track_toggle_is_user_initiated_album_toggle_is_not(api):
    """Audit P1-11: only the DIRECT track-level toggle may clear a user's
    wishlist-ignore (user_initiated=True). An album toggle is a cascade over
    tracks the user may have deliberately cancelled — it must respect the
    ignore-list, as must scheduled jobs and profile assignments."""
    client, db, ids = api
    resp = client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                       json={"monitored": True}).get_json()
    assert resp["success"] is True
    assert db.wishlist_adds and all(a["user_initiated"] for a in db.wishlist_adds)

    db.wishlist_adds.clear()
    resp = client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": True}).get_json()
    assert resp["success"] is True
    assert db.wishlist_adds and not any(a["user_initiated"] for a in db.wishlist_adds)

    db.wishlist_adds.clear()
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2, "monitor_existing": True},
    ).get_json()
    assert resp["success"] is True
    assert db.wishlist_adds and not any(a["user_initiated"] for a in db.wishlist_adds)


def test_album_unmonitor_preserves_explicit_track_intent(api):
    """Audit P1-14: an album toggle is a cascade — it must not destroy a
    deliberate per-track choice. A track the user explicitly monitored stays
    monitored (and is NOT withdrawn from the wishlist) when its album is
    unmonitored; rule-less siblings follow the cascade."""
    client, db, ids = api
    # Direct user action on the track: explicit intent.
    assert client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                       json={"monitored": True}).get_json()["success"] is True
    # Album ON then OFF — the cascade projects the sibling-less album; the
    # explicit track must survive the OFF.
    assert client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": True}).get_json()["success"] is True
    db.wishlist_removes.clear()
    resp = client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": False}).get_json()
    assert resp["success"] is True
    assert resp["preserved_tracks"] == 1
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_albums WHERE id=?",
                            (ids["ep"],)).fetchone()[0] == 0
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["ep_track"],)).fetchone()[0] == 1
    assert all(r["id"] != "sp-t2" for r in db.wishlist_removes), (
        "the explicitly monitored track must not be withdrawn from the wishlist")


def test_album_cascade_still_projects_ruleless_tracks(api):
    """Without explicit per-track intent the album toggle behaves exactly as
    before: every child follows the cascade."""
    client, db, ids = api
    assert client.post(f"/api/library/v2/albums/{ids['views']}/monitor",
                       json={"monitored": True}).get_json()["preserved_tracks"] == 0
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["album_track"],)).fetchone()[0] == 1
    resp = client.post(f"/api/library/v2/albums/{ids['views']}/monitor",
                       json={"monitored": False}).get_json()
    assert resp["preserved_tracks"] == 0
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["album_track"],)).fetchone()[0] == 0


def test_monitor_actions_record_provenance(api):
    client, db, ids = api
    client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                json={"monitored": True})
    client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                json={"monitored": True})
    client.post(f"/api/library/v2/artists/{ids['artist']}/monitor",
                json={"monitored": True})
    with _conn(db) as conn:
        rows = {(r["entity_type"], r["entity_id"]): r["provenance"]
                for r in conn.execute(
                    "SELECT entity_type, entity_id, provenance FROM lib2_monitor_rules")}
    assert rows[("track", ids["ep_track"])] == "user_explicit"
    assert rows[("album", ids["ep"])] == "user_explicit"
    assert rows[("artist", ids["artist"])] == "user_explicit"


def test_profile_assign_respects_explicit_track_unmonitor(api):
    """The monitor_existing opt-in is a bulk cascade — it must not overturn a
    track the user explicitly unmonitored."""
    client, db, ids = api
    # Explicit user decision: this track stays off.
    assert client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                       json={"monitored": False}).get_json()["success"] is True
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2, "monitor_existing": True},
    ).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["ep_track"],)).fetchone()[0] == 0
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["album_track"],)).fetchone()[0] == 1


def test_profile_assign_does_not_touch_monitoring_by_default(api):
    """Audit P1-15: assigning a quality profile is a quality decision, not a
    wanted-action. Without the explicit opt-in it must neither flip monitored
    flags nor queue wishlist adds — a deliberately unmonitored track must not
    get re-downloaded because the user changed a profile."""
    client, db, ids = api
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2},  # upgrade policy, but no opt-in
    ).get_json()
    assert resp["success"] is True
    assert resp["auto_monitored"] == 0 and resp["mirrored"] == 0
    with _conn(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_tracks WHERE monitored=1").fetchone()[0] == 0
    assert db.wishlist_adds == []


def test_profile_assign_skips_consolidated_duplicates(api):
    """With the explicit monitor-existing opt-in, an upgrade-policy profile
    monitors the artist's tracks — but not a consolidated-away duplicate (no
    file, canonical partner owns the file)."""
    client, db, ids = api
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2, "monitor_existing": True},  # seeded 'until_cutoff' profile
    ).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        monitored = {r["id"]: r["monitored"] for r in conn.execute(
            "SELECT id, monitored FROM lib2_tracks")}
    assert monitored[ids["album_track"]] == 1
    assert monitored[ids["ep_track"]] == 1
    assert monitored[ids["single_track"]] == 0, (
        "the consolidated single variant must not be re-wanted")
    queued = {a["id"] for a in db.wishlist_adds}
    from core.library2.stable_ids import ensure_track_stable_id
    with _conn(db) as conn:
        single_stable = ensure_track_stable_id(conn, ids["single_track"])
    assert f"lib2-track:{single_stable}" not in queued


def test_delete_artist_removes_rows_mirrors_and_artwork(api):
    client, db, ids = api
    # Cached artwork that must disappear with the entity.
    from core.library2.artwork import artwork_file, thumb_file
    art = artwork_file(db, "artist", ids["artist"])
    art.write_bytes(b"jpg")
    thumb = thumb_file(db, "album", ids["views"])
    thumb.write_bytes(b"jpg")

    resp = client.delete(f"/api/library/v2/artists/{ids['artist']}").get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks", "lib2_track_files"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    # Wishlist withdrawals went out for the artist's tracks; watchlist too.
    assert db.wishlist_removes
    assert db.watchlist_removes and db.watchlist_removes[0]["ext_id"] == "sp-drake"
    assert not art.exists()
    assert not thumb.exists()


def test_delete_featured_artist_keeps_owner_album(api):
    """Audit P0-01: deleting an artist who is merely featured on another
    artist's album must NOT delete that album — only the credit rows."""
    client, db, ids = api
    with _conn(db) as conn:
        cur = conn.execute(
            "INSERT INTO lib2_artists(name, spotify_id) VALUES('Wizkid','sp-wizkid')")
        wizkid = cur.lastrowid
        conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'featured')",
            (ids["views"], wizkid))
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, role) VALUES(?,?,'featured')",
            (ids["album_track"], wizkid))
        conn.commit()

    # Preview shows the real blast radius: nothing owned, one detachment.
    preview = client.get(f"/api/library/v2/artists/{wizkid}/delete-preview").get_json()
    assert preview["success"] is True
    assert preview["albums"] == 0 and preview["tracks"] == 0
    assert preview["detached_albums"] == 1

    resp = client.delete(f"/api/library/v2/artists/{wizkid}").get_json()
    assert resp["success"] is True
    assert resp["albums"] == 0 and resp["detached_albums"] == 1

    with _conn(db) as conn:
        # Drake's album, tracks and file links all survive.
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id=?",
                            (ids["views"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?",
                            (ids["views"],)).fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?",
                            (ids["album_track"],)).fetchone()[0] == 1
        # Only the credit rows are gone.
        assert conn.execute("SELECT COUNT(*) FROM lib2_album_artists WHERE artist_id=?",
                            (wizkid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_track_artists WHERE artist_id=?",
                            (wizkid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_artists WHERE id=?",
                            (wizkid,)).fetchone()[0] == 0
    # No wishlist withdrawals for the surviving album's tracks.
    assert not db.wishlist_removes


def test_artist_delete_preview_for_primary_artist(api):
    client, _db, ids = api
    preview = client.get(f"/api/library/v2/artists/{ids['artist']}/delete-preview").get_json()
    assert preview["success"] is True
    assert preview["albums"] == 3 and preview["tracks"] == 3
    assert preview["file_links"] == 1 and preview["detached_albums"] == 0
    missing = client.get("/api/library/v2/artists/999999/delete-preview")
    assert missing.status_code == 404


def test_non_admin_profile_writes_are_rejected(api):
    """ADR-01 (admin-only, technically enforced): lib2 mutations from any
    profile but the admin are rejected with 403 — not silently applied to the
    global monitored columns and mirrored into the wrong profile's wishlist
    (audit P0-02). Reads stay available to every profile."""
    client, db, ids = api
    db.active_profile = 7  # non-admin household profile

    for method, url, body in (
        ("post", f"/api/library/v2/albums/{ids['ep']}/monitor", {"monitored": True}),
        ("post", f"/api/library/v2/artists/{ids['artist']}/quality-profile",
         {"quality_profile_id": 2}),
        ("post", f"/api/library/v2/albums/{ids['single']}/edit", {"album_type": "ep"}),
        ("delete", f"/api/library/v2/artists/{ids['artist']}", None),
        ("post", "/api/library/v2/import", {}),
    ):
        resp = getattr(client, method)(url, json=body) if body is not None else \
            getattr(client, method)(url)
        assert resp.status_code == 403, f"{method.upper()} {url} must be admin-only"
        assert "admin" in (resp.get_json() or {}).get("error", "").lower()

    with _conn(db) as conn:
        # Nothing changed, nothing mirrored.
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_tracks WHERE monitored=1").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_artists").fetchone()[0] == 1
    assert db.wishlist_adds == [] and db.wishlist_removes == []

    # Reads still work for the non-admin profile.
    resp = client.get(f"/api/library/v2/artists/{ids['artist']}")
    assert resp.status_code == 200 and resp.get_json()["success"] is True


def test_import_is_hard_limited_to_admin_profile(legacy_db=None):
    """ADR-01: the importer derives GLOBAL monitored flags from one profile's
    watchlist/wishlist — any profile but the admin must be refused."""
    import pytest as _pytest
    from core.library2.importer import import_legacy_library

    with _pytest.raises(ValueError, match="admin-only"):
        import_legacy_library(None, profile_id=7)


def test_artist_list_rejects_non_numeric_page(api):
    client, _db, _ids = api
    resp = client.get("/api/library/v2/artists?page=abc")
    assert resp.status_code == 400


def test_album_edit_refiles_release_type(api):
    client, db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['single']}/edit",
                       json={"album_type": "ep"}).get_json()
    assert resp["success"] is True and resp["album_type"] == "ep"
    with _conn(db) as conn:
        assert conn.execute("SELECT album_type FROM lib2_albums WHERE id=?",
                            (ids["single"],)).fetchone()[0] == "ep"
    bad = client.post(f"/api/library/v2/albums/{ids['single']}/edit",
                      json={"album_type": "mixtape"})
    assert bad.status_code == 400


def test_refresh_unknown_entity_is_404(api):
    """An unknown id must be a 404 — not a success whose empty album scope
    silently widens into a full-library rescan (audit P1-08)."""
    client, _db, _ids = api
    resp = client.post("/api/library/v2/artists/999999/refresh")
    assert resp.status_code == 404
    resp = client.post("/api/library/v2/albums/999999/refresh")
    assert resp.status_code == 404


def test_refresh_artist_without_albums_scans_nothing(api):
    client, db, _ids = api
    with _conn(db) as conn:
        cur = conn.execute("INSERT INTO lib2_artists(name) VALUES('Empty Artist')")
        empty_artist = cur.lastrowid
        conn.commit()
    resp = client.post(f"/api/library/v2/artists/{empty_artist}/refresh").get_json()
    assert resp["success"] is True
    assert resp["refreshed_albums"] == 0
    assert resp["scan"].get("scanned") == 0


def test_refresh_busts_full_artwork_and_thumbnails(api):
    """Refresh must invalidate BOTH cached variants — the thumb wins the serve
    fast path, so a stale one would pin the old cover in lists forever."""
    client, db, ids = api
    from core.library2.artwork import artwork_file, thumb_file
    files = [
        artwork_file(db, "artist", ids["artist"]),
        thumb_file(db, "artist", ids["artist"]),
        artwork_file(db, "album", ids["views"]),
        thumb_file(db, "album", ids["views"]),
    ]
    for f in files:
        f.write_bytes(b"jpg")
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/refresh").get_json()
    assert resp["success"] is True
    for f in files:
        assert not f.exists(), f"{f.name} must be invalidated by refresh"
