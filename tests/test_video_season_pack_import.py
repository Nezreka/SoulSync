"""Phase 2 of pack ingest: a finished pack becomes N imported episodes, not one.

What this fixes, precisely. A torrent season pack finishes as a FOLDER. The torrent
path then calls ``find_video``, which narrows a folder to its single largest video —
so the importer was handed one episode out of ten. And because a pack row carries no
single episode identity, that one file was then rejected outright ("Season/complete
packs need manual import"), the row went **import_failed** — terminal, no retry — and
the whole pack sat on disk while every episode stayed on the wishlist. One click, one
dead download, ten wanted episodes.

The shape of the fix is the music album bundle's (``core.downloads.staging`` +
``album_bundle_dispatch``): the pack itself places nothing. It hands each file to the
SAME importer a single-episode grab uses, so there is one import path — parse, ffprobe
verify, template rename, upgrade-or-replace, sidecars — rather than a second one that
drifts from it.

Soulseek is untouched on purpose and it is worth saying why: ``/downloads/grab-pack``
already fans out at GRAB time, because slskd lists a pack folder's files before you
download them. A torrent can't — its files do not exist until it completes — so the
fan-out has to happen here instead.
"""

from __future__ import annotations

import json
import os

import pytest

from core.video.client_download import process_client_download
from core.video.season_pack import is_pack_download, want_season_of

_BIG = 900 * 1024 * 1024


def _pack_row(season=1, **over):
    ctx = {"scope": "season", "title": "Some Show", "season": season, "year": 2026}
    row = {"id": 7, "kind": "show", "title": "Some Show", "source": "torrent",
           "client_ref": "abc", "release_title": "Some.Show.S01.1080p.WEB-GROUP",
           "search_ctx": json.dumps(ctx), "media_id": "44", "media_source": "tmdb"}
    row.update(over)
    return row


# ── which rows are packs ─────────────────────────────────────────────────────

def test_a_season_scoped_row_is_a_pack():
    assert is_pack_download(_pack_row()) is True


def test_a_show_row_with_a_season_but_no_episode_is_a_pack():
    """The commonest real shape: 'Grab season' builds a season context and there is
    no episode number, because the grab is for all of them."""
    row = _pack_row(search_ctx=json.dumps({"title": "S", "season": 3}))
    assert is_pack_download(row) is True
    assert want_season_of(row) == 3


def test_a_single_episode_grab_is_never_a_pack():
    """The regression that would matter most: treating ordinary episode grabs as
    packs would route every TV download through the folder mapper."""
    row = _pack_row(search_ctx=json.dumps({"scope": "episode", "season": 1, "episode": 4}))
    assert is_pack_download(row) is False


def test_a_movie_is_never_a_pack():
    assert is_pack_download({"kind": "movie", "search_ctx": json.dumps({"scope": "movie"})}) is False
    assert is_pack_download({"kind": "movie"}) is False


def test_a_whole_series_grab_is_a_pack_with_no_season_filter():
    row = _pack_row(search_ctx=json.dumps({"scope": "series", "title": "S"}))
    assert is_pack_download(row) is True
    assert want_season_of(row) is None


def test_junk_rows_do_not_raise():
    for bad in (None, {}, {"search_ctx": "not json"}, {"search_ctx": "[1,2]"}, {"kind": None}):
        assert is_pack_download(bad) in (True, False)
        assert want_season_of(bad) is None


# ── the seam: a pack never reaches find_video ────────────────────────────────

class _Status:
    def __init__(self, path, name=None):
        self.state, self.progress = "completed", 1.0
        self.content_path, self.name = path, name


def _run(dl, status, *, import_pack=None, organizer=None, find_video=None):
    calls = {"find_video": []}

    def _fv(root, name):
        calls["find_video"].append((root, name))
        return (find_video or (lambda r, n: None))(root, name)

    out = process_client_download(dl, get_status=lambda s, r: status,
                                  resolve_path=lambda p: p, find_video=_fv,
                                  organizer=organizer, import_pack=import_pack)
    return out, calls


def test_a_pack_is_handed_the_folder_and_never_narrowed_to_one_file():
    """The whole bug in one assertion: find_video must not be consulted, because
    its answer — the largest single episode — is what stranded the other nine."""
    seen = {}

    def _pack(dl, root, name):
        seen["root"], seen["name"] = root, name
        return {"status": "completed", "progress": 100.0, "_pack_imported": 10}

    out, calls = _run(_pack_row(), _Status("/dl/Some.Show.S01"), import_pack=_pack)
    assert out["_pack_imported"] == 10
    assert seen["root"] == "/dl/Some.Show.S01"
    assert calls["find_video"] == [], "a pack must be mapped, not narrowed"


