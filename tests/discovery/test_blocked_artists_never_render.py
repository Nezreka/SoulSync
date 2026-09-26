"""A blocked artist never renders on a discovery surface (plan phase 5a).

Discovery used to ignore the profile blocklist entirely (only acquisition
honoured it); the two surfaces that filtered read the legacy global list
unioned with EVERY profile's blocks. Now one definition, per profile.
"""

from __future__ import annotations

import json

import pytest

from core.discovery.blocked import (
    ARTISTS, GRAPH, NAMES, WORKS, BlockedArtists, hide_blocked,
)
from database.music_database import MusicDatabase


def _blocked(names=(), **ids):
    return BlockedArtists(frozenset(n.casefold() for n in names),
                          {s: frozenset(ids.get(s, ())) for s in ('spotify', 'itunes', 'deezer', 'musicbrainz')})


# ---------------------------------------------------------------------------
# what counts as blocked
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def test_loads_this_profiles_artist_blocks_only(db):
    other = db.create_profile(name='sam')
    db.add_blocklist_entry(1, 'artist', 'Nickelback', spotify_id='sp-nb')
    db.add_blocklist_entry(other, 'artist', 'Creed')
    db.add_blocklist_entry(1, 'album', 'Some Album')   # not an artist
    mine = BlockedArtists.load(db, 1)
    assert mine.blocks_name('nickelback')
    assert not mine.blocks_name('Creed'), "another profile's block leaked"
    assert not mine.blocks_name('Some Album')
    assert mine.blocks_id('spotify', 'sp-nb')
    theirs = BlockedArtists.load(db, other)
    assert theirs.blocks_name('Creed')
    assert not theirs.blocks_name('Nickelback')


def test_the_old_global_table_is_not_a_second_list(db):
    """It was copied into every profile's blocklist once. Reading it too would
    mean an artist unblocked in the blocklist stays hidden forever."""
    conn = db._get_connection()
    conn.execute("INSERT INTO discovery_artist_blacklist (artist_name) VALUES ('Limp Bizkit')")
    conn.commit()
    conn.close()
    assert not BlockedArtists.load(db, 1).blocks_name('Limp Bizkit')


def test_empty_blocklist_is_empty(db):
    assert BlockedArtists.load(db, 1).is_empty


def test_ids_only_match_their_own_source():
    b = _blocked(deezer=['123'])
    assert b.blocks_artist({'deezer_artist_id': '123'})
    assert not b.blocks_artist({'itunes_artist_id': '123'}), 'a deezer id collided with itunes'
    assert b.blocks_artist({'id': '123', 'source': 'deezer'})
    assert not b.blocks_artist({'id': '123', 'source': 'itunes'})


def test_a_work_is_blocked_by_any_of_its_artists_not_by_its_title():
    b = _blocked(['Nickelback'])
    assert b.blocks_work({'name': 'Song', 'artist_name': 'Nickelback'})
    assert b.blocks_work({'name': 'Duet', 'artists': [{'name': 'Someone'}, {'name': 'nickelback'}]})
    assert b.blocks_work({'name': 'x', 'artists': ['Nickelback']})
    assert b.blocks_work({'name': 'x', 'album': {'artists': [{'name': 'Nickelback'}]}})
    assert b.blocks_work({'title': 'x', 'artist': {'name': 'Nickelback', 'id': '1'}})
    # a track TITLED like the artist is not by the artist
    assert not b.blocks_work({'name': 'Nickelback', 'artist_name': 'A Tribute Band'})


# ---------------------------------------------------------------------------
# every surface shape
# ---------------------------------------------------------------------------

B = _blocked(['Nickelback'], spotify=['sp-creed'])


def test_artist_cards_and_their_name_lists():
    payload = {'artists': [
        {'artist_name': 'Nickelback'},
        {'artist_name': 'Creed', 'spotify_artist_id': 'sp-creed'},
        {'artist_name': 'Tool', 'because': ['Nickelback', {'name': 'Deftones'}, {'name': 'NICKELBACK'}]},
    ], 'count': 3}
    removed = hide_blocked(payload, {'artists': ARTISTS, 'artists[].because': NAMES}, B)
    assert [a['artist_name'] for a in payload['artists']] == ['Tool']
    assert payload['artists'][0]['because'] == [{'name': 'Deftones'}]
    assert payload['count'] == 1
    assert removed == 4


