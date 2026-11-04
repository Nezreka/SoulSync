"""The post-upgrade preparation runs behind a library that already works.

It used to run inside the upgrade barrier: all 18 workers stopped and every
library change answered 503 until the credit pass over ~29k imported credits
and a tracklist for every album were done. On a 300k-track library that is
hours, and the credit pass repeated on every restart. Releasing early is only
safe because the worker queues keep an unresolved combined credit out on their
own; the second half of this file pins that.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.library2 import post_import
from core.library2.schema import ensure_library_v2_schema


def test_preparation_runs_in_the_background_not_in_the_barrier(monkeypatch):
    from core.library2.migration_gate import local_migration_active
    seen, release = {}, threading.Event()

    def credits(database, config_manager=None, **kw):
        seen["barrier_held"] = local_migration_active()
        release.wait(5)

    monkeypatch.setattr(post_import, "prepare_imported_artists", credits)
    thread = post_import.start_background_preparation(object(), None, full=False)
    assert thread is not None and thread.is_alive()
    # a second start while one runs is a no-op
    assert post_import.start_background_preparation(object(), None, full=False) is None
    release.set()
    thread.join(5)
    assert seen == {"barrier_held": False}


def test_a_failed_pass_is_logged_and_still_runs_the_follow_up(monkeypatch):
    after = []
    monkeypatch.setattr(post_import, "run_post_import_precache",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider down")))
    thread = post_import.start_background_preparation(object(), None, full=True,
                                                      after=lambda: after.append(True))
    thread.join(5)
    assert after == [True]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    return c


def _owned_artist(conn, name, **ids):
    cols = ", ".join(["name", "name_key", *ids])
    marks = ", ".join("?" * (2 + len(ids)))
    artist = conn.execute(f"INSERT INTO lib2_artists({cols}) VALUES({marks})",
                          (name, name.lower(), *ids.values())).lastrowid
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
                         "VALUES(?,'A','album')", (artist,)).lastrowid
    track = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'T')", (album,)).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
                 "VALUES(?,?,1,'active')", (track, f"/m/{track}.flac"))
    conn.commit()
    return artist


def _queued(conn, service="lastfm"):
    from core.library2.worker_queue import invalidate_pending_cache, next_pending
    seen = set()
    for _ in range(10):
        invalidate_pending_cache()
        row = next_pending(conn, service, entity_types=("artist",))
        if not row or row["id"] in seen:
            break
        seen.add(row["id"])
        conn.execute("INSERT OR REPLACE INTO lib2_provider_attempts(entity_type, entity_id, service,"
                     " status, last_attempted_at) VALUES('artist', ?, ?, 'matched', CURRENT_TIMESTAMP)",
                     (row["id"], service))
    return seen


def test_an_unresolved_combined_credit_never_reaches_a_worker(conn):
    solo = _owned_artist(conn, "Eminem")
    combined = _owned_artist(conn, "Eminem, Nate Dogg")
    band = _owned_artist(conn, "Earth, Wind & Fire", spotify_id="sp-ewf")
    queued = _queued(conn)
    assert solo in queued and band in queued
    assert combined not in queued
