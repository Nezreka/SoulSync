import os

import core.imports.album_naming as album_naming
import core.imports.paths as import_paths


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def test_sanitize_filename_replaces_illegal_characters():
    assert import_paths.sanitize_filename("AC/DC: Song?") == "AC_DC_ Song_"
    assert import_paths.sanitize_filename("AUX.txt").startswith("_")


# ── #767: dry-run path build must not create folders ──────────────────────

def _album_path_config(tmp_path):
    return _Config({
        "soulseek.transfer_path": str(tmp_path / "Transfer"),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": "$albumartist/$albumartist - $album/$track - $title",
            "single_path": "$artist/$artist - $title",
        },
        "file_organization.collab_artist_mode": "first",
        "file_organization.disc_label": "Disc",
    })


def _album_context():
    return {
        "artist": {"name": "Lenka"},
        "album": {"name": "Lenka", "id": "album-1", "release_date": "2008-01-01",
                  "total_tracks": 12, "album_type": "album", "artists": [{"name": "Lenka"}]},
        "track_info": {"name": "The Show", "id": "t1", "track_number": 1,
                       "disc_number": 1, "artists": [{"name": "Lenka"}]},
        "original_search_result": {"title": "The Show", "clean_title": "The Show",
                                   "clean_album": "Lenka", "clean_artist": "Lenka",
                                   "artists": [{"name": "Lenka"}]},
        "source": "deezer", "is_album_download": False,
    }


def test_create_dirs_false_does_not_create_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _album_path_config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    final_path, created = import_paths.build_final_path_for_track(
        _album_context(), {"name": "Lenka"},
        {"is_album": True, "album_name": "Lenka", "track_number": 1, "disc_number": 1},
        ".flac", create_dirs=False,
    )

    # Path is still computed correctly...
    assert created is True
    assert final_path == str(tmp_path / "Transfer" / "Lenka" / "Lenka - Lenka" / "01 - The Show.flac")
    # ...but NOTHING was written to disk — not even the Transfer root.
    assert not (tmp_path / "Transfer").exists()


def test_itunes_single_albumartist_falls_back_to_track_artist(monkeypatch, tmp_path):
    """#989: an iTunes single's collection carries a placeholder 'Unknown Artist'
    album artist while the track artist is real. The album_path must use the real
    artist for $albumartist, not bury the file under 'Unknown Artist'. FAILS pre-fix
    (the placeholder album-context artist overrode the real track artist)."""
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _album_path_config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    ctx = {
        "source": "itunes",
        "artist": {"name": "Forevert", "id": "123456"},
        "album": {"name": "CHAOSRIFT", "id": "999", "album_type": "single", "total_tracks": 1,
                  "artists": [{"name": "Unknown Artist"}]},   # placeholder collection artist
        "track_info": {"name": "CHAOSRIFT", "track_number": 1, "disc_number": 1,
                       "artists": [{"name": "Forevert"}]},
        "original_search_result": {"title": "CHAOSRIFT", "clean_title": "CHAOSRIFT",
                                   "artists": [{"name": "Forevert"}]},
    }
    info = {"is_album": True, "album_name": "CHAOSRIFT", "track_number": 1, "disc_number": 1}
    final_path, _ = import_paths.build_final_path_for_track(
        ctx, {"name": "Forevert", "id": "123456"}, info, ".flac", create_dirs=False)
    assert final_path == str(tmp_path / "Transfer" / "Forevert" / "Forevert - CHAOSRIFT" / "01 - CHAOSRIFT.flac")
    assert "Unknown Artist" not in final_path


def test_compilation_uses_compilation_path_template(monkeypatch, tmp_path):
    """Compilation albums route through the compilation_path template so all tracks
    land in a single Compilations/<album>/ folder instead of being split by artist."""
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _album_path_config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    ctx = {
        "source": "spotify",
        "artist": {"name": "Guest Singer"},
        "album": {"name": "Big Comp", "id": "c1", "album_type": "compilation", "total_tracks": 20,
                  "artists": [{"name": "Various Artists"}]},
        "track_info": {"name": "A Song", "track_number": 3, "disc_number": 1,
                       "artists": [{"name": "Guest Singer"}]},
        "original_search_result": {"title": "A Song", "clean_title": "A Song",
                                   "artists": [{"name": "Guest Singer"}]},
    }
    info = {"is_album": True, "album_name": "Big Comp", "track_number": 3, "disc_number": 1}
    final_path, _ = import_paths.build_final_path_for_track(
        ctx, {"name": "Guest Singer"}, info, ".flac", create_dirs=False)
    # compilation_path default: Compilations/$album/$track - $artist - $title
    assert "Compilations" in final_path
    assert "Big Comp" in final_path
    assert "Guest Singer" in final_path
    assert "Various Artists" not in final_path


