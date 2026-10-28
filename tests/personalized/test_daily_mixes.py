"""daily mixes: taste clustering, artist spacing, owned+discovery blending."""

import random
from datetime import date

import pytest

from core.personalized.daily_mixes import (
    cluster_seeds,
    generate_daily_mixes,
    get_or_build_daily_mixes,
    interleave_owned,
)
from database.music_database import MusicDatabase


# ── clustering (pure) ─────────────────────────────────────────────────────

def test_similar_artists_cluster_together():
    seeds = [{'name': 'A', 'weight': 10}, {'name': 'B', 'weight': 8},
             {'name': 'C', 'weight': 6}, {'name': 'D', 'weight': 4}]
    similars = {'a': [{'name': 'B'}], 'c': [{'name': 'D'}]}
    clusters = cluster_seeds(seeds, similars, {})
    assert [set(c['artists']) for c in clusters] == [{'A', 'B'}, {'C', 'D'}]


def test_shared_genre_connects_without_similarity_edges():
    seeds = [{'name': 'A', 'weight': 10}, {'name': 'B', 'weight': 8}]
    genres = {'a': ['phonk'], 'b': ['Phonk']}
    clusters = cluster_seeds(seeds, {}, genres)
    assert len(clusters) == 1
    assert set(clusters[0]['artists']) == {'A', 'B'}
    assert clusters[0]['genre'] == 'Phonk'


def test_unrelated_big_artists_each_anchor_their_own_mix():
    """a mixed bag of artists that don't go together was the junk mix (kvpv
    next to a disney soundtrack). each big artist leads its own instead"""
    seeds = [{'name': n, 'weight': 10 - i} for i, n in enumerate('ABCD')]
    clusters = cluster_seeds(seeds, {}, {})   # nothing connects
    assert [c['artists'] for c in clusters] == [['A'], ['B'], ['C'], ['D']]


def test_a_light_lone_artist_founds_nothing():
    seeds = [{'name': 'A', 'weight': 100}, {'name': 'B', 'weight': 90},
             {'name': 'Tiny', 'weight': 5}]
    clusters = cluster_seeds(seeds, {'a': [{'name': 'B'}]}, {})
    assert [set(c['artists']) for c in clusters] == [{'A', 'B'}]


def test_cluster_size_cap_and_count_cap():
    seeds = [{'name': f'A{i}', 'weight': 100 - i} for i in range(30)]
    genres = {f'a{i}': ['rock'] for i in range(30)}
    clusters = cluster_seeds(seeds, {}, genres, max_clusters=3, max_per=5)
    assert len(clusters) <= 3
    assert all(len(c['artists']) <= 5 for c in clusters)


def test_clustering_is_deterministic():
    seeds = [{'name': f'A{i}', 'weight': 50 - i} for i in range(10)]
    genres = {f'a{i}': ['rock' if i % 2 else 'jazz'] for i in range(10)}
    assert cluster_seeds(seeds, {}, genres) == cluster_seeds(seeds, {}, genres)


# ── interleave (pure) ─────────────────────────────────────────────────────

def test_interleave_spaces_artists():
    by_artist = {
        'a': [{'name': f'a{i}', 'play_count': 10 - i} for i in range(5)],
        'b': [{'name': f'b{i}', 'play_count': 10 - i} for i in range(5)],
    }
    picked = interleave_owned(by_artist, ['a', 'b'], 6, random.Random(1))
    assert len(picked) == 6
    for prev, cur in zip(picked, picked[1:], strict=False):
        assert prev['name'][0] != cur['name'][0]


def test_interleave_survives_exhausted_artists():
    by_artist = {'a': [{'name': 'a0', 'play_count': 1}], 'b': []}
    picked = interleave_owned(by_artist, ['a', 'b'], 10, random.Random(1))
    assert [t['name'] for t in picked] == ['a0']


# ── end to end on a real (tmp) database ───────────────────────────────────

