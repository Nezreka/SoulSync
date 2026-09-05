"""Tracklist completeness helpers for Library v2."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

from core.library2 import completeness
from core.library2.completeness import (
    _persist_tracklist_tracks,
    precache_tracklists,
    resolve_tracklist,
)
from core.library2.provider_adapters import TRACKLIST_PARSER_VERSION


def test_persist_tracklist_tracks_creates_monitorable_missing_rows(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET monitored=1, quality_profile_id=2 WHERE id=?",
        (views_id,),
    )
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    record_rule(imported_conn, "album", views_id, True, PROVENANCE_USER)

    created = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "One Dance"},
        {"track_number": 3, "title": "Missing Provider Title", "spotify_id": "sp-missing"},
    ])

    assert created == 1
    row = imported_conn.execute(
        "SELECT id, monitored, quality_profile_id, spotify_id FROM lib2_tracks "
        "WHERE album_id=? AND track_number=3",
        (views_id,),
    ).fetchone()
    assert row["monitored"] == 1
    assert row["quality_profile_id"] == 2
    assert row["spotify_id"] == "sp-missing"
    projected = imported_conn.execute(
        "SELECT wanted, projection_version FROM lib2_wanted_tracks WHERE track_id=?",
        (row["id"],),
    ).fetchone()
    assert projected is not None and bool(projected["wanted"]) is True

    linked_artist = imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_track_artists WHERE track_id=?",
        (row["id"],),
    ).fetchone()[0]
    assert linked_artist == 1

    assert _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 3, "title": "Missing Provider Title", "spotify_id": "sp-missing"},
    ]) == 0


def test_persist_tracklist_tracks_keeps_non_spotify_provider_ids(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]

    created = _persist_tracklist_tracks(imported_conn, views_id, [{
        "track_number": 9,
        "title": "Deezer Provider Track",
        "external_ids": {"deezer": "dz-track-9", "itunes": "it-track-9"},
    }])

    assert created == 1
    row = imported_conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_tracks "
        "WHERE album_id=? AND track_number=9",
        (views_id,),
    ).fetchone()
    assert row["spotify_id"] is None
    assert json.loads(row["external_ids"]) == {
        "deezer": "dz-track-9", "itunes": "it-track-9",
    }


def test_persist_tracklist_tracks_heals_and_keeps_multi_artist_credits(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]

    # Parser v2 created provider-only rows with the album artist fallback.
    _persist_tracklist_tracks(imported_conn, views_id, [{
        "track_number": 9,
        "title": "Provider Collab",
    }])
    # Parser v3 receives the real ordered track credit and must replace that
    # fallback because this fileless row has no local tag credit to preserve.
    _persist_tracklist_tracks(imported_conn, views_id, [{
        "track_number": 9,
        "title": "Provider Collab",
        "artists": ["Guest Artist", "Drake"],
    }])

    rows = imported_conn.execute(
        """SELECT ar.name, ta.role, ta.position
             FROM lib2_tracks t
             JOIN lib2_track_artists ta ON ta.track_id=t.id
             JOIN lib2_artists ar ON ar.id=ta.artist_id
            WHERE t.album_id=? AND t.track_number=9
            ORDER BY ta.position""",
        (views_id,),
    ).fetchall()
    assert [(row["name"], row["role"], row["position"]) for row in rows] == [
        ("Guest Artist", "primary", 0),
        ("Drake", "featured", 1),
    ]
    assert imported_conn.execute(
        """SELECT 1 FROM lib2_album_artists aa
             JOIN lib2_artists ar ON ar.id=aa.artist_id
            WHERE aa.album_id=? AND ar.name='Guest Artist'""",
        (views_id,),
    ).fetchone() is not None


def test_precache_materializes_cached_tracklists_before_provider_lookup(legacy_db):
    from core.library2.importer import import_legacy_library

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    views_id = conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    conn.execute(
        "UPDATE lib2_albums SET expected_track_count=3, monitored=1, quality_profile_id=2, "
        "tracklist_json=? WHERE id=?",
        (
            json.dumps([
                {"track_number": 1, "title": "One Dance"},
                {"track_number": 2, "title": "Hotline Bling"},
                {"track_number": 3, "title": "Provider Only", "spotify_id": "sp-provider-only"},
            ]),
            views_id,
        ),
    )
    conn.commit()
    conn.close()

    assert precache_tracklists(legacy_db, config_manager=None) >= 1

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT title, monitored, quality_profile_id, spotify_id FROM lib2_tracks "
            "WHERE album_id=? AND track_number=3",
            (views_id,),
        ).fetchone()
        assert dict(row) == {
            "title": "Provider Only",
            "monitored": 1,
            "quality_profile_id": 2,
            "spotify_id": "sp-provider-only",
        }
        snapshot = conn.execute(
            """SELECT provider, is_complete, parser_version, payload_json
                 FROM library_provider_snapshots
                WHERE entity_type='album' AND entity_id=? AND scope='tracklist'""",
            (views_id,),
        ).fetchone()
        assert snapshot["provider"] == "legacy-cache"
        assert snapshot["is_complete"] == 1
        assert snapshot["parser_version"] == TRACKLIST_PARSER_VERSION
        assert json.loads(snapshot["payload_json"])["reference"][
            "release_edition_id"] is not None
    finally:
        conn.close()


def test_resolve_tracklist_snapshots_spotify_and_reuses_durable_cache(
        imported_conn, monkeypatch):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    edition_id = imported_conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (views_id,),
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-edition-1' WHERE id=?",
        (edition_id,),
    )
    imported_conn.execute(
        "UPDATE lib2_albums SET tracklist_json=NULL WHERE id=?", (views_id,))

    class Spotify:
        calls = []

        def is_spotify_authenticated(self):
            return True

        def get_album_tracks(self, album_id):
            self.calls.append(album_id)
            return {"items": [
                {"id": "sp-t1", "name": "One Dance", "track_number": 1,
                 "disc_number": 1, "duration_ms": 180000},
                {"id": "sp-t2", "name": "Hotline Bling", "track_number": 2,
                 "disc_number": 1, "duration_ms": 200000},
            ]}

    spotify = Spotify()
    monkeypatch.setattr(
        "core.metadata.registry.get_client_for_source",
        lambda source: spotify if source == "spotify" else None,
    )

    first = resolve_tracklist(None, imported_conn, views_id)
    second = resolve_tracklist(None, imported_conn, views_id)

    assert first == second
    assert spotify.calls == ["sp-edition-1"]
    snapshot = imported_conn.execute(
        """SELECT provider, provider_entity_id, is_complete, parser_version,
                  payload_json
             FROM library_provider_snapshots
            WHERE entity_type='album' AND entity_id=? AND scope='tracklist'""",
        (views_id,),
    ).fetchone()
    payload = json.loads(snapshot["payload_json"])
    assert snapshot["provider"] == "spotify"
    assert snapshot["provider_entity_id"] == "sp-edition-1"
    assert snapshot["is_complete"] == 1
    assert snapshot["parser_version"] == TRACKLIST_PARSER_VERSION
    assert payload["reference"]["release_edition_id"] == edition_id
    assert payload["tracks"][0]["spotify_id"] == "sp-t1"


def test_resolve_tracklist_uses_effective_default_edition_facts(
    imported_conn, monkeypatch
):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    edition_id = imported_conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (views_id,),
    ).fetchone()[0]
    imported_conn.execute(
        """UPDATE lib2_albums
              SET release_date='2016-04-29', expected_track_count=2,
                  external_ids='{"upc":"group-upc"}', tracklist_json=NULL
            WHERE id=?""",
        (views_id,),
    )
    imported_conn.execute(
        """UPDATE lib2_release_editions
              SET release_date='2017-01-01', track_count=20,
                  external_ids='{"upc":"edition-upc"}'
            WHERE id=?""",
        (edition_id,),
    )
    captured = {}

    def fake_fetch(album_title, artist_name, **kwargs):
        captured.update(
            album_title=album_title,
            artist_name=artist_name,
            **kwargs,
        )
        return None

    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist", fake_fetch
    )

    assert resolve_tracklist(None, imported_conn, views_id) is None
    assert captured["release_date"] == "2017-01-01"
    assert captured["expected_track_count"] == 20
    assert captured["source_album_ids"]["upc"] == "edition-upc"


def test_default_edition_change_invalidates_tracklist_cache(
        imported_conn, monkeypatch):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    edition_id = imported_conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (views_id,),
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-standard' WHERE id=?",
        (edition_id,),
    )
    imported_conn.execute(
        "UPDATE lib2_albums SET tracklist_json=NULL WHERE id=?", (views_id,))

    class Spotify:
        calls = []

        def is_spotify_authenticated(self):
            return True

        def get_album_tracks(self, album_id):
            self.calls.append(album_id)
            return {"items": [{
                "id": f"{album_id}-track",
                "name": f"Track from {album_id}",
                "track_number": 1,
            }]}

    spotify = Spotify()
    monkeypatch.setattr(
        "core.metadata.registry.get_client_for_source",
        lambda source: spotify if source == "spotify" else None,
    )

    first = resolve_tracklist(None, imported_conn, views_id)
    imported_conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-deluxe' WHERE id=?",
        (edition_id,),
    )
    second = resolve_tracklist(None, imported_conn, views_id)

    assert first[0]["title"] == "Track from sp-standard"
    assert second[0]["title"] == "Track from sp-deluxe"
    assert spotify.calls == ["sp-standard", "sp-deluxe"]
    latest = imported_conn.execute(
        """SELECT provider_entity_id, payload_json
             FROM library_provider_snapshots
            WHERE entity_type='album' AND entity_id=? AND scope='tracklist'
            ORDER BY fetched_at DESC, id DESC LIMIT 1""",
        (views_id,),
    ).fetchone()
    assert latest["provider_entity_id"] == "sp-deluxe"
    assert json.loads(latest["payload_json"])["reference"]["spotify_id"] == "sp-deluxe"


def test_edition_change_persists_invalidation_when_provider_is_unavailable(
        legacy_db, monkeypatch):
    from core.library2.importer import import_legacy_library

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    views_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    edition_id = conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (views_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-old' WHERE id=?",
        (edition_id,),
    )
    conn.execute(
        "UPDATE lib2_albums SET tracklist_json=? WHERE id=?",
        (json.dumps([{"track_number": 1, "title": "Old Edition"}]), views_id),
    )
    conn.commit()
    assert resolve_tracklist(None, conn, views_id)[0]["title"] == "Old Edition"

    conn.execute(
        "UPDATE lib2_release_editions SET spotify_id='sp-new' WHERE id=?",
        (edition_id,),
    )
    conn.commit()
    monkeypatch.setattr("core.metadata.registry.get_spotify_client", lambda: None)
    monkeypatch.setattr("core.metadata.registry.get_deezer_client", lambda: None)
    assert resolve_tracklist(None, conn, views_id) is None
    conn.close()

    reopened = legacy_db._get_connection()
    try:
        row = reopened.execute(
            "SELECT tracklist_json, tracklist_status FROM lib2_albums WHERE id=?",
            (views_id,),
        ).fetchone()
        assert row["tracklist_json"] is None
        assert row["tracklist_status"] == "idle"
    finally:
        reopened.close()


def test_persist_tracklist_tracks_infers_discs_when_numbers_reset(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET expected_track_count=4, monitored=1 WHERE id=?",
        (views_id,),
    )

    created = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "Disc 1 A"},
        {"track_number": 2, "title": "Disc 1 B"},
        {"track_number": 1, "title": "Disc 2 A"},
        {"track_number": 2, "title": "Disc 2 B"},
    ])

    assert created == 2
    rows = imported_conn.execute(
        "SELECT title, track_number, disc_number FROM lib2_tracks "
        "WHERE album_id=? ORDER BY disc_number, track_number",
        (views_id,),
    ).fetchall()
    assert [dict(r) for r in rows][-2:] == [
        {"title": "Disc 2 A", "track_number": 1, "disc_number": 2},
        {"title": "Disc 2 B", "track_number": 2, "disc_number": 2},
    ]


def test_persist_tracklist_heals_duplicated_track_numbers_by_title(imported_conn):
    """§16.3: when local track numbers got corrupted (a whole album collapsed
    onto number 1), a correctly-fetched provider tracklist must HEAL the numbers
    by matching on TITLE — updating the existing rows in place — not insert
    duplicate rows keyed on the corrupt number. The old (disc, number)-only match
    is exactly why "Update Discography" never repaired the collapse: the match
    key WAS the corrupt field, so it could only re-confirm it.
    """
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, expected_track_count) "
        "VALUES(?, 'swag', 3)",
        (artist_id,),
    ).lastrowid
    # All three local tracks collapsed onto track_number=1 (the corruption).
    for title in ("Alpha", "Bravo", "Charlie"):
        imported_conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
            "VALUES(?,?,1,1,1)",
            (album_id, title),
        )
    original_ids = {
        r["title"]: r["id"] for r in imported_conn.execute(
            "SELECT id, title FROM lib2_tracks WHERE album_id=?", (album_id,))
    }

    created = _persist_tracklist_tracks(imported_conn, album_id, [
        {"track_number": 1, "title": "Alpha"},
        {"track_number": 2, "title": "Bravo"},
        {"track_number": 3, "title": "Charlie"},
    ])

    # Healed in place: no new rows, same ids, corrected numbers.
    assert created == 0
    healed = {
        r["title"]: (r["id"], r["track_number"]) for r in imported_conn.execute(
            "SELECT id, title, track_number FROM lib2_tracks WHERE album_id=?",
            (album_id,))
    }
    assert healed == {
        "Alpha": (original_ids["Alpha"], 1),
        "Bravo": (original_ids["Bravo"], 2),
        "Charlie": (original_ids["Charlie"], 3),
    }


def test_persist_tracklist_heals_real_track_over_its_own_placeholder_duplicate(
        imported_conn):
    """§17.2 (SWAG case): a REAL, downloaded track collapsed onto the wrong
    number can ALSO have a fileless placeholder row already sitting at its
    correct number with the identical title (created by an earlier resolve
    before the file existed). ``_unique_untouched_title_match`` alone sees two
    untouched same-title rows and refuses to heal (ambiguous) — the real row
    stays corrupted and the redundant placeholder survives, i.e. exactly the
    "DAISIES at number 1 AND number 2" duplication the user reported. When one
    of the ambiguous candidates has a file and the rest are safe-to-drop
    placeholders (no file, not monitored, no positive rule, not wanted), the
    real row must be healed to the correct number and the placeholder dropped.
    """
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, expected_track_count) "
        "VALUES(?, 'swag', 'library', 3)",
        (artist_id,),
    ).lastrowid
    for title in ("Alpha", "Bravo", "Charlie"):
        imported_conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
            "VALUES(?,?,1,1,1)",
            (album_id, title),
        )
    real_ids = {
        r["title"]: r["id"] for r in imported_conn.execute(
            "SELECT id, title FROM lib2_tracks WHERE album_id=?", (album_id,))
    }
    for title, tid in real_ids.items():
        imported_conn.execute(
            "INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
            (tid, f"/m/{title.lower()}.flac"),
        )
    # A stale placeholder for "Bravo" already sits at its correct number 2 —
    # created by an earlier resolve, before the real file existed.
    bravo_placeholder_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?, 'Bravo', 2, 1, 0)",
        (album_id,),
    ).lastrowid

    created = _persist_tracklist_tracks(imported_conn, album_id, [
        {"track_number": 1, "title": "Alpha"},
        {"track_number": 2, "title": "Bravo"},
        {"track_number": 3, "title": "Charlie"},
    ])

    assert created == 0
    healed = {
        r["title"]: r["track_number"] for r in imported_conn.execute(
            "SELECT title, track_number FROM lib2_tracks WHERE album_id=?",
            (album_id,))
    }
    # The real "Bravo" track (has a file) is healed to number 2, in place.
    assert healed == {"Alpha": 1, "Bravo": 2, "Charlie": 3}
    assert imported_conn.execute(
        "SELECT track_number FROM lib2_tracks WHERE id=?", (real_ids["Bravo"],)
    ).fetchone()["track_number"] == 2
    # The redundant fileless placeholder is gone, not left as a duplicate.
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE id=?", (bravo_placeholder_id,)
    ).fetchone()[0] == 0
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchone()[0] == 3


def test_persist_tracklist_tracks_trims_surplus_fileless_rows(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET expected_track_count=2 WHERE id=?",
        (views_id,),
    )
    imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?, 'stale provider row', 3, 1, 0)",
        (views_id,),
    )
    stale_id = imported_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "SELECT ?, primary_artist_id, 'primary', 0 FROM lib2_albums WHERE id=?",
        (stale_id, views_id),
    )

    changed = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "One Dance"},
        {"track_number": 2, "title": "Hotline Bling"},
    ])

    assert changed == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE id=?",
        (stale_id,),
    ).fetchone()[0] == 0


def test_persist_tracklist_does_not_truncate_provider_rows_to_stale_expected_count(
        imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET expected_track_count=2 WHERE id=?", (views_id,)
    )

    changed = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "One Dance"},
        {"track_number": 2, "title": "Hotline Bling"},
        {"track_number": 3, "title": "Provider Confirms Third", "spotify_id": "sp-third"},
    ])

    assert changed == 1
    assert imported_conn.execute(
        "SELECT title FROM lib2_tracks WHERE album_id=? AND track_number=3",
        (views_id,),
    ).fetchone()[0] == "Provider Confirms Third"
    assert imported_conn.execute(
        "SELECT expected_track_count FROM lib2_albums WHERE id=?", (views_id,)
    ).fetchone()[0] == 3


def test_tracklist_trim_preserves_positive_monitor_intent_despite_flag_drift(
        imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET expected_track_count=2 WHERE id=?", (views_id,)
    )
    protected_id = imported_conn.execute(
        """INSERT INTO lib2_tracks(
               album_id, title, track_number, disc_number, monitored)
           VALUES(?, 'Explicitly Wanted Extra', 3, 1, 0)""",
        (views_id,),
    ).lastrowid
    from core.library2.monitor_rules import PROVENANCE_WISHLIST, record_rule
    record_rule(
        imported_conn,
        "track",
        protected_id,
        True,
        PROVENANCE_WISHLIST,
    )

    changed = _persist_tracklist_tracks(imported_conn, views_id, [
        {"track_number": 1, "title": "One Dance"},
        {"track_number": 2, "title": "Hotline Bling"},
    ])

    assert changed == 0
    assert imported_conn.execute(
        "SELECT title FROM lib2_tracks WHERE id=?", (protected_id,)
    ).fetchone()[0] == "Explicitly Wanted Extra"
    assert imported_conn.execute(
        "SELECT expected_track_count FROM lib2_albums WHERE id=?", (views_id,)
    ).fetchone()[0] == 3


# ---------------------------------------------------------------------------
# docs/library-v2.md §17.6 — precache_tracklists must resolve partial albums
# through a bounded ThreadPoolExecutor instead of one album at a time. Each
# uncached album triggers a synchronous provider network call
# (fetch_album_tracklist), so a serial loop is a real bottleneck for a
# first-time migration of thousands of albums. Same config-key contract as
# core.auto_import_worker (default 3, auto_import.max_workers).
# ---------------------------------------------------------------------------


def _mark_all_albums_partial(conn, expected_track_count: int = 2) -> list:
    album_ids = [r[0] for r in conn.execute("SELECT id FROM lib2_albums").fetchall()]
    for album_id in album_ids:
        conn.execute(
            "UPDATE lib2_albums SET expected_track_count=?, tracklist_json=NULL WHERE id=?",
            (expected_track_count, album_id),
        )
    conn.commit()
    return album_ids


_PARTIAL_ALBUMS = 6


def test_precache_tracklists_runs_resolves_concurrently(legacy_db_factory):
    """Peak in-flight resolves must reach the pool size (or the number of
    albums, whichever is smaller), proving real concurrency rather than a
    one-at-a-time loop."""
    from core.library2.importer import import_legacy_library

    legacy_db = legacy_db_factory(n_albums=_PARTIAL_ALBUMS)
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    _mark_all_albums_partial(conn)
    conn.close()

    in_flight = [0]
    peak = [0]
    lock = threading.Lock()
    proceed = threading.Event()

    def slow_resolve(_config_manager, _conn, _album_id):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        proceed.wait(timeout=2)
        with lock:
            in_flight[0] -= 1
        return None

    orig = completeness.resolve_tracklist
    completeness.resolve_tracklist = slow_resolve
    try:
        result = {}

        def run():
            result["resolved"] = precache_tracklists(legacy_db, None)

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.3)
        from core.library2.completeness import _precache_max_workers
        # Capped by the fixture: concurrency cannot exceed the work available.
        expected = min(_precache_max_workers(None), _PARTIAL_ALBUMS)
        assert peak[0] == expected, (
            f"Expected {expected} concurrent tracklist resolves (default "
            f"precache pool over {_PARTIAL_ALBUMS} albums), peaked at "
            f"{peak[0]} — precache_tracklists looks serial."
        )
        proceed.set()
        t.join(timeout=5)
    finally:
        completeness.resolve_tracklist = orig


def test_precache_tracklists_max_workers_caps_concurrency(legacy_db_factory, monkeypatch):
    """`library_v2.precache_workers=2` must cap concurrent resolves at 2, even
    with more than 2 pending partial albums.

    The knob used to be `auto_import.max_workers` -- the one that also governs
    how many songs download at once. Sharing it is why the default sat at 3 and
    nobody raised it: three workers x one provider call per uncached album is
    over an hour on a migrated library, and raising it meant also saturating
    the downloader (PERF-12). The download knob is still honoured, but only
    upward: it can raise this pool, never throttle it below its own default.
    """
    from core.library2.importer import import_legacy_library

    legacy_db = legacy_db_factory(n_albums=6)
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    _mark_all_albums_partial(conn)
    conn.close()

    in_flight = [0]
    peak = [0]
    lock = threading.Lock()
    proceed = threading.Event()

    def slow_resolve(_config_manager, _conn, _album_id):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        proceed.wait(timeout=2)
        with lock:
            in_flight[0] -= 1
        return None

    monkeypatch.setattr(completeness, "resolve_tracklist", slow_resolve)

    config = MagicMock()
    config.get = MagicMock(
        side_effect=lambda key, default: 2 if key == "library_v2.precache_workers" else default
    )

    result = {}

    def run():
        result["resolved"] = precache_tracklists(legacy_db, config)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    assert peak[0] == 2, (
        f"library_v2.precache_workers=2 should cap concurrency at 2, "
        f"peaked at {peak[0]}"
    )
    proceed.set()
    t.join(timeout=5)


def test_precache_tracklists_reports_one_monotonic_combined_stage(
        legacy_db_factory, monkeypatch):
    from core.library2.importer import import_legacy_library

    legacy_db = legacy_db_factory(n_albums=3)
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    album_ids = _mark_all_albums_partial(conn)
    conn.close()

    monkeypatch.setattr(
        completeness,
        "_partial_album_rows",
        lambda _conn, *, cached: [(album_ids[0],)] if cached else [
            (album_ids[1],),
            (album_ids[2],),
        ],
    )
    monkeypatch.setattr(completeness, "resolve_tracklist", lambda *_args: True)
    events = []

    precache_tracklists(
        legacy_db,
        None,
        progress=lambda stage, current, total: events.append((stage, current, total)),
    )

    assert events[0] == ("tracklists", 0, 3)
    assert events[-1] == ("tracklists", 3, 3)
    assert [current for _, current, _ in events] == sorted(
        current for _, current, _ in events
    )


def test_the_download_knob_can_raise_the_precache_pool_but_not_throttle_it():
    """A user who raised `auto_import.max_workers` meant "use my bandwidth",
    so it still applies. One who left it low did not mean "spend an hour on
    metadata"."""
    from unittest.mock import MagicMock

    from core.library2.completeness import _precache_max_workers

    default = _precache_max_workers(None)

    low = MagicMock()
    low.get = MagicMock(
        side_effect=lambda key, d=None: 2 if key == "auto_import.max_workers" else d)
    assert _precache_max_workers(low) == default

    high = MagicMock()
    high.get = MagicMock(
        side_effect=lambda key, d=None: 32 if key == "auto_import.max_workers" else d)
    assert _precache_max_workers(high) == 32

    explicit = MagicMock()
    explicit.get = MagicMock(
        side_effect=lambda key, d=None: 1 if key == "library_v2.precache_workers" else d)
    assert _precache_max_workers(explicit) == 1


