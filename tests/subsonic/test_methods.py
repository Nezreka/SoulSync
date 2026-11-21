"""the music apps' methods, pinned: a method a paired device may call is a
decision, made here where a reviewer sees it (the same rule as the device
battery's allow-list, tests/test_device_token_battery.py, where /rest is one
route)."""

from __future__ import annotations

from api import subsonic as API

PINNED = {
    # system
    "ping", "getLicense", "getOpenSubsonicExtensions", "getScanStatus", "startScan", "tokenInfo", "getUser",
    "getMusicFolders", "getBookmarks", "getPodcasts", "getNewestPodcasts", "getInternetRadioStations", "getShares",
    # browsing
    "getIndexes", "getArtists", "getArtist", "getAlbum", "getSong", "getMusicDirectory", "getGenres",
    "getArtistInfo", "getArtistInfo2", "getAlbumInfo", "getAlbumInfo2", "getSimilarSongs", "getSimilarSongs2",
    "getTopSongs",
    # lists and search
    "getAlbumList", "getAlbumList2", "getRandomSongs", "getSongsByGenre", "search2", "search3",
    "getStarred", "getStarred2", "getNowPlaying",
    # the profile's own things
    "star", "unstar", "setRating", "scrobble", "getPlayQueue", "savePlayQueue",
    "getPlaylists", "getPlaylist", "createPlaylist", "updatePlaylist", "deletePlaylist",
    # media
    "stream", "download", "getCoverArt", "getLyrics", "getLyricsBySongId",
}


def test_the_methods_are_exactly_the_pinned_ones():
    assert set(API.METHODS) == PINNED


def test_only_the_extensions_list_is_open():
    assert API.PUBLIC_METHODS == {"getOpenSubsonicExtensions"}


def test_start_scan_is_refused():
    # a music app never starts a library scan: that is SoulSync's (admin) job
    from core.subsonic import response as R

    class Ctx:
        pass
    try:
        API.METHODS["startScan"](Ctx())
    except R.SubsonicError as exc:
        assert exc.code == R.NOT_AUTHORIZED
    else:  # pragma: no cover
        raise AssertionError("startScan answered")


def test_the_view_suffix_and_method_names():
    assert API.method_name("/rest/ping.view") == "ping"
    assert API.method_name("/rest/getAlbum") == "getAlbum"
    assert API.is_rest_path("/rest/x") and not API.is_rest_path("/api/rest")