@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    artists = [(1, 'Daft Punk', 'sp1'), (2, 'Justice', 'sp2'),
               (3, 'QOTSA', 'sp3'), (4, 'Foo Fighters', 'sp4')]
    for aid, name, sp in artists:
        cur.execute("INSERT INTO artists (id, name, spotify_artist_id) VALUES (?,?,?)",
                    (aid, name, sp))
        cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (?,?,?)",
                    (aid * 10, f'{name} Album', aid))
        for t in range(8):
            cur.execute(
                "INSERT INTO tracks (title, artist_id, album_id, file_path, play_count) "
                "VALUES (?,?,?,?,?)",
                (f'{name} Song {t}', aid, aid * 10, f'/m/{aid}-{t}.flac', 10 - t))
    for _aid, name, _sp in artists:
        for i in range(20):
            cur.execute(
                "INSERT INTO listening_history (title, artist, played_at) "
                "VALUES (?,?,datetime('now', ?))",
                (f'{name} Song {i % 8}', name, f'-{i} hours'))
    for src, sim in (('sp1', 'Justice'), ('sp2', 'Daft Punk'),
                     ('sp3', 'Foo Fighters'), ('sp4', 'QOTSA')):
        cur.execute(
            "INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
            "similarity_rank, profile_id) VALUES (?,?,1,1)", (src, sim))
    cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                "similarity_rank, profile_id) VALUES ('sp1','SebastiAn',2,1)")
    cur.execute(
        "INSERT INTO discovery_pool (spotify_track_id, track_name, artist_name, "
        "album_name, popularity, profile_id, track_data_json) VALUES "
        "('dp1','Ross Ross Ross','SebastiAn','Total',80,1,"
        "'{\"name\": \"Ross Ross Ross\", \"artists\": [{\"name\": \"SebastiAn\"}]}')")
    conn.commit()
    conn.close()
    return d


def test_generates_cluster_mixes_with_owned_and_discovery(db):
    payload = generate_daily_mixes(db, today=date(2026, 8, 25))
    mixes = payload['mixes']
    assert len(mixes) == 2
    names = [set(m['artists']) for m in mixes]
    assert {'Daft Punk', 'Justice'} in names
    assert {'QOTSA', 'Foo Fighters'} in names
    electro = mixes[names.index({'Daft Punk', 'Justice'})]
    assert electro['owned_count'] >= 10
    flavor = [t for t in electro['tracks'] if not t.get('owned')]
    assert any(t['artists'][0]['name'] == 'SebastiAn' for t in flavor)
    owned_seq = [t['artists'][0]['name'] for t in electro['tracks'] if t.get('owned')]
    assert all(a != b for a, b in zip(owned_seq, owned_seq[1:], strict=False))


def test_same_day_is_stable_next_day_differs(db):
    a = generate_daily_mixes(db, today=date(2026, 8, 25))
    b = generate_daily_mixes(db, today=date(2026, 8, 25))
    c = generate_daily_mixes(db, today=date(2026, 8, 26))
    def strip(p):
        return [[t['name'] for t in m['tracks']] for m in p['mixes']]
    assert strip(a) == strip(b)
    assert strip(a) != strip(c)


def test_get_or_build_serves_stored_until_stale(db):
    first = get_or_build_daily_mixes(db)
    assert first['mixes']
    again = get_or_build_daily_mixes(db)
    assert again['generated_at'] == first['generated_at']
    forced = get_or_build_daily_mixes(db, force=True)
    assert forced['mixes']


def test_empty_listening_history_yields_no_mixes(tmp_path):
    d = MusicDatabase(str(tmp_path / 'empty.db'))
    payload = generate_daily_mixes(d)
    assert payload['mixes'] == []


def test_owned_durations_remain_milliseconds(db):
    from core.personalized.daily_mixes import _owned_tracks_for
    conn = db._get_connection()
    conn.execute("UPDATE tracks SET duration = 367725 WHERE artist_id = 1")
    conn.commit()
    conn.close()
    tracks = _owned_tracks_for(db, ['Daft Punk'])['daft punk']
    assert tracks
    assert all(track['duration_ms'] == 367725 for track in tracks)


def test_old_duration_payload_is_rebuilt(db):
    from core.personalized.daily_mixes import CURATED_KEY, PAYLOAD_VERSION
    from datetime import datetime, timezone
    db.save_curated_playlist(CURATED_KEY, {
        'v': PAYLOAD_VERSION - 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'mixes': [{'key': 'bad-cached-duration', 'tracks': [{'duration_ms': 367725000}]}],
    }, 1)
    result = get_or_build_daily_mixes(db)
    assert result['v'] == PAYLOAD_VERSION
    assert all(mix['key'] != 'bad-cached-duration' for mix in result['mixes'])