def test_a_pack_that_is_actually_one_file_falls_back_to_the_normal_path():
    """Some 'season' grabs are a single double-length episode or a lone file. The
    importer returning None must degrade to the ordinary single-file import rather
    than failing the download."""
    out, calls = _run(_pack_row(), _Status("/dl/Some.Show.S01E01.mkv"),
                      import_pack=lambda dl, root, name: None,
                      find_video=lambda r, n: "/dl/Some.Show.S01E01.mkv",
                      organizer=lambda dl, src: {"status": "completed", "dest_path": src})
    assert out["status"] == "completed"
    assert calls["find_video"] == [("/dl/Some.Show.S01E01.mkv", None)]


def test_an_ordinary_episode_still_takes_the_single_file_path():
    row = _pack_row(search_ctx=json.dumps({"scope": "episode", "season": 1, "episode": 2}))
    out, calls = _run(row, _Status("/dl/x.mkv"),
                      import_pack=lambda *a: pytest.fail("not a pack"),
                      find_video=lambda r, n: "/dl/x.mkv",
                      organizer=lambda dl, src: {"status": "completed", "dest_path": src})
    assert out["status"] == "completed" and calls["find_video"]


def test_a_pack_still_downloading_is_left_alone():
    st = _Status("/dl/Some.Show.S01")
    st.state, st.progress = "downloading", 0.4
    out, _ = _run(_pack_row(), st, import_pack=lambda *a: pytest.fail("not finished yet"))
    assert out["status"] == "downloading"


def test_the_pack_seam_is_inert_when_no_importer_is_injected():
    """Older callers pass no import_pack; they must behave exactly as before."""
    out, calls = _run(_pack_row(), _Status("/dl/S01"),
                      find_video=lambda r, n: "/dl/S01/e1.mkv",
                      organizer=lambda dl, src: {"status": "completed", "dest_path": src})
    assert calls["find_video"] == [("/dl/S01", None)]
    assert out["status"] == "completed"


def test_the_save_path_fallback_carries_the_job_name_into_the_pack_importer():
    """Clients that report no content_path give save_path + name; the name is the
    ONLY thing scoping the pack to its own folder inside a shared download dir."""
    seen = {}
    st = _Status(None, name="Some.Show.S01.1080p.WEB-GROUP")
    st.save_path = "/dl"

    def _pack(dl, root, name):
        seen.update(root=root, name=name)
        return {"status": "completed", "_pack_imported": 1}

    _run(_pack_row(), st, import_pack=_pack)
    assert seen == {"root": "/dl", "name": "Some.Show.S01.1080p.WEB-GROUP"}


# ── the fan-out ──────────────────────────────────────────────────────────────

class _DB:
    """Just enough of the video DB to watch what the fan-out records."""

    def __init__(self):
        self.added, self.updates, self.removed = [], [], []

    def add_video_download(self, rec):
        self.added.append(rec)
        return 100 + len(self.added)

    def update_video_download(self, dl_id, **fields):
        self.updates.append((dl_id, fields))

    def record_download_history(self, row):
        pass

    def remove_from_wishlist(self, kind, **kw):
        self.removed.append((kind, kw))

    def show_tmdb_id(self, mid):
        return 44


def _importer(db, organize, tmp_path, files):
    """Build the real pack importer against a real folder on disk."""
    from core.video.download_monitor import _make_pack_importer
    root = tmp_path / "Some.Show.S01.1080p.WEB-GROUP"
    root.mkdir()
    for name, size in files:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * size)
    return _make_pack_importer(db, organize), str(root)


def test_every_episode_in_the_pack_is_imported(tmp_path):
    seen = []

    def organize(dl, src):
        seen.append((json.loads(dl["search_ctx"])["episode"], os.path.basename(src)))
        return {"status": "completed", "dest_path": "/tv/" + os.path.basename(src),
                "quality_label": "1080p"}

    db = _DB()
    imp, root = _importer(db, organize, tmp_path, [
        ("Some.Show.S01E01.1080p.mkv", 40 << 20),
        ("Some.Show.S01E02.1080p.mkv", 40 << 20),
        ("Some.Show.S01E03.1080p.mkv", 40 << 20),
    ])
    out = imp(_pack_row(), root, None)
    assert out["status"] == "completed" and out["_pack_imported"] == 3
    assert [e for e, _ in seen] == [1, 2, 3]


