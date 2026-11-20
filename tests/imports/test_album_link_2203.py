"""#2203: the import matcher takes a pasted release link or MBID, and opens on
a default search source.

Ericsson matches by MusicBrainz release id and picks MusicBrainz on every
import. The link is read off the text (core.library.direct_id.detect_link),
looked up by id on the source it names (core.imports.album_link), and comes
back as the one candidate. Clients are fakes: nothing touches the network.
"""

from __future__ import annotations

import pytest

from core.imports import album_link
from core.imports import routes as import_routes
from core.imports.routes import ImportRouteRuntime, search_albums
from core.library.direct_id import LinkRef, detect_link

MBID = "9dfe45c8-4ad5-4811-853f-97291fdb957f"
GROUP = "1b022e01-4da6-387b-8658-8678046e4cef"


# ── detection ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    (f"https://musicbrainz.org/release/{MBID}", LinkRef("musicbrainz", "album", MBID)),
    (f"musicbrainz.org/release/{MBID.upper()}?tab=x", LinkRef("musicbrainz", "album", MBID)),
    (f"https://musicbrainz.org/release-group/{GROUP}", LinkRef("musicbrainz", "release-group", GROUP)),
    (f"  {MBID}  ", LinkRef("musicbrainz", None, MBID)),
    (f"https://musicbrainz.org/recording/{MBID}", LinkRef("musicbrainz", "track", MBID)),
    ("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy", LinkRef("spotify", "album", "4aawyAB9vmqN3uQ7FjRGTy")),
    ("https://open.spotify.com/intl-de/track/4aawyAB9vmqN3uQ7FjRGTy?si=1", LinkRef("spotify", "track", "4aawyAB9vmqN3uQ7FjRGTy")),
    ("spotify:album:4aawyAB9vmqN3uQ7FjRGTy", LinkRef("spotify", "album", "4aawyAB9vmqN3uQ7FjRGTy")),
    ("https://www.deezer.com/en/album/302127", LinkRef("deezer", "album", "302127")),
    ("https://music.apple.com/us/album/discovery/697194953", LinkRef("itunes", "album", "697194953")),
    ("https://music.apple.com/us/album/discovery/697194953?i=697195462", LinkRef("itunes", "track", "697195462")),
    ("https://www.discogs.com/release/249504-Daft-Punk-Discovery", LinkRef("discogs", "album", "r249504")),
    ("https://www.discogs.com/master/2386-Daft-Punk-Discovery", LinkRef("discogs", "album", "m2386")),
    ("https://tidal.com/browse/album/1234567", LinkRef("tidal", "album", "1234567")),
    ("https://open.qobuz.com/album/0724384960650", LinkRef("qobuz", "album", "0724384960650")),
    ("https://musicbrainz.org/label/" + MBID, LinkRef("musicbrainz", None, "")),
    ("https://link.deezer.com/s/30xyz", LinkRef("deezer", None, "")),
])
def test_detects_a_pasted_link(text, expected):
    assert detect_link(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "Daft Punk Discovery", "1989", "M83", "Taylor Swift 1989",
    "4aawyAB9vmqN3uQ7FjRGTy",            # a bare spotify id names no source
    "live at deezer.com sessions",        # a name that mentions a site
    MBID + "x",
])
def test_ordinary_text_is_not_a_link(text):
    assert detect_link(text) is None


# ── lookup ───────────────────────────────────────────────────────────────


class _Client:
    """the get-by-id methods the lookup calls, recording each call."""

    def __init__(self, album=None, track=None, editions=None):
        self._album, self._track, self._editions = album, track, editions
        self.calls = []

    def get_album(self, album_id, include_tracks=True, allow_fallback=True):
        self.calls.append(("album", album_id, include_tracks, allow_fallback))
        return self._album

    def get_album_metadata(self, album_id, include_tracks=True):
        self.calls.append(("album_metadata", album_id, include_tracks))
        return self._album

    def get_track_details(self, track_id, allow_fallback=True):
        self.calls.append(("track", track_id, allow_fallback))
        return self._track


class _MBClient(_Client):
    def release_group_editions(self, group_id, artist_name=""):
        self.calls.append(("editions", group_id))
        return self._editions or []