def test_a_blocked_artist_founds_no_mix_and_names_none(db):
    """The subtitle is text the response filter can't reach, so blocks apply
    where the mixes are built."""
    db.add_blocklist_entry(1, 'artist', 'Justice')
    db.add_blocklist_entry(1, 'artist', 'SebastiAn')
    payload = generate_daily_mixes(db, today=date(2026, 8, 25))
    for mix in payload['mixes']:
        assert 'Justice' not in mix['artists']
        assert 'Justice' not in mix['subtitle']
        names = [t['artists'][0]['name'] for t in mix['tracks']]
        assert 'Justice' not in names and 'SebastiAn' not in names


def test_a_new_block_rebuilds_the_stored_mixes(db):
    first = get_or_build_daily_mixes(db)
    assert any('Justice' in m['artists'] for m in first['mixes'])
    assert get_or_build_daily_mixes(db)['generated_at'] == first['generated_at']
    db.add_blocklist_entry(1, 'artist', 'Justice')
    rebuilt = get_or_build_daily_mixes(db)
    assert not any('Justice' in m['artists'] for m in rebuilt['mixes'])


# ── track identity ────────────────────────────────────────────────────────

from core.personalized.daily_mixes import (  # noqa: E402
    allocate,
    fold_credit_seeds,
    in_season,
    is_filler,
    is_fresh,
    pick_discovery,
    renumber,
    session_cooccurrence,
    space_out,
    title_key,
    weave,
)


@pytest.mark.parametrize('a,b', [
    ('DtMF', 'DtMF (Explicit)'),
    ('PRESTIGE', 'PRESTIGE (SPED UP)'),
    ('Song', 'Song - Remastered 2011'),
    ('Song', 'Song (feat. Someone) [Remastered]'),
    ('Song', 'Song ft. Someone'),
    ('Song', 'Song (Slowed + Reverb)'),
])
def test_version_noise_is_the_same_song(a, b):
    assert title_key(a) == title_key(b)


@pytest.mark.parametrize('a,b', [
    ('Electrify', 'Electrify (Luke Million Remix)'),
    ('Song', 'Song (Live)'),
    ('Song', 'Song (Acoustic)'),
])
def test_remixes_and_live_takes_are_different_songs(a, b):
    assert title_key(a) != title_key(b)


def test_filler_is_short_or_a_short_skit():
    assert is_filler('Song', 30_000)
    assert is_filler('Intro', 45_000)
    assert is_filler('Skit', 100_000)
    assert not is_filler('Intro', 200_000)      # a long intro is a song
    assert not is_filler('Song', 0)             # unknown length isn't filler


def test_christmas_waits_for_november():
    xmas = {'name': 'Last Christmas', 'album': {'name': 'Hits'}}
    assert not in_season(xmas, date(2026, 7, 1))
    assert in_season(xmas, date(2026, 12, 1))
    assert in_season({'name': 'Song', 'album': {'name': 'Hits'}}, date(2026, 7, 1))


def test_joined_credits_count_for_their_owned_artist():
    seeds = [{'name': 'Bad Bunny, Jhayco', 'weight': 10}, {'name': 'Bad Bunny', 'weight': 5},
             {'name': 'Tyler, The Creator', 'weight': 3}]
    out = fold_credit_seeds(seeds, {'bad bunny', 'tyler, the creator'})
    assert out == [{'name': 'Bad Bunny', 'weight': 15.0},
                   {'name': 'Tyler, The Creator', 'weight': 3.0}]


# ── co-listening ──────────────────────────────────────────────────────────

def _sessions(groups):
    """one session per group, an hour apart"""
    from datetime import datetime, timedelta
    t = datetime(2026, 1, 1)
    out = []
    for group in groups:
        for j, artist in enumerate(group):
            out.append((artist, t + timedelta(minutes=3 * j)))
        t += timedelta(hours=2)
    return out


def test_artists_you_play_together_score_high():
    cooc = session_cooccurrence(_sessions([['a', 'b']] * 6 + [['c', 'd']] * 6))
    assert cooc[('a', 'b')] == pytest.approx(1.0)
    assert ('a', 'c') not in cooc and ('c', 'a') not in cooc


def test_shuffling_the_whole_library_says_nothing():
    """everything in every session is no signal, not a signal for everything"""
    assert session_cooccurrence(_sessions([['a', 'b', 'c', 'd']] * 10)) == {}


