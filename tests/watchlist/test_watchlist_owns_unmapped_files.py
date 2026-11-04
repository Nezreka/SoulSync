"""The watchlist scan asks the library whether a new release's track is owned.

It asked the matcher scoped to the active media server, so a song SoulSync
imported that the server hadn't scanned yet (or a profile folder the server
doesn't index) read as missing and went on the wishlist again. #1952 took
that scope out of the download dialog for the same reason; the matcher
itself only ever counts a live file in the library.
"""
from types import SimpleNamespace

import core.watchlist_scanner as ws_mod


def test_a_file_the_server_hasnt_mapped_is_owned(monkeypatch):
    asked = []

    def check_track_exists(title, artist, **kwargs):
        asked.append(kwargs.get("server_source"))
        if kwargs.get("server_source"):
            return None, 0.0  # the active server has no mapping for it yet
        return SimpleNamespace(title=title, file_path="/music/A/Album/01.flac",
                               album_title="Album"), 0.95

    scanner = ws_mod.WatchlistScanner.__new__(ws_mod.WatchlistScanner)
    scanner._database = SimpleNamespace(check_track_exists=check_track_exists)
    scanner._matching_engine = SimpleNamespace(clean_title=lambda title: title)
    monkeypatch.setattr("core.settings.config_manager.get_active_media_server",
                        lambda: "plex", raising=False)

    missing = scanner.is_track_missing_from_library(
        {"name": "Song", "artists": [{"name": "A"}]}, album_name="Album")

    assert missing is False
    assert asked and set(asked) == {None}