def _only(source, client):
    return lambda name: client if name == source else None


def test_a_musicbrainz_release_link_comes_back_as_that_release():
    release = {"id": MBID, "name": "Discovery", "artists": [{"name": "Daft Punk"}],
               "external_urls": {"musicbrainz": f"https://musicbrainz.org/release/{MBID}"}}
    client = _MBClient(album=release)
    out = album_link.lookup_album_link(f"https://musicbrainz.org/release/{MBID}",
                                       client_for=_only("musicbrainz", client))
    assert out == {"source": "musicbrainz", "albums": [release], "message": ""}
    assert client.calls == [("album", MBID, False, True)]


def test_a_release_group_link_lists_every_edition():
    editions = [{"id": "r1", "name": "Discovery"}, {"id": "r2", "name": "Discovery"}]
    client = _MBClient(editions=editions)
    out = album_link.lookup_album_link(f"https://musicbrainz.org/release-group/{GROUP}",
                                       client_for=_only("musicbrainz", client))
    assert out["albums"] == editions
    assert client.calls == [("editions", GROUP)]


def test_a_bare_group_mbid_lists_the_editions_too():
    group_album = {"id": GROUP, "name": "Discovery",
                   "external_urls": {"musicbrainz": f"https://musicbrainz.org/release-group/{GROUP}"}}
    client = _MBClient(album=group_album, editions=[{"id": "r1", "name": "Discovery"}])
    out = album_link.lookup_album_link(GROUP, client_for=_only("musicbrainz", client))
    assert [a["id"] for a in out["albums"]] == ["r1"]


def test_spotify_is_asked_without_its_fallback_and_a_song_link_finds_its_album():
    client = _Client(album={"id": "alb22chars0000000000001", "name": "Discovery"},
                     track={"id": "t", "album": {"id": "alb22chars0000000000001"}})
    out = album_link.lookup_album_link("https://open.spotify.com/track/4aawyAB9vmqN3uQ7FjRGTy",
                                       client_for=_only("spotify", client))
    assert out["albums"][0]["name"] == "Discovery"
    assert client.calls == [("track", "4aawyAB9vmqN3uQ7FjRGTy", False),
                            ("album", "alb22chars0000000000001", True, False)]


def test_deezer_and_discogs_keep_the_id_that_was_looked_up():
    deezer = _Client(album={"id": 302127, "name": "Discovery"})
    out = album_link.lookup_album_link("https://www.deezer.com/album/302127",
                                       client_for=_only("deezer", deezer))
    assert deezer.calls == [("album_metadata", "302127", False)]
    assert out["albums"][0]["id"] == "302127"

    discogs = _Client(album={"id": "249504", "name": "Discovery"})
    out = album_link.lookup_album_link("https://www.discogs.com/release/249504-Daft-Punk-Discovery",
                                       client_for=_only("discogs", discogs))
    # the r tag says release, not master, to the match step
    assert out["albums"][0]["id"] == "r249504"


@pytest.mark.parametrize("text, fragment", [
    (f"https://musicbrainz.org/release/{MBID}", "MusicBrainz has no release with that id"),
    (f"https://musicbrainz.org/artist/{MBID}", "artist link"),
    (f"https://musicbrainz.org/recording/{MBID}", "more than one release"),
    ("https://musicbrainz.org/label/" + MBID, "doesn't point at a release"),
    ("https://tidal.com/browse/album/1234567", "Tidal links can't be matched here"),
])
def test_a_link_that_finds_nothing_says_why(text, fragment):
    client = _MBClient(album=None)
    out = album_link.lookup_album_link(text, client_for=lambda _s: client)
    assert out["albums"] == []
    assert fragment in out["message"]


def test_a_source_that_is_not_set_up_says_so():
    out = album_link.lookup_album_link("https://www.discogs.com/release/1-x", client_for=lambda _s: None)
    assert out["albums"] == [] and "Discogs isn't set up" in out["message"]


def test_a_client_that_raises_is_a_miss_not_an_error():
    class Boom(_Client):
        def get_album(self, *a, **k):
            raise RuntimeError("503")
    out = album_link.lookup_album_link(MBID, client_for=lambda _s: Boom())
    assert out["albums"] == [] and "no release with that id" in out["message"]