def test_stations_and_their_companions():
    payload = {'stations': [
        {'name': 'Nickelback', 'with': []},
        {'name': 'Tool', 'with': ['Nickelback', 'A Perfect Circle'], 'related': ['Nickelback']},
    ]}
    hide_blocked(payload, {'stations': ARTISTS, 'stations[].with': NAMES,
                           'stations[].related': NAMES}, B)
    assert payload['stations'] == [{'name': 'Tool', 'with': ['A Perfect Circle'], 'related': []}]


def test_because_you_listen_to_sections():
    payload = {'sections': [
        {'artist_name': 'Nickelback', 'tracks': [{'artist': 'Tool', 'name': 'a'}]},
        {'artist_name': 'Tool', 'tracks': [{'artist': 'Nickelback', 'name': 'b'},
                                           {'artist': 'Deftones', 'name': 'c'}]},
    ]}
    hide_blocked(payload, {'sections': ARTISTS, 'sections[].tracks': WORKS}, B)
    assert [s['artist_name'] for s in payload['sections']] == ['Tool']
    assert [t['name'] for t in payload['sections'][0]['tracks']] == ['c']


def test_daily_mixes_and_nested_playlists():
    mixes = {'mixes': [{'id': 'a', 'tracks': [{'artist_name': 'Nickelback'}, {'artist_name': 'Tool'}]},
                       {'id': 'b', 'tracks': [{'artist_name': 'Nickelback'}]},
                       {'id': 'c', 'tracks': []}]}
    hide_blocked(mixes, {'mixes[].tracks': WORKS}, B)
    # a mix the block emptied goes (not a heading over nothing); one that was
    # already empty isn't the block's doing and stays
    assert [m['id'] for m in mixes['mixes']] == ['a', 'c']
    assert mixes['mixes'][0]['tracks'] == [{'artist_name': 'Tool'}]
    playlist = {'playlist': {'tracks': [{'artists': [{'name': 'Nickelback'}]}, {'artists': ['Tool']}]}}
    hide_blocked(playlist, {'playlist.tracks': WORKS}, B)
    assert playlist['playlist']['tracks'] == [{'artists': ['Tool']}]


def test_an_emptied_bylt_section_goes_and_the_count_follows():
    payload = {'count': 2, 'sections': [
        {'artist_name': 'Tool', 'tracks': [{'artist_name': 'Nickelback'}]},
        {'artist_name': 'Deftones', 'tracks': [{'artist_name': 'Deftones'}]}]}
    hide_blocked(payload, {'sections': ARTISTS, 'sections[].tracks': WORKS}, B)
    assert [x['artist_name'] for x in payload['sections']] == ['Deftones']
    assert payload['count'] == 1


def test_a_station_whose_with_list_empties_still_stands():
    payload = {'stations': [{'name': 'Tool', 'with': ['Nickelback']}]}
    hide_blocked(payload, {'stations': ARTISTS, 'stations[].with': NAMES}, B)
    assert payload['stations'] == [{'name': 'Tool', 'with': []}]


def test_artist_map_drops_nodes_and_their_edges_but_keeps_the_center():
    payload = {
        'nodes': [{'id': 0, 'name': 'Nickelback', 'type': 'center'},
                  {'id': 1, 'name': 'Creed', 'spotify_id': 'sp-creed'},
                  {'id': 2, 'name': 'Tool'}],
        'edges': [{'source': 0, 'target': 1}, {'source': 0, 'target': 2}, {'source': 1, 'target': 2}],
    }
    hide_blocked(payload, {'nodes': GRAPH}, B)
    assert [n['id'] for n in payload['nodes']] == [0, 2]
    assert payload['edges'] == [{'source': 0, 'target': 2}]


def test_nothing_blocked_touches_nothing():
    payload = {'artists': [{'artist_name': 'Nickelback'}], 'count': 1}
    assert hide_blocked(payload, {'artists': ARTISTS}, _blocked()) == 0
    assert payload == {'artists': [{'artist_name': 'Nickelback'}], 'count': 1}


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

