"""Repair-worker mutations converge into optional Library v2."""

from __future__ import annotations

from core.repair_jobs.base import JobContext


class _Config:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def get(self, key, default=None):
        if key == "features.library_v2":
            return self.enabled
        return default


def _import(legacy_db):
    from core.library2.importer import import_legacy_library

    import_legacy_library(legacy_db)


def _add_v2_only_file(legacy_db, path, *, title="V2-only Song"):
    conn = legacy_db._get_connection()
    try:
        album_id = conn.execute(
            "SELECT id FROM lib2_albums WHERE legacy_album_id=10"
        ).fetchone()[0]
        artist_id = conn.execute(
            "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
        ).fetchone()[0]
        track_id = conn.execute(
            """INSERT INTO lib2_tracks(
                   album_id, title, track_number, duration, monitored,
                   quality_profile_id)
               VALUES(?,?,9,210000,1,
                      (SELECT id FROM quality_profiles ORDER BY id LIMIT 1))""",
            (album_id, title),
        ).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id,artist_id,role,position) "
            "VALUES(?,?,'primary',0)",
            (track_id, artist_id),
        )
        file_id = conn.execute(
            """INSERT INTO lib2_track_files(
                   track_id,path,source,file_state,is_primary)
               VALUES(?,?,'autolink','active',1)""",
            (track_id, str(path)),
        ).lastrowid
        conn.commit()
        return int(track_id), int(file_id)
    finally:
        conn.close()


def test_finding_annotation_attaches_stable_v2_subjects(legacy_db):
    from core.library2.maintenance_sync import annotate_finding_details

    _import(legacy_db)
    details = annotate_finding_details(
        legacy_db,
        _Config(True),
        entity_type="track",
        entity_id=100,
        file_path="/m/01.flac",
        details={"reason": "test"},
    )

    assert details["reason"] == "test"
    assert details["library_v2"]["track_id"] is not None
    assert details["library_v2"]["album_id"] is not None
    assert details["library_v2"]["artist_id"] is not None
    assert details["library_v2"]["file_id"] is not None
    assert len(details["library_v2"]["track_ids"]) == 1
    assert len(details["library_v2"]["file_ids"]) == 1


def test_feature_gate_keeps_disabled_library_untouched(legacy_db):
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    outcome = sync_repair_change(
        legacy_db,
        _Config(False),
        job_id="acoustid_scanner",
        finding_type="acoustid_verification",
        action="verification_status_updated",
        entity_type="track",
        entity_id=100,
        file_path="/m/01.flac",
    )

    assert outcome == {"enabled": False, "reason": "feature_disabled"}
    conn = legacy_db._get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM lib2_maintenance_events").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_verification_change_updates_v2_file_and_history(legacy_db):
    from core.library2.history_feed import scoped_history
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    conn = legacy_db._get_connection()
    conn.execute("ALTER TABLE tracks ADD COLUMN verification_status TEXT")
    conn.execute("UPDATE tracks SET verification_status='verified' WHERE id=100")
    conn.commit()
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]
    conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="acoustid_scanner",
        finding_type="acoustid_verification",
        action="verification_status_updated",
        entity_type="track",
        entity_id=100,
        file_path="/m/01.flac",
    )

    assert outcome["reason"] == "synchronized"
    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT verification_status FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
        history = scoped_history(conn, scope="track", entity_id=track_id)
    finally:
        conn.close()
    assert row[0] == "verified"
    event = next(item for item in history if item["event_type"] == "verification_status_updated")
    assert event["title"] == "Acoustic ID status updated"
    assert event["source"] == "maintenance"


