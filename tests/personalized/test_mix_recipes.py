"""Renewable mixes (plan phase 7): recipes, their rules, keep-this-one."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.personalized import recipes as R
from core.personalized.recipes import Recipe, build_mix, quotas, replace_failing
from database.music_database import MusicDatabase


def T(artist, title='x', **kw):
    return {'name': title, 'artists': [{'name': artist}], **kw}


def _names(tracks):
    return [t['artists'][0]['name'] for t in tracks]


# ---------------------------------------------------------------------------
# the recipe
# ---------------------------------------------------------------------------

def test_a_recipe_is_cleaned_and_clamped():
    r = Recipe.from_dict({'name': ' Late Metal ', 'seeds': ['Tool', 'tool', '', 'Soen'],
                          'year_from': 2020, 'year_to': 1990, 'length': 500,
                          'mix': {'library': 2, 'discovery': 1, 'trending': 1}, 'schedule': 'hourly'})
    assert r.name == 'Late Metal' and r.seeds == ['Tool', 'Soen']
    assert (r.year_from, r.year_to) == (1990, 2020)
    assert r.length == R.MAX_LENGTH
    assert r.mix == {'library': 0.5, 'discovery': 0.25, 'trending': 0.25}
    assert r.schedule == 'weekly'


def test_nonsense_is_not_a_recipe():
    with pytest.raises(ValueError):
        Recipe.from_dict({'name': '', 'seeds': ['Tool']})
    with pytest.raises(ValueError):
        Recipe.from_dict({'name': 'Empty'})


def test_quotas_sum_to_the_length():
    for length in (10, 13, 37, 100):
        r = Recipe(name='x', seeds=['a'], length=length,
                   mix={'library': 0.6, 'discovery': 0.3, 'trending': 0.1})
        q = quotas(r)
        assert sum(q.values()) == length
    assert quotas(Recipe(name='x', seeds=['a'], length=10,
                         mix={'library': 1.0, 'discovery': 0.0, 'trending': 0.0})) == {
        'library': 10, 'discovery': 0, 'trending': 0}


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

def test_one_song_per_artist_across_every_source():
    r = Recipe(name='x', seeds=['a'], length=10, mix={'library': 0.5, 'discovery': 0.5, 'trending': 0})
    pools = {'library': [T('A', '1'), T('A', '2'), T('B')] + [T(f'L{i}') for i in range(10)],
             'discovery': [T('a', '3'), T('B', '4')] + [T(f'D{i}') for i in range(10)]}
    mix, reserve = build_mix(pools, r)
    names = [n.casefold() for n in _names(mix + reserve)]
    assert len(names) == len(set(names))
    assert len(mix) == 10


def test_a_short_source_hands_its_share_to_the_others():
    r = Recipe(name='x', seeds=['a'], length=10, mix={'library': 0.2, 'discovery': 0.8, 'trending': 0})
    pools = {'library': [T(f'L{i}') for i in range(20)], 'discovery': [T('D0'), T('D1')]}
    mix, _ = build_mix(pools, r)
    assert len(mix) == 10
    assert sum(1 for t in mix if t['mix_source'] == 'discovery') == 2
    assert sum(1 for t in mix if t['mix_source'] == 'library') == 8


def test_the_mix_is_as_long_as_the_pools_allow_and_no_longer():
    r = Recipe(name='x', seeds=['a'], length=20)
    mix, reserve = build_mix({'library': [T('A'), T('B')]}, r)
    assert len(mix) == 2 and reserve == []


def test_a_reserve_is_kept_by_new_artists():
    r = Recipe(name='x', seeds=['a'], length=10, mix={'library': 1, 'discovery': 0, 'trending': 0})
    mix, reserve = build_mix({'library': [T(f'L{i}') for i in range(30)]}, r)
    assert len(reserve) == r.reserve_size == 3
    assert not set(_names(reserve)) & set(_names(mix))


def test_sources_are_woven_not_played_in_blocks():
    r = Recipe(name='x', seeds=['a'], length=10, mix={'library': 0.5, 'discovery': 0.5, 'trending': 0})
    mix, _ = build_mix({'library': [T(f'L{i}') for i in range(10)],
                        'discovery': [T(f'D{i}') for i in range(10)]}, r)
    sources = [t['mix_source'] for t in mix]
    assert max(len(run) for run in ''.join(s[0] for s in sources).replace('ld', 'l d').replace(
        'dl', 'd l').split()) <= 2


def test_skipped_artists_never_appear():
    r = Recipe(name='x', seeds=['a'], length=5)
    mix, reserve = build_mix({'library': [T('Nickelback'), T('A'), T('B')]}, r,
                             skip_artist=lambda a: a == 'nickelback')
    assert 'Nickelback' not in _names(mix + reserve)


def test_a_failing_track_is_swapped_in_place_for_a_reserve_track():
    mix = [T('A', id='1'), T('B', id='2'), T('C', id='3')]
    reserve = [T('B', id='9'), T('D', id='4'), T('E', id='5')]
    out, left, n = replace_failing(mix, reserve, lambda t: t.get('id') == '2')
    # B's reserve track would repeat nobody (B is the one leaving), so it fits
    assert n == 1 and _names(out) == ['A', 'B', 'C'] and out[1]['id'] == '9'
    out, left, n = replace_failing(mix, [T('A', id='7'), T('D', id='4')], lambda t: t.get('id') == '3')
    assert _names(out) == ['A', 'B', 'D'], 'a reserve track by an artist already in the mix was used'
    assert [t['id'] for t in left] == ['7']


# ---------------------------------------------------------------------------
# against a real database
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    lib = [(1, 'Tool', 'sp-tool', '["progressive metal"]', 2019),
           (2, 'Deftones', 'sp-def', '["alternative metal"]', 2020),
           (3, 'Adele', 'sp-ad', '["pop"]', 2015)]
    for aid, name, sp, genres, year in lib:
        cur.execute("INSERT INTO artists (id, name, spotify_artist_id, genres) VALUES (?,?,?,?)",
                    (aid, name, sp, genres))
        cur.execute("INSERT INTO albums (id, title, artist_id, year) VALUES (?,?,?,?)",
                    (aid * 10, f'{name} LP', aid, year))
        for t in range(3):
            cur.execute("INSERT INTO tracks (title, artist_id, album_id, file_path) VALUES (?,?,?,?)",
                        (f'{name} {t}', aid, aid * 10, f'/m/{aid}-{t}.flac'))
    cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                "similarity_rank, profile_id) VALUES ('sp-tool', 'Soen', 1, 1)")
    for i, (artist, pop, genres, rel) in enumerate([
            ('Soen', 40, '["progressive metal"]', '2021-01-01'),
            ('Karnivool', 90, '["progressive metal"]', '2013-05-01'),
            ('Taylor Swift', 99, '["pop"]', '2022-01-01')]):
        cur.execute(
            "INSERT INTO discovery_pool (spotify_track_id, track_name, artist_name, album_name, "
            "popularity, release_date, artist_genres, profile_id, source, track_data_json) "
            "VALUES (?,?,?,?,?,?,?,1,'spotify',?)",
            # Soen's blob is empty: the track is rebuilt from the columns
            (f'dp{i}', f'{artist} song', artist, 'Al', pop, rel, genres,
             '{}' if artist == 'Soen' else json.dumps(
                 {'id': f'dp{i}', 'name': f'{artist} song', 'artists': [{'name': artist}]})))
    conn.commit()
    conn.close()
    return d


def _make(db, **kw):
    recipe = Recipe.from_dict({'name': 'Prog', 'length': 10, **kw})
    return R.save_recipe(db, 1, recipe)


def test_a_seeded_recipe_draws_on_the_library_and_the_seeds_circle(db):
    rid = _make(db, seeds=['Tool'], mix={'library': 0.5, 'discovery': 0.5, 'trending': 0})
    payload = R.get_or_build(db, 1, rid)
    by_source = {}
    for t in payload['tracks']:
        by_source.setdefault(t['mix_source'], []).append(t['artists'][0]['name'])
    assert by_source == {'library': ['Tool'], 'discovery': ['Soen']}   # the seed, and its circle
    assert payload['explanation']['kind'] == 'listened'
    assert payload['explanation']['seeds'][0]['name'] == 'Tool'


def test_seeds_and_genres_together_widen_it(db):
    rid = _make(db, seeds=['Tool'], genres=['progressive metal'],
                mix={'library': 0.5, 'discovery': 0.3, 'trending': 0.2})
    names = set(_names(R.get_or_build(db, 1, rid)['tracks']))
    assert names == {'Tool', 'Soen', 'Karnivool'}
    assert 'Taylor Swift' not in names and 'Adele' not in names


def test_trending_is_the_pools_most_popular_in_the_genre(db):
    rid = _make(db, genres=['pop'], mix={'library': 0, 'discovery': 0, 'trending': 1})
    payload = R.get_or_build(db, 1, rid)
    assert _names(payload['tracks']) == ['Taylor Swift']
    assert payload['explanation']['kind'] == 'genre'


def test_years_narrow_every_source(db):
    rid = _make(db, genres=['metal'], year_from=2020, year_to=2022,
                mix={'library': 0.5, 'discovery': 0.5, 'trending': 0})
    names = _names(R.get_or_build(db, 1, rid)['tracks'])
    assert 'Deftones' in names and 'Soen' in names
    assert 'Tool' not in names and 'Karnivool' not in names


def test_blocked_artists_are_never_in_a_mix(db):
    db.add_blocklist_entry(1, 'artist', 'Soen')
    rid = _make(db, seeds=['Tool'], mix={'library': 0.5, 'discovery': 0.5, 'trending': 0})
    assert 'Soen' not in _names(R.get_or_build(db, 1, rid)['tracks'])


def test_it_renews_on_schedule_and_when_the_recipe_changes(db):
    rid = _make(db, genres=['metal'], schedule='daily')
    first = R.get_or_build(db, 1, rid)
    assert R.get_or_build(db, 1, rid)['generated_at'] == first['generated_at']
    row = R.get_recipe(db, 1, rid)
    fp = R._fingerprints(db, 1)
    assert not R.is_stale(first, row, fp)
    later = datetime.fromisoformat(first['generated_at']) + timedelta(hours=21)
    assert R.is_stale(first, row, fp, now=later)
    manual = dict(row, recipe=dict(row['recipe'], schedule='manual'))
    assert not R.is_stale(dict(first, recipe=manual['recipe']), manual, fp,
                          now=later + timedelta(days=90))
    R.save_recipe(db, 1, Recipe.from_dict(dict(row['recipe'], length=12)), rid)
    assert R.get_or_build(db, 1, rid)['recipe']['length'] == 12


def test_a_track_the_wishlist_gave_up_on_is_replaced(db):
    rid = _make(db, genres=['metal'], length=10, mix={'library': 0, 'discovery': 1, 'trending': 0})
    first = R.get_or_build(db, 1, rid)
    # force a known reserve so the swap is visible
    payload = dict(first, reserve=[T('Mastodon', 'Oblivion', id='zz')])
    db.save_curated_playlist(R._key(rid), payload, 1)
    victim = first['tracks'][0]
    conn = db._get_connection()
    conn.execute("INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, retry_count) "
                 "VALUES (?, '{}', 3)", (victim['id'],))
    conn.commit()
    conn.close()
    after = R.get_or_build(db, 1, rid)
    assert after['tracks'][0]['name'] == 'Oblivion'
    assert after['replaced'] == 1 and after['reserve'] == []


def test_keep_this_one_makes_a_normal_playlist(db):
    rid = _make(db, genres=['metal'])
    pid = R.keep(db, 1, rid)
    assert pid
    conn = db._get_connection()
    row = conn.execute("SELECT name, source, profile_id FROM mirrored_playlists WHERE id = ?",
                       (pid,)).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM mirrored_playlist_tracks WHERE playlist_id = ?",
                         (pid,)).fetchone()[0]
    conn.close()
    assert row['source'] == 'soulsync_mix' and row['profile_id'] == 1
    assert row['name'].startswith('Prog (')
    assert count == len(R.get_or_build(db, 1, rid)['tracks'])


def test_recipes_are_per_profile(db):
    other = db.create_profile(name='sam')
    rid = _make(db, genres=['metal'])
    assert R.get_recipe(db, other, rid) is None
    assert R.get_or_build(db, other, rid) is None
    assert not R.delete_recipe(db, other, rid)
    assert R.delete_recipe(db, 1, rid)
    assert R.list_recipes(db, 1) == []


def test_a_source_set_to_nothing_gives_nothing_even_when_short():
    r = Recipe(name='x', seeds=['a'], length=10, mix={'library': 1.0, 'discovery': 0.0, 'trending': 0.0})
    mix, reserve = build_mix({'library': [T('A')], 'trending': [T(f'P{i}') for i in range(20)]}, r)
    assert _names(mix) == ['A'] and reserve == []


def test_seeds_only_trending_stays_in_the_seeds_circle(db):
    rid = _make(db, seeds=['Tool'], mix={'library': 0, 'discovery': 0, 'trending': 1})
    assert _names(R.get_or_build(db, 1, rid)['tracks']) == ['Soen']


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
    web_server.app.config['TESTING'] = True
    return web_server.app.test_client()


def test_the_recipe_routes(client, db):
    assert client.post('/api/discover/recipes', json={'name': ''}).status_code == 400
    made = client.post('/api/discover/recipes', json={
        'name': 'Prog', 'seeds': ['Tool'], 'length': 10,
        'mix': {'library': 0.5, 'discovery': 0.5, 'trending': 0}}).get_json()['mix']
    assert made['key'] == f"recipe_{made['recipe_id']}"
    assert set(_names(made['tracks'])) == {'Tool', 'Soen'}
    rid = made['recipe_id']

    listed = client.get('/api/discover/recipes').get_json()['mixes']
    assert [m['name'] for m in listed] == ['Prog']

    upd = client.put(f'/api/discover/recipes/{rid}', json={
        'name': 'Prog II', 'seeds': ['Tool'], 'length': 10,
        'mix': {'library': 1, 'discovery': 0, 'trending': 0}}).get_json()['mix']
    assert upd['name'] == 'Prog II' and _names(upd['tracks']) == ['Tool']
    assert client.put('/api/discover/recipes/999', json={'name': 'x', 'seeds': ['a']}).status_code == 404

    assert client.post(f'/api/discover/recipes/{rid}/refresh').get_json()['success']
    kept = client.post(f'/api/discover/recipes/{rid}/keep', json={'name': 'Keeper'}).get_json()
    assert kept['success'] and kept['playlist_id']

    assert client.delete(f'/api/discover/recipes/{rid}').get_json()['success']
    assert client.delete(f'/api/discover/recipes/{rid}').status_code == 404
    assert client.get('/api/discover/recipes').get_json()['mixes'] == []


def test_a_new_block_takes_its_tracks_out_of_a_listed_mix(client, db):
    client.post('/api/discover/recipes', json={
        'name': 'Prog', 'seeds': ['Tool'], 'length': 10,
        'mix': {'library': 0.5, 'discovery': 0.5, 'trending': 0}})
    db.add_blocklist_entry(1, 'artist', 'Soen')
    mix = client.get('/api/discover/recipes').get_json()['mixes'][0]
    assert _names(mix['tracks']) == ['Tool']
