"""Live folder watching (Phase 3 item 3.2): the watcher half.

Path rules (temp files, album folders, Windows and UNC paths, live or
polling), SoulSync's own writes, the bounded pending set, the polling walk and
its outage guard, and the service: start, stop, events in, one rescan per
album out."""

from __future__ import annotations

import os
import threading
import time

import pytest

from core.library import folder_watch as fw
from core.library import own_writes
from core.library import folder_watch_paths as paths


@pytest.fixture(autouse=True)
def _clean_own_writes():
    own_writes.reset_for_tests()
    yield
    own_writes.reset_for_tests()


# ── temp and partial files ───────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "01 - Song.flac.part", "01 - Song.mp3.tmp", "song.flac~", ".01 - Song.flac",
    "~$song.flac", "song.flac.crdownload", "song.flac.!qB", "a.flac.1234.99.ab12cd34.sstmp",
    ".01.flac.x1y2.ssync-tmp", "song.flac.partial", "song.flac.aria2",
])
def test_temp_and_partial_files_are_ignored(name):
    assert paths.is_temp_name(name)
    assert not paths.is_audio_name(name)


@pytest.mark.parametrize("name", ["01 - Song.flac", "Song.MP3", "x.opus", "a.m4a", "b.aiff"])
def test_audio_files_are_kept(name):
    assert paths.is_audio_name(name)


def test_cover_art_and_dot_folders_are_not_the_watchers_business(tmp_path):
    root = str(tmp_path)
    assert paths.is_ignored(os.path.join(root, "A", "B", "cover.jpg"), root)
    assert paths.is_ignored(os.path.join(root, ".soulsync_atomic_staging", "1", "a.flac"), root)
    assert paths.is_ignored(os.path.join(root, "@eaDir", "a.flac"), root)
    assert paths.is_ignored(os.path.join(root, "A", ".trash", "a.flac"), root)
    assert not paths.is_ignored(os.path.join(root, "A", "B", "01.flac"), root)
    staging = os.path.join(root, "Staging")
    assert paths.is_ignored(os.path.join(staging, "x", "01.flac"), root, excluded=[staging])
    assert paths.is_ignored("/elsewhere/01.flac", root)


# ── album folders ────────────────────────────────────────────────────────


def test_a_file_belongs_to_its_album_folder():
    root = "/music"
    assert paths.album_unit("/music/Artist/Album/01.flac", root) == ("/music/Artist/Album", True)


def test_a_disc_folder_belongs_to_its_album():
    root = "/music"
    for disc in ("CD1", "Disc 2", "disk_03", "CD 1 - Bonus"):
        assert paths.album_unit(f"/music/A/Album/{disc}/01.flac", root) == ("/music/A/Album", True)
    # an album that is only called "Discovery" is not a disc folder
    assert paths.album_unit("/music/Daft Punk/Discovery/01.flac", root) == \
        ("/music/Daft Punk/Discovery", True)


def test_a_loose_file_in_the_root_reads_only_the_root():
    assert paths.album_unit("/music/loose.flac", "/music") == ("/music", False)
    assert paths.album_unit("/music", "/music", is_dir=True) is None
    assert paths.album_unit("/other/x.flac", "/music") is None


def test_collapse_keeps_one_unit_per_tree():
    units = {"/m/A": True, "/m/A/Album": True, "/m/A/Album/CD1": True, "/m/B/X": True,
             "/m": False}
    assert paths.collapse(units) == {"/m": False, "/m/A": True, "/m/B/X": True}


def test_coarsen_lifts_a_flood_to_parent_folders():
    units = {f"/m/Artist{a}/Album{b}": True for a in range(30) for b in range(10)}
    out = paths.coarsen(units, "/m", 50)
    assert len(out) <= 50
    assert set(out) == {f"/m/Artist{a}" for a in range(30)}
    assert paths.coarsen(units, "/m", 5) == {"/m": True}


# ── Windows and UNC paths ────────────────────────────────────────────────


def test_unc_and_drive_paths_are_recognised():
    assert paths.is_unc(r"\\nas\music")
    assert paths.is_unc("//nas/music")
    assert not paths.is_unc(r"\\?\C:\Music")  # a long-path prefix, not a share
    assert paths.is_windows_path(r"C:\Music")
    assert paths.is_windows_path("D:/Music")
    assert not paths.is_windows_path("/music")


