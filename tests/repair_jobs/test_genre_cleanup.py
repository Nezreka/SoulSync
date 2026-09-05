"""#1057 — clean stored genres after strict genre filtering is enabled.

The whitelist gated NEW enrichment only; genres stored before the toggle stayed
dirty and every downstream surface (server sync, Write Tags) reproduced them.
Two-part fix under test here:
  * the Genre Tag Cleanup repair job (scan + fix, removal-only)
  * the tag-write seam filter in _build_library_tag_db_data
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.repair_jobs.base import JobContext, JobResult
from core.repair_jobs.genre_cleanup import GenreCleanupJob, parse_stored_genres
from database.music_database import MusicDatabase


class _Cfg:
    """Fake config: strict mode + a tiny whitelist."""

    def __init__(self, enabled=True, genres=None):
        self._d = {
            'genre_whitelist.enabled': enabled,
            'genre_whitelist.genres': genres or ['Rock', 'Jazz'],
        }

    def get(self, key, default=None):
        return self._d.get(key, default)


@pytest.fixture()
def db():
    """Library-v2 rows. The job's catalogue boundary is lib2 (T-11) — reading
    artists/albums would have meant scanning the shrinking legacy projection, 9 of
    273 albums in the user's real library."""
    d = MusicDatabase(os.path.join(tempfile.mkdtemp(), 't.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists (id, name, sort_name, genres, image_url) "
                "VALUES (1, 'Dirty Artist', 'Dirty Artist', ?, 'art.jpg')",
                (json.dumps(['Rock', 'downtempo fusion junk', 'seen live']),))
    cur.execute("INSERT INTO lib2_artists (id, name, sort_name, genres) "
                "VALUES (2, 'Clean Artist', 'Clean Artist', ?)",
                (json.dumps(['Jazz']),))
    cur.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, album_type, "
                "genres, image_url) VALUES (1, 1, 'Dirty Album', 'album', ?, 'alb.jpg')",
                (json.dumps(['favorites', 'Rock']),))
    conn.commit()
    conn.close()
    return d


def _ctx(db, cfg, findings):
    return JobContext(
        db=db, transfer_folder='/tmp', config_manager=cfg,
        create_finding=lambda **kw: findings.append(kw) or True,
    )


# ── parse_stored_genres ──────────────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    (None, []), ('', []), ('[]', []),
    ('["Rock", "Jazz"]', ['Rock', 'Jazz']),
    ('Rock, Jazz , ', ['Rock', 'Jazz']),
    (['Rock', ' Jazz '], ['Rock', 'Jazz']),
    ('not json but a genre', ['not json but a genre']),
])
def test_parse_stored_genres(raw, expected):
    assert parse_stored_genres(raw) == expected


# ── scan ─────────────────────────────────────────────────────────────────────

def test_scan_noop_when_strict_off(db):
    findings = []
    res = GenreCleanupJob().scan(_ctx(db, _Cfg(enabled=False), findings))
    assert findings == []
    assert res.scanned == 0        # skipped before touching the DB


def test_scan_flags_dirty_artist_and_album_not_clean_ones(db):
    findings = []
    res = GenreCleanupJob().scan(_ctx(db, _Cfg(), findings))
    assert res.findings_created == 2
    # Native subjects carry a `lib2:` entity id so they can never collide with a
    # legacy integer one.
    by_entity = {(f['entity_type'], f['entity_id']): f for f in findings}
    assert ('artist', 'lib2:1') in by_entity and ('album', 'lib2:1') in by_entity
    assert ('artist', 'lib2:2') not in by_entity     # already clean → no finding

    art = by_entity[('artist', 'lib2:1')]['details']
    assert art['kept_genres'] == ['Rock']
    assert art['removed_genres'] == ['downtempo fusion junk', 'seen live']
    assert art['artist_id'] == 1                     # clickable-card contract
    assert art['library_v2'] == {'artist_ids': [1]}

    alb = by_entity[('album', 'lib2:1')]['details']
    assert alb['kept_genres'] == ['Rock']
    assert alb['artist_id'] == 1
    assert alb['library_v2'] == {'album_ids': [1]}


