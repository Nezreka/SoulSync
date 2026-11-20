"""exact soundcloud uploads on the wishlist (companion extension).

an exact upload is a wishlist row keyed ``soundcloud_exact_<sc id>``: that
one upload, never "the song". these pin the add service against a real
database, and every place that matches by title and artist keeping its
hands off an exact row (and an exact row off a catalogue one).
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.runtime_state import download_tasks, tasks_lock
from core.wishlist import soundcloud_exact as exact
from database.music_database import MusicDatabase

PERMALINK = "https://soundcloud.com/djuser/song-flip"
PRIVATE = "https://soundcloud.com/djuser/song-flip/s-AbCdEf"
ARTWORK = "https://i1.sndcdn.com/artworks-1-t500x500.jpg"


# ── fakes ────────────────────────────────────────────────────────────────────


def _entry(sc_id="123", title="Artist A - Song (DJ Flip)", uploader="djuser",
           url=PERMALINK, duration=201.0):
    return {"id": sc_id, "title": title, "uploader": uploader, "webpage_url": url,
            "duration": duration, "thumbnail": ARTWORK}


class FakeClient:
    """the real SoundcloudClient result conversion, with a scripted resolve."""

    def __init__(self, tmp_path):
        from core.soundcloud_client import SoundcloudClient
        self._real = SoundcloudClient(download_path=str(tmp_path / "sc"))
        self.script = {}
        self.calls = []
        self.available = True

    def is_available(self):
        return self.available

    async def resolve_url(self, url, timeout=None, progress_callback=None):
        self.calls.append(url)
        entries = self.script.get(url, [])
        return [self._real._sc_to_track_result({**e, "url": e["webpage_url"]}) for e in entries], []


class FakeOrch:
    def __init__(self, client, mode="hybrid", order=("soulseek", "soundcloud")):
        self._client = client
        self.mode = mode
        self.hybrid_order = list(order)
        self.hybrid_primary = "soulseek"
        self.hybrid_secondary = ""
        self.registry = SimpleNamespace(names=lambda: ["soulseek", "youtube", "soundcloud"])

    def client(self, name):
        return self._client if name == "soundcloud" else None

    def _normalize_source_name(self, name):
        return name if name in ("soulseek", "youtube", "soundcloud") else None


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


@pytest.fixture()
def client(tmp_path):
    c = FakeClient(tmp_path)
    c.script[PERMALINK] = [_entry()]
    # whether yt-dlp's webpage_url keeps the share token is unverified; the
    # harder case is that it doesn't, so the stored request_url must carry it
    c.script[PRIVATE] = [_entry(url=PERMALINK)]
    return c


@pytest.fixture()
def orch(client):
    return FakeOrch(client)


@pytest.fixture(autouse=True)
def _clean_tasks():
    with tasks_lock:
        download_tasks.clear()
    yield
    with tasks_lock:
        download_tasks.clear()


def _add(db, orch, url=PERMALINK, **kw):
    kw.setdefault("profile_id", 1)
    return exact.add_exact(db, orch, url=url, run_async=_run, **kw)


def _rows(db, profile_id=1):
    with db._get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM wishlist_tracks WHERE profile_id=?", (profile_id,)).fetchall()]


def _refusal(fn):
    with pytest.raises(exact.ExactError) as err:
        fn()
    return err.value


# ── ids and urls ─────────────────────────────────────────────────────────────


def test_exact_ids():
    assert exact.exact_track_id("123") == "soundcloud_exact_123"
    assert exact.exact_track_id(123) == "soundcloud_exact_123"
    for bad in ("abc", "12a", "", None, "-1"):
        with pytest.raises(ValueError):
            exact.exact_track_id(bad)
    assert exact.is_exact_id("soundcloud_exact_1")
    assert exact.is_exact_id("soundcloud_exact_1::album9")
    assert not exact.is_exact_id("soundcloud_exact_")
    assert not exact.is_exact_id("soundcloud_exact_x")
    assert not exact.is_exact_id("4uLU6hMCjMI75M1A2tKUQC")
    assert not exact.is_exact_id(None)
    assert exact.is_exact_track({"spotify_track_id": "soundcloud_exact_5"})
    assert exact.is_exact_track({"track_id": "soundcloud_exact_5"})
    assert not exact.is_exact_track({"id": "123"})


@pytest.mark.parametrize("raw, want", [
    ("https://soundcloud.com/u/t", "https://soundcloud.com/u/t"),
    ("http://m.soundcloud.com/u/t?in=x#frag", "https://soundcloud.com/u/t"),
    ("www.soundcloud.com/u/t/", "https://soundcloud.com/u/t"),
    ("https://soundcloud.com/u/t/s-tok123?si=abc", "https://soundcloud.com/u/t/s-tok123"),
    ("https://on.soundcloud.com/xYz12?x=1", "https://on.soundcloud.com/xYz12"),
])
def test_track_urls_normalize(raw, want):
    assert exact.normalize_track_url(raw) == want


@pytest.mark.parametrize("raw", [
    "https://soundcloud.com/u/sets/x",
    "https://soundcloud.com/u/likes",
    "https://soundcloud.com/u",
    "https://soundcloud.com/discover",
    "https://soundcloud.com/discover/sets/x",
    "https://soundcloud.com/u/t/comments",
    "https://soundcloud.com/u/tracks",
    "https://example.com/u/t",
    "not a url",
    "",
])
def test_non_track_urls_are_refused(raw):
    assert exact.normalize_track_url(raw) is None


def test_short_links_need_a_resolve():
    assert exact.needs_resolve("https://on.soundcloud.com/xYz12")
    assert not exact.needs_resolve(PERMALINK)


# ── payload ──────────────────────────────────────────────────────────────────


def _result(client, **kw):
    e = _entry(**kw)
    return client._real._sc_to_track_result({**e, "url": e["webpage_url"]})


def test_payload_shape(client):
    p = exact.build_payload(_result(client), PERMALINK)
    assert p["id"] == "soundcloud_exact_123"
    assert "id" not in p["album"]
    assert p["source"] == p["provider"] == "soundcloud_exact"
    assert p["name"] == "Song (DJ Flip)"
    assert p["artists"] == [{"name": "Artist A"}]
    assert p["album"]["images"] == [{"url": ARTWORK}]
    assert p["duration_ms"] == 201000
    block = p["soundcloud_exact"]
    assert set(block) == {
        "sc_id", "permalink_url", "request_url", "permalink_url_norm", "request_url_norm",
        "uploader", "upload_title", "duration_ms", "artwork_url", "official_download", "added_via"}
    assert block["sc_id"] == "123"
    assert block["upload_title"] == "Artist A - Song (DJ Flip)"
    assert block["uploader"] == "djuser"
    assert block["added_via"] == "companion"


def test_user_tags_win_and_the_uploader_stays_out(client):
    p = exact.build_payload(_result(client), PERMALINK, title="  Song  ",
                            artists=["Artist A", "artist a", "Feat B"], remixers=["DJ Flip", "feat b"])
    assert p["name"] == "Song"
    assert [a["name"] for a in p["artists"]] == ["Artist A", "Feat B", "DJ Flip"]
    assert "djuser" not in json.dumps(p["artists"])
    assert p["album"]["artists"] == [{"name": "Artist A"}]


def test_no_split_falls_back_to_the_uploader(client):
    p = exact.build_payload(_result(client, title="untitled jam"), PERMALINK)
    assert p["artists"] == [{"name": "djuser"}]
    assert p["name"] == "untitled jam"


def test_names_are_capped(client):
    p = exact.build_payload(_result(client), PERMALINK, title="x" * 900,
                            artists=[f"a{i}" for i in range(9)], remixers=["r1", "r2", "r3"])
    assert len(p["name"]) == 300
    assert len(p["artists"]) == 10


def test_canonicalize_leaves_the_payload_alone(db, client):
    from core.library2.catalogue_identity import canonicalize_wishlist_identity
    p = exact.build_payload(_result(client), PERMALINK)
    with db._get_connection() as conn:
        out, info = canonicalize_wishlist_identity(conn, p, {"a": 1})
    assert out is p and info == {"a": 1}


# ── add ──────────────────────────────────────────────────────────────────────


def test_new_add_is_201_keyed_by_the_soundcloud_id(db, orch):
    status, data = _add(db, orch)
    assert status == 201
    assert data["created"] is True and data["state"] == "queued"
    assert data["track_id"] == "soundcloud_exact_123"
    assert data["upload"]["sc_id"] == "123" and data["upload"]["artwork_url"] == ARTWORK
    assert data["track"]["spotify_track_id"] == "soundcloud_exact_123"
    assert data["playlist"] is None
    rows = _rows(db)
    assert [r["spotify_track_id"] for r in rows] == ["soundcloud_exact_123"]
    assert rows[0]["source_type"] == "companion"


def test_the_private_link_lands_on_the_same_row(db, orch):
    _add(db, orch)
    status, data = _add(db, orch, url=PRIVATE)
    assert status == 200 and data["created"] is False
    assert len(_rows(db)) == 1
    stored = json.loads(_rows(db)[0]["spotify_data"])["soundcloud_exact"]
    assert stored["request_url"] == PRIVATE
    assert exact.download_url(stored) == PRIVATE


def test_a_set_url_writes_nothing(db, orch, client):
    err = _refusal(lambda: _add(db, orch, url="https://soundcloud.com/djuser/sets/flips"))
    assert (err.status, err.code) == (400, "NOT_SOUNDCLOUD_TRACK")
    assert client.calls == []
    client.script[PERMALINK] = [_entry(), _entry(sc_id="124")]
    err = _refusal(lambda: _add(db, orch))
    assert (err.status, err.code) == (400, "NOT_SOUNDCLOUD_TRACK")
    assert _rows(db) == []


def test_an_empty_resolve_writes_nothing(db, orch, client):
    client.script[PERMALINK] = []
    err = _refusal(lambda: _add(db, orch))
    assert (err.status, err.code) == (422, "UPLOAD_UNAVAILABLE")
    assert _rows(db) == []


def test_disabled_and_unavailable_never_resolve(db, client):
    off = FakeOrch(client, mode="soulseek", order=())
    err = _refusal(lambda: _add(db, off))
    assert (err.status, err.code) == (409, "SOURCE_DISABLED")
    client.available = False
    err = _refusal(lambda: _add(db, FakeOrch(client)))
    assert (err.status, err.code) == (503, "SOURCE_UNAVAILABLE")
    assert client.calls == [] and _rows(db) == []


def test_soundcloud_only_mode_is_enabled(client):
    assert exact.source_state(FakeOrch(client, mode="soundcloud", order=())) == (True, None)
    assert exact.source_state(None) == (False, "source_unavailable")


def test_blocklisted_artist(db, orch):
    db.add_blocklist_entry(1, "artist", "Artist A")
    err = _refusal(lambda: _add(db, orch))
    assert (err.status, err.code) == (409, "BLOCKLISTED")
    assert _rows(db) == []


def test_blocklisted_uploader_alone(db, orch):
    db.add_blocklist_entry(1, "artist", "djuser")
    err = _refusal(lambda: _add(db, orch, artists=["Artist A"]))
    assert (err.status, err.code) == (409, "BLOCKLISTED")
    assert _rows(db) == []


def test_unknown_quality_profile(db, orch):
    err = _refusal(lambda: _add(db, orch, quality_profile_id=999))
    assert (err.status, err.code) == (400, "BAD_REQUEST")
    assert _rows(db) == []
    status, _ = _add(db, orch, quality_profile_id=2)
    assert status == 201
    assert _rows(db)[0]["quality_profile_id"] == 2


def test_a_failed_row_readded_is_retried(db, orch):
    _add(db, orch)
    with db._get_connection() as conn:
        conn.execute("UPDATE wishlist_tracks SET retry_count=3, last_attempted=CURRENT_TIMESTAMP")
        conn.commit()
    assert exact.status_for(db, sc_id="123")["state"] == "failed"
    status, data = _add(db, orch)
    assert status == 200 and data["state"] == "queued"
    assert _rows(db)[0]["retry_count"] == 0


def _imported(db, tmp_path, name="upload.mp3"):
    """the upload's import as production records it: a library row for the
    published file and its id match (resolution._record_exact_match)."""
    path = tmp_path / "lib" / name
    lib_id = _library_file(db, path)
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", str(lib_id),
                                 library_file_path=str(path),
                                 source_context_json=exact.match_context(
                                     {"request_url_norm": PERMALINK, "permalink_url_norm": PERMALINK,
                                      "sc_id": "123"}))
    return path, lib_id


def _library_file(db, path):
    """a published file in the library: on disk, with its lib2 track and
    primary file row (what the import's library write leaves)."""
    from tests import lib2_seed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    with db._get_connection() as conn:
        lib_id = lib2_seed.track(conn, "Artist A", "Song (DJ Flip)", "Song (DJ Flip)", path=str(path))
        conn.commit()
    return lib_id


def _downloaded(db, path, sc_id="123", url=PERMALINK):
    """the download history row an exact import writes (imports/side_effects.
    record_library_history_download, pinned by
    test_history_is_what_an_exact_import_records): the task's exact id, and
    the candidate's dispatch key as the source filename."""
    db.add_library_history_entry(event_type="download", title="Song", file_path=str(path),
                                 download_source="SoundCloud", source_track_id=f"soundcloud_exact_{sc_id}",
                                 source_filename=f"{sc_id}||{url}||Artist A - Song (DJ Flip)")


def test_an_id_match_says_acquired(db, orch, tmp_path):
    _imported(db, tmp_path)
    status, data = _add(db, orch)
    assert status == 200
    assert data == {**data, "created": False, "state": "acquired", "track": None}
    assert _rows(db) == []


def test_an_acquired_upload_with_only_its_history_is_not_queued_again(db, orch, tmp_path):
    """a media-server import: no library row yet, so no match; the download
    history is the record. a re-add must not queue a second download."""
    real = tmp_path / "song.mp3"
    real.write_bytes(b"x")
    _downloaded(db, real)
    assert exact.status_for(db, url=PERMALINK)["state"] == "acquired"
    assert exact.status_for(db, url=PRIVATE)["state"] == "acquired"
    status, data = _add(db, orch)
    assert (status, data["state"], data["created"]) == (200, "acquired", False)
    assert _rows(db) == []
    with tasks_lock:
        assert download_tasks == {}


def _file_state(db, lib_id, state):
    """what a library delete (library2/file_delete: rows kept as 'deleted') or
    a scan that can't find the file ('missing_confirmed') leaves: the track
    and file rows stay, the file row changes state."""
    from core.library2.track_files import set_file_state
    with db._get_connection() as conn:
        file_id = conn.execute("SELECT id FROM lib2_track_files WHERE track_id = ?", (lib_id,)).fetchone()[0]
        assert set_file_state(conn, file_id, state)
        conn.commit()


@pytest.mark.parametrize("state", ["deleted", "missing_confirmed"])
def test_a_dead_match_never_reads_acquired(db, orch, tmp_path, state):
    """the match the import recorded, then the file left the library: its
    rows stay with a dead file_state, so the library track row still reads.
    the upload is not acquired, a re-add drops the match and queues it."""
    path, lib_id = _imported(db, tmp_path)
    assert _add(db, orch)[1]["state"] == "acquired"
    path.unlink()
    _file_state(db, lib_id, state)
    from core.library import manual_library_match as _mlm
    match = db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123")
    assert _mlm.match_is_live(db, match) is True   # the shared rule still reads the row
    assert exact.status_for(db, sc_id="123")["state"] == "none"
    assert exact.status_for(db, url=PERMALINK)["state"] == "none"
    status, data = _add(db, orch)
    assert (status, data["state"], data["created"]) == (201, "queued", True)
    assert [r["spotify_track_id"] for r in _rows(db)] == ["soundcloud_exact_123"]
    assert db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123") is None
    assert exact.status_for(db, sc_id="123")["state"] == "queued"


def test_a_file_gone_from_disk_is_dead_while_its_row_still_reads_active(db, tmp_path):
    """a file removed behind the library's back, before any scan: the row is
    still 'active', but the folder is visible here and the file isn't in it."""
    path, _lib_id = _imported(db, tmp_path)
    path.unlink()
    assert exact.status_for(db, sc_id="123")["state"] == "none"


def test_a_live_match_reads_acquired_and_is_kept(db, orch, tmp_path):
    path, _lib_id = _imported(db, tmp_path)
    assert exact.upload_file(db, {"id": "soundcloud_exact_123"}) == str(path)
    assert exact.status_for(db, sc_id="123")["state"] == "acquired"
    status, data = _add(db, orch)
    assert (status, data["state"], data["created"]) == (200, "acquired", False)
    assert exact._drop_dead_matches(db, "soundcloud_exact_123", 1) == 0
    assert db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123") is not None
    assert _rows(db) == []


def test_every_match_row_for_the_id_is_read(db, orch, tmp_path):
    """a dead match under the exact source label must not hide a live one
    saved under another (and the dead one alone is dropped on a re-add)."""
    path, lib_id = _imported(db, tmp_path, name="old.mp3")
    path.unlink()
    _file_state(db, lib_id, "deleted")
    live = tmp_path / "lib" / "new.mp3"
    live_id = _library_file(db, live)
    db.save_manual_library_match(1, "mirrored", "soundcloud_exact_123", str(live_id),
                                 library_file_path=str(live))
    assert exact.upload_file(db, {"id": "soundcloud_exact_123"}) == str(live)
    assert exact.status_for(db, sc_id="123")["state"] == "acquired"
    assert exact._drop_dead_matches(db, "soundcloud_exact_123", 1) == 1
    assert db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123") is None
    assert db.get_manual_library_match(1, "mirrored", "soundcloud_exact_123") is not None


def _shared_track(db, tmp_path, upload_name="Song (DJ Flip) [123].mp3"):
    """the original FLAC in the library, then the upload's import as the
    catalogue files it: its album (named after the track, its first artist)
    is the original's single, and autolink's title match finds the original's
    track, so the upload is a second, lossy file row there. its match points
    at that track (resolution._record_exact_match)."""
    from core.library2.autolink import _find_or_create_track
    original = tmp_path / "lib" / "Song (DJ Flip).flac"
    lib_id = _library_file(db, original)
    upload = tmp_path / "lib" / upload_name
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"x")
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_track_files SET format = 'flac' WHERE track_id = ?", (lib_id,))
        album_id, artist_id = conn.execute(
            "SELECT al.id, al.primary_artist_id FROM lib2_tracks t JOIN lib2_albums al "
            "ON al.id = t.album_id WHERE t.id = ?", (lib_id,)).fetchone()
        assert _find_or_create_track(conn, album_id, artist_id, "Song (DJ Flip)",
                                     track_number=None) == lib_id
        upload_file = conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, format) VALUES (?, ?, 'mp3')",
            (lib_id, str(upload))).lastrowid
        conn.commit()
        # the FLAC original stays the track's primary file
        assert conn.execute("SELECT path FROM lib2_track_files WHERE track_id = ? AND is_primary = 1",
                            (lib_id,)).fetchone()[0] == str(original)
    assert db.find_track_id_by_file_path(str(upload)) == str(lib_id)
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", str(lib_id),
                                 library_file_path=str(upload))
    return original, upload, upload_file