def test_windows_paths_nest_case_blind_with_either_separator():
    assert paths.is_under(r"C:\Music\Artist\Album\01.flac", r"c:\music")
    assert paths.is_under("C:/Music/Artist", r"C:\Music")
    assert not paths.is_under(r"C:\Musicals\x.flac", r"C:\Music")
    assert paths.is_under(r"\\nas\share\A\B\01.flac", r"\\NAS\share")
    assert paths.album_unit(r"\\nas\share\A\B\01.flac", r"\\nas\share") == (r"\\nas\share\A\B", True)
    assert paths.album_unit(r"E:\Music\A\B\CD2\01.flac", "E:\\Music") == (r"E:\Music\A\B", True)
    assert paths.norm("E:\\") == "E:\\"
    assert paths.parent(r"E:\Music\A") == r"E:\Music"


def test_a_posix_folder_is_not_folded_into_a_longer_name():
    assert not paths.is_under("/music/kimberly/x.flac", "/music/kim")
    assert paths.is_under("/music/kim/x.flac", "/music/kim/")


# ── live or polling ──────────────────────────────────────────────────────

_MOUNTS = """\
/dev/sda1 / ext4 rw 0 0
/dev/sdb1 /music ext4 rw 0 0
//nas/music /mnt/nas cifs rw 0 0
nas:/export /mnt/nfs nfs4 rw 0 0
drvfs /mnt/e 9p rw 0 0
shfs /mnt/user fuse.shfs rw 0 0
"""


def test_mount_table_decides_live_or_polling():
    mounts = paths.parse_mounts(_MOUNTS)
    assert paths.classify("/music/A", platform="linux", mounts=mounts)[0] == paths.LIVE
    assert paths.classify("/mnt/nas/Music", platform="linux", mounts=mounts) == \
        (paths.POLLING, "network share (cifs)")
    assert paths.classify("/mnt/nfs/x", platform="linux", mounts=mounts)[0] == paths.POLLING
    assert paths.classify("/mnt/e/Music", platform="linux", mounts=mounts)[0] == paths.POLLING
    # unraid's user share is local to the box SoulSync runs on
    assert paths.classify("/mnt/user/music", platform="linux", mounts=mounts)[0] == paths.LIVE


def test_mountinfo_format_parses_too():
    text = "36 35 98:0 /mnt1 /mnt/parent rw,noatime master:1 - cifs //srv/x rw\n" \
           "37 35 98:0 / /data rw - ext4 /dev/sda rw\n"
    assert paths.parse_mounts(text) == [("/mnt/parent", "cifs"), ("/data", "ext4")]


def test_windows_network_paths_are_polled():
    assert paths.classify(r"\\nas\music", platform="win32")[0] == paths.POLLING
    remote = paths.classify(r"Z:\Music", platform="win32", drive_type=lambda d: paths.DRIVE_REMOTE)
    assert remote == (paths.POLLING, "network drive")
    assert paths.classify(r"C:\Music", platform="win32", drive_type=lambda d: 3)[0] == paths.LIVE
    # a UNC path in a Linux container config is still a share
    assert paths.classify("//nas/music", platform="linux", mounts=[])[0] == paths.POLLING


# ── SoulSync's own writes ────────────────────────────────────────────────


def test_a_noted_write_is_ours_until_it_expires(tmp_path):
    path = str(tmp_path / "A" / "01.flac")
    assert not own_writes.is_own(path)
    own_writes.note(path, ttl=60)
    assert own_writes.is_own(path)
    assert not own_writes.is_own(str(tmp_path / "A" / "02.flac"))
    assert not own_writes.is_own(path, now=time.monotonic() + 61)


def test_a_held_folder_covers_everything_in_it(tmp_path):
    folder = str(tmp_path / "Artist")
    with own_writes.holding(folder):
        assert own_writes.is_own(os.path.join(folder, "Album", "01.flac"))
    # and for the TTL after the job, while its last events arrive
    assert own_writes.is_own(os.path.join(folder, "Album", "01.flac"))
    assert not own_writes.is_own(str(tmp_path / "Other" / "01.flac"))