def test_successful_delete_marks_v2_file_deleted_and_recomputes_wanted(legacy_db):
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="expired_download_cleaner",
        finding_type="expired_download",
        action="deleted_expired",
        entity_type="track",
        entity_id=100,
        file_path="/m/01.flac",
    )

    assert outcome["reason"] == "synchronized"
    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT file_state FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
        event = conn.execute(
            "SELECT action, changed_fields_json FROM lib2_maintenance_events "
            "WHERE lib2_track_id=(SELECT id FROM lib2_tracks WHERE legacy_track_id=100) "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "deleted"
    assert event[0] == "deleted_expired"
    assert "file_state" in event[1]


def test_new_derivative_is_linked_to_same_v2_track(legacy_db, tmp_path):
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    output = tmp_path / "01.mp3"
    output.write_bytes(b"synthetic derivative")

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="lossy_converter",
        finding_type="missing_lossy_copy",
        action="converted",
        entity_type="track",
        entity_id=100,
        file_path="/m/01.flac",
        result={"output_path": str(output)},
    )

    assert outcome["reason"] == "synchronized"
    conn = legacy_db._get_connection()
    try:
        original = conn.execute(
            "SELECT track_id FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
        derivative = conn.execute(
            "SELECT track_id, source FROM lib2_track_files WHERE path=?",
            (str(output),),
        ).fetchone()
    finally:
        conn.close()
    assert derivative is not None
    assert derivative[0] == original[0]
    assert derivative[1] == "repair_job"


def test_cover_fix_invalidates_both_managed_cache_variants(legacy_db):
    from core.library2.artwork import artwork_file, thumb_file
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    conn = legacy_db._get_connection()
    album_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE legacy_album_id=10"
    ).fetchone()[0]
    conn.close()
    full = artwork_file(legacy_db, "album", album_id)
    thumb = thumb_file(legacy_db, "album", album_id)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"old full")
    thumb.write_bytes(b"old thumb")

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="missing_cover_art",
        finding_type="missing_cover_art",
        action="applied_cover_art",
        entity_type="album",
        entity_id=10,
    )

    assert outcome["artwork_invalidated"] == 2
    assert not full.exists()
    assert not thumb.exists()


def test_v2_file_subject_enumerator_is_gated_and_excludes_legacy_owned_files(
    legacy_db, tmp_path,
):
    from core.library2.maintenance_sync import v2_uncovered_file_subjects

    _import(legacy_db)
    audio = tmp_path / "v2-only.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio)

    assert v2_uncovered_file_subjects(legacy_db, _Config(False)) == []
    subjects = v2_uncovered_file_subjects(legacy_db, _Config(True))
    assert [(row["track_id"], row["file_id"]) for row in subjects] == [
        (track_id, file_id)
    ]


def test_replaygain_scanner_finds_v2_only_file(legacy_db, tmp_path, monkeypatch):
    from core.repair_jobs.replaygain_filler import ReplayGainFillerJob

    _import(legacy_db)
    audio = tmp_path / "rg-v2-only.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Needs RG")
    monkeypatch.setattr("core.replaygain.is_ffmpeg_available", lambda: True)
    monkeypatch.setattr("core.replaygain.read_replaygain_tags", lambda path: {})
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = ReplayGainFillerJob().scan(context)

    assert result.findings_created == 1
    assert findings[0]["entity_id"] == f"lib2:{track_id}"
    assert findings[0]["details"]["library_v2"]["file_id"] == file_id
    assert findings[0]["details"]["file_path"] == str(audio)


def test_lyrics_scanner_finds_v2_only_file(legacy_db, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core.repair_jobs.missing_lyrics import MissingLyricsJob

    _import(legacy_db)
    audio = tmp_path / "lyrics-v2-only.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Has Remote Lyrics")
    fake_client = SimpleNamespace(
        api=object(),
        has_remote_lyrics=lambda title, *_args: title == "Has Remote Lyrics",
    )
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = MissingLyricsJob().scan(context)

    assert result.findings_created == 1
    assert findings[0]["entity_id"] == f"lib2:{track_id}"
    assert findings[0]["details"]["library_v2"]["file_id"] == file_id
    assert findings[0]["details"]["duration"] == 210


def test_every_registered_job_declares_v2_effects():
    from core.repair_jobs import JOB_DATA_BASIS, JOB_LIBRARY_V2_EFFECTS

    assert set(JOB_LIBRARY_V2_EFFECTS) == set(JOB_DATA_BASIS)
    assert all(effects for effects in JOB_LIBRARY_V2_EFFECTS.values())