def test_persist_tracklist_tracks_gives_each_credit_its_own_provider_id(imported_conn):
    """Each credited artist is created with the id the provider gave for THAT
    artist — not left id-less for the reconciler to guess from the album."""
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]

    _persist_tracklist_tracks(imported_conn, views_id, [{
        "track_number": 9,
        "title": "Provider Collab",
        "provider": "spotify",
        "artist_credits": [
            {"name": "Drake", "id": "sp-drake"},
            {"name": "Guest Artist", "id": "sp-guest"},
        ],
    }])

    guest = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE name='Guest Artist'"
    ).fetchone()
    assert guest is not None
    assert guest["spotify_id"] == "sp-guest"


def test_persist_tracklist_credits_keep_non_spotify_ids_out_of_the_spotify_column(
    imported_conn,
):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]

    _persist_tracklist_tracks(imported_conn, views_id, [{
        "track_number": 11,
        "title": "Deezer Collab",
        "provider": "deezer",
        "artist_credits": [
            {"name": "Deezer Lead", "id": "12345"},
            {"name": "Deezer Guest", "id": "67890"},
        ],
    }])

    row = imported_conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_artists WHERE name='Deezer Guest'"
    ).fetchone()
    assert row is not None
    assert row["spotify_id"] is None
    assert json.loads(row["external_ids"]) == {"deezer": "67890"}


