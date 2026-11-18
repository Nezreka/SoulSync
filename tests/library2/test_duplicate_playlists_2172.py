"""#2172: Keep Best and playlists.

Two copies in the same playlists: the removed one loses no entry, so it is
not protected. Playlists the user names never protect. With moving on, a
copy in a playlist the kept copy is not in has its entry pointed at the kept
copy before it goes; a move that fails keeps the copy. One server item behind
two files of one track is attributed by the path the server plays, or both
stay. The preview changes nothing.
"""

from contextlib import closing
from types import SimpleNamespace

import pytest

from tests.library2.test_native_duplicate_review import Config, copy, states  # noqa: F401
from tests.library2.test_native_duplicate_review import library  # noqa: F401 - fixture
from core.library2 import duplicate_review as dr


def _map(db, tid, sid, path=None, server="plex"):
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type,entity_id,server_source,server_id,server_path) "
                     "VALUES('track',?,?,?,?)", (tid, server, sid, path))
        conn.commit()


def _review(library, membership, **kw):
    db, config, *_ = library
    return dr.find_duplicate_candidates(db, config, server_source="plex", playlist_membership=membership, **kw)


def _apply(library, candidate, membership, **kw):
    db, config, _root, transfer = library
    return dr.apply_keep_best(db, candidate, config_manager=config, transfer_folder=str(transfer),
                              approved=True, server_source="plex", playlist_membership=membership, **kw)


def _row(candidate, fid):
    return next(t for t in candidate["tracks"] if t["file_id"] == fid)


def test_both_in_the_same_playlist_keep_best_removes_the_worse(library):
    db = library[0]
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    b, bf, pb = copy(library, album="Other", fmt="flac")
    _map(db, a, "pa")
    _map(db, b, "pb")
    membership = {"pa": ["Road Trip"], "pb": ["Road Trip", "Gym"]}
    candidate = _review(library, membership)[0]
    assert candidate["recommended_file_id"] == bf
    worse = _row(candidate, af)
    assert worse["protected_reasons"] == []
    assert worse["playlists_covered"] == ["Road Trip"]

    result = _apply(library, candidate, membership)
    assert result["success"] is True and result["removed_file_ids"] == [af]
    assert not pa.exists() and pb.exists()


def test_a_playlist_the_keeper_is_not_in_still_protects(library):
    db = library[0]
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    b, bf, _pb = copy(library, album="Other", fmt="flac")
    _map(db, a, "pa")
    _map(db, b, "pb")
    membership = {"pa": ["Road Trip", "Chill"], "pb": ["Road Trip"]}
    candidate = _review(library, membership)[0]
    # keeper among the playlist copies is the better one
    assert candidate["recommended_file_id"] == bf
    row = _row(candidate, af)
    assert row["protected_reasons"] == ["playlist_reference"]
    assert row["playlists_blocked"] == "not_in_kept"
    result = _apply(library, candidate, membership)
    assert result["removed_file_ids"] == [] and pa.exists()


def test_named_playlists_never_protect(library):
    db, config, *_ = library
    config.values[dr.SETTINGS_KEY] = {"playlists_that_never_protect": "Unsorted; Inbox"}
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    b, bf, _pb = copy(library, album="Other", fmt="flac")
    _map(db, a, "pa")
    membership = {"pa": ["unsorted"]}
    candidate = _review(library, membership)[0]
    # an ignored playlist no longer makes the worse copy the keeper either
    assert candidate["recommended_file_id"] == bf
    row = _row(candidate, af)
    assert row["protected_reasons"] == [] and row["ignored_playlists"] == ["unsorted"]
    result = _apply(library, candidate, membership)
    assert result["removed_file_ids"] == [af] and not pa.exists()


def test_two_files_of_one_track_follow_the_path_the_server_plays(library):
    db, config, root, _t = library
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    with closing(db._get_connection()) as conn:
        better = root / "Other" / "song.flac"
        better.parent.mkdir()
        better.write_bytes(b"better")
        bf = conn.execute("INSERT INTO lib2_track_files(track_id,path,format,bit_depth) VALUES(?,?,?,24)",
                          (a, str(better), "flac")).lastrowid
        conn.commit()
    # the server's one item plays the flac, at its own mount
    _map(db, a, "item", path="/data/music/Other/song.flac")
    membership = {"item": ["Road Trip"]}
    candidate = _review(library, membership)[0]
    assert _row(candidate, bf)["playlists"] == ["Road Trip"]
    assert _row(candidate, af)["playlists"] == []
    assert candidate["recommended_file_id"] == bf
    result = _apply(library, candidate, membership)
    assert result["removed_file_ids"] == [af] and not pa.exists() and better.exists()