def test_create_dirs_true_still_creates_folders(monkeypatch, tmp_path):
    # The download/import flow must keep working (default behavior unchanged).
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _album_path_config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    final_path, created = import_paths.build_final_path_for_track(
        _album_context(), {"name": "Lenka"},
        {"is_album": True, "album_name": "Lenka", "track_number": 1, "disc_number": 1},
        ".flac",  # create_dirs defaults True
    )

    assert created is True
    assert (tmp_path / "Transfer" / "Lenka" / "Lenka - Lenka").is_dir()


def test_create_dirs_false_and_true_yield_identical_path(monkeypatch, tmp_path):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _album_path_config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    dry, _ = import_paths.build_final_path_for_track(
        _album_context(), {"name": "Lenka"},
        {"is_album": True, "album_name": "Lenka", "track_number": 1, "disc_number": 1},
        ".flac", create_dirs=False,
    )
    live, _ = import_paths.build_final_path_for_track(
        _album_context(), {"name": "Lenka"},
        {"is_album": True, "album_name": "Lenka", "track_number": 1, "disc_number": 1},
        ".flac", create_dirs=True,
    )
    assert dry == live


def test_build_simple_download_destination_uses_album_folder(monkeypatch, tmp_path):
    config = _Config({"soulseek.transfer_path": str(tmp_path / "Transfer")})
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)

    destination, album_name, filename = import_paths.build_simple_download_destination(
        {
            "search_result": {
                "filename": "Album Folder/source.flac",
                "album": "Album Folder",
            }
        },
        str(tmp_path / "source.flac"),
    )

    assert destination == tmp_path / "Transfer" / "Album Folder" / "source.flac"
    assert album_name == "Album Folder"
    assert filename == "source.flac"
    assert destination.parent.exists()


def test_build_simple_download_destination_falls_back_to_transfer_root(monkeypatch, tmp_path):
    config = _Config({"soulseek.transfer_path": str(tmp_path / "Transfer")})
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)

    destination, album_name, filename = import_paths.build_simple_download_destination(
        {
            "search_result": {
                "filename": "source.flac",
                "album": "Unknown Album",
            }
        },
        str(tmp_path / "source.flac"),
    )

    assert destination == tmp_path / "Transfer" / "source.flac"
    assert album_name == ""
    assert filename == "source.flac"
    assert destination.parent.exists()


def test_get_file_path_from_template_raw_handles_quality_and_disc_placeholders(monkeypatch):
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))

    folder_path, filename = import_paths.get_file_path_from_template_raw(
        "$artist/$album/$discnum - $title [$quality]",
        {
            "artist": "Artist One",
            "album": "Album One",
            "title": "Song One",
            "quality": "FLAC 16bit",
            "disc_number": 3,
        },
    )

    assert folder_path == os.path.join("Artist One", "Album One")
    assert filename == "3 - Song One [FLAC 16bit]"


def test_disc_placeholder_drops_on_single_disc_album(monkeypatch):
    """#981: $disc must NOT stamp '01-' on every track of a single-disc album
    (reported on 'The Best of Snoop Dogg (2005)'). Empty like $cdnum; the
    leading-dash cleanup removes the orphaned separator."""
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))
    folder_path, filename = import_paths.get_file_path_from_template_raw(
        "$artist/$album/$disc-$track - $title",
        {"artist": "Snoop Dogg", "album": "The Best of Snoop Dogg (2005)",
         "title": "Bitch Please", "track_number": 7, "disc_number": 1, "total_discs": 1},
    )
    assert filename == "07 - Bitch Please"          # not "01-07 - Bitch Please"