def test_persist_tracklist_tracks_still_accepts_plain_name_credits(imported_conn):
    """Caches written by the previous parser hold names only — they must keep
    working, just without identities."""
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]

    _persist_tracklist_tracks(imported_conn, views_id, [{
        "track_number": 12,
        "title": "Legacy Cache Collab",
        "artists": ["Drake", "Old Cache Guest"],
    }])

    assert imported_conn.execute(
        "SELECT 1 FROM lib2_artists WHERE name='Old Cache Guest'"
    ).fetchone() is not None


def test_positional_fallback_never_claims_an_owned_track_with_another_title(
    imported_conn,
):
    """A downloaded file whose track number belongs to a DIFFERENT edition must
    not be overwritten by whatever the provider has at that slot.

    Production case: the only owned file of "Peace Is The Mission (Extended)"
    was ``04 - Lean On.flac``, but slot 4 of that edition is "Blaze Up the Fire
    (feat. Chronixx)". The positional fallback claimed the row with no title
    check at all, so (a) Chronixx was credited on "Lean On", (b) the real slot
    5 could no longer heal that row by title and inserted a duplicate "Lean On",
    and (c) "Blaze Up the Fire" never became a missing-track row.
    """
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, expected_track_count) "
        "VALUES(?, 'Peace Is The Mission (Extended)', 'library', 5)",
        (artist_id,),
    ).lastrowid
    owned_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?, 'Lean On', 4, 1, 1)",
        (album_id,),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/04 - Lean On.flac')",
        (owned_id,),
    )

    _persist_tracklist_tracks(imported_conn, album_id, [
        {"track_number": 4, "title": "Blaze Up the Fire (feat. Chronixx)",
         "provider": "spotify",
         "artist_credits": [{"name": "Major Lazer", "id": "sp-ml"},
                            {"name": "Chronixx", "id": "sp-chronixx"}]},
        {"track_number": 5, "title": "Lean On", "provider": "spotify",
         "artist_credits": [{"name": "Major Lazer", "id": "sp-ml"},
                            {"name": "DJ Snake", "id": "sp-djsnake"}]},
    ])

    # (a) the owned row keeps its identity and gets the credits of ITS song
    credits = [r["name"] for r in imported_conn.execute(
        """SELECT ar.name FROM lib2_track_artists ta
             JOIN lib2_artists ar ON ar.id=ta.artist_id
            WHERE ta.track_id=? ORDER BY ta.position""",
        (owned_id,))]
    assert "Chronixx" not in credits
    assert credits == ["Major Lazer", "DJ Snake"]
    # (b) it is healed to the number the provider actually gives it, in place —
    #     no second "Lean On" row
    rows = imported_conn.execute(
        "SELECT id, title, track_number FROM lib2_tracks WHERE album_id=? "
        "ORDER BY track_number", (album_id,)).fetchall()
    assert [(r["title"], r["track_number"]) for r in rows] == [
        ("Blaze Up the Fire (feat. Chronixx)", 4), ("Lean On", 5),
    ]
    assert rows[1]["id"] == owned_id
    # (c) the provider track that really sits at slot 4 exists as its own row
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=? AND title LIKE 'Blaze%'",
        (album_id,)).fetchone()[0] == 1


