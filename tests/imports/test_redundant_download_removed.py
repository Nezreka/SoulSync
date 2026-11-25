"""a download the library didn't need doesn't stay in the downloads folder.

when the existing library file is kept (the new one is no improvement) the
branch returned without touching the download. a file in the person's own
import folder must stay; one SoulSync fetched was left behind for good, and
the next grab of that track then found the stale copy by its name.
"""

from core.imports import pipeline


class _Cfg:
    def __init__(self, downloads):
        self.downloads = str(downloads)

    def get(self, key, default=None):
        return self.downloads if key == "soulseek.download_path" else default


def test_a_file_in_the_downloads_folder_is_removed(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    f = downloads / "user" / "Album" / "01 - Song.flac"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "docker_resolve_path", lambda p: p)
    assert pipeline._discard_redundant_download(str(f), _Cfg(downloads)) is True
    assert not f.exists()


def test_a_file_anywhere_else_is_left_alone(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    mine = tmp_path / "import" / "01 - Song.flac"
    mine.parent.mkdir()
    mine.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "docker_resolve_path", lambda p: p)
    assert pipeline._discard_redundant_download(str(mine), _Cfg(downloads)) is False
    assert mine.exists()
    # and never the folder itself, or a path that only looks like it
    assert pipeline._discard_redundant_download(str(downloads), _Cfg(downloads)) is False
    twin = tmp_path / "downloads-old" / "a.flac"
    twin.parent.mkdir()
    twin.write_bytes(b"x")
    assert pipeline._discard_redundant_download(str(twin), _Cfg(downloads)) is False
    assert twin.exists()