def test_each_episode_carries_its_own_identity_not_the_packs(tmp_path):
    """The rename template and the wrong-episode gate both read these. A row still
    holding 'Some.Show.S01.1080p.WEB-GROUP' parses to no episode at all, which
    silently switches the gate off — exactly how E04 ends up in E03's slot."""
    rows = []

    def organize(dl, src):
        rows.append(dl)
        return {"status": "completed", "dest_path": "/tv/x.mkv"}

    db = _DB()
    imp, root = _importer(db, organize, tmp_path,
                          [("Some.Show.S01E05.1080p.mkv", 40 << 20)])
    imp(_pack_row(), root, None)
    ctx = json.loads(rows[0]["search_ctx"])
    assert (ctx["scope"], ctx["season"], ctx["episode"]) == ("episode", 1, 5)
    assert rows[0]["release_title"] == "Some.Show.S01E05.1080p.mkv"
    assert rows[0]["title"] == "Some Show", "the SHOW title must survive"


def test_each_imported_episode_gets_its_own_completed_row(tmp_path):
    """The pack row is the batch — mirroring the music album bundle, where the
    bundle stages and the per-track rows do the importing. Without a row per
    episode the Downloads page shows one entry for ten files."""
    db = _DB()
    imp, root = _importer(db, lambda dl, src: {"status": "completed", "dest_path": "/tv/" + os.path.basename(src)},
                          tmp_path, [("S.S01E01.mkv", 40 << 20), ("S.S01E02.mkv", 40 << 20)])
    imp(_pack_row(), root, None)
    assert len(db.added) == 2
    assert all(r["status"] == "completed" for r in db.added)
    assert {json.loads(r["search_ctx"])["episode"] for r in db.added} == {1, 2}


def test_the_wishlist_row_is_cleared_per_episode(tmp_path):
    db = _DB()
    imp, root = _importer(db, lambda dl, src: {"status": "completed", "dest_path": "/tv/x.mkv",
                                               "quality_label": "1080p"},
                          tmp_path, [("S.S01E01.mkv", 40 << 20), ("S.S01E02.mkv", 40 << 20)])
    imp(_pack_row(), root, None)
    assert [kw["episode_number"] for _k, kw in db.removed] == [1, 2]
    assert all(kw["season_number"] == 1 for _k, kw in db.removed)


def test_episodes_the_pack_did_not_supply_keep_their_wishlist_rows(tmp_path):
    """A partial pack must not look complete. Their rows stay, so the ordinary
    hourly drain chases them — one click never becomes twenty instant searches."""
    db = _DB()
    imp, root = _importer(db, lambda dl, src: {"status": "completed", "dest_path": "/tv/x.mkv"},
                          tmp_path, [("S.S01E01.mkv", 40 << 20), ("S.S01E09.mkv", 40 << 20)])
    imp(_pack_row(), root, None)
    assert [kw["episode_number"] for _k, kw in db.removed] == [1, 9]


def test_one_refused_episode_does_not_abandon_the_rest(tmp_path):
    def organize(dl, src):
        if "E02" in src:
            return {"status": "import_failed", "error": "not an upgrade"}
        return {"status": "completed", "dest_path": "/tv/" + os.path.basename(src)}

    db = _DB()
    imp, root = _importer(db, organize, tmp_path, [
        ("S.S01E01.mkv", 40 << 20), ("S.S01E02.mkv", 40 << 20), ("S.S01E03.mkv", 40 << 20)])
    out = imp(_pack_row(), root, None)
    assert out["status"] == "completed"
    assert (out["_pack_imported"], out["_pack_failed"]) == (2, 1)
    assert len(db.removed) == 2, "the refused episode keeps its wishlist row"


def test_an_episode_whose_import_raises_does_not_take_down_the_pack(tmp_path):
    def organize(dl, src):
        if "E01" in src:
            raise RuntimeError("ffprobe fell over")
        return {"status": "completed", "dest_path": "/tv/x.mkv"}

    db = _DB()
    imp, root = _importer(db, organize, tmp_path,
                          [("S.S01E01.mkv", 40 << 20), ("S.S01E02.mkv", 40 << 20)])
    out = imp(_pack_row(), root, None)
    assert out["_pack_imported"] == 1 and out["_pack_failed"] == 1


