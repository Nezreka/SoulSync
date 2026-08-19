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


def _stub(name='Stub', artist='Stub Artist'):
    """matched_data as the wing-it fallback actually writes it — the `wing_it_`
    id prefix is what marks the row as still-a-guess."""
    return {'id': 'wing_it_ab12cd34', 'name': name, 'artists': [{'name': artist}],
            'album': {'name': ''}, 'source': 'wing_it_fallback'}


WING_IT = {'discovered': True, 'provider': 'wing_it_fallback', 'confidence': 0,
           'wing_it_fallback': True, 'matched_data': _stub()}
# A track that wing-it'd on one pass and matched for real on a later one. The
# writer only ever SETS wing_it_fallback and extra_data is merged, so the stale
# flag survives — but matched_data was replaced wholesale, stub id and all.
WING_IT_AUTO_RESOLVED = {'discovered': True, 'provider': 'spotify', 'confidence': 0.99,
                         'wing_it_fallback': True,
                         'matched_data': {'id': '2cb10pqT6p291kduzk3jvO', 'name': 'Real Match'}}
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


def test_stale_flag_on_a_real_match_is_not_attention(tmp_path):
    """A track that wing-it'd once and later matched at 0.99 must leave the pool.

    Discovery re-runs every sync and the flag is never cleared, so keying purely
    on wing_it_fallback reports resolved tracks as unverified guesses forever —
    on the live library that was 317 of 445 rows.
    """
    db = MusicDatabase(database_path=str(tmp_path / "stale.db"))
    pid = _playlist(db, 'Liked Music')
    _track(db, pid, 0, 'Still A Guess', 'Nobody', WING_IT)
    _track(db, pid, 1, 'Semi-Charmed Life', 'Dance Gavin Dance', WING_IT_AUTO_RESOLVED)

    attention = db.get_wing_it_pool(profile_id=1)
    assert [t['track_name'] for t in attention] == ['Still A Guess']
    # The auto-resolved track leaves the pool entirely — it must not count as
    # 'matched' either. That label is earned only by a MANUAL fix
    # (wing_it_fallback AND manual_match); an auto-resolve on a later
    # discovery pass carries no manual_match key at all.
    assert db.get_wing_it_pool_stats(profile_id=1) == {'wing_it': 1, 'matched': 0}


def test_underscore_in_prefix_is_matched_literally(tmp_path):
    """`_` is a single-char wildcard in SQL LIKE — without ESCAPE, `wing_it_`
    would also match ids like `wingXitY`, letting non-stubs back into the pool."""
    db = MusicDatabase(database_path=str(tmp_path / "esc.db"))
    pid = _playlist(db, 'Edge Cases')
    _track(db, pid, 0, 'Impostor', 'Nobody', {
        'discovered': True, 'wing_it_fallback': True,
        'matched_data': {'id': 'wingXitY0001', 'name': 'Impostor'}})

    assert db.get_wing_it_pool(profile_id=1) == []


def test_stub_id_outside_matched_data_does_not_count(tmp_path):
    """The predicate is scoped to $.matched_data.id specifically, not "any 'id'
    key anywhere in the document" — a wing_it_-prefixed id sitting elsewhere in
    extra_data must not make an otherwise-real match look like a stub."""
    db = MusicDatabase(database_path=str(tmp_path / "scope.db"))
    pid = _playlist(db, 'Edge Cases')
    _track(db, pid, 0, 'Not A Stub', 'Nobody', {
        'discovered': True, 'wing_it_fallback': True,
        'id': 'wing_it_ab12cd34',  # stray top-level key, not matched_data.id
        'matched_data': {'name': 'no id here'}})

    assert db.get_wing_it_pool(profile_id=1) == []


def test_malformed_extra_data_on_one_row_does_not_blank_the_pool(tmp_path):
    """SQLite's json_extract raises on a malformed value, and that error is
    for the whole statement, not just the offending row — so a naive
    json_extract-only predicate would let one corrupt row hide every other
    track behind the outer try/except's empty-list fallback. json_valid() as
    the leading clause is what keeps the failure scoped to that one row,
    matching what the substring-LIKE predicate this replaced did for free."""
    db = MusicDatabase(database_path=str(tmp_path / "corrupt.db"))
    pid = _playlist(db, 'Edge Cases')
    _track(db, pid, 0, 'Still Fine', 'Nobody', WING_IT)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name, extra_data) "
            "VALUES (?,?,?,?,?)",
            (pid, 1, 'Corrupted Row', 'Nobody', 'not valid json {{{'))
        conn.commit()

    attention = db.get_wing_it_pool(profile_id=1)
    assert [t['track_name'] for t in attention] == ['Still Fine']
    assert db.get_wing_it_pool_stats(profile_id=1) == {'wing_it': 1, 'matched': 0}


def test_null_extra_data_is_excluded_not_an_error(tmp_path):
    """json_extract on a NULL/absent extra_data must not raise — the row is
    simply not a wing-it track."""
    db = MusicDatabase(database_path=str(tmp_path / "null.db"))
    pid = _playlist(db, 'Edge Cases')
    _track(db, pid, 0, 'No Data', 'Nobody', None)

    assert db.get_wing_it_pool(profile_id=1) == []
    assert db.get_wing_it_pool_stats(profile_id=1) == {'wing_it': 0, 'matched': 0}
