"""#1311: history imported into listenbrainz after the first sync never came in.

an incremental sync only reads listens newer than the last one, so an old
spotify zip imported later sat behind the cursor forever. the sync now notices
the account grew by more than it pulled in and reads the whole thing once.
"""

from __future__ import annotations

import json

import core.listening_import.listenbrainz as lb_module
from core.listening_import.listenbrainz import ListenBrainzListeningImportWorker
from database.music_database import MusicDatabase

# real timestamps: an incremental sync re-reads the last 24h, so anything a
# month back is truly behind the cursor.
NOW = 1_700_000_000
DAY = 86_400


class _Config:
    values = {"listenbrainz.token": "token-123", "listenbrainz.username": "tester"}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        pass


def _listen(ts, title=None):
    return {
        "listened_at": ts,
        "recording_msid": f"msid-{ts}",
        "track_metadata": {"track_name": f"Track {ts}" if title is None else title, "artist_name": "Artist"},
    }


class _FakeAccount:
    """a listenbrainz account: newest-first pages under max_ts, and a listen count."""

    def __init__(self, listens, count=None):
        self.listens = sorted(listens, key=lambda item: -item["listened_at"])
        self.count = len(self.listens) if count is None else count
        self.page_calls = []

    def client(self, **_kwargs):
        account = self

        class _Client:
            def get_user_listen_count(self, username):
                return account.count

            def get_user_listens(self, username, min_ts=None, max_ts=None, count=100):
                account.page_calls.append(max_ts)
                page = [item for item in account.listens if max_ts is None or item["listened_at"] < max_ts]
                return {"payload": {"listens": page[:count], "count": len(page[:count])}}

        return _Client()


def _setup(tmp_path, monkeypatch, account):
    monkeypatch.setattr(lb_module, "ListenBrainzClient", account.client)
    monkeypatch.setattr(lb_module.time, "sleep", lambda _s: None)
    return MusicDatabase(str(tmp_path / "music.db"))


def _played(db):
    conn = db._get_connection()
    try:
        rows = conn.execute("SELECT title FROM listening_history ORDER BY played_at").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def test_older_history_added_after_the_first_sync_gets_imported(tmp_path, monkeypatch):
    monkeypatch.setattr(lb_module, "MISSED_HISTORY_SLACK", 0)
    account = _FakeAccount([_listen(NOW - 600), _listen(NOW)])
    db = _setup(tmp_path, monkeypatch, account)
    worker = ListenBrainzListeningImportWorker(db, _Config())
    assert worker.run_once()["backfill_complete"] is True

    # the user imports an old zip: listens dated long before the cursor
    account.listens += [_listen(NOW - 40 * DAY), _listen(NOW - 30 * DAY)]
    account.listens.sort(key=lambda item: -item["listened_at"])
    account.count = 4
    state = worker.run_once()

    assert _played(db) == [f"Track {ts}" for ts in (NOW - 40 * DAY, NOW - 30 * DAY, NOW - 600, NOW)]
    assert state["status"] == "complete"
    assert state["backfill_complete"] is True
    assert state["last_imported_ts"] == NOW
    assert state["reconciled_gap"] == 0


def test_no_new_older_history_stays_a_cheap_incremental_sync(tmp_path, monkeypatch):
    account = _FakeAccount([_listen(NOW - 600), _listen(NOW)])
    db = _setup(tmp_path, monkeypatch, account)
    worker = ListenBrainzListeningImportWorker(db, _Config())
    worker.run_once()

    account.listens.insert(0, _listen(NOW + 3600))
    account.count = 3
    account.page_calls.clear()
    state = worker.run_once()

    assert account.page_calls == [None]  # one newest page, no full crawl
    assert _played(db)[-1] == f"Track {NOW + 3600}"
    assert state["reconciled_gap"] == 0


def test_listens_we_cannot_import_do_not_trigger_a_full_crawl_every_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(lb_module, "MISSED_HISTORY_SLACK", 0)
    # a title-less listen counts on listenbrainz but never becomes a row
    account = _FakeAccount([_listen(NOW - 1200, title=""), _listen(NOW - 600), _listen(NOW)])
    db = _setup(tmp_path, monkeypatch, account)
    worker = ListenBrainzListeningImportWorker(db, _Config())
    assert worker.run_once()["reconciled_gap"] == 1

    account.page_calls.clear()
    worker.run_once()
    worker.run_once()

    assert account.page_calls == [None, None]


def test_first_check_on_an_old_install_reads_the_full_history_once(tmp_path, monkeypatch):
    # the reporter's shape: synced before this fix, so no gap on record, and
    # the old zip is already on listenbrainz.
    # half an hour apart, so the 24h re-read fits in the first page
    stamps = [NOW + i * 1800 for i in range(200)]
    account = _FakeAccount([_listen(ts) for ts in stamps])
    db = _setup(tmp_path, monkeypatch, account)
    worker = ListenBrainzListeningImportWorker(db, _Config())
    worker._insert_events_deduped([lb_module.normalize_listenbrainz_listen(_listen(ts)) for ts in stamps[100:]])
    db.set_metadata("listenbrainz_listening_import_state", json.dumps({
        "username": "tester", "status": "complete", "backfill_complete": True,
        "last_imported_ts": stamps[-1],
    }))
    worker = ListenBrainzListeningImportWorker(db, _Config())

    state = worker.run_once()

    assert len(_played(db)) == 200
    assert state["reconciled_gap"] == 0
    account.page_calls.clear()
    worker.run_once()
    assert account.page_calls == [None]


def test_server_without_a_listen_count_skips_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(lb_module, "MISSED_HISTORY_SLACK", 0)
    account = _FakeAccount([_listen(NOW - 600), _listen(NOW)])
    db = _setup(tmp_path, monkeypatch, account)
    worker = ListenBrainzListeningImportWorker(db, _Config())
    worker.run_once()

    account.listens.append(_listen(NOW - 40 * DAY))
    account.count = None  # maloja has no listen-count endpoint
    account.page_calls.clear()
    worker.run_once()

    assert account.page_calls == [None]
    assert f"Track {NOW - 40 * DAY}" not in _played(db)
