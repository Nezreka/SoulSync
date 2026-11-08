from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from contextlib import closing


def _create(worker, path, details):
    return worker._create_finding(
        job_id="quality_upgrade_scan",
        finding_type="quality_below_cutoff",
        severity="info",
        entity_type="track",
        entity_id="lib2:7",
        file_path=str(path),
        title="Below cutoff",
        description="test",
        details=details,
    )


def test_file_findings_dedup_per_file_and_fingerprint(tmp_path):
    database = MusicDatabase(str(tmp_path / "repair.sqlite"))
    worker = RepairWorker(database)
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    assert _create(worker, first, {"profile_id": 1, "target": "flac"}) is True
    assert _create(worker, second, {"profile_id": 1, "target": "flac"}) is True

    with closing(database._get_connection()) as conn:
        conn.execute(
            "UPDATE repair_findings SET status='dismissed' WHERE file_path=?",
            (str(first),),
        )
        conn.commit()

    assert _create(worker, first, {"profile_id": 1, "target": "flac"}) is False
    assert _create(worker, first, {"profile_id": 2, "target": "24bit-flac"}) is True


def test_failed_library_sync_keeps_successful_physical_fix_pending(
    tmp_path, monkeypatch
):
    database = MusicDatabase(str(tmp_path / "sync-failure.sqlite"))
    worker = RepairWorker(database)
    target = tmp_path / "broken.flac"
    target.write_bytes(b"broken")
    assert worker._create_finding(
        job_id="audio_corruption_detector", finding_type="corrupt_audio",
        severity="warning", entity_type="track", entity_id="lib2:9",
        file_path=str(target), title="Corrupt", description="test", details={},
    )
    with closing(database._get_connection()) as conn:
        finding_id = conn.execute(
            "SELECT id FROM repair_findings WHERE file_path=?", (str(target),)
        ).fetchone()[0]
    monkeypatch.setattr(
        worker, "_execute_fix",
        lambda *_args, **_kwargs: {"success": True, "action": "redownload"},
    )
    monkeypatch.setattr(
        "core.library2.maintenance_sync.sync_repair_change",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sync down")),
    )

    result = worker.fix_finding(finding_id)

    assert result["success"] is False
    assert result["retryable"] is True
    with closing(database._get_connection()) as conn:
        assert conn.execute(
            "SELECT status FROM repair_findings WHERE id=?", (finding_id,)
        ).fetchone()[0] == "pending"


def test_two_combined_artists_on_one_artist_are_two_findings(tmp_path):
    """'A, B' and 'A, C' can both land on A's id; keyed on the entity alone the
    second overwrote the first, so one split was never offered."""
    database = MusicDatabase(str(tmp_path / "comma.sqlite"))
    worker = RepairWorker(database)

    def create(name):
        return worker._create_finding(
            job_id="comma_artist_splitter", finding_type="comma_artist_split",
            severity="warning", entity_type="artist", entity_id="lib2:5", file_path=None,
            title=f"Combined artist: {name}", description="test",
            details={"combined_name": name, "split_artists": name.split(", ")})

    assert create("A, B") is True
    assert create("A, C") is True
    assert create("A, B") is False  # the same credit again refreshes its own row
    with closing(database._get_connection()) as conn:
        titles = sorted(r[0] for r in conn.execute(
            "SELECT title FROM repair_findings WHERE job_id='comma_artist_splitter'"))
    assert titles == ["Combined artist: A, B", "Combined artist: A, C"]



def _dismiss_all(database):
    with closing(database._get_connection()) as conn:
        conn.execute("UPDATE repair_findings SET status='dismissed'")
        conn.commit()


def test_a_dismissed_finding_stays_dismissed_when_only_volatile_details_change(tmp_path):
    """the fingerprint hashed every detail, including a rescan's size and
    bitrate (dedup_file), ids enrichment adds and a provider's art url. a tag
    write or an enrichment pass brought dismissed findings back."""
    database = MusicDatabase(str(tmp_path / "repair.sqlite"))
    worker = RepairWorker(database)
    song = tmp_path / "song.flac"
    song.write_bytes(b"audio")
    base = {"target": "flac", "dedup_file": {"size": 100, "bitrate": 900},
            "provider_ids": {"track": {}}, "found_artwork_url": "http://a/1.jpg"}
    assert _create(worker, song, base) is True
    _dismiss_all(database)

    song.write_bytes(b"audio, retagged")      # same file, new size
    later = {**base, "dedup_file": {"size": 140, "bitrate": 900},
             "provider_ids": {"track": {"spotify": "sp1"}}, "found_artwork_url": "http://a/2.jpg"}
    assert _create(worker, song, later) is False
    # a different problem on the same file still surfaces
    assert _create(worker, song, {**later, "target": "24bit-flac"}) is True


def test_a_row_dismissed_under_the_old_fingerprint_stays_dismissed(tmp_path):
    database = MusicDatabase(str(tmp_path / "repair.sqlite"))
    worker = RepairWorker(database)
    song = tmp_path / "song.flac"
    song.write_bytes(b"audio")
    assert _create(worker, song, {"target": "flac", "dedup_file": {"size": 1}}) is True
    with closing(database._get_connection()) as conn:
        # what an older build stored: a fingerprint over every detail, and no
        # file identity
        conn.execute("UPDATE repair_findings SET status='dismissed', details_json=?",
                     ('{"target": "flac", "dedup_file": {"size": 1}, "_dedup_fingerprint": "old"}',))
        conn.commit()
    assert _create(worker, song, {"target": "flac", "dedup_file": {"size": 2}}) is False
