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


def test_singletons_merge_into_one_mixed_cluster():
    seeds = [{'name': n, 'weight': 10 - i} for i, n in enumerate('ABCD')]
    clusters = cluster_seeds(seeds, {}, {})   # nothing connects
    assert len(clusters) == 1
    assert set(clusters[0]['artists']) == {'A', 'B', 'C', 'D'}


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
        cur.execute(
            "INSERT INTO lib2_artists (id, name, name_key, spotify_id) VALUES (?,?,?,?)",
            (aid, name, name.lower(), sp))
        cur.execute(
            "INSERT INTO lib2_albums (id, title, primary_artist_id) VALUES (?,?,?)",
            (aid * 10, f'{name} Album', aid))
        for t in range(8):
            cur.execute(
                "INSERT INTO lib2_tracks (title, album_id, play_count) VALUES (?,?,?)",
                (f'{name} Song {t}', aid * 10, 10 - t))
            cur.execute(
                "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
                "VALUES (?,?,1,'active')", (cur.lastrowid, f'/m/{aid}-{t}.flac'))
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
