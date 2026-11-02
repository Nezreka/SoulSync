"""a media server on another machine still feeds the library.

when SoulSync can't open a file the server lists, the scan adopts it from the
server's own metadata, but only if the server lives somewhere SoulSync can't
see. that used to be "no library folder is visible", and the shared transfer
folder is always one and always exists, so a remote Plex/Jellyfin's new
albums never reached the library. it is decided per file now.
"""

from __future__ import annotations

import os

from core.library2.library_roots import normalize_prefix
from core.library2.media_server_sync import _library_folders_visible


def test_a_remote_servers_path_is_not_visible_even_with_the_transfer_folder_here(tmp_path):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    roots = {normalize_prefix(str(transfer)): 0}
    remote = os.path.join(os.sep, "nas", "plex", "music", "Artist", "Album", "01 - Song.flac")
    assert _library_folders_visible(roots, remote) is False


def test_a_file_gone_from_a_folder_that_is_still_here_stays_local(tmp_path):
    album = tmp_path / "music" / "Artist" / "Album"
    album.mkdir(parents=True)
    assert _library_folders_visible({}, str(album / "01 - Song.flac")) is True


def test_a_path_under_a_visible_root_stays_local_even_when_its_album_folder_is_gone(tmp_path):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    roots = {normalize_prefix(str(transfer)): 0}
    gone = os.path.join(str(transfer), "Artist", "Album", "01 - Song.flac")
    assert _library_folders_visible(roots, gone) is True
