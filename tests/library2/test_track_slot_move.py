"""#2193: move a track you have into an album's missing slot, and a
re-identify / reassign replace that folds the old row instead of leaving it.

Vidariondr: "It shouldn't copy, it should move, so there is no duplicates."
"""

from __future__ import annotations

import os
import sqlite3

import pytest
from tests.lib2_seed import row_conn

from core.library2.track_slot_move import (
    SlotMoveError,
    apply_slot_move,
    fold_track_into,
    preview_slot_move,
    retire_emptied_album,
)


class _DB:
    def __init__(self, path: str):
        self.path = self.database_path = path

    def _get_connection(self) -> sqlite3.Connection:
        return row_conn(self.path)

    # the wishlist mirror's side: these tests keep no wishlist
    def remove_release_from_wishlist(self, *_a, **_k):
        return False

    def remove_from_wishlist(self, *_a, **_k):
        return False

    def add_to_wishlist(self, *_a, **_k):
        return False


def _mp3(path) -> str:
    from mutagen.id3 import ID3, TALB, TIT2, TPE1, TRCK
    path.write_bytes((b"\xff\xfb\x90\x00" + bytes(413)) * 40)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Punkrocker (radio edit)"))
    tags.add(TALB(encoding=3, text="Punkrocker (radio edit)"))
    tags.add(TPE1(encoding=3, text="Teddybears"))
    tags.add(TRCK(encoding=3, text="32"))
    tags.save(str(path))
    return str(path)


@pytest.fixture(autouse=True)
def _fresh_locks():
    from core.library2 import locks
    locks.reset_for_tests()
    yield
    locks.reset_for_tests()


@pytest.fixture
def lib(tmp_path):
    """The issue's library: a stray single holding the song, and the
    soundtrack album where it is the one missing track."""
    (tmp_path / "db").mkdir()
    music = tmp_path / "music"
    music.mkdir()
    path = str(tmp_path / "db" / "lib2.db")
    conn = row_conn(path)
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    teddy = cur.execute("INSERT INTO lib2_artists(name) VALUES('Teddybears')").lastrowid
    various = cur.execute("INSERT INTO lib2_artists(name) VALUES('The Crabjoys OST')").lastrowid
    single = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,origin)"
        " VALUES(?,'Punkrocker (radio edit)','single','library')", (teddy,)).lastrowid
    ost = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,year,origin,spotify_id)"
        " VALUES(?,'The Crabjoys','album',2006,'library','sp-ost')", (various,)).lastrowid
    for album, artist in ((single, teddy), (ost, various)):
        cur.execute("INSERT INTO lib2_album_artists(album_id,artist_id,role) VALUES(?,?,'primary')",
                    (album, artist))
    source = cur.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,play_count,monitored)"
        " VALUES(?,'Punkrocker (radio edit)',32,1,7,1)", (single,)).lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)", (source, teddy))
    audio = _mp3(music / "32 Punkrocker (radio edit).mp3")
    file_id = cur.execute(
        "INSERT INTO lib2_track_files(track_id,path,format,is_primary,file_state,size)"
        " VALUES(?,?,'mp3',1,'active',4930000)", (source, audio)).lastrowid
    kept = cur.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,monitored)"
        " VALUES(?,'Walking on Air',31,1,1)", (ost,)).lastrowid
    cur.execute("INSERT INTO lib2_track_files(track_id,path,format,is_primary,file_state)"
                " VALUES(?,?,'mp3',1,'active')", (kept, str(music / "31.mp3")))
    target = cur.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,play_count,monitored)"
        " VALUES(?,'Punkrocker',32,1,2,1)", (ost,)).lastrowid
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.wanted import recompute_wanted
    for track_id in (source, kept, target):
        record_rule(conn, "track", track_id, True, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[source, kept, target])
    conn.commit()
    conn.close()
    return {"db": _DB(path), "path": path, "single": single, "ost": ost, "source": source,
            "target": target, "kept": kept, "file_id": file_id, "audio": audio, "tmp": music}


