"""Deezer's own editors publish playlists, and the public API serves them free.

This is the BROWSE half of a pipeline that already existed. Loading a playlist,
matching its tracks and syncing the result is /api/deezer/playlist/<id> plus the
/api/deezer/discovery/* family, all of it already shipped. The only thing
missing was a way to find a playlist without pasting a url — which is what the
ListenBrainz created-for shelf does for its own source.

No live network here. The api layer is stubbed; the shapes are real responses,
trimmed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from flask import Flask

from core.deezer_client import DeezerClient


# a real chart/152/playlists row, trimmed
_ROW = {
    "id": 1306931615,
    "title": "Rock Essentials",
    "nb_tracks": 100,
    "link": "https://www.deezer.com/playlist/1306931615",
    "picture": "https://api.deezer.com/playlist/1306931615/image",
    "picture_medium": "https://cdn/250.jpg",
    "picture_xl": "https://cdn/1000.jpg",
    "user": {"id": 5080304882, "name": "Rod - Deezer Rock Editor"},
    "type": "playlist",
}


def _client(payload):
    c = DeezerClient.__new__(DeezerClient)
    c._api_get = MagicMock(return_value=payload)
    return c


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

def test_an_editorial_row_becomes_the_shape_the_shelf_reads():
    c = _client({"data": [_ROW]})
    got = c.get_editorial_playlists(152)[0]

    assert got == {
        'id': '1306931615',
        'title': 'Rock Essentials',
        'creator': 'Rod - Deezer Rock Editor',
        'track_count': 100,
        'image_url': 'https://cdn/1000.jpg',
        'link': 'https://www.deezer.com/playlist/1306931615',
        'source': 'deezer',
    }


def test_the_id_is_the_one_the_existing_loader_takes():
    """The whole point: a card picked here goes through the pipeline that is
    already there, not a second one written for browsing."""
    c = _client({"data": [_ROW]})
    assert c.get_editorial_playlists(152)[0]['id'] == str(_ROW['id'])


def test_it_asks_the_genre_chart_it_was_given():
    c = _client({"data": []})
    c.get_editorial_playlists(116, limit=10)
    c._api_get.assert_called_once_with('chart/116/playlists', {'limit': 10}, use_token=False)


def test_the_biggest_artwork_wins():
    """Shelf tiles are retina; the smaller keys are the same image scaled."""
    c = _client({"data": [dict(_ROW, picture_xl=None)]})
    assert c.get_editorial_playlists(0)[0]['image_url'] == 'https://cdn/250.jpg'


def test_an_unnamed_or_id_less_row_is_dropped_not_rendered_blank():
    c = _client({"data": [dict(_ROW, title=''), dict(_ROW, id=None), _ROW]})
    assert len(c.get_editorial_playlists(0)) == 1


def test_a_row_without_a_user_is_credited_to_deezer():
    c = _client({"data": [dict(_ROW, user=None)]})
    assert c.get_editorial_playlists(0)[0]['creator'] == 'Deezer'


@pytest.mark.parametrize("payload", [None, {}, {"data": None}, {"data": []}])
def test_a_failed_browse_is_an_empty_row_not_an_error(payload):
    assert _client(payload).get_editorial_playlists(0) == []


@pytest.mark.parametrize("genre,expected", [
    ("152", 'chart/152/playlists'),
    (None, 'chart/0/playlists'),
    ("not-a-number", 'chart/0/playlists'),
])
def test_a_junk_genre_falls_back_to_the_everything_chart(genre, expected):
    c = _client({"data": []})
    c.get_editorial_playlists(genre)
    assert c._api_get.call_args.args[0] == expected


def test_the_limit_is_bounded():
    c = _client({"data": []})
    c.get_editorial_playlists(0, limit=9999)
    assert c._api_get.call_args.args[1]['limit'] == 100


def test_search_uses_the_playlist_search_endpoint():
    c = _client({"data": [_ROW]})
    out = c.search_playlists('deep house', limit=5)
    c._api_get.assert_called_once_with('search/playlist', {'q': 'deep house', 'limit': 5},
                                       use_token=False)
    assert out[0]['title'] == 'Rock Essentials'


def test_an_empty_search_asks_nothing():
    c = _client({"data": []})
    assert c.search_playlists('   ') == []
    c._api_get.assert_not_called()


def test_the_genre_list_is_worth_its_one_request():
    """This used to assert the opposite: that the chips cost no api call.

    That saved one request and cost sixteen genres, because the hardcoded set
    held twelve of Deezer's twenty-eight. The trade was wrong, so the assertion
    changed with the decision rather than being deleted — the fallback below is
    what still protects the offline case.
    """
    c = DeezerClient.__new__(DeezerClient)
    c._api_get = MagicMock(return_value={"data": [{"id": 464, "name": "Metal"}]})
    assert c.get_editorial_genres() == [{'id': 464, 'name': 'Metal'}]
    c._api_get.assert_called_once()


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    import api.source_playlists as sp

    fake = MagicMock()
    fake.get_editorial_playlists.return_value = [dict(
        id='1', title='Rock Essentials', creator='Rod', track_count=100,
        image_url='https://cdn/1000.jpg', link='', source='deezer')]
    fake.search_playlists.return_value = []
    fake.get_editorial_genres.return_value = [{'id': 0, 'name': 'Top'}]
    monkeypatch.setattr(sp, "_get_deezer_client", lambda: fake)

    app = Flask(__name__)
    app.register_blueprint(sp.bp)
    app.config["TESTING"] = True
    c = app.test_client()
    c._fake = fake
    return c


def test_the_shelf_endpoint_returns_playlists(client):
    body = client.get('/api/discover/deezer/editorial?genre=152').get_json()
    assert body['success'] is True
    assert body['count'] == 1
    assert body['playlists'][0]['id'] == '1'
    assert body['scope'] == {'kind': 'genre', 'genre': '152'}
    client._fake.get_editorial_playlists.assert_called_once_with('152', limit=25)


def test_a_query_searches_instead_of_browsing(client):
    body = client.get('/api/discover/deezer/editorial?q=deep+house').get_json()
    assert body['scope'] == {'kind': 'search', 'query': 'deep house'}
    client._fake.search_playlists.assert_called_once_with('deep house', limit=25)
    client._fake.get_editorial_playlists.assert_not_called()


def test_a_client_that_blows_up_gives_an_empty_shelf_not_a_500(client):
    client._fake.get_editorial_playlists.side_effect = RuntimeError("deezer is down")
    resp = client.get('/api/discover/deezer/editorial')
    assert resp.status_code == 200
    assert resp.get_json()['playlists'] == []


def test_no_deezer_client_is_an_empty_shelf(monkeypatch):
    import api.source_playlists as sp
    monkeypatch.setattr(sp, "_get_deezer_client", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(sp.bp)
    resp = app.test_client().get('/api/discover/deezer/editorial')
    assert resp.status_code == 200
    assert resp.get_json()['playlists'] == []


def test_the_genres_endpoint_lists_the_chips(client):
    body = client.get('/api/discover/deezer/genres').get_json()
    assert body['genres'] == [{'id': 0, 'name': 'Top'}]


# ---------------------------------------------------------------------------
# the browse endpoints are public and must stay that way
# ---------------------------------------------------------------------------

def test_the_chart_call_sends_no_access_token():
    """These endpoints need no auth, and a STALE token does not get ignored:

        {"error": {"type": "OAuthException", "message": "Invalid OAuth access token."}}

    so a user whose Deezer link had expired lost the browse rows entirely —
    rows that never needed their account. Reported as the shelf going back to
    "Could not reach Deezer just now" after having worked.
    """
    c = _client({"data": []})
    c.get_editorial_playlists(152)
    assert c._api_get.call_args.kwargs.get('use_token') is False


def test_the_search_call_sends_no_access_token():
    c = _client({"data": []})
    c.search_playlists('deep house')
    assert c._api_get.call_args.kwargs.get('use_token') is False


def test_api_get_still_sends_the_token_by_default():
    """Only the public browse calls opt out; the user-level endpoints must not."""
    import inspect

    from core.deezer_client import DeezerClient as _DC

    signature = inspect.signature(_DC._api_get)
    assert signature.parameters['use_token'].default is True


def test_a_token_is_still_attached_when_asked_for():
    """The opt-out must not have quietly disabled auth everywhere."""
    import requests

    from core.deezer_client import DeezerClient as _DC

    sent = {}

    class _Session:
        def get(self, url, params=None, timeout=None):
            sent.update(params or {})

            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return {"ok": True}

            return _R()

    c = _DC.__new__(_DC)
    c.session = _Session()
    c._access_token = 'live-token'
    c._api_get('user/me/albums')
    assert sent.get('access_token') == 'live-token'

    sent.clear()
    c._api_get('chart/0/playlists', use_token=False)
    assert 'access_token' not in sent
    assert requests is not None


# ---------------------------------------------------------------------------
# the genre list
# ---------------------------------------------------------------------------

def test_the_genres_come_from_deezer_not_a_constant():
    """Twelve were hardcoded to save a request. Deezer publishes 28, so that
    quietly hid Metal, Country, Blues, Folk, Soul & Funk and every regional
    category from the shelf."""
    c = _client({"data": [
        {"id": 0, "name": "All"},
        {"id": 464, "name": "Metal"},
        {"id": 84, "name": "Country"},
    ]})
    genres = c.get_editorial_genres()
    assert [g['name'] for g in genres] == ['All', 'Country', 'Metal']
    c._api_get.assert_called_once_with('genre', use_token=False)


def test_all_stays_at_the_front():
    """It is the row the shelf opens on; alphabetical would bury it."""
    c = _client({"data": [{"id": 464, "name": "Metal"}, {"id": 0, "name": "All"}]})
    assert c.get_editorial_genres()[0]['name'] == 'All'


def test_an_unreachable_genre_list_falls_back_to_the_builtin_set():
    """The chips must still appear when Deezer is down."""
    c = _client(None)
    genres = c.get_editorial_genres()
    assert len(genres) > 5
    assert any(g['name'] == 'Top' for g in genres)


def test_malformed_genre_rows_are_dropped():
    c = _client({"data": [{"id": 1}, {"name": "No id"}, "junk", {"id": 2, "name": "Good"}]})
    assert c.get_editorial_genres() == [{'id': 2, 'name': 'Good'}]


def test_the_genre_list_does_not_send_a_token():
    """Same public endpoint, same OAuthException trap as the charts."""
    c = _client({"data": [{"id": 0, "name": "All"}]})
    c.get_editorial_genres()
    assert c._api_get.call_args.kwargs.get('use_token') is False
