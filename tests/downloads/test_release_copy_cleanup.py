"""a torrent/usenet track's copy doesn't stay loose in the library root.

the requested track is copied out of the release and handed to the import,
which moves it into place. when the import didn't move it, the copy stayed.
"""

from core.downloads import post_processing as pp


def test_a_copy_the_import_left_behind_is_removed(tmp_path):
    release = tmp_path / "release" / "01 - Song.flac"
    release.parent.mkdir()
    release.write_bytes(b"audio")
    library = tmp_path / "music"
    copy = pp._copy_release_audio_to_transfer(str(release), str(library))
    assert copy and (library / "01 - Song.flac").is_file()

    assert pp._discard_unused_release_copy(copy) is True
    assert not (library / "01 - Song.flac").exists()
    assert release.read_bytes() == b"audio"            # the release keeps its original


def test_a_copy_the_import_moved_is_nothing_to_remove(tmp_path):
    assert pp._discard_unused_release_copy(str(tmp_path / "gone.flac")) is False
    assert pp._discard_unused_release_copy(None) is False
