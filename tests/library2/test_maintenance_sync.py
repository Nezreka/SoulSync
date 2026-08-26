"""Repair-worker mutations converge into optional Library v2."""

from __future__ import annotations

from core.repair_jobs.base import JobContext, JobResult


class _Config:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def get(self, key, default=None):
        if key == "features.library_v2":
            return self.enabled
        return default

    def set(self, key, value):
        return None


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


def test_deprecated_false_flag_cannot_silence_maintenance_sync(legacy_db):
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

    assert outcome["enabled"] is True
    assert outcome["reason"] == "synchronized"
    conn = legacy_db._get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM lib2_maintenance_events").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_verification_change_updates_v2_file_and_history(legacy_db):
    from core.library2.history_feed import scoped_history
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    conn = legacy_db._get_connection()
    native = conn.execute(
        "SELECT t.id AS track_id, f.id AS file_id FROM lib2_tracks t "
        "JOIN lib2_track_files f ON f.track_id=t.id WHERE t.legacy_track_id=100"
    ).fetchone()
    track_id, file_id = native["track_id"], native["file_id"]
    conn.execute(
        "UPDATE lib2_track_files SET verification_status='verified' WHERE id=?",
        (file_id,),
    )
    conn.commit()
    conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="acoustid_scanner",
        finding_type="acoustid_verification",
        action="verification_status_updated",
        entity_type="track",
        entity_id=f"lib2:{track_id}",
        file_path="/m/01.flac",
        details={"library_v2": {
            "track_id": track_id, "track_ids": [track_id],
            "file_id": file_id, "file_ids": [file_id],
        }},
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
    conn = legacy_db._get_connection()
    native = conn.execute(
        "SELECT t.id AS track_id, f.id AS file_id FROM lib2_tracks t "
        "JOIN lib2_track_files f ON f.track_id=t.id WHERE t.legacy_track_id=100"
    ).fetchone()
    track_id, file_id = native["track_id"], native["file_id"]
    conn.close()
    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="dead_file_cleaner",
        finding_type="dead_file",
        action="redownload",
        entity_type="track",
        entity_id=f"lib2:{track_id}",
        file_path="/m/01.flac",
        details={"library_v2": {
            "track_id": track_id, "track_ids": [track_id],
            "file_id": file_id, "file_ids": [file_id],
        }},
        result={"library_v2_file_deleted": True},
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
    assert event[0] == "redownload"
    assert "file_state" in event[1]


def test_repair_sync_ignores_a_stale_finding_track_id(legacy_db):
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    conn = legacy_db._get_connection()
    native = conn.execute(
        "SELECT t.id AS track_id, f.id AS file_id FROM lib2_tracks t "
        "JOIN lib2_track_files f ON f.track_id=t.id WHERE t.legacy_track_id=100"
    ).fetchone()
    conn.close()

    outcome = sync_repair_change(
        legacy_db, _Config(True), job_id="acoustid_scanner",
        finding_type="acoustid_mismatch", action="retagged",
        entity_type="track", entity_id="lib2:999999", file_path="/m/01.flac",
        details={"library_v2": {
            "track_ids": [999999], "file_ids": [native["file_id"]],
        }},
        result={"library_v2_recompute_wanted": True},
    )

    assert outcome["reason"] == "synchronized"


def test_remove_only_suppresses_wanted_even_for_monitored_track(legacy_db):
    from core.library2.maintenance_sync import sync_repair_change
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule

    _import(legacy_db)
    conn = legacy_db._get_connection()
    native = conn.execute(
        "SELECT t.id AS track_id, f.id AS file_id FROM lib2_tracks t "
        "JOIN lib2_track_files f ON f.track_id=t.id WHERE t.legacy_track_id=100"
    ).fetchone()
    conn.execute("UPDATE lib2_tracks SET monitored=1 WHERE id=?", (native["track_id"],))
    record_rule(conn, "track", native["track_id"], True, PROVENANCE_USER)
    conn.commit()
    conn.close()

    sync_repair_change(
        legacy_db, _Config(True), job_id="dead_file_cleaner",
        finding_type="dead_file", action="removed", entity_type="track",
        entity_id=f"lib2:{native['track_id']}", file_path="/m/01.flac",
        details={"library_v2": {
            "track_id": native["track_id"], "track_ids": [native["track_id"]],
            "file_id": native["file_id"], "file_ids": [native["file_id"]],
        }},
        result={"library_v2_file_deleted": True, "repair_intent": "remove"},
    )

    conn = legacy_db._get_connection()
    try:
        assert conn.execute(
            "SELECT monitored FROM lib2_tracks WHERE id=?", (native["track_id"],)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT wanted FROM lib2_wanted_tracks WHERE profile_id=1 AND track_id=?",
            (native["track_id"],),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_redownload_forces_wanted_even_for_unmonitored_track(legacy_db):
    from core.library2.maintenance_sync import sync_repair_change
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule

    _import(legacy_db)
    conn = legacy_db._get_connection()
    native = conn.execute(
        "SELECT t.id AS track_id, f.id AS file_id FROM lib2_tracks t "
        "JOIN lib2_track_files f ON f.track_id=t.id WHERE t.legacy_track_id=100"
    ).fetchone()
    conn.execute("UPDATE lib2_tracks SET monitored=0 WHERE id=?", (native["track_id"],))
    record_rule(conn, "track", native["track_id"], False, PROVENANCE_USER)
    conn.commit()
    conn.close()

    outcome = sync_repair_change(
        legacy_db, _Config(True), job_id="dead_file_cleaner",
        finding_type="dead_file", action="redownload", entity_type="track",
        entity_id=f"lib2:{native['track_id']}", file_path="/m/01.flac",
        details={"library_v2": {
            "track_id": native["track_id"], "track_ids": [native["track_id"]],
            "file_id": native["file_id"], "file_ids": [native["file_id"]],
        }},
        result={"library_v2_file_deleted": True, "repair_intent": "redownload"},
    )

    assert outcome["repair_intent"] == "redownload"
    conn = legacy_db._get_connection()
    try:
        assert conn.execute(
            "SELECT monitored FROM lib2_tracks WHERE id=?", (native["track_id"],)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT wanted FROM lib2_wanted_tracks WHERE profile_id=1 AND track_id=?",
            (native["track_id"],),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_native_track_number_scan_uses_missing_tracks_in_canonical_album_list(
    legacy_db, tmp_path, monkeypatch
):
    from core.repair_jobs.track_number_repair import TrackNumberRepairJob

    _import(legacy_db)
    audio = tmp_path / "01 - Owned.flac"
    audio.write_bytes(b"not decoded because inspection is stubbed")
    conn = legacy_db._get_connection()
    existing = conn.execute(
        """SELECT t.id AS track_id, t.album_id, f.id AS file_id
             FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id
            WHERE t.legacy_track_id=100"""
    ).fetchone()
    conn.execute(
        "UPDATE lib2_track_files SET path=? WHERE id=?",
        (str(audio), existing["file_id"]),
    )
    missing = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number) "
        "VALUES(?,'Missing Canonical Track',2,1)",
        (existing["album_id"],),
    ).lastrowid
    conn.commit()
    conn.close()
    captured = []
    monkeypatch.setattr(
        "core.library2.completeness.resolve_tracklist",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.repair_jobs.track_number_repair._check_single_track",
        lambda _path, _name, tracks, _similarity: captured.append(tracks) or None,
    )
    context = JobContext(
        db=legacy_db, transfer_folder=str(tmp_path), config_manager=_Config(True),
    )

    TrackNumberRepairJob().scan(context)

    assert captured
    canonical_ids = {row["lib2_track_id"] for row in captured[0]}
    assert existing["track_id"] in canonical_ids
    assert missing in canonical_ids