def test_one_server_item_with_no_path_keeps_both(library):
    db, _config, root, _t = library
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    with closing(db._get_connection()) as conn:
        better = root / "Other" / "song.flac"
        better.parent.mkdir()
        better.write_bytes(b"better")
        conn.execute("INSERT INTO lib2_track_files(track_id,path,format,bit_depth) VALUES(?,?,?,24)",
                     (a, str(better), "flac"))
        conn.commit()
    _map(db, a, "item")
    membership = {"item": ["Road Trip"]}
    candidate = _review(library, membership)[0]
    removable = [t for t in candidate["tracks"] if t["file_id"] != candidate["recommended_file_id"]]
    assert removable[0]["playlists_blocked"] == "same_server_item"
    assert removable[0]["protected_reasons"] == ["playlist_reference"]
    result = _apply(library, candidate, membership)
    assert result["removed_file_ids"] == [] and pa.exists() and better.exists()


@pytest.fixture
def server(monkeypatch):
    calls = []
    outcome = {"moved": None, "failed": [], "skipped": []}

    def move_entries(srv, client, titles, old, new, playlists=None):
        calls.append((srv, list(titles), old, new))
        return {"moved": list(titles) if outcome["moved"] is None else outcome["moved"],
                "failed": outcome["failed"], "skipped": outcome["skipped"]}

    monkeypatch.setattr("core.sync.playlist_entry_move.move_entries", move_entries)
    monkeypatch.setattr("core.sync.playlist_entry_move.server_playlists", lambda client: [])
    monkeypatch.setattr("core.library.playlist_membership._active_server_and_client",
                        lambda: ("plex", SimpleNamespace()))
    return SimpleNamespace(calls=calls, outcome=outcome)


