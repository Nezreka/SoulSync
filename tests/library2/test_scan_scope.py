"""Scope semantics for the file rescan (audit P1-08).

``album_ids=None`` means the whole library; ``[]`` means nothing. The empty
list must never widen into an unscoped full-library scan — an artist without
albums would otherwise probe every file in the database.
"""

from __future__ import annotations

import sqlite3
import json

import pytest

from core.library2.scan import _file_rows_in_scope
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def scoped_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "lib2.db"))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    artist_id = cur.lastrowid
    album_ids = []
    for title, path in (("Album One", "/m/a.flac"), ("Album Two", "/m/b.flac")):
        cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?,?)",
                    (artist_id, title))
        album_id = cur.lastrowid
        cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,1)",
                    (album_id, title))
        cur.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                    (cur.lastrowid, path))
        album_ids.append(album_id)
    conn.commit()
    yield conn, album_ids
    conn.close()


def _stats(**overrides):
    """The complete ``rescan_files`` stats shape, zeroed but for the overrides.

    Spelling the whole dict out at every call site meant that adding a counter
    broke three unrelated tests, which teaches people to stop asserting on the
    shape at all. This keeps the exact-equality check — the one that catches a
    counter being incremented twice — without the maintenance tax.
    """
    base = {
        "scanned": 0, "updated": 0, "missing": 0, "path_drift": 0,
        "path_repointed": 0, "missing_suspected": 0, "missing_confirmed": 0,
        "recovered": 0,
    }
    base.update(overrides)
    return base


def test_none_scope_scans_whole_library(scoped_conn):
    conn, _album_ids = scoped_conn
    rows = _file_rows_in_scope(conn, album_ids=None)
    assert sorted(r["path"] for r in rows) == ["/m/a.flac", "/m/b.flac"]


def test_single_album_scope_stays_scoped(scoped_conn):
    conn, album_ids = scoped_conn
    rows = _file_rows_in_scope(conn, album_ids=[album_ids[0]])
    assert [r["path"] for r in rows] == ["/m/a.flac"]


def test_empty_scope_scans_nothing(scoped_conn):
    """[] must not fall through to the unscoped full-library query."""
    conn, _album_ids = scoped_conn
    assert _file_rows_in_scope(conn, album_ids=[]) == []


def test_rescan_files_with_empty_scope_probes_nothing(scoped_conn, tmp_path):
    from core.library2.scan import rescan_files

    class _Shim:
        def __init__(self, path):
            self.path = path

        def _get_connection(self):
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            return conn

    db_path = str(tmp_path / "lib2.db")
    stats = rescan_files(_Shim(db_path), album_ids=[])
    assert stats == _stats()


def test_explicit_file_scope_is_batched_below_sqlite_parameter_limits():
    class _Result:
        def __init__(self, values):
            self._values = values

        def fetchall(self):
            return [(value,) for value in self._values]

    class _Connection:
        def __init__(self):
            self.batch_sizes = []

        def execute(self, _sql, params):
            self.batch_sizes.append(len(params))
            assert len(params) <= 500
            return _Result(params)

    conn = _Connection()
    rows = _file_rows_in_scope(conn, file_ids=list(range(1, 1202)))

    assert conn.batch_sizes == [500, 500, 201]
    assert len(rows) == 1201


