"""#1299: two releases sharing a title land in their own folders.

musicbrainz releases 55c7242f (the original) and 3b98979b ("baby punk version")
of кис-кис's "юность в стиле панк" share title, artist and release group. the
only thing telling them apart is the disambiguation, and without it in the
folder both imports landed in one directory with every shared song twice.
"""

from __future__ import annotations

import core.imports.paths as import_paths

ALBUM = "юность в стиле панк"
ARTIST = "кис-кис"
ORIGINAL = "55c7242f-1b4b-485d-b5f2-d6a8feeee088"
BABY_PUNK = "3b98979b-6494-4a7c-8de6-2165902f8a87"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _config(tmp_path, album_path="$albumartist/$albumartist - $album/$track - $title", **extra):
    return _Config({
        "soulseek.transfer_path": str(tmp_path / "Transfer"),
        "file_organization.enabled": True,
        "file_organization.templates": {"album_path": album_path, **extra},
        "file_organization.collab_artist_mode": "first",
        "file_organization.disc_label": "Disc",
    })


def _context(release_id, disambiguation=""):
    album = {
        "name": ALBUM, "id": release_id, "musicbrainz_release_id": release_id,
        "release_date": "2019-03-22", "total_tracks": 9, "total_discs": 1,
        "album_type": "album", "artists": [{"name": ARTIST}],
    }
    if disambiguation:
        album["disambiguation"] = disambiguation
    return {
        "artist": {"name": ARTIST},
        "album": album,
        "track_info": {"name": "рэпер", "id": "r1", "track_number": 1,
                       "disc_number": 1, "artists": [{"name": ARTIST}]},
        "original_search_result": {"title": "рэпер", "clean_title": "рэпер",
                                   "clean_album": ALBUM, "clean_artist": ARTIST,
                                   "artists": [{"name": ARTIST}]},
        "source": "musicbrainz", "is_album_download": True,
    }


def _album_info():
    return {"is_album": True, "album_name": ALBUM, "track_number": 1, "disc_number": 1}


def _no_reuse(monkeypatch, seen=None):
    """stand in for the folder-reuse lookup so no real db is touched."""
    import core.library.existing_album_folder as eaf
    import database.music_database as mdb

    def fake_resolve(**kwargs):
        if seen is not None:
            seen.append(kwargs)
        return None

    monkeypatch.setattr(eaf, "resolve_existing_album_folder", fake_resolve)
    monkeypatch.setattr(mdb, "get_database", lambda: object())


# ── the template rewrite ─────────────────────────────────────────────────────

def test_suffix_follows_album_in_the_folder_part():
    got = import_paths.with_disambiguation(
        "$albumartist/$albumartist - $album/$track - $title", "baby punk version", ALBUM)
    assert got == "$albumartist/$albumartist - $album ($disambiguation)/$track - $title"


def test_no_disambiguation_leaves_template_untouched():
    template = "$albumartist/$album/$track - $title"
    assert import_paths.with_disambiguation(template, "", ALBUM) is template
    assert import_paths.with_disambiguation(template, "   ", ALBUM) is template


def test_template_placing_it_itself_is_left_alone():
    for template in ("$albumartist/$album [$disambiguation]/$track - $title",
                     "$albumartist/$album ${disambiguation}/$track - $title"):
        assert import_paths.with_disambiguation(template, "baby punk version", ALBUM) == template


def test_album_name_already_carrying_it_gets_no_second_copy():
    template = "$albumartist/$album/$track - $title"
    assert import_paths.with_disambiguation(template, "deluxe", "Album (Deluxe)") == template


def test_albumartist_and_albumtype_are_not_album():
    template = "$albumartist/$albumtype/$track - $title"
    assert import_paths.with_disambiguation(template, "baby punk version", ALBUM) == template


def test_bracket_album_token_counts():
    got = import_paths.with_disambiguation("$artist/${album}/$title", "x", ALBUM)
    assert got == "$artist/${album} ($disambiguation)/$title"


def test_album_only_in_filename_is_not_touched():
    template = "$artist/$track - $album - $title"
    assert import_paths.with_disambiguation(template, "x", ALBUM) == template


# ── rendered paths ───────────────────────────────────────────────────────────

