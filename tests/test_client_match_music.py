"""music match & import: a download soulsync didn't send, followed to the library.

the watcher waits for the client to finish, copies the audio out so the
original keeps seeding, imports the copy with the import page's own import,
and reports on the downloads page. hermetic: a temp sqlite file, temp folders,
a fake client status and a stub import.
"""

from __future__ import annotations

import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

import core.client_match as cm
from core.runtime_state import download_batches, download_tasks, tasks_lock


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "music.db")
    conn = sqlite3.connect(path)
    cm.ensure_schema(conn.cursor())
    conn.commit()
    conn.close()
    return cm.MusicMatchStore(lambda: sqlite3.connect(path))


@pytest.fixture(autouse=True)
def clean_cards():
    yield
    with tasks_lock:
        for key in [k for k in download_tasks if str(k).startswith("client-match-")]:
            download_tasks.pop(key, None)
        download_batches.pop(cm.MATCH_BATCH_ID, None)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    download = tmp_path / "torrent" / "Radiohead - In Rainbows (2007) [FLAC]"
    (download / "CD1").mkdir(parents=True)
    (download / "CD1" / "01 15 Step.flac").write_bytes(b"a" * 50)
    (download / "02 Bodysnatchers.flac").write_bytes(b"b" * 40)
    (download / "info.nfo").write_text("not audio")
    copies = tmp_path / "downloads" / ".soulsync-matches"
    staging = tmp_path / "Staging"
    staging.mkdir()
    monkeypatch.setattr(cm, "_copy_root", lambda: str(copies))
    import core.imports.staging as staging_mod
    monkeypatch.setattr(staging_mod, "get_staging_path", lambda: str(staging))
    return SimpleNamespace(download=download, copies=copies, staging=staging)


ALBUM = {"id": "alb1", "name": "In Rainbows", "artist": "Radiohead", "source": "deezer"}


def _add(store, **kw):
    args = dict(client="torrent", client_ref="HASH1", kind="album", match=ALBUM,
                release_title="Radiohead - In Rainbows (2007) [FLAC]")
    args.update(kw)
    match_id = store.add(**args)
    row = [r for r in store.active() if r["id"] == match_id][0]
    cm.card_register(row)
    return row


def _status(state="seeding", progress=1.0, path=None):
    return SimpleNamespace(state=state, progress=progress, size=90, downloaded=90,
                           download_speed=0, content_path=path, save_path=None)


def _card(match_id):
    with tasks_lock:
        return dict(download_tasks[f"client-match-{match_id}"])


# ── the store ────────────────────────────────────────────────────────────────

def test_a_match_is_kept_and_followed(store):
    row = _add(store)
    assert row["status"] == "waiting"
    assert json.loads(row["match_json"])["name"] == "In Rainbows"
    # clients report info-hashes in either case
    assert store.following("torrent", "hash1")
    assert not store.following("usenet", "HASH1")


def test_the_card_sits_on_the_downloads_page_away_from_the_music_engine(store):
    row = _add(store)
    with tasks_lock:
        batch = dict(download_batches[cm.MATCH_BATCH_ID])
    assert batch["is_music"] is False and batch["managed_externally"] is True
    assert _card(row["id"])["track_info"]["artist"] == "Radiohead"


# ── a pass of the watcher ────────────────────────────────────────────────────

def test_still_downloading_waits_and_shows_progress(store, folders):
    row = _add(store)
    out = cm.process_match(row, store, get_status=lambda c, r: (_status("downloading", 0.4), True),
                           resolve=lambda p: p, runtime_factory=lambda r: None)
    assert out == "waiting"
    assert _card(row["id"])["progress"] == pytest.approx(40.0)


def test_a_finished_download_is_copied_imported_and_keeps_seeding(store, folders, monkeypatch):
    row = _add(store)
    seen = {}

    def fake_import(kind, match, folder, files, runtime):
        seen.update(kind=kind, match=match, folder=folder, files=sorted(files))
        for path in files:    # the import moves what it takes
            os.remove(path)
        return {"ok": True, "imported": len(files), "error": ""}

    monkeypatch.setattr(cm, "import_copy", fake_import)
    out = cm.process_match(row, store, get_status=lambda c, r: (_status(path=str(folders.download)), True),
                           resolve=lambda p: p, runtime_factory=lambda r: None)
    assert out == "completed"
    # audio only, layout kept, from a private copy
    assert {os.path.relpath(f, seen["folder"]) for f in seen["files"]} == {
        os.path.join("CD1", "01 15 Step.flac"), "02 Bodysnatchers.flac"}
    assert seen["folder"].startswith(str(folders.copies))
    # the client's files are untouched, so the torrent keeps seeding
    assert (folders.download / "CD1" / "01 15 Step.flac").exists()
    # the private copy is cleaned up
    assert not os.path.exists(seen["folder"])
    assert store.active() == []
    assert _card(row["id"])["status"] == "completed"


def test_a_failed_import_fails_the_match_and_leaves_the_files_on_the_import_page(store, folders, monkeypatch):
    row = _add(store)
    monkeypatch.setattr(cm, "import_copy",
                        lambda *a: {"ok": False, "imported": 0, "error": "None of the files matched."})
    out = cm.process_match(row, store, get_status=lambda c, r: (_status(path=str(folders.download)), True),
                           resolve=lambda p: p, runtime_factory=lambda r: None)
    assert out == "failed"
    [failed] = [r for r in store.recent() if r["id"] == row["id"]]
    assert failed["status"] == "failed"
    assert "None of the files matched." in failed["error"]
    assert "2 files left on the import page" in failed["error"]
    left = sorted(os.path.relpath(os.path.join(r, f), folders.staging)
                  for r, _d, fs in os.walk(folders.staging) for f in fs)
    assert len(left) == 2 and all(p.endswith(".flac") for p in left)
    assert _card(row["id"])["status"] == "failed"