def test_rescan_refreshes_tag_and_gap_cache_independently_of_quality(
        scoped_conn, tmp_path, monkeypatch):
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    file_path = tmp_path / "readable.flac"
    file_path.write_bytes(b"not-real-audio")

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _path: None)
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: {
        "title": "Album One",
        "artist": "A",
        "album": "Album One",
        "album_artist": "A",
        "track_number": 1,
        "disc_number": 1,
        "year": "2026",
        "genre": None,
        "has_cover_art": False,
        "error": None,
    })

    stats = rescan_files(_Shim(), album_ids=[album_ids[0]])

    row = conn.execute(
        """SELECT tags_json, missing_tags_json, metadata_gaps_json
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert stats == _stats(scanned=1)
    assert json.loads(row["tags_json"])["title"] == "Album One"
    assert json.loads(row["missing_tags_json"]) == ["genre", "cover"]
    assert json.loads(row["metadata_gaps_json"]) == ["genre", "cover"]


def test_rescan_closes_snapshot_connection_before_file_io(
        scoped_conn, tmp_path, monkeypatch):
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    file_path = tmp_path / "readable.flac"
    file_path.write_bytes(b"not-real-audio")
    state = {"active": 0, "opened": 0}

    class _TrackedConnection:
        def __init__(self):
            self._conn = sqlite3.connect(db_path)
            self._conn.row_factory = sqlite3.Row
            state["active"] += 1
            state["opened"] += 1

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def close(self):
            self._conn.close()
            state["active"] -= 1

    class _Shim:
        def _get_connection(self):
            return _TrackedConnection()

    def _assert_no_connection(_path):
        assert state["active"] == 0
        return str(file_path)

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", _assert_no_connection)
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda _path: ({"error": "fake"} if state["active"] == 0 else pytest.fail(
            "tag read ran with a database connection open"
        )),
    )
    monkeypatch.setattr(
        "core.imports.file_ops.probe_audio_quality",
        lambda _path: (None if state["active"] == 0 else pytest.fail(
            "quality probe ran with a database connection open"
        )),
    )

    stats = rescan_files(_Shim(), album_ids=[album_ids[0]])

    assert stats == _stats(scanned=1)
    # Still exactly two connections: nothing resolved to a miss, so the drift
    # reconcile phase never opens one.
    assert state == {"active": 0, "opened": 2}


def test_failed_tag_read_invalidates_stale_gap_cache(scoped_conn):
    from core.library2.tag_cache import persist_tag_cache

    conn, _album_ids = scoped_conn
    file_id = conn.execute(
        "SELECT id FROM lib2_track_files WHERE path='/m/a.flac'"
    ).fetchone()[0]
    conn.execute(
        """UPDATE lib2_track_files
              SET tags_json='{"title":"stale"}',
                  missing_tags_json='["cover"]',
                  metadata_gaps_json='["cover"]'
            WHERE id=?""",
        (file_id,),
    )

    assert persist_tag_cache(conn, file_id, {"error": "unreadable"}) is False

    row = conn.execute(
        """SELECT tags_json, missing_tags_json, metadata_gaps_json
             FROM lib2_track_files WHERE id=?""",
        (file_id,),
    ).fetchone()
    assert json.loads(row["tags_json"]) == {}
    assert json.loads(row["missing_tags_json"]) is None
    assert json.loads(row["metadata_gaps_json"]) is None


def test_healthy_consecutive_misses_confirm_and_recovery_resets_lifecycle(
        scoped_conn, tmp_path, monkeypatch):
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
        lambda _path, config_manager=None: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: True
    )

    rescan_files(_Shim(), album_ids=[album_ids[0]])
    first = conn.execute(
        """SELECT file_state, missing_scan_count, missing_since
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert first["file_state"] == "missing_suspected"
    assert first["missing_scan_count"] == 1
    assert first["missing_since"] is not None

    rescan_files(_Shim(), album_ids=[album_ids[0]])
    second = conn.execute(
        """SELECT file_state, missing_scan_count
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert dict(second) == {
        "file_state": "missing_confirmed",
        "missing_scan_count": 2,
    }

    recovered = tmp_path / "recovered.flac"
    recovered.write_bytes(b"fake")
    monkeypatch.setattr(
        "core.library2.paths.resolve_lib2_path", lambda _path: str(recovered)
    )
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: {"error": "fake"})
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _path: None)
    rescan_files(_Shim(), album_ids=[album_ids[0]])

    final = conn.execute(
        """SELECT file_state, missing_scan_count, missing_since
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert dict(final) == {
        "file_state": "active",
        "missing_scan_count": 0,
        "missing_since": None,
    }


def test_unhealthy_root_does_not_advance_missing_lifecycle(
        scoped_conn, monkeypatch):
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
        lambda _path, config_manager=None: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: False
    )
    rescan_files(_Shim(), album_ids=[album_ids[0]])

    row = conn.execute(
        """SELECT file_state, missing_scan_count, missing_since
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert dict(row) == {
        "file_state": "active",
        "missing_scan_count": 0,
        "missing_since": None,
    }


def test_root_health_requires_every_configured_library_mount(tmp_path):
    from core.library2.paths import missing_path_root_is_healthy

    healthy = tmp_path / "music-a"
    healthy.mkdir()

    class _Config:
        roots = [str(healthy)]

        def get(self, key, default=None):
            assert key == "library.music_paths"
            return self.roots

    config = _Config()
    assert missing_path_root_is_healthy("/remote/Artist/song.flac", config)
    config.roots.append(str(tmp_path / "offline-mount"))
    assert not missing_path_root_is_healthy("/remote/Artist/song.flac", config)


def _tags(**overrides):
    base = {
        "title": "Album One", "artist": "A", "album": "Album One",
        "album_artist": "A", "track_number": 1, "disc_number": 1,
        "year": "2026", "genre": "Pop", "has_cover_art": True, "error": None,
    }
    base.update(overrides)
    return base


def _verification(conn, path="/m/a.flac"):
    return conn.execute(
        "SELECT verification_status FROM lib2_track_files WHERE path=?", (path,)
    ).fetchone()[0]


def test_scan_heals_verification_status_from_the_file_tag(
        scoped_conn, tmp_path, monkeypatch):
    """issues.md T-09: every file the download pipeline finalized carries a
    SOULSYNC_VERIFICATION tag, and rescan already reads it — it was simply
    dropped on the way into the tag cache, so 194 of 268 production files had
    a NULL verification_status the UI could not show."""
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    file_path = tmp_path / "a.flac"
    file_path.write_bytes(b"audio")

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _p: str(file_path))
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _p: None)
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda _p: _tags(verification_status="verified"),
    )

    rescan_files(_Shim(), album_ids=[album_ids[0]])

    assert _verification(conn) == "verified"


def test_scan_never_downgrades_a_human_verification(
        scoped_conn, tmp_path, monkeypatch):
    """A human approval is a stronger statement than whatever the pipeline
    stamped into the file. The file pass observes, it does not re-judge."""
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    conn.execute(
        "UPDATE lib2_track_files SET verification_status='human_verified' "
        "WHERE path='/m/a.flac'"
    )
    conn.commit()
    file_path = tmp_path / "a.flac"
    file_path.write_bytes(b"audio")

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _p: str(file_path))
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _p: None)
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda _p: _tags(verification_status="unverified"),
    )

    rescan_files(_Shim(), album_ids=[album_ids[0]])

    assert _verification(conn) == "human_verified"


def test_scan_leaves_verification_alone_when_the_file_carries_no_tag(
        scoped_conn, tmp_path, monkeypatch):
    """An untagged file is no evidence — never clear a status the catalogue
    already knows (e.g. one the AcoustID scanner wrote)."""
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    conn.execute(
        "UPDATE lib2_track_files SET verification_status='verified' "
        "WHERE path='/m/a.flac'"
    )
    conn.commit()
    file_path = tmp_path / "a.flac"
    file_path.write_bytes(b"audio")

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _p: str(file_path))
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _p: None)
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _p: _tags())

    rescan_files(_Shim(), album_ids=[album_ids[0]])

    assert _verification(conn) == "verified"


def test_unknown_verification_tag_value_is_ignored(scoped_conn, tmp_path, monkeypatch):
    """Only the four known states are accepted; a hand-edited tag must not
    invent a fifth one the UI cannot render."""
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    file_path = tmp_path / "a.flac"
    file_path.write_bytes(b"audio")

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _p: str(file_path))
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _p: None)
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda _p: _tags(verification_status="totally-made-up"),
    )

    rescan_files(_Shim(), album_ids=[album_ids[0]])

    assert _verification(conn) is None


def test_a_relative_stored_path_no_longer_defeats_the_missing_lifecycle(tmp_path):
    """The bug behind "Refresh & Scan never marks anything as missing".

    A media server reports paths relative to its own library root, so
    ``os.path.isabs`` is False and the direct-parent branch could never run.
    Everything then fell through to the configured-roots fallback, which is
    False for anyone who never filled in Settings -> Music Library Paths — and
    ``_persist_missing_observation`` returns without writing when the root is
    unhealthy. The result was a file gone from disk that stayed ``active``
    through any number of scans, with its tag cache stuck on "pending".
    """
    from core.library2.paths import missing_path_root_is_healthy

    music = tmp_path / "music"
    (music / "Sawano Hiroyuki" / "Call of Silence").mkdir(parents=True)

    class _Config:
        def get(self, key, default=None):
            return {
                "library.music_paths": [],
                "soulseek.transfer_path": str(music),
            }.get(key, default)

    stored = "Sawano Hiroyuki/Call of Silence/01-05 - Call of Silence.flac"
    assert missing_path_root_is_healthy(stored, _Config())


def test_a_deleted_folder_is_still_credible_while_the_artist_folder_lives(tmp_path):
    """The album folder went with the files; the storage is plainly there."""
    from core.library2.paths import missing_path_root_is_healthy

    music = tmp_path / "music"
    (music / "Sawano Hiroyuki").mkdir(parents=True)

    class _Config:
        def get(self, key, default=None):
            return {
                "library.music_paths": [],
                "soulseek.transfer_path": str(music),
            }.get(key, default)

    stored = "Sawano Hiroyuki/Deleted Album/01 - Gone.flac"
    assert missing_path_root_is_healthy(stored, _Config())


def test_unreachable_storage_still_defers_every_miss(tmp_path):
    """The protection that must survive: an absent mount is not a deletion."""
    from core.library2.paths import missing_path_root_is_healthy

    class _Config:
        def get(self, key, default=None):
            return {
                "library.music_paths": [],
                "soulseek.transfer_path": str(tmp_path / "never-mounted"),
            }.get(key, default)

    assert not missing_path_root_is_healthy("Artist/Album/song.flac", _Config())


def test_manual_refresh_confirms_a_credible_miss_on_the_first_pass(
        scoped_conn, monkeypatch):
    """The two-scan wait protects the unattended sweep, not the button.

    Someone who presses "Refresh & Scan" asked a direct question. Making them
    press twice to get an answer is what the report read as "it never notices
    the file is gone".
    """
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
        lambda _path, config_manager=None: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: True
    )

    stats = rescan_files(_Shim(), album_ids=[album_ids[0]], manual=True)

    row = conn.execute(
        """SELECT file_state, missing_scan_count FROM lib2_track_files
            WHERE path='/m/a.flac'"""
    ).fetchone()
    assert row["file_state"] == "missing_confirmed"
    assert row["missing_scan_count"] == 1
    assert stats["missing_confirmed"] == 1
    assert stats["missing"] == 1


def test_an_unattended_scan_keeps_the_two_pass_wait(scoped_conn, monkeypatch):
    """Same input, no button: one miss is still only a suspicion."""
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
        lambda _path, config_manager=None: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: True
    )

    stats = rescan_files(_Shim(), album_ids=[album_ids[0]])

    row = conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE path='/m/a.flac'"
    ).fetchone()
    assert row["file_state"] == "missing_suspected"
    assert stats["missing_suspected"] == 1
    assert stats["missing_confirmed"] == 0


def test_a_stale_configured_root_cannot_veto_a_folder_we_can_see(tmp_path):
    """Second half of the same bug, found only after the first fix shipped.

    The user's diagnostic proved the artist folder was reachable — eight files
    in a sibling album resolved in the very same scan — and the miss was still
    dropped. By elimination that leaves one branch: a configured Music Library
    Path that does not exist in this process's filesystem view. One stale entry
    (a host path the container cannot see, a share renamed years ago) then
    vetoed every missing observation in the whole library, permanently.

    Evidence about this specific path has to outrank that: we can see the
    directory the row points into, and the file is not in it.
    """
    from core.library2.paths import missing_path_root_is_healthy

    music = tmp_path / "music"
    (music / "Sawano Hiroyuki" / "TV Anime Original Soundtrack").mkdir(parents=True)

    class _Config:
        def get(self, key, default=None):
            return {
                # Points at a path this process cannot see — the stale entry.
                "library.music_paths": [str(tmp_path / "mnt" / "user" / "Music")],
                "soulseek.transfer_path": str(music),
            }.get(key, default)

    stored = "Sawano Hiroyuki/TV Anime Original Soundtrack/01-03 - Gone.flac"
    assert missing_path_root_is_healthy(stored, _Config())


def test_a_stale_root_still_defers_a_path_nothing_can_vouch_for(tmp_path):
    """The dd28-19 protection has to survive the reordering.

    No reachable directory anywhere on this path, and a declared root that is
    not mounted: that is what an absent share looks like, and its files may be
    perfectly alive.
    """
    from core.library2.paths import missing_path_root_is_healthy

    music = tmp_path / "music"
    music.mkdir()

    class _Config:
        def get(self, key, default=None):
            return {
                "library.music_paths": [str(tmp_path / "offline-mount")],
                "soulseek.transfer_path": str(music),
            }.get(key, default)

    assert not missing_path_root_is_healthy("Some Other Artist/Album/x.flac", _Config())


def test_the_scan_reports_which_tracks_changed_availability(
        scoped_conn, tmp_path, monkeypatch):
    """The hand-off that lets Refresh & Scan feed acquisition directly.

    Without it the catalogue knew a file was gone and the only consumer that
    cares — the Wishlist — found out up to an hour later, from a job that
    rescans the whole library to rediscover what this scan already knew.
    """
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    track_id = conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE path='/m/a.flac'"
    ).fetchone()[0]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
        lambda _path, config_manager=None: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: True
    )

    reported = []
    rescan_files(_Shim(), album_ids=[album_ids[0]], manual=True,
                 on_presence_change=reported.extend)
    assert reported == [track_id]

    # A second identical pass changes nothing, so it reports nothing: the
    # signal is the transition, not the state.
    reported.clear()
    rescan_files(_Shim(), album_ids=[album_ids[0]], manual=True,
                 on_presence_change=reported.extend)
    assert reported == []


def test_a_failing_presence_consumer_cannot_fail_the_scan(
        scoped_conn, monkeypatch):
    """The scan's own work is already committed by then."""
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
        lambda _path, config_manager=None: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: True
    )

    def _explode(_ids):
        raise RuntimeError("acquisition is down")

    stats = rescan_files(_Shim(), album_ids=[album_ids[0]], manual=True,
                         on_presence_change=_explode)

    assert stats["missing_confirmed"] == 1
    assert conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE path='/m/a.flac'"
    ).fetchone()[0] == "missing_confirmed"
