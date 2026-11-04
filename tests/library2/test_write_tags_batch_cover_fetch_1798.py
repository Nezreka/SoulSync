"""batch tag write downloaded every cover before the unchanged check (#1798).

the worker pre-fetched one cover per unique album url up front, then skipped
any file that already matched the db. rerunning a batch with cover embedding
on pulled every cover again even when every track was skipped.

Library v2 port: the batch is ``retag.write_tags`` (the legacy web_server
route is gone). It already reads a cover only for a file it writes, once per
album; these pin that, with upstream's four cases.
"""

from __future__ import annotations

import pytest

from core.library2 import retag

_TAGS = {
    "title": "t", "artist": "Artist", "album_artist": "Artist", "album": "One",
    "track_number": 1, "disc_number": 1, "has_cover_art": True, "error": None,
}


@pytest.fixture
def batch(imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Artist')").lastrowid
    albums, tracks = {}, []
    for title in ("One", "Two"):
        albums[title] = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, ?)",
            (artist, title)).lastrowid
    for name, album in (("a", "One"), ("b", "One"), ("c", "Two")):
        track = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number)"
                             " VALUES(?, ?, 1)", (albums[album], name)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, position)"
                     " VALUES(?, ?, 0)", (track, artist))
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
                     (track, f"/m/{name}.flac"))
        (tmp_path / f"{name}.flac").write_bytes(b"fake")
        tracks.append(track)
    conn.commit()

    calls = {"covers": [], "writes": []}
    changed = set()
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
                        lambda p: str(tmp_path / p.rsplit("/", 1)[-1]))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda path: {**_TAGS, "path": path})
    monkeypatch.setattr("core.tag_writer.build_tag_diff",
                        lambda existing, db: [{"field": "Title", "file_key": "title",
                                               "file_value": "t", "db_value": "t",
                                               "changed": existing["path"] in changed}])
    monkeypatch.setattr(retag, "_album_cover_data",
                        lambda _db, album_id: calls["covers"].append(album_id) or (b"img", "image/jpeg"))
    monkeypatch.setattr("core.tag_writer.write_tags_to_file",
                        lambda path, data, *, embed_cover, cover_data:
                        calls["writes"].append((path.rsplit("/", 1)[-1], cover_data))
                        or {"success": True})

    def run(names_changed, embed_cover=True):
        changed.clear()
        changed.update(str(tmp_path / f"{n}.flac") for n in names_changed)
        return retag.write_tags(legacy_db, tracks, embed_cover=embed_cover)

    yield run, calls, albums


def test_an_all_unchanged_batch_downloads_no_covers(batch):
    run, calls, _ = batch
    stats = run(set())
    assert stats["skipped"] == 3
    assert calls["writes"] == []
    assert calls["covers"] == []


def test_only_the_written_tracks_albums_get_a_cover(batch):
    run, calls, albums = batch
    stats = run({"c"})
    assert stats["written"] == 1 and stats["skipped"] == 2
    assert calls["covers"] == [albums["Two"]]
    assert calls["writes"] == [("c.flac", (b"img", "image/jpeg"))]


def test_a_cover_still_downloads_once_per_album(batch):
    run, calls, albums = batch
    run({"a", "b", "c"})
    assert sorted(calls["covers"]) == sorted(albums.values())
    assert [c for _, c in calls["writes"]] == [(b"img", "image/jpeg")] * 3


def test_cover_embedding_off_never_downloads(batch):
    run, calls, _ = batch
    run({"a", "c"}, embed_cover=False)
    assert calls["covers"] == []
    assert [c for _, c in calls["writes"]] == [None, None]
