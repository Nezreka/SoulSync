"""#2209: changing the watchlist is the Watchlist's (music.watchlist).

add, remove and the rest were only held to the music side, so a member whose
profile has no Watchlist page could still follow artists from anywhere.
reads stay with the music side: the dashboard and the artist pages ask
whether an artist is watched.
"""

from __future__ import annotations

import pytest

flask = pytest.importorskip("flask")

from api import artist_watchlist  # noqa: E402
from core import permissions as P  # noqa: E402
from tests.permissions_fixtures import member_perms  # noqa: E402

WRITES = [
    ("/api/watchlist/add", {"artist_id": "sp-1", "artist_name": "A"}),
    ("/api/watchlist/remove", {"artist_id": "sp-1"}),
    ("/api/watchlist/add-batch", {"artists": [{"artist_id": "sp-1", "artist_name": "A"}]}),
    ("/api/watchlist/remove-batch", {"artist_ids": ["sp-1"]}),
    ("/api/library/watchlist-all-unwatched", {}),
    ("/api/watchlist/artist/sp-1/config", {"include_albums": True}),
    ("/api/watchlist/artist/sp-1/link-provider", {"source": "deezer", "provider_id": "1"}),
]


@pytest.fixture
def member_client():
    app = flask.Flask(__name__)
    app.register_blueprint(artist_watchlist.bp)
    state = {"perms": member_perms("adult", **{"music.watchlist": False})}

    @app.before_request
    def _gate():
        flask.g.profile_id, flask.g.is_admin, flask.g.perms = 50, False, state["perms"]

    return app.test_client(), state


@pytest.mark.parametrize("path,body", WRITES)
def test_a_member_without_the_watchlist_is_refused(member_client, path, body):
    client, _ = member_client
    r = client.post(path, json=body)
    assert r.status_code == 403, (path, r.get_json())
    assert r.get_json()["permission"] == "music.watchlist"


@pytest.mark.parametrize("view", [
    "add_to_watchlist", "remove_from_watchlist", "add_batch_to_watchlist",
    "remove_batch_from_watchlist", "watchlist_all_unwatched_library_artists",
    "watchlist_artist_link_provider",
])
def test_the_watchlist_writes_declare_the_watchlist(view):
    fn = getattr(artist_watchlist, view)
    assert getattr(fn, P.ACCESS_ATTR, None) == ("perm", "music.watchlist")


@pytest.mark.parametrize("view", ["check_watchlist_status", "check_watchlist_status_batch",
                                  "get_watchlist_count"])
def test_reads_stay_with_the_music_side(view):
    fn = getattr(artist_watchlist, view)
    assert getattr(fn, P.ACCESS_ATTR, None) == ("perm", "music.access")
