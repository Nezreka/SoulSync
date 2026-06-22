"""Custom file naming for organize-by-playlist folders.

The materialized playlist folder used to be stuck with the library filename.
A user can now opt into a flat filename template (e.g. "$position - $artist -
$title"). It's a FILENAME, not a path — validated so it can't make folders or
broken names, and it falls back to the library filename on anything invalid.
"""

from __future__ import annotations

import os

import pytest

from core.playlists.item_naming import (
    render_playlist_item_name,
    validate_playlist_item_template,
)
from core.playlists.materialize import rebuild_playlist_folder


# ── validation ──────────────────────────────────────────────────────────────

def test_empty_template_is_valid_means_off():
    assert validate_playlist_item_template("") == (True, "")
    assert validate_playlist_item_template("   ") == (True, "")
    assert validate_playlist_item_template(None) == (True, "")


def test_slash_is_rejected_no_folder_structure():
    ok, why = validate_playlist_item_template("$artist/$title")
    assert ok is False and "separator" in why
    ok, why = validate_playlist_item_template("$artist\\$title")
    assert ok is False


def test_must_contain_title():
    ok, why = validate_playlist_item_template("$position - $artist")
    assert ok is False and "$title" in why


def test_valid_flat_template_passes():
    assert validate_playlist_item_template("$position - $artist - $title") == (True, "")


# ── rendering ───────────────────────────────────────────────────────────────

def test_renders_tokens_and_keeps_extension():
    out = render_playlist_item_name(
        "$position - $artist - $title",
        title="One More Time", artist="Daft Punk", position="01", ext=".flac",
        fallback_name="x.flac")
    assert out == "01 - Daft Punk - One More Time.flac"


def test_track_is_zero_padded_album_is_optional():
    out = render_playlist_item_name(
        "$track - $title", title="Genesis", track=5, ext=".mp3", fallback_name="x.mp3")
    assert out == "05 - Genesis.mp3"


def test_invalid_template_falls_back_to_library_name():
    # slash / missing-title / empty all fall back — never a broken name
    for bad in ("$artist/$title", "$artist - $position", ""):
        assert render_playlist_item_name(
            bad, title="T", artist="A", ext=".flac", fallback_name="orig.flac") == "orig.flac"


def test_garbage_title_still_yields_a_safe_name_not_broken():
    # a title made of separators is sanitized to a safe (ugly) name with the
    # extension intact — never a broken name and never a path.
    out = render_playlist_item_name(
        "$title", title="/////", ext=".flac", fallback_name="orig.flac")
    assert out.endswith(".flac") and "/" not in out and "\\" not in out


def test_rendered_name_can_never_contain_a_separator():
    out = render_playlist_item_name(
        "$artist - $title", title="AC/DC Song", artist="AC/DC", ext=".flac", fallback_name="x.flac")
    assert "/" not in out and "\\" not in out


# ── end-to-end through the real folder builder ──────────────────────────────

def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\x00")


def test_rebuild_uses_dest_names_when_given(tmp_path):
    lib = tmp_path / "lib"
    a = str(lib / "Artist A" / "05 - Song A.flac")
    b = str(lib / "Artist B" / "02 - Song B.flac")
    _touch(a); _touch(b)
    root = str(tmp_path / "Playlists")
    summary = rebuild_playlist_folder(
        root, "My Mix", [a, b], "copy",
        dest_names=["01 - Artist A - Song A.flac", "02 - Artist B - Song B.flac"])
    got = sorted(os.listdir(summary.playlist_dir))
    assert got == ["01 - Artist A - Song A.flac", "02 - Artist B - Song B.flac"]


def test_rebuild_without_dest_names_keeps_basename(tmp_path):
    # back-compat: default behavior unchanged
    a = str(tmp_path / "lib" / "05 - Song A.flac")
    _touch(a)
    summary = rebuild_playlist_folder(str(tmp_path / "PL"), "Mix", [a], "copy")
    assert os.listdir(summary.playlist_dir) == ["05 - Song A.flac"]


def test_rebuild_disambiguates_colliding_dest_names(tmp_path):
    # two different sources, same templated name (e.g. template "$title" + dup title)
    a = str(tmp_path / "lib" / "a" / "x.flac")
    b = str(tmp_path / "lib" / "b" / "y.flac")
    _touch(a); _touch(b)
    summary = rebuild_playlist_folder(
        str(tmp_path / "PL"), "Mix", [a, b], "copy",
        dest_names=["Song.flac", "Song.flac"])
    got = sorted(os.listdir(summary.playlist_dir))
    assert got == ["Song (2).flac", "Song.flac"]
