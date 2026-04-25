import sys
import types

from core.imports.file_ops import (
    cleanup_empty_directories,
    safe_move_file,
)
from core.imports.filename import extract_track_number_from_filename
from core.imports.staging import read_staging_file_metadata


def test_extract_track_number_from_filename_handles_common_patterns():
    assert extract_track_number_from_filename("01 - Song.mp3") == 1
    assert extract_track_number_from_filename("1-03 - Song.mp3") == 3
    assert extract_track_number_from_filename("Artist - Song.mp3") == 1


def test_safe_move_file_replaces_existing_destination(tmp_path):
    src = tmp_path / "source.flac"
    dst_dir = tmp_path / "dest"
    dst_dir.mkdir()
    dst = dst_dir / "track.flac"

    src.write_text("new")
    dst.write_text("old")

    safe_move_file(src, dst)

    assert not src.exists()
    assert dst.read_text() == "new"


def test_cleanup_empty_directories_removes_nested_empty_paths(tmp_path):
    download_root = tmp_path / "downloads"
    nested_dir = download_root / "Artist" / "Album"
    nested_dir.mkdir(parents=True)
    moved_file_path = nested_dir / "track.flac"

    cleanup_empty_directories(str(download_root), str(moved_file_path))

    assert not nested_dir.exists()
    assert not (download_root / "Artist").exists()
    assert download_root.exists()


def test_read_staging_file_metadata_reads_tags(monkeypatch, tmp_path):
    file_path = tmp_path / "Song One.flac"
    file_path.write_text("fake")

    class DummyTags:
        def __init__(self):
            self.values = {
                "title": ["Song One"],
                "artist": ["Artist One"],
                "albumartist": ["Album Artist"],
                "album": ["Album One"],
                "tracknumber": ["03/12"],
                "discnumber": ["2/3"],
            }

        def get(self, key, default=None):
            return self.values.get(key, default)

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: DummyTags()
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata == {
        "title": "Song One",
        "artist": "Artist One",
        "albumartist": "Album Artist",
        "album": "Album One",
        "track_number": 3,
        "disc_number": 2,
    }


def test_read_staging_file_metadata_falls_back_to_filename_track_number(monkeypatch, tmp_path):
    file_path = tmp_path / "07 - Song Two.flac"
    file_path.write_text("fake")

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: None
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata["title"] == "07 - Song Two"
    assert metadata["track_number"] == 7
    assert metadata["disc_number"] == 1