def test_a_pair_needs_a_few_shared_sessions():
    cooc = session_cooccurrence(_sessions([['a', 'b']] * 2 + [['c']] * 8 + [['a'], ['b']]))
    assert cooc == {}


# ── clustering ────────────────────────────────────────────────────────────

def test_a_common_genre_no_longer_glues_unrelated_artists():
    """'pop' on everyone joined katy perry to virtual mage"""
    seeds = [{'name': 'Synth1', 'weight': 10}, {'name': 'Pop1', 'weight': 9},
             {'name': 'Synth2', 'weight': 8}, {'name': 'Pop2', 'weight': 7}]
    similars = {'synth1': [{'name': 'Synth2'}], 'pop1': [{'name': 'Pop2'}]}
    genres = {'synth1': ['Pop', 'Synthwave'], 'synth2': ['Pop', 'Synthwave'],
              'pop1': ['Pop'], 'pop2': ['Pop']}
    clusters = cluster_seeds(seeds, similars, genres)
    assert [set(c['artists']) for c in clusters] == [{'Synth1', 'Synth2'}, {'Pop1', 'Pop2'}]
    assert clusters[0]['genre'] == 'Synthwave'      # the label is what sets it apart


def test_a_mix_cannot_chain_drift():
    """c is tied to e only, so it doesn't fit a,b,d,e as a whole"""
    core = ['A', 'B', 'D', 'E']
    seeds = [{'name': n, 'weight': 10} for n in core] + [{'name': 'C', 'weight': 1}]
    similars = {n.lower(): [{'name': m} for m in core if m != n] for n in core}
    similars['e'].append({'name': 'C'})
    clusters = cluster_seeds(seeds, similars, {})
    assert [set(c['artists']) for c in clusters] == [set(core)]


def test_the_mixes_you_play_most_win_the_slots():
    seeds = [{'name': 'Solo', 'weight': 30}, {'name': 'Y1', 'weight': 15},
             {'name': 'Y2', 'weight': 15}, {'name': 'Y3', 'weight': 15}]
    similars = {'y1': [{'name': 'Y2'}, {'name': 'Y3'}], 'y2': [{'name': 'Y3'}]}
    clusters = cluster_seeds(seeds, similars, {}, max_clusters=1)
    assert set(clusters[0]['artists']) == {'Y1', 'Y2', 'Y3'}


def test_co_listening_alone_can_make_a_mix():
    seeds = [{'name': 'A', 'weight': 10}, {'name': 'B', 'weight': 9}]
    clusters = cluster_seeds(seeds, {}, {}, cooccurrence={('a', 'b'): 0.8})
    assert [set(c['artists']) for c in clusters] == [{'A', 'B'}]


# ── picking ───────────────────────────────────────────────────────────────

def test_allocate_leans_on_the_favourite_without_drowning_the_rest():
    got = allocate({'a': 100, 'b': 25, 'c': 1}, {'a': 10, 'b': 10, 'c': 10}, 13)
    assert sum(got.values()) == 13
    assert got['a'] > got['b'] >= got['c']
    assert got['a'] < 13


def test_allocate_hands_unused_slots_on():
    got = allocate({'a': 100, 'b': 1}, {'a': 2, 'b': 10}, 8)
    assert got == {'a': 2, 'b': 6}


def test_space_out_never_repeats_an_artist_back_to_back_when_it_can_help_it():
    piles = {'a': [{'n': f'a{i}'} for i in range(3)], 'b': [{'n': 'b0'}, {'n': 'b1'}],
             'c': [{'n': 'c0'}]}
    out = [t['n'][0] for t in space_out(piles, ['a', 'b', 'c'], random.Random(3))]
    assert len(out) == 6
    assert all(x != y for x, y in zip(out, out[1:], strict=False))


def test_space_out_still_plays_everything_when_it_cannot():
    out = space_out({'a': [{'n': 1}, {'n': 2}], 'b': []}, ['a', 'b'], random.Random(1))
    assert [t['n'] for t in out] == [1, 2]


def test_interleave_owned_caps_each_artist():
    by_artist = {'a': [{'name': f'a{i}', 'play_count': 9} for i in range(10)],
                 'b': [{'name': f'b{i}', 'play_count': 1} for i in range(10)]}
    picked = interleave_owned(by_artist, ['a', 'b'], 20, random.Random(1),
                              artist_weights={'a': 1000, 'b': 1}, cap=6)
    assert sum(1 for t in picked if t['name'][0] == 'a') == 6