pytest.importorskip("flask")


@pytest.fixture()
def client(db, monkeypatch):
    import web_server
    import api.discover_routes as routes

    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: db)
    monkeypatch.setattr(routes, 'get_database', lambda *a, **k: db)
    monkeypatch.setattr(web_server, 'get_database', lambda *a, **k: db)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    monkeypatch.setattr(routes, 'get_current_profile_id', lambda: 1)
    routes.invalidate_discover_shelf_cache()
    db.add_blocklist_entry(1, 'artist', 'Nickelback')
    web_server.app.config['TESTING'] = True
    yield web_server.app.test_client()
    routes.invalidate_discover_shelf_cache()


def test_the_discover_blocked_artists_modal_edits_this_profiles_blocklist(client, db, monkeypatch):
    # the add resolves the other sources like the blocklist page does; no network here
    monkeypatch.setattr('core.blocklist.runtime.build_resolvers',
                        lambda: {'spotify': lambda *a, **k: 'sp-lb'})
    other = db.create_profile(name='sam')
    db.add_blocklist_entry(other, 'artist', 'Creed')
    with client.session_transaction() as sess:  # two profiles: one must be picked
        sess['profile_id'] = 1
    listed = client.get('/api/discover/artist-blacklist').get_json()['entries']
    assert [e['artist_name'] for e in listed] == ['Nickelback'], "another profile's block leaked"

    assert client.post('/api/discover/artist-blacklist',
                       json={'artist_name': 'Limp Bizkit', 'deezer_artist_id': 'dz-lb'}
                       ).get_json()['success']
    row = next(r for r in db.get_blocklist(1, entity_type='artist') if r['name'] == 'Limp Bizkit')
    assert row['deezer_id'] == 'dz-lb' and row['spotify_id'] == 'sp-lb'
    assert BlockedArtists.load(db, 1).blocks_name('limp bizkit')

    assert client.delete(f"/api/discover/artist-blacklist/{row['id']}").get_json()['success']
    assert not BlockedArtists.load(db, 1).blocks_name('limp bizkit')
    # and it can't reach into another profile's list
    creed = db.get_blocklist(other, entity_type='artist')[0]
    assert not client.delete(f"/api/discover/artist-blacklist/{creed['id']}").get_json()['success']


def test_stations_route(client, monkeypatch):
    monkeypatch.setattr('core.discovery.stations.build_stations', lambda db, pid: [
        {'name': 'Nickelback', 'with': [], 'related': []},
        {'name': 'Tool', 'with': ['Nickelback'], 'related': []}])
    body = client.get('/api/discover/stations').get_json()
    assert body['stations'] == [{'name': 'Tool', 'with': [], 'related': []}]


def test_daily_mixes_route(client, monkeypatch):
    monkeypatch.setattr('core.personalized.daily_mixes.get_or_build_daily_mixes',
                        lambda db, pid, force=False: {'mixes': [
                            {'title': 'Mix 1', 'tracks': [{'artist_name': 'Nickelback'},
                                                          {'artist_name': 'Tool'}]}]})
    body = client.get('/api/discover/personalized/daily-mixes').get_json()
    assert body['mixes'][0]['tracks'] == [{'artist_name': 'Tool'}]


def test_a_new_block_applies_even_to_a_cached_shelf(client, db):
    """The real decorators in the real order: filter outside the shelf cache."""
    import flask
    import api.discover_routes as routes
    from core.discovery.blocked import hide_blocked_in_response

    calls = []
    app = flask.Flask(__name__)

    @app.route('/shelf')
    @hide_blocked_in_response({'artists': ARTISTS})
    @routes._discover_shelf_cache()
    def shelf():
        calls.append(1)
        return flask.jsonify({'success': True, 'count': 2,
                              'artists': [{'artist_name': 'Tool'}, {'artist_name': 'Deftones'}]})

    with app.test_client() as c:
        first = c.get('/shelf').get_json()
        assert [a['artist_name'] for a in first['artists']] == ['Tool', 'Deftones']
        db.add_blocklist_entry(1, 'artist', 'Deftones')
        second = c.get('/shelf').get_json()
    assert calls == [1], 'expected the second request to be a cache hit'
    assert [a['artist_name'] for a in second['artists']] == ['Tool']
    assert second['count'] == 1