def _calls():
    seen = {"tags": [], "organize": []}

    def write_tags(database, track_ids):
        seen["tags"].append(list(track_ids))
        return {"written": 1, "skipped": 0, "failed": 0, "errors": []}

    def enqueue(database, album_id, track_ids):
        seen["organize"].append((album_id, list(track_ids)))
        return {"queued": True, "queue_id": "q1", "position": 1}

    return seen, write_tags, enqueue


class _Config:
    def __init__(self, organize=True):
        self.organize = organize

    def get(self, key, default=None):
        if key == "file_organization.enabled":
            return self.organize
        return default


def test_move_attaches_the_file_and_removes_the_old_row(lib):
    seen, write_tags, enqueue = _calls()
    result = apply_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config(),
                             write_tags_fn=write_tags, enqueue_fn=enqueue)
    assert result["success"] is True
    conn = row_conn(lib["path"])
    # the file row moved to the slot: one row, now the slot's, still primary
    rows = conn.execute("SELECT track_id, is_primary FROM lib2_track_files WHERE path=?",
                        (lib["audio"],)).fetchall()
    assert [(r["track_id"], r["is_primary"]) for r in rows] == [(lib["target"], 1)]
    # the old row is gone, not left behind as a missing track
    assert conn.execute("SELECT 1 FROM lib2_tracks WHERE id=?", (lib["source"],)).fetchone() is None
    # plays carried over
    assert conn.execute("SELECT play_count FROM lib2_tracks WHERE id=?",
                        (lib["target"],)).fetchone()[0] == 9
    # a redirect, so a link to the old track opens the moved one
    from core.library2.catalogue_identity import resolve_native_id
    assert resolve_native_id(conn, "track", lib["source"]) == lib["target"]
    # the emptied single left the library: no duplicate album on the artist page
    assert conn.execute("SELECT 1 FROM lib2_albums WHERE id=?", (lib["single"],)).fetchone() is None
    assert result["source_album"] == "deleted"
    conn.close()


def test_move_moves_never_copies(lib):
    seen, write_tags, enqueue = _calls()
    before = sorted(os.listdir(lib["tmp"]))
    apply_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config(),
                    write_tags_fn=write_tags, enqueue_fn=enqueue)
    # no staging copy, no second file anywhere
    assert sorted(os.listdir(lib["tmp"])) == before
    conn = row_conn(lib["path"])
    assert conn.execute("SELECT COUNT(*) FROM lib2_track_files WHERE path=?",
                        (lib["audio"],)).fetchone()[0] == 1
    conn.close()


def test_move_retags_and_files_only_the_moved_track(lib):
    seen, write_tags, enqueue = _calls()
    result = apply_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config(),
                             write_tags_fn=write_tags, enqueue_fn=enqueue)
    assert seen["tags"] == [[lib["target"]]]
    assert seen["organize"] == [(lib["ost"], [lib["target"]])]
    assert result["organize"]["queued"] is True


def test_move_leaves_the_folder_when_organization_is_off(lib):
    seen, write_tags, enqueue = _calls()
    result = apply_slot_move(lib["db"], lib["source"], lib["target"],
                             config_manager=_Config(organize=False),
                             write_tags_fn=write_tags, enqueue_fn=enqueue)
    assert seen["organize"] == []
    assert result["organize"] == {"queued": False, "reason": "organization_off"}


def test_move_writes_the_slot_tags_into_the_file(lib):
    from mutagen.id3 import ID3
    _seen, _write, enqueue = _calls()
    apply_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config(),
                    enqueue_fn=enqueue)
    tags = ID3(lib["audio"])
    assert str(tags["TIT2"]) == "Punkrocker"
    assert str(tags["TALB"]) == "The Crabjoys"


def test_preview_changes_nothing_and_shows_the_tags(lib):
    preview = preview_slot_move(lib["db"], lib["source"], lib["target"],
                                config_manager=_Config(organize=False))
    assert preview["success"] is True
    assert preview["source_album_left_empty"] is True
    fields = {c["field"].lower(): c for c in preview["tags"]["changes"]}
    assert any("title" in f for f in fields)
    assert any("album" in f for f in fields)
    assert preview["path"]["new"] is None
    conn = row_conn(lib["path"])
    assert conn.execute("SELECT track_id FROM lib2_track_files WHERE path=?",
                        (lib["audio"],)).fetchone()[0] == lib["source"]
    conn.close()


