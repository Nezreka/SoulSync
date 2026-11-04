"""The back catalogue on the discography refresh (artists='library').

3.6.1's Discography Backfill walked every artist you have a release of and
asked the fuzzy matcher, scoped to the active media server, what you own. On
Library v2 the refresh does that walk itself, and a release is missing when
the catalogue has no file of it, whichever server scanned it or none did.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.library2.schema import ensure_library_v2_schema
from core.repair_jobs.base import JobContext
from core.repair_jobs.lib2_discography_refresh import Lib2DiscographyRefreshJob
from tests import lib2_seed

KEY = "repair.jobs.monitored_discography_refresh.scan_checkpoint"


def _db(tmp_path):
    db = lib2_seed.RowDb(str(tmp_path / "lib2.db"))
    with db._get_connection() as conn:
        ensure_library_v2_schema(conn)
        conn.commit()
    return db


def _id(conn, name):
    return conn.execute("SELECT id FROM lib2_artists WHERE name=?", (name,)).fetchone()[0]


def test_library_reach_takes_artists_with_a_release_of_their_own(tmp_path):
    """#977: a guest credit or a wishlisted release is not "in the library"."""
    db = _db(tmp_path)
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Owned Artist", "Alb", "T")
        lib2_seed.track(conn, "Wishlist Only Artist", "Alb2", "T2", owned=False)
        lib2_seed.track(conn, "Owned Artist", "Alb", "T3", credit="Feat Guest")
        conn.commit()
        ids = Lib2DiscographyRefreshJob._library_artist_ids(conn)
        assert ids == [_id(conn, "Owned Artist")]
        # the watchlist reach is unchanged: nobody here is watched
        assert Lib2DiscographyRefreshJob()._sweep_ids(conn, {"artists": "watchlist"}) == []


def test_an_artist_whose_files_sit_on_its_alias_row_is_swept_once(tmp_path):
    """§40 rows keep their own albums. the 3.6.1 port only asked the canonical
    row, so an artist whose files are filed under the other name was skipped."""
    db = _db(tmp_path)
    with db._get_connection() as conn:
        lib2_seed.track(conn, "澤野弘之", "Attack on Titan OST", "Eye-Water")
        canonical = lib2_seed.artist(conn, "Hiroyuki Sawano")
        conn.execute("UPDATE lib2_artists SET canonical_artist_id=? WHERE name='澤野弘之'",
                     (canonical,))
        conn.commit()
        assert Lib2DiscographyRefreshJob._library_artist_ids(conn) == [canonical]


def test_min_album_tracks_needs_that_many_on_one_album_or_the_watchlist(tmp_path):
    """#1572: a few playlist songs don't pull in a whole discography."""
    db = _db(tmp_path)
    with db._get_connection() as conn:
        for n in range(3):
            lib2_seed.track(conn, "Collected", "Full Album", f"Song {n}")
        lib2_seed.track(conn, "Playlist Grab", "Their Album", "One Song")
        lib2_seed.track(conn, "Watched", "Their Album", "One Song")
        conn.execute("UPDATE lib2_artists SET monitored=1 WHERE name='Watched'")
        conn.commit()
        ids = Lib2DiscographyRefreshJob()._sweep_ids(
            conn, {"artists": "library", "min_album_tracks_owned": 3})
        assert ids == sorted([_id(conn, "Collected"), _id(conn, "Watched")])


def _findings(db, artist, settings=None):
    created = []
    job = Lib2DiscographyRefreshJob()
    context = SimpleNamespace(db=db, create_finding=lambda **kw: created.append(kw) or True)
    result = SimpleNamespace(findings_created=0)
    with db._get_connection() as conn:
        artist_id = _id(conn, artist)
    job._create_backfill_findings(context, artist_id, {**job._settings(SimpleNamespace(
        config_manager=None)), **(settings or {})}, result)
    return created


