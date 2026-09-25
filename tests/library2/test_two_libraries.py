"""Two libraries, for real (#1199, docs/library-v2-dir-ownership.md §6).

Until these existed no test created a second directory, so the suite could not
see the state the feature is about. Every test here runs against a real
MusicDatabase with a shared folder and an own folder for "Kim", and asks the
production code -- not a fake of it -- whose file is whose, what each library
lists, where a download lands and what a job touches.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from core import library_scope
from core.library2 import library_roots
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A database with the shared folder and Kim's own folder configured."""
    shared = tmp_path / "Transfer"
    kim_root = tmp_path / "libraries" / "kim"
    shared.mkdir()
    kim_root.mkdir(parents=True)
    db = MusicDatabase(str(tmp_path / "music.db"))
    kim = db.create_profile("Kim")
    sam = db.create_profile("Sam")
    assert db.set_profile_library(kim, "own", str(kim_root))

    import database.music_database as md
    import core.imports.paths as paths
    monkeypatch.setattr(md, "get_database", lambda: db)
    monkeypatch.setattr(library_scope, "own_library_supported", lambda: True)
    cfg = SimpleNamespace(get=lambda key, default=None: str(shared)
                          if key == "soulseek.transfer_path" else default,
                          get_active_media_server=lambda: "plex")
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cfg)
    library_scope.invalidate_library_scope_cache()
    library_roots.sync_library_roots(db)
    yield SimpleNamespace(db=db, kim=kim, sam=sam, shared=str(shared), kim_root=str(kim_root))
    library_scope.invalidate_library_scope_cache()


def _album_with_file(db, *, artist, album, title, path, key):
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id=f"ar-{artist}", name=artist, server_source="soulsync")
        album_id = seed_album(conn, server_id=f"al-{key}", title=album,
                              artist_id=artist_id, server_source="soulsync")
        track_id = seed_track(conn, server_id=f"t-{key}", title=title, album_id=album_id,
                              artist_id=artist_id, server_source="soulsync", file_path=path)
        conn.commit()
    return artist_id, album_id, track_id


def _owner_of(db, path):
    with db._get_connection() as conn:
        return conn.execute("SELECT owner_profile_id FROM lib2_track_files WHERE path=?",
                            (path,)).fetchone()[0]


# ── owning by path (E-15) ────────────────────────────────────────────────────

class TestOwningByPath:
    def test_a_file_in_kims_folder_is_kims(self, lib):
        path = os.path.join(lib.kim_root, "A", "B", "01.flac")
        _album_with_file(lib.db, artist="A", album="B", title="One", path=path, key="k1")
        assert _owner_of(lib.db, path) == lib.kim

    def test_a_file_in_the_shared_folder_is_nobodys(self, lib):
        path = os.path.join(lib.shared, "A", "B", "01.flac")
        _album_with_file(lib.db, artist="A", album="B", title="One", path=path, key="s1")
        with lib.db._get_connection() as conn:
            conn.execute("UPDATE lib2_track_files SET owner_profile_id=? WHERE path=?",
                         (lib.kim, path))
            # a stamp that contradicts the folder does not survive a move
            conn.execute("UPDATE lib2_track_files SET path=? WHERE path=?",
                         (path.replace("01.flac", "01 - One.flac"), path))
            conn.commit()
        assert _owner_of(lib.db, path.replace("01.flac", "01 - One.flac")) is None

    def test_an_own_folder_inside_the_shared_one_wins_its_files(self, lib, tmp_path):
        nested = os.path.join(lib.shared, "kim")
        os.makedirs(nested)
        assert lib.db.set_profile_library(lib.kim, "own", nested)
        library_scope.invalidate_library_scope_cache()
        library_roots.sync_library_roots(lib.db)
        path = os.path.join(nested, "A", "B", "01.flac")
        _album_with_file(lib.db, artist="A", album="B", title="One", path=path, key="n1")
        assert _owner_of(lib.db, path) == lib.kim

    def test_a_sibling_folder_with_the_same_prefix_is_not_kims(self, lib):
        path = lib.kim_root + "berly/A/B/01.flac"
        _album_with_file(lib.db, artist="A", album="B", title="One", path=path, key="kb")
        assert _owner_of(lib.db, path) is None

    def test_a_move_between_libraries_changes_hands(self, lib):
        shared_path = os.path.join(lib.shared, "A", "B", "01.flac")
        _album_with_file(lib.db, artist="A", album="B", title="One", path=shared_path, key="m1")
        kim_path = os.path.join(lib.kim_root, "A", "B", "01.flac")
        with lib.db._get_connection() as conn:
            conn.execute("UPDATE lib2_track_files SET path=? WHERE path=?", (kim_path, shared_path))
            conn.commit()
        assert _owner_of(lib.db, kim_path) == lib.kim

    def test_a_path_under_no_folder_keeps_its_writers_stamp(self, lib):
        path = "/media-server/view/A/B/01.flac"
        _, _, track_id = _album_with_file(lib.db, artist="A", album="B", title="One",
                                          path=path, key="x1")
        with lib.db._get_connection() as conn:
            conn.execute("UPDATE lib2_track_files SET owner_profile_id=? WHERE track_id=?",
                         (lib.kim, track_id))
            conn.commit()
        assert _owner_of(lib.db, path) == lib.kim

    def test_switching_kim_back_to_shared_hands_her_files_to_the_house(self, lib):
        path = os.path.join(lib.kim_root, "A", "B", "01.flac")
        _album_with_file(lib.db, artist="A", album="B", title="One", path=path, key="r1")
        assert lib.db.set_profile_library(lib.kim, "shared", None)
        library_scope.invalidate_library_scope_cache()
        library_roots.sync_library_roots(lib.db)
        assert _owner_of(lib.db, path) is None

    def test_an_unchanged_configuration_rewrites_nothing(self, lib):
        assert library_roots.sync_library_roots(lib.db) == 0