def test_preview_plans_the_new_path_without_moving(lib, monkeypatch):
    seen = {}

    def fake_plan(conn, album_id, **kwargs):
        # the planner sees the file on the slot, inside the dry run
        seen["owner"] = conn.execute("SELECT track_id FROM lib2_track_files WHERE path=?",
                                     (lib["audio"],)).fetchone()[0]
        return {"success": True, "tracks": [
            {"track_id": lib["target"], "current_path": "Teddybears/x.mp3",
             "new_path": "The Crabjoys OST/The Crabjoys/32 - Punkrocker.mp3",
             "unchanged": False, "collision": False}]}

    monkeypatch.setattr("core.library2.reorganize_plan.plan_album_reorganize", fake_plan)
    preview = preview_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config())
    assert seen["owner"] == lib["target"]
    assert preview["path"]["new"].endswith("32 - Punkrocker.mp3")
    conn = row_conn(lib["path"])
    # rolled back: the file is still the single's
    assert conn.execute("SELECT track_id FROM lib2_track_files WHERE path=?",
                        (lib["audio"],)).fetchone()[0] == lib["source"]
    conn.close()


def test_a_locked_album_is_refused(lib):
    from core.library2.locks import set_lock
    conn = row_conn(lib["path"])
    set_lock(conn, "album", lib["ost"])
    conn.commit()
    conn.close()
    with pytest.raises(SlotMoveError) as err:
        preview_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config())
    assert err.value.status == 423
    _seen, write_tags, enqueue = _calls()
    with pytest.raises(SlotMoveError):
        apply_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config(),
                        write_tags_fn=write_tags, enqueue_fn=enqueue)
    conn = row_conn(lib["path"])
    assert conn.execute("SELECT track_id FROM lib2_track_files WHERE path=?",
                        (lib["audio"],)).fetchone()[0] == lib["source"]
    conn.close()


def test_a_slot_with_a_file_is_refused(lib):
    with pytest.raises(SlotMoveError) as err:
        preview_slot_move(lib["db"], lib["source"], lib["kept"], config_manager=_Config())
    assert err.value.status == 409


def test_a_track_without_a_file_is_refused(lib):
    conn = row_conn(lib["path"])
    conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE id=?", (lib["file_id"],))
    conn.commit()
    conn.close()
    with pytest.raises(SlotMoveError) as err:
        preview_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config())
    assert err.value.status == 409


def test_a_provider_listed_album_stays_listed_not_owned(lib):
    conn = row_conn(lib["path"])
    conn.execute("UPDATE lib2_albums SET spotify_id='sp-single' WHERE id=?", (lib["single"],))
    conn.commit()
    conn.close()
    _seen, write_tags, enqueue = _calls()
    result = apply_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config(),
                             write_tags_fn=write_tags, enqueue_fn=enqueue)
    assert result["source_album"] == "forgotten"
    conn = row_conn(lib["path"])
    row = conn.execute("SELECT origin, monitored FROM lib2_albums WHERE id=?",
                       (lib["single"],)).fetchone()
    assert (row["origin"], row["monitored"]) == ("discography", 0)
    conn.close()


def test_an_album_with_other_files_stays(lib):
    conn = row_conn(lib["path"])
    other = conn.execute("INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,'B-side',2)",
                         (lib["single"],)).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state)"
                 " VALUES(?,'/m/b.mp3',1,'active')", (other,))
    conn.commit()
    assert retire_emptied_album(conn, lib["single"]) is None
    conn.close()


# -- reassign / re-identify replace -------------------------------------------

