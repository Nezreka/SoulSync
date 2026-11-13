"""Every worker's pending count is read off the one shared progress pass.

Nov 13 2026, measured on a 319k-track library with a tab open: the status loop
asked each running enrichment worker for its pending count, each count was six
catalogue passes of its own (25s a service), and the 30s cache expired before
the loop got round all sixteen. One thread never stopped counting: 86% of a
core and several hundred MB/s of reads, for as long as any browser tab was
open. The progress pass already walks the owned library once for every
service, so it now carries the per-status and past-the-retry-window numbers
too and the pending counts are read from it.

These tests hold the new numbers to the plain per-service queries, row for
row, on a library with every kind of row the queue leaves out."""

from __future__ import annotations

import pytest

from core.library2 import worker_queue as wq
from core.library2.provider_attempts import ensure_provider_attempt_schema, record_attempt
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn

SERVICES = ("lastfm", "deezer", "spotify", "qobuz", "genius")
OLD = "2020-01-01 00:00:00"


def _library(tmp_path, *, owned_albums: int, unowned_albums: int):
    c = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(c)
    ensure_provider_attempt_schema(c.cursor())
    artists = {}
    for name in ("Massive Attack", "Portishead", "Eminem, Nate Dogg", "Tricky"):
        artists[name] = c.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES(?,?)", (name, name)).lastrowid
    names = list(artists)
    tracks = []
    for n in range(owned_albums + unowned_albums):
        owner = artists[names[n % len(names)]]
        album = c.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) VALUES(?,?,'album')",
            (owner, f"Album {n}")).lastrowid
        for t in range(3):
            track = c.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)",
                              (album, f"Track {n}-{t}")).lastrowid
            tracks.append(track)
            if n < owned_albums and t < 2:  # the third track of an owned album has no file
                c.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
                          "VALUES(?,?,1,'active')", (track, f"/music/{n}-{t}.flac"))
    # a reviewed and locked album is not enrichment's to look at
    c.execute("INSERT INTO lib2_entity_locks(entity_type, entity_id, scopes) "
              "VALUES('album', 1, 'metadata')")
    c.commit()
    # attempts of every status and age, on owned and unowned rows alike, plus
    # rows whose entity is gone
    statuses = ("matched", "not_found", "error", "skipped")
    n = 0
    for entity, ids in (("artist", list(artists.values())),
                        ("album", range(1, owned_albums + unowned_albums + 1)),
                        ("track", tracks)):
        for entity_id in ids:
            for index, service in enumerate(SERVICES[:4]):
                n += 1
                if n % 3 == 0:
                    continue  # never asked
                record_attempt(c, entity_type=entity, entity_id=entity_id, service=service,
                               status=statuses[(n + index) % 4],
                               attempted_at=OLD if n % 2 else None)
        for service in SERVICES[:2]:
            record_attempt(c, entity_type=entity, entity_id=99990, service=service,
                           status="not_found", attempted_at=OLD)
    c.commit()
    return c


@pytest.fixture(params=[(6, 2), (2, 9)], ids=["mostly-owned", "mostly-discography"])
def conn(request, tmp_path, monkeypatch):
    c = _library(tmp_path, owned_albums=request.param[0], unowned_albums=request.param[1])
    monkeypatch.setattr(wq, "PROGRESS_SNAPSHOT_TTL", 120.0)
    monkeypatch.setattr(wq, "PENDING_COUNT_TTL", 30.0)
    monkeypatch.setattr(wq, "_snapshot_windows", {30, 7})
    wq._reset_progress_snapshots()
    yield c
    wq._reset_progress_snapshots()
    c.close()


def _exact(c, service, days, entity_types, statuses):
    return wq._pending_count(c, service, retry_after_days=days, entity_types=entity_types,
                             retry_statuses=statuses, require_provider_id=False)


