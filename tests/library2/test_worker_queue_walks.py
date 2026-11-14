"""The workers' own next-item queries stop at their first row.

Nov 13 2026, timed on a 319k-track library:

- the "never attempted" track walk was planned from the albums' artist index
  and then sorted every unattempted track to hand back one;
- a retry of two statuses gathered and sorted every due row, each with its
  owned check, per worker tick;
- the batch lookup (Spotify, iTunes) started from the matched attempts and
  sorted every qualifying parent, 2-5s per worker loop, and a worker with no
  batch left re-read every matched parent twice per loop (7s for tracks).
"""

from __future__ import annotations

import json

import pytest

from core.library2 import worker_queue as wq
from core.library2.provider_attempts import ensure_provider_attempt_schema, record_attempt
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn


@pytest.fixture
def conn(tmp_path):
    c = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(c)
    ensure_provider_attempt_schema(c.cursor())
    yield c
    c.close()


def _artist(conn, name, provider_id=None):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, external_ids) VALUES(?,?,?)",
        (name, name, json.dumps({"spotify": provider_id} if provider_id else {}))).lastrowid


def _album(conn, artist_id, title, provider_id=None):
    return conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,external_ids) "
        "VALUES(?,?,'album',?)",
        (artist_id, title, json.dumps({"spotify": provider_id} if provider_id else {}))).lastrowid


def _track(conn, album_id, title, owned=True):
    track = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)",
                         (album_id, title)).lastrowid
    if owned:
        conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
                     "VALUES(?,?,1,'active')", (track, f"/music/{track}.flac"))
    return track


# what the planner knows on a real 319k-track library (sqlite_stat1, Nov 13
# 2026). an empty test database has no statistics and plans everything well
_BIG_LIBRARY_STATS = (
    ("lib2_albums", "idx_lib2_albums_artist", "72734 19"),
    ("lib2_albums", "idx_lib2_albums_spotify", "72734 3"),
    ("lib2_artists", "idx_lib2_artists_name", "30009 1"),
    ("lib2_artists", "idx_lib2_artists_spotify", "30009 2"),
    ("lib2_provider_attempts", "idx_lib2_provider_attempts_due", "3142135 1001 1001 667 1"),
    ("lib2_provider_attempts", "sqlite_autoindex_lib2_provider_attempts_1", "3142135 1001 11 1"),
    ("lib2_track_files", "idx_lib2_track_files_primary", "311411 1 1"),
    ("lib2_track_files", "idx_lib2_track_files_track", "311411 1"),
    ("lib2_tracks", "idx_lib2_tracks_album", "319511 3"),
    ("lib2_tracks", "idx_lib2_tracks_album_order", "319511 3 3 1 1"),
    ("lib2_tracks", "idx_lib2_tracks_spotify", "319511 3"),
)


