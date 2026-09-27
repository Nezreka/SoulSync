"""Discovery remembers what you told it (plan phase 5c).

more / less like this / not now / block, from a recommendation's ⋯ menu.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.discovery.blocked import BlockedArtists
from core.discovery.explain import explanation
from core.discovery.feedback import (
    LESS_ARTIST, LESS_EDGE, MAX_SEED_WEIGHT, MORE_ARTIST, NOT_NOW_DAYS, Taste, record,
)
from database.music_database import MusicDatabase

ARTIST = {'type': 'artist', 'name': 'Soen', 'ids': {'deezer': 'dz-soen'}}
BECAUSE_TOOL = explanation('listened', ['Tool'])


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _taste(db, pid=1):
    return Taste.load(db, pid)


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

def test_more_boosts_the_seeds_that_brought_it_and_the_artist(db):
    record(db, 1, 'more', ARTIST, BECAUSE_TOOL)
    t = _taste(db)
    assert t.seed_weight('tool') == 1.5
    assert t.artist_factor('SOEN') == MORE_ARTIST
    assert t.rec_factor('Karnivool', ['Tool']) == 1.5      # anything else Tool brings
    assert t.rec_factor('Karnivool', ['Deftones']) == 1.0
    # and it saturates: two answers through the same seed, not unbounded
    record(db, 1, 'more', {'type': 'artist', 'name': 'Karnivool'}, BECAUSE_TOOL)
    record(db, 1, 'more', {'type': 'artist', 'name': 'Tesseract'}, BECAUSE_TOOL)
    assert _taste(db).seed_weight('Tool') == MAX_SEED_WEIGHT


def test_less_dampens_the_artist_and_the_edge_that_brought_it(db):
    record(db, 1, 'less', ARTIST, BECAUSE_TOOL)
    t = _taste(db)
    assert t.artist_factor('Soen') == LESS_ARTIST
    assert t.edge_factor('Tool', 'Soen') == LESS_EDGE
    assert t.edge_factor('Deftones', 'Soen') == 1.0
    # through Tool it's lower still than through anything else
    assert t.rec_factor('Soen', ['Tool']) == LESS_ARTIST * LESS_EDGE
    assert t.rec_factor('Soen', ['Deftones']) == LESS_ARTIST
    # a seed you said less of (as an artist) counts for less as a seed too
    assert t.seed_weight('Soen') == LESS_ARTIST


def test_more_and_less_replace_each_other(db):
    record(db, 1, 'more', ARTIST, BECAUSE_TOOL)
    record(db, 1, 'less', ARTIST, BECAUSE_TOOL)
    rows = db.get_discovery_feedback(1)
    assert [r['kind'] for r in rows] == ['less']
    record(db, 1, 'less', ARTIST)              # the same answer again is one row
    assert len(db.get_discovery_feedback(1)) == 1


def test_a_work_is_judged_by_its_artist(db):
    record(db, 1, 'less', {'type': 'track', 'name': 'Lotus', 'artist_name': 'Soen'}, BECAUSE_TOOL)
    assert _taste(db).artist_factor('soen') == LESS_ARTIST


def test_nonsense_is_not_recorded(db):
    assert record(db, 1, 'love', ARTIST) is None
    assert record(db, 1, 'more', {'type': 'playlist', 'name': 'x'}) is None
    assert record(db, 1, 'more', {'type': 'artist', 'name': '  '}) is None
    assert record(db, 1, 'more', {'type': 'track', 'name': 'Lotus'}) is None   # no artist
    assert db.get_discovery_feedback(1) == []


def test_feedback_is_per_profile(db):
    other = db.create_profile(name='sam')
    record(db, other, 'less', ARTIST)
    assert _taste(db, 1).is_empty
    assert _taste(db, other).artist_factor('Soen') == LESS_ARTIST


def test_adjusting_keeps_a_missing_weight_at_the_rankers_default(db):
    record(db, 1, 'less', ARTIST, BECAUSE_TOOL)
    t = _taste(db)
    out = t.adjust_related('Tool', [{'name': 'Soen'}, {'name': 'Karnivool', 'weight': 0.5}])
    assert [r['name'] for r in out] == ['Karnivool', 'Soen']
    assert out[1]['weight'] == pytest.approx(LESS_ARTIST * LESS_EDGE)
    seeds = t.adjust_seeds([{'name': 'Soen'}, {'name': 'Tool', 'weight': 3}])
    assert [s['weight'] for s in seeds] == [LESS_ARTIST, 3.0]


def test_an_empty_taste_changes_nothing(db):
    t = _taste(db)
    related = [{'name': 'B', 'weight': 1}, {'name': 'A', 'weight': 2}]
    assert t.adjust_related('x', related) == related
    assert t.rec_factor('anyone', ['anything']) == 1.0


# ---------------------------------------------------------------------------
# not now: hidden for a while, then back
# ---------------------------------------------------------------------------

def test_not_now_hides_an_artist_by_name_and_id_until_it_expires(db):
    record(db, 1, 'not_now', ARTIST)
    hidden = BlockedArtists.load(db, 1)
    assert hidden.blocks_artist({'artist_name': 'Soen'})
    assert hidden.blocks_artist({'name': 'Different Spelling', 'deezer_artist_id': 'dz-soen'})
    assert hidden.blocks_work({'track_name': 'Lotus', 'artist_name': 'Soen'})
    # thirty days later it may come back
    record(db, 1, 'not_now', ARTIST, now=datetime.now(timezone.utc) - timedelta(days=NOT_NOW_DAYS + 1))
    assert BlockedArtists.load(db, 1).is_empty


def test_not_now_on_a_track_hides_that_track_not_the_artist(db):
    record(db, 1, 'not_now', {'type': 'track', 'name': 'Lotus', 'artist_name': 'Soen'})
    hidden = BlockedArtists.load(db, 1)
    assert hidden.blocks_work({'track_name': 'Lotus', 'artist_name': 'Soen'})
    assert hidden.blocks_work({'name': 'lotus', 'artists': [{'name': 'SOEN'}]})
    assert not hidden.blocks_work({'track_name': 'Martyrs', 'artist_name': 'Soen'})
    assert not hidden.blocks_artist({'artist_name': 'Soen'})
    # the same title by someone else is someone else's
    assert not hidden.blocks_work({'track_name': 'Lotus', 'artist_name': 'Elsewhere'})


def test_not_now_expiry_is_thirty_days_out(db):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    record(db, 1, 'not_now', ARTIST, now=now + timedelta(days=3650))   # far future so it's active
    row = db.get_discovery_feedback(1)[0]
    assert row['expires_at'] == (now + timedelta(days=3650 + NOT_NOW_DAYS)).strftime('%Y-%m-%d %H:%M:%S')


def test_resetting_taste_never_clears_a_block(db):
    db.add_blocklist_entry(1, 'artist', 'Nickelback')
    record(db, 1, 'less', ARTIST)
    record(db, 1, 'not_now', {'type': 'artist', 'name': 'Creed'})
    assert db.clear_discovery_feedback(1) == 2
    assert _taste(db).is_empty
    assert BlockedArtists.load(db, 1).blocks_name('Nickelback')


# ---------------------------------------------------------------------------
# the routes
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
    monkeypatch.setattr('core.blocklist.runtime.build_resolvers', lambda: {})
    routes.invalidate_discover_shelf_cache()
    web_server.app.config['TESTING'] = True
    yield web_server.app.test_client()
    routes.invalidate_discover_shelf_cache()


def _post(client, action, entity=ARTIST, expl=BECAUSE_TOOL):
    return client.post('/api/discover/feedback',
                       json={'action': action, 'entity': entity, 'explanation': expl})


def test_the_four_answers(client, db):
    assert _post(client, 'more').get_json()['success']
    assert _post(client, 'not_now', {'type': 'artist', 'name': 'Creed'}).get_json()['success']
    # block from a track blocks its artist, in the blocklist, with ids resolved there
    body = _post(client, 'block', {'type': 'track', 'name': 'Rockstar', 'artist_name': 'Nickelback'})
    assert body.get_json()['success']
    assert [r['name'] for r in db.get_blocklist(1, entity_type='artist')] == ['Nickelback']
    listed = client.get('/api/discover/feedback').get_json()['feedback']
    assert {(f['name'], f['kind']) for f in listed} == {('Soen', 'more'), ('Creed', 'not_now')}


def test_block_keeps_an_artists_ids(client, db):
    _post(client, 'block', {'type': 'artist', 'name': 'Creed', 'ids': {'deezer': 'dz-creed'}})
    assert db.get_blocklist(1, entity_type='artist')[0]['deezer_id'] == 'dz-creed'


def test_nonsense_is_a_400(client):
    assert _post(client, 'love').status_code == 400
    assert _post(client, 'more', {'type': 'artist', 'name': ''}).status_code == 400
    assert _post(client, 'block', {'type': 'track', 'name': 'x'}).status_code == 400


def test_undo_and_reset(client, db):
    fid = _post(client, 'less').get_json()['id']
    _post(client, 'block', {'type': 'artist', 'name': 'Nickelback'})
    assert client.delete(f'/api/discover/feedback/{fid}').get_json()['success']
    assert db.get_discovery_feedback(1) == []
    _post(client, 'not_now')
    assert client.delete('/api/discover/feedback').get_json()['cleared'] == 1
    assert db.get_blocklist(1, entity_type='artist'), 'reset taste cleared a block'


def test_not_now_takes_a_card_off_a_cached_shelf(client, db):
    db.set_metadata('listening_recs_artists', json.dumps([
        {'name': 'Soen', 'seeds': ['Tool'], 'seed_count': 1, 'score': 2.0},
        {'name': 'Karnivool', 'seeds': ['Tool'], 'seed_count': 1, 'score': 1.0}]))
    url = '/api/discover/listening-recommendations'
    assert [a['artist_name'] for a in client.get(url).get_json()['artists']] == ['Soen', 'Karnivool']
    _post(client, 'not_now')
    assert [a['artist_name'] for a in client.get(url).get_json()['artists']] == ['Karnivool']


def test_less_like_this_reorders_the_listening_recs(client, db):
    db.set_metadata('listening_recs_artists', json.dumps([
        {'name': 'Soen', 'seeds': ['Tool'], 'seed_count': 1, 'score': 2.0},
        {'name': 'Karnivool', 'seeds': ['Tool'], 'seed_count': 1, 'score': 1.0}]))
    url = '/api/discover/listening-recommendations'
    _post(client, 'less')
    assert [a['artist_name'] for a in client.get(url).get_json()['artists']] == ['Karnivool', 'Soen']
    # and more of it brings it back up
    _post(client, 'more')
    assert [a['artist_name'] for a in client.get(url).get_json()['artists']] == ['Soen', 'Karnivool']


def test_less_like_this_reorders_the_similar_artists(client, db, monkeypatch):
    def rec(name, occ):
        return SimpleNamespace(
            id=1, similar_artist_name=name, similar_artist_spotify_id=None,
            similar_artist_itunes_id=None, similar_artist_deezer_id=f'dz-{name}',
            similar_artist_musicbrainz_id=None, occurrence_count=occ, similarity_rank=1,
            image_url=None, genres=None, popularity=None)
    monkeypatch.setattr(type(db), 'get_top_similar_artists',
                        lambda self, **k: [rec('Soen', 3), rec('Karnivool', 2)])
    monkeypatch.setattr(type(db), 'get_recommendation_sources',
                        lambda self, names, profile_id=1, max_per=6: {n: ['Tool'] for n in names})
    url = '/api/discover/similar-artists'
    assert [a['artist_name'] for a in client.get(url).get_json()['artists']] == ['Soen', 'Karnivool']
    _post(client, 'less')
    assert [a['artist_name'] for a in client.get(url).get_json()['artists']] == ['Karnivool', 'Soen']


# ---------------------------------------------------------------------------
# the generators read it
# ---------------------------------------------------------------------------

@pytest.fixture()
def library(db):
    """Three artists you own and play; Daft Punk most, Justice least."""
    conn = db._get_connection()
    cur = conn.cursor()
    for aid, name, sp, plays in ((1, 'Daft Punk', 'sp1', 30), (2, 'Justice', 'sp2', 10),
                                 (3, 'Air', 'sp3', 20)):
        cur.execute("INSERT INTO artists (id, name, spotify_artist_id) VALUES (?,?,?)", (aid, name, sp))
        cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (?,?,?)", (aid * 10, 'Al', aid))
        for t in range(8):
            cur.execute("INSERT INTO tracks (title, artist_id, album_id, file_path) VALUES (?,?,?,?)",
                        (f'{name} {t}', aid, aid * 10, f'/m/{aid}-{t}.flac'))
        for i in range(plays):
            cur.execute("INSERT INTO listening_history (title, artist, played_at) "
                        "VALUES (?,?,datetime('now', ?))", (f'{name} {i % 8}', name, f'-{i} hours'))
    for src, sim in (('sp1', 'SebastiAn'), ('sp1', 'Kavinsky'), ('sp2', 'SebastiAn')):
        cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                    "similarity_rank, profile_id) VALUES (?,?,1,1)", (src, sim))
    conn.commit()
    conn.close()
    return db


def test_stations_put_the_artist_you_want_more_from_first(library):
    from core.discovery.stations import build_stations

    assert [s['name'] for s in build_stations(library, 1)] == ['Daft Punk', 'Air', 'Justice']
    # more like something Justice brought counts Justice up (x1.5), less of
    # Daft Punk counts it down (x0.4): 30/20/10 plays become 12/20/15
    record(library, 1, 'more', {'type': 'artist', 'name': 'SebastiAn'},
           explanation('listened', ['Justice']))
    record(library, 1, 'less', {'type': 'artist', 'name': 'Daft Punk'})
    assert [s['name'] for s in build_stations(library, 1)] == ['Air', 'Justice', 'Daft Punk']


def test_the_listening_recs_scan_ranks_with_it(library, monkeypatch):
    import core.watchlist_scanner as scanner_mod
    from core.watchlist_scanner import WatchlistScanner

    monkeypatch.setattr(scanner_mod, 'get_client_for_source', lambda *a, **k: None)
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._database = library

    def ranked():
        scanner._build_listening_recommendations(1, [])
        return [r['name'] for r in json.loads(library.get_metadata('listening_recs_artists'))]

    assert ranked() == ['SebastiAn', 'Kavinsky']          # two of yours agree on SebastiAn
    record(library, 1, 'less', {'type': 'artist', 'name': 'SebastiAn'},
           explanation('listened', ['Daft Punk', 'Justice']))
    assert ranked() == ['Kavinsky', 'SebastiAn']


def test_daily_mixes_rebuild_when_the_taste_changes(library):
    from core.personalized.daily_mixes import get_or_build_daily_mixes

    first = get_or_build_daily_mixes(library)
    assert get_or_build_daily_mixes(library)['taste'] == first['taste']
    record(library, 1, 'less', {'type': 'artist', 'name': 'Kavinsky'})
    rebuilt = get_or_build_daily_mixes(library)
    assert rebuilt['taste'] != first['taste']


def test_more_on_a_station_counts_its_own_artist_up_as_a_seed(library):
    from core.discovery.stations import build_stations

    justice = next(s for s in build_stations(library, 1) if s['name'] == 'Justice')
    record(library, 1, 'more', {'type': 'artist', 'name': 'Justice'}, justice['explanation'])
    record(library, 1, 'more', {'type': 'artist', 'name': 'Justice'}, justice['explanation'])
    assert _taste(library).seed_weight('Justice') == 1.5     # the same answer twice is one
    record(library, 1, 'less', {'type': 'artist', 'name': 'Daft Punk'})
    assert [s['name'] for s in build_stations(library, 1)] == ['Air', 'Justice', 'Daft Punk']