def test_the_set_is_bounded(monkeypatch):
    monkeypatch.setattr(own_writes, "MAX_ENTRIES", 10)
    for i in range(50):
        own_writes.note(f"/m/{i}.flac")
    assert len(own_writes._paths) <= 10


# ── pending changes ──────────────────────────────────────────────────────


def test_fifteen_files_of_one_album_are_one_rescan():
    pending = fw.PendingChanges()
    for i in range(15):
        unit = paths.album_unit(f"/m/A/B/{i:02d}.flac", "/m")
        pending.add("/m", unit[0], unit[1], now=100.0 + i * 0.1)
    assert pending.ready(now=103.0, quiet=5.0) == {}  # still busy
    ready = pending.ready(now=107.0, quiet=5.0)
    assert [folder for folder, _u in ready["/m"]] == ["/m/A/B"]
    assert pending.count() == 0


def test_a_flood_coarsens_instead_of_growing():
    pending = fw.PendingChanges(cap=40)
    for a in range(20):
        for b in range(20):
            pending.add("/m", f"/m/Artist{a}/Album{b}", True, now=1.0)
    assert pending.count("/m") <= 40
    folders = [f for f, _u in pending.ready(10.0, 5.0)["/m"]]
    # artist folders where a flood was coarsened, albums after it, never both
    assert any(f.count("/") == 2 for f in folders)
    for f in folders:
        assert not any(o != f and paths.is_under(f, o) for o in folders)
    pending.add("/m", "/m/Artist0", True, now=1.0)
    pending.add("/m", "/m/Artist0/Album3", True, now=1.0)
    assert [f for f, _u in pending.ready(10.0, 5.0)["/m"]] == ["/m/Artist0"]


def test_moves_are_bounded():
    pending = fw.PendingChanges(max_moves=3)
    for i in range(10):
        pending.add_move("/m", f"/m/a{i}", f"/m/b{i}")
    assert len(pending.take_moves("/m")) == 3


# ── polling ──────────────────────────────────────────────────────────────


def _album(root, artist, album, n=3, size=10):
    folder = root / artist / album
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"{i + 1:02d}.flac").write_bytes(b"x" * size)
    return folder


def test_the_first_walk_only_learns_the_layout(tmp_path):
    _album(tmp_path, "A", "B")
    poller = fw.DirectoryPoller(str(tmp_path))
    assert poller.poll() == []
    assert poller.poll() == []


def test_polling_finds_a_new_album_and_a_removed_one(tmp_path):
    old = _album(tmp_path, "A", "Old")
    _album(tmp_path, "A", "Keep")
    poller = fw.DirectoryPoller(str(tmp_path))
    poller.poll()
    new = _album(tmp_path, "A", "New")
    for f in old.iterdir():
        f.unlink()
    old.rmdir()
    changed = dict(poller.poll())
    assert changed[str(new)] == "new"
    assert changed[str(old)] == "gone"


def test_polling_finds_an_edit_in_place_within_its_slices(tmp_path):
    album = _album(tmp_path, "A", "B")
    poller = fw.DirectoryPoller(str(tmp_path), slices=1)  # every folder every round
    poller.poll()
    poller.poll()  # a round with file stats to compare against
    target = album / "01.flac"
    st = target.stat()
    target.write_bytes(b"y" * 10)   # same size, same name: a retag
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    os.utime(album, ns=(album.stat().st_atime_ns, album.stat().st_mtime_ns))
    assert (str(album), "changed") in poller.poll()


def test_a_vanished_share_is_an_outage_not_a_mass_delete(tmp_path):
    for a in range(30):
        _album(tmp_path, f"Artist{a}", "Album", n=1)
    poller = fw.DirectoryPoller(str(tmp_path))
    poller.poll()
    import shutil
    for a in range(30):
        shutil.rmtree(tmp_path / f"Artist{a}")
    assert poller.poll() is None
    # and nothing was learned from it: the layout is still the old one
    assert len(poller.state) == 61


def test_a_truncated_walk_does_not_report_the_rest_gone(tmp_path):
    for a in range(5):
        _album(tmp_path, f"Artist{a}", "Album", n=1)
    poller = fw.DirectoryPoller(str(tmp_path), max_folders=100)
    poller.poll()
    poller.max_folders = 3
    changed = poller.poll()
    assert changed is not None and not [c for c in changed if c[1] == "gone"]


