"""Notification journal + filter (Kazimir: "50 of em", "people will
reflexively clear them").

Toasts were client-side only — a Clear All (or a reload) lost everything,
and there was no filtering at all. Now every toast is journaled server-side
per profile (batched, pruned), the bell panel filters by type, and a History
modal reads the journal with search + pagination.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.music_database import MusicDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def test_journal_roundtrip_newest_first(db):
    assert db.add_notifications([{'type': 'success', 'message': 'first'},
                                 {'type': 'error', 'message': 'second'}]) == 2
    rows = db.get_notification_history()
    assert [r['message'] for r in rows] == ['second', 'first']
    assert rows[0]['type'] == 'error' and rows[0]['created_at']


def test_types_are_whitelisted_and_messages_capped(db):
    db.add_notifications([{'type': 'bogus', 'message': 'x' * 900},
                          {'type': 'warning', 'message': '  ok  '},
                          {'message': ''},          # empty → dropped
                          'not-a-dict'])
    rows = db.get_notification_history()
    assert len(rows) == 2
    assert rows[1]['type'] == 'info' and len(rows[1]['message']) == 500
    assert rows[0]['type'] == 'warning' and rows[0]['message'] == 'ok'


def test_filter_and_search(db):
    db.add_notifications([
        {'type': 'error', 'message': 'Download failed: Adrianne'},
        {'type': 'success', 'message': 'Downloaded: Adrianne'},
        {'type': 'success', 'message': 'Playlist synced'},
    ])
    assert len(db.get_notification_history(type_filter='success')) == 2
    hits = db.get_notification_history(search='adrianne')
    assert len(hits) == 2
    both = db.get_notification_history(type_filter='error', search='adrianne')
    assert len(both) == 1 and both[0]['type'] == 'error'
    # LIKE wildcards in user input are literals, not wildcards
    db.add_notifications([{'type': 'info', 'message': 'literal % sign'}])
    assert len(db.get_notification_history(search='% sign')) == 1
    assert db.get_notification_history(search='%%%') == []


def test_prune_keeps_only_the_newest(db, monkeypatch):
    monkeypatch.setattr(MusicDatabase, '_NOTIFICATION_KEEP', 3)
    for i in range(5):
        db.add_notifications([{'type': 'info', 'message': f'n{i}'}])
    rows = db.get_notification_history()
    assert [r['message'] for r in rows] == ['n4', 'n3', 'n2']


def test_profiles_are_isolated(db):
    db.add_notifications([{'type': 'info', 'message': 'mine'}], profile_id=1)
    db.add_notifications([{'type': 'info', 'message': 'theirs'}], profile_id=2)
    assert [r['message'] for r in db.get_notification_history(profile_id=1)] == ['mine']
    assert db.clear_notification_history(profile_id=2) == 1
    assert db.get_notification_history(profile_id=2) == []
    assert [r['message'] for r in db.get_notification_history(profile_id=1)] == ['mine']


def test_pagination(db):
    db.add_notifications([{'type': 'info', 'message': f'n{i}'} for i in range(7)])
    page1 = db.get_notification_history(limit=3, offset=0)
    page2 = db.get_notification_history(limit=3, offset=3)
    assert len(page1) == 3 and len(page2) == 3
    assert {r['id'] for r in page1}.isdisjoint({r['id'] for r in page2})


# ── wiring contracts ──────────────────────────────────────────────────────────

def test_endpoints_exist():
    src = (_ROOT / 'web_server.py').read_text(encoding='utf-8', errors='replace')
    assert "'/api/notifications/log'" in src
    assert "'/api/notifications/history'" in src
    assert 'add_notifications(' in src and 'get_notification_history(' in src


def test_frontend_wiring():
    js = (_ROOT / 'webui' / 'static' / 'downloads.js').read_text(encoding='utf-8', errors='replace')
    css = (_ROOT / 'webui' / 'static' / 'style.css').read_text(encoding='utf-8', errors='replace')
    # toasts journal to the server, batched
    assert '_queueNotifJournal(type, message)' in js
    assert "'/api/notifications/log'" in js
    # panel filter chips + the persistent history modal
    assert '_setNotifFilter' in js and 'notif-filter-chip' in js and 'notif-filter-chip' in css
    assert '_openNotifHistory' in js and 'notif-history-modal' in css
    # destructive clear goes through the SoulSync confirm dialog, never window.confirm
    assert 'showConfirmDialog' in js.split('function _clearServerNotifHistory')[1][:600]
