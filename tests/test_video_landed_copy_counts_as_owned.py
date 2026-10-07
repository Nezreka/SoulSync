"""the video drain re-grabbed the same release every hour.

a below-cutoff grab (720p under a 1080p cutoff) lands, and its wish stays
open so a better copy can replace it later. the drain then only takes a
strictly better release than the owned one. but "owned" came only from the
media server's scan, which lags, or never sees a library that isn't picked.
so the drain saw nothing owned and grabbed the same 720p again, every hour:
shark tank S18E02 34 times in two days on a real install, and a jellyfin
user's specials the same way.

now a copy soulsync itself placed counts as owned while its file is still
there. real VideoDatabase on a tmp file.
"""

from __future__ import annotations

import json

import pytest

from core.automation.handlers.video_process_wishlist import acceptable_candidates, annotate_upgrades
from core.video.quality_eval import resolution_rank
from database.video_database import VideoDatabase

SHARK = 30703


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


def _landed(db, path, *, kind="show", media_id=SHARK, source="tmdb", season=18, episode=2,
            label="WEBDL-720p", release="Shark Tank S18E02 720p WEB h264-EDITH", dl_id=1):
    ctx = {} if kind == "movie" else {"season": season, "episode": episode}
    db.record_download_history({
        "id": dl_id, "status": "completed", "kind": kind, "title": "x",
        "release_title": release, "dest_path": str(path), "quality_label": label,
        "media_id": str(media_id), "media_source": source, "search_ctx": json.dumps(ctx)})


def _wish_episode(db, season=18, episode=2):
    db.add_episodes_to_wishlist(SHARK, "Shark Tank", [{"season_number": season, "episode_number": episode}])


def _row(rows, season=18, episode=2):
    return next(r for r in rows if r["season_number"] == season and r["episode_number"] == episode)


def _cands(*labels):
    return [{"title": f"Shark Tank S18E02 {lab}", "quality_label": lab,
             "resolution": lab.split("-")[-1], "accepted": True} for lab in labels]


def test_a_landed_copy_counts_as_owned(db, tmp_path):
    f = tmp_path / "Shark Tank - S18E02 WEBDL-720p.mkv"
    f.write_bytes(b"x")
    _wish_episode(db)
    _landed(db, f)
    row = _row(db.episode_wishlist_to_download(due_only=False))
    assert row["owned"] == 1 and "720p" in row["owned_resolutions"]


def test_the_drain_then_wants_strictly_better(db, tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    _wish_episode(db)
    _landed(db, f)
    items = annotate_upgrades(db.episode_wishlist_to_download(due_only=False),
                              resolution_rank("1080p"))
    assert len(items) == 1 and items[0]["_min_rank"] == resolution_rank("720p")
    # the same 720p release is no longer acceptable; a 1080p is
    picked = acceptable_candidates(_cands("WEBDL-720p", "WEBDL-1080p"), items[0]["_min_rank"])
    assert [c["quality_label"] for c in picked] == ["WEBDL-1080p"]


def test_at_cutoff_the_item_is_left_alone(db, tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    _wish_episode(db, season=0, episode=1)          # a special, season 0
    _landed(db, f, season=0, episode=1, label="1080p", release="Show S00E01 1080p")
    items = annotate_upgrades(db.episode_wishlist_to_download(due_only=False),
                              resolution_rank("1080p"))
    assert items == []


def test_a_deleted_file_can_be_grabbed_again(db, tmp_path):
    _wish_episode(db)
    _landed(db, tmp_path / "gone.mkv")
    row = _row(db.episode_wishlist_to_download(due_only=False))
    assert not row["owned"]


def test_another_episode_is_not_covered(db, tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    _wish_episode(db, episode=3)
    _landed(db, f, episode=2)
    assert not _row(db.episode_wishlist_to_download(due_only=False), episode=3)["owned"]


def test_a_library_sourced_download_maps_through_the_show(db, tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    conn = db._get_connection()
    conn.execute("INSERT INTO shows (id, tmdb_id, title) VALUES (77, ?, 'Shark Tank')", (SHARK,))
    conn.commit()
    conn.close()
    _wish_episode(db)
    _landed(db, f, media_id=77, source="library")
    assert _row(db.episode_wishlist_to_download(due_only=False))["owned"] == 1


def test_movies_too(db, tmp_path):
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    db.add_movie_to_wishlist(603, "The Matrix", year=1999)
    _landed(db, f, kind="movie", media_id=603, label="720p", release="The.Matrix.1999.720p")
    rows = db.movie_wishlist_to_download(due_only=False)
    m = next(r for r in rows if r["tmdb_id"] == 603)
    assert m["owned"] == 1 and "720p" in m["owned_resolutions"]


def test_a_failed_download_is_not_a_copy(db, tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    _wish_episode(db)
    db.record_download_history({
        "id": 9, "status": "import_failed", "kind": "show", "title": "x",
        "release_title": "Shark Tank S18E02 720p", "dest_path": str(f), "quality_label": "720p",
        "media_id": str(SHARK), "media_source": "tmdb",
        "search_ctx": json.dumps({"season": 18, "episode": 2})})
    assert not _row(db.episode_wishlist_to_download(due_only=False))["owned"]


# -- a jellyfin library set up as mixed content --

class _JF:
    user_id = "u1"

    def __init__(self):
        self.calls = []

    def _make_request(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path.endswith("/Views"):
            return {"Items": [
                {"Id": "m", "Name": "Movies", "CollectionType": "movies"},
                {"Id": "t", "Name": "Shows", "CollectionType": "tvshows"},
                {"Id": "a", "Name": "Anime"},                               # mixed: no type
                {"Id": "x", "Name": "Mixed", "CollectionType": "mixed"},
                {"Id": "p", "Name": "Playlists", "CollectionType": "playlists"},
                {"Id": "z", "Name": "Music", "CollectionType": "music"},
            ]}
        return {"Items": []}


def test_a_mixed_jellyfin_library_can_be_picked_and_scanned():
    """c5pie's anime library never showed up to pick, so its shows were never
    scanned and every episode looked missing"""
    from core.video.sources import JellyfinVideoSource
    jf = _JF()
    src = JellyfinVideoSource(jf, tv_lib="Anime")
    libs = src.available_libraries()
    assert [v["title"] for v in libs["tv"]] == ["Shows", "Anime", "Mixed"]
    assert [v["title"] for v in libs["movies"]] == ["Movies", "Anime", "Mixed"]
    list(src.iter_shows())
    scanned = [p.get("ParentId") for path, p in jf.calls if p.get("IncludeItemTypes") == "Series"]
    assert scanned == ["a"]