def _plan(conn, sql, params):
    conn.execute("ANALYZE")
    conn.execute("DELETE FROM sqlite_stat1")
    known = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    used = [row for row in _BIG_LIBRARY_STATS if row[1] in known]
    assert len(used) >= 8, "the indexes these statistics describe were renamed"
    conn.executemany("INSERT INTO sqlite_stat1(tbl, idx, stat) VALUES(?,?,?)", used)
    conn.commit()
    conn.execute("ANALYZE sqlite_master")   # load the statistics just written
    return [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


@pytest.mark.parametrize("entity", wq.ENTITY_ORDER)
def test_the_never_attempted_walk_is_in_id_order_with_no_sort(conn, entity):
    plan = _plan(conn, wq._pending_sql(entity, wq._RETRYABLE, False, "lastfm", "new") + " LIMIT 1",
                 wq._params(entity, "lastfm", 30))
    assert plan[0] == "SCAN e", plan
    assert not any("TEMP B-TREE" in step for step in plan), plan


@pytest.mark.parametrize("entity", wq.ENTITY_ORDER)
def test_a_one_status_retry_reads_the_due_index_in_age_order(conn, entity):
    plan = _plan(conn, wq._pending_sql(entity, ("not_found",), False, "lastfm", "retry") + " LIMIT 1",
                 wq._params(entity, "lastfm", 30))
    assert "idx_lib2_provider_attempts_due" in plan[0], plan
    # the id tie-break may sort rows of one timestamp; never the whole set
    assert not any(step == "USE TEMP B-TREE FOR ORDER BY" for step in plan), plan


def test_retries_of_both_statuses_come_back_oldest_first(conn):
    artist = _artist(conn, "Rone")
    album = _album(conn, artist, "Tohu Bohu")
    tracks = [_track(conn, album, f"T{n}") for n in range(8)]
    for entity, entity_id in (("artist", artist), ("album", album)):
        record_attempt(conn, entity_type=entity, entity_id=entity_id, service="lastfm",
                       status="matched")
    # ages interleave across the two statuses, with one tie broken by id
    stamps = ["2020-01-05", "2020-01-02", "2020-01-08", "2020-01-02",
              "2020-01-01", "2020-01-07", "2020-01-03", "2020-01-06"]
    for index, (track, day) in enumerate(zip(tracks, stamps, strict=True)):
        record_attempt(conn, entity_type="track", entity_id=track, service="lastfm",
                       status="error" if index % 2 else "not_found",
                       attempted_at=f"{day} 00:00:00")
    conn.commit()
    expected = [track for _day, track in sorted(zip(stamps, tracks, strict=True))]
    served = []
    for _ in tracks:
        item = wq.next_pending(conn, "lastfm")
        served.append(item["id"])
        record_attempt(conn, entity_type="track", entity_id=item["id"], service="lastfm",
                       status="matched")
    assert served == expected
    assert wq.next_pending(conn, "lastfm") is None


def test_a_narrowed_retry_serves_only_that_status(conn):
    artist = _artist(conn, "Rone")
    record_attempt(conn, entity_type="artist", entity_id=artist, service="lastfm",
                   status="not_found", attempted_at="2020-01-01 00:00:00")
    other = _artist(conn, "Other")
    _track(conn, _album(conn, artist, "A"), "T")
    _track(conn, _album(conn, other, "B"), "T")
    record_attempt(conn, entity_type="artist", entity_id=other, service="lastfm",
                   status="error", attempted_at="2020-01-02 00:00:00")
    conn.commit()
    item = wq.next_pending(conn, "lastfm", entity_types=("artist",), retry_statuses=("error",))
    assert item["id"] == other


def _batch_library(conn, parents=6):
    """Matched artists with a matched, fully attempted album each."""
    made = []
    for n in range(parents):
        artist = _artist(conn, f"Artist {n}", provider_id=f"sp-artist-{n}")
        album = _album(conn, artist, f"Album {n}", provider_id=f"sp-album-{n}")
        track = _track(conn, album, f"Track {n}")
        for entity, entity_id in (("artist", artist), ("album", album), ("track", track)):
            record_attempt(conn, entity_type=entity, entity_id=entity_id, service="spotify",
                           status="matched")
        made.append((artist, album, track))
    conn.commit()
    return made


def test_the_batch_walk_is_over_parents_in_id_order(conn):
    for child, parent_table in (("album", "lib2_artists"), ("track", "lib2_albums")):
        seen = []
        conn.set_trace_callback(seen.append)
        wq._batch_parent_from(conn, "spotify", child, 0)
        conn.set_trace_callback(None)
        plan = _plan(conn, seen[-1].replace(":parent_type", "'x'").replace(":child_type", "'y'")
                     .replace(":service", "'spotify'").replace(":after", "0"), ())
        assert plan[0].startswith("SEARCH p USING INTEGER PRIMARY KEY") or plan[0] == "SCAN p", plan
        assert not any("TEMP B-TREE" in step for step in plan), plan


def test_an_empty_batch_answer_is_not_asked_again_until_something_moves(conn, monkeypatch):
    made = _batch_library(conn)
    walks = []
    real = wq._batch_parent_from
    monkeypatch.setattr(wq, "_batch_parent_from",
                        lambda *a: walks.append(a[2:]) or real(*a))
    for _ in range(5):
        assert wq._batch_parent(conn, "spotify", "album") is None
        assert wq._batch_parent(conn, "spotify", "track") is None
    assert len(walks) == 2, walks

    # a new track on a matched album: found on the next ask
    fresh = _track(conn, made[3][1], "Bonus")
    conn.commit()
    row = wq._batch_parent(conn, "spotify", "track")
    assert row["parent_id"] == made[3][1]
    record_attempt(conn, entity_type="track", entity_id=fresh, service="spotify", status="matched")
    conn.commit()
    assert wq._batch_parent(conn, "spotify", "track") is None


def test_a_newly_matched_parent_is_found_at_once(conn):
    made = _batch_library(conn, parents=3)
    artist = _artist(conn, "Late", provider_id="sp-late")
    album = _album(conn, artist, "Late Album")
    _track(conn, album, "Late Track")
    conn.commit()
    assert wq._batch_parent(conn, "spotify", "album") is None   # the artist is not matched yet
    record_attempt(conn, entity_type="artist", entity_id=artist, service="spotify", status="matched")
    conn.commit()
    row = wq._batch_parent(conn, "spotify", "album")
    assert row is not None and row["parent_id"] == artist
    assert made  # the settled parents were never offered


def test_a_cleared_child_attempt_is_found_at_once(conn):
    made = _batch_library(conn, parents=3)
    assert wq._batch_parent(conn, "spotify", "track") is None
    conn.execute("DELETE FROM lib2_provider_attempts WHERE entity_type='track' AND entity_id=? "
                 "AND service='spotify'", (made[1][2],))
    conn.commit()
    assert wq._batch_parent(conn, "spotify", "track")["parent_id"] == made[1][1]


def test_the_batch_walk_resumes_and_wraps(conn):
    made = _batch_library(conn, parents=6)
    late = _track(conn, made[4][1], "Late")
    conn.commit()
    assert wq._batch_parent(conn, "spotify", "track")["parent_id"] == made[4][1]
    # an earlier album gains a track while the walk stands at a later one
    early = _track(conn, made[1][1], "Early")
    conn.commit()
    assert wq._batch_parent(conn, "spotify", "track")["parent_id"] == made[4][1]
    record_attempt(conn, entity_type="track", entity_id=late, service="spotify", status="matched")
    conn.commit()
    # past the end: once more from the start
    assert wq._batch_parent(conn, "spotify", "track")["parent_id"] == made[1][1]
    record_attempt(conn, entity_type="track", entity_id=early, service="spotify", status="matched")
    conn.commit()
    assert wq._batch_parent(conn, "spotify", "track") is None


def test_clearing_the_pending_cache_clears_the_batch_memo(conn, monkeypatch):
    _batch_library(conn, parents=2)
    walks = []
    real = wq._batch_parent_from
    monkeypatch.setattr(wq, "_batch_parent_from", lambda *a: walks.append(1) or real(*a))
    wq._batch_parent(conn, "spotify", "album")
    wq._batch_parent(conn, "spotify", "album")
    assert len(walks) == 1
    wq.invalidate_pending_cache()
    wq._batch_parent(conn, "spotify", "album")
    assert len(walks) == 2
