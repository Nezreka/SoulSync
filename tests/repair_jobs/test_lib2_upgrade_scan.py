import sqlite3
from types import SimpleNamespace

import pytest

from core.library2 import ADMIN_PROFILE_ID
from core.library2.monitor_rules import PROVENANCE_LEGACY, record_rule
from core.library2.schema import ensure_library_v2_schema
from core.library2.wanted import recompute_wanted
from core.repair_jobs.base import JobContext
from core.repair_jobs.lib2_upgrade_scan import Lib2UpgradeScanJob


class _Database:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


@pytest.fixture
def library_database(tmp_path):
    database = _Database(tmp_path / "library.sqlite")
    conn = database._get_connection()
    ensure_library_v2_schema(conn)
    conn.commit()
    yield database, conn
    conn.close()


def _seed_track(conn, *, policy: str, monitored: int = 1) -> int:
    suffix = conn.execute("SELECT COUNT(*) FROM quality_profiles").fetchone()[0]
    profile = conn.execute(
        "INSERT INTO quality_profiles(name, ranked_targets, upgrade_policy) "
        "VALUES(?,?,?)",
        (f"Upgrade {policy} {suffix}",
         '[{"label":"FLAC","format":"flac"}]', policy),
    ).lastrowid
    artist = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES(?)", (f"Artist {profile}",)
    ).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?,?)",
        (artist, f"Album {profile}"),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
        (album, artist),
    )
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, monitored, quality_profile_id) "
        "VALUES(?,?,?,?)",
        (album, f"Track {profile}", monitored, profile),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, bitrate) "
        "VALUES(?,?,?,?)",
        (track, f"/music/{track}.mp3", "mp3", 320),
    )
    record_rule(conn, "track", track, bool(monitored), PROVENANCE_LEGACY)
    recompute_wanted(conn, track_ids=[track])
    conn.commit()
    return track


def test_periodic_job_queues_only_profiles_that_allow_upgrades(
    monkeypatch, library_database
):
    database, conn = library_database
    cutoff = _seed_track(conn, policy="until_cutoff")
    top = _seed_track(conn, policy="until_top")
    _seed_track(conn, policy="acceptable")
    _seed_track(conn, policy="until_cutoff", monitored=0)
    calls = []

    def mirror(_db, _conn, track_ids, *, profile_id, **_kwargs):
        calls.append((tuple(track_ids), profile_id))
        return len(track_ids)

    monkeypatch.setattr(
        "core.library2.wishlist_mirror.mirror_projected_tracks_wishlist", mirror)
    progress = []
    context = JobContext(
        db=database,
        transfer_folder="",
        config_manager=SimpleNamespace(
            get=lambda key, default=None: (
                True if key == "features.library_v2" else default
            )
        ),
        update_progress=lambda done, total: progress.append((done, total)),
    )

    result = Lib2UpgradeScanJob().scan(context)

    assert result.scanned == 2
    assert result.auto_fixed == 2
    assert result.errors == 0
    assert calls == [((cutoff, top), ADMIN_PROFILE_ID)]
    assert progress == [(2, 2)]


def test_periodic_job_is_noop_when_library_v2_is_disabled(library_database):
    database, _conn = library_database
    context = JobContext(
        db=database,
        transfer_folder="",
        config_manager=SimpleNamespace(get=lambda _key, default=None: default),
    )

    result = Lib2UpgradeScanJob().scan(context)

    assert result.scanned == 0
    assert result.auto_fixed == 0
    assert result.errors == 0
