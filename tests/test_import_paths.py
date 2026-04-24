import core.import_paths as import_paths


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

    assert folder_path == "Artist One/Album One"
    assert filename == "3 - Song One [FLAC 16bit]"


def test_resolve_album_group_upgrades_standard_to_deluxe():
    import_paths._album_name_cache.clear()
    import_paths._album_editions.clear()

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