def test_native_track_number_fix_updates_the_catalogue(legacy_db, tmp_path):
    """The legacy write-through went with the legacy readers (§50.4.4.29) —
    what has to hold is that the number and the path move together on the
    catalogue side, which is the only side left."""
    from core.repair_worker import RepairWorker

    _import(legacy_db)
    original = tmp_path / "01 - Song.flac"
    original.write_bytes(b"tag update intentionally skipped")
    conn = legacy_db._get_connection()
    native = conn.execute(
        """SELECT t.id AS track_id, f.id AS file_id
             FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id
            WHERE t.legacy_track_id=100"""
    ).fetchone()
    conn.execute(
        "UPDATE lib2_track_files SET path=? WHERE id=?",
        (str(original), native["file_id"]),
    )
    conn.commit()
    conn.close()
    worker = RepairWorker(legacy_db, transfer_folder=str(tmp_path))

    result = worker._fix_track_number(
        "track", f"lib2:{native['track_id']}", str(original), {
            "correct_track_num": 2,
            "tag_ok": True,
            "new_filename": "02 - Song.flac",
            "library_v2": {"file_id": native["file_id"]},
        },
    )

    assert result["success"] is True
    renamed = tmp_path / "02 - Song.flac"
    assert renamed.exists() and not original.exists()
    conn = legacy_db._get_connection()
    try:
        v2 = conn.execute(
            "SELECT track_number FROM lib2_tracks WHERE id=?", (native["track_id"],)
        ).fetchone()[0]
        v2_path = conn.execute(
            "SELECT path FROM lib2_track_files WHERE id=?", (native["file_id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert v2 == 2
    assert v2_path == str(renamed)


def test_new_derivative_is_linked_to_same_v2_track(legacy_db, tmp_path):
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    output = tmp_path / "01.mp3"
    output.write_bytes(b"synthetic derivative")
    conn = legacy_db._get_connection()
    try:
        parent = conn.execute(
            "SELECT id, track_id FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
    finally:
        conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="lossy_converter",
        finding_type="missing_lossy_copy",
        action="converted",
        entity_type="track",
        entity_id=100,
        file_path="/m/01.flac",
        result={
            "output_path": str(output),
            "file_role": "derivative",
            "derived_from_file_id": parent["id"],
            "acquired_quality": {
                "format": "flac", "sample_rate": 44100, "bit_depth": 16,
                "bitrate": None,
            },
            "retention_transforms": [{
                "type": "lossy_copy", "source_replaced": False,
                "codec": "mp3", "bitrate": "320",
            }],
        },
    )

    assert outcome["reason"] == "synchronized"
    conn = legacy_db._get_connection()
    try:
        original = conn.execute(
            "SELECT track_id FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
        derivative = conn.execute(
            """SELECT track_id, source, file_role, derived_from_file_id,
                      acquired_quality_json, retention_json
                 FROM lib2_track_files WHERE path=?""",
            (str(output),),
        ).fetchone()
    finally:
        conn.close()
    assert derivative is not None
    assert derivative[0] == original[0]
    assert derivative[1] == "repair_job"
    assert derivative["file_role"] == "derivative"
    assert derivative["derived_from_file_id"] == parent["id"]
    assert derivative["acquired_quality_json"]
    assert derivative["retention_json"]


def test_lossy_source_replacement_stays_active_after_maintenance_sync(legacy_db, tmp_path):
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    output = tmp_path / "01.mp3"
    output.write_bytes(b"replacement")
    conn = legacy_db._get_connection()
    try:
        source = conn.execute(
            "SELECT id, track_id, path FROM lib2_track_files WHERE legacy_track_id=100"
        ).fetchone()
        # The fix worker atomically repoints this same logical file row before
        # the general maintenance convergence callback runs.
        conn.execute(
            "UPDATE lib2_track_files SET path=? WHERE id=?",
            (str(output), source["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="lossy_converter",
        finding_type="missing_lossy_copy",
        action="converted_and_deleted",
        entity_type="track",
        entity_id=f"lib2:{source['track_id']}",
        file_path=source["path"],
        details={"library_v2": {
            "track_id": source["track_id"], "file_id": source["id"],
        }},
        result={
            "output_path": str(output),
            "library_v2_source_replaced": True,
            "file_role": "derivative",
            "acquired_quality": {
                "format": "flac", "sample_rate": 44100, "bit_depth": 16,
                "bitrate": None,
            },
            "retention_transforms": [{
                "type": "lossy_copy", "source_replaced": True,
                "codec": "mp3", "bitrate": "320",
            }],
        },
    )

    assert outcome["reason"] == "synchronized"
    conn = legacy_db._get_connection()
    try:
        rows = conn.execute(
            """SELECT id, path, file_state, file_role, retention_json
                 FROM lib2_track_files WHERE track_id=? AND path=?""",
            (source["track_id"], str(output)),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["id"] == source["id"]
    assert rows[0]["file_state"] == "active"
    assert rows[0]["file_role"] == "derivative"
    assert rows[0]["retention_json"]


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
        entity_id=f"lib2:{album_id}",
    )

    assert outcome["artwork_invalidated"] == 2
    assert not full.exists()
    assert not thumb.exists()


def test_v2_file_subject_enumerator_ignores_deprecated_disable_flag(
    legacy_db, tmp_path,
):
    from core.library2.maintenance_subjects import active_file_subjects

    _import(legacy_db)
    audio = tmp_path / "v2-only.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio)

    disabled_key_subjects = active_file_subjects(legacy_db, _Config(False))
    assert (track_id, file_id) in [
        (row["track_id"], row["file_id"]) for row in disabled_key_subjects
    ]
    subjects = active_file_subjects(legacy_db, _Config(True))
    assert (track_id, file_id) in [
        (row["track_id"], row["file_id"]) for row in subjects
    ]
    assert any(row.get("legacy_track_id") for row in subjects) is False


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


def test_v2_file_subjects_carry_full_track_album_context(legacy_db, tmp_path):
    from core.library2.maintenance_subjects import active_file_subjects

    _import(legacy_db)
    audio = tmp_path / "context.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Context Song")
    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_tracks SET track_number=9, disc_number=2, isrc='ISRC123', "
            "spotify_id='sp-track', musicbrainz_id='mb-track', "
            "external_ids='{\"itunes\":\"777\"}' WHERE id=?",
            (track_id,),
        )
        conn.execute(
            "UPDATE lib2_albums SET image_url='http://album-img', spotify_id='sp-album', "
            "year=2020, track_count=12 WHERE id="
            "(SELECT album_id FROM lib2_tracks WHERE id=?)",
            (track_id,),
        )
        conn.commit()
    finally:
        conn.close()

    subject = next(
        row for row in active_file_subjects(legacy_db, _Config(True))
        if row["file_id"] == file_id
    )
    assert subject["track_number"] == 9
    assert subject["disc_number"] == 2
    assert subject["isrc"] == "ISRC123"
    assert subject["spotify_track_id"] == "sp-track"
    assert subject["musicbrainz_recording_id"] == "mb-track"
    assert subject["itunes_track_id"] == "777"
    assert subject["album_image"] == "http://album-img"
    assert subject["spotify_album_id"] == "sp-album"
    assert subject["album_year"] == 2020
    assert subject["album_track_count"] == 12
    assert subject["is_primary"] == 1


def _add_v2_only_album(legacy_db, path, *, title="V2-only Album"):
    conn = legacy_db._get_connection()
    try:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, spotify_id, image_url) "
            "VALUES('V2 Only Artist','sp-v2-artist','http://artist-img')"
        ).lastrowid
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, spotify_id) "
            "VALUES(?,?, 'sp-v2-album')",
            (artist_id, title),
        ).lastrowid
        conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) "
            "VALUES(?,?,'primary')",
            (album_id, artist_id),
        )
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,'T1',1)",
            (album_id,),
        ).lastrowid
        file_id = conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, file_state, is_primary) "
            "VALUES(?,?, 'active', 1)",
            (track_id, str(path)),
        ).lastrowid
        conn.commit()
        return int(album_id), int(artist_id), int(track_id), int(file_id)
    finally:
        conn.close()


