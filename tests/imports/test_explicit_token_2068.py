"""#2068 (QT3496): $explicit in a naming template came out empty on downloads
and on imports.

The token worked; nothing gave it a value. Download tasks carried an album
context with no explicit flag, so $explicit in an album FOLDER had nothing to
read. Auto-import and the manual import both rebuilt the track and album
without the flag, so there the filename had nothing either.
"""

from __future__ import annotations

from core.imports.album import build_album_import_context
from core.imports.paths import (
    album_explicit_flag, apply_content_rating, resolve_album_explicit,
)


def test_an_album_is_explicit_when_any_track_is():
    tracks = [{"explicit": False}, {"explicit": True}, {"explicit": False}]
    assert album_explicit_flag({}, tracks) is True


def test_an_album_is_clean_only_when_every_track_is_known_clean():
    assert album_explicit_flag({}, [{"explicit": False}, {"explicit": 0}]) is False
    assert album_explicit_flag({}, [{"explicit": False}, {}]) is None
    assert album_explicit_flag({}, []) is None
    assert album_explicit_flag(None, None) is None


def test_the_albums_own_word_wins_over_its_tracks():
    assert album_explicit_flag({"explicit": False}, [{"explicit": True}]) is False
    assert album_explicit_flag({"explicit": "explicit"}, [{"explicit": False}]) is True


def test_the_path_reads_the_album_context_then_the_batch_stamp():
    assert resolve_album_explicit({"explicit": True}, {}) is True
    assert resolve_album_explicit({}, {"_explicit_album_context": {"explicit": True}}) is True
    assert resolve_album_explicit({"name": "x"}, {"_album_explicit": True}) is True
    assert resolve_album_explicit(None, None) is None


def test_a_clean_track_on_an_explicit_album_stays_in_the_albums_folder():
    # the folder never reads the track's own flag: an interlude with no
    # swearing would otherwise be filed in a second folder
    track_info = {"explicit": False, "_album_explicit": True}
    album_flag = resolve_album_explicit({}, track_info)
    path = apply_content_rating("$albumartist/$album $explicit/$title $explicit",
                                track_info["explicit"], album_flag)
    assert path == "$albumartist/$album [E]/$title "
    # and with nothing known about the album, the folder says nothing
    assert resolve_album_explicit({}, {"explicit": True}) is None


def test_a_manual_import_keeps_the_flags():
    album = {"id": "a1", "name": "Album", "artists": [{"name": "Artist"}], "explicit": True}
    context = build_album_import_context(
        album, {"id": "t1", "name": "Song", "track_number": 1, "explicit": True}, source="deezer")
    assert context["track_info"]["explicit"] is True
    assert context["album"]["explicit"] is True
    clean = build_album_import_context(
        album, {"id": "t2", "name": "Interlude", "track_number": 2, "explicit": False},
        source="deezer")
    assert clean["track_info"]["explicit"] is False and clean["album"]["explicit"] is True


def test_the_download_and_import_paths_carry_the_flag():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent / "core"
    master = (root / "downloads" / "master.py").read_text(encoding="utf-8")
    assert "track_info['_album_explicit'] = batch_album_explicit" in master
    candidates = (root / "downloads" / "candidates.py").read_text(encoding="utf-8")
    assert "else track_info.get('_album_explicit')" in candidates
    auto = (root / "auto_import_worker.py").read_text(encoding="utf-8")
    assert "'explicit': album_explicit," in auto and "'explicit': track.get('explicit')," in auto
    routes = (root / "imports" / "routes.py").read_text(encoding="utf-8")
    assert routes.count("album_explicit_flag(") >= 2