@pytest.mark.parametrize("days", [30, 7])
@pytest.mark.parametrize("statuses", [("not_found", "error"), ("error",), ("not_found",)])
@pytest.mark.parametrize("entity_types", [wq.ENTITY_ORDER, ("artist", "track"), ("album",)])
def test_the_shared_count_is_the_plain_count(conn, days, statuses, entity_types):
    for service in SERVICES:
        exact = _exact(conn, service, days, entity_types, statuses)
        assert wq.pending_count(conn, service, retry_after_days=days,
                                entity_types=entity_types,
                                retry_statuses=statuses) == exact, service
    # the library has work of each kind, or the comparison proves nothing
    assert _exact(conn, "lastfm", days, wq.ENTITY_ORDER, ("not_found", "error")) > 0
    assert _exact(conn, "genius", days, wq.ENTITY_ORDER, ("not_found", "error")) > 0


def test_every_service_is_counted_in_one_pass(conn, monkeypatch):
    plain = []
    real = wq._pending_count
    monkeypatch.setattr(wq, "_pending_count",
                        lambda *a, **kw: plain.append(a[1]) or real(*a, **kw))
    passes = []
    fast = wq._compute_progress
    monkeypatch.setattr(wq, "_compute_progress", lambda c: passes.append(1) or fast(c))
    for _ in range(3):
        for service in SERVICES:
            wq.pending_count(conn, service)
            wq.progress_breakdown(conn, service)
    assert passes == [1]
    assert plain == []


def test_the_fast_pass_and_the_plain_pass_agree(conn):
    fast = wq._compute_progress(conn)
    plain = wq._compute_progress_plain(conn)
    assert "attempts" in fast["track"], "the fast pass did not run"
    for entity in wq.ENTITY_ORDER:
        assert fast[entity]["total"] == plain[entity]["total"]
        assert fast[entity]["processed"] == plain[entity]["processed"]
    assert plain["track"]["total"] > 0 and plain["album"]["total"] > 0
    # nothing is left behind on the connection
    assert conn.execute("SELECT COUNT(*) FROM sqlite_temp_master "
                        "WHERE name LIKE 'lib2_q_%'").fetchone()[0] == 0


def test_a_connection_with_open_work_is_counted_without_committing_it(conn):
    conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES('Open Work','Open Work')")
    assert conn.in_transaction
    data = wq._compute_progress(conn)
    assert "attempts" not in data["artist"]
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM lib2_artists WHERE name='Open Work'").fetchone()[0] == 0


def test_a_window_the_pass_has_not_counted_is_counted_plainly_then_joins(conn):
    exact = _exact(conn, "lastfm", 3, wq.ENTITY_ORDER, ("not_found", "error"))
    assert wq.pending_count(conn, "lastfm", retry_after_days=3) == exact
    assert 3 in wq._snapshot_windows
    wq._reset_progress_snapshots()
    seen = []
    real = wq._pending_count
    wq_pending = lambda *a, **kw: seen.append(1) or real(*a, **kw)  # noqa: E731
    try:
        wq._pending_count = wq_pending
        assert wq.pending_count(conn, "lastfm", retry_after_days=3) == exact
    finally:
        wq._pending_count = real
    assert seen == []


def test_an_unknown_retry_status_still_raises(conn):
    with pytest.raises(ValueError):
        wq.pending_count(conn, "lastfm", retry_statuses=("bogus",))


def test_a_slow_pass_is_recounted_less_often(monkeypatch):
    monkeypatch.setattr(wq, "PROGRESS_SNAPSHOT_TTL", 120.0)
    assert wq._snapshot_ttl({"cost": 0.2}) == wq.PROGRESS_SNAPSHOT_TTL
    assert wq._snapshot_ttl({}) == wq.PROGRESS_SNAPSHOT_TTL
    assert wq._snapshot_ttl({"cost": 30.0}) == 300.0
    assert wq._snapshot_ttl({"cost": 500.0}) == wq._SNAPSHOT_TTL_MAX