def test_move_points_the_entry_at_the_kept_copy_then_removes(library, server):
    db = library[0]
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    b, bf, pb = copy(library, album="Other", fmt="flac")
    _map(db, a, "pa")
    _map(db, b, "pb")
    with closing(db._get_connection()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sync_match_cache (spotify_track_id TEXT, normalized_title TEXT,"
                     " normalized_artist TEXT, server_source TEXT, server_track_id, confidence REAL)")
        conn.execute("INSERT INTO sync_match_cache(spotify_track_id,normalized_title,normalized_artist,"
                     "server_source,server_track_id,confidence) VALUES('sp1','song','artist','plex','pa',1.0)")
        conn.commit()
    membership = {"pa": ["Road Trip", "Chill"], "pb": ["Road Trip"]}
    candidate = _review(library, membership)[0]

    preview = dr.preview_keep_best(db, candidate, config_manager=library[1], keep_file_id=bf,
                                   move_playlist_entries=True, server_source="plex",
                                   playlist_membership=membership)
    rows = {r["file_id"]: r for r in preview["copies"]}
    assert rows[af]["action"] == "remove" and rows[af]["playlist_moves"] == ["Chill"]
    assert rows[af]["playlists_covered"] == ["Road Trip"]
    assert server.calls == [] and pa.exists()  # the preview wrote nothing

    result = _apply(library, candidate, membership, keep_file_id=bf, move_playlist_entries=True)
    assert result["success"] is True, result
    assert server.calls == [("plex", ["Chill"], "pa", "pb")]
    assert result["removed_file_ids"] == [af] and not pa.exists()
    assert result["playlist_moves"] == [{"file_id": af, "playlists": ["Chill"]}]
    with closing(db._get_connection()) as conn:
        assert conn.execute("SELECT server_track_id FROM sync_match_cache").fetchone()[0] == "pb"


def test_a_move_that_fails_keeps_the_copy(library, server):
    db = library[0]
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    b, bf, _pb = copy(library, album="Other", fmt="flac")
    _map(db, a, "pa")
    _map(db, b, "pb")
    server.outcome.update(moved=[], failed=[("Chill", "the server did not take the change")])
    membership = {"pa": ["Chill"]}
    candidate = _review(library, membership)[0]
    result = _apply(library, candidate, membership, keep_file_id=bf, move_playlist_entries=True)
    assert result["success"] is True
    assert result["removed_file_ids"] == [] and pa.exists() and states(library)[af] == "active"
    assert {"file_id": af, "reasons": ["playlist_reference"]} in result["protected_files"]


def test_no_move_when_the_kept_copy_is_not_on_the_server(library, server):
    db = library[0]
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    _b, bf, _pb = copy(library, album="Other", fmt="flac")
    _map(db, a, "pa")
    membership = {"pa": ["Chill"]}
    candidate = _review(library, membership)[0]
    result = _apply(library, candidate, membership, keep_file_id=bf, move_playlist_entries=True)
    assert server.calls == []
    assert result["removed_file_ids"] == [] and pa.exists()


def test_the_job_setting_turns_moving_on(library, server):
    db, config, *_ = library
    config.values[dr.SETTINGS_KEY] = {"move_playlist_entries": True}
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    b, bf, _pb = copy(library, album="Other", fmt="flac")
    _map(db, a, "pa")
    _map(db, b, "pb")
    membership = {"pa": ["Chill"]}
    candidate = _review(library, membership)[0]
    result = _apply(library, candidate, membership, keep_file_id=bf)
    assert server.calls and result["removed_file_ids"] == [af]


# ── the fix action ──

@pytest.mark.parametrize("action,expected", [
    ("keep_best", (None, False, None)),
    ("keep_best:confirmed", (None, True, None)),
    ("keep_best_confirmed", (None, True, None)),
    ("file-7:move", (7, False, True)),
    ("file-7:confirmed:nomove", (7, True, False)),
])
def test_fix_action_flags(action, expected):
    from core.repair_worker import _duplicate_fix_action
    assert _duplicate_fix_action(action) == expected


def test_unknown_fix_action_flags_are_refused():
    from core.repair_worker import _duplicate_fix_action
    with pytest.raises(ValueError):
        _duplicate_fix_action("keep_best:everything")


# ── the server write ──

class _Navidrome:
    def __init__(self, ids, readonly=False):
        self.ids = list(ids)
        self.playlist = SimpleNamespace(id="p1", title="Chill", readonly=readonly)
        self.writes = []

    def get_all_playlists(self):
        return [self.playlist]

    def get_playlist_tracks(self, playlist_id):
        return [SimpleNamespace(ratingKey=i) for i in self.ids]

    def create_playlist(self, name, tracks, playlist_id=None):
        self.writes.append((name, playlist_id))
        self.ids = [t.ratingKey for t in tracks]
        return True


def test_navidrome_entry_is_replaced_in_place():
    from core.sync.playlist_entry_move import move_entries
    client = _Navidrome(["x", "old", "y"])
    out = move_entries("navidrome", client, ["Chill"], "old", "new")
    assert out == {"moved": ["Chill"], "skipped": [], "failed": []}
    assert client.ids == ["x", "new", "y"] and client.writes == [("Chill", "p1")]


def test_a_smart_playlist_is_never_written():
    from core.sync.playlist_entry_move import move_entries
    client = _Navidrome(["old"], readonly=True)
    out = move_entries("navidrome", client, ["Chill"], "old", "new")
    assert out["moved"] == [] and out["skipped"] == [("Chill", "smart or read-only playlist")]
    assert client.writes == []


def test_a_write_the_server_did_not_take_is_a_failure():
    from core.sync.playlist_entry_move import move_entries
    client = _Navidrome(["old"])
    client.create_playlist = lambda *a, **k: True  # says yes, changes nothing
    out = move_entries("navidrome", client, ["Chill"], "old", "new")
    assert out["moved"] == [] and out["failed"]


def test_the_worker_previews_a_finding_without_touching_it(library):
    import json
    from core.repair_worker import RepairWorker
    db, config, *_ = library
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    _b, bf, pb = copy(library, album="Other", fmt="flac")
    candidate = dr.find_duplicate_candidates(db, config, playlist_membership={})[0]
    with closing(db._get_connection()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS repair_findings (id INTEGER PRIMARY KEY, finding_type TEXT, details_json TEXT)")
        conn.execute("INSERT INTO repair_findings VALUES (5, 'native_duplicate_tracks', ?)", (json.dumps(candidate),))
        conn.commit()
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager = db, config
    import core.library2.duplicate_review as module
    original = module._playlist_context
    module._playlist_context = lambda membership, source: ({}, None)
    try:
        preview = worker.duplicate_finding_preview(5, f"file-{bf}:nomove")
    finally:
        module._playlist_context = original
    assert preview["success"] is True and preview["kept_file_id"] == bf
    assert preview["move_playlist_entries"] is False
    assert {r["file_id"]: r["action"] for r in preview["copies"]} == {af: "remove", bf: "keep"}
    assert pa.exists() and pb.exists()
    assert worker.duplicate_finding_preview(99) is None
    assert worker.duplicate_finding_preview(5, "delete-everything")["success"] is False
