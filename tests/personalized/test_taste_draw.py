"""the taste-weighted draw the discovery-pool playlists share."""

import random
from datetime import date

from core.personalized.taste_draw import artist_affinity, draw, is_junk, taste_weight
from core.personalized_playlists import PersonalizedPlaylistsService


def _t(artist, title, genres=None, **extra):
    return {'artist_name': artist, 'track_name': title, 'duration_ms': 200_000,
            '_artist_genres_raw': genres, **extra}


def test_junk_is_compilations_chapters_skits_and_christmas_in_july():
    july = date(2026, 7, 1)
    assert is_junk(_t('Various Artists', 'Wagner: Albumblatt'), july)
    assert is_junk(_t('Luminaries', 'Kapitel 269 - The Luminaries'), july)
    assert is_junk(_t('A', 'Chapter 3'), july)
    assert is_junk(_t('A', 'Intro', duration_ms=40_000), july)
    assert is_junk(_t('A', 'Last Christmas'), july)
    assert not is_junk(_t('A', 'Last Christmas'), date(2026, 12, 1))
    assert not is_junk(_t('M83', 'Midnight City'), july)
    assert not is_junk(_t('A', 'The Chapter Song'), july)     # a word, not a chapter


def test_artist_affinity_follows_what_you_play():
    seeds = [{'name': 'Daft Punk', 'weight': 500}, {'name': 'Justice', 'weight': 400},
             {'name': 'Rarely', 'weight': 2}]
    sims = {'daft punk': [{'name': 'Kavinsky', 'score': 1.0}, {'name': 'Far Off', 'score': 0.1}],
            'justice': [{'name': 'Kavinsky', 'score': 0.9}],
            'rarely': [{'name': 'Polka Band', 'score': 1.0}]}
    aff = artist_affinity(seeds, sims)
    # two of your heavy artists agree on kavinsky
    assert aff['kavinsky'] > aff['polka band'] > 0
    assert aff['kavinsky'] > aff['far off']
    # your own artists count too
    assert aff['daft punk'] > aff['polka band']
    assert artist_affinity([], {}) == {}


def test_a_live_take_weighs_less():
    genres, artists = {}, {'kavinsky': 1.0}
    studio = taste_weight(_t('Kavinsky', 'Nightcall'), genres, artists)
    live = taste_weight(_t('Kavinsky', 'Nightcall (Live at Coachella)'), genres, artists)
    assert live < studio / 2
    assert taste_weight(_t('Kavinsky', 'Deliver Us'), genres, artists) == studio


def test_taste_weight_runs_from_a_floor_to_one():
    genres, artists = {'synthwave': 1.0}, {'kavinsky': 1.0}
    stranger = taste_weight(_t('Nobody', 'x', '["polka"]'), genres, artists)
    mine = taste_weight(_t('Kavinsky', 'x', '["synthwave"]'), genres, artists)
    assert stranger == 0.15         # a stranger is still possible
    assert mine == 1.0
    assert stranger < taste_weight(_t('Nobody', 'x', '["synthwave"]'), genres, artists) < mine


def test_draw_keeps_one_version_of_each_song():
    tracks = [_t('CLTX', 'Deadzone'), _t('CLTX', 'Deadzone (Denis Dekay Remix)'),
              _t('CLTX', 'Other')]
    out = draw(tracks, lambda t: 1.0, random.Random(1), date(2026, 7, 1))
    assert len(out) == 2


def test_draw_leans_toward_heavier_tracks():
    mine, other = _t('Mine', 'a'), _t('Other', 'b')
    firsts = [draw([other, mine], lambda t: 1.0 if t is mine else 0.15,
                   random.Random(i))[0]['artist_name'] for i in range(100)]
    assert firsts.count('Mine') > 75