def test_scan_warns_when_cleanup_empties_the_list(db):
    conn = db._get_connection()
    conn.execute("UPDATE lib2_artists SET genres = ? WHERE id=1",
                 (json.dumps(['all junk', 'no matches']),))
    conn.commit(); conn.close()
    findings = []
    GenreCleanupJob().scan(_ctx(db, _Cfg(), findings))
    art = next(f for f in findings
               if f['entity_type'] == 'artist' and f['entity_id'] == 'lib2:1')
    assert art['details']['kept_genres'] == []
    assert 'NO genres' in art['description']


# ── fix ──────────────────────────────────────────────────────────────────────

def _worker_with(db):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    return w


def test_fix_rewrites_to_kept_genres(db):
    w = _worker_with(db)
    out = w._fix_genre_cleanup('artist', 'lib2:1', None,
                               {'kept_genres': ['Rock'], 'removed_genres': ['x']})
    assert out['success'] and out['action'] == 'genres_cleaned'
    conn = db._get_connection()
    row = conn.execute("SELECT genres FROM lib2_artists WHERE id=1").fetchone()
    conn.close()
    assert json.loads(row['genres']) == ['Rock']


def test_fix_empty_kept_stores_an_empty_list(db):
    """T-11: "no genres left" is an empty array on a native row, not NULL — the
    lib2 columns are NOT NULL DEFAULT '[]'. Strict still means strict."""
    w = _worker_with(db)
    out = w._fix_genre_cleanup('album', 'lib2:1', None, {'kept_genres': []})
    assert out['success']
    conn = db._get_connection()
    row = conn.execute("SELECT genres FROM lib2_albums WHERE id=1").fetchone()
    conn.close()
    assert json.loads(row['genres']) == []


def test_fix_refuses_bad_inputs(db):
    w = _worker_with(db)
    assert not w._fix_genre_cleanup('artist', 'lib2:1', None, {})['success']
    assert not w._fix_genre_cleanup('track', 'lib2:1', None, {'kept_genres': []})['success']
    assert not w._fix_genre_cleanup('artist', 'GONE', None, {'kept_genres': ['Rock']})['success']


# ── the tag-write seam (#1057 part A) ────────────────────────────────────────

def test_write_tags_payload_filters_genres_when_strict(monkeypatch):
    import web_server
    monkeypatch.setattr(web_server, 'config_manager', _Cfg())
    payload = web_server._build_library_tag_db_data(
        {'title': 'T', 'artist_name': 'A', 'album_title': 'Al'},
        ['Rock', 'seen live', 'favorites'])
    assert payload['genres'] == ['Rock']


def test_write_tags_payload_untouched_when_strict_off(monkeypatch):
    import web_server
    monkeypatch.setattr(web_server, 'config_manager', _Cfg(enabled=False))
    payload = web_server._build_library_tag_db_data(
        {'title': 'T', 'artist_name': 'A', 'album_title': 'Al'},
        ['Rock', 'seen live'])
    assert payload['genres'] == ['Rock', 'seen live']


def test_scanned_counts_the_whole_library_not_just_genre_rows(db):
    """#1066 (carlosjfcasero): 1,056 artists but 'Scanned: 16' — the scan only
    fetched genre-carrying rows, so the card looked like an incomplete scan.
    Every artist + album now counts as scanned; genre-less rows are skips."""
    conn = db._get_connection()
    cur = conn.cursor()
    for i in range(5):
        cur.execute("INSERT INTO lib2_artists (name, sort_name) VALUES (?, ?)",
                    (f'No Genres {i}', f'No Genres {i}'))
    conn.commit()
    conn.close()

    findings = []
    res = GenreCleanupJob().scan(_ctx(db, _Cfg(), findings))
    # 2 seeded artists + 5 bare artists + 1 seeded album = 8 examined
    assert res.scanned == 8
    # findings unchanged by the counting fix: only the dirty rows raise them
    assert {f['title'] for f in findings} == {
        'Off-whitelist genres: Dirty Artist', 'Off-whitelist genres: Dirty Album'}