def test_a_shared_track_reads_the_uploads_own_file(db, orch, tmp_path):
    """the upload and the original live on one track: the upload's file is
    what it acquired (playlists add it), never the primary original."""
    _original, upload, _file = _shared_track(db, tmp_path)
    assert exact.upload_file(db, {"id": "soundcloud_exact_123"}) == str(upload)
    assert exact.status_for(db, sc_id="123")["state"] == "acquired"
    assert exact._drop_dead_matches(db, "soundcloud_exact_123", 1) == 0


@pytest.mark.parametrize("state", ["deleted", "missing_confirmed"])
def test_a_dead_upload_on_a_shared_track_is_not_held_up_by_the_original(db, orch, tmp_path, state):
    """the upload's file left the library, its row kept with a dead state
    (the original was primary all along and still is): the live original on
    the same track is not the upload. not acquired, the match is dropped and
    a re-add queues it."""
    from core.library2.track_files import set_file_state
    original, upload, upload_file = _shared_track(db, tmp_path)
    upload.unlink()
    with db._get_connection() as conn:
        assert set_file_state(conn, upload_file, state)
        conn.commit()
    assert original.exists()
    assert exact.upload_file(db, {"id": "soundcloud_exact_123"}) is None
    assert exact.upload_acquired(db, {"id": "soundcloud_exact_123"}) is False
    assert exact.status_for(db, sc_id="123")["state"] == "none"
    status, data = _add(db, orch)
    assert (status, data["state"], data["created"]) == (201, "queued", True)
    assert db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123") is None


