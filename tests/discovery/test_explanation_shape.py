"""One explanation shape, written by whoever makes the recommendation (plan 5b).

``{"kind", "seeds": [{"name", "id", "source"}], "confidence"}`` under
``explanation`` on BYLT sections, listening recs, similar-artist and hero
cards, daily mixes and stations.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.discovery.explain import KINDS, consensus_confidence, explanation, seed


def _valid(e):
    assert set(e) == {'kind', 'seeds', 'confidence'}
    assert e['kind'] in KINDS
    for s in e['seeds']:
        assert set(s) == {'name', 'id', 'source'} and s['name']
    assert e['confidence'] is None or 0.0 <= e['confidence'] <= 1.0
    return e


# ---------------------------------------------------------------------------
# the shape
# ---------------------------------------------------------------------------

def test_the_shape():
    e = _valid(explanation('listened', ['Tool', {'name': 'Deftones', 'id': 7, 'source': 'deezer'}], 0.823))
    assert e == {'kind': 'listened', 'confidence': 0.82, 'seeds': [
        {'name': 'Tool', 'id': None, 'source': None},
        {'name': 'Deftones', 'id': '7', 'source': 'deezer'}]}


def test_blank_and_repeated_seeds_go_and_five_is_the_cap():
    e = explanation('similar_to', ['', None, 'Tool', 'tool', 'A', 'B', 'C', 'D', 'E', 42])
    assert [s['name'] for s in e['seeds']] == ['Tool', 'A', 'B', 'C', 'D']


def test_confidence_is_clamped_or_none():
    assert explanation('genre', [], 3)['confidence'] == 1.0
    assert explanation('genre', [], -1)['confidence'] == 0.0
    assert explanation('genre', [], 'x')['confidence'] is None
    assert explanation('genre', [])['confidence'] is None


def test_an_unknown_kind_is_a_bug_not_a_shape():
    with pytest.raises(ValueError):
        explanation('because')


def test_consensus_saturates():
    assert [consensus_confidence(n) for n in (0, 1, 2, 3)] == [None, 0.5, 0.75, 0.88]
    assert consensus_confidence(50) == 1.0
    assert consensus_confidence('x') is None


def test_seed_normalises():
    assert seed(' Tool ', 12, 'spotify') == {'name': 'Tool', 'id': '12', 'source': 'spotify'}


# ---------------------------------------------------------------------------
# the producers write it
# ---------------------------------------------------------------------------

def test_bylt_sections_carry_it_from_the_seed_identity():
    from core.discovery.bylt import SeedIdentity, Shelf, candidate_from_row, section_from_shelf

    seed_id = SeedIdentity(name='Tool', ids=(('deezer', 'dz-tool'), ('spotify', 'sp-tool')))
    picks = [candidate_from_row({'artist_name': 'A', 'track_name': 'x', 'track_id': '1'},
                                seed_id.key, 'direct', 'A', 1.0),
             candidate_from_row({'artist_name': 'B', 'track_name': 'y', 'track_id': '2'},
                                seed_id.key, 'genre', 'metal', 1.0)]
    e = _valid(section_from_shelf(Shelf(seed=seed_id, selected=picks))['explanation'])
    assert e['kind'] == 'listened'
    assert e['seeds'] == [{'name': 'Tool', 'id': 'dz-tool', 'source': 'deezer'}]
    assert e['confidence'] == 0.7    # half direct, half genre
    all_direct = section_from_shelf(Shelf(seed=seed_id, selected=picks[:1]))['explanation']
    assert all_direct['confidence'] == 0.9


def test_a_bylt_generation_stored_before_the_shape_still_serves_one():
    from core.discovery.bylt_view import section_payload

    out = section_payload({'seed_key': 'k', 'seed_name': 'Tool', 'tracks': []})
    assert _valid(out['explanation'])['seeds'][0]['name'] == 'Tool'
    stored = explanation('listened', ['Deftones'], 0.9)
    assert section_payload({'seed_name': 'Tool', 'explanation': stored, 'tracks': []})['explanation'] == stored


@pytest.fixture()
def db(tmp_path):
    """Two artists you own and play, each the other's similar artist."""
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    cur = conn.cursor()
    for aid, name, sp in ((1, 'Daft Punk', 'sp1'), (2, 'Justice', 'sp2')):
        cur.execute("INSERT INTO artists (id, name, spotify_artist_id) VALUES (?,?,?)", (aid, name, sp))
        cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (?,?,?)", (aid * 10, 'Al', aid))
        for t in range(8):
            cur.execute("INSERT INTO tracks (title, artist_id, album_id, file_path) VALUES (?,?,?,?)",
                        (f'{name} {t}', aid, aid * 10, f'/m/{aid}-{t}.flac'))
        for i in range(20):
            cur.execute("INSERT INTO listening_history (title, artist, played_at) "
                        "VALUES (?,?,datetime('now', ?))", (f'{name} {i % 8}', name, f'-{i} hours'))
    for src, sim in (('sp1', 'Justice'), ('sp2', 'Daft Punk')):
        cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                    "similarity_rank, profile_id) VALUES (?,?,1,1)", (src, sim))
    conn.commit()
    conn.close()
    return db


