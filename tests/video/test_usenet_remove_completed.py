"""Remove Completed (video): a usenet job leaves the client once its import landed.

SoulSync never took a finished job out of SABnzbd/NZBGet. The importer already
reclaims the video it took (and the job folder when that ends up empty), so on
the video side what stayed behind was the history entry plus any nfo or sample.

One setting, ``usenet_remove_completed``: off (default, the old behavior),
history (remove the job, keep its files) or files (remove it and delete what is
left in its folder). It runs only for a ``completed`` row, after the row is
persisted; failed and import_failed rows keep their job for Manual import, and
a season pack goes only when every episode it mapped imported and it placed
every video it held.

``files`` deletes the job's folder itself (SABnzbd's ``del_files`` only deletes
a FAILED job's files), and only a folder the client's own report proves is the
job's: named after it or SAB's ``<job>.N``, not a link, not a protected root.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from core.downloads import remove_completed as rc
from core.video import download_config
from core.video import download_monitor as monitor
from core.video.season_pack import map_pack

_ROOT = Path(__file__).resolve().parents[2]


class _Adapter:
    def __init__(self, result=True, boom=False):
        self.calls = []
        self.result, self.boom = result, boom

    async def remove(self, job_id, delete_files=False):
        self.calls.append((job_id, delete_files))
        if self.boom:
            raise RuntimeError("client down")
        return self.result


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class _DB:
    def __init__(self, **settings):
        self.s = dict(settings)

    def get_setting(self, key):
        return self.s.get(key)

    def set_setting(self, key, value):
        self.s[key] = value


# ── the setting ───────────────────────────────────────────────────────────────

def test_the_mode_is_off_unless_it_is_one_of_the_three():
    assert rc.MODES == ("off", "history", "files")
    for raw, want in (("history", "history"), ("FILES", "files"), (" off ", "off"),
                      (None, "off"), ("", "off"), ("delete", "off"), (1, "off")):
        assert rc.normalize(raw) == want


def test_the_video_setting_defaults_off_and_saves_normalised():
    db = _DB()
    assert download_config.load(db)["usenet_remove_completed"] == "off"
    download_config.save(db, {"usenet_remove_completed": "Files"})
    assert download_config.load(db)["usenet_remove_completed"] == "files"
    download_config.save(db, {"usenet_remove_completed": "nonsense"})
    assert download_config.load(db)["usenet_remove_completed"] == "off"


def test_saving_other_download_settings_leaves_it_alone():
    db = _DB(usenet_remove_completed="history")
    download_config.save(db, {"seed_ratio_goal": 2})
    assert download_config.load(db)["usenet_remove_completed"] == "history"


# ── asking the client ─────────────────────────────────────────────────────────

def test_off_never_calls_the_client():
    a = _Adapter()
    assert rc.remove_imported_job("nzo_1", "off", run=_run, adapter=a) is False
    assert a.calls == []


def test_history_keeps_the_files_and_files_deletes_them():
    a = _Adapter()
    assert rc.remove_imported_job("nzo_1", "history", run=_run, adapter=a) is True
    assert rc.remove_imported_job("nzo_2", "files", run=_run, adapter=a) is True
    assert a.calls == [("nzo_1", False), ("nzo_2", True)]


def test_a_client_error_is_logged_not_raised():
    a = _Adapter(boom=True)
    assert rc.remove_imported_job("nzo_1", "files", run=_run, adapter=a) is False


def test_no_job_id_or_a_refusal_reports_false():
    a = _Adapter(result=False)
    assert rc.remove_imported_job("", "files", run=_run, adapter=a) is False
    assert a.calls == []
    assert rc.remove_imported_job("nzo_1", "files", run=_run, adapter=a) is False


# ── files: the job's own folder, and nothing else ────────────────────────────

class _Reporting(_Adapter):
    """An adapter that also reports the job (name + final path), like SAB's
    history slot. The report is gone once the job is removed."""
    def __init__(self, name, save_path, result=True):
        super().__init__(result=result)
        self.name, self.save_path = name, save_path

    async def get_status(self, job_id):
        if any(call[0] == job_id for call in self.calls):
            return None
        return _St(name=self.name, save_path=self.save_path, state="completed")


class _St:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _job(tmp_path, name="Some.Show.S01E01.1080p.WEB-DL", files=("some.show.nfo",)):
    job = tmp_path / "complete" / "soulsync" / name
    job.mkdir(parents=True)
    for f in files:
        (job / f).write_bytes(b"x")
    return job


def test_a_job_folder_is_named_after_the_job_or_sabs_repeat_name():
    assert rc.matches_job_name("Some.Show", "some.show")
    assert rc.matches_job_name("Some.Show.1", "Some.Show")
    assert rc.matches_job_name("Some.Show.12", "Some.Show")
    for folder in ("soulsync", "Some.Show.x", "Some.Show.", "Some.Show.1.2", "Some", ""):
        assert not rc.matches_job_name(folder, "Some.Show")
    assert not rc.matches_job_name("Some.Show", "")


def test_the_reported_folder_or_a_files_parent_is_the_job_folder(tmp_path):
    job = _job(tmp_path, files=("ep.mkv",))
    assert rc.job_folder(str(job), job.name) == str(job)
    assert rc.job_folder(str(job / "ep.mkv"), job.name) == str(job)
    repeat = _job(tmp_path, name=job.name + ".1", files=("ep.mkv",))
    assert rc.job_folder(str(repeat / "ep.mkv"), job.name) == str(repeat)


def test_a_category_folder_a_link_or_a_protected_root_is_never_the_job_folder(tmp_path):
    job = _job(tmp_path, files=("ep.mkv",))
    category = job.parent
    (category / "loose.mkv").write_bytes(b"x")
    # a category without job folders: the file sits straight in the category
    assert rc.job_folder(str(category / "loose.mkv"), "loose") is None
    assert rc.job_folder(str(category), job.name) is None
    link = tmp_path / job.name
    link.symlink_to(job, target_is_directory=True)
    assert rc.job_folder(str(link), job.name) is None
    assert rc.job_folder(str(job), job.name, protected=[str(job)]) is None
    assert rc.job_folder(str(job), job.name, protected=[str(job / "inner")]) is None
    assert rc.job_folder(str(job), job.name, protected=[str(category)]) == str(job)
    assert rc.job_folder(str(tmp_path / "gone"), "gone") is None
    assert rc.job_folder(None, job.name) is None and rc.job_folder(str(job), "") is None


def test_the_reported_path_is_resolved_to_this_mount(tmp_path):
    job = _job(tmp_path)
    resolve = lambda p: p.replace("/client/complete", str(job.parent.parent))  # noqa: E731
    assert rc.job_folder("/client/complete/soulsync/" + job.name, job.name,
                         resolve=resolve) == str(job)


def test_files_deletes_the_job_folder_after_the_client_removed_the_job(tmp_path):
    job = _job(tmp_path)
    a = _Reporting(job.name, str(job))
    assert rc.remove_imported_job("nzo_1", "files", run=_run, adapter=a) is True
    assert a.calls == [("nzo_1", True)] and not job.exists()
    assert job.parent.exists()


def test_history_and_a_refused_removal_keep_the_folder(tmp_path):
    job = _job(tmp_path)
    assert rc.remove_imported_job("nzo_1", "history", run=_run,
                                  adapter=_Reporting(job.name, str(job))) is True
    assert job.exists()
    assert rc.remove_imported_job("nzo_1", "files", run=_run,
                                  adapter=_Reporting(job.name, str(job), result=False)) is False
    assert job.exists()


def test_files_keeps_a_folder_it_cannot_prove_is_the_jobs(tmp_path):
    job = _job(tmp_path)
    # the client reports the category itself, or a name that is not the folder's
    for name, path in ((job.name, str(job.parent)), ("Other.Release", str(job))):
        assert rc.remove_imported_job("nzo_1", "files", run=_run,
                                      adapter=_Reporting(name, path)) is True
        assert job.exists()
    assert rc.remove_imported_job("nzo_1", "files", run=_run,
                                  adapter=_Reporting(job.name, str(job)),
                                  protected=[str(job)]) is True
    assert job.exists()


def test_files_without_a_report_still_removes_the_job(tmp_path):
    job = _job(tmp_path)
    a = _Adapter()                       # no get_status at all
    assert rc.remove_imported_job("nzo_1", "files", run=_run, adapter=a) is True
    assert a.calls == [("nzo_1", True)] and job.exists()


# ── sab's repeat-name folders count as the job's own ──────────────────────────

def test_a_single_file_job_in_a_repeat_name_folder_has_a_job_dir(tmp_path):
    from core.video.client_download import _usenet_job_dir
    job = _job(tmp_path, name="Some.Show.S01E01.1080p.WEB-DL.1", files=("ep.mkv",))
    dl = {"source": "usenet"}
    assert _usenet_job_dir(dl, str(job / "ep.mkv"), None,
                           job_name="Some.Show.S01E01.1080p.WEB-DL") == str(job)
    assert _usenet_job_dir(dl, str(job / "ep.mkv"), None, job_name="Other") is None


# ── which rows qualify ────────────────────────────────────────────────────────

def _usenet_row(**kw):
    row = {"id": 1, "source": "usenet", "client_ref": "SABnzbd_nzo_1", "title": "Some Show"}
    row.update(kw)
    return row


def _hook(monkeypatch, db, dl, upd):
    a = _Adapter()
    monkeypatch.setattr("core.usenet_clients.get_active_adapter", lambda: a)
    return monitor._remove_completed_usenet(db, dl, upd), a.calls


def test_a_completed_usenet_import_is_removed_per_the_setting(monkeypatch):
    done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="history"),
                        _usenet_row(), {"status": "completed"})
    assert done is True and calls == [("SABnzbd_nzo_1", False)]
    done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="files"),
                        _usenet_row(), {"status": "completed"})
    assert done is True and calls == [("SABnzbd_nzo_1", True)]


def test_off_by_default(monkeypatch):
    done, calls = _hook(monkeypatch, _DB(), _usenet_row(), {"status": "completed"})
    assert done is False and calls == []


def test_failed_and_import_failed_rows_keep_their_job(monkeypatch):
    for status in ("import_failed", "failed", "cancelled", "downloading", None):
        done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="files"),
                            _usenet_row(), {"status": status})
        assert done is False and calls == [], status


def test_torrents_and_other_sources_are_never_touched(monkeypatch):
    for source in ("torrent", "soulseek", "youtube", None):
        done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="files"),
                            _usenet_row(source=source), {"status": "completed"})
        assert done is False and calls == [], source


def test_a_row_without_a_client_job_is_skipped(monkeypatch):
    done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="files"),
                        _usenet_row(client_ref=""), {"status": "completed"})
    assert done is False and calls == []


def test_a_pack_with_an_episode_not_imported_keeps_its_job(monkeypatch):
    done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="files"), _usenet_row(),
                        {"status": "completed", "_pack_imported": 9, "_pack_failed": 1})
    assert done is False and calls == []


def test_a_pack_with_a_video_it_could_not_place_keeps_its_job(monkeypatch):
    done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="files"), _usenet_row(),
                        {"status": "completed", "_pack_imported": 10, "_pack_failed": 0,
                         "_pack_unplaced": 1})
    assert done is False and calls == []


def test_a_fully_imported_pack_is_removed(monkeypatch):
    done, calls = _hook(monkeypatch, _DB(usenet_remove_completed="files"), _usenet_row(),
                        {"status": "completed", "_pack_imported": 10, "_pack_failed": 0,
                         "_pack_unplaced": 0})
    assert done is True and calls == [("SABnzbd_nzo_1", True)]


def test_the_hook_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no client package")
    monkeypatch.setattr("core.usenet_clients.get_active_adapter", boom)
    assert monitor._remove_completed_usenet(_DB(usenet_remove_completed="files"),
                                            _usenet_row(), {"status": "completed"}) is False
    bad_db = Mock()
    bad_db.get_setting.side_effect = RuntimeError("db gone")
    assert monitor._remove_completed_usenet(bad_db, _usenet_row(), {"status": "completed"}) is False


# ── season packs say what they could not place ────────────────────────────────

def _big(name):
    return {"path": "/pack/" + name, "size_bytes": 900 * 1024 * 1024}


def test_a_video_with_no_episode_number_or_another_season_is_unplaced():
    out = map_pack([_big("Show.S02E01.mkv"), _big("Show.Finale.mkv"), _big("Show.S01E05.mkv")],
                   want_season=2, root="/pack")
    assert sorted(out["claimed"]) == [(2, 1)]
    unplaced = sorted(Path(s["path"]).name for s in out["skipped"] if s.get("unplaced"))
    assert unplaced == ["Show.Finale.mkv", "Show.S01E05.mkv"]


def test_samples_non_video_and_smaller_duplicates_are_not_unplaced():
    out = map_pack([_big("Show.S01E01.mkv"),
                    {"path": "/pack/Show.S01E01.sample.mkv", "size_bytes": 20 * 1024 * 1024},
                    {"path": "/pack/release.nfo", "size_bytes": 1000},
                    {"path": "/pack/Show.S01E01.720p.mkv", "size_bytes": 400 * 1024 * 1024}],
                   want_season=1, root="/pack")
    assert out["skipped"], out
    assert not [s for s in out["skipped"] if s.get("unplaced")], out["skipped"]


# ── end to end through a monitor tick ─────────────────────────────────────────


def _tick_setup(tmp_path, monkeypatch, organizer, setting):
    import core.video.client_download as client
    job = tmp_path / "complete" / "soulsync" / "Some.Show.S01E01.1080p.WEB-DL"
    job.mkdir(parents=True)
    video = job / "some.show.s01e01.1080p.mkv"
    video.write_bytes(b"x" * 1000)
    row = {"id": 7, "source": "usenet", "status": "downloading", "client_ref": "SABnzbd_nzo_7",
           "progress": 99, "title": "Some Show",
           "search_ctx": {"scope": "episode", "title": "Some Show", "season": 1, "episode": 1}}
    order = []
    db = Mock()
    db.get_active_video_downloads.side_effect = [[row], []]
    db.get_setting.side_effect = lambda key: setting if key == "usenet_remove_completed" else None

    def persist(key, **patch):
        order.append("persist")
        row.update({k: v for k, v in patch.items() if not k.startswith("_")})
    db.update_video_download.side_effect = persist
    adapter = _Adapter()
    real_remove = adapter.remove

    async def remove(job_id, delete_files=False):
        order.append("remove")
        return await real_remove(job_id, delete_files=delete_files)
    adapter.remove = remove
    monkeypatch.setattr("core.usenet_clients.get_active_adapter", lambda: adapter)
    monkeypatch.setattr(monitor, "_make_organizer", lambda db: organizer)
    monkeypatch.setattr(monitor, "_make_pack_importer", lambda db, organizer: None)
    monkeypatch.setattr(monitor, "_misses", {})
    monkeypatch.setattr(monitor, "_protected_roots", lambda: set())
    monkeypatch.setattr(monitor, "_wishlist_obtained", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_archive_history", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_publish_terminal", lambda *a, **k: None)
    monkeypatch.setattr(client, "_get_status", lambda source, ref: _St(
        state="completed", progress=1.0, save_path=str(job), name=job.name))
    return db, row, order, adapter


def test_a_tick_persists_the_import_then_removes_the_job(tmp_path, monkeypatch):
    def organizer(dl, src):
        return {"status": "completed", "progress": 100.0, "dest_path": "/lib/x.mkv",
                "_cleanup_source": src}
    db, row, order, adapter = _tick_setup(tmp_path, monkeypatch, organizer, "files")
    monitor._tick(db)
    assert row["status"] == "completed"
    assert order == ["persist", "remove"]
    assert adapter.calls == [("SABnzbd_nzo_7", True)]


def test_a_tick_with_files_deletes_what_the_import_left_in_the_job_folder(tmp_path, monkeypatch):
    def organizer(dl, src):
        return {"status": "completed", "progress": 100.0, "dest_path": "/lib/x.mkv",
                "_cleanup_source": src}
    db, row, order, adapter = _tick_setup(tmp_path, monkeypatch, organizer, "files")
    job = tmp_path / "complete" / "soulsync" / "Some.Show.S01E01.1080p.WEB-DL"
    (job / "some.show.nfo").write_bytes(b"nfo")       # what the importer leaves behind
    adapter.get_status = lambda job_id: _status(job)
    monitor._tick(db)
    assert row["status"] == "completed" and adapter.calls == [("SABnzbd_nzo_7", True)]
    assert not job.exists() and job.parent.exists()


def test_a_tick_with_history_keeps_the_leftovers(tmp_path, monkeypatch):
    def organizer(dl, src):
        return {"status": "completed", "progress": 100.0, "dest_path": "/lib/x.mkv",
                "_cleanup_source": src}
    db, row, order, adapter = _tick_setup(tmp_path, monkeypatch, organizer, "history")
    job = tmp_path / "complete" / "soulsync" / "Some.Show.S01E01.1080p.WEB-DL"
    (job / "some.show.nfo").write_bytes(b"nfo")
    adapter.get_status = lambda job_id: _status(job)
    monitor._tick(db)
    assert adapter.calls == [("SABnzbd_nzo_7", False)] and (job / "some.show.nfo").exists()


async def _status(job):
    return _St(name=job.name, save_path=str(job), state="completed")


def test_a_tick_keeps_an_import_failed_job(tmp_path, monkeypatch):
    def organizer(dl, src):
        return {"status": "import_failed", "progress": 100.0, "dest_path": src, "error": "x"}
    db, row, order, adapter = _tick_setup(tmp_path, monkeypatch, organizer, "files")
    monitor._tick(db)
    assert row["status"] == "import_failed"
    assert adapter.calls == []


def test_a_tick_with_the_setting_off_leaves_the_client_alone(tmp_path, monkeypatch):
    def organizer(dl, src):
        return {"status": "completed", "progress": 100.0, "dest_path": "/lib/x.mkv",
                "_cleanup_source": src}
    db, row, order, adapter = _tick_setup(tmp_path, monkeypatch, organizer, None)
    monitor._tick(db)
    assert row["status"] == "completed" and adapter.calls == []


# ── the settings page ─────────────────────────────────────────────────────────

def test_the_video_setting_is_on_the_settings_page_and_saved():
    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    i = html.index('id="video-usenet-remove-completed"')
    group = html.rfind('<div class="form-group"', 0, i)
    # the Sources tab is shared: no side gating, the label says which side
    assert "data-video-only" not in html[group:i] and "data-music-only" not in html[group:i]
    assert "(video)" in html[group:i]
    for value in ('value="off"', 'value="history"', 'value="files"'):
        assert value in html[i:i + 800]
    js = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
    body = js.split("function saveDownloads(", 1)[1].split("function loadImportLists(", 1)[0]
    assert "usenet_remove_completed: val('video-usenet-remove-completed')" in body
    assert "d.usenet_remove_completed" in js