def test_a_deleted_primary_upload_does_not_hand_over_to_the_original(db, tmp_path):
    """the upload was the primary (same quality, newer): its delete promotes
    the original to primary, which must not read as the upload."""
    from core.library2.track_files import set_file_state
    _original, upload, upload_file = _shared_track(db, tmp_path)
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_track_files SET format = 'mp3'")
        conn.execute("UPDATE lib2_track_files SET is_primary = (id = ?)", (upload_file,))
        assert set_file_state(conn, upload_file, "deleted")
        conn.commit()
    assert exact.upload_file(db, {"id": "soundcloud_exact_123"}) is None
    assert exact._drop_dead_matches(db, "soundcloud_exact_123", 1) == 1


def test_a_moved_upload_is_found_by_its_id_on_the_name(db, tmp_path):
    """a reorganize moved the upload after its match was recorded: the match
    names the old path, the file on its track carries the upload's id."""
    from core.library2.track_files import repoint_file_path
    _original, old, _file = _shared_track(db, tmp_path)
    moved = tmp_path / "lib" / "Artist A" / "01 - Song (DJ Flip) [123].mp3"
    moved.parent.mkdir(parents=True)
    old.rename(moved)
    with db._get_connection() as conn:
        assert repoint_file_path(conn, str(old), str(moved)) == 1
        conn.commit()
    assert exact.upload_file(db, {"id": "soundcloud_exact_123"}) == str(moved)