def test_v2_album_subject_enumerator_lists_all_native_albums(legacy_db, tmp_path):
    from core.library2.maintenance_subjects import active_album_subjects

    _import(legacy_db)
    audio = tmp_path / "v2-album-01.flac"
    audio.write_bytes(b"audio")
    album_id, artist_id, _track_id, _file_id = _add_v2_only_album(legacy_db, audio)

    assert album_id in [
        row["album_id"] for row in active_album_subjects(legacy_db, _Config(False))
    ]
    subjects = active_album_subjects(legacy_db, _Config(True))
    assert album_id in [row["album_id"] for row in subjects]
    subject = next(row for row in subjects if row["album_id"] == album_id)
    assert subject["artist_id"] == artist_id
    assert subject["title"] == "V2-only Album"
    assert subject["artist_name"] == "V2 Only Artist"
    assert subject["spotify_album_id"] == "sp-v2-album"
    assert subject["rep_path"] == str(audio)


def test_acoustid_scanner_persists_native_verification_for_v2_only_file(
    legacy_db, tmp_path, monkeypatch,
):
    from types import SimpleNamespace

    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob

    _import(legacy_db)
    audio = tmp_path / "acoustid-v2.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Native Song")
    fake_client = SimpleNamespace(
        fingerprint_and_lookup=lambda path: {
            "recordings": [
                {"title": "Native Song", "artist": "Drake", "duration": 210}
            ],
            "best_score": 0.95,
        }
    )
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        acoustid_client=fake_client,
        create_finding=lambda **kwargs: True,
    )

    result = AcoustIDScannerJob().scan(context)

    assert result.scanned >= 1
    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT verification_status FROM lib2_track_files WHERE id=?", (file_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "verified"


