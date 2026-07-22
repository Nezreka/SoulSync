"""Tests for core.library2.reorganize_bridge (docs §50, Interactive Reorganize).

The bridge resolves a lib2 album/artist to its legacy back-reference and
delegates to the existing (legacy-schema) reorganize planner/queue — see the
module docstring for why reimplementing that pipeline against lib2 tables
would be a second implementation to keep in sync. These tests pin:

1. Legacy-id resolution: found-with-link, found-without-link (409), missing
   entity (404).
2. Each public function resolves the id THEN delegates to the right
   core.library_reorganize / core.reorganize_queue call with the right args.
3. Planner-level failure statuses (no_album/no_tracks) surface as
   ReorganizeBridgeError, not a raw dict the caller has to re-inspect.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

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
                   FROM albums al JOIN artists ar ON al.artist_id = ar.id WHERE al.id=?""",
                (str(album_id),),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def get_artist_albums_for_reorganize(self, artist_id):
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """SELECT al.id AS album_id, al.title AS album_title, ar.id AS artist_id,
                          ar.name AS artist_name
                   FROM albums al JOIN artists ar ON al.artist_id = ar.id WHERE ar.id=?
                   ORDER BY al.year ASC, al.title ASC""",
                (str(artist_id),),
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


def test_resolve_legacy_album_id_returns_the_backref(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()
    assert resolve_legacy_album_id(imported_legacy_db._get_connection(), lib2_album_id) == 10


def test_resolve_legacy_album_id_raises_409_without_backref(discography_only_album, imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        resolve_legacy_album_id(conn, discography_only_album)
    assert exc_info.value.status == 409
    assert "Update Discography" in str(exc_info.value)


def test_resolve_legacy_album_id_raises_404_for_missing_album(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        resolve_legacy_album_id(conn, 999999)
    assert exc_info.value.status == 404


def test_resolve_legacy_artist_id_returns_the_backref(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()["id"]
    conn.close()
    assert resolve_legacy_artist_id(imported_legacy_db._get_connection(), lib2_artist_id) == 1


def test_resolvers_preserve_text_legacy_backrefs(imported_legacy_db):
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

    assert resolve_legacy_album_id(conn, album_id) == album_legacy_id
    assert resolve_legacy_artist_id(conn, artist_id) == artist_legacy_id
    conn.close()


def test_resolve_legacy_artist_id_raises_409_without_backref(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    conn.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES ('New Artist', NULL)"
    )
    conn.commit()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='New Artist'").fetchone()["id"]
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        resolve_legacy_artist_id(conn, artist_id)
    assert exc_info.value.status == 409


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


def test_album_reorganize_sources_raises_for_discography_only(discography_only_album, imported_legacy_db):
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        album_reorganize_sources(imported_legacy_db, discography_only_album)
    assert exc_info.value.status == 409


def test_global_reorganize_sources_delegates(monkeypatch):
    monkeypatch.setattr(
        'core.library_reorganize.authed_sources',
        lambda: [{"source": "deezer", "label": "Deezer"}],
        raising=True,
    )
    assert global_reorganize_sources() == [{"source": "deezer", "label": "Deezer"}]


# -- preview_album_reorganize -------------------------------------------------


def test_preview_album_reorganize_resolves_and_delegates(monkeypatch, imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()

    captured = {}

    def fake_preview(**kwargs):
        captured.update(kwargs)
        return {"success": True, "status": "planned", "tracks": []}

    monkeypatch.setattr('core.library_reorganize.preview_album_reorganize', fake_preview, raising=True)

    result = preview_album_reorganize(
        imported_legacy_db, config_manager=None, lib2_album_id=lib2_album_id,
        source="spotify", mode="tags",
    )
    assert result["status"] == "planned"
    assert captured['album_id'] == '10'
    assert captured['primary_source'] == 'spotify'
    assert captured['strict_source'] is True
    assert captured['metadata_source'] == 'tags'
    assert callable(captured['resolve_file_path_fn'])
    assert callable(captured['build_final_path_fn'])


def test_preview_album_reorganize_raises_for_discography_only(discography_only_album, imported_legacy_db):
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        preview_album_reorganize(imported_legacy_db, config_manager=None, lib2_album_id=discography_only_album)
    assert exc_info.value.status == 409


def test_preview_album_reorganize_translates_no_source_id_status_through(monkeypatch, imported_legacy_db):
    """no_source_id is a legitimate planned-but-unresolvable outcome (the UI
    shows it inline) — NOT an error the bridge should raise."""
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()
    monkeypatch.setattr(
        'core.library_reorganize.preview_album_reorganize',
        lambda **kwargs: {"success": False, "status": "no_source_id", "tracks": []},
        raising=True,
    )
    result = preview_album_reorganize(imported_legacy_db, config_manager=None, lib2_album_id=lib2_album_id)
    assert result["status"] == "no_source_id"


def test_preview_album_reorganize_raises_for_no_tracks_status(monkeypatch, imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    lib2_album_id = conn.execute("SELECT id FROM lib2_albums WHERE legacy_album_id=10").fetchone()["id"]
    conn.close()
    monkeypatch.setattr(
        'core.library_reorganize.preview_album_reorganize',
        lambda **kwargs: {"success": False, "status": "no_tracks", "tracks": []},
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
    assert '10' in all_ids


def test_enqueue_album_reorganize_raises_for_discography_only(discography_only_album, imported_legacy_db):
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        enqueue_album_reorganize(imported_legacy_db, discography_only_album)
    assert exc_info.value.status == 409


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


def test_enqueue_artist_reorganize_all_raises_for_missing_backref(imported_legacy_db):
    conn = imported_legacy_db._get_connection()
    conn.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES ('New Artist', NULL)"
    )
    conn.commit()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='New Artist'").fetchone()["id"]
    conn.close()
    with pytest.raises(ReorganizeBridgeError) as exc_info:
        enqueue_artist_reorganize_all(imported_legacy_db, artist_id)
    assert exc_info.value.status == 409


def test_enqueue_artist_reorganize_all_includes_linked_alias_legacy_artist(
    imported_legacy_db,
):
    conn = imported_legacy_db._get_connection()
    canonical_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO artists VALUES(2, 'Alias Artist', NULL, NULL, NULL, NULL, NULL)"
    )
    alias_id = conn.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Alias Artist', 2)"
    ).lastrowid
    conn.execute(
        "INSERT INTO albums(id, artist_id, title, year) "
        "VALUES(999, 2, 'Alias Legacy Album', 2026)"
    )
    from core.library2.artist_aliases import link_artist_alias
    link_artist_alias(conn, alias_id, canonical_id)
    conn.commit()
    conn.close()

    result = enqueue_artist_reorganize_all(imported_legacy_db, canonical_id)

    assert result["total_albums"] == 3
