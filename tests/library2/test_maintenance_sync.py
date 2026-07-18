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
        entity_id=f"lib2:{album_id}",
    )

    assert outcome["artwork_invalidated"] == 2
    assert not full.exists()
    assert not thumb.exists()


def test_v2_file_subject_enumerator_is_gated_and_lists_the_full_native_catalogue(
    legacy_db, tmp_path,
):
    from core.library2.maintenance_subjects import active_file_subjects

    _import(legacy_db)
    audio = tmp_path / "v2-only.flac"
    audio.write_bytes(b"audio")
    track_id, file_id = _add_v2_only_file(legacy_db, audio)

    assert active_file_subjects(legacy_db, _Config(False)) == []
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

    assert active_album_subjects(legacy_db, _Config(False)) == []
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


def test_cover_art_scanner_flags_v2_only_album(legacy_db, tmp_path, monkeypatch):
    from core.repair_jobs.missing_cover_art import MissingCoverArtJob

    _import(legacy_db)
    audio = tmp_path / "v2-cover-01.flac"
    audio.write_bytes(b"audio")
    album_id, artist_id, _track_id, _file_id = _add_v2_only_album(
        legacy_db, audio, title="Artless Album"
    )
    monkeypatch.setattr(
        "core.repair_jobs.missing_cover_art.get_primary_source", lambda: "spotify"
    )
    monkeypatch.setattr(
        "core.repair_jobs.missing_cover_art.get_source_priority",
        lambda primary: ["spotify"],
    )
    monkeypatch.setattr(
        "core.repair_jobs.missing_cover_art.file_has_embedded_art", lambda p: False
    )
    monkeypatch.setattr(
        "core.repair_jobs.missing_cover_art.folder_has_cover_sidecar", lambda d: False
    )
    monkeypatch.setattr(
        MissingCoverArtJob, "_try_source",
        lambda self, *args, **kwargs: "http://found-art",
    )
    monkeypatch.setattr(
        MissingCoverArtJob, "_find_artist_art", lambda self, *args, **kwargs: None
    )
    findings = []
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    MissingCoverArtJob().scan(context)

    native = [f for f in findings if f["entity_id"] == f"lib2:{album_id}"]
    assert len(native) == 1
    assert native[0]["entity_type"] == "album"
    assert native[0]["details"]["library_v2"]["album_id"] == album_id
    assert native[0]["details"]["found_artwork_url"] == "http://found-art"


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


def test_metadata_gap_scanner_covers_v2_only_track(legacy_db, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core.repair_jobs.metadata_gap_filler import MetadataGapFillerJob

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

    MetadataGapFillerJob().scan(context)

    native = [f for f in findings if f["entity_id"] == f"lib2:{track_id}"]
    assert len(native) == 1
    assert native[0]["details"]["found_fields"]["musicbrainz_recording_id"] == "mb-999"
    assert native[0]["details"]["library_v2"]["track_id"] == track_id


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


def test_track_number_repair_visits_v2_only_folders(legacy_db, tmp_path, monkeypatch):
    from core.repair_jobs.track_number_repair import TrackNumberRepairJob

    _import(legacy_db)
    music = tmp_path / "music"
    music.mkdir()
    audio = music / "01 - Song.flac"
    audio.write_bytes(b"audio")
    _add_v2_only_file(legacy_db, audio, title="Song")
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    visited = []
    monkeypatch.setattr(
        TrackNumberRepairJob, "_repair_album",
        lambda self, folder, filenames, *args, **kwargs: (
            visited.append((folder, tuple(sorted(filenames)))) or JobResult()
        ),
    )
    context = JobContext(
        db=legacy_db,
        transfer_folder=str(transfer),
        config_manager=_Config(True),
    )

    TrackNumberRepairJob().scan(context)

    assert (str(music), ("01 - Song.flac",)) in visited


def test_every_registered_job_declares_v2_effects():
    from core.repair_jobs import JOB_DATA_BASIS, JOB_LIBRARY_V2_EFFECTS

    assert set(JOB_LIBRARY_V2_EFFECTS) == set(JOB_DATA_BASIS)
    assert all(effects for effects in JOB_LIBRARY_V2_EFFECTS.values())
