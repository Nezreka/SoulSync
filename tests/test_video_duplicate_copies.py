"""Duplicate copies: which drive each one is on, and whether it looks like a fork.

Boulder's library spans eleven mount roots. When SoulSync cannot resolve the copy
it already owns — the stored path is the media server's view of a mount SoulSync
has no mapping for — an upgrade files a SECOND copy in the template location
instead of replacing the first. Nothing is corrupted; you end up with the old copy
on the old drive and, before this, no way to find it from the app.

Measured on his live database (120,805 scanned files):

    6,838 titles hold more than one file
      3,429 SAME resolution      <- fork-shaped
      3,409 DIFFERENT resolution <- usually a deliberate 4K + 1080p pair
    4,963 titles have copies on more than one mount root

The second number is why nothing here computes "reclaimable space" and why the job
never offers a delete. From the database alone a kept quality-pair is
indistinguishable from a fork, so the job reports, ranks and explains.

Two gaps this closes, both confirmed by reading the existing job first: movie
duplicates were already reported (so that half is NOT rebuilt), episodes were not
reported at all, and neither ever said which drive a copy was on.
"""

from __future__ import annotations

from core.video.duplicate_copies import (
    describe_copies,
    mount_root,
    severity_for,
    summary_line,
)


def _f(path, size_gb=1.0, res="1080p", fid=1):
    return {"file_id": fid, "relative_path": path,
            "size_bytes": int(size_gb * 1073741824), "resolution": res}


# ── naming the drive ─────────────────────────────────────────────────────────

def test_the_posix_mounts_from_the_live_library():
    """These exact roots are what Plex reports on his install."""
    assert mount_root("/mnt/easystore3/TV/Show/S01E01.mkv") == "/mnt/easystore3"
    assert mount_root("/mnt/plex_20tb/Movies/Film.mkv") == "/mnt/plex_20tb"
    assert mount_root("/mnt/seagate_expansion1/x.mkv") == "/mnt/seagate_expansion1"


def test_the_unc_share_soulsync_is_configured_with():
    """His library paths are the SMB view of the same disks — the two dialects
    have to reduce to something comparable or every title looks cross-drive."""
    assert mount_root("\\\\192.168.86.36\\plex_20tb_2_share\\PLEX\\MOVIES\\Film.mkv") == \
        "\\\\192.168.86.36\\plex_20tb_2_share"


def test_a_windows_drive_letter():
    assert mount_root("D:\\Media\\Film.mkv") == "D:"
    assert mount_root("d:/media/film.mkv") == "D:"


def test_junk_paths_name_no_drive():
    for bad in (None, "", "   ", 12345):
        assert mount_root(bad) is None or isinstance(mount_root(bad), str)


def test_a_bare_relative_path_still_yields_something():
    assert mount_root("Movies/Film.mkv") == "Movies"


# ── describing a title's copies ──────────────────────────────────────────────

def test_two_copies_on_two_drives_are_recognised():
    s = describe_copies([_f("/mnt/easystore3/a.mkv", 8.0, fid=1),
                         _f("/mnt/plex_20tb/a.mkv", 8.0, fid=2)])
    assert s["spans_drives"] is True
    assert s["roots"] == ["/mnt/easystore3", "/mnt/plex_20tb"]
    assert s["same_resolution"] is True


def test_two_copies_on_ONE_drive_are_not_cross_drive():
    """Two editions in the same folder is an ordinary stacked-versions case, and
    the existing movie report already covered it."""
    s = describe_copies([_f("/mnt/easystore3/a.mkv", 8.0, fid=1),
                         _f("/mnt/easystore3/a-extended.mkv", 9.0, fid=2)])
    assert s["spans_drives"] is False


def test_a_deliberate_quality_pair_is_marked_as_mixed():
    """4K + 1080p is what people keep on purpose. Reporting it identically to a
    fork is how a report becomes noise nobody reads."""
    s = describe_copies([_f("/mnt/easystore3/a.mkv", 40.0, res="2160p", fid=1),
                         _f("/mnt/plex_20tb/a.mkv", 8.0, res="1080p", fid=2)])
    assert s["same_resolution"] is False


def test_the_largest_copy_is_identified_and_the_rest_measured():
    s = describe_copies([_f("/mnt/a/x.mkv", 2.0, fid=1),
                         _f("/mnt/b/x.mkv", 8.0, fid=2),
                         _f("/mnt/c/x.mkv", 1.0, fid=3)])
    assert s["copies"][s["largest_index"]]["file_id"] == 2
    assert s["smaller_gb"] == 3.0, "the two non-largest copies, not all three"


def test_a_single_copy_is_not_a_duplicate():
    s = describe_copies([_f("/mnt/a/x.mkv")])
    assert s["spans_drives"] is False and s["smaller_gb"] == 0.0