def test_an_upload_moved_outside_soulsync_is_found_after_the_rescan(db, tmp_path):
    """beets/Picard moved the upload, then a rescan ran: the old row is kept
    'missing_confirmed' (scan.settle_absent_rows) and an active row for the
    new path joins the same track. the match still reads the upload there."""
    from core.library2.track_files import set_file_state
    _original, old, upload_file = _shared_track(db, tmp_path)
    moved = tmp_path / "lib" / "Tagged" / "Song (DJ Flip) [123].mp3"
    moved.parent.mkdir(parents=True)
    old.rename(moved)
    with db._get_connection() as conn:
        lib_id = conn.execute("SELECT track_id FROM lib2_track_files WHERE id = ?",
                              (upload_file,)).fetchone()[0]
        assert set_file_state(conn, upload_file, "missing_confirmed")
        conn.execute("INSERT INTO lib2_track_files (track_id, path, format) VALUES (?, ?, 'mp3')",
                     (lib_id, str(moved)))
        conn.commit()
    assert exact.upload_file(db, {"id": "soundcloud_exact_123"}) == str(moved)
    assert exact._acquired_file(db, "soundcloud_exact_123", "123", 1) == str(moved)
    assert exact.status_for(db, sc_id="123")["state"] == "acquired"
    assert exact._drop_dead_matches(db, "soundcloud_exact_123", 1) == 0
    assert db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123") is not None


def _analyse(db, track_data, *, force):
    """the real analysis loop (downloads/master.run_full_missing_tracks_process)
    on one track, against this database. force: the wishlist processor's
    internal force_download_all."""
    from unittest.mock import patch
    from core.downloads.master import run_full_missing_tracks_process
    from core.runtime_state import download_batches
    batch_id = f"exact-analysis-{force}"
    with tasks_lock:
        download_batches[batch_id] = {
            "phase": "analysis", "analysis_total": 1, "analysis_processed": 0,
            "force_download_all": force, "ignore_manual_matches": False,
            "is_album_download": False, "album_context": None, "artist_context": None,
            "profile_id": 1, "batch_source": "wishlist", "wing_it": False,
            "playlist_folder_mode": False, "queue": [], "active_count": 0, "max_concurrent": 1,
            "queue_index": 0, "permanently_failed_tracks": [], "cancelled_tracks": set(),
            "analysis_results": [],
        }
    deps = MagicMock()
    deps.config_manager.get.return_value = False
    deps.config_manager.get_active_media_server.return_value = "plex"
    deps.mb_worker = None
    deps.mb_release_cache, deps.mb_release_detail_cache = {}, {}
    deps.mb_release_cache_lock, deps.mb_release_detail_cache_lock = threading.Lock(), threading.Lock()
    deps.normalize_album_cache_key = lambda x: x.lower().strip()
    deps.is_explicit_blocked = MagicMock(return_value=False)
    deps.youtube_playlist_states, deps.tidal_discovery_states = {}, {}
    deps.deezer_discovery_states, deps.spotify_public_discovery_states = {}, {}
    try:
        with patch("database.music_database.MusicDatabase", return_value=db):
            run_full_missing_tracks_process(batch_id, "wishlist", [track_data], deps)
        with tasks_lock:
            batch = download_batches.get(batch_id, {})
            return batch.get("analysis_results", []), list(batch.get("queue", [])), deps
    finally:
        with tasks_lock:
            download_batches.pop(batch_id, None)


def _wishlist_track(db):
    from core.wishlist.service import WishlistService
    row = db.get_wishlist_track("soundcloud_exact_123", profile_id=1)
    return WishlistService.format_track_for_download(row)


@pytest.mark.parametrize("force", [False, True])
def test_the_analysis_removes_an_acquired_exact_row_by_its_id(db, orch, tmp_path, force):
    """the row is on the wishlist (queued before the import recorded the
    match) and its own download is in: the analysis finds it, and removes the
    row by its id, as the by-metadata remover never does for an exact row."""
    _add(db, orch)
    path, _lib_id = _imported(db, tmp_path)
    results, queue, deps = _analyse(db, _wishlist_track(db), force=force)
    assert [(r["found"], r["matched_file_path"]) for r in results] == [(True, str(path))]
    assert queue == []
    assert _rows(db) == []
    deps.check_and_remove_track_from_wishlist_by_metadata.assert_not_called()


@pytest.mark.parametrize("force", [False, True])
def test_the_analysis_keeps_an_exact_row_not_yet_acquired(db, orch, tmp_path, force):
    """not acquired: the row stays and the track is queued. a match whose
    file is gone (rows kept as 'deleted') is not acquired either."""
    _add(db, orch)
    path, lib_id = _imported(db, tmp_path)
    path.unlink()
    _file_state(db, lib_id, "deleted")
    results, queue, deps = _analyse(db, _wishlist_track(db), force=force)
    assert [r["found"] for r in results] == [False]
    assert len(queue) == 1
    assert [r["spotify_track_id"] for r in _rows(db)] == ["soundcloud_exact_123"]
    deps.check_and_remove_track_from_wishlist_by_metadata.assert_not_called()


def test_no_library_v2_intent(db, orch):
    from core.library2.materialize import materialize_wishlist_row
    with db._get_connection() as conn:
        before = conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0]
    _add(db, orch)
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == before
        row_id = conn.execute("SELECT id FROM wishlist_tracks").fetchone()[0]
        assert materialize_wishlist_row(conn, row_id, profile_id=1) is None


# ── isolation from title/artist matching ─────────────────────────────────────


def _catalogue(track_id="sp1"):
    return {"id": track_id, "name": "Song (DJ Flip)", "artists": [{"name": "Artist A"}],
            "album": {"name": "Song (DJ Flip)"}}


def _config(monkeypatch, allow):
    import core.settings as settings
    real = settings.config_manager.get
    monkeypatch.setattr(settings.config_manager, "get",
                        lambda k, d=None: allow if k == "wishlist.allow_duplicate_tracks" else real(k, d))


@pytest.mark.parametrize("allow", [True, False])
def test_remove_duplicates_keeps_both(db, orch, monkeypatch, allow):
    _config(monkeypatch, allow)
    db.add_to_wishlist_detailed(_catalogue(), profile_id=1)
    _add(db, orch)
    assert len(_rows(db)) == 2
    assert db.remove_wishlist_duplicates(profile_id=1) == 0
    assert {r["spotify_track_id"] for r in _rows(db)} == {"sp1", "soundcloud_exact_123"}


def test_no_duplicate_refresh_across_kinds(db, orch, monkeypatch):
    _config(monkeypatch, False)
    _add(db, orch)
    exact_before = _rows(db)[0]["spotify_data"]
    out = db.add_to_wishlist_detailed(_catalogue(), profile_id=1, quality_profile_id=1)
    assert out["status"] == "created" and out["track_id"] == "sp1"
    assert next(r for r in _rows(db) if r["spotify_track_id"] == "soundcloud_exact_123")["spotify_data"] == exact_before
    assert {r["spotify_track_id"] for r in _rows(db)} == {"sp1", "soundcloud_exact_123"}