def test_releases_with_nothing_on_disk_become_findings_for_the_owner(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Kid Artist", "Owned Album", "Hit Song")
        conn.execute("UPDATE lib2_track_files SET owner_profile_id=3")
        for title, cols in (
                ("Second Album", {"album_type": "album", "track_count": 10}),
                # its song is on disk under the album already
                ("Hit Song", {"album_type": "single", "track_count": 1}),
                ("Live at Wembley", {"album_type": "album", "track_count": 12}),
                ("Greatest Hits", {"album_type": "compilation", "track_count": 18}),
                ("Already Wanted", {"album_type": "album", "track_count": 9, "monitored": 1}),
                ("Said No To", {"album_type": "album", "track_count": 9})):
            # a discography row is written unmonitored unless a new-release rule
            # takes it (the schema default is the imported library's 1)
            lib2_seed.album(conn, "Kid Artist", title, origin="discography",
                            **{"monitored": 0, **cols})
        said_no = conn.execute("SELECT id FROM lib2_albums WHERE title='Said No To'").fetchone()[0]
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id, "
                     "monitored, provenance) VALUES('album', ?, 3, 0, 'user_explicit')", (said_no,))
        conn.commit()

    created = _findings(db, "Kid Artist")

    assert [f["title"] for f in created] == ["Missing release: Second Album — Kid Artist"]
    assert created[0]["finding_type"] == "missing_discography_release"
    assert created[0]["details"]["owner_profile_id"] == 3