def test_recently_served_tracks_usually_sit_out():
    rows = [{'name': 'old', 'play_count': 5, '_key': 'x|old'},
            {'name': 'new', 'play_count': 5, '_key': 'x|new'}]
    firsts = [interleave_owned({'x': rows}, ['x'], 1, random.Random(i),
                               recent={'x|old'})[0]['name'] for i in range(60)]
    assert firsts.count('new') >= 40


def test_numbers_follow_their_artists_from_day_to_day():
    previous = [{'key': 'daily_mix_1', 'artists': ['X', 'Y']},
                {'key': 'daily_mix_2', 'artists': ['P', 'Q']}]
    today = [{'artists': ['P', 'Q', 'R']}, {'artists': ['X', 'Y']}, {'artists': ['New']}]
    assert renumber(today, previous) == [2, 1, 3]


def test_numbers_are_always_one_to_n():
    previous = [{'key': 'daily_mix_5', 'artists': ['X']}]
    assert renumber([{'artists': ['X']}, {'artists': ['Z']}], previous) == [1, 2]


def test_weave_spreads_discovery_and_never_opens_on_it():
    owned = [{'owned': True, 'i': i} for i in range(20)]
    disc = [{'owned': False, 'i': i} for i in range(4)]
    out = weave(owned, disc)
    assert len(out) == 24 and out[0]['owned']
    spots = [i for i, t in enumerate(out) if not t['owned']]
    assert all(b - a >= 3 for a, b in zip(spots, spots[1:], strict=False))


def _disc(artist, n, genres=()):
    return [{'name': f'{artist}{i}', 'artists': [{'name': artist}], '_key': f'{artist}|{i}',
             '_popularity': 50, '_genres': list(genres)} for i in range(n)]


def test_discovery_spreads_across_similar_artists():
    """every slot used to go to the one artist with the most popular tracks"""
    cands = {'a': _disc('a', 5), 'b': _disc('b', 5), 'c': _disc('c', 5)}
    out = pick_discovery(cands, ['a', 'b', 'c'], set(), 3, random.Random(1), set(), set())
    assert sorted(t['artists'][0]['name'] for t in out) == ['a', 'b', 'c']
    more = pick_discovery(cands, ['a', 'b', 'c'], set(), 5, random.Random(1), set(), set())
    assert max(sum(1 for t in more if t['artists'][0]['name'] == x) for x in 'abc') == 2


def test_a_same_named_stranger_is_skipped():
    """an outlaw-country 'silo' doesn't belong in an electro mix"""
    cands = {'silo': _disc('silo', 3, ['outlaw country']), 'ok': _disc('ok', 3, ['house'])}
    out = pick_discovery(cands, ['silo', 'ok'], {'Electronic/Dance'}, 4, random.Random(1),
                         set(), set())
    assert {t['artists'][0]['name'] for t in out} == {'ok'}


def test_discovery_already_used_by_another_mix_is_skipped():
    cands = {'a': _disc('a', 2)}
    out = pick_discovery(cands, ['a'], set(), 2, random.Random(1), set(), {'a|0'})
    assert [t['_key'] for t in out] == ['a|1']


# ── the day ───────────────────────────────────────────────────────────────

def test_a_payload_is_fresh_only_on_its_own_day():
    from datetime import datetime, timezone
    from core.personalized.daily_mixes import PAYLOAD_VERSION
    stored = {'mixes': [{}], 'v': PAYLOAD_VERSION, 'day': '2026-10-27',
              'blocked': 'b', 'taste': 't',
              'generated_at': datetime.now(timezone.utc).isoformat()}
    assert is_fresh(stored, date(2026, 10, 27), 'b', 't')
    assert not is_fresh(stored, date(2026, 10, 28), 'b', 't')
    assert not is_fresh(stored, date(2026, 10, 27), 'other', 't')
    assert not is_fresh(dict(stored, day=None), date(2026, 10, 27), 'b', 't')