def test_disc_placeholder_kept_on_multi_disc_album(monkeypatch):
    """The disc prefix stays for a genuine multi-disc album."""
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))
    folder_path, filename = import_paths.get_file_path_from_template_raw(
        "$artist/$album/$disc-$track - $title",
        {"artist": "A", "album": "B", "title": "Song",
         "track_number": 7, "disc_number": 2, "total_discs": 2},
    )
    assert filename == "02-07 - Song"


def test_disc_placeholder_kept_when_track_is_on_disc_two_without_total(monkeypatch):
    """A track known to be on disc 2 shows its disc even if total_discs wasn't
    populated — disc_number > 1 is itself proof of a multi-disc release."""
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))
    folder_path, filename = import_paths.get_file_path_from_template_raw(
        "$artist/$album/$disc-$track - $title",
        {"artist": "A", "album": "B", "title": "Song", "track_number": 3, "disc_number": 2},
    )
    assert filename == "02-03 - Song"


def test_get_file_path_from_template_raw_substitutes_cdnum_for_multi_disc(monkeypatch):
    """$cdnum should expand to 'CDxx' on multi-disc albums (regression).

    Reported by user: filenames had literal '$cdnum' instead of 'CD02'
    because `_replace_template_variables` did not handle the placeholder.
    """
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))

    folder_path, filename = import_paths.get_file_path_from_template_raw(
        "$artist/$album/$cdnum - $track - $title",
        {
            "artist": "Artist",
            "album": "Album",
            "title": "Song",
            "track_number": 5,
            "disc_number": 2,
            "total_discs": 2,
        },
    )

    assert folder_path == os.path.join("Artist", "Album")
    assert filename == "CD02 - 05 - Song"


def test_get_file_path_from_template_raw_collapses_cdnum_for_single_disc(monkeypatch):
    """$cdnum should expand to '' on single-disc albums so it disappears cleanly."""
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))

    folder_path, filename = import_paths.get_file_path_from_template_raw(
        "$artist/$album/$cdnum - $track - $title",
        {
            "artist": "Artist",
            "album": "Album",
            "title": "Song",
            "track_number": 5,
            "disc_number": 1,
            "total_discs": 1,
        },
    )

    # No "CD01" prefix; trailing-dash regex collapses the empty placeholder.
    assert folder_path == os.path.join("Artist", "Album")
    assert filename == "05 - Song"


def test_get_file_path_from_template_raw_strips_cdnum_from_folders(monkeypatch):
    """Even if user puts $cdnum inside a folder segment, it gets stripped (defensive)."""
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config({}))

    folder_path, filename = import_paths.get_file_path_from_template_raw(
        "$artist/$album/$cdnum/$track - $title",
        {
            "artist": "Artist",
            "album": "Album",
            "title": "Song",
            "track_number": 5,
            "disc_number": 1,
            "total_discs": 1,
        },
    )

    # Folder containing only $cdnum (which expands to empty for single-disc) gets dropped.
    assert folder_path == os.path.join("Artist", "Album")
    assert filename == "05 - Song"


def test_resolve_album_group_upgrades_standard_to_deluxe():
    album_naming.clear_album_grouping_cache()

    artist_context = {"name": "Cache Artist"}
    standard_album = {"album_name": "Cache Album"}
    deluxe_album = {"album_name": "Cache Album (Deluxe Edition)"}

    assert import_paths.resolve_album_group(artist_context, standard_album) == "Cache Album"
    assert import_paths.resolve_album_group(artist_context, deluxe_album) == "Cache Album (Deluxe Edition)"
    assert import_paths.resolve_album_group(artist_context, standard_album) == "Cache Album (Deluxe Edition)"