# ── what each library lists (E-03) ───────────────────────────────────────────

class TestWhatEachLibraryLists:
    @pytest.fixture
    def two(self, lib):
        _album_with_file(lib.db, artist="Shared Band", album="House", title="S",
                         path=os.path.join(lib.shared, "Shared Band", "House", "01.flac"), key="h")
        _album_with_file(lib.db, artist="Kims Band", album="Mine", title="K",
                         path=os.path.join(lib.kim_root, "Kims Band", "Mine", "01.flac"), key="k")
        return lib

    def _names(self, db, scope):
        from core.library2.queries import list_artists
        with library_scope.library_scope(scope):
            with db._get_connection() as conn:
                artists, _ = list_artists(conn, limit=50, include_size=False)
        return {a["name"] for a in artists}

    def test_kim_sees_her_library_only(self, two):
        assert self._names(two.db, two.kim) == {"Kims Band"}

    def test_the_shared_library_does_not_list_kims(self, two):
        assert self._names(two.db, "shared") == {"Shared Band"}

    def test_all_libraries_lists_both(self, two):
        assert self._names(two.db, None) == {"Shared Band", "Kims Band"}

    def test_an_artist_kim_wants_but_does_not_have_is_in_her_library(self, two):
        from core.library2.monitor_rules import PROVENANCE_USER, record_rule
        with two.db._get_connection() as conn:
            wanted = seed_artist(conn, server_id="ar-wanted", name="Wanted Band",
                                 server_source="soulsync")
            record_rule(conn, "artist", wanted, True, PROVENANCE_USER, profile_id=two.kim)
            conn.commit()
        assert "Wanted Band" in self._names(two.db, two.kim)

    def test_an_artist_kim_wants_one_track_of_is_in_her_library(self, two):
        from core.library2.monitor_rules import PROVENANCE_USER, record_rule
        with two.db._get_connection() as conn:
            artist = seed_artist(conn, server_id="ar-one", name="One Track Band",
                                 server_source="soulsync")
            album = seed_album(conn, server_id="al-one", title="Single",
                               artist_id=artist, server_source="soulsync")
            seed_track(conn, server_id="t-filler", title="Filler", album_id=album,
                       artist_id=artist, server_source="soulsync")
            wanted = seed_track(conn, server_id="t-one", title="The One", album_id=album,
                                artist_id=artist, server_source="soulsync")
            record_rule(conn, "track", wanted, True, PROVENANCE_USER, profile_id=two.kim)
            conn.commit()
        assert "One Track Band" in self._names(two.db, two.kim)

    def test_kims_monitoring_is_not_the_houses(self, two):
        """The global monitored column is the shared library's intent; Kim's
        rule shows in her library and nowhere else."""
        from core.library2.monitor_rules import PROVENANCE_USER, record_rule
        from core.library2.queries import list_artists
        with two.db._get_connection() as conn:
            kims = conn.execute("SELECT id FROM lib2_artists WHERE name='Kims Band'").fetchone()[0]
            record_rule(conn, "artist", kims, True, PROVENANCE_USER, profile_id=two.kim)
            conn.commit()
            with library_scope.library_scope(two.kim):
                mine, _ = list_artists(conn, monitored="monitored", include_size=False)
            with library_scope.library_scope("shared"):
                house, _ = list_artists(conn, monitored="monitored", include_size=False)
        assert [a["name"] for a in mine] == ["Kims Band"]
        assert house == []


