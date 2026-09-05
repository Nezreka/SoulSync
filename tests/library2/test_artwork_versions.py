"""Cache-bust versions come from a per-entity mtime cache (perf25-01/rev25-03).

The list endpoint needs a ``?v=<mtime>`` token per row. Building it used to
cost one ``Path.stat()`` per artist per list request (perf25-01), then a
whole-directory scan reused across a page (its fix) — but on a large library
that directory can hold tens of thousands of entries, and every successful
build forgets the shared snapshot, so the "reused across renders" premise
rarely holds under real traffic (rev25-03). Each entity is now cached
individually and invalidated individually.
"""

from __future__ import annotations

from pathlib import Path

from core.library2 import artwork


def _shim(tmp_path):
    class _DB:
        database_path = str(tmp_path / "music.db")

    return _DB()


def test_versions_are_cached_per_entity_after_the_first_stat(tmp_path, monkeypatch):
    database = _shim(tmp_path)
    directory = artwork.artwork_dir(database)
    for entity_id in range(1, 76):
        (directory / f"artist_{entity_id}.jpg").write_bytes(b"\xff\xd8\xff")
    for entity_id in range(1, 76):
        artwork.forget_artwork_versions(database, "artist", entity_id)

    stats = []
    real_stat = Path.stat

    def counting_stat(self, *args, **kwargs):
        stats.append(str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counting_stat)

    first = [artwork.artwork_version(database, "artist", i) for i in range(1, 76)]
    second = [artwork.artwork_version(database, "artist", i) for i in range(1, 76)]

    assert all(value > 0 for value in first)
    assert first == second
    # One real stat per entity — the second pass must be a pure cache hit,
    # not a re-scan of the (potentially huge) artwork directory.
    assert len(stats) == 75


def test_missing_artwork_has_no_version(tmp_path):
    database = _shim(tmp_path)
    artwork.artwork_dir(database)
    artwork.forget_artwork_versions(database, "artist", 4242)

    assert artwork.artwork_version(database, "artist", 4242) == 0


def test_new_artwork_file_is_picked_up(tmp_path):
    database = _shim(tmp_path)
    artwork.artwork_dir(database)
    artwork.forget_artwork_versions(database, "album", 7)
    assert artwork.artwork_version(database, "album", 7) == 0

    artwork.artwork_file(database, "album", 7).write_bytes(b"\xff\xd8\xff")
    artwork.forget_artwork_versions(database, "album", 7)

    assert artwork.artwork_version(database, "album", 7) > 0


def test_invalidation_drops_the_cached_version(tmp_path):
    database = _shim(tmp_path)
    artwork.artwork_file(database, "artist", 9).write_bytes(b"\xff\xd8\xff")
    artwork.thumb_file(database, "artist", 9).write_bytes(b"\xff\xd8\xff")
    assert artwork.artwork_version(database, "artist", 9) > 0

    artwork.invalidate_artwork(database, "artist", 9)

    assert artwork.artwork_version(database, "artist", 9) == 0


def test_forgetting_one_entity_does_not_drop_another(tmp_path):
    """rev25-09/rev25-03: a targeted invalidation (the normal case — every
    managed write knows exactly what it touched) must not force every other
    already-cached entity on the page to re-stat too."""
    database = _shim(tmp_path)
    artwork.artwork_file(database, "artist", 1).write_bytes(b"\xff\xd8\xff")
    artwork.artwork_file(database, "artist", 2).write_bytes(b"\xff\xd8\xff")
    artwork.forget_artwork_versions(database, "artist", 1)
    artwork.forget_artwork_versions(database, "artist", 2)
    assert artwork.artwork_version(database, "artist", 1) > 0
    assert artwork.artwork_version(database, "artist", 2) > 0

    artwork.invalidate_artwork(database, "artist", 1)

    # Entity 2's cached mtime survives entity 1's invalidation.
    real_stat = Path.stat
    calls = []

    def counting_stat(self, *args, **kwargs):
        calls.append(str(self))
        return real_stat(self, *args, **kwargs)

    import unittest.mock as mock

    with mock.patch.object(Path, "stat", counting_stat):
        assert artwork.artwork_version(database, "artist", 2) > 0

    assert not calls, "entity 2 should have been a cache hit"


def test_a_write_racing_a_concurrent_lookup_is_never_cached_stale(tmp_path, monkeypatch):
    """rev25-09: the old whole-directory snapshot could validate against a
    directory mtime stamped *before* a concurrent writer's change landed,
    serving a stale cache-bust token under a 7-day immutable Cache-Control
    header. The generation counter must make that impossible: a lookup that's
    mid-stat when a write invalidates its target must not commit its
    (possibly stale) result."""
    database = _shim(tmp_path)
    artwork.artwork_file(database, "artist", 5).write_bytes(b"\xff\xd8\xff")
    artwork.forget_artwork_versions(database, "artist", 5)

    real_stat = Path.stat
    raced = []

    def racing_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if not raced:
            raced.append(True)
            # A concurrent write completes and invalidates the entity's
            # cache entry while this lookup is still between its stat() call
            # and committing that value to the cache.
            artwork.artwork_file(database, "artist", 5).write_bytes(b"\xff\xd8\xff\xff")
            artwork.forget_artwork_versions(database, "artist", 5)
        return result

    monkeypatch.setattr(Path, "stat", racing_stat)
    first = artwork.artwork_version(database, "artist", 5)
    monkeypatch.undo()

    # The racing lookup must not have cached its (pre-write) value: a fresh
    # lookup has to re-stat and see the write that happened during the race.
    second_stats = []
    real_stat_2 = Path.stat

    def counting_stat(self, *args, **kwargs):
        second_stats.append(str(self))
        return real_stat_2(self, *args, **kwargs)

    import unittest.mock as mock

    with mock.patch.object(Path, "stat", counting_stat):
        second = artwork.artwork_version(database, "artist", 5)

    assert second_stats, "the racing lookup must not have poisoned the cache"
    assert second >= first