def test_acoustid_scanner_flags_v2_only_mismatch_with_subject(
    legacy_db, tmp_path,
):
    from types import SimpleNamespace

    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob

    _import(legacy_db)
    audio = tmp_path / "acoustid-wrong.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Expected Song")
    fake_client = SimpleNamespace(
        fingerprint_and_lookup=lambda path: {
            "recordings": [
                {"title": "Totally Different", "artist": "Someone Else",
                 "duration": 210}
            ],
            "best_score": 0.97,
        }
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        acoustid_client=fake_client,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = AcoustIDScannerJob().scan(context)

    assert result.findings_created == 1
    assert findings[0]["entity_id"] == f"lib2:{track_id}"
    assert findings[0]["details"]["library_v2"]["file_id"] == file_id


def test_cover_art_scanner_flags_v2_only_album(migrated_legacy_db, tmp_path, monkeypatch):
    """§26 pinned that native rows were padded to the width of a legacy SELECT
    whose optional provider-ID columns only exist on a migrated schema — an
    IndexError waiting on the narrow end of that range.

    The legacy SELECT is gone (the native scan is now the only scan), so the whole
    padding hazard is structurally impossible rather than merely handled. What is
    still worth pinning is the coverage it was protecting: a v2-only album is
    flagged, with its real artist id rather than a padded empty slot."""
    from core.repair_jobs.missing_cover_art import MissingCoverArtJob

    legacy_db = migrated_legacy_db
    _import(legacy_db)
    audio = tmp_path / "v2-cover-01.flac"
    audio.write_bytes(b"audio")
    album_id, artist_id, _track_id, _file_id = _add_v2_only_album(
        legacy_db, audio, title="Artless Album"
    )
    # The source-priority names these used to patch belonged to the legacy
    # projection and are gone with it; the order now travels to the adapter as
    # an argument. The disk checks are imported inside `scan`, so they are
    # patched where they live.
    monkeypatch.setattr(
        "core.metadata.art_apply.file_has_embedded_art", lambda p: False
    )
    monkeypatch.setattr(
        "core.metadata.art_apply.folder_has_cover_sidecar", lambda d: False
    )
    # The native scan's provider seam is the typed adapter, not the old per-source
    # methods — those belonged to the legacy projection that has since been folded
    # away.
    from core.library2.provider_adapters import ArtworkProviderResult

    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_artwork_url",
        lambda kind, **kwargs: (
            ArtworkProviderResult(kind="album", url="http://found-art",
                                  source="spotify",
                                  provider_entity_id="sp-v2-album")
            if kind == "album" else None
        ),
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = MissingCoverArtJob().scan(context)

    assert result.errors == 0
    native = [f for f in findings if f["entity_id"] == f"lib2:{album_id}"]
    assert len(native) == 1
    assert native[0]["entity_type"] == "album"
    assert native[0]["details"]["library_v2"]["album_id"] == album_id
    assert native[0]["details"]["found_artwork_url"] == "http://found-art"
    # The native row carries a real artist, where the padded legacy slot could only
    # ever read as absent.
    assert native[0]["details"]["artist_id"] == artist_id
    # The album's Spotify id reaches the finding as a namespaced provider id rather
    # than through the fixed set of per-source columns the legacy SELECT had to pad
    # out — which is what made the padding fragile in the first place.
    assert native[0]["details"]["provider_ids"]["album"]["spotify"] == "sp-v2-album"
    # Every finding names a native subject now; there is no legacy half left to scan.
    assert all(str(f["entity_id"]).startswith("lib2:") for f in findings)


def test_cover_art_scanner_covers_v2_album_on_unmigrated_legacy_schema(
    legacy_db, tmp_path, monkeypatch
):
    """The other end of what used to be the padding range: zero optional
    provider-ID columns.

    A legacy schema old enough to lack ``albums.spotify_album_id`` used to make the
    legacy SELECT fail, and the point was that the failure cost an error rather than
    the native coverage. With no legacy SELECT there is nothing left to fail, so the
    scan is now simply clean on such a schema — which is the outcome that half of the
    test was defending.
    """
    from core.repair_jobs.missing_cover_art import MissingCoverArtJob

    _import(legacy_db)
    audio = tmp_path / "v2-cover-unmigrated.flac"
    audio.write_bytes(b"audio")
    album_id, _artist_id, _track_id, _file_id = _add_v2_only_album(
        legacy_db, audio, title="Artless Album"
    )
    # The source-priority names these used to patch belonged to the legacy
    # projection and are gone with it; the order now travels to the adapter as
    # an argument. The disk checks are imported inside `scan`, so they are
    # patched where they live.
    monkeypatch.setattr(
        "core.metadata.art_apply.file_has_embedded_art", lambda p: False
    )
    monkeypatch.setattr(
        "core.metadata.art_apply.folder_has_cover_sidecar", lambda d: False
    )
    # The native scan's provider seam is the typed adapter, not the old per-source
    # methods — those belonged to the legacy projection that has since been folded
    # away.
    from core.library2.provider_adapters import ArtworkProviderResult

    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_artwork_url",
        lambda kind, **kwargs: (
            ArtworkProviderResult(kind="album", url="http://found-art",
                                  source="spotify",
                                  provider_entity_id="sp-v2-album")
            if kind == "album" else None
        ),
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = MissingCoverArtJob().scan(context)

    assert result.errors == 0  # nothing reads the legacy schema any more
    assert f"lib2:{album_id}" in [f["entity_id"] for f in findings]


def test_cover_art_fix_applies_natively_to_v2_album(legacy_db, tmp_path):
    from core.repair_worker import RepairWorker

    _import(legacy_db)
    audio = tmp_path / "v2-cover-fix.flac"
    audio.write_bytes(b"audio")
    album_id, artist_id, _track_id, _file_id = _add_v2_only_album(
        legacy_db, audio, title="Fix Album"
    )
    worker = RepairWorker(database=legacy_db, transfer_folder=str(tmp_path))
    worker._config_manager = _Config(True)

    result = worker._fix_missing_cover_art(
        "album", f"lib2:{album_id}", None,
        {"found_artwork_url": "http://new-art", "album_title": "Fix Album"},
    )

    assert result["success"] is True, result
    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT image_url FROM lib2_albums WHERE id=?", (album_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "http://new-art"


class _ToolConfig(_Config):
    """_Config plus arbitrary extra keys for tool-specific settings."""

    def __init__(self, enabled: bool = True, extra: dict | None = None):
        super().__init__(enabled)
        self.extra = extra or {}

    def get(self, key, default=None):
        if key in self.extra:
            return self.extra[key]
        return super().get(key, default)


def test_corruption_scanner_covers_v2_only_file(legacy_db, tmp_path, monkeypatch):
    from core.repair_jobs import audio_corruption_detector as mod

    _import(legacy_db)
    audio = tmp_path / "v2-corrupt.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Damaged")
    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "check_flac_integrity", lambda path: (False, "bad frame"))
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    mod.AudioCorruptionDetectorJob().scan(context)

    native = [f for f in findings if f["entity_id"] == f"lib2:{track_id}"]
    assert len(native) == 1
    assert native[0]["details"]["library_v2"]["file_id"] == file_id