# ── the service ──────────────────────────────────────────────────────────


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeWatch:
    def __init__(self, handler, path):
        self.handler, self.path = handler, path


class _FakeObserver:
    instances = []

    def __init__(self):
        self.watches = []
        self.started = self.stopped = self.joined = False
        self.daemon = False
        _FakeObserver.instances.append(self)

    def start(self):
        self.started = True

    def schedule(self, handler, path, recursive=False):
        watch = _FakeWatch(handler, path)
        self.watches.append(watch)
        return watch

    def unschedule(self, watch):
        self.watches.remove(watch)

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


def _service(tmp_path, *, values=None, processed=None, **kwargs):
    music = tmp_path / "music"
    staging = tmp_path / "staging"
    music.mkdir(exist_ok=True)
    staging.mkdir(exist_ok=True)
    (music / "keep.txt").write_text("x")  # populated: a reachable library folder
    (staging / "keep.txt").write_text("x")
    config = {"soulseek.transfer_path": str(music), "import.staging_path": str(staging),
              "soulseek.download_path": "", "library.music_paths": [],
              "library.watch_folders": True}
    config.update(values or {})
    calls = processed if processed is not None else []

    def _process(root, units, moves, *, excluded=()):
        calls.append((root, sorted(units), list(moves)))
        return {"folders": len(units)}

    kwargs.setdefault("platform", "linux")
    kwargs.setdefault("mounts_reader", lambda: [])
    kwargs.setdefault("inotify_limit", lambda: 100_000)
    kwargs.setdefault("observer_factory", _FakeObserver)
    kwargs.setdefault("use_watchdog", True)
    service = fw.FolderWatchService(config_manager=_Config(config), get_database=None,
                                    process_library=_process, quiet=5.0, stable=0.0, **kwargs)
    return service, str(music), str(staging), calls


def _watch_for(service, root):
    return service.roots[root].watch


def test_roots_are_found_and_each_says_how_it_is_watched(tmp_path):
    service, music, staging, _ = _service(tmp_path)
    service.refresh(0.0)
    status = service.status()
    by_path = {r["path"]: r for r in status["roots"]}
    assert by_path[music]["mode"] == "live" and by_path[music]["kind"] == "library"
    assert by_path[staging]["kind"] == "import"
    assert status["watching"] == 2


def test_an_album_copied_in_is_one_rescan_after_it_settles(tmp_path):
    service, music, _staging, calls = _service(tmp_path)
    service.refresh(0.0)
    service.enabled = True
    handler = _watch_for(service, music).handler
    album = os.path.join(music, "Artist", "Album")
    os.makedirs(album)
    for i in range(15):
        path = os.path.join(album, f"{i:02d}.flac")
        open(path, "wb").write(b"x")
        handler.dispatch("created", path)
        handler.dispatch("modified", path)
    service._next_refresh = 10_000
    service.tick(time.monotonic())
    assert calls == []  # not quiet yet
    service.tick(time.monotonic() + 6)
    assert calls == [(music, [(album, True)], [])]


def test_temp_files_and_own_writes_never_start_a_rescan(tmp_path):
    service, music, _staging, calls = _service(tmp_path)
    service.refresh(0.0)
    handler = _watch_for(service, music).handler
    album = os.path.join(music, "A", "B")
    os.makedirs(album)
    assert not handler.dispatch("created", os.path.join(album, "01.flac.part"))
    assert not handler.dispatch("created", os.path.join(album, ".01.flac.abc.ssync-tmp"))
    assert not handler.dispatch("created", os.path.join(album, "cover.jpg"))
    ours = os.path.join(album, "02.flac")
    own_writes.note(ours)
    assert not handler.dispatch("created", ours)
    service._next_refresh = 10_000
    service.tick(time.monotonic() + 60)
    assert calls == []


def test_a_file_still_growing_waits(tmp_path):
    service, music, _staging, calls = _service(tmp_path)
    service.stable = 30.0  # anything written in the last 30 s is still being copied
    service.refresh(0.0)
    handler = _watch_for(service, music).handler
    album = os.path.join(music, "A", "B")
    os.makedirs(album)
    path = os.path.join(album, "01.flac")
    open(path, "wb").write(b"x")
    handler.dispatch("created", path)
    service._next_refresh = 10_000
    service.tick(time.monotonic() + 6)
    assert calls == [] and service.pending.count() == 1
    old = time.time() - 120
    os.utime(path, (old, old))
    service.tick(time.monotonic() + 12)
    assert calls == []  # it changed since the last look
    service.tick(time.monotonic() + 18)
    assert calls == [(music, [(album, True)], [])]


