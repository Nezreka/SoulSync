"""#1299: reorganize keeps a same-named release in its own folder.

the import puts the release's disambiguation on the album folder. reorganize
rebuilds every destination from a metadata source, and only musicbrainz knows
the disambiguation, so without carrying it reorganize would fold
"album (baby punk version)" straight back into "album", on top of the other
release. the acceptance check is the user's: import, press reorganize, nothing
moves.
"""

from __future__ import annotations

import pytest

import core.imports.paths as import_paths
import core.library_reorganize as lr
from core.imports.paths import build_final_path_for_track

ARTIST = "кис-кис"
ALBUM = "юность в стиле панк"
BABY_PUNK = "3b98979b-6494-4a7c-8de6-2165902f8a87"
API_TRACKS = [{"name": t, "track_number": n, "disc_number": 1, "artists": [{"name": ARTIST}]}
              for n, t in enumerate(["рэпер", "тиндер", "teen love"], start=1)]


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "soulsync"


@pytest.fixture()
def cfg(monkeypatch, tmp_path):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path / "Transfer"),
        "file_organization.enabled": True,
        "file_organization.templates": {"album_path": "$albumartist/$album/$track - $title"},
        "file_organization.collab_artist_mode": "first",
        "file_organization.disc_label": "Disc",
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: True)
    monkeypatch.setattr(lr, "_feat_in_title_enabled", lambda: False)
    return tmp_path


def _user_tracks():
    return [{"id": f"T{n}", "title": t["name"], "track_number": n, "duration": 0,
             "file_path": f"/music/{n}.flac"}
            for n, t in enumerate(API_TRACKS, start=1)]


def _download_destination(track):
    context = {
        "artist": {"name": ARTIST},
        "album": {"name": ALBUM, "id": BABY_PUNK, "musicbrainz_release_id": BABY_PUNK,
                  "disambiguation": "baby punk version", "release_date": "2019-03-29",
                  "total_tracks": 10, "total_discs": 1, "album_type": "album",
                  "artists": [{"name": ARTIST}]},
        "track_info": {"name": track["name"], "id": "t", "track_number": track["track_number"],
                       "disc_number": 1, "artists": [{"name": ARTIST}]},
        "original_search_result": {"title": track["name"], "clean_title": track["name"],
                                   "clean_album": ALBUM, "clean_artist": ARTIST,
                                   "artists": [{"name": ARTIST}]},
        "source": "musicbrainz", "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, {"name": ARTIST},
        {"is_album": True, "album_name": ALBUM, "track_number": track["track_number"],
         "disc_number": 1},
        ".flac", create_dirs=False)
    return path


def _reorganize_destinations(monkeypatch, source, api_album, identity):
    monkeypatch.setattr(
        lr, "_resolve_source",
        lambda ad, ps, strict_source=False, **kw: (source, api_album, API_TRACKS))
    monkeypatch.setattr(lr, "read_release_identity", lambda path: identity)
    album_data = {"id": "AL1", "title": ALBUM, "artist_name": ARTIST, "artist_id": "AR1",
                  "musicbrainz_release_id": BABY_PUNK}
    plan = lr.plan_album_reorganize(album_data, _user_tracks(), source,
                                    resolve_file_path_fn=lambda p: p)
    out = []
    for item in plan["items"]:
        assert item["matched"], item.get("reason")
        ctx = lr._build_post_process_context(
            plan["api_album"], item["api_track"], ARTIST, ALBUM, plan["total_discs"],
            local_title=item["track"]["title"])
        path, _ = build_final_path_for_track(
            ctx, ctx["spotify_artist"], lr._build_album_info(ctx), ".flac", create_dirs=False)
        out.append(path)
    return out


def test_musicbrainz_source_keeps_the_folder(cfg, monkeypatch):
    api_album = {"id": BABY_PUNK, "name": ALBUM, "release_date": "2019-03-29",
                 "total_tracks": 10, "disambiguation": "baby punk version"}
    got = _reorganize_destinations(monkeypatch, "musicbrainz", api_album, ("", ""))
    assert got == [_download_destination(t) for t in API_TRACKS]
    assert all("(baby punk version)" in p for p in got)


def test_other_source_reads_it_off_the_files(cfg, monkeypatch):
    # spotify knows nothing about musicbrainz editions; the files' album comment does
    api_album = {"id": "sp1", "name": ALBUM, "release_date": "2019-03-29", "total_tracks": 10}
    got = _reorganize_destinations(monkeypatch, "spotify", api_album,
                                   (BABY_PUNK, "baby punk version"))
    assert got == [_download_destination(t) for t in API_TRACKS]


def test_tag_mode_reads_it_off_the_files(cfg, monkeypatch):
    monkeypatch.setattr(lr, "read_release_identity", lambda path: (BABY_PUNK, "baby punk version"))
    plan = {"status": "planned", "source": "tags", "api_album": {"name": ALBUM},
            "items": [{"api_album": {"name": ALBUM}}, {"api_album": None}]}
    got = lr._with_release_disambiguation(plan, _user_tracks(), lambda p: p)
    assert got["api_album"]["disambiguation"] == "baby punk version"
    assert got["items"][0]["api_album"]["disambiguation"] == "baby punk version"
    assert got["items"][1]["api_album"] is None


def test_plain_release_stays_plain(cfg, monkeypatch):
    api_album = {"id": "sp1", "name": ALBUM, "release_date": "2019-03-22", "total_tracks": 9}
    got = _reorganize_destinations(monkeypatch, "spotify", api_album, ("55c7242f", ""))
    assert all(f"/{ALBUM}/" in p.replace("\\", "/") for p in got)


def test_a_musicbrainz_plain_release_does_not_read_files(cfg, monkeypatch):
    def boom(path):
        raise AssertionError("musicbrainz already said there's no disambiguation")
    monkeypatch.setattr(lr, "read_release_identity", boom)
    plan = {"status": "planned", "source": "musicbrainz",
            "api_album": {"id": "x", "name": ALBUM, "disambiguation": ""}, "items": []}
    got = lr._with_release_disambiguation(plan, _user_tracks(), lambda p: p)
    assert got["api_album"]["disambiguation"] == ""


def test_unplanned_results_pass_through(cfg):
    plan = {"status": "no_source_id", "source": None, "api_album": None, "items": []}
    assert lr._with_release_disambiguation(plan, _user_tracks(), lambda p: p) is plan