def test_a_replace_folds_the_old_row_and_its_emptied_album(lib):
    """the reassign half of #2193: after the re-import landed, the old row
    went missing but stayed, on an album that stayed, and was still wanted"""
    from core.imports.rematch_hints import delete_replaced_track
    conn = row_conn(lib["path"])
    new_path = str(lib["tmp"] / "new.mp3")
    open(new_path, "wb").close()
    # what the import made: a track holding the re-imported copy
    conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state)"
                 " VALUES(?,?,1,'active')", (lib["target"], new_path))
    conn.commit()
    removed = delete_replaced_track(conn.cursor(), lib["source"], new_paths=[new_path],
                                    file_id=lib["file_id"])
    conn.commit()
    assert removed == lib["audio"]
    assert not os.path.exists(lib["audio"])
    assert conn.execute("SELECT 1 FROM lib2_tracks WHERE id=?", (lib["source"],)).fetchone() is None
    assert conn.execute("SELECT 1 FROM lib2_albums WHERE id=?", (lib["single"],)).fetchone() is None
    assert conn.execute("SELECT play_count FROM lib2_tracks WHERE id=?",
                        (lib["target"],)).fetchone()[0] == 9
    conn.close()


def test_a_replace_with_no_known_landing_keeps_the_row(lib):
    from core.imports.rematch_hints import delete_replaced_track
    conn = row_conn(lib["path"])
    delete_replaced_track(conn.cursor(), lib["source"], new_paths=[str(lib["tmp"] / "nowhere.mp3")],
                          file_id=lib["file_id"])
    conn.commit()
    assert conn.execute("SELECT 1 FROM lib2_tracks WHERE id=?", (lib["source"],)).fetchone()
    conn.close()


def test_fold_refuses_a_track_onto_itself(lib):
    conn = row_conn(lib["path"])
    with pytest.raises(SlotMoveError):
        fold_track_into(conn, lib["source"], lib["source"])
    conn.close()


# -- the routes ------------------------------------------------------------------

@pytest.fixture
def client(lib, monkeypatch):
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app, get_database=lambda: lib["db"],
        config_get=lambda key, default=None: {"features.library_v2": True}.get(key, default),
        config_manager=_Config(organize=False), profile_id_getter=lambda: 1)
    monkeypatch.setattr("core.library2.retag.write_tags",
                        lambda database, ids: {"written": 1, "skipped": 0, "failed": 0, "errors": []})
    return app.test_client()


def test_routes_preview_then_apply(lib, client):
    url = f"/api/library/v2/tracks/{lib['target']}/move-into"
    resp = client.post(url + "/preview", json={"source_track_id": lib["source"]})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["target"]["id"] == lib["target"]
    resp = client.post(url, json={"source_track_id": lib["source"]})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["removed_track_id"] == lib["source"]


def test_route_needs_a_source(lib, client):
    resp = client.post(f"/api/library/v2/tracks/{lib['target']}/move-into", json={})
    assert resp.status_code == 400


def test_route_is_admin_only(lib):
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app, get_database=lambda: lib["db"],
        config_get=lambda key, default=None: {"features.library_v2": True}.get(key, default),
        config_manager=_Config(organize=False), profile_id_getter=lambda: 2)
    resp = app.test_client().post(f"/api/library/v2/tracks/{lib['target']}/move-into",
                                  json={"source_track_id": lib["source"]})
    assert resp.status_code == 403
    conn = row_conn(lib["path"])
    assert conn.execute("SELECT 1 FROM lib2_tracks WHERE id=?", (lib["source"],)).fetchone()
    conn.close()


def test_a_track_with_a_copy_in_another_library_is_refused(lib, monkeypatch):
    """the row moves whole, so a copy another library holds would go along"""
    from core.library_scope import library_scope
    monkeypatch.setattr("core.library_scope.any_own_library_exists", lambda: True)
    conn = row_conn(lib["path"])
    conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state,owner_profile_id)"
                 " VALUES(?,'/other/copy.mp3',0,'active',2)", (lib["source"],))
    conn.commit()
    conn.close()
    with library_scope("shared"):
        with pytest.raises(SlotMoveError) as err:
            preview_slot_move(lib["db"], lib["source"], lib["target"], config_manager=_Config())
    assert err.value.status == 409
    assert "another library" in str(err.value)
