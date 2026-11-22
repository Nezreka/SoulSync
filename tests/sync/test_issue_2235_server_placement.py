"""#2235: a mirror said "167 in library", the jellyfin playlist held 149.

carlosjfcasero's "Eva" (kids' songs, append mode, jellyfin). "in library" is
soulsync's answer (the matcher found the file); the server playlist is the
server's. nothing compared the two:

- the write was trusted. jellyfin answers 204 to an add it quietly drops
  (an id it doesn't know, a file already on the playlist), and a create whose
  second batch was refused still returned True
- the sync counted every match as synced, and a match with no server id at
  all was dropped by the dedupe without a word
- the scheduled run saw "167 matched last time" and skipped every time after,
  so nothing ever looked again ("Recorded no-op sync for 'Eva' (167 track(s)
  already present)")

these pin the readback: what the server holds is what counts as synced,
every matched track says whether it is on the playlist and why not, the
mirror page gets the counts, and the skip runs again while any are left.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from core.sync.server_placement import (
    DUPLICATE,
    NOT_ON_SERVER,
    ON_PLAYLIST,
    missing_from_server,
    norm_id,
    placement_of,
)
from services.sync_service import PlaylistSyncService


def _lib(rk, title):
    return SimpleNamespace(ratingKey=rk, title=title, artist="La Vaca Lola", album="Album")


def _src(sid, name):
    return SimpleNamespace(id=sid, name=name, artists=["La Vaca Lola"], album="Album",
                           duration_ms=160000)


def _service(matches, server_ids, server="jellyfin"):
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    service._find_track_in_media_server = AsyncMock(side_effect=matches)
    client = Mock()
    client.is_connected.return_value = True
    client.update_playlist.return_value = True
    client.append_to_playlist.return_value = True
    client._last_validated_write_skipped = []
    client.read_playlist_track_ids = Mock(return_value=server_ids)
    service._get_active_media_client = lambda *a, **k: (client, server)
    return service, client


def _guid(i):
    return f"{i:032x}"


# --- the pure part ------------------------------------------------------------

def test_jellyfin_ids_compare_with_or_without_hyphens():
    sent = ["0123456789ABCDEF0123456789abcdef"]
    held = ["01234567-89ab-cdef-0123-456789abcdef"]
    assert missing_from_server(sent, held) == set()
    assert norm_id(" AB-cd ") == "abcd"


def test_an_unread_playlist_is_no_answer_not_all_missing():
    assert missing_from_server(["a", "b"], None) is None
    assert missing_from_server(["a", "b"], []) == {"a", "b"}


def test_only_known_placements_count():
    assert placement_of({"server_placement": "duplicate"}) == DUPLICATE
    assert placement_of({"server_placement": "bogus"}) is None
    assert placement_of(None) is None


# --- the sync -----------------------------------------------------------------

def test_167_matched_against_149_on_the_server_says_which_18():
    # the reported shape: 167 found, the server keeps 149 of them
    files = [_lib(_guid(i), f"Song {i}") for i in range(167)]
    matches = [(f, 1.0) for f in files]
    held = [f.ratingKey for f in files[:149]]
    service, client = _service(matches, held)
    playlist = SimpleNamespace(id="auto_mirror_6", name="Eva",
                               tracks=[_src(str(i), f"Song {i}") for i in range(167)])

    result = asyncio.run(service.sync_playlist(playlist, sync_mode="append"))

    assert client.append_to_playlist.called
    client.read_playlist_track_ids.assert_called_once()
    assert result.matched_tracks == 167
    assert result.synced_tracks == 149
    assert result.unplaced_tracks == 18
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert [by_id[str(i)]["server_placement"] for i in range(149, 167)] == [NOT_ON_SERVER] * 18
    assert {by_id[str(i)]["server_placement"] for i in range(149)} == {ON_PLAYLIST}
    # still found: they ARE in the library, they're just not on the playlist
    assert by_id["150"]["status"] == "found"


def test_a_duplicate_is_on_the_playlist_once_and_says_so():
    a, b = _lib(_guid(1), "La Vaca Lola"), _lib(_guid(2), "Pollito Pio")
    matches = [(a, 1.0), (a, 0.9), (b, 1.0)]
    service, _client = _service(matches, [a.ratingKey, b.ratingKey])
    playlist = SimpleNamespace(id="pl", name="Eva", tracks=[
        _src("1", "La Vaca Lola"), _src("2", "La Vaca Lola (Remix)"), _src("3", "Pollito Pio")])

    result = asyncio.run(service.sync_playlist(playlist))

    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["server_placement"] == DUPLICATE
    assert by_id["2"]["folded_into"] == "La Vaca Lola - La Vaca Lola"
    assert by_id["1"]["server_placement"] == ON_PLAYLIST
    assert result.unplaced_tracks == 0
    assert result.synced_tracks == 2
    assert result.duplicate_tracks == 1


def test_a_match_with_no_server_id_is_not_on_the_server():
    # the dedupe drops a None key without a word: it counted as matched, and
    # nothing said it never went out
    a = _lib(_guid(1), "Song")
    ghost = _lib(None, "In the library by file only")
    service, _client = _service([(a, 1.0), (ghost, 1.0)], [a.ratingKey])
    playlist = SimpleNamespace(id="pl", name="Eva", tracks=[_src("1", "Song"), _src("2", "Ghost")])

    result = asyncio.run(service.sync_playlist(playlist))

    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["server_placement"] == NOT_ON_SERVER
    assert result.unplaced_tracks == 1
    assert result.synced_tracks == 1


def test_a_playlist_that_cant_be_read_back_changes_nothing():
    a, b = _lib(_guid(1), "A"), _lib(_guid(2), "B")
    service, _client = _service([(a, 1.0), (b, 1.0)], None)
    playlist = SimpleNamespace(id="pl", name="Eva", tracks=[_src("1", "A"), _src("2", "B")])

    result = asyncio.run(service.sync_playlist(playlist))

    assert result.synced_tracks == 2
    assert result.unplaced_tracks == 0
    assert all("server_placement" not in d for d in result.match_details)


def test_a_failed_write_is_not_read_back():
    a = _lib(_guid(1), "A")
    service, client = _service([(a, 1.0)], [])
    client.update_playlist.return_value = False
    playlist = SimpleNamespace(id="pl", name="Eva", tracks=[_src("1", "A")])

    result = asyncio.run(service.sync_playlist(playlist))

    client.read_playlist_track_ids.assert_not_called()
    assert result.unplaced_tracks == 0


@pytest.mark.parametrize("server", ["plex", "navidrome"])
def test_plex_and_navidrome_get_the_same_readback(server):
    a, b = _lib("101", "A"), _lib("102", "B")
    service, _client = _service([(a, 1.0), (b, 1.0)], ["101"], server=server)
    playlist = SimpleNamespace(id="pl", name="Eva", tracks=[_src("1", "A"), _src("2", "B")])

    result = asyncio.run(service.sync_playlist(playlist))

    assert result.unplaced_tracks == 1
    assert result.synced_tracks == 1


# --- the jellyfin client ------------------------------------------------------

def _jellyfin():
    from core.jellyfin_client import JellyfinClient
    client = JellyfinClient.__new__(JellyfinClient)
    client.base_url = "http://jf"
    client.api_key = "k"
    client.user_id = "u"
    client._auth_header = lambda: "MediaBrowser"
    client.ensure_connection = lambda: True
    return client


def test_a_create_with_a_refused_batch_is_not_a_success(monkeypatch):
    import requests
    client = _jellyfin()
    posts = []

    def fake_post(url, json=None, params=None, headers=None, timeout=None):
        posts.append(params)
        if params is None:  # the empty playlist
            return SimpleNamespace(status_code=200, json=lambda: {"Id": "pl1"}, text="",
                                   raise_for_status=lambda: None)
        # the second batch of ids is refused
        return SimpleNamespace(status_code=500 if len(posts) == 3 else 204, text="no")

    monkeypatch.setattr(requests, "post", fake_post)
    ids = [_guid(i) for i in range(167)]

    assert client._create_large_playlist("Eva", ids) is False
    batches = [p["Ids"].split(",") for p in posts if p]
    # every id still went out, in batches of at most 100
    assert [len(b) for b in batches] == [100, 67]
    assert sum(batches, []) == ids


def test_a_create_with_every_batch_taken_is_a_success(monkeypatch):
    import requests
    client = _jellyfin()

    def fake_post(url, json=None, params=None, headers=None, timeout=None):
        if params is None:
            return SimpleNamespace(status_code=200, json=lambda: {"Id": "pl1"}, text="",
                                   raise_for_status=lambda: None)
        return SimpleNamespace(status_code=204, text="")

    monkeypatch.setattr(requests, "post", fake_post)
    assert client._create_large_playlist("Eva", [_guid(i) for i in range(167)]) is True


def test_jellyfin_readback_returns_the_items_or_none():
    client = _jellyfin()
    client._target_playlist = lambda name, pid=None: SimpleNamespace(id="pl1")
    client._make_request = lambda path, params=None: {
        "Items": [{"Id": "a", "PlaylistItemId": "e1"}, {"Id": "b", "PlaylistItemId": "e2"}]}
    assert client.read_playlist_track_ids("Eva") == ["a", "b"]
    client._make_request = lambda path, params=None: None
    assert client.read_playlist_track_ids("Eva") is None
    client._target_playlist = lambda name, pid=None: None
    assert client.read_playlist_track_ids("Eva") is None


# --- what the sync stamps, and what the page reads ---------------------------

def test_the_sync_stamps_placement_on_the_mirror_rows(monkeypatch):
    from core.discovery import sync as dsync
    written = {}
    fake_db = SimpleNamespace(update_mirrored_track_extra_data=lambda tid, d: written.__setitem__(tid, d))
    monkeypatch.setattr("database.music_database.get_database", lambda: fake_db)
    tracks_json = [{"db_track_id": 11}, {"db_track_id": 12}, {"db_track_id": 13}]
    details = [
        {"index": 0, "status": "found", "server_placement": ON_PLAYLIST, "matched_track": {"id": "a"}},
        {"index": 1, "status": "found", "server_placement": DUPLICATE, "folded_into": "X - Y",
         "matched_track": {"id": "a"}},
        {"index": 2, "status": "not_found"},
    ]

    dsync._record_library_membership(tracks_json, details)

    assert written[11]["server_placement"] == ON_PLAYLIST
    assert written[12]["server_placement"] == DUPLICATE
    assert written[12]["server_placement_with"] == "X - Y"
    assert written[13]["server_placement"] is None


def test_the_page_counts_in_library_apart_from_on_the_playlist():
    from core.playlists.track_status import placement_counts, server_placement, track_status

    def row(placement, in_library=True):
        return {"extra_data": json.dumps({"in_library": in_library, "server_placement": placement})}

    rows = [row(ON_PLAYLIST)] * 149 + [row(NOT_ON_SERVER)] * 15 + [row(DUPLICATE)] * 3
    # a track not in the library has nothing to place, whatever is stamped
    rows.append(row(NOT_ON_SERVER, in_library=False))
    placements = [server_placement(r, track_status(r, set())) for r in rows]
    assert placement_counts(placements) == {
        ON_PLAYLIST: 149, NOT_ON_SERVER: 15, DUPLICATE: 3}


# --- the scheduled skip -------------------------------------------------------

@pytest.fixture()
def mdb(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _run_skip(db, monkeypatch, **extra_status):
    from core.automation.handlers.sync_playlist import auto_sync_playlist
    pk = db.mirror_playlist(
        source="spotify", source_playlist_id="p1", name="Eva",
        tracks=[{"track_name": "La Vaca Lola", "artist_name": "La Vaca Lola",
                 "source_track_id": "sp1"}],
        profile_id=1,
    )
    h = hashlib.md5("sp1".encode()).hexdigest()
    status = {"tracks_hash": h, "mirror_tracks_hash": h, "matched_tracks": 1,
              "quality_profile_id": db.get_mirrored_playlist(pk).get("quality_profile_id"),
              "sync_mode": "append", **extra_status}
    deps = MagicMock()
    deps.get_database.return_value = db
    deps.load_sync_status_file.return_value = {f"auto_mirror_{pk}": status}
    deps.config_manager.get.side_effect = (
        lambda key, default=None: "append" if key == "playlist_sync.mode" else default)
    deps.config_manager.get_active_media_server.return_value = "jellyfin"
    deps.server_playlist_exists = lambda name, profile_id=None: True
    started = threading.Event()
    deps.run_sync_task.side_effect = lambda *a, **k: started.set()
    monkeypatch.setattr("core.sync.library_presence.matched_tracks_still_present",
                        lambda *_a, **_k: True)
    return auto_sync_playlist({"playlist_id": str(pk)}, deps)


def test_every_track_placed_last_time_still_skips(mdb, monkeypatch):
    assert _run_skip(mdb, monkeypatch, unplaced_tracks=0)["status"] == "skipped"


def test_tracks_left_off_the_server_playlist_run_the_sync_again(mdb, monkeypatch):
    assert _run_skip(mdb, monkeypatch, unplaced_tracks=18)["status"] == "started"


def test_a_status_from_before_the_readback_runs_once(mdb, monkeypatch):
    # the reporter's own state: "167 matched" and no readback ever done
    assert _run_skip(mdb, monkeypatch)["status"] == "started"


def test_the_status_file_keeps_the_count():
    with open("web_server.py", encoding="utf-8") as f:
        src = f.read()
    keys_at = src.index("for key in ('matched_tracks', 'total_tracks'")
    assert "'unplaced_tracks'" in src[keys_at:keys_at + 300]


# --- a library row the server has no id for (#2235 follow-up) ----------------
#
# with a server asked for, a lib2 row the server hasn't mapped came back with
# its catalogue row id as if it were the server's (_rows_to_tracks:
# ``id=public_id or row['id']``). the sync sent it, jellyfin dropped it, plex
# would fetchItem an unrelated item. it is in the library, not on the server.

from tests.sync.test_issue_1715_album_fallback_artist_gate import _make_db  # noqa: E402
from tests.sync.test_issue_1721_stale_sync_match_cache import (  # noqa: E402
    _db_only_match, _media_match)


def _eva_db(tmp_path, server_id):
    return _make_db(
        tmp_path,
        artists=[('a-lola', 'La Vaca Lola', 'navidrome')],
        albums=[('al-lola', 'a-lola', 'Canciones de la Granja', 'navidrome')],
        tracks=[(server_id, 'al-lola', 'a-lola', 'La Vaca Lola', None, 'navidrome')],
    )


def _lola():
    from core.spotify_client import Track as SpotifyTrack
    return SpotifyTrack(id='sp-lola', name='La Vaca Lola', artists=['La Vaca Lola'],
                        album='Canciones de la Granja', duration_ms=0, popularity=0)


def test_the_library_says_when_the_id_is_not_the_servers(tmp_path):
    db = _eva_db(tmp_path, None)
    track, _ = db.check_track_exists('La Vaca Lola', 'La Vaca Lola', confidence_threshold=0.7,
                                     server_source='navidrome')
    assert track is not None
    assert track.server_id_known is False  # its id is the catalogue row's
    mapped = _eva_db(tmp_path / "m", 'nd-77')
    track, _ = mapped.check_track_exists('La Vaca Lola', 'La Vaca Lola', confidence_threshold=0.7,
                                         server_source='navidrome')
    assert track.server_id_known is True and str(track.id) == 'nd-77'
    # without a server asked for, nothing changes
    track, _ = db.check_track_exists('La Vaca Lola', 'La Vaca Lola', confidence_threshold=0.7)
    assert track.server_id_known is None


@pytest.mark.parametrize('match', [_media_match, _db_only_match], ids=['media', 'db-only'])
def test_an_unmapped_track_sends_no_id_and_caches_nothing(tmp_path, match):
    (tmp_path / "x").mkdir()
    db = _eva_db(tmp_path / "x", None)
    found, conf = match(db, _lola())
    assert found is not None and conf >= 0.7
    assert found.ratingKey is None and found.id is None
    assert found.title == 'La Vaca Lola'
    assert db.read_sync_match_cache('sp-lola', 'navidrome') is None


@pytest.mark.parametrize('match', [_media_match, _db_only_match], ids=['media', 'db-only'])
def test_a_mapped_track_still_syncs(tmp_path, match):
    (tmp_path / "x").mkdir()
    db = _eva_db(tmp_path / "x", 'nd-77')
    found, _ = match(db, _lola())
    assert str(found.ratingKey) == 'nd-77'


def test_an_unmapped_match_is_reported_and_never_sent():
    from services.sync_service import NotOnServerTrack
    a = _lib(_guid(1), "Pollito Pio")
    ghost = NotOnServerTrack(SimpleNamespace(title="La Vaca Lola", artist_name="La Vaca Lola"))
    service, client = _service([(a, 1.0), (ghost, 1.0)], [a.ratingKey])
    playlist = SimpleNamespace(id="pl", name="Eva", tracks=[_src("1", "Pollito Pio"),
                                                            _src("2", "La Vaca Lola")])

    result = asyncio.run(service.sync_playlist(playlist))

    (_name, pushed), = [c.args for c in client.update_playlist.call_args_list]
    assert [t.ratingKey for t in pushed] == [a.ratingKey]
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["status"] == "found"
    assert by_id["2"]["server_placement"] == NOT_ON_SERVER
    assert result.unplaced_tracks == 1 and result.synced_tracks == 1


def test_only_unmapped_matches_keep_the_server_playlist():
    # nothing the server can take: the playlist is left alone, not emptied,
    # and the sync isn't a failed write
    from services.sync_service import NotOnServerTrack
    ghost = NotOnServerTrack(SimpleNamespace(title="La Vaca Lola"))
    service, client = _service([(ghost, 1.0)], ["other"])
    playlist = SimpleNamespace(id="pl", name="Eva", tracks=[_src("1", "La Vaca Lola")])

    result = asyncio.run(service.sync_playlist(playlist))

    client.update_playlist.assert_not_called()
    assert result.errors == []
    assert result.unplaced_tracks == 1
    assert result.match_details[0]["server_placement"] == NOT_ON_SERVER


def test_the_server_playlist_editor_refuses_a_library_row_id(tmp_path, monkeypatch):
    import web_server
    db = _eva_db(tmp_path, None)  # a library row jellyfin has no id for
    with db._get_connection() as conn:
        row_id = conn.execute("SELECT id FROM lib2_tracks").fetchone()[0]
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    with web_server.app.test_request_context():
        refused = web_server._not_on_server_refusal("navidrome", row_id)
        assert refused is not None and refused[1] == 400
        assert "not on Navidrome yet" in refused[0].get_json()["error"]
        # an id soulsync has never seen, a non-numeric id, soulsync itself: as before
        assert web_server._not_on_server_refusal("navidrome", 999999) is None
        assert web_server._not_on_server_refusal("navidrome", "abc") is None
        assert web_server._not_on_server_refusal("soulsync", row_id) is None


def test_the_editor_lets_a_mapped_row_through(tmp_path, monkeypatch):
    import web_server
    db = _eva_db(tmp_path, '4242')  # the server's own id is numeric too
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    with web_server.app.test_request_context():
        assert web_server._not_on_server_refusal("navidrome", "4242") is None


def test_a_failed_read_does_not_refuse(monkeypatch):
    import web_server
    db = MagicMock()
    db.get_track_by_server_id.side_effect = RuntimeError("db gone")
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    with web_server.app.test_request_context():
        assert web_server._not_on_server_refusal("jellyfin", 18) is None


def test_add_and_replace_both_ask_before_writing():
    with open("web_server.py", encoding="utf-8") as f:
        src = f.read()
    for route in ("def server_playlist_replace_track(", "def server_playlist_add_track("):
        start = src.index(route)
        body = src[start:src.index("\n@app.route", start)]
        assert "_not_on_server_refusal(active_server" in body, route
