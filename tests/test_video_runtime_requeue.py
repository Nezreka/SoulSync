"""once: episodes the old runtime rule failed get another import.

hermetic: a temp video database, no monitor thread.
"""

from __future__ import annotations

import json

from core.video.download_monitor import requeue_runtime_rejects
from database.video_database import VideoDatabase

RUNTIME = "Runs 42 of 85 min — truncated download or the wrong file"


def _row(db, *, kind="show", status="import_failed", error=RUNTIME, season=12, episode=4,
         media_id="74676"):
    dl_id = db.add_video_download({
        "kind": kind, "title": "Halloween Baking Championship", "release_title": "r",
        "source": "torrent", "client_ref": "abc", "status": status, "error": error,
        "media_id": media_id, "media_source": "tmdb", "target_dir": "/tv",
        "search_ctx": json.dumps({"scope": "episode", "season": season, "episode": episode}),
        "candidates": "[]", "tried_queries": "[]", "tried_files": "[]", "attempts": 0})
    db.update_video_download(dl_id, status=status, error=error)   # the insert keeps no error
    return dl_id


def _status(db, dl_id):
    return db.get_video_download(dl_id)["status"]


def test_the_newest_row_per_episode_goes_back_to_downloading_once(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    older = _row(db)
    newer = _row(db)
    other_episode = _row(db, episode=5)
    not_runtime = _row(db, error="Release is S12E05, not the episode requested")
    movie = _row(db, kind="movie", error=RUNTIME)
    assert requeue_runtime_rejects(db) == 2
    assert _status(db, newer) == "downloading" and db.get_video_download(newer)["error"] is None
    assert _status(db, other_episode) == "downloading"
    # the older duplicate, other failures and movies stay as they were
    assert _status(db, older) == "import_failed"
    assert _status(db, not_runtime) == "import_failed"
    assert _status(db, movie) == "import_failed"
    # once: a second start does nothing
    _row(db, episode=9)
    assert requeue_runtime_rejects(db) == 0