def test_two_exact_uploads_with_the_same_names_are_two_rows(db, orch, client, monkeypatch):
    _config(monkeypatch, False)
    other = "https://soundcloud.com/someoneelse/song-flip"
    client.script[other] = [_entry(sc_id="999", url=other)]
    _add(db, orch, quality_profile_id=1)
    status, data = _add(db, orch, url=other, quality_profile_id=1)
    assert status == 201 and data["track_id"] == "soundcloud_exact_999"
    assert db.remove_wishlist_duplicates(profile_id=1) == 0
    assert len(_rows(db)) == 2


def test_exact_add_beside_an_existing_catalogue_row(db, client, monkeypatch):
    _config(monkeypatch, False)
    db.add_to_wishlist_detailed(_catalogue(), profile_id=1, quality_profile_id=1)
    catalogue_before = _rows(db)[0]["spotify_data"]
    status, data = _add(db, FakeOrch(client), quality_profile_id=1)
    assert status == 201 and data["track_id"] == "soundcloud_exact_123"
    assert next(r for r in _rows(db) if r["spotify_track_id"] == "sp1")["spotify_data"] == catalogue_before


def test_metadata_match_ignores_exact_rows():
    from core.wishlist.resolution import _match_by_metadata
    rows = [{"spotify_track_id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "A"}]}]
    assert _match_by_metadata(rows, "Song", "A") is None
    rows.append({"spotify_track_id": "sp1", "name": "Song", "artists": [{"name": "A"}]})
    assert _match_by_metadata(rows, "Song", "A") == "sp1"


def test_manual_match_lookup_by_id_only_for_exact(db):
    from core.library import manual_library_match as mlm
    db.save_manual_library_match(1, "spotify", "sp1", "lib-1", source_title="Song", source_artist="A")
    exact_track = {"id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "A"}]}
    assert mlm.get_match_for_track(db, 1, exact_track) is None
    assert mlm.get_match_for_track(db, 1, {"id": "sp9", "name": "Song", "artists": [{"name": "A"}]})
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", "lib-2")
    assert mlm.get_match_for_track(db, 1, exact_track)["library_track_id"] == "lib-2"


def test_an_exact_match_never_satisfies_a_catalogue_track(db):
    from core.library import manual_library_match as mlm
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", "lib-2",
                                 source_title="Song", source_artist="A")
    assert mlm.get_match_for_track(db, 1, {"id": "sp9", "name": "Song", "artists": [{"name": "A"}]}) is None


def test_a_newer_exact_match_never_shadows_a_catalogue_match(db):
    from core.library import manual_library_match as mlm
    db.save_manual_library_match(1, "spotify", "sp1", "lib-1", source_title="Song", source_artist="A")
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", "lib-2",
                                 source_title="Song", source_artist="A")
    with db._get_connection() as conn:
        conn.execute("UPDATE manual_library_track_matches SET updated_at = '2020-01-01 00:00:00' "
                     "WHERE source_track_id = 'sp1'")
        conn.execute("UPDATE manual_library_track_matches SET updated_at = '2030-01-01 00:00:00' "
                     "WHERE source_track_id = 'soundcloud_exact_123'")
        conn.commit()
    catalogue = {"id": "sp9", "name": "Song", "artists": [{"name": "A"}]}
    assert mlm.get_match_for_track(db, 1, catalogue)["library_track_id"] == "lib-1"
    assert db.find_manual_library_match_by_metadata(1, "Song", "A")["source_track_id"] == "sp1"
    # the exact track is still id-only
    exact_track = {"id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "A"}]}
    assert mlm.get_match_for_track(db, 1, exact_track)["library_track_id"] == "lib-2"
    assert mlm.get_match_for_track(db, 1, {**exact_track, "id": "soundcloud_exact_999"}) is None


def test_owned_sweep_skips_exact_rows(monkeypatch):
    import core.wishlist.library_match as lm
    from core.library import manual_library_match as mlm
    from core.wishlist.processing import _check_one_owned

    monkeypatch.setattr(lm, "find_owned_match", lambda *a, **k: (SimpleNamespace(file_path="/x"), 0.99, "A"))
    monkeypatch.setattr(mlm, "get_match_for_track", lambda *a, **k: None)
    svc = MagicMock()
    svc.mark_track_download_result.return_value = True
    log = MagicMock()
    track = {"name": "Song", "artists": [{"name": "A"}], "album": {"name": "Song"}}
    assert _check_one_owned(svc, MagicMock(), mlm, 1, {**track, "spotify_track_id": "soundcloud_exact_123"},
                            "plex", logger=log, log_prefix="t") == 'kept'
    svc.mark_track_download_result.assert_not_called()
    assert _check_one_owned(svc, MagicMock(), mlm, 1, {**track, "spotify_track_id": "sp1"},
                            "plex", logger=log, log_prefix="t") == 'removed'


# ── playlists ────────────────────────────────────────────────────────────────


def test_attach_to_a_user_playlist(db, orch):
    from core.discovery.manual_match import should_rediscover
    from core.playlists import user_playlists
    pid = user_playlists.create_playlist(db, 1, "Flips")
    status, data = _add(db, orch, playlist_id=pid)
    assert status == 201
    assert data["playlist"] == {"id": pid, "name": "Flips", "added": True, "already_present": False}
    tracks = db.get_mirrored_playlist_tracks(pid)
    assert len(tracks) == 1
    row = tracks[0]
    assert row["source_track_id"] == "soundcloud_exact_123"
    extra = json.loads(row["extra_data"])
    assert extra["manual_match"] is True and extra["matched_data"]["id"] == "soundcloud_exact_123"
    assert should_rediscover(extra) is False

    stored = _rows(db)[0]
    assert stored["source_type"] == "playlist"
    info = json.loads(stored["source_info"])
    resolved = db.resolve_mirrored_playlist_assignment(
        playlist_ref=info["playlist_id"], playlist_name=info["playlist_name"], profile_id=1)
    assert resolved["id"] == pid

    status, data = _add(db, orch, url=PRIVATE, playlist_id=pid)
    assert status == 200 and data["playlist"]["already_present"] is True
    assert len(db.get_mirrored_playlist_tracks(pid)) == 1

    _add(db, orch)  # a plain re-add keeps the playlist provenance
    assert _rows(db)[0]["source_type"] == "playlist"
    assert json.loads(_rows(db)[0]["source_info"])["playlist_id"] == info["playlist_id"]


def test_already_owned_still_joins_the_playlist(db, orch, tmp_path):
    from core.playlists import user_playlists
    pid = user_playlists.create_playlist(db, 1, "Flips")
    _imported(db, tmp_path)
    status, data = _add(db, orch, playlist_id=pid)
    assert status == 200 and data["state"] == "acquired" and _rows(db) == []
    assert data["playlist"]["added"] is True
    assert [t["source_track_id"] for t in db.get_mirrored_playlist_tracks(pid)] == ["soundcloud_exact_123"]


def test_a_failed_playlist_write_keeps_the_wish(db, orch, monkeypatch):
    from core.playlists import user_playlists
    pid = user_playlists.create_playlist(db, 1, "Flips")

    def boom(*a, **k):
        raise RuntimeError("could not save the playlist")

    monkeypatch.setattr(user_playlists, "add_exact_track", boom)
    status, data = _add(db, orch, playlist_id=pid)
    assert status == 201
    assert data["playlist"] == {"id": pid, "added": False, "error": "could not save the playlist"}
    assert len(_rows(db)) == 1


def test_playlist_refusals_write_nothing(db, orch, client):
    from core.playlists import user_playlists
    spotify = db.mirror_playlist(source="spotify", source_playlist_id="sp-pl", name="Mirror",
                                 tracks=[], profile_id=1)
    err = _refusal(lambda: _add(db, orch, playlist_id=spotify))
    assert (err.status, err.code) == (422, "PLAYLIST_NOT_EDITABLE")
    theirs = user_playlists.create_playlist(db, 2, "Theirs")
    err = _refusal(lambda: _add(db, orch, playlist_id=theirs))
    assert (err.status, err.code) == (400, "BAD_PLAYLIST")
    assert client.calls == [] and _rows(db) == []


def test_add_exact_track_is_keyed_on_the_id(db, client):
    from core.playlists import user_playlists
    pid = user_playlists.create_playlist(db, 1, "Flips")
    playlist = db.get_mirrored_playlist(pid)
    payload = exact.build_payload(_result(client), PERMALINK)
    assert user_playlists.add_exact_track(db, playlist, payload)["added"] is True
    # a catalogue track of the same name doesn't count as present, and back
    user_playlists.add_tracks(db, playlist, [{"track_name": payload["name"], "artist_name": "Artist A"}],
                              allow_duplicates=True)
    again = user_playlists.add_exact_track(db, playlist, payload)
    assert again == {"added": False, "already_present": True, "track_count": 2}


def test_mirror_library_check_never_stamps_an_exact_row_by_name(db, client, monkeypatch, tmp_path):
    import core.downloads.master as master
    from core.playlists import library_check, user_playlists

    # the library owns the original: every name lookup says yes
    asked = []
    monkeypatch.setattr(master, "match_track_in_library",
                        lambda db_, q, **kw: asked.append(q["id"]) or (True, 0.95, SimpleNamespace(id=501)))
    pid = user_playlists.create_playlist(db, 1, "Flips")
    playlist = db.get_mirrored_playlist(pid)
    user_playlists.add_exact_track(db, playlist, exact.build_payload(_result(client), PERMALINK))
    user_playlists.add_tracks(db, playlist, [{"track_name": "Song (DJ Flip)", "artist_name": "Artist A",
                                              "source_track_id": "sp1"}], allow_duplicates=True)

    def check():
        tracks = db.get_mirrored_playlist_tracks(pid)
        for t in tracks:  # due again
            db.update_mirrored_track_extra_data(t["id"], {"in_library": None, "library_checked_at": None})
        library_check.check_mirror_tracks(db, db.get_mirrored_playlist_tracks(pid), profile_id=1, source=None)
        return {t["source_track_id"]: json.loads(t["extra_data"]).get("in_library")
                for t in db.get_mirrored_playlist_tracks(pid)}

    stamped = check()
    assert stamped["soundcloud_exact_123"] is False
    assert "soundcloud_exact_123" not in asked
    # the catalogue row of the same names is still matched by name
    assert [v for k, v in stamped.items() if k != "soundcloud_exact_123"] == [True]

    # its own download lands: stamped by its history
    real = tmp_path / "song.mp3"
    real.write_bytes(b"x")
    _downloaded(db, real)
    assert check()["soundcloud_exact_123"] is True
    assert "soundcloud_exact_123" not in asked


def test_mirror_library_check_honours_the_exact_id_match(db, client, monkeypatch):
    import core.downloads.master as master
    from core.playlists import library_check, user_playlists

    monkeypatch.setattr(master, "match_track_in_library", lambda *a, **k: (False, 0.0, None))
    pid = user_playlists.create_playlist(db, 1, "Flips")
    user_playlists.add_exact_track(db, db.get_mirrored_playlist(pid),
                                   exact.build_payload(_result(client), PERMALINK))
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", "lib-9")
    monkeypatch.setattr("core.library.manual_library_match.match_is_live", lambda *a, **k: True)
    library_check.check_mirror_tracks(db, db.get_mirrored_playlist_tracks(pid), profile_id=1, source=None)
    extra = json.loads(db.get_mirrored_playlist_tracks(pid)[0]["extra_data"])
    assert extra["in_library"] is True and extra["library_track_id"] == "lib-9"


def _seed_file(db, path, title="Song (DJ Flip)", artist="Artist A", album="Song (DJ Flip)"):
    from tests import lib2_seed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    with db._get_connection() as conn:
        lib_id = lib2_seed.track(conn, artist, album, title, path=str(path))
        conn.commit()
    return lib_id


def _organized_rebuild(db, tmp_path, pid):
    from core.playlists.materialize_service import _rebuild_one_from_db

    class _Cfg:
        def get(self, key, default=None):
            return {"playlists.materialize_path": str(tmp_path / "Playlists"),
                    "playlists.materialize_mode": "symlink"}.get(key, default)

    _name, summary = _rebuild_one_from_db(db, _Cfg(), db.get_mirrored_playlist(pid), profile_id=1)
    from pathlib import Path
    return {p.name: p.resolve() for p in Path(summary.playlist_dir).iterdir()}


def test_organized_playlist_never_links_the_original_for_an_exact_row(db, client, tmp_path):
    """the organize-by-playlist rebuild matches members by name; an exact row
    whose original is owned must not get the catalogue file as its upload."""
    from core.playlists import user_playlists
    original = tmp_path / "Music" / "Artist A" / "Song (DJ Flip).flac"
    _seed_file(db, original)
    # the name matcher really does find the original for the upload's names
    hit, _conf = db.check_track_exists("Song (DJ Flip)", "Artist A", confidence_threshold=0.7)
    assert hit is not None and hit.file_path == str(original)

    pid = user_playlists.create_playlist(db, 1, "Flips")
    user_playlists.add_exact_track(db, db.get_mirrored_playlist(pid),
                                   exact.build_payload(_result(client), PERMALINK))
    assert _organized_rebuild(db, tmp_path, pid) == {}

    # the upload lands (the import's own record: a library row + its id match)
    upload = tmp_path / "Music" / "Artist A" / "Song (DJ Flip) [123].mp3"
    lib_id = _seed_file(db, upload, album="Song (DJ Flip) [SoundCloud]")
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", str(lib_id),
                                 library_file_path=str(upload))
    assert _organized_rebuild(db, tmp_path, pid) == {upload.name: upload.resolve()}


def test_organized_playlist_links_the_exact_upload_by_its_history(db, client, tmp_path):
    """a media-server import with no library row yet: the download history's
    file is the upload's own file."""
    from core.playlists import user_playlists
    _seed_file(db, tmp_path / "Music" / "Artist A" / "Song (DJ Flip).flac")
    pid = user_playlists.create_playlist(db, 1, "Flips")
    user_playlists.add_exact_track(db, db.get_mirrored_playlist(pid),
                                   exact.build_payload(_result(client), PERMALINK))
    upload = tmp_path / "Downloads" / "Song (DJ Flip) [123].mp3"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"audio")
    _downloaded(db, upload)
    assert _organized_rebuild(db, tmp_path, pid) == {upload.name: upload.resolve()}