def test_a_new_day_builds_new_mixes(db):
    first = get_or_build_daily_mixes(db, today=date(2026, 8, 25))
    assert first['day'] == '2026-08-25'
    same = get_or_build_daily_mixes(db, today=date(2026, 8, 25))
    assert same['generated_at'] == first['generated_at'] and same['day'] == first['day']
    nxt = get_or_build_daily_mixes(db, today=date(2026, 8, 26))
    assert nxt['day'] == '2026-08-26'
    assert '2026-08-25' in nxt['served'] and '2026-08-26' in nxt['served']


def test_internal_fields_never_reach_the_payload(db):
    payload = generate_daily_mixes(db, today=date(2026, 8, 25))
    for mix in payload['mixes']:
        for t in mix['tracks']:
            assert not [k for k in t if k.startswith('_')]
            assert 'play_count' not in t


# ── end to end on richer libraries ────────────────────────────────────────

def _library(tmp_path, name, artists, plays_per_artist=20, similar=(), pool=()):
    """artists: [(id, name, sp, n_tracks, genres)]"""
    import json as _json
    d = MusicDatabase(str(tmp_path / name))
    conn = d._get_connection()
    cur = conn.cursor()
    for aid, aname, sp, n, genres in artists:
        cur.execute("INSERT INTO artists (id, name, spotify_artist_id, genres) VALUES (?,?,?,?)",
                    (aid, aname, sp, _json.dumps(list(genres)) if genres else None))
        cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (?,?,?)",
                    (aid * 10, f'{aname} Album', aid))
        for t in range(n):
            # tracks.id is the media server's id (text), never null on a real install
            cur.execute(
                "INSERT INTO tracks (id, title, artist_id, album_id, file_path, play_count, duration) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(aid * 1000 + t), f'{aname} Song {t}', aid, aid * 10, f'/m/{aid}-{t}.flac',
                 n - t, 200_000))
    for _aid, aname, _sp, _n, _g in artists:
        for i in range(plays_per_artist if not isinstance(plays_per_artist, dict)
                       else plays_per_artist.get(aname, 0)):
            cur.execute("INSERT INTO listening_history (title, artist, played_at) "
                        "VALUES (?,?,datetime('now', ?))", (f'{aname} Song 0', aname, f'-{i} hours'))
    for edge in similar:
        src, sim, rank = (*edge, 1)[:3]
        cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                    "similarity_rank, profile_id) VALUES (?,?,?,1)", (src, sim, rank))
    for i, row in enumerate(pool):
        artist, title, pop, genres = row[:4]
        lead = row[4] if len(row) > 4 else artist    # who the track json credits first
        cur.execute(
            "INSERT INTO discovery_pool (spotify_track_id, track_name, artist_name, album_name, "
            "popularity, profile_id, artist_genres, track_data_json, duration_ms) "
            "VALUES (?,?,?,?,?,1,?,?,200000)",
            (f'dp{i}', title, artist, 'A', pop, _json.dumps(list(genres)),
             _json.dumps({'name': title, 'artists': [{'name': lead}]})))
    conn.commit()
    conn.close()
    return d


def test_one_copy_of_each_song(db):
    conn = db._get_connection()
    conn.execute("INSERT INTO albums (id, title, artist_id) VALUES (99, 'Deluxe', 1)")
    for title in ('Daft Punk Song 0', 'Daft Punk Song 0 (Remastered 2011)'):
        conn.execute("INSERT INTO tracks (title, artist_id, album_id, file_path, play_count) "
                     "VALUES (?,1,99,'/m/dup.flac',50)", (title,))
    conn.commit()
    conn.close()
    from core.personalized.daily_mixes import track_key
    for mix in generate_daily_mixes(db, today=date(2026, 8, 25))['mixes']:
        keys = [track_key(t['artists'][0]['name'], t['name']) for t in mix['tracks']]
        assert len(keys) == len(set(keys))


def test_christmas_songs_stay_out_of_a_july_mix(db):
    conn = db._get_connection()
    conn.execute("INSERT INTO tracks (title, artist_id, album_id, file_path, play_count) "
                 "VALUES ('Christmas Robot', 1, 10, '/m/x.flac', 999)")
    conn.commit()
    conn.close()
    for mix in generate_daily_mixes(db, today=date(2026, 7, 1))['mixes']:
        assert 'Christmas Robot' not in [t['name'] for t in mix['tracks']]