def _artist_names(conn):
    return {r["name"] for r in conn.execute("SELECT name FROM lib2_artists")}


def test_browse_only_discography_credits_do_not_mint_artists(imported_conn):
    """Materializing the tracklist of a release the user neither owns nor wants
    must not add its guests to the library.

    A discography sync fetches an artist's whole back catalogue; resolving
    those tracklists created a full lib2_artists row for every name credited on
    every release. On the production library that was 82 of 380 artists (22%) —
    each one listed as a library artist whose "My Library" is empty, because
    there is nothing of theirs to show.
    """
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, monitored) "
        "VALUES(?, 'Someone Elses Record', 'discography', 0)",
        (artist_id,),
    ).lastrowid
    before = _artist_names(imported_conn)

    _persist_tracklist_tracks(imported_conn, album_id, [{
        "track_number": 1, "title": "Not Mine", "provider": "spotify",
        "artist_credits": [{"name": "Browse Only Guest", "id": "sp-guest"}],
    }])

    assert _artist_names(imported_conn) == before


def test_wanted_discography_credits_still_mint_artists(imported_conn):
    """Monitoring a release IS intent to own it — its credits belong in the
    library like any owned release's."""
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, monitored) "
        "VALUES(?, 'Wanted Record', 'discography', 1)",
        (artist_id,),
    ).lastrowid

    _persist_tracklist_tracks(imported_conn, album_id, [{
        "track_number": 1, "title": "Wanted Song", "provider": "spotify",
        "artist_credits": [{"name": "Wanted Guest", "id": "sp-wanted"}],
    }])

    assert "Wanted Guest" in _artist_names(imported_conn)