def test_rendered_folder_carries_the_disambiguation(monkeypatch, tmp_path):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _config(tmp_path))
    folder, filename = import_paths.get_file_path_from_template({
        "artist": ARTIST, "albumartist": ARTIST, "album": ALBUM, "title": "рэпер",
        "track_number": 1, "disc_number": 1, "disambiguation": "baby punk version",
    })
    assert folder == f"{ARTIST}/{ARTIST} - {ALBUM} (baby punk version)"
    assert filename == "01 - рэпер"


def test_disambiguation_variable_renders_where_the_user_put_it(monkeypatch, tmp_path):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _config(
        tmp_path, album_path="$albumartist/$album [$disambiguation]/$track - $title"))
    ctx = {"artist": ARTIST, "albumartist": ARTIST, "album": ALBUM, "title": "t",
           "track_number": 2, "disc_number": 1}
    folder, _ = import_paths.get_file_path_from_template({**ctx, "disambiguation": "baby punk version"})
    assert folder == f"{ARTIST}/{ALBUM} [baby punk version]"
    # empty on a plain release, and the empty brackets go with it
    folder, _ = import_paths.get_file_path_from_template(ctx)
    assert folder == f"{ARTIST}/{ALBUM}"


def test_raw_template_path_gets_the_same_suffix(monkeypatch):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))
    folder, _ = import_paths.get_file_path_from_template_raw(
        "$albumartist/$album/$track - $title",
        {"artist": ARTIST, "albumartist": ARTIST, "album": ALBUM, "title": "t",
         "track_number": 1, "disc_number": 1, "disambiguation": "baby punk version"})
    assert folder == f"{ARTIST}/{ALBUM} (baby punk version)"


def test_the_two_releases_get_different_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    _no_reuse(monkeypatch)

    original, _ = import_paths.build_final_path_for_track(
        _context(ORIGINAL), {"name": ARTIST}, _album_info(), ".flac", create_dirs=False)
    baby_punk, _ = import_paths.build_final_path_for_track(
        _context(BABY_PUNK, "baby punk version"), {"name": ARTIST}, _album_info(), ".flac",
        create_dirs=False)

    artist_dir = tmp_path / "Transfer" / ARTIST
    assert original == str(artist_dir / f"{ARTIST} - {ALBUM}" / "01 - рэпер.flac")
    assert baby_punk == str(artist_dir / f"{ARTIST} - {ALBUM} (baby punk version)" / "01 - рэпер.flac")


def test_folder_reuse_is_asked_about_this_release(monkeypatch, tmp_path):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    (tmp_path / "Transfer").mkdir()
    seen = []
    _no_reuse(monkeypatch, seen)

    import_paths.build_final_path_for_track(
        _context(BABY_PUNK, "baby punk version"), {"name": ARTIST}, _album_info(), ".flac",
        create_dirs=False)

    assert seen and seen[0]["musicbrainz_release_id"] == BABY_PUNK
    assert seen[0]["disambiguation"] == "baby punk version"


def test_no_template_fallback_also_splits(monkeypatch, tmp_path):
    config = _config(tmp_path)
    config._values["file_organization.enabled"] = False
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    _no_reuse(monkeypatch)

    path, _ = import_paths.build_final_path_for_track(
        _context(BABY_PUNK, "baby punk version"), {"name": ARTIST}, _album_info(), ".flac",
        create_dirs=False)
    assert f"{ARTIST} - {ALBUM} (baby punk version)" in path


def test_web_server_template_engine_knows_the_variable():
    # the m3u folder still renders through web_server's copy of the engine; an
    # unknown $disambiguation there would come out as literal text
    import web_server
    ctx = {"artist": ARTIST, "albumartist": ARTIST, "album": ALBUM, "title": "t",
           "track_number": 1, "disc_number": 1}
    out = web_server._apply_path_template("$albumartist/$album [$disambiguation]/$title",
                                          {**ctx, "disambiguation": "baby punk version"})
    assert out == f"{ARTIST}/{ALBUM} [baby punk version]/t"
    out = web_server._apply_path_template("$albumartist/$album [${disambiguation}]/$title", ctx)
    assert "disambiguation" not in out


def test_album_name_check_matches_whole_words_only():
    template = "$albumartist/$album/$track - $title"
    # "live" is not in "Alive", so the live release still gets its suffix
    assert import_paths.with_disambiguation(template, "live", "Alive") == \
        "$albumartist/$album ($disambiguation)/$track - $title"
    assert import_paths.album_name_carries("Alive (Live)", "live")
    assert import_paths.album_name_carries("Album [Baby Punk Version]", "baby punk version")
    assert not import_paths.album_name_carries("Album", "")