def test_a_rename_reaches_the_rescan_as_a_move(tmp_path):
    service, music, _staging, calls = _service(tmp_path)
    service.refresh(0.0)
    handler = _watch_for(service, music).handler
    os.makedirs(os.path.join(music, "A", "Old"))
    os.makedirs(os.path.join(music, "A", "New"))
    src = os.path.join(music, "A", "Old", "01.flac")
    dst = os.path.join(music, "A", "New", "01.flac")
    open(dst, "wb").write(b"x")
    handler.dispatch("moved", src, dest=dst)
    service._next_refresh = 10_000
    service.tick(time.monotonic() + 6)
    assert calls == [(music, sorted([(os.path.join(music, "A", "Old"), True),
                                     (os.path.join(music, "A", "New"), True)]), [(src, dst)])]


def test_a_vanished_library_folder_is_an_outage(tmp_path):
    service, music, _staging, calls = _service(tmp_path)
    service.refresh(0.0)
    handler = _watch_for(service, music).handler
    album = os.path.join(music, "A", "B")
    os.makedirs(album)
    handler.dispatch("deleted", os.path.join(album, "01.flac"))
    import shutil
    shutil.rmtree(music)
    os.makedirs(music)  # an empty mount point, the share behind it gone
    service._next_refresh = 10_000
    service.tick(time.monotonic() + 6)
    assert calls == []
    root = service.status()["roots"]
    assert any(r["path"] == music and not r["reachable"] for r in root)


def test_the_import_folder_wakes_the_auto_import(tmp_path):
    woke = []
    service, _music, staging, calls = _service(tmp_path, trigger_import=lambda: woke.append(1))
    service.refresh(0.0)
    handler = _watch_for(service, staging).handler
    handler.dispatch("created", os.path.join(staging, "Album", "01.flac"))
    service._next_refresh = 10_000
    now = time.monotonic()
    service.tick(now + 6)
    assert woke == [1] and calls == []
    service.tick(now + 12)  # the second look the auto-import needs
    assert woke == [1, 1]


def test_the_import_folder_inside_the_library_is_not_a_library_change(tmp_path):
    music = tmp_path / "music"
    service, music_s, _staging, calls = _service(
        tmp_path, values={"import.staging_path": str(music / "Staging")})
    (music / "Staging").mkdir()
    (music / "Staging" / "keep.txt").write_text("x")
    service.refresh(0.0)
    handler = _watch_for(service, music_s).handler
    assert not handler.dispatch("created", str(music / "Staging" / "A" / "01.flac"))


def test_inotify_limit_falls_back_to_polling_with_a_note(tmp_path):
    service, music, _staging, _ = _service(tmp_path, inotify_limit=lambda: 5)
    for a in range(10):
        os.makedirs(os.path.join(music, f"A{a}", "B"))
    service.refresh(0.0)
    state = service.roots[music]
    assert state.mode == "polling" and "fs.inotify.max_user_watches" in state.note
    assert state.watch is None and state.poller is not None


def test_running_out_of_watches_while_scheduling_falls_back_to_polling(tmp_path):
    import errno

    class _Full(_FakeObserver):
        def schedule(self, handler, path, recursive=False):
            raise OSError(errno.ENOSPC, "inotify watch limit reached")

    service, music, _staging, _ = _service(tmp_path, observer_factory=_Full)
    service.refresh(0.0)
    assert service.roots[music].mode == "polling"
    assert "ran out of folder watches" in service.roots[music].note


def test_a_network_share_is_polled(tmp_path):
    service, music, _staging, _ = _service(
        tmp_path, mounts_reader=lambda: [(str(tmp_path), "cifs")])
    service.refresh(0.0)
    status = {r["path"]: r for r in service.status()["roots"]}
    assert status[music]["mode"] == "polling"
    assert status[music]["reason"] == "network share (cifs)"
    assert status[music]["poll_minutes"] == 15


