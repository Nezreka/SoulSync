"""Discover page layout: validation, merge, persistence, routes.

core/discovery/layout.py is the single source of truth for the 19 section
ids, the 4 zones and the default order. GET/PUT /api/discover/layout persist
a per-profile customization; the frontend renders from it.
"""

from __future__ import annotations

import pytest

from core.discovery import layout as layout_mod
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _full():
    """A valid proposal: every section id exactly once."""
    return [{'id': sid, 'zone': layout_mod.DEFAULT_ZONE[sid], 'enabled': True}
            for sid in layout_mod.SECTION_IDS]


# ── source of truth ──────────────────────────────────────────────────────────

def test_nineteen_sections_four_zones():
    assert len(layout_mod.SECTION_IDS) == 19
    assert len(set(layout_mod.SECTION_IDS)) == 19
    assert layout_mod.ZONES == ('for-you', 'new-missing', 'library', 'tools')
    assert set(layout_mod.DEFAULT_ZONE) == set(layout_mod.SECTION_IDS)
    assert set(layout_mod.DEFAULT_ZONE.values()) == set(layout_mod.ZONES)


def test_default_layout_is_the_current_page_order():
    entries = layout_mod.default_layout()
    assert len(entries) == 19
    assert all(e['enabled'] for e in entries)
    flat = [e['id'] for e in entries]
    assert flat == list(layout_mod.SECTION_IDS)
    for zone in layout_mod.ZONES:
        positions = [e['position'] for e in entries if e['zone'] == zone]
        assert positions == list(range(len(positions)))


# ── sanitize ─────────────────────────────────────────────────────────────────

def test_sanitize_accepts_a_full_layout():
    entries = layout_mod.sanitize(_full())
    assert len(entries) == 19
    assert {e['id'] for e in entries} == set(layout_mod.SECTION_IDS)


def test_sanitize_rejects_unknown_ids():
    bad = _full() + [{'id': 'nope', 'zone': 'tools', 'enabled': True}]
    with pytest.raises(layout_mod.LayoutValidationError):
        layout_mod.sanitize(bad)


def test_sanitize_rejects_missing_sections():
    with pytest.raises(layout_mod.LayoutValidationError):
        layout_mod.sanitize(_full()[:-1])


def test_sanitize_dedupes_duplicates_first_wins():
    dup = _full()
    dup.insert(0, {'id': dup[0]['id'], 'zone': 'tools', 'enabled': False})
    entries = layout_mod.sanitize(dup)
    assert len(entries) == 19
    first = next(e for e in entries if e['id'] == dup[0]['id'])
    assert first['zone'] == 'tools' and first['enabled'] is False


def test_sanitize_falls_back_to_default_zone():
    entries = layout_mod.sanitize([
        {**e, 'zone': 'bogus'} if e['id'] == 'adv-wave' else e
        for e in _full()
    ])
    adv = next(e for e in entries if e['id'] == 'adv-wave')
    assert adv['zone'] == 'for-you'


def test_sanitize_reorders_positions_per_zone():
    flipped = list(reversed(_full()))
    entries = layout_mod.sanitize(flipped)
    tools = [e for e in entries if e['zone'] == 'tools']
    assert [e['id'] for e in tools] == [
        'build-a-playlist', 'deezer-editorial', 'listenbrainz',
        'lastfm-radio', 'cache-genre-explorer']
    assert [e['position'] for e in tools] == [0, 1, 2, 3, 4]


# ── merge over defaults ──────────────────────────────────────────────────────

def test_merge_returns_defaults_when_nothing_saved():
    merged = layout_mod.merge_over_defaults([])
    assert merged == layout_mod.default_layout()