def test_personalized_playlist_route(client, monkeypatch):
    import core.personalized.api as papi

    monkeypatch.setattr(papi, 'get_playlist_with_tracks', lambda *a, **k: {
        'success': True, 'tracks': [{'artist_name': 'Nickelback'}, {'artist_name': 'Tool'}]})
    body = client.get('/api/personalized/playlist/hidden_gems').get_json()
    assert body['tracks'] == [{'artist_name': 'Tool'}]


def test_library_radio_route(client, db, monkeypatch):
    tracks = lambda: {'success': True, 'tracks': [  # noqa: E731
        {'artist_name': 'Nickelback', 'id': 1}, {'artist_name': 'Tool', 'id': 2}]}
    monkeypatch.setattr(type(db), 'get_library_radio_tracks',
                        lambda self, limit=0, exclude_ids=None: tracks())
    monkeypatch.setattr(type(db), 'get_radio_tracks',
                        lambda self, track_id, limit=0, exclude_ids=None: tracks())
    for url in ('/api/library/radio?library=1', '/api/library/radio?track_id=5'):
        body = client.get(url).get_json()
        assert [t['artist_name'] for t in body['tracks']] == ['Tool'], url


# ---------------------------------------------------------------------------
# the guard: a discover surface can't forget
# ---------------------------------------------------------------------------

# Discover GETs that return no artist, album or track rows to render.
NOT_A_SURFACE = {
    '/api/discover/adventurousness',
    '/api/discover/popularity-backfill/status',
    '/api/discover/genre-explorer',          # genre names + counts
    '/api/discover/resolve-cache-album',     # one id lookup for a clicked card
    '/api/discover/diagnose',
    '/api/discover/artist-blacklist',        # the block list itself
    '/api/discover/feedback',                # what you answered, listed back to you
    '/api/discover/inbox/counts',            # one number, for the nav badge
    '/api/discover/your-artists/sources',
    '/api/discover/your-albums/sources',
    '/api/discover/your-artists/info/<artist_id>',  # details for an artist already on screen
    '/api/discover/artist-map/genre-list',   # genre names + counts
    '/api/discover/decades/available',
    '/api/discover/genres/available',
    '/api/discover_downloads/hydrate',       # the user's own download bubbles
    # playlist / genre cards; their tracks come from a filtered route
    '/api/discover/listenbrainz/created-for',
    '/api/discover/listenbrainz/user-playlists',
    '/api/discover/listenbrainz/collaborative',
    '/api/discover/listenbrainz/lastfm-radio',
    '/api/discover/deezer/editorial',
    '/api/discover/deezer/genres',
    '/api/personalized/playlists',           # the playlist index, no tracks
    # match results for the user's own mirrored playlists (a repair view)
    '/api/discovery-pool',
    # the tracklist of an album card someone clicked; the card was filtered
    '/api/discover/album/<source>/<album_id>',
    '/api/discovery/lookback-period',        # settings
    '/api/discovery/hemisphere',             # settings
}


def _is_hidden(view) -> bool:
    # outermost only: under a shelf cache the filter must wrap the cache, or a
    # cached response would skip it (and a new block would wait out the TTL)
    return bool(getattr(view, 'hides_blocked_artists', False))


def test_every_discover_surface_filters_blocked_artists():
    import web_server

    missing = []
    for rule in web_server.app.url_map.iter_rules():
        path = rule.rule
        if 'GET' not in (rule.methods or ()) or not (
                path.startswith('/api/discover') or path.startswith('/api/personalized/playlist')):
            continue
        if path in NOT_A_SURFACE:
            continue
        if not _is_hidden(web_server.app.view_functions[rule.endpoint]):
            missing.append(path)
    assert not missing, (
        f"discover routes that could render a blocked artist: {missing}. "
        "decorate them with hide_blocked_in_response, or add them to NOT_A_SURFACE with why")