def test_finished_but_not_visible_keeps_waiting(store, folders):
    row = _add(store)
    out = cm.process_match(row, store, get_status=lambda c, r: (_status(path="/elsewhere/x"), True),
                           resolve=lambda p: p, runtime_factory=lambda r: None)
    assert out == "waiting"
    assert "Waiting to see the files" in _card(row["id"])["error_message"]


def test_a_download_the_client_lost_fails_after_a_few_passes(store, folders):
    row = _add(store)
    misses = {}
    statuses = [cm.process_match(row, store, get_status=lambda c, r: (None, True),
                                 misses=misses, resolve=lambda p: p) for _ in range(6)]
    assert statuses[:5] == ["waiting"] * 5 and statuses[5] == "failed"


def test_a_client_that_is_down_never_counts_as_lost(store, folders):
    row = _add(store)
    misses = {}
    for _ in range(10):
        assert cm.process_match(row, store, get_status=lambda c, r: (None, False),
                                misses=misses, resolve=lambda p: p) == "waiting"


def test_cancelling_the_card_cancels_the_match(store, folders):
    row = _add(store)
    with tasks_lock:
        download_tasks[f"client-match-{row['id']}"]["cancel_requested"] = True
    assert cm.process_match(row, store, get_status=lambda c, r: (_status(), True)) == "cancelled"
    assert store.active() == []


# ── importing the copy ──────────────────────────────────────────────────────

def test_an_album_imports_only_the_files_that_matched(monkeypatch, tmp_path):
    calls = {}

    def fake_payload(album_id, **kw):
        calls["payload"] = (album_id, kw)
        return {"success": True, "album": {"id": album_id}, "source": "deezer",
                "matches": [{"track": {"id": "t1"}, "staging_file": {"full_path": "a"}},
                            {"track": {"id": "t2"}, "staging_file": None}]}

    def fake_process(runtime, data):
        calls["process"] = data
        return {"success": True, "processed": 1}, 200

    import core.imports.album as album_mod
    import core.imports.routes as routes_mod
    monkeypatch.setattr(album_mod, "build_album_import_match_payload", fake_payload)
    monkeypatch.setattr(routes_mod, "album_process", fake_process)
    out = cm.import_copy("album", ALBUM, str(tmp_path), ["a", "b"], runtime=None)
    assert out == {"ok": True, "imported": 1, "error": ""}
    # matched from the private folder, not the import folder
    assert calls["payload"][1]["root"] == str(tmp_path)
    assert [m["track"]["id"] for m in calls["process"]["matches"]] == ["t1"]


def test_an_album_with_no_matching_files_is_a_failure(monkeypatch, tmp_path):
    import core.imports.album as album_mod
    monkeypatch.setattr(album_mod, "build_album_import_match_payload", lambda *a, **k: {
        "success": True, "album": {}, "matches": [{"track": {}, "staging_file": None}]})
    out = cm.import_copy("album", ALBUM, str(tmp_path), ["a"], runtime=None)
    assert out["ok"] is False and "matched" in out["error"]


def test_a_track_imports_the_largest_audio_file(monkeypatch, tmp_path):
    small, big = tmp_path / "intro.flac", tmp_path / "song.flac"
    small.write_bytes(b"x")
    big.write_bytes(b"x" * 100)
    seen = {}

    def fake_single(runtime, info):
        seen.update(info)
        return ("ok", "")

    import core.imports.routes as routes_mod
    monkeypatch.setattr(routes_mod, "process_single_import_file", fake_single)
    match = {"id": "trk9", "source": "spotify", "name": "Airbag"}
    out = cm.import_copy("track", match, str(tmp_path), [str(small), str(big)], runtime=None)
    assert out["ok"] is True
    assert seen["full_path"] == str(big)
    assert seen["manual_match"] == {"id": "trk9", "source": "spotify"}


def test_staging_files_can_come_from_another_folder(tmp_path, monkeypatch):
    import core.imports.staging as staging_mod
    other = tmp_path / "private"
    other.mkdir()
    (other / "a.flac").write_bytes(b"x")
    monkeypatch.setattr(staging_mod, "get_staging_path", lambda: str(tmp_path / "Staging"))
    monkeypatch.setattr(staging_mod, "read_staging_file_metadata", lambda p, n: {})
    found = staging_mod.collect_staging_files(root=str(other))
    assert [f["filename"] for f in found] == ["a.flac"]
    assert staging_mod.collect_staging_files() == []


# ── labels and guesses ──────────────────────────────────────────────────────

def test_a_followed_match_labels_its_client_row(store):
    _add(store)
    known = cm.music_match_known(store.recent())
    assert known["torrent"]["hash1"] == {"kind": "album", "title": "Radiohead - In Rainbows"}


@pytest.mark.parametrize("name, kind, query", [
    ("Halloween.Baking.Championship.S12E04.1080p.WEB.h264-EDITH", "episode", "Halloween Baking Championship"),
    ("Ted Lasso S04 1080p WEB H264-CAKES", "season", "Ted Lasso"),
    ("Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1-FLUX", "movie", "Dune Part Two"),
    ("Radiohead - In Rainbows (2007) [FLAC 24-96]", "album", "Radiohead In Rainbows"),
    ("Andy.Weir.-.Project.Hail.Mary.2021.M4B-GRP", "audiobook", "Andy Weir Project Hail Mary"),
    ("ubuntu-24.04-desktop-amd64.iso", None, None),
])
def test_the_release_name_gives_a_first_guess(name, kind, query):
    guess = cm.suggest_from_name(name)
    assert guess["kind"] == kind
    if query:
        assert guess["query"] == query