def test_a_big_lone_artist_gets_a_mix_with_its_owned_neighbours(tmp_path):
    d = _library(tmp_path, 'bb.db', [
        (1, 'Bad Bunny', 'bb', 30, ['Urbano latino']),
        (2, 'Anuel AA', 'an', 10, ['Urbano latino']),   # owned, never played
        (3, 'Daft Punk', 'dp', 10, ['Electro']),
        (4, 'Justice', 'ju', 10, ['Electro']),
    ], plays_per_artist={'Bad Bunny': 40, 'Daft Punk': 20, 'Justice': 20},
        similar=[('bb', 'Anuel AA'), ('dp', 'Justice'), ('ju', 'Daft Punk')])
    mixes = generate_daily_mixes(d, today=date(2026, 8, 25))['mixes']
    bb = next(m for m in mixes if m['artists'] == ['Bad Bunny'])
    names = [t['artists'][0]['name'] for t in bb['tracks']]
    assert 'Anuel AA' in names
    assert bb['subtitle'].startswith('Bad Bunny, Anuel AA')
    # a short mix leans on the neighbours (up to the per-artist cap) before
    # it lets its own artist past the cap
    assert names.count('Anuel AA') == 6
    assert names[0] == 'Bad Bunny'


def test_a_served_track_is_less_likely_tomorrow(tmp_path):
    from datetime import timedelta
    d = _library(tmp_path, 'big.db', [
        (1, 'Daft Punk', 'dp', 10, ['Electro']), (2, 'Justice', 'ju', 10, ['Electro'])],
        similar=[('dp', 'Justice'), ('ju', 'Daft Punk')])
    from core.personalized.daily_mixes import track_key

    def keys(p):
        return {track_key(t['artists'][0]['name'], t['name'])
                for m in p['mixes'] for t in m['tracks']}

    def build(day, previous=None):
        return generate_daily_mixes(d, today=day, mix_size=8, discovery_per_mix=0,
                                    previous=previous)

    blind = told = 0
    for n in range(10):
        day = date(2026, 8, 1) + timedelta(days=n)
        yesterday = build(day)
        tomorrow = day + timedelta(days=1)
        blind += len(keys(build(tomorrow)) & keys(yesterday))
        told += len(keys(build(tomorrow, yesterday)) & keys(yesterday))
    assert told < blind * 0.75


def test_a_profiles_own_plays_pick_its_favourites(tmp_path):
    from core.personalized.daily_mixes import _owned_tracks_for
    d = _library(tmp_path, 'pp.db', [(1, 'Daft Punk', 'dp', 3, [])], plays_per_artist=0)
    conn = d._get_connection()
    tid = conn.execute("SELECT id FROM tracks WHERE title = 'Daft Punk Song 2'").fetchone()[0]
    for i in range(4):
        conn.execute("INSERT INTO listening_history (title, artist, played_at, db_track_id) "
                     "VALUES ('Daft Punk Song 2', 'Daft Punk', datetime('now', ?), ?)",
                     (f'-{i} hours', tid))
    conn.commit()
    conn.close()
    counts = {t['name']: t['play_count'] for t in _owned_tracks_for(d, ['Daft Punk'], 1)['daft punk']}
    # the server's counts (3, 2, 1) are ignored once the pile has its own
    assert counts == {'Daft Punk Song 0': 0, 'Daft Punk Song 1': 0, 'Daft Punk Song 2': 4}


def test_no_listening_history_falls_back_to_the_library(tmp_path):
    d = _library(tmp_path, 'fresh.db', [
        (1, 'Daft Punk', 'dp', 10, ['Electro']), (2, 'Justice', 'ju', 10, ['Electro'])],
        plays_per_artist=0, similar=[('dp', 'Justice')])
    mixes = generate_daily_mixes(d, today=date(2026, 8, 25))['mixes']
    assert mixes and {'Daft Punk', 'Justice'} <= set(mixes[0]['artists'])


def test_discovery_in_a_mix_comes_from_several_artists(tmp_path):
    pool = [(a, f'{a} hit {i}', 90 - i, ['house']) for a in ('SebastiAn', 'Kavinsky')
            for i in range(6)]
    d = _library(tmp_path, 'disc.db', [
        (1, 'Daft Punk', 'dp', 20, ['Electro']), (2, 'Justice', 'ju', 20, ['Electro'])],
        similar=[('dp', 'Justice'), ('ju', 'Daft Punk'), ('dp', 'SebastiAn'), ('ju', 'Kavinsky')],
        pool=pool)
    mix = generate_daily_mixes(d, today=date(2026, 8, 25))['mixes'][0]
    flavour = [t['artists'][0]['name'] for t in mix['tracks'] if not t.get('owned')]
    assert set(flavour) == {'SebastiAn', 'Kavinsky'}
    assert mix['tracks'][0].get('owned')     # a mix opens on something you own