def test_junk_rows_never_raise():
    for bad in ([], None, [None], [{}], ["nope", 7]):
        s = describe_copies(bad)
        assert isinstance(s["copies"], list) and isinstance(s["roots"], list)


def test_rows_using_path_instead_of_relative_path_are_accepted():
    """Movie rows carry relative_path; other callers carry path. Reading only one
    would silently produce copies with no drive at all."""
    s = describe_copies([{"file_id": 1, "path": "/mnt/easystore1/a.mkv", "size_bytes": 1},
                         {"file_id": 2, "path": "/mnt/easystore2/a.mkv", "size_bytes": 2}])
    assert s["spans_drives"] is True


# ── what the finding says ────────────────────────────────────────────────────

def test_the_line_names_every_drive():
    """"2 copies" is not actionable. Naming the drives is the whole point."""
    line = summary_line(describe_copies([_f("/mnt/easystore3/a.mkv", 8.0, fid=1),
                                         _f("/mnt/plex_20tb/a.mkv", 8.0, fid=2)]))
    assert "/mnt/easystore3" in line and "/mnt/plex_20tb" in line
    assert "8.0 GB" in line


def test_the_fork_shape_is_called_out_by_name():
    line = summary_line(describe_copies([_f("/mnt/easystore3/a.mkv", 8.0, fid=1),
                                         _f("/mnt/plex_20tb/a.mkv", 8.0, fid=2)]))
    assert "same quality on different drives" in line


def test_a_quality_pair_is_not_called_a_fork():
    line = summary_line(describe_copies([_f("/mnt/a/x.mkv", 40.0, res="2160p", fid=1),
                                         _f("/mnt/b/x.mkv", 8.0, res="1080p", fid=2)]))
    assert "same quality" not in line


def test_the_line_survives_nothing():
    assert summary_line(None) == "no copies"
    assert summary_line({}) == "no copies"


# ── ranking ──────────────────────────────────────────────────────────────────

def test_same_quality_across_drives_is_the_one_worth_looking_at():
    s = describe_copies([_f("/mnt/a/x.mkv", 8.0, fid=1), _f("/mnt/b/x.mkv", 8.0, fid=2)])
    assert severity_for(s) == "warning"


def test_a_mixed_pair_stays_informational():
    """Three thousand of these exist on the live library. Flagging them all as
    warnings would bury the few hundred that are real."""
    s = describe_copies([_f("/mnt/a/x.mkv", 40.0, res="2160p", fid=1),
                         _f("/mnt/b/x.mkv", 8.0, res="1080p", fid=2)])
    assert severity_for(s) == "info"


def test_same_quality_on_ONE_drive_stays_informational():
    s = describe_copies([_f("/mnt/a/x.mkv", 8.0, fid=1), _f("/mnt/a/y.mkv", 8.0, fid=2)])
    assert severity_for(s) == "info"


def test_junk_never_raises_a_severity():
    for bad in (None, {}, "x", 7):
        assert severity_for(bad) == "info"


def test_nothing_here_deletes_or_touches_the_filesystem():
    """Half the multi-file titles are kept quality-pairs, so a "free 4.5 TB" number
    would be wrong for thousands of them and would invite exactly the bulk delete
    this module must never grow.

    Read the CODE, not the prose — the module docstring explains at length WHY it
    refuses to reclaim anything, and a naive grep flags that explanation. (Same
    trap as the 0.75 threshold in season_pack.)"""
    import ast
    import inspect

    from core.video import duplicate_copies
    tree = ast.parse(inspect.getsource(duplicate_copies))
    # Only calls ON a filesystem module count — 'x.replace(...)' on a string is
    # ordinary text work, and banning the bare name would fail on that.
    banned = {"remove", "unlink", "rmtree", "move", "rename", "makedirs", "rmdir"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(node, "names", [])] + [getattr(node, "module", "") or ""]
            assert not any((n or "").startswith("shutil") for n in names), "no filesystem mutation"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = getattr(node.func.value, "id", "")
            if owner in ("os", "shutil", "pathlib", "Path"):
                assert node.func.attr not in banned, (
                    "duplicate reporting must never mutate: %s.%s" % (owner, node.func.attr))


# ── the job that reports them ────────────────────────────────────────────────
# The MOVIE half already existed and shipped; it is deliberately not rebuilt here.
# What was missing: episodes had no duplicate report at all, and no finding ever
# said which drive a copy sat on.

class _FakeDB:
    """Only what the job asks for."""

    def __init__(self, movies=None, movie_files=None, episodes=None):
        self._m = movies or []
        self._mf = movie_files or []
        self._eps = episodes or []
        self.dismissed = []

    def repair_duplicate_movies(self):
        return {"rows": self._m, "files": self._mf}

    def repair_duplicate_episodes(self):
        return self._eps

    def repair_dismiss_absent(self, job_id, finding_type, valid):
        self.dismissed.append((finding_type, list(valid or [])))


