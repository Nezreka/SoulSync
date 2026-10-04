"""#1504 part 1 — Library Reorganize must not move own-library files into the
shared folder.

Per-track ownership: ``preview_album_reorganize`` and the apply path resolve
the owning profile from each track's real on-disk path via
``owning_profile_for_path()`` and stamp it into the post-process context, so
``transfer_root_for_context()`` routes the destination into the owning
profile's own folder. Shared-library tracks keep today's behavior
(``profile_id`` None → shared transfer folder), and the preview's display
trims anchor to the owning profile's root so own-library previews don't show
raw absolute paths.

Hermetic: the db, the per-profile roots, and the shared transfer root are all
faked; the planner is stubbed so these tests exercise the routing + display
layer only.
"""

from __future__ import annotations

import os
import threading

import pytest

import core.imports.paths as paths
import core.library_reorganize as lr
from core.imports.paths import transfer_root_for_context


class _FakeDb:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_own_library_profiles(self):
        return self._profiles


@pytest.fixture
def own_libs(monkeypatch):
    """profile 2 owns /lib/u2; everything else is shared."""
    monkeypatch.setattr(
        "database.music_database.get_database",
        lambda: _FakeDb([{"id": 2, "name": "user2", "root": "/lib/u2"}]),
    )
    monkeypatch.setattr(
        paths, "library_root_for_profile",
        lambda pid, announce=True: "/lib/u2" if pid and int(pid) == 2 else None,
    )
    monkeypatch.setattr(paths, "shared_transfer_root", lambda: "/Transfer")


def _api_album():
    return {
        "id": "album123",
        "name": "Album",
        "artists": [{"name": "Artist"}],
        "release_date": "2020-01-01",
        "total_tracks": 2,
    }


def _api_track(name="Title", num=1):
    return {
        "name": name,
        "track_number": num,
        "disc_number": 1,
        "artists": [{"name": "Artist"}],
    }


# ── _build_post_process_context stamps the profile ───────────────────────────

def test_context_carries_profile_id(own_libs):
    ctx = lr._build_post_process_context(
        _api_album(), _api_track(), "Artist", "Album", 1, profile_id=2)
    assert ctx["profile_id"] == 2
    assert transfer_root_for_context(ctx) == "/lib/u2"


def test_context_without_profile_keeps_shared_routing(own_libs):
    ctx = lr._build_post_process_context(
        _api_album(), _api_track(), "Artist", "Album", 1)
    assert ctx["profile_id"] is None
    # today's behavior, byte-for-byte: the shared transfer folder
    assert transfer_root_for_context(ctx) == "/Transfer"


# ── preview: per-track routing + display anchoring ────────────────────────────

def _stub_plan(monkeypatch):
    album_data = {
        "id": "7", "title": "Album", "artist_name": "Artist", "year": 2020,
    }
    tracks = [
        {"id": "t1", "title": "Title One", "track_number": 1,
         "file_path": "/lib/u2/Artist/Old/01 - Title One.flac"},
        {"id": "t2", "title": "Title Two", "track_number": 2,
         "file_path": "/Transfer/Artist/Old/02 - Title Two.flac"},
    ]
    monkeypatch.setattr(
        lr, "load_album_and_tracks", lambda db, album_id: (album_data, tracks))
    plan = {
        "status": "planned",
        "source": "spotify",
        "total_discs": 1,
        "api_album": _api_album(),
        "record_type": "album",
        "is_compilation": False,
        "items": [
            {"track": tracks[0], "matched": True,
             "api_track": _api_track("Title One", 1), "reason": None},
            {"track": tracks[1], "matched": True,
             "api_track": _api_track("Title Two", 2), "reason": None},
        ],
    }
    monkeypatch.setattr(lr, "plan_album_reorganize", lambda *a, **k: plan)


def _recording_path_builder(recorded):
    def _build(context, artist, album_info, file_ext, create_dirs=True):
        recorded.append(dict(context))
        root = transfer_root_for_context(context)
        title = (context.get("track_info") or {}).get("name") or "Title"
        num = (context.get("track_info") or {}).get("track_number") or 1
        return os.path.join(root, "Artist", "Album",
                            f"{num:02d} - {title}{file_ext}"), True
    return _build