def test_release_type_filters_apply_to_an_artist_on_no_watchlist(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Band", "Owned Album", "Song")
        lib2_seed.album(conn, "Band", "Some EP", origin="discography",
                        album_type="single", track_count=5, monitored=0)
        lib2_seed.album(conn, "Band", "Some Single", origin="discography",
                        album_type="single", track_count=2, monitored=0)
        conn.commit()

    assert {f["title"] for f in _findings(db, "Band", {"include_singles": False})} == {
        "Missing release: Some EP — Band"}
    assert _findings(db, "Band", {"include_eps": False, "include_singles": False}) == []


class _Config:
    def __init__(self, settings):
        self.store = {"repair.jobs.monitored_discography_refresh.settings": settings}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value


def test_a_per_run_limit_spreads_the_artists_over_runs(monkeypatch):
    seen = []

    def refresh(_db, artist_id, *_a, **_kw):
        seen.append(artist_id)
        return {"auto_monitor_album_ids": []}, 0

    monkeypatch.setattr("core.library2.discography.refresh_artist_discography", refresh)
    job = Lib2DiscographyRefreshJob()
    monkeypatch.setattr(job, "_sweep_ids", lambda _conn, _settings: [1, 2, 3, 4, 5])

    class _Conn:
        def close(self):
            pass

    config = _Config({"max_artists_per_run": 2})
    context = JobContext(db=None, transfer_folder="/music", config_manager=config)
    context.db = type("DB", (), {"_get_connection": lambda self: _Conn()})()

    runs = []
    for _ in range(4):
        seen.clear()
        job.scan(context)
        runs.append(list(seen))

    assert runs == [[1, 2], [3, 4], [5], [1, 2]]
    assert config.store[KEY] == {"id": 2, "scan": ""}


def test_approving_a_back_catalogue_finding_wishes_for_its_library(tmp_path, monkeypatch):
    from core.repair_worker import RepairWorker

    db = _db(tmp_path)
    with db._get_connection() as conn:
        album_id = lib2_seed.album(conn, "Kid Artist", "Second Album", origin="discography")
        conn.commit()
    calls = []
    monkeypatch.setattr("core.library2.discography.auto_monitor_releases",
                        lambda _db, _cfg, ids, *, wishlist_profile_id=1:
                        calls.append((ids, wishlist_profile_id)) or 0)
    worker = RepairWorker(database=db)

    worker._fix_discography_release("album", f"lib2:{album_id}", None,
                                    {"lib2_album_id": album_id, "owner_profile_id": 3})
    worker._fix_discography_release("album", f"lib2:{album_id}", None,
                                    {"lib2_album_id": album_id})

    assert calls == [([album_id], 3), ([album_id], 1)]


def test_the_library_walk_paces_its_provider_calls(monkeypatch):
    """3.6.1 slept a second between artists; a library-wide walk without it is
    hundreds of discography fetches back to back (fe5e0e585)"""
    monkeypatch.setattr("core.library2.discography.refresh_artist_discography",
                        lambda *_a, **_kw: ({"auto_monitor_album_ids": []}, 0))
    job = Lib2DiscographyRefreshJob()
    monkeypatch.setattr(job, "_sweep_ids", lambda _conn, _settings: [1, 2, 3])
    monkeypatch.setattr(job, "_create_backfill_findings", lambda *a, **k: None)

    class _Conn:
        def close(self):
            pass

    naps = []
    config = _Config({"artists": "library"})
    context = JobContext(db=None, transfer_folder="/music", config_manager=config)
    context.db = type("DB", (), {"_get_connection": lambda self: _Conn()})()
    context.sleep_or_stop = lambda seconds: naps.append(seconds) or False

    job.scan(context)

    assert naps == [1.0, 1.0]  # between artists, not after the last


def _auto_add_case(tmp_path, monkeypatch, *, auto_add, inserted=True):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Kid Artist", "Owned Album", "Hit Song")
        conn.execute("UPDATE lib2_track_files SET owner_profile_id=3")
        album_id = lib2_seed.album(conn, "Kid Artist", "Second Album", origin="discography",
                                   album_type="album", track_count=10, monitored=0)
        conn.commit()
    calls = []
    monkeypatch.setattr("core.library2.discography.auto_monitor_releases",
                        lambda _db, _cfg, ids, *, wishlist_profile_id=1:
                        calls.append((ids, wishlist_profile_id)) or 4)
    created = []
    context = SimpleNamespace(db=db, config_manager=None,
                              create_finding=lambda **kw: created.append(kw) or inserted)
    result = SimpleNamespace(findings_created=0, auto_fixed=0)
    job = Lib2DiscographyRefreshJob()
    with db._get_connection() as conn:
        artist_id = _id(conn, "Kid Artist")
    job._create_backfill_findings(
        context, artist_id,
        {**job._settings(SimpleNamespace(config_manager=None)), "auto_add_to_wishlist": auto_add},
        result)
    with db._get_connection() as conn:
        monitored = conn.execute("SELECT monitored FROM lib2_albums WHERE id=?",
                                 (album_id,)).fetchone()[0]
    return album_id, calls, created, result, monitored


def test_auto_add_wishes_for_a_new_back_catalogue_release_at_once(tmp_path, monkeypatch):
    """3.6.1's backfill had auto add to wishlist: the finding is made as the log
    and the release goes on the owner's wishlist without a click."""
    album_id, calls, created, result, monitored = _auto_add_case(
        tmp_path, monkeypatch, auto_add=True)

    assert calls == [([album_id], 3)]
    assert monitored == 1
    assert len(created) == 1 and created[0]["details"]["auto_added"] is True
    assert result.findings_created == 1 and result.auto_fixed == 1


def test_without_auto_add_a_back_catalogue_release_waits_for_fix(tmp_path, monkeypatch):
    album_id, calls, created, result, monitored = _auto_add_case(
        tmp_path, monkeypatch, auto_add=False)

    assert calls == [] and monitored == 0
    assert len(created) == 1 and result.auto_fixed == 0


def test_auto_add_skips_a_release_already_found_before(tmp_path, monkeypatch):
    """only a new finding: a dismissed one or one added on an earlier run is
    not wished for again on every run"""
    _album_id, calls, _created, result, monitored = _auto_add_case(
        tmp_path, monkeypatch, auto_add=True, inserted=False)

    assert calls == [] and monitored == 0 and result.auto_fixed == 0


def test_a_saved_3_6_1_auto_add_carries_to_the_refresh(tmp_path):
    from core.repair_worker import RepairWorker

    config = _Config({})
    config.store = {"repair.jobs.discography_backfill": {
        "enabled": True, "settings": {"auto_add_to_wishlist": True}}}
    RepairWorker(database=_db(tmp_path))._migrate_legacy_job_configs(config)

    settings = config.store["repair.jobs.monitored_discography_refresh"]["settings"]
    assert settings["auto_add_to_wishlist"] is True
    assert settings["artists"] == "library"
    assert Lib2DiscographyRefreshJob.default_settings["auto_add_to_wishlist"] is False