def test_ordinary_text_is_left_to_the_search():
    assert album_link.lookup_album_link("Daft Punk Discovery",
                                        client_for=lambda _s: pytest.fail("no lookup")) is None


# ── the search route ─────────────────────────────────────────────────────


def test_the_search_route_answers_a_link_with_the_release_and_skips_the_search():
    runtime = ImportRouteRuntime(
        get_primary_source=lambda: "deezer",
        get_primary_source_label=lambda: "deezer",
        search_import_albums=lambda *a, **k: pytest.fail("a link is not searched"),
        lookup_album_link=lambda q: {"source": "musicbrainz", "message": "",
                                     "albums": [{"id": MBID, "name": "Discovery", "source": "musicbrainz"}]},
    )
    payload, status = search_albums(runtime, f"https://musicbrainz.org/release/{MBID}", 12, "deezer")
    assert status == 200
    assert payload["albums"] == [{"id": MBID, "name": "Discovery", "source": "musicbrainz"}]
    assert payload["link"] == {"source": "musicbrainz", "message": ""}


def test_the_search_route_passes_the_miss_message_through():
    runtime = ImportRouteRuntime(
        get_primary_source_label=lambda: "deezer",
        lookup_album_link=lambda q: {"source": "musicbrainz", "albums": [],
                                     "message": "MusicBrainz has no release with that id."},
    )
    payload, status = search_albums(runtime, MBID, 12)
    assert status == 200 and payload["albums"] == []
    assert payload["link"]["message"] == "MusicBrainz has no release with that id."


def test_the_staging_lookup_shapes_the_release_like_a_search_result(monkeypatch):
    from core.imports import staging
    release = {"id": MBID, "name": "Discovery", "artists": [{"name": "Daft Punk"}],
               "release_date": "2001-03-12", "total_tracks": 14, "country": "GB"}
    monkeypatch.setattr(staging, "get_client_for_source", _only("musicbrainz", _MBClient(album=release)))
    out = staging.lookup_import_album_link(MBID)
    card = out["albums"][0]
    assert (card["id"], card["name"], card["artist"], card["source"], card["country"]) == (
        MBID, "Discovery", "Daft Punk", "musicbrainz", "GB")
    assert staging.lookup_import_album_link("Discovery") is None


# ── the default search source ────────────────────────────────────────────


@pytest.fixture
def config(monkeypatch):
    from core.settings import config_manager
    store = {}
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(config_manager, "set", lambda key, value: store.__setitem__(key, value))
    return store


SOURCES = [{"source": "deezer", "label": "Deezer", "active": True},
           {"source": "musicbrainz", "label": "Musicbrainz", "active": False}]


def test_the_default_is_any_source_until_one_is_saved(config, monkeypatch):
    monkeypatch.setattr(import_routes, "_available_import_sources", lambda: SOURCES)
    payload, status = import_routes.search_sources()
    assert status == 200 and payload["default_source"] == ""


def test_a_saved_default_opens_the_picker_on_it(config, monkeypatch):
    monkeypatch.setattr(import_routes, "_available_import_sources", lambda: SOURCES)
    assert import_routes.set_import_default_search_source("MusicBrainz") == (
        {"success": True, "default_source": "musicbrainz"}, 200)
    assert config["import.default_search_source"] == "musicbrainz"
    payload, _ = import_routes.search_sources()
    assert payload["default_source"] == "musicbrainz"


def test_a_saved_default_that_is_not_connected_reads_as_any_source(config):
    config["import.default_search_source"] = "discogs"
    assert import_routes.import_default_search_source(SOURCES) == ""


def test_an_unknown_source_is_refused(config):
    payload, status = import_routes.set_import_default_search_source("napster")
    assert status == 400 and not payload["success"]
    assert "import.default_search_source" not in config


def test_clearing_the_default_goes_back_to_any_source(config):
    config["import.default_search_source"] = "musicbrainz"
    assert import_routes.set_import_default_search_source("")[0]["default_source"] == ""
    assert config["import.default_search_source"] == ""


def test_the_install_default_is_any_source():
    from core.settings import ConfigManager
    defaults = ConfigManager._get_default_config(ConfigManager.__new__(ConfigManager))
    assert defaults["import"]["default_search_source"] == ""