def test_polling_end_to_end_finds_a_new_album(tmp_path):
    service, music, _staging, calls = _service(
        tmp_path, mounts_reader=lambda: [(str(tmp_path), "nfs4")])
    service.refresh(0.0)
    service._next_refresh = 10_000
    now = time.monotonic()
    service.tick(now)  # the first walk
    album = os.path.join(music, "Artist", "Album")
    os.makedirs(album)
    path = os.path.join(album, "01.flac")
    open(path, "wb").write(b"x")
    old = time.time() - 120
    os.utime(path, (old, old))
    service.roots[music].next_poll = 0
    service.tick(now + 1)
    service.tick(now + 7)    # first look at the folder
    service.tick(now + 13)   # second look: unchanged, so it settled
    # the new artist folder with everything in it, and the root's loose files
    assert calls and calls[-1][1] == [(music, False), (os.path.join(music, "Artist"), True)]


def test_per_folder_settings_and_the_global_switch(tmp_path):
    service, music, staging, _ = _service(
        tmp_path, values={"library.watch_root_modes": {str(tmp_path / "music"): "off"}})
    service.refresh(0.0)
    assert service.roots[music].mode == "off"
    assert service.status()["watching"] == 1
    service.config_manager.values["library.watch_folders"] = False
    service.refresh(1.0)
    assert service.roots == {} and not service.enabled


def test_watchdog_missing_means_polling_with_a_note(tmp_path):
    service, music, _staging, _ = _service(tmp_path, use_watchdog=False)
    service.refresh(0.0)
    assert service.roots[music].mode == "polling"
    assert "watchdog" in service.roots[music].note


def test_start_and_stop_leave_no_thread_behind(tmp_path):
    service, music, _staging, _ = _service(tmp_path)
    service.tick_seconds = 0.01
    before = {t.ident for t in threading.enumerate()}
    service.start()
    deadline = time.time() + 5
    while not service.roots and time.time() < deadline:
        time.sleep(0.01)
    assert service.running and service.roots
    observer = service._observer
    service.stop()
    assert not service.running
    assert observer.stopped and observer.joined
    leftover = [t for t in threading.enumerate()
                if t.ident not in before and t.name == "FolderWatch"]
    assert leftover == []


def test_real_watchdog_sees_a_file_land(tmp_path):
    """the real backend, when it is installed: an album copied in reaches
    the rescan once"""
    pytest.importorskip("watchdog")
    base = tmp_path
    mounts = paths.parse_mounts(open("/proc/mounts").read()) if os.path.exists("/proc/mounts") else []
    if paths.classify(str(tmp_path), platform="linux", mounts=mounts)[0] != paths.LIVE:
        # a host folder bridge (WSL's /mnt/e) passes no notifications: that is
        # exactly why such folders are polled. use a RAM folder for this one
        if not os.path.isdir("/dev/shm"):
            pytest.skip("no local folder to watch live")
        import tempfile
        base = __import__("pathlib").Path(tempfile.mkdtemp(dir="/dev/shm"))
    service, music, _staging, calls = _service(base, observer_factory=None)
    service.quiet = 0.3
    service.refresh(0.0)
    service.enabled = True
    service._next_refresh = float("inf")
    try:
        album = os.path.join(music, "Artist", "Album")
        os.makedirs(album)
        for i in range(5):
            with open(os.path.join(album, f"{i:02d}.flac"), "wb") as handle:
                handle.write(b"x")
        old = time.time() - 120
        for i in range(5):
            os.utime(os.path.join(album, f"{i:02d}.flac"), (old, old))
        deadline = time.time() + 10
        while not calls and time.time() < deadline:
            time.sleep(0.2)
            service.tick(time.monotonic())
        assert len(calls) == 1
        assert calls[0][1] in ([(album, True)], [(os.path.join(music, "Artist"), True)])
    finally:
        service.stop()
        if base != tmp_path:
            import shutil
            shutil.rmtree(base, ignore_errors=True)


def test_watching_is_off_until_someone_turns_it_on(tmp_path):
    """3.6.9 ships folder watching off by default: a config without the key
    watches nothing."""
    service, _music, _staging, _calls = _service(tmp_path)
    del service.config_manager.values["library.watch_folders"]
    service.refresh()
    assert not service.status().get("enabled")