def test_merge_keeps_saved_zone_order_and_enabled():
    saved = [{'section_id': 'build-a-playlist', 'zone': 'for-you',
              'position': 0, 'enabled': 0},
             {'section_id': 'your-mixes-section', 'zone': 'for-you',
              'position': 1, 'enabled': 1}]
    merged = layout_mod.merge_over_defaults(saved)
    for_you = [e for e in merged if e['zone'] == 'for-you']
    assert [e['id'] for e in for_you[:2]] == ['build-a-playlist', 'your-mixes-section']
    assert for_you[0]['enabled'] is False
    # the other for-you sections follow in default order
    assert [e['id'] for e in for_you[2:]] == [
        'adv-wave', 'listening-recs-section', 'recommended-artists-section',
        'discover-bylt-sections']
    assert len(merged) == 19


def test_merge_surfaces_newly_shipped_sections():
    # a saved layout from before 'deezer-editorial' existed
    saved = [{'section_id': sid, 'zone': layout_mod.DEFAULT_ZONE[sid],
              'position': i, 'enabled': 1}
             for i, sid in enumerate(layout_mod.SECTION_IDS)
             if sid != 'deezer-editorial']
    merged = layout_mod.merge_over_defaults(saved)
    assert len(merged) == 19
    deezer = next(e for e in merged if e['id'] == 'deezer-editorial')
    assert deezer['zone'] == 'tools' and deezer['enabled'] is True


# ── persistence ──────────────────────────────────────────────────────────────

def test_db_roundtrip(db):
    assert db.get_discovery_layout(1) == []
    entries = layout_mod.sanitize(_full())
    assert db.save_discovery_layout(1, entries) is True
    rows = db.get_discovery_layout(1)
    assert len(rows) == 19
    assert {r['section_id'] for r in rows} == set(layout_mod.SECTION_IDS)
    mixes = next(r for r in rows if r['section_id'] == 'your-mixes-section')
    assert mixes['zone'] == 'for-you' and mixes['enabled'] is True


def test_db_layout_is_per_profile(db):
    entries = layout_mod.sanitize(_full())
    db.save_discovery_layout(1, entries)
    assert db.get_discovery_layout(2) == []
    db.save_discovery_layout(1, [e for e in entries if e['id'] != 'adv-wave']
                             + [{'id': 'adv-wave', 'zone': 'tools',
                                 'enabled': False, 'position': 99}])
    rows = db.get_discovery_layout(1)
    adv = next(r for r in rows if r['section_id'] == 'adv-wave')
    assert adv['zone'] == 'tools' and adv['enabled'] is False


# ── routes ───────────────────────────────────────────────────────────────────

# web_server pulls in Flask, which the sandbox may not have: only the route
# tests below skip then, never the validation/DB tests above.


@pytest.fixture()
def client(monkeypatch, tmp_path):
    web_server = pytest.importorskip('web_server')
    import os
    os.environ['DATABASE_PATH'] = str(tmp_path / 'w.db')
    os.environ['SOULSYNC_TEST_DB_READY'] = '1'
    from core.security import session_profile as _sp
    _resolve = _sp.resolve_session_profile
    monkeypatch.setattr(_sp, 'resolve_session_profile',
                        lambda **kw: _resolve(**{**kw, 'profile_count': 1}))
    yield web_server.app.test_client()


def test_get_layout_returns_defaults_when_nothing_saved(client):
    resp = client.get('/api/discover/layout')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert [s['id'] for s in body['sections']] == list(layout_mod.SECTION_IDS)
    assert resp.headers.get('Cache-Control') == 'no-store'


def test_put_then_get_roundtrip(client):
    proposal = _full()
    proposal[0]['enabled'] = False  # hide your-mixes-section
    resp = client.put('/api/discover/layout', json={'sections': proposal})
    assert resp.status_code == 200
    body = client.get('/api/discover/layout').get_json()
    first = next(s for s in body['sections'] if s['id'] == 'your-mixes-section')
    assert first['enabled'] is False


def test_put_rejects_unknown_ids(client):
    bad = _full() + [{'id': 'nope', 'zone': 'tools', 'enabled': True}]
    resp = client.put('/api/discover/layout', json={'sections': bad})
    assert resp.status_code == 400


def test_put_rejects_missing_sections(client):
    resp = client.put('/api/discover/layout', json={'sections': _full()[:-1]})
    assert resp.status_code == 400