def test_one_version_of_a_song_per_mix():
    from core.personalized.daily_mixes import song_key
    assert song_key('CLTX', 'Deadzone') == song_key('CLTX', 'Deadzone (Denis Dekay Remix)')
    assert song_key('A', 'Song - Live at Wembley') == song_key('A', 'Song')
    assert song_key('A', 'Song') != song_key('B', 'Song')


def _electro(tmp_path, name, similar, pool):
    return _library(tmp_path, name, [
        (1, 'Daft Punk', 'dp', 20, ['Electro']), (2, 'Justice', 'ju', 20, ['Electro'])],
        similar=[('dp', 'Justice'), ('ju', 'Daft Punk'), *similar], pool=pool)


def test_a_featured_credit_is_not_discovery_for_its_lead(tmp_path):
    """the pool files a feature under the featured artist too: an owned
    artist's own song showed up as 'discovery', and a 'silo' nobody asked for"""
    d = _electro(tmp_path, 'feat.db', [('dp', 'SebastiAn')], [
        ('SebastiAn', 'Theirs', 90, ['house'], 'Justice'),     # owned lead
        ('SebastiAn', 'Stranger', 80, ['house'], 'Silo'),       # someone else leads
        ('SebastiAn', 'Ross Ross Ross', 70, ['house']),
    ])
    mix = generate_daily_mixes(d, today=date(2026, 8, 25))['mixes'][0]
    flavour = [(t['artists'][0]['name'], t['name']) for t in mix['tracks'] if not t.get('owned')]
    assert flavour == [('SebastiAn', 'Ross Ross Ross')]


def test_discovery_needs_more_than_one_far_endorsement(tmp_path):
    d = _electro(tmp_path, 'floor.db', [('dp', 'SebastiAn'), ('ju', 'SebastiAn'),
                                        ('dp', 'Faraway', 10)], [
        ('SebastiAn', 'Ross Ross Ross', 70, ['house']),
        ('Faraway', 'Far Song', 99, ['house']),
    ])
    mix = generate_daily_mixes(d, today=date(2026, 8, 25))['mixes'][0]
    flavour = {t['artists'][0]['name'] for t in mix['tracks'] if not t.get('owned')}
    assert flavour == {'SebastiAn'}


def test_a_thin_mix_hands_its_slot_to_the_next_one(tmp_path):
    """a lone artist with eight songs and nobody next to it played the same
    eight songs back to back every day"""
    d = _library(tmp_path, 'thin.db', [
        (1, 'Kylie Cantrall', 'kc', 8, []),
        (2, 'Daft Punk', 'dp', 10, ['Electro']), (3, 'Justice', 'ju', 10, ['Electro'])],
        plays_per_artist={'Kylie Cantrall': 60, 'Daft Punk': 20, 'Justice': 20},
        similar=[('dp', 'Justice'), ('ju', 'Daft Punk')])
    mixes = generate_daily_mixes(d, today=date(2026, 8, 25), max_mixes=1)['mixes']
    assert [set(m['artists']) for m in mixes] == [{'Daft Punk', 'Justice'}]
    assert mixes[0]['key'] == 'daily_mix_1'


def test_a_one_artist_mix_with_nobody_beside_it_is_not_a_mix(tmp_path):
    d = _library(tmp_path, 'solo.db', [(1, 'Kylie Cantrall', 'kc', 30, [])],
                 plays_per_artist=60)
    assert generate_daily_mixes(d, today=date(2026, 8, 25))['mixes'] == []


def test_a_mix_too_short_to_rotate_is_not_a_mix(tmp_path):
    d = _library(tmp_path, 'short.db', [
        (1, 'Daft Punk', 'dp', 4, ['Electro']), (2, 'Justice', 'ju', 4, ['Electro'])],
        similar=[('dp', 'Justice'), ('ju', 'Daft Punk')])
    assert generate_daily_mixes(d, today=date(2026, 8, 25))['mixes'] == []