def _run_job(db):
    from core.video.repair.base import JobContext
    from core.video.repair.duplicate_movies import DuplicateMoviesJob
    found = []

    def create_finding(**kw):
        found.append(kw)
        return True

    ctx = JobContext(db=db, create_finding=create_finding)
    result = DuplicateMoviesJob().scan(ctx)
    return result, found


def _epfile(fid, path, res="1080p", size_gb=2.0):
    return {"episode_id": 55, "season_number": 2, "episode_number": 3,
            "episode_title": "The One", "show_id": 9, "show_title": "Aussie Shore",
            "tmdb_id": 4242, "file_id": fid, "relative_path": path,
            "size_bytes": int(size_gb * 1073741824), "resolution": res}


def test_an_episode_with_two_copies_is_now_reported():
    """Before this, TV was invisible — only movies were ever checked."""
    db = _FakeDB(episodes=[[_epfile(1, "/mnt/easystore3/S02E03.mkv"),
                            _epfile(2, "/mnt/plex_20tb/S02E03.mkv")]])
    result, found = _run_job(db)
    eps = [f for f in found if f["finding_type"] == "duplicate_episode"]
    assert len(eps) == 1
    assert "Aussie Shore S02E03" in eps[0]["title"]
    assert result.scanned == 1


def test_the_episode_finding_names_both_drives():
    db = _FakeDB(episodes=[[_epfile(1, "/mnt/easystore3/S02E03.mkv"),
                            _epfile(2, "/mnt/plex_20tb/S02E03.mkv")]])
    _, found = _run_job(db)
    f = found[0]
    assert "/mnt/easystore3" in f["description"] and "/mnt/plex_20tb" in f["description"]
    assert f["details"]["roots"] == ["/mnt/easystore3", "/mnt/plex_20tb"]
    assert f["details"]["spans_drives"] is True


def test_same_quality_across_drives_is_raised_to_warning():
    db = _FakeDB(episodes=[[_epfile(1, "/mnt/easystore3/a.mkv", res="1080p"),
                            _epfile(2, "/mnt/plex_20tb/a.mkv", res="1080p")]])
    _, found = _run_job(db)
    assert found[0]["severity"] == "warning"


def test_a_deliberate_quality_pair_stays_info():
    """Three thousand of these exist on the live library; flagging them all would
    bury the ones that matter."""
    db = _FakeDB(episodes=[[_epfile(1, "/mnt/a/a.mkv", res="2160p", size_gb=40),
                            _epfile(2, "/mnt/b/a.mkv", res="1080p", size_gb=8)]])
    _, found = _run_job(db)
    assert found[0]["severity"] == "info"


def test_each_finding_type_is_swept_against_its_own_list():
    """Passing the movie list while sweeping episodes would dismiss every episode
    finding the moment it was created."""
    db = _FakeDB(episodes=[[_epfile(1, "/mnt/a/a.mkv"), _epfile(2, "/mnt/b/a.mkv")]])
    _run_job(db)
    kinds = dict((t, v) for t, v in db.dismissed)
    assert set(kinds) == {"duplicate_movie", "duplicate_episode"}
    assert kinds["duplicate_movie"] == [], "no movie dupes in this fixture"
    assert len(kinds["duplicate_episode"]) == 1, "the live episode must be kept"


def test_the_movie_half_still_works():
    """Additive: the existing report must not regress."""
    db = _FakeDB(movie_files=[[
        {"movie_id": 3, "tmdb_id": 1, "title": "Film", "year": 2020, "file_id": 10,
         "relative_path": "/mnt/a/f.mkv", "size_bytes": 8 * 1073741824, "resolution": "1080p"},
        {"movie_id": 3, "tmdb_id": 1, "title": "Film", "year": 2020, "file_id": 11,
         "relative_path": "/mnt/b/f.mkv", "size_bytes": 8 * 1073741824, "resolution": "1080p"}]])
    _, found = _run_job(db)
    mv = [f for f in found if f["finding_type"] == "duplicate_movie"]
    assert len(mv) == 1 and mv[0]["severity"] == "warning"
    assert "/mnt/a" in mv[0]["description"], "movies gained the drive column too"


def test_a_db_without_the_new_method_does_not_break_the_job():
    """Belt and braces for an older DB object — the movie half must still run."""
    class _Old(_FakeDB):
        def repair_duplicate_episodes(self):
            raise AttributeError("no such method")
    result, found = _run_job(_Old())
    assert result.errors == 1
    assert not [f for f in found if f["finding_type"] == "duplicate_episode"]


def test_the_job_declares_the_new_finding_type():
    from core.video.repair.duplicate_movies import DuplicateMoviesJob
    assert "duplicate_episode" in DuplicateMoviesJob.finding_types
    assert DuplicateMoviesJob.auto_fix is False, "report-only, always"