def test_preview_scanner_covers_v2_only_file(legacy_db, tmp_path, monkeypatch):
    from core.repair_jobs.short_preview_track import ShortPreviewTrackJob

    _import(legacy_db)
    audio = tmp_path / "v2-preview.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Clip")
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_tracks SET duration=25000 WHERE id=?", (track_id,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        ShortPreviewTrackJob, "_lookup_source",
        lambda self, context, row: {"duration_s": 200.0, "album_image": None},
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    ShortPreviewTrackJob().scan(context)

    native = [f for f in findings if f["entity_id"] == f"lib2:{track_id}"]
    assert len(native) == 1
    assert native[0]["details"]["library_v2"]["file_id"] == file_id


def test_lossy_converter_covers_v2_only_file(legacy_db, tmp_path):
    from core.repair_jobs.lossy_converter import LossyConverterJob

    _import(legacy_db)
    audio = tmp_path / "v2-lossless.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Lossless Only")
    conn = legacy_db._get_connection()
    try:
        profile_id = conn.execute(
            "SELECT quality_profile_id FROM lib2_tracks WHERE id=?", (track_id,)
        ).fetchone()[0]
        conn.execute(
            """UPDATE quality_profiles
                  SET lossy_copy_enabled=1, lossy_copy_codec='mp3',
                      lossy_copy_bitrate='320'
                WHERE id=?""",
            (profile_id,),
        )
        conn.execute(
            "UPDATE lib2_tracks SET quality_profile_explicit=1 WHERE id=?",
            (track_id,),
        )
        conn.commit()
    finally:
        conn.close()
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_ToolConfig(True, {
            "lossy_copy.enabled": True,
            "lossy_copy.codec": "mp3",
            "lossy_copy.bitrate": "320",
        }),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    LossyConverterJob().scan(context)

    native = [f for f in findings if f["entity_id"] == f"lib2:{track_id}"]
    assert len(native) == 1
    assert native[0]["details"]["library_v2"]["file_id"] == file_id