def test_the_service_draw_tilts_to_your_corner_of_the_pool(monkeypatch):
    """popular picks used to be mozart and a zelda soundtrack for a synthwave
    listener: thousands of tracks tied at the top popularity, picked blind"""
    svc = PersonalizedPlaylistsService(database=None)
    monkeypatch.setattr(svc, '_taste_signals',
                        lambda: ({'synthwave': 1.0}, {'kavinsky': 1.0}))
    pool = ([_t('Kavinsky', f'k{i}', '["synthwave"]', popularity=80) for i in range(10)]
            + [_t(f'Classical {i}', 'c', '["classical"]', popularity=80) for i in range(90)])
    yours = 0
    for seed in range(20):
        svc.rng = random.Random(seed)
        top10 = svc._taste_ordered(pool)[:10]
        yours += sum(1 for t in top10 if t['artist_name'] == 'Kavinsky')
    # 10% of the pool, most of the top of the draw
    assert yours / 200 > 0.6
    # a shuffle tilts gently: more than chance, far less than the curated draw
    soft = 0
    for seed in range(20):
        svc.rng = random.Random(seed)
        soft += sum(1 for t in svc._taste_ordered(pool, power=1.0)[:10]
                    if t['artist_name'] == 'Kavinsky')
    assert 0.1 < soft / 200 < yours / 200


def test_a_profile_without_signals_draws_evenly(monkeypatch):
    svc = PersonalizedPlaylistsService(database=None)
    monkeypatch.setattr(svc, '_taste_signals', lambda: ({}, {}))
    svc.rng = random.Random(3)
    pool = [_t(f'A{i}', 't') for i in range(30)]
    assert len(svc._taste_ordered(pool)) == 30


def _pool_db(tmp_path, rows):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'pool.db'))
    conn = db._get_connection()
    for i, (artist, pop) in enumerate(rows):
        conn.execute(
            "INSERT INTO discovery_pool (deezer_track_id, track_name, artist_name, album_name, "
            "source, popularity, profile_id, track_data_json, duration_ms) "
            "VALUES (?,?,?,?,'deezer',?,1,'{}',200000)", (f'dz{i}', f'{artist} {i}', artist, 'X', pop))
    conn.commit()
    conn.close()
    return db


def test_candidates_come_mostly_from_your_nearest_artists(tmp_path, monkeypatch):
    """a random slice of a 145k pool was three quarters strangers; no
    weighting makes a playlist out of that"""
    db = _pool_db(tmp_path, [('Kavinsky', 80)] * 100 + [(f'Stranger {i}', 80) for i in range(900)])
    svc = PersonalizedPlaylistsService(db)
    monkeypatch.setattr(svc, '_taste_signals', lambda: ({}, {'kavinsky': 1.0}))
    svc.rng = random.Random(1)
    rows = svc._candidates(source='deezer', fetch_limit=90)
    assert len(rows) == 90
    # 10% of the pool, two thirds of the candidates
    assert sum(1 for r in rows if r['artist_name'] == 'Kavinsky') == 60
    assert '_artist_genres_raw' in rows[0]


def test_candidates_honour_the_kinds_filter(tmp_path, monkeypatch):
    db = _pool_db(tmp_path, [('Kavinsky', 80)] * 20 + [('Kavinsky', 10)] * 20 + [('Other', 80)] * 20)
    svc = PersonalizedPlaylistsService(db)
    monkeypatch.setattr(svc, '_taste_signals', lambda: ({}, {'kavinsky': 1.0}))
    rows = svc._candidates(source='deezer', fetch_limit=100,
                           extra_where='AND popularity >= ?', extra_params=(60,))
    assert len(rows) == 40 and all(r['popularity'] == 80 for r in rows)


def test_without_taste_signals_candidates_are_a_random_slice(tmp_path, monkeypatch):
    db = _pool_db(tmp_path, [(f'A{i}', 50) for i in range(50)])
    svc = PersonalizedPlaylistsService(db)
    monkeypatch.setattr(svc, '_taste_signals', lambda: ({}, {}))
    assert len(svc._candidates(source='deezer', fetch_limit=20)) == 20


def test_spread_keeps_draw_order_but_parts_an_artist_from_itself():
    from core.personalized.taste_draw import spread
    rows = [_t('U2', '1'), _t('U2', '2'), _t('U2', '3'), _t('A', '1'), _t('B', '1'), _t('C', '1')]
    out = [(t['artist_name'], t['track_name']) for t in spread(rows)]
    assert out[0] == ('U2', '1')                 # the draw's first pick still leads
    names = [a for a, _ in out]
    assert all(a != b for a, b in zip(names, names[1:]))
    # u2's songs keep their own order
    assert [t for a, t in out if a == 'U2'] == ['1', '2', '3']