def test_preview_routes_own_library_track_to_own_root(own_libs, monkeypatch):
    _stub_plan(monkeypatch)
    recorded = []
    result = lr.preview_album_reorganize(
        album_id="7",
        db=object(),
        transfer_dir="/Transfer",
        resolve_file_path_fn=lambda p: p,   # db path == on-disk path
        build_final_path_fn=_recording_path_builder(recorded),
    )
    assert result["success"] is True
    by_id = {t["track_id"]: t for t in result["tracks"]}

    own = by_id["t1"]
    assert recorded[0]["profile_id"] == 2
    assert own["new_path_abs"] == \
        "/lib/u2/Artist/Album/01 - Title One.flac"
    # display columns anchor to the own root — no raw absolute paths
    assert own["new_path"] == "Artist/Album/01 - Title One.flac"
    assert own["current_path"] == "Artist/Old/01 - Title One.flac"
    assert not os.path.isabs(own["new_path"])
    assert not os.path.isabs(own["current_path"])


def test_preview_keeps_shared_track_on_shared_folder(own_libs, monkeypatch):
    _stub_plan(monkeypatch)
    recorded = []
    result = lr.preview_album_reorganize(
        album_id="7",
        db=object(),
        transfer_dir="/Transfer",
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=_recording_path_builder(recorded),
    )
    by_id = {t["track_id"]: t for t in result["tracks"]}

    shared = by_id["t2"]
    assert recorded[1]["profile_id"] is None
    assert shared["new_path_abs"] == \
        "/Transfer/Artist/Album/02 - Title Two.flac"
    assert shared["new_path"] == "Artist/Album/02 - Title Two.flac"
    assert shared["current_path"] == "Artist/Old/02 - Title Two.flac"


def test_preview_ignores_session_profile(own_libs, monkeypatch):
    """An admin reorganizing another profile's album must not have the
    admin's own session profile stamped anywhere — ownership comes from
    the file paths alone."""
    _stub_plan(monkeypatch)
    recorded = []
    lr.preview_album_reorganize(
        album_id="7",
        db=object(),
        transfer_dir="/Transfer",
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=_recording_path_builder(recorded),
    )
    assert [c["profile_id"] for c in recorded] == [2, None]


# ── _trim_to_transfer anchoring ───────────────────────────────────────────────

def test_trim_anchors_to_profile_root():
    assert lr._trim_to_transfer(
        "/db/x.flac", "/lib/u2/Artist/x.flac", "/Transfer",
        anchor_root="/lib/u2") == "Artist/x.flac"


def test_trim_without_anchor_keeps_todays_behavior():
    # the file is NOT under the transfer dir → raw DB value, as before
    assert lr._trim_to_transfer(
        "/db/x.flac", "/lib/u2/Artist/x.flac", "/Transfer") == "/db/x.flac"
    assert lr._trim_to_transfer(
        "./Transfer/A/x.flac", "/Transfer/A/x.flac", "/Transfer",
        anchor_root=None) == "A/x.flac"


# ── apply path: _run_post_process_for_track ───────────────────────────────────

def _run_ctx(tmp_path, recorded):
    def _post_process(context_key, context, staging_file):
        recorded.append(dict(context))
        context["_final_processed_path"] = staging_file

    return lr._RunContext(
        album_id="7",
        api_album=_api_album(),
        artist_name="Artist",
        album_title="Album",
        total_discs=1,
        local_year="2020",
        staging_album_dir=str(tmp_path),
        state_lock=threading.Lock(),
        summary={"moved": 0, "skipped": 0, "failed": 0, "errors": []},
        src_dirs_touched=set(),
        dst_dirs_touched=set(),
        resolve_file_path_fn=lambda p: p,
        post_process_fn=_post_process,
    )


def test_apply_path_stamps_owning_profile(own_libs, tmp_path):
    recorded = []
    ctx = _run_ctx(tmp_path, recorded)
    staged = tmp_path / "01 - Title.flac"
    staged.write_text("audio")
    new_path = lr._run_post_process_for_track(
        ctx, "t1", "Title", _api_track(), str(staged), profile_id=2)
    assert new_path == str(staged)
    assert recorded[0]["profile_id"] == 2
    assert transfer_root_for_context(recorded[0]) == "/lib/u2"


def test_apply_path_without_profile_keeps_shared_routing(own_libs, tmp_path):
    recorded = []
    ctx = _run_ctx(tmp_path, recorded)
    staged = tmp_path / "02 - Title.flac"
    staged.write_text("audio")
    lr._run_post_process_for_track(
        ctx, "t2", "Title", _api_track(), str(staged))
    assert recorded[0]["profile_id"] is None
    assert transfer_root_for_context(recorded[0]) == "/Transfer"