# ── where a download lands (E-04) ────────────────────────────────────────────

class TestWhereADownloadLands:
    def test_a_batch_kim_starts_fills_her_library(self, lib):
        from core.runtime_state import _BatchRegistry
        batches = _BatchRegistry()
        batches["b"] = {"profile_id": lib.kim}
        assert batches["b"]["library_owner_id"] == lib.kim
        assert library_scope.batch_scope(batches["b"]) == lib.kim

    def test_a_shared_profiles_batch_fills_the_shared_library(self, lib):
        from core.runtime_state import _BatchRegistry
        batches = _BatchRegistry()
        batches["b"] = {"profile_id": lib.sam}
        assert "library_owner_id" in batches["b"]
        assert batches["b"]["library_owner_id"] is None

    def test_a_decided_shared_library_is_not_overruled_by_the_profile(self, lib, monkeypatch):
        import core.imports.paths as paths
        from core import runtime_state
        monkeypatch.setitem(runtime_state.download_batches, "decided",
                            {"profile_id": lib.kim, "library_owner_id": None})
        ctx = {"batch_id": "decided", "profile_id": lib.kim}
        assert paths.import_owner_id(ctx) is None
        assert paths.transfer_root_for_context(ctx) == lib.shared

    def test_the_file_goes_into_kims_folder(self, lib, monkeypatch):
        import core.imports.paths as paths
        ctx = {"library_owner_id": lib.kim}
        assert paths.transfer_root_for_context(ctx) == lib.kim_root

    def test_a_scope_set_for_a_unit_of_work_decides_a_new_file(self, lib):
        with library_scope.library_scope(lib.kim):
            assert library_scope.owner_for_new_file(None) == lib.kim
        with library_scope.library_scope("shared"):
            assert library_scope.owner_for_new_file(lib.kim) is None


# ── wishlist and duplicate downloads ─────────────────────────────────────────

class TestNothingCrossesLibraries:
    def test_the_same_track_is_wanted_once_per_library(self, lib):
        from core.wishlist.processing import _tag_wishlist_owner
        from core.wishlist.selection import sanitize_and_dedupe_wishlist_tracks
        kims = [{"id": "sp1", "name": "Song", "artists": ["A"]}]
        sams = [{"id": "sp1", "name": "Song", "artists": ["A"]}]
        admins = [{"id": "sp1", "name": "Song", "artists": ["A"]}]
        _tag_wishlist_owner(kims, lib.kim)
        _tag_wishlist_owner(sams, lib.sam)
        _tag_wishlist_owner(admins, 1)
        tracks, dupes = sanitize_and_dedupe_wishlist_tracks(kims + sams + admins)
        # Sam and the admin share one library: one download. Kim gets her own.
        assert dupes == 1
        assert sorted(t["_wishlist_library"] or 0 for t in tracks) == [0, lib.kim]

    def test_a_sibling_batch_for_another_library_does_not_stand_a_task_down(self, lib, monkeypatch):
        from core.downloads import task_worker
        batches = {"mine": {"library_owner_id": lib.kim}, "theirs": {"library_owner_id": None}}
        tasks = {
            "t-mine": {"batch_id": "mine", "track_info": {"name": "Song", "artists": [{"name": "A"}]}},
            "t-theirs": {"batch_id": "theirs", "status": "completed", "file_path": "/x.flac",
                         "track_info": {"name": "Song", "artists": [{"name": "A"}]}},
        }
        monkeypatch.setattr(task_worker, "download_batches", batches)
        monkeypatch.setattr(task_worker, "download_tasks", tasks)
        assert task_worker._find_owning_sibling("t-mine", None) == (None, None)
        batches["theirs"]["library_owner_id"] = lib.kim
        assert task_worker._find_owning_sibling("t-mine", None)[0] == "t-theirs"


