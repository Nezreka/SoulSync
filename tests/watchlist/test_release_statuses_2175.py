"""#2175: a library-wide MusicBrainz release status filter, with an override
per artist.

"I would like to filter out any releases which don't have the Status
Official on MusicBrainz." Every status is allowed until someone picks, a
release group passes when any of its releases has an allowed status, and a
group with no status (or from a source without statuses) always passes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
import core.release_statuses as RS  # noqa: E402
import core.settings as settings  # noqa: E402
from core import watchlist_scanner as WS  # noqa: E402
from database.music_database import MusicDatabase, WatchlistArtist  # noqa: E402


@pytest.fixture()
def cfg(monkeypatch):
    store = {'watchlist.global_override_enabled': False}
    cm = settings.config_manager
    monkeypatch.setattr(cm, 'get', lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(cm, 'set', lambda key, value: store.__setitem__(key, value))
    return store


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


# -- the rule ------------------------------------------------------------------

def test_every_status_is_allowed_until_someone_picks():
    assert RS.library_statuses(_Config()) == RS.RELEASE_STATUSES
    assert RS.library_statuses(None) == RS.RELEASE_STATUSES
    # an empty pick reads as every status, never as "let nothing through"
    assert RS.library_statuses(_Config({RS.CONFIG_KEY: []})) == RS.RELEASE_STATUSES
    assert not RS.is_restrictive(RS.RELEASE_STATUSES)
    assert RS.is_restrictive(("official",))


@pytest.mark.parametrize("statuses, expected", [
    ({"official"}, True),
    ({"official", "bootleg"}, True),     # any one release allowed is enough
    ({"bootleg"}, False),
    ({"bootleg", "promotion"}, False),
    (set(), True),                       # no release has a status
    (None, True),                        # nothing known
    ({"some-new-status"}, True),         # a status this list doesn't know
])
def test_a_group_passes_on_any_allowed_status(statuses, expected):
    assert RS.statuses_pass(statuses, ("official",)) is expected


def test_nothing_is_kept_out_when_every_status_is_allowed():
    assert RS.statuses_pass({"bootleg"}, RS.RELEASE_STATUSES)
    assert RS.statuses_pass({"bootleg"}, None)


def test_an_artist_follows_the_library_unless_it_has_its_own():
    library = _Config({RS.CONFIG_KEY: ["official"]})
    follower = WatchlistArtist(1, 'sp', 'A', None)
    assert RS.effective_statuses(follower, library) == ("official",)
    own = WatchlistArtist(1, 'sp', 'A', None, allowed_release_statuses='["bootleg", "official"]')
    assert RS.effective_statuses(own, library) == ("official", "bootleg")
    assert RS.effective_statuses({"allowed_release_statuses": None}, library) == ("official",)


def test_statuses_are_read_the_way_musicbrainz_writes_them():
    assert RS.parse_statuses(["Official", "Pseudo-Release", "CANCELLED"]) == (
        "official", "pseudo-release", "cancelled")
    with pytest.raises(RS.ReleaseStatusError):
        RS.validate_allowed([])
    with pytest.raises(RS.ReleaseStatusError):
        RS.validate_allowed(["official", "nope"])


class _MB:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def browse_artist_releases(self, mbid, limit=100, offset=0):
        self.calls.append(offset)
        return self.pages[offset // 100] if offset // 100 < len(self.pages) else []


def _release(group, status):
    return {"status": status, "release-group": {"id": group}}


def test_a_group_collects_the_statuses_of_all_its_releases():
    first = [_release("RG-A", "Official")] * 50 + [_release("rg-b", "Bootleg")] * 50
    second = [_release("rg-a", "Bootleg"), _release("rg-c", None)]
    client = _MB([first, second])
    groups = RS.fetch_group_statuses("mb-1", client=client)
    assert client.calls == [0, 100]
    assert groups == {"rg-a": {"official", "bootleg"}, "rg-b": {"bootleg"}, "rg-c": set()}


def test_musicbrainz_not_answering_keeps_nothing_out():
    class Down:
        def browse_artist_releases(self, *_a, **_k):
            return None
    assert RS.fetch_group_statuses("mb-1", client=Down()) is None
    assert RS.fetch_group_statuses("", client=Down()) is None


def test_a_huge_catalogue_stops_at_the_page_cap():
    client = _MB([[_release(f"rg-{n}", "Bootleg") for n in range(100)]] * 50)
    groups = RS.fetch_group_statuses("mb-1", client=client, max_pages=3)
    assert len(client.calls) == 3 and groups


# -- the watchlist scanner -----------------------------------------------------

def test_the_scanner_asks_musicbrainz_only_for_a_musicbrainz_catalogue(monkeypatch, cfg):
    asked = []
    monkeypatch.setattr(RS, "fetch_group_statuses", lambda mbid, **_k: asked.append(mbid) or {})
    artist = WatchlistArtist(1, 'sp', 'A', None, musicbrainz_artist_id='mb-a')

    # every status allowed: nothing to ask
    assert WS._watchlist_release_statuses(artist, 'musicbrainz', 'mb-a', 'musicbrainz')[1] is None
    cfg[RS.CONFIG_KEY] = ["official"]
    assert WS._watchlist_release_statuses(artist, 'spotify', 'sp', 'spotify')[1] is None
    assert asked == []
    allowed, groups = WS._watchlist_release_statuses(artist, 'musicbrainz', 'mb-a', 'musicbrainz')
    assert allowed == ("official",) and groups == {} and asked == ['mb-a']


def test_the_scanner_skips_an_album_whose_group_is_only_a_bootleg():
    groups = {"rg-boot": {"bootleg"}, "rg-off": {"official"}, "rg-none": set()}
    allowed = ("official",)
    assert not WS._album_statuses_pass(SimpleNamespace(id="rg-boot"), {}, groups, allowed)
    assert WS._album_statuses_pass(SimpleNamespace(id="rg-off"), {}, groups, allowed)
    assert WS._album_statuses_pass(SimpleNamespace(id="rg-none"), {}, groups, allowed)
    assert WS._album_statuses_pass(SimpleNamespace(id="sp-123"), {}, groups, allowed)
    # the album data's group counts when the listing's id is a release
    assert not WS._album_statuses_pass(SimpleNamespace(id="rel-1"),
                                       {"release_group_id": "rg-boot"}, groups, allowed)
    # no lookup: everything passes
    assert WS._album_statuses_pass(SimpleNamespace(id="rg-boot"), {}, None, allowed)


# -- the api -------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch, cfg):
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_artist_to_watchlist("sp-1", "Artist", source="spotify")
    import database.music_database as music_database
    import api.artist_watchlist as aw
    monkeypatch.setattr(music_database, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(aw, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(aw, "_spotify_client", lambda: None)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as test_client:
        yield test_client, db


def _stored(db):
    with db._get_connection() as conn:
        return conn.execute("SELECT allowed_release_statuses FROM watchlist_artists "
                            "WHERE spotify_artist_id='sp-1'").fetchone()[0]


def test_the_migration_adds_the_column_empty(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_artist_to_watchlist("sp-1", "Artist", source="spotify")
    assert _stored(db) is None
    with db._get_connection() as conn:
        conn.execute("ALTER TABLE watchlist_artists DROP COLUMN allowed_release_statuses")
        db._add_watchlist_release_statuses_column(conn.cursor())
        db._add_watchlist_release_statuses_column(conn.cursor())  # twice is fine
        conn.commit()
    assert _stored(db) is None
    assert db.get_watchlist_artists()[0].allowed_release_statuses is None


def test_global_config_saves_the_library_statuses(client, cfg):
    test_client, _db = client
    assert test_client.get("/api/watchlist/global-config").get_json()['config'][
        'release_statuses'] == list(RS.RELEASE_STATUSES)

    resp = test_client.post("/api/watchlist/global-config", json={
        'include_albums': True, 'release_statuses': ['Official', 'promotion']})
    assert resp.status_code == 200
    assert cfg[RS.CONFIG_KEY] == ['official', 'promotion']
    assert resp.get_json()['config']['release_statuses'] == ['official', 'promotion']

    # a client that never heard of statuses leaves them as they are
    test_client.post("/api/watchlist/global-config", json={'include_albums': True})
    assert cfg[RS.CONFIG_KEY] == ['official', 'promotion']

    assert test_client.post("/api/watchlist/global-config", json={
        'include_albums': True, 'release_statuses': []}).status_code == 400
    assert test_client.post("/api/watchlist/global-config", json={
        'include_albums': True, 'release_statuses': ['nope']}).status_code == 400


def test_artist_config_inherits_then_overrides(client, cfg):
    test_client, db = client
    cfg[RS.CONFIG_KEY] = ['official']
    config = test_client.get("/api/watchlist/artist/sp-1/config").get_json()['config']
    assert config['release_statuses'] is None
    assert config['library_release_statuses'] == ['official']
    assert config['effective_release_statuses'] == ['official']

    resp = test_client.post("/api/watchlist/artist/sp-1/config",
                            json={'release_statuses': ['bootleg', 'official']})
    assert resp.status_code == 200
    assert resp.get_json()['config']['release_statuses'] == ['official', 'bootleg']
    assert RS.parse_statuses(_stored(db)) == ('official', 'bootleg')

    # a save that doesn't mention it keeps it
    test_client.post("/api/watchlist/artist/sp-1/config", json={'include_eps': False})
    assert RS.parse_statuses(_stored(db)) == ('official', 'bootleg')
    # and it counts as an artist with its own rules
    assert test_client.get("/api/watchlist/global-config").get_json()['config'][
        'custom_artist_count'] >= 1

    test_client.post("/api/watchlist/artist/sp-1/config", json={'release_statuses': None})
    assert _stored(db) is None

    assert test_client.post("/api/watchlist/artist/sp-1/config",
                            json={'release_statuses': ['nope']}).status_code == 400


def test_reset_to_defaults_puts_its_statuses_back_on_the_librarys(client, cfg):
    test_client, db = client
    test_client.post("/api/watchlist/artist/sp-1/config", json={'follow_defaults': True})
    test_client.post("/api/watchlist/artist/sp-1/config", json={'release_statuses': ['official']})
    assert test_client.post("/api/watchlist/reset-to-defaults").get_json()['reset'] == 1
    assert _stored(db) is None


def test_the_v1_api_reads_and_writes_an_artists_statuses(client, monkeypatch):
    _client, db = client
    from unittest.mock import patch

    from flask import Blueprint, Flask

    import api.watchlist as v1
    monkeypatch.setattr(v1, "get_database", lambda *a, **k: db)
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(v1, "require_api_key", lambda f: f):
        v1.register_routes(bp)
    app.register_blueprint(bp)
    api = app.test_client()

    resp = api.patch("/api/v1/watchlist/sp-1", json={'release_statuses': ['official']})
    assert resp.status_code == 200, resp.get_json()
    assert RS.parse_statuses(_stored(db)) == ('official',)
    assert api.patch("/api/v1/watchlist/sp-1", json={'release_statuses': 'official'}).status_code == 400
    assert api.patch("/api/v1/watchlist/sp-1", json={'release_statuses': None}).status_code == 200
    assert _stored(db) is None

    from api.serializers import serialize_watchlist_artist
    artist = WatchlistArtist(1, 'sp', 'A', None, allowed_release_statuses='["official"]')
    assert serialize_watchlist_artist(artist)['release_statuses'] == ['official']