def test_build_final_path_for_track_uses_template_and_disc_folder(monkeypatch, tmp_path):
    config = _Config(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$albumartist - $album/$track - $title",
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)

    calls = []

    def fake_get_album_tracks_for_source(source, album_id):
        calls.append((source, album_id))
        return {
            "items": [
                {"disc_number": 1},
                {"disc_number": 2},
            ]
        }

    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", fake_get_album_tracks_for_source)

    context = {
        "artist": {"name": "Artist One"},
        "album": {
            "name": "Album One",
            "id": "album-1",
            "release_date": "2026-01-01",
            "total_tracks": 12,
            "album_type": "album",
            "artists": [{"name": "Artist One"}],
        },
        "track_info": {
            "name": "Song One",
            "id": "track-1",
            "track_number": 4,
            "disc_number": 1,
            "artists": [{"name": "Artist One"}],
        },
        "original_search_result": {
            "title": "Song One",
            "clean_title": "Song One",
            "clean_album": "Album One",
            "clean_artist": "Artist One",
            "artists": [{"name": "Artist One"}],
        },
        "source": "deezer",
        "is_album_download": False,
    }

    final_path, created = import_paths.build_final_path_for_track(
        context,
        {"name": "Artist One"},
        {
            "is_album": True,
            "album_name": "Album One",
            "track_number": 4,
            "disc_number": 1,
        },
        ".flac",
    )

    assert created is True
    assert calls == [("deezer", "album-1")]
    assert final_path == str(
        tmp_path / "Transfer" / "Artist One" / "Artist One - Album One" / "Disc 1" / "04 - Song One.flac"
    )
    assert (tmp_path / "Transfer" / "Artist One" / "Artist One - Album One" / "Disc 1").is_dir()


def test_build_final_path_for_track_uses_track_disc_number_without_provider_lookup(monkeypatch, tmp_path):
    config = _Config(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$albumartist - $album/$track - $title",
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)

    calls = []
    monkeypatch.setattr(
        import_paths,
        "_get_album_tracks_for_source",
        lambda source, album_id: calls.append((source, album_id)) or None,
    )

    context = {
        "artist": {"name": "Artist One"},
        "album": {
            "name": "Album One",
            "id": "album-1",
            "release_date": "2026-01-01",
            "total_tracks": 12,
            "album_type": "album",
            "artists": [{"name": "Artist One"}],
        },
        "track_info": {
            "name": "Song Two",
            "id": "track-2",
            "track_number": 4,
            "disc_number": 2,
            "artists": [{"name": "Artist One"}],
        },
        "original_search_result": {
            "title": "Song Two",
            "clean_title": "Song Two",
            "clean_album": "Album One",
            "clean_artist": "Artist One",
            "artists": [{"name": "Artist One"}],
        },
        "source": "deezer",
        "is_album_download": False,
    }

    final_path, created = import_paths.build_final_path_for_track(
        context,
        {"name": "Artist One"},
        {
            "is_album": True,
            "album_name": "Album One",
            "track_number": 4,
            "disc_number": 2,
        },
        ".flac",
    )

    assert created is True
    assert calls == []
    assert final_path == str(
        tmp_path / "Transfer" / "Artist One" / "Artist One - Album One" / "Disc 2" / "04 - Song Two.flac"
    )


