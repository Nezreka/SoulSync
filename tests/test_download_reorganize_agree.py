"""A freshly downloaded album must already sit where Reorganize would put it.

Both pipelines call the SAME builder (``core.imports.paths.build_final_path_for_track``),
so the destination can only diverge through the context they feed it. It did:
Reorganize applied a single-disc cap that the download pipeline knows nothing
about, so an album whose tracks the downloader had just filed under "Disc 1/"
was immediately proposed for a move back out — and moved back in again once the
second disc arrived.

The acceptance criterion is the user's: download an album, press Reorganize,
nothing moves.
"""

from __future__ import annotations

import os

import pytest

import core.imports.paths as import_paths
import core.library_reorganize as lr
from core.imports.paths import build_final_path_for_track


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


ARTIST = "Sawano Hiroyuki"
ALBUM = "TV Anime Attack on Titan Season 2 (Original Soundtrack)"
# A genuine 2-disc release, numbered per disc.
API_TRACKS = ([{"name": "D1-%02d" % n, "track_number": n, "disc_number": 1,
                "artists": [{"name": ARTIST}]} for n in range(1, 26)]
              + [{"name": "D2-%02d" % n, "track_number": n, "disc_number": 2,
                  "artists": [{"name": ARTIST}]} for n in range(1, 21)])
API_ALBUM = {"id": "sp1", "name": ALBUM, "release_date": "2017-06-28",
             "total_tracks": len(API_TRACKS), "images": [{"url": ""}]}


@pytest.fixture()
def cfg(monkeypatch, tmp_path):
    config = _Config({
        # The shipped default spelling — relative, exactly what the settings
        # page shows and what a container install carries.
        "soulseek.transfer_path": "./Transfer",
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": "$albumartist/$album/$track - $title",
            "single_path": "$albumartist/$albumartist - $title/$title",
        },
        "file_organization.collab_artist_mode": "first",
        "file_organization.disc_label": "Disc",
    })
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    # The provider tracklist lookup the builder may make. Returning None is the
    # realistic worst case (cache miss, provider down) AND the case that used to
    # change the destination: see
    # test_the_destination_does_not_depend_on_a_provider_lookup below.
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: True)
    monkeypatch.setattr(lr, "_feat_in_title_enabled", lambda: False)
    return tmp_path


def _download_destination(track_number, disc_number, title):
    """What the download/import pipeline files a finished track as."""
    context = {
        "artist": {"name": ARTIST},
        "album": {"name": ALBUM, "id": "sp1", "release_date": "2017-06-28",
                  "total_tracks": len(API_TRACKS), "total_discs": 2,
                  "album_type": "album", "artists": [{"name": ARTIST}]},
        "track_info": {"name": title, "id": "t", "track_number": track_number,
                       "disc_number": disc_number, "artists": [{"name": ARTIST}]},
        "original_search_result": {"title": title, "clean_title": title,
                                   "clean_album": ALBUM, "clean_artist": ARTIST,
                                   "artists": [{"name": ARTIST}]},
        "source": "spotify", "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, {"name": ARTIST},
        {"is_album": True, "album_name": ALBUM,
         "track_number": track_number, "disc_number": disc_number},
        ".flac", create_dirs=False)
    return path


def _reorganize_destination(user_tracks, monkeypatch):
    """What Reorganize proposes for the same album, via the real planner."""
    album_data = {"id": "AL1", "title": ALBUM, "artist_name": ARTIST,
                  "artist_id": "AR1", "spotify_album_id": "sp1"}
    monkeypatch.setattr(
        lr, "_resolve_source",
        lambda ad, ps, strict_source=False, **kw: ("spotify", API_ALBUM, API_TRACKS))
    plan = lr.plan_album_reorganize(album_data, user_tracks, "spotify")

    out = []
    for item in plan["items"]:
        assert item["matched"], item.get("reason")
        ctx = lr._build_post_process_context(
            API_ALBUM, item["api_track"], ARTIST, ALBUM, plan["total_discs"],
            local_title=item["track"]["title"])
        path, _ = build_final_path_for_track(
            ctx, ctx["spotify_artist"], lr._build_album_info(ctx), ".flac",
            create_dirs=False)
        out.append(path)
    return out


def test_reorganize_proposes_nothing_for_a_part_downloaded_multi_disc_album(cfg, monkeypatch):
    """Three tracks of disc 1 have landed. This is the reported case."""
    downloaded = [_download_destination(n, 1, "D1-%02d" % n) for n in (1, 2, 3)]
    assert all(os.sep + "Disc 1" + os.sep in p for p in downloaded), downloaded

    user_tracks = [{"id": "T%d" % n, "title": "D1-%02d" % n, "track_number": n,
                    "file_path": downloaded[i]}
                   for i, n in enumerate((1, 2, 3))]

    assert _reorganize_destination(user_tracks, monkeypatch) == downloaded


def test_reorganize_proposes_nothing_once_both_discs_have_landed(cfg, monkeypatch):
    """And it must not flip back the other way when the album completes."""
    picks = [(1, 1, "D1-01"), (25, 1, "D1-25"), (1, 2, "D2-01"), (20, 2, "D2-20")]
    downloaded = [_download_destination(tn, dn, title) for tn, dn, title in picks]

    user_tracks = [{"id": "T%d" % i, "title": title, "track_number": tn,
                    "file_path": downloaded[i]}
                   for i, (tn, dn, title) in enumerate(picks)]

    assert _reorganize_destination(user_tracks, monkeypatch) == downloaded


