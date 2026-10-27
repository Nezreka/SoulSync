"""a song SoulSync downloaded counts as owned before the media server scans it
in. a sync or wishlist run in that gap fetched it again, so the same song
landed as an .mp3 and a .flac, each with its own review row."""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _record_download(db, path, title, artist, album="insomnia", status="completed"):
    with db._get_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO track_downloads (file_path, source_service, track_title, "
            "track_artist, track_album, status) VALUES (?, 'deezer', ?, ?, ?, ?)",
            (str(path), title, artist, album, status))
        conn.commit()


def _song(tmp_path, name="01 - early breeze.mp3"):
    path = tmp_path / "Petit Biscuit" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_downloaded_file_not_yet_scanned_is_owned(db, tmp_path):
    path = _song(tmp_path)
    _record_download(db, path, "early breeze", "Petit Biscuit")

    track, confidence = db.check_track_exists("Early Breeze", "Petit Biscuit", include_downloads=True)
    assert track is not None and confidence >= 0.8
    assert track.file_path == str(path)
    assert str(track.id).startswith("download:")


def test_sync_style_lookup_still_asks_the_media_server(db, tmp_path):
    # a sync builds playlists on the server, a file it hasn't seen is no use there
    _record_download(db, _song(tmp_path), "early breeze", "Petit Biscuit")
    for kwargs in ({}, {"server_source": "plex"}, {"album": "insomnia"}):
        track, _ = db.check_track_exists("early breeze", "Petit Biscuit", **kwargs)
        assert track is None


def test_deleted_file_is_not_owned(db, tmp_path):
    _record_download(db, tmp_path / "gone.mp3", "early breeze", "Petit Biscuit")
    track, _ = db.check_track_exists("early breeze", "Petit Biscuit", include_downloads=True)
    assert track is None


def test_other_artist_with_the_same_title_is_not_owned(db, tmp_path):
    _record_download(db, _song(tmp_path), "early breeze", "Petit Biscuit")
    track, _ = db.check_track_exists("early breeze", "Some Other Band", include_downloads=True)
    assert track is None


def test_unfinished_download_is_not_owned(db, tmp_path):
    _record_download(db, _song(tmp_path), "early breeze", "Petit Biscuit", status="failed")
    track, _ = db.check_track_exists("early breeze", "Petit Biscuit", include_downloads=True)
    assert track is None


def test_feat_suffix_on_the_request_still_finds_the_download(db, tmp_path):
    path = _song(tmp_path)
    _record_download(db, path, "early breeze", "Petit Biscuit")
    track, _ = db.check_track_exists("early breeze (feat. Øneheart)", "Petit Biscuit", include_downloads=True)
    assert track is not None and track.file_path == str(path)


def test_wishlist_owned_sweep_sees_the_download(db, tmp_path):
    from core.wishlist.library_match import find_owned_match

    path = _song(tmp_path)
    _record_download(db, path, "early breeze", "Petit Biscuit")
    match = find_owned_match(db, "early breeze", [{"name": "Petit Biscuit"}], "insomnia",
                             "plex", strict_identity=True)
    assert match is not None and match[0].file_path == str(path)