def test_a_pack_of_junk_fails_the_row_with_a_countable_reason(tmp_path):
    """It must NOT report success. The old behaviour's real cost was a download
    that looked finished while nothing had been filed."""
    db = _DB()
    imp, root = _importer(db, lambda dl, src: pytest.fail("nothing should import"),
                          tmp_path, [("readme.nfo", 100), ("poster.jpg", 100)])
    out = imp(_pack_row(), root, None)
    assert out["status"] == "import_failed"
    assert "2 files" in out["error"]


def test_a_pack_whose_every_episode_is_refused_is_not_reported_as_completed(tmp_path):
    db = _DB()
    imp, root = _importer(db, lambda dl, src: {"status": "import_failed", "error": "already have better"},
                          tmp_path, [("S.S01E01.mkv", 40 << 20)])
    out = imp(_pack_row(), root, None)
    assert out["status"] == "import_failed"
    assert "already have better" in out["error"]


def test_the_sample_beside_the_real_file_is_not_imported(tmp_path):
    """The classic pack trap: a sample parses to the same SxxExx as the episode."""
    got = []
    db = _DB()
    imp, root = _importer(db, lambda dl, src: (got.append(os.path.basename(src)) or
                                               {"status": "completed", "dest_path": "/tv/x.mkv"}),
                          tmp_path, [("S.S01E01.1080p.mkv", 60 << 20),
                                     ("Sample/S.S01E01.sample.mkv", 40 << 20)])
    imp(_pack_row(), root, None)
    assert got == ["S.S01E01.1080p.mkv"]


def test_a_stray_wrong_season_file_is_not_filed(tmp_path):
    got = []
    db = _DB()
    imp, root = _importer(db, lambda dl, src: (got.append(os.path.basename(src)) or
                                               {"status": "completed", "dest_path": "/tv/x.mkv"}),
                          tmp_path, [("S.S02E01.mkv", 40 << 20), ("S.S01E09.mkv", 40 << 20)])
    imp(_pack_row(season=2), root, None)
    assert got == ["S.S02E01.mkv"]


def test_a_single_file_job_returns_none_so_the_caller_falls_back(tmp_path):
    p = tmp_path / "Some.Show.S01E01.mkv"
    p.write_bytes(b"\0" * (40 << 20))
    from core.video.download_monitor import _make_pack_importer
    imp = _make_pack_importer(_DB(), lambda dl, src: pytest.fail("not reached"))
    assert imp(_pack_row(), str(p), None) is None


def test_every_skipped_file_is_logged_with_its_reason(tmp_path, caplog):
    """#706/#708: on the music side a staged file that silently failed to match
    produced 'download it, stage it, never claim it, re-add to the wishlist' — a
    loop nobody could diagnose from the logs."""
    import logging
    caplog.set_level(logging.INFO)
    db = _DB()
    imp, root = _importer(db, lambda dl, src: {"status": "completed", "dest_path": "/tv/x.mkv"},
                          tmp_path, [("S.S01E01.mkv", 40 << 20), ("mystery.mkv", 40 << 20),
                                     ("notes.txt", 10)])
    imp(_pack_row(), root, None)
    text = caplog.text
    assert "mystery.mkv" in text and "no episode number" in text
    assert "notes.txt" in text and "not a video" in text


# ── the batch row must not double-report ─────────────────────────────────────

def test_the_batch_row_does_not_publish_a_second_notification():
    """Ten episodes already published ten events. An eleventh naming no episode
    is noise a notification rule cannot even match on."""
    from core.video import download_monitor as dm
    published = []
    import sys
    import types
    mod = types.ModuleType("core.video.download_events")
    mod.publish = lambda name, payload: published.append(name)
    old = sys.modules.get("core.video.download_events")
    sys.modules["core.video.download_events"] = mod
    try:
        dm._publish_terminal(_pack_row(), {"status": "completed", "_pack_imported": 4})
        assert published == []
        dm._publish_terminal(_pack_row(), {"status": "completed"})
        assert published == ["video_download_completed"]
    finally:
        if old is not None:
            sys.modules["core.video.download_events"] = mod if old is None else old
        else:
            sys.modules.pop("core.video.download_events", None)


def test_the_batch_rows_own_wishlist_clear_is_a_no_op():
    """The pack row has a season but no episode, so the generic completion handler
    must not try to clear anything with it — the per-episode calls already did."""
    from core.video.download_monitor import _wishlist_obtained
    db = _DB()
    _wishlist_obtained(db, _pack_row(), {"status": "completed"})
    assert db.removed == []