def test_browse_only_credits_still_link_artists_that_already_exist(imported_conn):
    """Not minting is not the same as not crediting: a guest the library
    already knows keeps getting their appearances recorded."""
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    known_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, name_key, sort_name) VALUES('Known Guest','known guest','Known Guest')"
    ).lastrowid
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, monitored) "
        "VALUES(?, 'Browsed Record', 'discography', 0)",
        (artist_id,),
    ).lastrowid

    _persist_tracklist_tracks(imported_conn, album_id, [{
        "track_number": 1, "title": "Guest Spot", "provider": "spotify",
        "artist_credits": [{"name": "Known Guest", "id": "sp-known"}],
    }])

    assert imported_conn.execute(
        "SELECT 1 FROM lib2_album_artists WHERE album_id=? AND artist_id=?",
        (album_id, known_id),
    ).fetchone() is not None


def _capture_tracklist_call(monkeypatch):
    captured = {}

    def fake_fetch(album_title, artist_name, **kwargs):
        captured.update(album_title=album_title, artist_name=artist_name, **kwargs)
        return None

    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist", fake_fetch)
    return captured


def test_a_release_group_is_a_usable_tracklist_key(imported_conn, monkeypatch):
    """A discography row has no concrete release — the group id is all it has.

    MusicBrainz's ``get_album_tracks`` resolves a release group too, so the
    group is a legitimate lookup key here. It is deliberately not STORED under
    the `musicbrainz` key, where the tag writer and the /release/ link would
    both read it as a release."""
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    imported_conn.execute(
        """UPDATE lib2_albums
              SET musicbrainz_id=NULL, external_ids='{}', tracklist_json=NULL,
                  musicbrainz_release_group_id='rg-views'
            WHERE id=?""", (views_id,))
    imported_conn.execute(
        "UPDATE lib2_release_editions SET musicbrainz_id=NULL, external_ids='{}' "
        "WHERE release_group_id=?", (views_id,))
    captured = _capture_tracklist_call(monkeypatch)

    assert resolve_tracklist(None, imported_conn, views_id) is None
    assert captured["source_album_ids"]["musicbrainz"] == "rg-views"


def test_a_known_release_id_wins_over_the_group(imported_conn, monkeypatch):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    imported_conn.execute(
        """UPDATE lib2_albums
              SET musicbrainz_id='mb-release', tracklist_json=NULL,
                  musicbrainz_release_group_id='rg-views'
            WHERE id=?""", (views_id,))
    captured = _capture_tracklist_call(monkeypatch)

    assert resolve_tracklist(None, imported_conn, views_id) is None
    assert captured["source_album_ids"]["musicbrainz"] == "mb-release"
