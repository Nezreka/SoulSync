"""Tests for the native Library-v2 interactive reorganize bridge."""

import sys
import types
from unittest.mock import MagicMock

import pytest

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

from core.library2.reorganize_bridge import (  # noqa: E402
    ReorganizeBridgeError,
    album_reorganize_sources,
    enqueue_album_reorganize,
    enqueue_artist_reorganize_all,
    global_reorganize_sources,
    preview_album_reorganize,
    resolve_legacy_album_id,
    resolve_legacy_artist_id,
)


def _attach_reorganize_helpers(db):
    """The shared ``LegacyDBShim`` fixture only exposes ``_get_connection()``
    — attach the two real ``MusicDatabase`` methods the bridge calls,
    mirroring their production SQL exactly (``database/music_database.py``
    ``get_album_display_meta``/``get_artist_albums_for_reorganize``)."""
    import types as _types

    def get_album_display_meta(self, album_id):
        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT al.title AS album_title, ar.id AS artist_id, ar.name AS artist_name
                   FROM lib2_albums al JOIN lib2_artists ar ON al.primary_artist_id = ar.id
                   WHERE al.id=? AND EXISTS (
                     SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id
                     WHERE t.album_id=al.id AND f.file_state='active')""", (album_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def get_artist_albums_for_reorganize(self, artist_id):
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """SELECT al.id AS album_id, al.title AS album_title, ar.id AS artist_id,
                          ar.name AS artist_name FROM lib2_albums al
                   JOIN lib2_artists ar ON al.primary_artist_id = ar.id WHERE ar.id=?
                   AND EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id
                     WHERE t.album_id=al.id AND f.file_state='active')
                   ORDER BY al.year ASC, al.title ASC""", (artist_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    db.get_album_display_meta = _types.MethodType(get_album_display_meta, db)
    db.get_artist_albums_for_reorganize = _types.MethodType(get_artist_albums_for_reorganize, db)
    return db


@pytest.fixture
def imported_legacy_db(legacy_db):
    from core.library2.importer import import_legacy_library
    import_legacy_library(legacy_db)
    return _attach_reorganize_helpers(legacy_db)


@pytest.fixture
def discography_only_album(imported_legacy_db):
    """A lib2 album with NO legacy back-reference (added via Update
    Discography, never present in the legacy scan)."""
    conn = imported_legacy_db._get_connection()
    try:
        artist_id = conn.execute("SELECT id FROM lib2_artists LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT INTO lib2_albums(title, primary_artist_id, origin, legacy_album_id) "
            "VALUES ('Unowned Release', ?, 'discography', NULL)",
            (artist_id,),
        )
        conn.commit()
        album_id = conn.execute(
            "SELECT id FROM lib2_albums WHERE title='Unowned Release'"
        ).fetchone()["id"]
    finally:
        conn.close()
    return album_id


@pytest.fixture(autouse=True)
def reset_queue_singleton():
    from core.reorganize_queue import reset_queue_for_tests
    reset_queue_for_tests()
    yield
    reset_queue_for_tests()


# -- resolve_legacy_album_id / resolve_legacy_artist_id ----------------------


def test_resolve_album_id_returns_the_native_id(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()
    assert resolve_legacy_album_id(imported_legacy_db._get_connection(), lib2_album_id) == lib2_album_id


def test_resolve_album_id_accepts_catalogue_only_rows(discography_only_album, imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    assert resolve_legacy_album_id(conn, discography_only_album) == discography_only_album


def test_resolve_legacy_album_id_raises_404_for_missing_album(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        resolve_legacy_album_id(conn, 999999)
    assert exc_info.value.status == 404


def test_resolve_artist_id_returns_the_native_id(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()["id"]
    conn.close()
    assert resolve_legacy_artist_id(imported_legacy_db._get_connection(), lib2_artist_id) == lib2_artist_id


def test_resolvers_ignore_legacy_backrefs(imported_legacy_db):
    album_legacy_id = "01MoTj8w4VkVtgdPOijUUE"
    artist_legacy_id = "base62-artist-key"
    conn = imported_legacy_db._get_connection()
    album_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE legacy_album_id=10"
    ).fetchone()["id"]
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE lib2_albums SET legacy_album_id=? WHERE id=?",
        (album_legacy_id, album_id),
    )
    conn.execute(
        "UPDATE lib2_artists SET legacy_artist_id=? WHERE id=?",
        (artist_legacy_id, artist_id),
    )
    conn.commit()

    assert resolve_legacy_album_id(conn, album_id) == album_id
    assert resolve_legacy_artist_id(conn, artist_id) == artist_id
    conn.close()


def test_resolve_artist_id_accepts_native_rows_without_backref(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    conn.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES ('New Artist', NULL)"
    )
    conn.commit()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='New Artist'").fetchone()["id"]
    assert resolve_legacy_artist_id(conn, artist_id) == artist_id


# -- album_reorganize_sources / global_reorganize_sources --------------------


def test_album_reorganize_sources_delegates_after_resolving(monkeypatch, imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()

    captured = {}

    def fake_available_sources(album_data):
        captured['album_data'] = album_data
        return [{"source": "spotify", "label": "Spotify"}]

    monkeypatch.setattr(
        'core.library_reorganize.available_sources_for_album', fake_available_sources, raising=True,
    )
    result = album_reorganize_sources(imported_legacy_db, lib2_album_id)
    assert result == [{"source": "spotify", "label": "Spotify"}]
    assert captured['album_data']['title'] == 'Views'


def test_album_reorganize_sources_for_discography_only_are_empty(discography_only_album, imported_legacy_db):
    assert album_reorganize_sources(imported_legacy_db, discography_only_album) == []


def test_global_reorganize_sources_delegates(monkeypatch):
    monkeypatch.setattr(
        'core.library_reorganize.authed_sources',
        lambda: [{"source": "deezer", "label": "Deezer"}],
        raising=True,
    )
    assert global_reorganize_sources() == [{"source": "deezer", "label": "Deezer"}]


# -- preview_album_reorganize -------------------------------------------------


def test_preview_album_reorganize_resolves_and_delegates(monkeypatch, imported_legacy_db):
    """The native album id and the path helpers reach the catalogue planner.
    `source`/`mode` are still accepted from an older client and then ignored —
    a destination path has no metadata source."""
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()

    captured = {}

    def fake_plan(conn_, album_id, **kwargs):
        captured['album_id'] = album_id
        captured.update(kwargs)
        return {"success": True, "status": "planned", "tracks": []}

    monkeypatch.setattr('core.library2.reorganize_plan.plan_album_reorganize',
                        fake_plan, raising=True)

    result = preview_album_reorganize(
        imported_legacy_db, config_manager=None, lib2_album_id=lib2_album_id,
        source="spotify", mode="tags",
    )
    assert result["status"] == "planned"
    assert captured['album_id'] == lib2_album_id
    assert callable(captured['resolve_file_path_fn'])
    assert callable(captured['build_final_path_fn'])
    assert 'primary_source' not in captured
    assert 'metadata_source' not in captured


def test_preview_album_reorganize_rejects_album_without_files(discography_only_album, imported_legacy_db):
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        preview_album_reorganize(imported_legacy_db, config_manager=None, lib2_album_id=discography_only_album)
    assert exc_info.value.status == 404


def test_an_unknown_status_is_passed_through_rather_than_raised(monkeypatch, imported_legacy_db):
    """The bridge raises for the two cases the UI cannot render and passes
    everything else through. `no_source_id` used to be one of those pass-through
    outcomes; the planner no longer produces it, because a path needs no source.
    """
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()
    monkeypatch.setattr(
        'core.library2.reorganize_plan.plan_album_reorganize',
        lambda *_a, **_k: {"success": False, "status": "something_new", "tracks": []},
        raising=True,
    )
    result = preview_album_reorganize(imported_legacy_db, config_manager=None, lib2_album_id=lib2_album_id)
    assert result["status"] == "something_new"


def test_preview_album_reorganize_raises_for_no_tracks_status(monkeypatch, imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()
    monkeypatch.setattr(
        'core.library2.reorganize_plan.plan_album_reorganize',
        lambda *_a, **_k: {"success": False, "status": "no_tracks", "tracks": []},
        raising=True,
    )
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        preview_album_reorganize(imported_legacy_db, config_manager=None, lib2_album_id=lib2_album_id)
    assert exc_info.value.status == 404


# -- enqueue_album_reorganize -------------------------------------------------


def test_enqueue_album_reorganize_resolves_and_enqueues(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()

    result = enqueue_album_reorganize(imported_legacy_db, lib2_album_id, source="deezer", mode="api")
    assert result["queued"] is True
    assert result["queue_id"]

    from core.reorganize_queue import get_queue
    snap = get_queue().snapshot()
    all_ids = [snap['active']['album_id']] if snap['active'] else []
    all_ids += [item['album_id'] for item in snap['queued']]
    assert str(lib2_album_id) in all_ids


def test_enqueue_album_reorganize_rejects_album_without_files(discography_only_album, imported_legacy_db):
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        enqueue_album_reorganize(imported_legacy_db, discography_only_album)
    assert exc_info.value.status == 404


# -- enqueue_artist_reorganize_all --------------------------------------------


def test_enqueue_artist_reorganize_all_resolves_and_enqueues_every_album(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()["id"]
    conn.close()

    result = enqueue_artist_reorganize_all(imported_legacy_db, lib2_artist_id, source=None, mode="api")
    # The fixture's Drake artist owns 2 legacy albums (Views, One Dance).
    assert result["total_albums"] == 2
    assert result["enqueued"] == 2
    assert result["already_queued"] == 0


def test_enqueue_artist_reorganize_all_rejects_artist_without_owned_albums(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    conn.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES ('New Artist', NULL)"
    )
    conn.commit()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='New Artist'").fetchone()["id"]
    conn.close()
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        enqueue_artist_reorganize_all(imported_legacy_db, artist_id)
    assert exc_info.value.status == 404


def test_enqueue_artist_reorganize_all_includes_linked_alias_legacy_artist(
    imported_legacy_db,
):
    conn = imported_legacy_db._get_connection()
    canonical_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()["id"]
    alias_id = conn.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Alias Artist', 2)"
    ).lastrowid
    alias_album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,year) VALUES(?,'Alias Album',2026)",
        (alias_id,),
    ).lastrowid
    alias_track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Alias Track')", (alias_album,),
    ).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary) VALUES(?,'/alias.flac',1)",
                 (alias_track,))
    from core.library2.artist_aliases import link_artist_alias
    link_artist_alias(conn, alias_id, canonical_id)
    conn.commit()
    conn.close()

    result = enqueue_artist_reorganize_all(imported_legacy_db, canonical_id)

    assert result["total_albums"] == 3
