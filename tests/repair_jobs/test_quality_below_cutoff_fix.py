"""The last of the `quality_below_cutoff` findings stays approvable.

The job that produced them is gone — queueing an upgrade candidate is what the
wanted projection does continuously, so a second job doing it on its own
cadence was pure duplication. The findings already sitting in a user's review
queue are the last of their kind, and `_fix_quality_below_cutoff` is what still
services them.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from core.library2 import ADMIN_PROFILE_ID
from core.library2.monitor_rules import PROVENANCE_LEGACY, record_rule
from core.library2.schema import ensure_library_v2_schema
from core.library2.wanted import recompute_wanted


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
    # dd28-11: the scan resolves the EFFECTIVE profile (the shared cascade),
    # not the denormalized column — and the cascade only honours a level whose
    # ``quality_profile_explicit`` flag is set, exactly as
    # ``assign_quality_profile`` writes it. Seeding the bare column alone
    # describes an inherited value, which must NOT win.
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, monitored, quality_profile_id, "
        "quality_profile_explicit) VALUES(?,?,?,?,1)",
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


def _config(settings=None):
    values = {
        "features.library_v2": True,
        "repair.jobs.quality_upgrade_scan.settings": settings or {},
    }
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def test_fix_quality_below_cutoff_queues_the_upgrade(monkeypatch, library_database):
    from core.repair_worker import RepairWorker

    database, conn = library_database
    cutoff = _seed_track(conn, policy="until_cutoff")
    calls = []

    def mirror(_db, _conn, track_ids, *, profile_id, **_kwargs):
        calls.append((tuple(track_ids), profile_id))
        return len(track_ids)

    monkeypatch.setattr(
        "core.library2.wishlist_mirror.mirror_projected_tracks_wishlist", mirror)
    worker = RepairWorker(database=database)
    worker._config_manager = _config()

    result = worker._fix_quality_below_cutoff(
        "track", f"lib2:{cutoff}", None, {})

    assert result["success"] is True, result
    assert calls == [((cutoff,), ADMIN_PROFILE_ID)]