def test_fake_lossless_scanner_covers_v2_only_file(legacy_db, tmp_path, monkeypatch):
    from core.repair_jobs import fake_lossless_detector as mod

    _import(legacy_db)
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    audio = tmp_path / "v2-fake.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Fake Lossless")
    monkeypatch.setattr(mod, "_is_ffprobe_available", lambda: True)
    monkeypatch.setattr(
        mod, "_analyze_file",
        lambda path: {"sample_rate": 44100, "detected_cutoff_khz": 10.0,
                      "bit_depth": 16, "bitrate": 900000},
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(transfer),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    mod.FakeLosslessDetectorJob().scan(context)

    native = [f for f in findings if f["file_path"] == str(audio)]
    assert len(native) == 1
    assert native[0]["details"]["library_v2"]["file_id"] == file_id


def test_metadata_gap_scanner_covers_v2_only_track(
    migrated_legacy_db, tmp_path, monkeypatch
):
    """Migrated schema on purpose — see the cover-art scanner test above."""  # noqa: D401
    from types import SimpleNamespace

    from core.repair_jobs.metadata_gap_filler import MetadataGapFillerJob

    legacy_db = migrated_legacy_db
    _import(legacy_db)
    audio = tmp_path / "v2-gap.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio, title="Gapped Song")
    monkeypatch.setattr(
        "core.repair_jobs.metadata_gap_filler.get_primary_source", lambda: "spotify"
    )
    monkeypatch.setattr(
        "core.repair_jobs.metadata_gap_filler.get_source_priority",
        lambda primary: ["spotify"],
    )
    fake_mb = SimpleNamespace(
        search_recording=lambda title, artist_name=None, limit=1: [{"id": "mb-999"}]
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        mb_client=fake_mb,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = MetadataGapFillerJob().scan(context)

    assert result.errors == 0
    native = [f for f in findings if f["entity_id"] == f"lib2:{track_id}"]
    assert len(native) == 1
    assert native[0]["details"]["found_fields"]["musicbrainz_recording_id"] == "mb-999"
    assert native[0]["details"]["library_v2"]["track_id"] == track_id
    # The native row carries a real artist, where the padded legacy slot could only
    # ever read as absent.
    assert native[0]["details"]["artist_id"] is not None
    # The native subject reports only the ids it actually has, where the padded
    # legacy row carried a fixed-width dict of Nones.
    assert native[0]["details"]["track_ids"] == {}
    # Every finding names a native subject now; there is no legacy half left to scan.
    assert all(str(f["entity_id"]).startswith("lib2:") for f in findings)


def test_metadata_gap_scanner_covers_v2_track_on_unmigrated_legacy_schema(
    legacy_db, tmp_path, monkeypatch
):
    """Zero optional provider-ID columns — see the cover-art counterpart. With the
    legacy SELECT gone there is nothing left to fail on such a schema."""
    from types import SimpleNamespace

    from core.repair_jobs.metadata_gap_filler import MetadataGapFillerJob

    _import(legacy_db)
    audio = tmp_path / "v2-gap-unmigrated.flac"
    audio.write_bytes(b"audio")
    track_id, _file_id = _add_v2_only_file(legacy_db, audio, title="Gapped Song")
    monkeypatch.setattr(
        "core.repair_jobs.metadata_gap_filler.get_primary_source", lambda: "spotify"
    )
    monkeypatch.setattr(
        "core.repair_jobs.metadata_gap_filler.get_source_priority",
        lambda primary: ["spotify"],
    )
    fake_mb = SimpleNamespace(
        search_recording=lambda title, artist_name=None, limit=1: [{"id": "mb-999"}]
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        mb_client=fake_mb,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = MetadataGapFillerJob().scan(context)

    assert result.errors == 0  # nothing reads the legacy schema any more
    assert f"lib2:{track_id}" in [f["entity_id"] for f in findings]


def test_metadata_gap_fix_writes_natively_to_v2_track(legacy_db, tmp_path):
    from core.repair_worker import RepairWorker

    _import(legacy_db)
    audio = tmp_path / "v2-gap-fix.flac"
    audio.write_bytes(b"audio")
    track_id, _file_id = _add_v2_only_file(legacy_db, audio, title="Gap Fix")
    worker = RepairWorker(database=legacy_db, transfer_folder=str(tmp_path))
    worker._config_manager = _Config(True)

    result = worker._fix_metadata_gap(
        "track", f"lib2:{track_id}", None,
        {"found_fields": {"isrc": "DE1234567890",
                          "musicbrainz_recording_id": "mb-42"}},
    )

    assert result["success"] is True, result
    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT isrc, musicbrainz_id FROM lib2_tracks WHERE id=?", (track_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "DE1234567890"
    assert row[1] == "mb-42"


def test_corrupt_fix_deletes_v2_only_file_for_native_wanted_sync(legacy_db, tmp_path):
    from core.repair_worker import RepairWorker

    _import(legacy_db)
    audio = tmp_path / "v2-corrupt-fix.flac"
    audio.write_bytes(b"audio")
    track_id, _file_id = _add_v2_only_file(legacy_db, audio, title="Corrupt Fix")
    wishlisted = []
    legacy_db.add_to_wishlist = (
        lambda payload, **kwargs: wishlisted.append(payload) or True
    )
    worker = RepairWorker(database=legacy_db, transfer_folder=str(tmp_path))
    worker._config_manager = _Config(True)

    result = worker._fix_corrupt_audio(
        "track", f"lib2:{track_id}", str(audio), {"reason": "bad frame"},
    )

    assert result["success"] is True, result
    assert result["library_v2_file_deleted"] is True
    assert not audio.exists()
    assert wishlisted == []


def test_preview_fix_deletes_v2_only_file_for_native_wanted_sync(legacy_db, tmp_path):
    from core.repair_worker import RepairWorker

    _import(legacy_db)
    audio = tmp_path / "v2-preview-fix.flac"
    audio.write_bytes(b"audio")
    track_id, _file_id = _add_v2_only_file(legacy_db, audio, title="Preview Fix")
    wishlisted = []
    legacy_db.add_to_wishlist = (
        lambda payload, **kwargs: wishlisted.append(payload) or True
    )
    worker = RepairWorker(database=legacy_db, transfer_folder=str(tmp_path))
    worker._config_manager = _Config(True)

    result = worker._fix_short_preview_track(
        "track", f"lib2:{track_id}", str(audio),
        {"expected_duration_s": 200.0},
    )

    assert result["success"] is True, result
    assert result["library_v2_file_deleted"] is True
    assert not audio.exists()
    assert wishlisted == []


def test_tag_consistency_scanner_covers_v2_only_album(legacy_db, tmp_path, monkeypatch):
    from core.repair_jobs import album_tag_consistency as mod

    _import(legacy_db)
    audio_a = tmp_path / "v2-tags-01.flac"
    audio_a.write_bytes(b"audio")
    album_id, artist_id, track_a, _file_a = _add_v2_only_album(
        legacy_db, audio_a, title="Split Album"
    )
    audio_b = tmp_path / "v2-tags-02.flac"
    audio_b.write_bytes(b"audio")
    conn = legacy_db._get_connection()
    track_b = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,'T2',2)",
        (album_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, file_state, is_primary) "
        "VALUES(?,?, 'active', 1)",
        (track_b, str(audio_b)),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mod, "MutagenFile", lambda path, easy=False: path)

    def fake_read(audio, tag_name):
        if tag_name == "album":
            return "Version A" if str(audio).endswith("01.flac") else "Version B"
        return None

    monkeypatch.setattr(mod, "_read_tag", fake_read)
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    mod.AlbumTagConsistencyJob().scan(context)

    native = [f for f in findings if f["entity_id"] == f"lib2:{album_id}"]
    assert len(native) == 1
    assert native[0]["details"]["library_v2"]["album_id"] == album_id
    fields = {inc["field"] for inc in native[0]["details"]["inconsistencies"]}
    assert "album" in fields


def test_track_number_repair_reaches_v2_only_files(legacy_db, tmp_path, monkeypatch):
    """A v2-only file must be inspected.

    The check used to be "the scan walks the folder the file sits in", because the
    scan enumerated directories. The native scan enumerates file subjects instead —
    the folder is now incidental — so the guarantee is stated against the file, which
    is what it was always about.
    """
    from core.repair_jobs.track_number_repair import TrackNumberRepairJob

    _import(legacy_db)
    music = tmp_path / "music"
    music.mkdir()
    audio = music / "01 - Song.flac"
    audio.write_bytes(b"audio")
    _add_v2_only_file(legacy_db, audio, title="Song")
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    inspected = []
    monkeypatch.setattr(
        "core.library2.completeness.resolve_tracklist",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.repair_jobs.track_number_repair._check_single_track",
        lambda path, name, _tracks, _similarity: inspected.append((path, name)) or None,
    )
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(transfer),
        config_manager=_Config(True),
    )

    TrackNumberRepairJob().scan(context)

    assert (str(audio), "01 - Song.flac") in inspected