# ── status ───────────────────────────────────────────────────────────────────


def test_status_lifecycle(db, orch, tmp_path):
    # never added: the same answer by id as by url
    assert exact.status_for(db, sc_id="123") == {
        "state": "none", "matched": False, "track_id": "soundcloud_exact_123",
        "failure_reason": None, "retry_count": 0, "last_attempted": None}
    assert exact.status_for(db, url=PERMALINK)["matched"] is False
    short = exact.status_for(db, url="https://on.soundcloud.com/zzz")
    assert (short["state"], short["matched"]) == ("none", False)

    _add(db, orch, url=PRIVATE)
    queued = exact.status_for(db, sc_id="123")
    assert (queued["state"], queued["matched"]) == ("queued", True)
    assert exact.status_for(db, url=PRIVATE + "?si=1")["state"] == "queued"
    assert exact.status_for(db, url=PERMALINK)["state"] == "queued"
    assert exact.status_for(db, sc_id="123", profile_id=2)["state"] == "none"

    with db._get_connection() as conn:
        conn.execute("UPDATE wishlist_tracks SET retry_count=2, failure_reason='quarantined: 128kbps'")
        conn.commit()
    failed = exact.status_for(db, sc_id="123")
    assert failed["state"] == "failed" and failed["retry_count"] == 2
    assert failed["failure_reason"] == "quarantined: 128kbps"

    with tasks_lock:
        download_tasks["t1"] = {"status": "downloading", "track_info": {"id": "soundcloud_exact_123"}}
    assert exact.status_for(db, sc_id="123")["state"] == "downloading"
    with tasks_lock:
        download_tasks["t1"]["status"] = "failed"

    db.remove_from_wishlist("soundcloud_exact_123", profile_id=1)
    gone = exact.status_for(db, sc_id="123")
    assert (gone["state"], gone["matched"]) == ("none", False)
    # a live task alone is state too
    with tasks_lock:
        download_tasks["t2"] = {"status": "searching", "track_info": {"id": "soundcloud_exact_123"}}
    live = exact.status_for(db, sc_id="123")
    assert (live["state"], live["matched"]) == ("downloading", True)
    with tasks_lock:
        download_tasks["t2"]["status"] = "cancelled"
    missing = str(tmp_path / "gone.mp3")
    _downloaded(db, missing)
    assert exact.status_for(db, sc_id="123")["matched"] is False
    real = tmp_path / "song.mp3"
    real.write_bytes(b"x")
    _downloaded(db, real)
    acquired = exact.status_for(db, sc_id="123")
    assert (acquired["state"], acquired["matched"]) == ("acquired", True)


