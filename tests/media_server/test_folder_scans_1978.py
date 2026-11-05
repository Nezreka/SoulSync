"""After downloads, scan only the folders that changed (#1978).

SoulSync asked the media server for a whole-library scan after every
download run. Plex and Jellyfin can scan one folder, but need it as THEY see
it, and a wrong folder means a new album that silently never shows up. So
the translation is learned from files the catalogue knows in both forms, and
anything uncertain falls back to the whole-library scan.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.web_scan_manager as wsm
from core.library2 import scan_paths as sp


# ── learning where the server sees SoulSync's folders ──────────────────────

def _pairs(n=5, local="H:\\Music", server="/mnt/musicBackup"):
    return [(f"{local}\\Artist {i}\\Album\\01 - Song.flac",
             f"{server}/Artist {i}/Album/01 - Song.flac") for i in range(n)]


def test_a_windows_library_on_a_linux_plex_is_learned():
    roots = sp.learn_roots(_pairs())
    assert sp.to_server_folder("H:\\Music\\New Artist\\New Album", roots) == \
        "/mnt/musicBackup/New Artist/New Album"
    assert sp.to_server_folder("h:/music/New Artist/New Album", roots) == \
        "/mnt/musicBackup/New Artist/New Album"


def test_a_few_odd_files_are_not_a_root():
    pairs = _pairs() + [("D:\\Other\\Artist\\Album\\x.flac", "/elsewhere/Artist/Album/x.flac")]
    roots = sp.learn_roots(pairs)
    assert sp.to_server_folder("D:\\Other\\Artist\\Album", roots) is None


def test_a_folder_no_root_covers_has_no_server_path():
    assert sp.to_server_folder("E:\\Downloads\\Album", sp.learn_roots(_pairs())) is None


def test_a_unc_server_path_keeps_its_form():
    pairs = [(f"/music/A{i}/B/c.flac", f"\\\\nas\\music\\A{i}\\B\\c.flac") for i in range(4)]
    assert sp.to_server_folder("/music/New/Album", sp.learn_roots(pairs)) == \
        "\\\\nas\\music\\New\\Album"


def test_a_folder_inside_a_library_location():
    assert sp.location_for("/mnt/musicBackup/A/B", ["/mnt/musicBackup"]) == "/mnt/musicBackup"
    assert sp.location_for("/mnt/musicBackup", ["/mnt/musicBackup"]) is None
    assert sp.location_for("/mnt/other/A", ["/mnt/musicBackup"]) is None


# ── the plan, against a real catalogue ──────────────────────────────────────

@pytest.fixture()
def conn(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    from tests.lib2_seed import row_conn, track
    c = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(c)
    for i in range(5):
        tid = track(c, f"Artist {i}", "Album", "Song", path=f"H:\\Music\\Artist {i}\\Album\\01.flac")
        c.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source,"
                  " server_id, server_path) VALUES('track', ?, 'plex', ?, ?)",
                  (tid, str(tid), f"/mnt/musicBackup/Artist {i}/Album/01.flac"))
    c.execute("UPDATE lib2_track_files SET added_at='2026-01-01 00:00:00',"
              " updated_at='2026-01-01 00:00:00'")
    c.commit()
    yield c
    c.close()


def _new_album(conn, artist, album, n=2):
    from tests.lib2_seed import track
    for k in range(n):
        track(conn, artist, album, f"Song {k}", path=f"H:\\Music\\{artist}\\{album}\\{k}.flac")
    conn.commit()


def test_the_new_albums_are_planned_as_plex_sees_them(conn):
    _new_album(conn, "New Artist", "First")
    _new_album(conn, "New Artist", "Second")
    plan = sp.plan_folder_scan(conn, "plex", "2026-06-01 00:00:00", ["/mnt/musicBackup"])
    assert sorted(plan) == ["/mnt/musicBackup/New Artist/First", "/mnt/musicBackup/New Artist/Second"]


def test_nothing_changed_is_a_whole_library_scan(conn):
    assert sp.plan_folder_scan(conn, "plex", "2026-06-01 00:00:00", ["/mnt/musicBackup"]) is None


def test_too_many_folders_is_a_whole_library_scan(conn, monkeypatch):
    monkeypatch.setattr(sp, "MAX_FOLDERS", 2)
    for a in ("One", "Two", "Three"):
        _new_album(conn, a, "Album", n=1)
    assert sp.plan_folder_scan(conn, "plex", "2026-06-01 00:00:00", ["/mnt/musicBackup"]) is None


def test_a_folder_outside_the_servers_libraries_is_a_whole_library_scan(conn):
    _new_album(conn, "New Artist", "First")
    assert sp.plan_folder_scan(conn, "plex", "2026-06-01 00:00:00", ["/mnt/elsewhere"]) is None


def test_nothing_learned_tries_the_folder_as_it_is(conn):
    # a server that sees SoulSync's own paths has no mapping rows to learn from
    from tests.lib2_seed import track
    conn.execute("DELETE FROM lib2_media_server_mappings")
    track(conn, "Same", "Box", "S", path="/music/Same/Box/1.flac")
    conn.commit()
    assert sp.plan_folder_scan(conn, "plex", "2026-06-01 00:00:00", ["/music"]) == ["/music/Same/Box"]


# ── the scan manager: folders for downloads, everything otherwise ──────────

class _Client:
    def __init__(self, folder_ok=True):
        self.folder_ok = folder_ok
        self.calls = []

    def trigger_folder_scan(self, folders):
        self.calls.append(("folders", list(folders)))
        return self.folder_ok

    def trigger_library_scan(self):
        self.calls.append(("full",))
        return True


@pytest.fixture()
def manager(monkeypatch):
    client = _Client()
    engine = SimpleNamespace(client=lambda name: client)
    mgr = wsm.WebScanManager(media_server_engine=engine, delay_seconds=60)
    monkeypatch.setattr(mgr, "_get_active_media_client", lambda: (client, "plex"))
    monkeypatch.setattr(mgr, "_start_periodic_completion_check", lambda: None)
    monkeypatch.setattr(mgr, "_folder_scan_plan",
                        lambda c, t, since: ["/mnt/musicBackup/New/Album"])

    class _Timer:
        def __init__(self, delay, fn):
            self.fn = fn
            self.daemon = True

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(wsm.threading, "Timer", _Timer)
    mgr.client = client

    def run(scope):
        mgr.request_scan("test", scope=scope)
        mgr._timer.fn()
        mgr._reset_scan_state()

    mgr.run = run
    return mgr


def test_the_first_scan_after_a_restart_is_the_whole_library(manager):
    # downloads from before the restart may never have been scanned
    manager.run("downloads")
    assert manager.client.calls == [("full",)]


def test_downloads_then_scan_only_their_folders(manager):
    manager.run("downloads")
    manager.run("downloads")
    assert manager.client.calls[-1] == ("folders", ["/mnt/musicBackup/New/Album"])
    assert manager.last_scan_kind == "folders"


def test_a_manual_request_in_the_window_scans_everything(manager):
    manager.run("downloads")
    manager.request_scan("downloads", scope="downloads")
    manager.request_scan("manual", scope="full")
    manager._timer.fn()
    assert manager.client.calls[-1] == ("full",)


def test_a_refused_folder_scan_falls_back_to_the_whole_library(manager):
    manager.client.folder_ok = False
    manager.run("downloads")
    manager.run("downloads")
    assert manager.client.calls[-2:] == [("folders", ["/mnt/musicBackup/New/Album"]), ("full",)]
    assert manager.last_scan_kind == "full"


# ── plex: each folder goes to the section that holds it ────────────────────

def test_plex_scans_each_folder_in_its_own_section_or_nothing():
    from core.plex_client import PlexClient

    class _Section:
        def __init__(self, title, locations):
            self.title, self.locations, self.updated = title, locations, []

        def update(self, path=None):
            self.updated.append(path)

    music, more = _Section("Music", ["/mnt/musicBackup"]), _Section("More", ["/mnt/more"])
    client = PlexClient.__new__(PlexClient)
    client.music_library = music
    client.ensure_connection = lambda: True
    client._get_music_sections = lambda: [music, more]

    assert client.trigger_folder_scan(["/mnt/more/A/B", "/mnt/musicBackup/C/D"]) is True
    assert more.updated == ["/mnt/more/A/B"] and music.updated == ["/mnt/musicBackup/C/D"]

    assert client.trigger_folder_scan(["/mnt/musicBackup/E/F", "/nowhere/G"]) is False
    assert music.updated == ["/mnt/musicBackup/C/D"], "nothing triggered when one folder has no section"