def test_every_registered_job_declares_v2_effects():
    from core.repair_jobs import JOB_DATA_BASIS, JOB_LIBRARY_V2_EFFECTS

    assert set(JOB_LIBRARY_V2_EFFECTS) == set(JOB_DATA_BASIS)
    assert all(effects for effects in JOB_LIBRARY_V2_EFFECTS.values())


def test_legacy_album_finding_still_converges_into_v2(legacy_db, tmp_path):
    """issues.md T-01: a finding created before the P3 cutover (or by a job that
    still scans legacy rows) carries a bare legacy album id and no
    ``details['library_v2']``.  The importer stored ``legacy_album_id`` on the
    native row, so the subject IS resolvable — refusing to use that backref
    left the physical repair done and the lib2 tag/gap cache stale forever."""
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    conn = legacy_db._get_connection()
    try:
        native_album_id = conn.execute(
            "SELECT id FROM lib2_albums WHERE legacy_album_id=10"
        ).fetchone()[0]
    finally:
        conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="album_tag_consistency",
        finding_type="album_tag_inconsistency",
        action="fixed_album_tags",
        entity_type="album",
        entity_id="10",
        file_path=None,
        details={},
    )

    assert outcome["reason"] == "synchronized"
    assert outcome["albums"] == 1
    conn = legacy_db._get_connection()
    try:
        event = conn.execute(
            "SELECT lib2_album_id FROM lib2_maintenance_events "
            "WHERE job_id='album_tag_consistency' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert event is not None and int(event[0]) == int(native_album_id)


def test_legacy_track_finding_still_converges_into_v2(legacy_db):
    """Same backref rescue for a track-scoped legacy finding (library_reorganize
    'path_mismatch' rows in the production DB look exactly like this)."""
    from core.library2.maintenance_sync import annotate_finding_details

    _import(legacy_db)
    details = annotate_finding_details(
        legacy_db,
        _Config(True),
        entity_type="track",
        entity_id="101",          # legacy tracks.id, no file on disk
        file_path=None,
        details={},
    )

    conn = legacy_db._get_connection()
    try:
        native_track_id = conn.execute(
            "SELECT id FROM lib2_tracks WHERE legacy_track_id=101"
        ).fetchone()[0]
    finally:
        conn.close()
    assert details["library_v2"]["track_id"] == int(native_track_id)


def test_legacy_id_lookup_never_coerces_ids_to_int(legacy_db):
    """Guide §5: legacy ids are opaque TEXT (soulsync/Deezer-generated rows are
    not numeric).  The backref lookup must compare as text and must not raise."""
    from core.library2.maintenance_sync import annotate_finding_details

    _import(legacy_db)
    details = annotate_finding_details(
        legacy_db,
        _Config(True),
        entity_type="album",
        entity_id="sp:6deadbeef",
        file_path=None,
        details={},
    )
    assert "library_v2" not in details


def test_unlinked_subject_is_reported_to_the_fix_caller(legacy_db):
    """issues.md T-02: 'repaired on disk, catalogue untouched' must be
    distinguishable from a full convergence."""
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="empty_folder_cleaner",
        action="deleted",
        entity_type="folder",
        entity_id="/nowhere",
        file_path=None,
        details={},
    )
    assert outcome["reason"] == "subject_unlinked"
    assert outcome["converged"] is False


def test_artist_subject_pulls_its_files_for_a_tag_repair(legacy_db, tmp_path):
    """T-11 — the comma-artist split re-tags every file under one artist.

    The finding names only the artist, so without an artist -> files fan-out
    ``rescan_files`` never runs and the lib2 tag snapshot keeps showing the old
    combined artist right after the repair rewrote it on disk.
    """
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    real_file = tmp_path / "credited.flac"
    real_file.write_bytes(b"audio")
    _add_v2_only_file(legacy_db, real_file, title="Credited Song")
    conn = legacy_db._get_connection()
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()[0]
    conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="comma_artist_splitter",
        finding_type="comma_artist_split",
        action="artists_split",
        entity_type="artist",
        entity_id=f"lib2:{artist_id}",
    )

    assert outcome["reason"] == "synchronized"
    assert outcome["artists"] == 1
    assert outcome["files"] >= 1
    assert outcome["scan"]["scanned"] >= 1


def test_artist_subject_of_a_metadata_only_repair_stays_narrow(legacy_db):
    """The same fan-out must not fire for a catalogue-only repair.

    Genre Tag Cleanup rewrites one ``lib2_artists.genres`` column and touches
    no file, so widening its subject to the artist's whole discography would
    re-probe every file for nothing (BR-08's idle-query flood).
    """
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    conn = legacy_db._get_connection()
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE legacy_artist_id=1"
    ).fetchone()[0]
    conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="genre_cleanup",
        finding_type="genre_cleanup",
        action="genres_cleaned",
        entity_type="artist",
        entity_id=f"lib2:{artist_id}",
    )

    assert outcome["reason"] == "synchronized"
    assert outcome["artists"] == 1
    assert outcome["files"] == 0
    assert outcome["scan"]["scanned"] == 0


