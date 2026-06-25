"""Wing It Pool query — surfaces tracks Wing It auto-matched (best-effort guesses).

Wing-it tracks are persisted as the ``wing_it_fallback: true`` flag on a mirrored track's
extra_data and count as 'discovered', so the Discovery Pool's failed list excludes them. The
Wing It Pool is the only surface that lists them. It must: include unverified wing-it tracks,
exclude ones the user already manually matched, scope by playlist + profile, and never include
plain matched/failed tracks.
"""

from __future__ import annotations

import json

from database.music_database import MusicDatabase


def _playlist(db, name, profile_id=1, source_id='pl1'):
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO mirrored_playlists (source, source_playlist_id, name, profile_id) VALUES (?,?,?,?)",
            ('spotify', source_id, name, profile_id))
        conn.commit()
        return cur.lastrowid


def _track(db, playlist_id, pos, name, artist, extra):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name, extra_data) "
            "VALUES (?,?,?,?,?)",
            (playlist_id, pos, name, artist, json.dumps(extra) if extra is not None else None))
        conn.commit()


WING_IT = {'discovered': True, 'provider': 'wing_it_fallback', 'confidence': 0, 'wing_it_fallback': True}
# A resolved wing-it track: /fix MERGES extra_data, so wing_it_fallback survives alongside the
# new manual_match flag — that pairing is what marks it resolved (no separate marker needed).
WING_IT_RESOLVED = {'discovered': True, 'provider': 'spotify', 'confidence': 1.0,
                    'wing_it_fallback': True, 'manual_match': True,
                    'matched_data': {'name': 'Dopamine (Real)'}}
MATCHED = {'discovered': True, 'provider': 'spotify', 'confidence': 0.95}
FAILED = {'discovery_attempted': True, 'discovered': False}


def test_lists_only_unverified_wing_it_tracks(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "w.db"))
    pid = _playlist(db, 'Liked Songs')
    _track(db, pid, 0, 'Orbital Trans', 'Yoga Mao', WING_IT)              # unverified -> attention
    _track(db, pid, 1, 'Dopamine', 'Rvdical the Kid', WING_IT_RESOLVED)  # resolved -> matched list
    _track(db, pid, 2, 'Real Match', 'Some Artist', MATCHED)             # normal match -> neither
    _track(db, pid, 3, 'Lost Track', 'Nobody', FAILED)                   # failed -> Discovery Pool's

    attention = db.get_wing_it_pool(profile_id=1)
    assert [t['track_name'] for t in attention] == ['Orbital Trans']
    assert attention[0]['artist_name'] == 'Yoga Mao'
    assert attention[0]['playlist_name'] == 'Liked Songs'

    resolved = db.get_wing_it_pool(profile_id=1, resolved=True)
    assert [t['track_name'] for t in resolved] == ['Dopamine']

    assert db.get_wing_it_pool_stats(profile_id=1) == {'wing_it': 1, 'matched': 1}


def test_scopes_by_playlist_and_profile(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "w2.db"))
    a = _playlist(db, 'Playlist A', profile_id=1, source_id='a')
    b = _playlist(db, 'Playlist B', profile_id=1, source_id='b')
    other = _playlist(db, 'Other Profile', profile_id=2, source_id='c')
    _track(db, a, 0, 'A Song', 'AA', WING_IT)
    _track(db, b, 0, 'B Song', 'BB', WING_IT)
    _track(db, other, 0, 'C Song', 'CC', WING_IT)

    assert {t['track_name'] for t in db.get_wing_it_pool(profile_id=1)} == {'A Song', 'B Song'}
    assert [t['track_name'] for t in db.get_wing_it_pool(playlist_id=a)] == ['A Song']
    assert db.get_wing_it_pool_stats(profile_id=1) == {'wing_it': 2, 'matched': 0}


def test_empty_when_no_wing_it(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "w3.db"))
    pid = _playlist(db, 'Clean')
    _track(db, pid, 0, 'Matched', 'X', MATCHED)
    assert db.get_wing_it_pool(profile_id=1) == []
    assert db.get_wing_it_pool_stats(profile_id=1) == {'wing_it': 0, 'matched': 0}
