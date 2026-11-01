"""Approving a Fake Lossless Detector finding (upstream's "Re-download FLAC").

Upstream keeps the transcode and adds a wishlist row against legacy ``tracks``.
In Library v2 a track whose FLAC is present is not wanted -- the projection
trusts the claimed format, the very thing a transcode lies about -- so the
fix moves the file to the deleted-files quarantine through the delete journal
and re-wants the track. The finding names the FILE (``lib2:<file id>``), and
only that file may leave the catalogue."""
from __future__ import annotations

from pathlib import Path

from core.library.deleted_quarantine import list_entries
from core.repair_worker import FINDING_TYPE_META, RepairWorker
from database.music_database import MusicDatabase


class _Config:
    def __init__(self, transfer):
        self.transfer = str(transfer)

    def get(self, key, default=None):
        if key == "library.music_paths":
            return [self.transfer]
        if key == "soulseek.transfer_path":
            return self.transfer
        return default


def _library(tmp_path: Path):
    transfer = tmp_path / "Transfer"
    album_dir = transfer / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    fake = album_dir / "01 - Song.flac"
    fake.write_bytes(b"transcoded audio")
    sibling = album_dir / "02 - Other.flac"
    sibling.write_bytes(b"real audio")
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = db._get_connection()
    artist = conn.execute(
        "INSERT INTO lib2_artists (name, name_key) VALUES ('Artist', 'artist')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums (primary_artist_id, title, origin) VALUES (?, 'Album', 'library')",
        (artist,)).lastrowid
    ids = {}
    for name, path in (("song", fake), ("other", sibling)):
        track = conn.execute(
            "INSERT INTO lib2_tracks (album_id, title, duration, monitored) VALUES (?, ?, 200000, 0)",
            (album, name.title())).lastrowid
        file_id = conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary, format) VALUES (?, ?, 1, 'flac')",
            (track, str(path))).lastrowid
        ids[name] = (track, file_id)
    conn.commit()
    conn.close()
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(transfer)
    worker._config_manager = _Config(transfer)
    return worker, transfer, fake, sibling, ids


def _details(file_id, track_id):
    return {"library_v2_native": True,
            "library_v2": {"file_id": file_id, "track_id": track_id,
                           "file_ids": [file_id], "track_ids": [track_id]}}


def _raise(worker, entity_id, path, details):
    worker._create_finding(
        job_id="fake_lossless_detector", finding_type="fake_lossless", severity="warning",
        entity_type="file", entity_id=entity_id, file_path=str(path),
        title="Possible fake lossless", description="cutoff at 16 kHz", details=details)
    conn = worker.db._get_connection()
    try:
        return conn.execute("SELECT MAX(id) FROM repair_findings").fetchone()[0]
    finally:
        conn.close()


def _one(db, sql, *args):
    conn = db._get_connection()
    try:
        row = conn.execute(sql, args).fetchone()
        return tuple(row) if row is not None else None
    finally:
        conn.close()


def test_fake_lossless_has_a_destructive_redownload_fix():
    from core.repair_worker import DESTRUCTIVE_FINDING_TYPES

    assert FINDING_TYPE_META["fake_lossless"]["verb"] == "Re-download"
    assert "fake_lossless" in DESTRUCTIVE_FINDING_TYPES
    assert "fake_lossless" in RepairWorker.__new__(RepairWorker)._fix_handlers()


def test_approving_quarantines_the_file_and_wants_the_track_again(tmp_path: Path):
    worker, transfer, fake, sibling, ids = _library(tmp_path)
    track, file_id = ids["song"]
    finding = _raise(worker, f"lib2:{file_id}", fake, _details(file_id, track))

    result = worker.fix_finding(finding)

    assert result["success"] is True, result
    assert result["repair_intent"] == "redownload"
    assert not fake.exists() and sibling.exists()
    entries = list_entries(str(transfer))["entries"]
    assert [(e["original_path"], e["source"]) for e in entries] == [(str(fake), "fake_lossless")]
    assert _one(worker.db, "SELECT mode, actor, entity_type FROM lib2_file_delete_operations") == (
        "quarantine", "repair:fake_lossless", "albums")
    assert _one(worker.db, "SELECT file_state FROM lib2_track_files WHERE id=?", file_id)[0] == "deleted"
    other_track, other_file = ids["other"]
    assert _one(worker.db, "SELECT COALESCE(file_state,'active') FROM lib2_track_files WHERE id=?",
                other_file)[0] == "active"
    assert _one(worker.db, "SELECT monitored FROM lib2_tracks WHERE id=?", track)[0] == 1
    assert _one(worker.db, "SELECT monitored FROM lib2_tracks WHERE id=?", other_track)[0] == 0
    assert _one(worker.db, "SELECT status FROM repair_findings WHERE id=?", finding)[0] == "resolved"


def test_delete_without_replacement_unmonitors_a_track_left_without_files(tmp_path: Path):
    worker, transfer, fake, _sibling, ids = _library(tmp_path)
    track, file_id = ids["song"]

    result = worker._fix_fake_lossless(
        "file", f"lib2:{file_id}", str(fake), {**_details(file_id, track), "_fix_action": "delete"})

    assert result["success"] is True
    assert result["action"] == "deleted_file"
    assert result["repair_intent"] == "remove"
    assert not fake.exists()
    assert [e["source"] for e in list_entries(str(transfer))["entries"]] == ["fake_lossless"]


def test_a_track_with_another_usable_file_is_not_unmonitored(tmp_path: Path):
    worker, _transfer, fake, _sibling, ids = _library(tmp_path)
    track, file_id = ids["song"]
    mp3 = fake.with_suffix(".mp3")
    mp3.write_bytes(b"lossy copy")
    conn = worker.db._get_connection()
    conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary, format) "
                 "VALUES (?, ?, 0, 'mp3')", (track, str(mp3)))
    conn.commit()
    conn.close()

    result = worker._fix_fake_lossless(
        "file", f"lib2:{file_id}", str(fake), {**_details(file_id, track), "_fix_action": "delete"})

    assert result["success"] is True and "repair_intent" not in result
    assert mp3.exists()


def test_a_finding_whose_id_names_another_file_is_refused(tmp_path: Path):
    worker, _transfer, fake, sibling, ids = _library(tmp_path)
    track, file_id = ids["song"]
    _other_track, other_file = ids["other"]

    result = worker._fix_fake_lossless(
        "file", f"lib2:{other_file}", str(fake), _details(file_id, track))

    assert result["success"] is False
    assert fake.exists() and sibling.exists()


def test_a_file_no_longer_in_the_catalogue_retires_the_finding(tmp_path: Path):
    worker, _transfer, fake, _sibling, ids = _library(tmp_path)
    track, file_id = ids["song"]
    conn = worker.db._get_connection()
    conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE id=?", (file_id,))
    conn.commit()
    conn.close()

    result = worker._fix_fake_lossless("file", f"lib2:{file_id}", str(fake), _details(file_id, track))

    assert result["success"] is False and result["stale"] is True
    assert fake.exists()


def test_an_uncatalogued_file_is_only_removed_when_asked(tmp_path: Path):
    worker, transfer, _fake, _sibling, _ids = _library(tmp_path)
    stray = transfer / "stray.flac"
    stray.write_bytes(b"transcoded")

    refused = worker._fix_fake_lossless("file", None, str(stray), {})
    assert refused["success"] is False and stray.exists()

    removed = worker._fix_fake_lossless("file", None, str(stray), {"_fix_action": "delete"})
    assert removed["success"] is True and not stray.exists()
    assert [e["source"] for e in list_entries(str(transfer))["entries"]] == ["fake_lossless"]