def test_build_final_path_for_track_with_cdnum_template_skips_disc_folder(monkeypatch, tmp_path):
    """When the user template encodes the disc via $cdnum, the auto disc folder must not be added.

    Reported by user: multi-disc albums got both a "CDxx" label in the
    filename AND a redundant "Disc N" folder. The auto-folder should
    suppress when the template already encodes the disc.
    """
    config = _Config(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$albumartist - $album/$cdnum - $track - $title",
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(
        import_paths,
        "_get_album_tracks_for_source",
        lambda source, album_id: None,
    )

    context = {
        "artist": {"name": "Artist One"},
        "album": {
            "name": "Album One",
            "id": "album-1",
            "release_date": "2026-01-01",
            "total_tracks": 24,
            "total_discs": 2,
            "album_type": "album",
            "artists": [{"name": "Artist One"}],
        },
        "track_info": {
            "name": "Song Two",
            "id": "track-2",
            "track_number": 4,
            "disc_number": 2,
            "artists": [{"name": "Artist One"}],
        },
        "original_search_result": {
            "title": "Song Two",
            "clean_title": "Song Two",
            "clean_album": "Album One",
            "clean_artist": "Artist One",
            "artists": [{"name": "Artist One"}],
        },
        "source": "deezer",
        "is_album_download": False,
    }

    final_path, created = import_paths.build_final_path_for_track(
        context,
        {"name": "Artist One"},
        {
            "is_album": True,
            "album_name": "Album One",
            "track_number": 4,
            "disc_number": 2,
        },
        ".flac",
    )

    assert created is True
    # Filename has the CD02 label; NO "Disc 2" folder injected.
    assert final_path == str(
        tmp_path / "Transfer" / "Artist One" / "Artist One - Album One" / "CD02 - 04 - Song Two.flac"
    )
    # Verify the disc folder was not created either.
    assert not (tmp_path / "Transfer" / "Artist One" / "Artist One - Album One" / "Disc 2").exists()


# ── #745: $year must validate, never blind-slice release_date ──────────────

def test_extract_year_accepts_real_dates():
    assert import_paths._extract_year_from_release_date("2026-01-01") == "2026"
    assert import_paths._extract_year_from_release_date("1999") == "1999"
    assert import_paths._extract_year_from_release_date("2026") == "2026"
    # Datetime-ish string — still leads with a valid year.
    assert import_paths._extract_year_from_release_date("2010-12-31T00:00:00Z") == "2010"


def test_extract_year_rejects_non_date_values():
    # #745 exact case: release_date poisoned with the album NAME.
    assert import_paths._extract_year_from_release_date("Mantras (Deluxe)") == ""
    assert import_paths._extract_year_from_release_date("Mant") == ""
    # Implausible / sentinel years are rejected.
    assert import_paths._extract_year_from_release_date("0000") == ""
    assert import_paths._extract_year_from_release_date("1800") == ""
    assert import_paths._extract_year_from_release_date("9999") == ""
    # Empty / None.
    assert import_paths._extract_year_from_release_date("") == ""
    assert import_paths._extract_year_from_release_date(None) == ""
    # Fewer than 4 leading digits.
    assert import_paths._extract_year_from_release_date("202") == ""


def test_build_final_path_drops_garbage_year_from_folder(monkeypatch, tmp_path):
    """#745 reproduction: release_date carries the album NAME, not a date.
    The $year slot must resolve to empty and the bracket cleanup must drop the
    empty () — producing 'Album One' NOT 'Album One (Mant)'."""
    config = _Config(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                # Template that uses $year, like the reporter's.
                "album_path": "$albumartist/$album ($year) [$albumtype]/$track - $title",
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(
        import_paths, "_get_album_tracks_for_source",
        lambda source, album_id: {"items": [{"disc_number": 1}]},
    )

    context = {
        "artist": {"name": "Katie Pruitt"},
        "album": {
            "name": "Mantras (Deluxe)",
            "id": "album-1",
            # POISONED: the album name landed in release_date (the #745 bug).
            "release_date": "Mantras (Deluxe)",
            "total_tracks": 12,
            "album_type": "album",
            "artists": [{"name": "Katie Pruitt"}],
        },
        "track_info": {
            "name": "White Lies, White Jesus And You",
            "id": "track-1",
            "track_number": 1,
            "disc_number": 1,
            "artists": [{"name": "Katie Pruitt"}],
        },
        "original_search_result": {
            "title": "White Lies, White Jesus And You",
            "clean_title": "White Lies, White Jesus And You",
            "clean_album": "Mantras (Deluxe)",
            "clean_artist": "Katie Pruitt",
            "artists": [{"name": "Katie Pruitt"}],
        },
        "source": "deezer",
        "is_album_download": False,
    }

    final_path, created = import_paths.build_final_path_for_track(
        context,
        {"name": "Katie Pruitt"},
        {"is_album": True, "album_name": "Mantras (Deluxe)", "track_number": 1, "disc_number": 1},
        ".flac",
    )

    assert created is True
    # The album folder must NOT contain "(Mant)" or any "(...)" year artifact.
    album_folder = os.path.basename(os.path.dirname(final_path))
    assert "(Mant" not in album_folder
    assert "()" not in album_folder
    # Empty () collapses; [Album] type stays.
    assert album_folder == "Mantras (Deluxe) [Album]"


def test_build_final_path_keeps_real_year_in_folder(monkeypatch, tmp_path):
    """Positive control: a genuine release_date still produces the (YYYY)
    folder — proves the guard didn't break the happy path."""
    config = _Config(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$album ($year) [$albumtype]/$track - $title",
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(
        import_paths, "_get_album_tracks_for_source",
        lambda source, album_id: {"items": [{"disc_number": 1}]},
    )

    context = {
        "artist": {"name": "Katie Pruitt"},
        "album": {
            "name": "Mantras (Deluxe)",
            "id": "album-1",
            "release_date": "2026-04-12",
            "total_tracks": 12,
            "album_type": "album",
            "artists": [{"name": "Katie Pruitt"}],
        },
        "track_info": {
            "name": "White Lies, White Jesus And You",
            "id": "track-1",
            "track_number": 1,
            "disc_number": 1,
            "artists": [{"name": "Katie Pruitt"}],
        },
        "original_search_result": {
            "title": "White Lies, White Jesus And You",
            "clean_title": "White Lies, White Jesus And You",
            "clean_album": "Mantras (Deluxe)",
            "clean_artist": "Katie Pruitt",
            "artists": [{"name": "Katie Pruitt"}],
        },
        "source": "deezer",
        "is_album_download": False,
    }

    final_path, _ = import_paths.build_final_path_for_track(
        context,
        {"name": "Katie Pruitt"},
        {"is_album": True, "album_name": "Mantras (Deluxe)", "track_number": 1, "disc_number": 1},
        ".flac",
    )

    album_folder = os.path.basename(os.path.dirname(final_path))
    assert album_folder == "Mantras (Deluxe) (2026) [Album]"


# ── #1009: album-folder reuse (#829) must never collapse a multi-disc album ──

def _reuse_test_config(tmp_path, template):
    return _Config(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": template,
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )


def _reuse_context(disc_number, total_discs_from_api):
    return {
        "artist": {"name": "Artist One"},
        "album": {
            "name": "Album One",
            "id": "album-1",
            "release_date": "2026-01-01",
            "total_tracks": 40,
            "total_discs": total_discs_from_api,
            "album_type": "album",
            "artists": [{"name": "Artist One"}],
        },
        "track_info": {
            "name": "Song One", "id": "track-1",
            "track_number": 13, "disc_number": disc_number,
            "artists": [{"name": "Artist One"}],
        },
        "original_search_result": {
            "title": "Song One", "clean_title": "Song One",
            "clean_album": "Album One", "clean_artist": "Artist One",
            "artists": [{"name": "Artist One"}],
        },
        "source": "deezer",
        "is_album_download": True,
    }


def test_multi_disc_album_skips_folder_reuse(monkeypatch, tmp_path):
    """#1009: mid-download the album 'exists' in exactly one folder — whichever
    disc landed first. Reusing it funnels every later track of the box set into
    that one disc folder ($cdnum templates AND the auto 'Disc N' folder alike),
    colliding the per-disc '01 - …' filenames. Multi-disc albums must always
    take the template path."""
    config = _reuse_test_config(tmp_path, "$albumartist/$album/$cdnum/$track - $title")
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)

    # a poisoned resolver: reuse would drop this disc-2 track into the CD01 folder
    cd01 = tmp_path / "Transfer" / "Artist One" / "Album One" / "CD01"
    cd01.mkdir(parents=True)
    import core.library.existing_album_folder as eaf
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "get_database", lambda: object(), raising=False)
    monkeypatch.setattr(eaf, "resolve_existing_album_folder",
                        lambda **kw: str(cd01))

    final_path, created = import_paths.build_final_path_for_track(
        _reuse_context(disc_number=2, total_discs_from_api=5),
        {"name": "Artist One"},
        {"is_album": True, "album_name": "Album One", "track_number": 13, "disc_number": 2},
        ".flac",
    )
    assert created is True
    assert final_path == str(
        tmp_path / "Transfer" / "Artist One" / "Album One" / "CD02" / "13 - Song One.flac")


def test_single_disc_album_still_reuses_existing_folder(monkeypatch, tmp_path):
    """The #829 behavior the gate must NOT break: single-disc albums keep
    joining their existing folder so $albumtype/$year drift can't split them."""
    config = _reuse_test_config(
        tmp_path, "$albumartist/$albumartist - $album/$track - $title")
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    existing = tmp_path / "Transfer" / "Artist One" / "Artist One - Album One (2020)"
    existing.mkdir(parents=True)
    import core.library.existing_album_folder as eaf
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "get_database", lambda: object(), raising=False)
    monkeypatch.setattr(eaf, "resolve_existing_album_folder",
                        lambda **kw: str(existing))

    final_path, created = import_paths.build_final_path_for_track(
        _reuse_context(disc_number=1, total_discs_from_api=1),
        {"name": "Artist One"},
        {"is_album": True, "album_name": "Album One", "track_number": 13, "disc_number": 1},
        ".flac",
    )
    assert created is True
    assert final_path == str(existing / "13 - Song One.flac")