# ── jobs work in one library (E-14) ──────────────────────────────────────────

class TestJobsWorkInOneLibrary:
    @pytest.fixture
    def two(self, lib):
        """ONE album with a copy in each library -- the case a job must not mix up."""
        artist_id, album_id, _ = _album_with_file(
            lib.db, artist="A", album="B", title="S",
            path=os.path.join(lib.shared, "A", "B", "01.flac"), key="js")
        with lib.db._get_connection() as conn:
            seed_track(conn, server_id="t-jk", title="K", album_id=album_id,
                       artist_id=artist_id, server_source="soulsync",
                       file_path=os.path.join(lib.kim_root, "A", "B", "01.flac"))
            conn.commit()
        return lib

    def _subject_paths(self, db, scope):
        from core.library2.maintenance_subjects import active_file_subjects
        with library_scope.library_scope(scope):
            return {os.path.dirname(os.path.dirname(os.path.dirname(s["path"])))
                    for s in active_file_subjects(db, None)}

    def test_a_run_started_in_kims_library_walks_her_files(self, two):
        assert self._subject_paths(two.db, two.kim) == {two.kim_root}

    def test_a_scheduled_run_walks_every_library(self, two):
        assert self._subject_paths(two.db, None) == {two.kim_root, two.shared}

    def test_findings_follow_the_folder_of_their_file(self, two):
        from core.repair_worker import _findings_library_clause
        with two.db._get_connection() as conn:
            conn.executemany(
                "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
                " entity_type, entity_id, file_path, title, description) "
                "VALUES ('j', 't', 'info', 'pending', 'track', '1', ?, ?, '')",
                [(os.path.join(two.kim_root, "x.flac"), "kim"),
                 (os.path.join(two.shared, "x.flac"), "shared"),
                 (None, "no file")])
            conn.commit()
            for scope, expected in ((two.kim, {"kim"}), ("shared", {"shared", "no file"})):
                with library_scope.library_scope(scope):
                    clause = _findings_library_clause()
                titles = {r[0] for r in conn.execute(
                    f"SELECT title FROM repair_findings WHERE {clause}")}
                assert titles == expected

    def test_the_findings_badges_count_the_library_on_screen(self, two):
        from core.repair_worker import RepairWorker
        with two.db._get_connection() as conn:
            conn.executemany(
                "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
                " entity_type, entity_id, file_path, title, description) "
                "VALUES ('j', 't', 'info', 'pending', 'track', '1', ?, ?, '')",
                [(os.path.join(two.kim_root, "x.flac"), "kim"),
                 (os.path.join(two.shared, "x.flac"), "shared")])
            conn.commit()
        worker = RepairWorker.__new__(RepairWorker)
        worker.db = two.db
        worker._jobs = {}
        worker._ensure_jobs_loaded = lambda: None
        for scope, pending in ((two.kim, 1), ("shared", 1), (None, 2)):
            with library_scope.library_scope(scope):
                counts = worker.get_findings_counts()
            assert counts["pending"] == pending
            assert counts["by_job"]["j"]["total"] == pending

    def test_a_reorganize_queued_for_all_libraries_runs_in_each_folder(self, two):
        from core.reorganize_runner import _libraries_for
        with two.db._get_connection() as conn:
            album_id = conn.execute("SELECT id FROM lib2_albums WHERE title='B'").fetchone()[0]
        item = SimpleNamespace(album_id=album_id, library=None)
        assert _libraries_for(two.db, item) == [("shared", two.shared), (two.kim, two.kim_root)]
        item.library = two.kim
        assert _libraries_for(two.db, item) == [(two.kim, two.kim_root)]

    def test_a_retag_writes_the_file_of_the_library_it_runs_in(self, two):
        from core.library2.retag import _track_rows
        with two.db._get_connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM lib2_tracks")]
            with library_scope.library_scope(two.kim):
                paths = {r["file_path"] for r in _track_rows(conn, ids) if r["file_path"]}
        assert paths and all(p.startswith(two.kim_root) for p in paths)