def test_daily_mixes_carry_it(db):
    from core.personalized.daily_mixes import generate_daily_mixes

    mixes = generate_daily_mixes(db, today=date(2026, 8, 25))['mixes']
    assert mixes
    for mix in mixes:
        e = _valid(mix['explanation'])
        assert e['kind'] == 'listened'
        assert {s['name'] for s in e['seeds']} == set(mix['artists'])
        assert e['confidence'] == 1.0    # nothing but owned tracks here


def test_stations_carry_it(db):
    from core.discovery.stations import build_stations

    stations = build_stations(db, 1)
    assert len(stations) == 2
    for st in stations:
        e = _valid(st['explanation'])
        assert e['kind'] == 'listened'
        assert e['seeds'][0]['name'] == st['name']
        assert e['seeds'][0]['source'] == 'spotify' and e['seeds'][0]['id']
    # the heaviest-played station is the most confident one
    assert max(st['explanation']['confidence'] for st in stations) == 1.0


# ---------------------------------------------------------------------------
# the routes serve it
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(db, monkeypatch):
    import web_server
    import api.discover_routes as routes

    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: db)
    monkeypatch.setattr(routes, 'get_database', lambda *a, **k: db)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    monkeypatch.setattr(routes, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr(routes, '_get_active_discovery_source', lambda: 'deezer')
    routes.invalidate_discover_shelf_cache()
    web_server.app.config['TESTING'] = True
    yield web_server.app.test_client()
    routes.invalidate_discover_shelf_cache()


def test_listening_recs_serve_what_the_scan_wrote(client, db):
    import json

    written = explanation('listened', ['Daft Punk'], 0.5)
    db.set_metadata('listening_recs_artists', json.dumps([
        {'name': 'Soen', 'seeds': ['Daft Punk'], 'seed_count': 1, 'score': 2.0,
         'explanation': written},
        # stored by a scan from before the shape: made from its seeds, once
        {'name': 'Karnivool', 'seeds': ['Daft Punk', 'Justice'], 'seed_count': 2, 'score': 1.0},
    ]))
    body = client.get('/api/discover/listening-recommendations').get_json()
    by_name = {a['artist_name']: a for a in body['artists']}
    assert by_name['Soen']['explanation'] == written
    old = _valid(by_name['Karnivool']['explanation'])
    assert [s['name'] for s in old['seeds']] == ['Daft Punk', 'Justice']
    assert old['confidence'] == 0.75
    assert 'because' not in by_name['Karnivool'], 'one shape, not two'


def test_similar_artists_carry_it(client, db, monkeypatch):
    from types import SimpleNamespace

    rec = SimpleNamespace(
        id=1, similar_artist_name='Soen', similar_artist_spotify_id=None,
        similar_artist_itunes_id=None, similar_artist_deezer_id='dz-soen',
        similar_artist_musicbrainz_id=None, occurrence_count=3, similarity_rank=1,
        image_url=None, genres=None, popularity=None)
    monkeypatch.setattr(type(db), 'get_top_similar_artists', lambda self, **k: [rec])
    monkeypatch.setattr(type(db), 'get_recommendation_sources',
                        lambda self, names, profile_id=1, max_per=6: {'Soen': ['Tool', 'Deftones']})
    body = client.get('/api/discover/similar-artists').get_json()
    e = _valid(body['artists'][0]['explanation'])
    assert e['kind'] == 'similar_to'
    assert [s['name'] for s in e['seeds']] == ['Tool', 'Deftones']
    assert e['confidence'] == 0.75
    assert 'because' not in body['artists'][0]


def test_a_blocked_seed_is_not_named_in_an_explanation(client, db, monkeypatch):
    import json

    db.add_blocklist_entry(1, 'artist', 'Justice')
    db.set_metadata('listening_recs_artists', json.dumps([
        {'name': 'Karnivool', 'seeds': ['Daft Punk', 'Justice'], 'seed_count': 2, 'score': 1.0}]))
    body = client.get('/api/discover/listening-recommendations').get_json()
    assert [s['name'] for s in body['artists'][0]['explanation']['seeds']] == ['Daft Punk']


def test_the_listening_recs_scan_writes_it(db, monkeypatch):
    """The scan that makes the recs writes the explanation into what it stores."""
    import json
    import core.watchlist_scanner as scanner_mod
    from core.watchlist_scanner import WatchlistScanner

    monkeypatch.setattr(scanner_mod, 'get_client_for_source', lambda *a, **k: None)  # no network

    conn = db._get_connection()
    for src in ('sp1', 'sp2'):   # both of your artists point at one you don't have
        conn.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                     "similarity_rank, profile_id) VALUES (?, 'SebastiAn', 2, 1)", (src,))
    conn.commit()
    conn.close()
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._database = db
    scanner._build_listening_recommendations(1, [])

    stored = json.loads(db.get_metadata('listening_recs_artists'))
    rec = next(r for r in stored if r['name'] == 'SebastiAn')
    e = _valid(rec['explanation'])
    assert e['kind'] == 'listened'
    assert {s['name'] for s in e['seeds']} == {'Daft Punk', 'Justice'}
    assert e['confidence'] == 0.75
