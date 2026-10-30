"""Fix Unknown Artists files the track under its artist, and (upstream's
"reorganize files", on by default) moves it into the matching folder through
Library Reorganize. A media server's library file is never pulled into
SoulSync's folders (upstream test_repair_worker_unknown_artist_path)."""

from types import SimpleNamespace

import pytest

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests import lib2_seed

DETAILS = {"corrected_artist": "New Artist", "corrected_title": "Song", "corrected_album": "Album"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    settings = {"fix_tags": False}
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(transfer)
    worker._config_manager = SimpleNamespace(
        get=lambda key, default=None: settings if key.endswith("unknown_artist_fixer.settings") else default)
    queued = []
    monkeypatch.setattr("core.library2.reorganize_bridge.enqueue_album_reorganize",
                        lambda db_, album_id, **kw: queued.append((album_id, kw)))
    return SimpleNamespace(db=db, worker=worker, transfer=transfer, settings=settings,
                           queued=queued, tmp=tmp_path)


def _placeholder(setup, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 32)
    with setup.db._get_connection() as conn:
        track = lib2_seed.track(conn, "Unknown Artist", "Unknown Album", "Song", path=str(path))
        conn.commit()
    return track


def _album_of(setup, title):
    with setup.db._get_connection() as conn:
        return conn.execute(
            "SELECT al.id FROM lib2_albums al JOIN lib2_artists ar ON ar.id=al.primary_artist_id "
            "WHERE al.title=? AND ar.name='New Artist'", (title,)).fetchone()[0]


def test_a_file_in_the_transfer_folder_is_refiled_and_moved(setup):
    track = _placeholder(setup, setup.transfer / "Unknown" / "song.flac")

    result = setup.worker._fix_unknown_artist("track", f"lib2:{track}", None, DETAILS)

    assert result["success"] is True and result["reorganize"] == "queued"
    assert setup.queued == [(_album_of(setup, "Album"), {"mode": "api"})]


def test_a_media_server_file_is_refiled_but_stays_put(setup):
    outside = setup.tmp / "plex" / "Old" / "song.flac"
    track = _placeholder(setup, outside)

    result = setup.worker._fix_unknown_artist("track", f"lib2:{track}", None, DETAILS)

    assert result["success"] is True and result["reorganize"] == "kept_in_place"
    assert setup.queued == [] and outside.is_file()
    assert _album_of(setup, "Album")


def test_the_setting_off_never_moves(setup):
    setup.settings["reorganize_files"] = False
    track = _placeholder(setup, setup.transfer / "Unknown" / "song.flac")

    result = setup.worker._fix_unknown_artist("track", f"lib2:{track}", None, DETAILS)

    assert result["success"] is True and "reorganize" not in result
    assert setup.queued == []
