import sqlite3
from types import SimpleNamespace

import pytest

from core.library2 import ADMIN_PROFILE_ID
from core.library2.monitor_rules import PROVENANCE_LEGACY, record_rule
from core.library2.schema import ensure_library_v2_schema
from core.library2.wanted import recompute_wanted
from core.repair_jobs.base import JobContext, JobResult
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


def _config(settings=None):
    values = {
        "features.library_v2": True,
        "repair.jobs.quality_upgrade_scan.settings": settings or {},
    }
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def test_review_mode_creates_findings_instead_of_queueing(
    monkeypatch, library_database
):
    database, conn = library_database
    cutoff = _seed_track(conn, policy="until_cutoff")
    calls = []

    def mirror(_db, _conn, track_ids, *, profile_id, **_kwargs):
        calls.append(tuple(track_ids))
        return len(track_ids)

    monkeypatch.setattr(
        "core.library2.wishlist_mirror.mirror_projected_tracks_wishlist", mirror)
    findings = []
    context = JobContext(
        db=database,
        transfer_folder="",
        config_manager=_config({"mode": "review"}),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = Lib2UpgradeScanJob().scan(context)

    assert calls == []
    assert result.auto_fixed == 0
    assert result.findings_created == 1
    finding = findings[0]
    assert finding["finding_type"] == "quality_below_cutoff"
    assert finding["entity_id"] == f"lib2:{cutoff}"
    assert finding["details"]["library_v2"]["track_id"] == cutoff
    assert finding["details"]["upgrade_candidate"] is True


def test_review_and_automatic_payload_share_live_profile_cascade(library_database):
    database, conn = library_database
    track = _seed_track(conn, policy="until_cutoff")
    row = conn.execute(
        """SELECT t.album_id, al.primary_artist_id AS artist_id
             FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
            WHERE t.id=?""",
        (track,),
    ).fetchone()
    stale = conn.execute(
        "INSERT INTO quality_profiles(name, ranked_targets, upgrade_policy) "
        "VALUES('Stale acceptable','[]','acceptable')"
    ).lastrowid
    live = conn.execute(
        "INSERT INTO quality_profiles(name, ranked_targets, upgrade_policy) "
        "VALUES('Live upgrade','[{\"label\":\"FLAC\",\"format\":\"flac\"}]',"
        "'until_cutoff')"
    ).lastrowid
    conn.commit()

    from core.library2.wishlist_mirror import track_wishlist_payload

    job = Lib2UpgradeScanJob()
    for source in ("global", "artist", "album", "track"):
        conn.execute("UPDATE quality_profiles SET is_default=0")
        conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=?", (stale,))
        conn.execute(
            "UPDATE lib2_artists SET quality_profile_id=?, quality_profile_explicit=0 "
            "WHERE id=?", (stale, row["artist_id"]),
        )
        conn.execute(
            "UPDATE lib2_albums SET quality_profile_id=?, quality_profile_explicit=0 "
            "WHERE id=?", (stale, row["album_id"]),
        )
        conn.execute(
            "UPDATE lib2_tracks SET quality_profile_id=?, quality_profile_explicit=0 "
            "WHERE id=?", (stale, track),
        )
        if source == "global":
            conn.execute("UPDATE quality_profiles SET is_default=0")
            conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=?", (live,))
        else:
            table = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}[source]
            entity_id = {"artist": row["artist_id"], "album": row["album_id"], "track": track}[source]
            conn.execute(
                f"UPDATE {table} SET quality_profile_id=?, quality_profile_explicit=1 WHERE id=?",
                (live, entity_id),
            )
        conn.commit()

        payload = track_wishlist_payload(conn, track)
        findings = []
        result = JobResult()
        context = JobContext(
            db=database,
            transfer_folder="",
            config_manager=_config({"mode": "review"}),
            create_finding=lambda **kwargs: findings.append(kwargs) or True,
        )
        job._create_review_finding(context, conn, track, result)

        assert payload["quality_profile_id"] == live
        assert payload["quality_profile"]["source"] == source
        assert result.findings_created == 1
        assert findings[0]["details"]["quality_profile_id"] == live
        assert findings[0]["details"]["quality_profile_source"] == source


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