def test_status_by_url_ignores_the_share_token_both_ways(db, orch, client, tmp_path):
    other = "https://soundcloud.com/djuser/another-flip"
    # added by the public permalink, asked by a share link
    _add(db, orch)
    assert exact.status_for(db, url=PRIVATE)["state"] == "queued"
    assert exact.status_for(db, url=PERMALINK + "/s-OtherTok")["state"] == "queued"
    assert exact.status_for(db, url=other)["matched"] is False
    db.remove_from_wishlist("soundcloud_exact_123", profile_id=1)

    # added by a share link whose resolve kept the token, asked by the permalink
    client.script[PRIVATE] = [_entry(url=PRIVATE)]
    _add(db, orch, url=PRIVATE)
    block = json.loads(_rows(db)[0]["spotify_data"])["soundcloud_exact"]
    assert block["request_url_norm"] == block["permalink_url_norm"] == PRIVATE
    assert exact.status_for(db, url=PERMALINK)["state"] == "queued"
    assert exact.status_for(db, url=other + "/s-AbCdEf")["matched"] is False

    # and after the import, through the recorded match
    from core.wishlist.resolution import _record_exact_match
    published = tmp_path / "lib" / "song.mp3"
    _library_file(db, published)
    _record_exact_match("soundcloud_exact_123", {"name": "Song", "artists": [{"name": "A"}]},
                        str(published), db, [1], block=block)
    db.remove_from_wishlist("soundcloud_exact_123", profile_id=1)
    assert exact.status_for(db, url=PERMALINK)["state"] == "acquired"
    assert exact.status_for(db, url=PERMALINK + "/s-OtherTok")["state"] == "acquired"
    assert exact.status_for(db, url=other)["matched"] is False


def test_an_exact_url_beats_a_token_only_match(db, orch, client):
    """two rows of the same user/slug: the one with the asked url answers."""
    client.script[PRIVATE] = [_entry(url=PRIVATE)]
    _add(db, orch, url=PRIVATE)
    second = PERMALINK + "/s-Zzz"
    client.script[second] = [_entry(sc_id="124", url=second)]
    _add(db, orch, url=second)
    assert exact.status_for(db, url=PRIVATE)["track_id"] == "soundcloud_exact_123"
    assert exact.status_for(db, url=second)["track_id"] == "soundcloud_exact_124"


def test_upload_acquired_reads_the_history(db, tmp_path):
    track = {"id": "soundcloud_exact_123"}
    assert exact.upload_acquired(db, track, 1) is False
    assert exact.upload_acquired(db, {"id": "sp1"}, 1) is False
    real = tmp_path / "song.mp3"
    real.write_bytes(b"x")
    _downloaded(db, real)
    assert exact.upload_acquired(db, track, 1) is True


def test_history_is_what_an_exact_import_records(db, client, tmp_path, monkeypatch):
    """the real history writer on an exact download's context, built as
    downloads/candidates.py builds it: the search result is the candidate's
    __dict__ (a TrackResult has no track_id or id), the track info is the
    task's, keyed by the exact id."""
    import core.imports.side_effects as side_effects
    monkeypatch.setattr(side_effects, "get_database", lambda: db)
    candidate = _result(client)
    published = tmp_path / "song.mp3"
    published.write_bytes(b"x")
    side_effects.record_library_history_download({
        "original_search_result": dict(candidate.__dict__, spotify_clean_title="Song (DJ Flip)"),
        "track_info": {"id": "soundcloud_exact_123", "name": "Song (DJ Flip)",
                       "artists": [{"name": "Artist A"}]},
        "_download_username": "soundcloud",
        "_final_processed_path": str(published),
    })
    with db._get_connection() as conn:
        row = dict(conn.execute("SELECT download_source, source_track_id, source_filename "
                                "FROM library_history").fetchone())
    assert row == {"download_source": "SoundCloud", "source_track_id": "soundcloud_exact_123",
                   "source_filename": f"123||{PERMALINK}||Artist A - Song (DJ Flip)"}
    assert exact.upload_acquired(db, {"id": "soundcloud_exact_123"}, 1) is True
    assert exact.status_for(db, url=PERMALINK)["state"] == "acquired"


def test_a_bare_soundcloud_id_counts_only_beside_its_dispatch_key(db, tmp_path):
    """source_track_id falls back to the dispatch key's bare sc id only when
    the task's track info has no id. a bare "123" on any other row (another
    shape, another upload's key) is not this upload's download."""
    track = {"id": "soundcloud_exact_123"}
    real = tmp_path / "song.mp3"
    real.write_bytes(b"x")
    for filename in ("", "Artist A - Song.mp3", f"1234||{PERMALINK}-2||Other", f"x123||{PERMALINK}||Song"):
        db.add_library_history_entry(event_type="download", title="Song", file_path=str(real),
                                     download_source="SoundCloud", source_track_id="123",
                                     source_filename=filename)
    assert exact.upload_acquired(db, track, 1) is False
    assert exact.status_for(db, url=PERMALINK)["matched"] is False
    db.add_library_history_entry(event_type="download", title="Song", file_path=str(real),
                                 download_source="SoundCloud", source_track_id="123",
                                 source_filename=f"123||{PERMALINK}||Artist A - Song (DJ Flip)")
    assert exact.upload_acquired(db, track, 1) is True
    assert exact.status_for(db, url=PERMALINK)["state"] == "acquired"


