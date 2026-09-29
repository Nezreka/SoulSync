"""mood mixes: chill / focus / energy / feel good / late night, from owned tracks.

sept 29 2026: discover had a 'flow moods' bar (energizing, chill & lo-fi,
focus, deep cuts, discovery roulette) whose pills only scrolled the page.
these are real mixes built from the last.fm tags and audiodb moods already
on the library's albums.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from core.discovery import moods
from database.music_database import MusicDatabase

TODAY = date(2026, 9, 29)
CHILL = next(m for m in moods.MOODS if m['key'] == 'chill')
ENERGY = next(m for m in moods.MOODS if m['key'] == 'energy')


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


_ids = iter(range(1, 10_000))


def _seed(db, artist, album, tags=None, mood=None, tracks=4, plays=0, owned=True):
    artist_id, album_id = f'ar{next(_ids)}', f'al{next(_ids)}'
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO artists (id, name) VALUES (?, ?)", (artist_id, artist))
        cur.execute("INSERT INTO albums (id, artist_id, title, lastfm_tags, mood) VALUES (?, ?, ?, ?, ?)",
                    (album_id, artist_id, album, json.dumps(tags) if tags is not None else None, mood))
        for n in range(tracks):
            cur.execute(
                "INSERT INTO tracks (id, artist_id, album_id, title, duration, play_count, file_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f'tr{next(_ids)}', artist_id, album_id, f'{album} {n}', 200000, plays,
                 f'/music/{artist}/{album}/{n}.flac' if owned else None))
        conn.commit()


# ── scoring ──────────────────────────────────────────────────────────────────

def test_an_albums_leading_tags_count_most():
    assert moods.mood_score(['chillout', 'electronic'], None, CHILL) == 1.0
    assert moods.mood_score(['electronic', 'chillout'], None, CHILL) == 0.5
    assert moods.mood_score(['electronic'], None, CHILL) == 0.0


def test_only_the_top_five_tags_describe_an_album():
    tags = ['rock', 'pop', 'indie', 'alternative', 'british', 'chill']
    assert moods.mood_score(tags, None, CHILL) == 0.0


def test_an_audiodb_mood_counts_like_a_top_tag():
    assert moods.mood_score([], 'Energetic', ENERGY) == 1.0


def test_anger_is_not_energy():
    # on real data 'angry' and 'aggressive' were metal, which broke a dance mix
    assert moods.mood_score([], 'Angry', ENERGY) == 0.0


def test_an_audiobook_is_never_a_mood():
    assert moods.mood_score(['chillout', 'audiobook'], 'Relaxed', CHILL) == 0.0


def test_interludes_and_intros_are_not_songs():
    assert not moods.is_song('Interlude')
    assert not moods.is_song('Mysterious (Intro)')
    assert not moods.is_song('Sugar Man (Demo Song)')
    assert moods.is_song('Introduction To The Moon') is True   # a word, not the tag
    assert moods.is_song('Nightcall')


# ── picking ──────────────────────────────────────────────────────────────────

def _cand(artist, album, title, score=1.0, plays=0):
    return {'artist': artist, 'album': album, 'title': title, 'score': score, 'plays': plays}


def test_the_pick_spreads_across_artists_and_albums():
    cands = [_cand('A', f'al{n % 3}', f't{n}') for n in range(30)]
    cands += [_cand(f'B{n}', 'x', 'y') for n in range(10)]
    picked = moods.pick_tracks(cands, 50, 'seed')
    assert sum(1 for c in picked if c['artist'] == 'A') == moods.PER_ARTIST
    by_album = {}
    for c in picked:
        by_album[(c['artist'], c['album'])] = by_album.get((c['artist'], c['album']), 0) + 1
    assert max(by_album.values()) <= moods.PER_ALBUM


def test_the_same_day_gives_the_same_mix_and_a_new_day_a_new_one():
    cands = [_cand(f'A{n}', 'x', f't{n}') for n in range(200)]
    first = [c['title'] for c in moods.pick_tracks(cands, 20, 'day1')]
    assert first == [c['title'] for c in moods.pick_tracks(cands, 20, 'day1')]
    assert first != [c['title'] for c in moods.pick_tracks(cands, 20, 'day2')]


def test_what_you_play_tends_to_lead():
    cands = [_cand(f'A{n}', 'x', f'cold{n}') for n in range(100)]
    cands += [_cand(f'H{n}', 'x', f'hot{n}', plays=500) for n in range(10)]
    wins = 0
    for day in range(20):
        top = moods.pick_tracks(cands, 10, f'd{day}')
        wins += sum(1 for c in top if c['title'].startswith('hot'))
    # ten hot tracks out of 110: blind luck would put ~18 in 200 slots
    assert wins > 60


# ── the whole thing, on a real database ──────────────────────────────────────

def test_mood_mixes_are_owned_tracks_from_albums_tagged_that_mood(db):
    for n in range(8):
        _seed(db, f'Chill {n}', f'Calm {n}', tags=['chillout', 'downtempo'])
    _seed(db, 'Loud', 'Bangers', tags=['edm', 'dance'])
    _seed(db, 'Ghost', 'Not Owned', tags=['chillout'], owned=False)
    out = moods.generate_mood_mixes(db, 1, today=TODAY)
    by_key = {m['key']: m for m in out['mixes']}
    assert 'mood_chill' in by_key
    names = {t['artists'][0]['name'] for t in by_key['mood_chill']['tracks']}
    assert names == {f'Chill {n}' for n in range(8)}
    # every track is one you own, so the mix plays straight away
    assert all(t['owned'] for t in by_key['mood_chill']['tracks'])
    # too few energy tracks to be worth a card
    assert 'mood_energy' not in by_key


def test_a_blocked_artist_never_turns_up_in_a_mood(db):
    for n in range(10):
        _seed(db, f'Chill {n}', f'Calm {n}', tags=['chillout'])
    db.add_blocklist_entry(1, 'artist', 'Chill 0')
    out = moods.generate_mood_mixes(db, 1, today=TODAY)
    chill = next(m for m in out['mixes'] if m['key'] == 'mood_chill')
    assert 'Chill 0' not in {t['artists'][0]['name'] for t in chill['tracks']}


def test_the_stored_mixes_are_reused_until_a_block_changes_them(db, monkeypatch):
    for n in range(8):
        _seed(db, f'Chill {n}', f'Calm {n}', tags=['chillout'])
    first = moods.get_or_build_mood_mixes(db, 1)
    calls = []
    real = moods.generate_mood_mixes
    monkeypatch.setattr(moods, 'generate_mood_mixes',
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    assert moods.get_or_build_mood_mixes(db, 1)['generated_at'] == first['generated_at']
    assert calls == []
    db.add_blocklist_entry(1, 'artist', 'Chill 1')
    moods.get_or_build_mood_mixes(db, 1)
    assert calls == [1]


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_moods_endpoint_serves_the_profiles_mixes(monkeypatch):
    from flask import Flask

    import api.discover_routes as routes

    seen = {}

    def fake(database, profile_id, force=False):
        seen.update(profile=profile_id, force=force)
        return {'mixes': [{'key': 'mood_chill'}], 'generated_at': 'now'}

    monkeypatch.setattr('core.discovery.moods.get_or_build_mood_mixes', fake)
    monkeypatch.setattr(routes, 'get_database', lambda: object())
    monkeypatch.setattr(routes, 'get_current_profile_id', lambda: 2)
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    body = app.test_client().get('/api/discover/moods?refresh=1').get_json()
    assert body == {'success': True, 'mixes': [{'key': 'mood_chill'}], 'generated_at': 'now'}
    assert seen == {'profile': 2, 'force': True}


def test_moods_are_a_layout_section_in_for_you():
    from core.discovery import layout
    assert layout.DEFAULT_ZONE['mood-mixes-section'] == 'for-you'
    assert layout.SECTION_IDS.index('mood-mixes-section') == 1