# --- iss29-E03: an album-scoped delete must retire ONE file, not the album ---


def test_album_scoped_delete_retires_only_the_file_the_repair_removed(legacy_db):
    """One removed file must not mark every file of the album deleted.

    ``_fix_unwanted_content`` deletes exactly one file and reports
    ``removed_content`` — a delete action. Its findings carry
    ``entity_type='album'`` because that is how ``live_commentary_cleaner``
    creates them (and that job is not retired, so an upgrade brings open ones
    along). ``_resolve_links`` widens an album subject to every track and every
    live file of the album so a rescan covers them, and the delete branch used
    that same widened set.

    The consequence is not cosmetic: the album ends up with no live files, so
    ``recompute_wanted`` wants the whole album again and the wishlist
    re-downloads a release that is sitting complete on disk, while the real
    files look like orphans to the dead-file cleaner.
    """
    from core.library2.maintenance_sync import sync_repair_change

    _import(legacy_db)
    # The seed album has a single file; a second one is what makes the
    # over-wide delete observable at all.
    _add_v2_only_file(legacy_db, "/m/02.flac", title="Second Track")
    conn = legacy_db._get_connection()
    try:
        album_id = conn.execute(
            "SELECT id FROM lib2_albums WHERE legacy_album_id=10"
        ).fetchone()[0]
        target = conn.execute(
            "SELECT f.id AS file_id, f.path AS path FROM lib2_track_files f "
            "JOIN lib2_tracks t ON t.id=f.track_id WHERE t.legacy_track_id=100"
        ).fetchone()
        sibling_files = conn.execute(
            "SELECT f.id FROM lib2_track_files f JOIN lib2_tracks t ON t.id=f.track_id "
            "WHERE t.album_id=? AND f.id<>?",
            (album_id, target["file_id"]),
        ).fetchall()
    finally:
        conn.close()

    outcome = sync_repair_change(
        legacy_db,
        _Config(True),
        job_id="live_commentary_cleaner",
        finding_type="unwanted_content",
        action="removed_content",
        entity_type="album",
        entity_id=f"lib2:{album_id}",
        file_path=target["path"],
        details={},
        result={"success": True, "action": "removed_content"},
    )

    assert outcome["reason"] == "synchronized"
    conn = legacy_db._get_connection()
    try:
        removed = conn.execute(
            "SELECT file_state FROM lib2_track_files WHERE id=?", (target["file_id"],)
        ).fetchone()[0]
        survivors = [
            conn.execute(
                "SELECT COALESCE(file_state,'active') FROM lib2_track_files WHERE id=?",
                (row[0],),
            ).fetchone()[0]
            for row in sibling_files
        ]
    finally:
        conn.close()

    assert removed == "deleted"
    assert survivors, "fixture must have at least one other file on the album"
    assert all(state != "deleted" for state in survivors), (
        f"the rest of the album was retired too: {survivors}"
    )


# ── BUG-01: the path-mapping fallback must be BOUNDED ───────────────────────
#
# `_resolve_links` falls back to a resolver pass when a finding names a path the
# catalogue does not store verbatim (path-mapped / media-server installs). That
# fallback used to read EVERY non-deleted row in `lib2_track_files` and call
# `resolve_lib2_path` on each — per finding. A scan producing 2,000 orphans
# against 50,000 file rows did 100 million path resolutions, each with at least
# one `stat` over the network, to return an empty link set every single time.


def _paths_db(tmp_path, paths):
    import sqlite3
    from core.library2.schema import ensure_library_v2_schema

    conn = sqlite3.connect(str(tmp_path / "paths.db"))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('A')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'X')",
        (artist,)).lastrowid
    ids = {}
    for path in paths:
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)", (album, path)).lastrowid
        ids[path] = conn.execute(
            "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
            "VALUES(?,?,1,'active')", (track, path)).lastrowid
    conn.commit()
    return conn, ids


def test_mapped_path_lookup_probes_by_basename_not_by_full_scan(tmp_path):
    from core.library2.maintenance_sync import _files_by_mapped_path

    paths = [
        "/music/Artist/Album/01 - Song.flac",
        r"D:\Music\Artist\Album\02 - Other.flac",   # Windows separator
        "/music/Artist/Album/100% Real_Deal.flac",  # LIKE wildcards in the name
        "/music/Other/Album/01 - Song.flac",        # same basename, other folder
    ]
    conn, ids = _paths_db(tmp_path, paths)
    try:
        for probe in paths[:3]:
            found = _files_by_mapped_path(conn, {probe}, config_manager=None)
            assert found == [ids[probe]], probe
    finally:
        conn.close()


def test_a_subject_that_cannot_have_a_catalogue_row_never_probes(tmp_path):
    """An orphan file is DEFINED as one the catalogue does not know, and an
    empty-folder subject is a directory — `lib2_track_files.path` only ever
    holds files. Both used to pay for the whole-library walk to prove it."""
    from core.library2.maintenance_sync import _may_have_catalogue_row

    assert _may_have_catalogue_row("file", None) is False      # orphan_file
    assert _may_have_catalogue_row("folder", "/music/Empty") is False  # empty_folder
    # A real catalogue subject still probes.
    assert _may_have_catalogue_row("file", "lib2:5") is True
    assert _may_have_catalogue_row("track", "lib2:5") is True
    # ...and so does a bare legacy back-reference, which may resolve.
    assert _may_have_catalogue_row("file", "123") is True
