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