def test_the_destination_is_absolute_so_the_catalogue_can_store_it(cfg):
    path = _download_destination(1, 1, "D1-01")
    assert os.path.isabs(path), path
    assert path.startswith(str(cfg / "Transfer") + os.sep)


def test_the_destination_does_not_depend_on_a_provider_lookup(cfg, monkeypatch):
    """A caller that KNOWS the disc count must be trusted.

    The builder re-derived the count from a live provider tracklist whenever the
    supplied value was <= 1, so the same track landed in `Album/01 - x.flac` or
    `Album/Disc 1/01 - x.flac` depending on whether that lookup happened to
    succeed. A cache miss or an offline provider was enough to file two tracks
    of one album in two different folders.

    "Knows" has to be explicit. Almost every context builder writes
    `.get('total_discs', 1)`, where the 1 means "nobody told me" — reading that
    as a declaration would silence the #981 lookup for playlist and wishlist
    downloads and file disc-1 tracks of a real 2-disc release flat.
    """
    def _ctx(total_discs):
        return {
            "artist": {"name": ARTIST},
            "album": {"name": ALBUM, "id": "sp1", "release_date": "2017-06-28",
                      "total_tracks": len(API_TRACKS), "total_discs": total_discs,
                      "total_discs_declared": True,
                      "album_type": "album", "artists": [{"name": ARTIST}]},
            "track_info": {"name": "D1-01", "id": "t", "track_number": 1,
                           "disc_number": 1, "artists": [{"name": ARTIST}]},
            "original_search_result": {"title": "D1-01", "clean_title": "D1-01",
                                       "clean_album": ALBUM, "clean_artist": ARTIST,
                                       "artists": [{"name": ARTIST}]},
            "source": "spotify", "is_album_download": True,
        }

    album_info = {"is_album": True, "album_name": ALBUM,
                  "track_number": 1, "disc_number": 1}

    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: API_TRACKS)
    with_lookup, _ = build_final_path_for_track(
        _ctx(1), {"name": ARTIST}, album_info, ".flac", create_dirs=False)

    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    without_lookup, _ = build_final_path_for_track(
        _ctx(1), {"name": ARTIST}, album_info, ".flac", create_dirs=False)

    assert with_lookup == without_lookup, (
        "an explicit total_discs=1 was overridden by a provider lookup"
    )
    assert os.sep + "Disc 1" + os.sep not in with_lookup


def test_an_absent_total_discs_still_asks_the_provider(cfg, monkeypatch):
    """The lookup is the fallback for callers that genuinely do not know — a
    single-track download has no album context of its own (#981)."""
    ctx = {
        "artist": {"name": ARTIST},
        "album": {"name": ALBUM, "id": "sp1", "release_date": "2017-06-28",
                  "total_tracks": len(API_TRACKS),
                  "album_type": "album", "artists": [{"name": ARTIST}]},
        "track_info": {"name": "D1-01", "id": "t", "track_number": 1,
                       "disc_number": 1, "artists": [{"name": ARTIST}]},
        "original_search_result": {"title": "D1-01", "clean_title": "D1-01",
                                   "clean_album": ALBUM, "clean_artist": ARTIST,
                                   "artists": [{"name": ARTIST}]},
        "source": "spotify", "is_album_download": True,
    }
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: API_TRACKS)
    path, _ = build_final_path_for_track(
        ctx, {"name": ARTIST},
        {"is_album": True, "album_name": ALBUM, "track_number": 1, "disc_number": 1},
        ".flac", create_dirs=False)
    assert os.sep + "Disc 1" + os.sep in path


def test_a_defaulted_total_discs_still_asks_the_provider(cfg, monkeypatch):
    """The regression guard for the above: `total_discs: 1` written by a caller
    that merely defaulted it (core/downloads/candidates.py, staging.py,
    master.py all do `.get('total_discs', 1)`) is NOT a declaration. Spotify
    album objects carry no disc count at all, so 1 there means unknown."""
    ctx = {
        "artist": {"name": ARTIST},
        "album": {"name": ALBUM, "id": "sp1", "release_date": "2017-06-28",
                  "total_tracks": len(API_TRACKS), "total_discs": 1,
                  "album_type": "album", "artists": [{"name": ARTIST}]},
        "track_info": {"name": "D1-01", "id": "t", "track_number": 1,
                       "disc_number": 1, "artists": [{"name": ARTIST}]},
        "original_search_result": {"title": "D1-01", "clean_title": "D1-01",
                                   "clean_album": ALBUM, "clean_artist": ARTIST,
                                   "artists": [{"name": ARTIST}]},
        "source": "spotify", "is_album_download": True,
    }
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: API_TRACKS)
    path, _ = build_final_path_for_track(
        ctx, {"name": ARTIST},
        {"is_album": True, "album_name": ALBUM, "track_number": 1, "disc_number": 1},
        ".flac", create_dirs=False)
    assert os.sep + "Disc 1" + os.sep in path, (
        "a defaulted 1 was read as a declaration, so the #981 lookup never ran"
    )


def test_the_reorganize_context_declares_its_disc_count():
    """Reorganize is the caller that really knows: it counted the discs off the
    source tracklist it just resolved."""
    ctx = lr._build_post_process_context(
        API_ALBUM, API_TRACKS[0], ARTIST, ALBUM, 2, local_title="D1-01")
    assert ctx["spotify_album"]["total_discs"] == 2
    assert ctx["spotify_album"]["total_discs_declared"] is True