def test_a_catalogue_download_of_the_same_upload_is_not_the_exact_one(db, tmp_path):
    """the catalogue song found the same upload by search: its history row has
    the upload's dispatch key but the catalogue track's id."""
    real = tmp_path / "song.mp3"
    real.write_bytes(b"x")
    db.add_library_history_entry(event_type="download", title="Song", file_path=str(real),
                                 download_source="SoundCloud", source_track_id="sp1",
                                 source_filename=f"123||{PERMALINK}||Artist A - Song (DJ Flip)")
    assert exact.upload_acquired(db, {"id": "soundcloud_exact_123"}, 1) is False
    assert exact.status_for(db, url=PERMALINK)["matched"] is False


def test_upload_history_is_one_library_when_nobody_keeps_their_own(db, tmp_path, monkeypatch):
    import core.library_scope as scope
    monkeypatch.setattr(scope, "any_own_library_exists", lambda: False)
    real = tmp_path / "song.mp3"
    real.write_bytes(b"x")
    _downloaded(db, real)
    track = {"id": "soundcloud_exact_123"}
    assert exact.upload_acquired(db, track, 1) is True
    assert exact.upload_acquired(db, track, 2) is True


def test_upload_history_is_scoped_to_the_profiles_library(db, tmp_path, monkeypatch):
    """profile 2 keeps a library of its own; 1 and 3 read the shared one. a
    download counts for the profiles whose library its folder is."""
    import core.library_scope as scope
    from core.library2.library_roots import apply_roots, desired_roots

    shared, own = tmp_path / "shared", tmp_path / "own2"
    shared.mkdir()
    own.mkdir()
    with db._get_connection() as conn:
        apply_roots(conn.cursor(), desired_roots(str(shared), [(2, str(own))]))
        conn.commit()
    monkeypatch.setattr(scope, "any_own_library_exists", lambda: True)
    monkeypatch.setattr(scope, "library_scope_for_profile", lambda pid: 2 if pid == 2 else "shared")

    def downloaded(folder, sc_id):
        f = folder / f"{sc_id}.mp3"
        f.write_bytes(b"x")
        _downloaded(db, f, sc_id=sc_id)

    downloaded(own, "1")
    assert [exact.upload_acquired(db, {"id": "soundcloud_exact_1"}, p) for p in (1, 2, 3)] == [False, True, False]
    downloaded(shared, "2")
    assert [exact.upload_acquired(db, {"id": "soundcloud_exact_2"}, p) for p in (1, 2, 3)] == [True, False, True]
    # a path under no library folder can't be placed: it proves nothing
    downloaded(tmp_path, "3")
    assert [exact.upload_acquired(db, {"id": "soundcloud_exact_3"}, p) for p in (1, 2, 3)] == [False, False, False]


def test_status_by_url_after_the_import_recorded_the_match(db, client, tmp_path):
    from core.wishlist.resolution import _record_exact_match
    payload = exact.build_payload(_result(client), PRIVATE)
    published = tmp_path / "lib" / "song.mp3"
    lib_id = _library_file(db, published)
    _record_exact_match("soundcloud_exact_123", {"name": "Song", "artists": [{"name": "A"}],
                                                 "track_data": payload}, str(published), db, [1])
    match = db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123")
    assert int(match["library_track_id"]) == lib_id
    assert exact.status_for(db, url=PRIVATE)["state"] == "acquired"
    assert exact.status_for(db, url=PERMALINK)["state"] == "acquired"


def test_import_records_the_real_context_when_the_block_is_lost(db, orch, tmp_path):
    """the import's track info lost the block (a rebuilt payload); the stored
    wishlist row still has it and is read before the removal deletes it."""
    from core.wishlist.resolution import check_and_remove_from_wishlist
    _add(db, orch, url=PRIVATE)
    published = tmp_path / "lib" / "song.mp3"
    _library_file(db, published)

    class _Service:
        def mark_track_download_result(self, track_id, success=True, profile_ids=None, **_kw):
            return db.remove_from_wishlist(track_id, profile_id=1)

    context = {"track_info": {"id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "A"}]},
               "search_result": {}, "original_search_result": {}}
    assert check_and_remove_from_wishlist(context, wishlist_service=_Service(), database=db,
                                          published_path=str(published), owner_profiles=[1]) is True
    assert _rows(db) == []
    match = db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123")
    assert json.loads(match["source_context_json"]) == {
        "request_url_norm": PRIVATE, "permalink_url_norm": PERMALINK, "sc_id": "123"}
    assert exact.status_for(db, url=PRIVATE)["state"] == "acquired"


# ── import side ──────────────────────────────────────────────────────────────


def test_hand_tagged_import():
    from core.metadata.manual import is_manual_context
    assert is_manual_context({"track_info": {"id": "soundcloud_exact_1"}}) is True
    assert is_manual_context({"track_info": {"id": "sp1"}}) is False


def test_exact_source_recovered_from_row_and_playlist(db, orch, client):
    from core.playlists import user_playlists
    _add(db, orch)
    block = exact.exact_source_for({"id": "soundcloud_exact_123"}, db, 1)
    assert block["sc_id"] == "123"
    db.remove_from_wishlist("soundcloud_exact_123", profile_id=1)
    assert exact.exact_source_for({"id": "soundcloud_exact_123"}, db, 1) is None
    pid = user_playlists.create_playlist(db, 1, "Flips")
    user_playlists.add_exact_track(db, db.get_mirrored_playlist(pid),
                                   exact.build_payload(_result(client), PERMALINK))
    assert exact.exact_source_for({"id": "soundcloud_exact_123"}, db, 1)["sc_id"] == "123"
    assert exact.exact_source_for({"id": "soundcloud_exact_123"}, db, 2) is None


def test_playlist_sync_keeps_the_block(client):
    from core.automation.handlers.sync_playlist import auto_sync_playlist
    payload = exact.build_payload(_result(client), PERMALINK)
    row = {"id": 5, "source_track_id": payload["id"], "artist_name": "Artist A",
           "track_name": payload["name"],
           "extra_data": json.dumps({"discovered": True, "manual_match": True,
                                     "matched_data": payload})}
    db = MagicMock()
    db.get_mirrored_playlist.return_value = {"id": 1, "name": "Flips"}
    db.get_mirrored_playlist_tracks.return_value = [row]
    started = threading.Event()
    calls = []

    def _run_sync_task(*a, **k):
        calls.append(a)
        started.set()

    deps = MagicMock()
    deps.get_database.return_value = db
    deps.run_sync_task = _run_sync_task
    deps.load_sync_status_file.return_value = {}
    auto_sync_playlist({"playlist_id": "1"}, deps)
    assert started.wait(5)
    track = calls[0][2][0]
    assert track["id"] == "soundcloud_exact_123"
    assert track["soundcloud_exact"]["sc_id"] == "123"
