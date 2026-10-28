"""#1852 through the real compare route: a playlist with no mirror reads its
source from sync history (0-based 'index', no 'position'), and two source rows
share one server track. the source column must start at 1 like the server
column, and the shared pairing must not read as out of order."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import web_server
import core.sync.match_overrides as match_overrides


def _hist_row(i, name, sid):
    # the exact shape services/sync_service.py writes into track_results
    return {'index': i, 'name': name, 'artist': 'Disney', 'album': 'Jams',
            'duration_ms': 180000, 'source_track_id': sid, 'image_url': '',
            'status': 'found', 'confidence': 1.0, 'matched_track': None}


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def get_sync_history(self, page=1, limit=50, profile_id=None):
        return [{'id': 7, 'playlist_name': 'Disney Jams'}], 1

    def get_sync_history_entry(self, entry_id, profile_id=None):
        return {'track_results': json.dumps(self._rows)}

    def get_server_match_rejections(self, *a, **k):
        return set()


def _svr(tid, title):
    return SimpleNamespace(ratingKey=tid, title=title, duration=180000,
                           _data={'artist': 'Disney', 'album': 'Jams'})


@pytest.fixture
def compare(monkeypatch):
    def run(rows, server, overrides):
        client = SimpleNamespace(get_playlist_tracks=lambda pid: server)
        monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: "navidrome")
        monkeypatch.setattr(web_server, "_server_playlist_guard",
                            lambda srv, pid, name: (client, pid, name, None))
        monkeypatch.setattr(web_server, "get_database", lambda: _Db(rows))
        monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
        monkeypatch.setattr(match_overrides, "build_bulk_override_lookup",
                            lambda *a, **k: lambda sid: overrides.get(sid))
        resp = web_server.app.test_client().get(
            "/api/server/playlist/p1/tracks?name=Disney%20Jams")
        return resp.get_json()
    return run


def test_history_sourced_compare_numbers_from_one_and_shared_pair_is_in_order(compare):
    # four source rows, three server tracks: rows 0 and 2 are the same song
    # under two provider ids, both hand-matched to server track s1
    rows = [_hist_row(0, "You're Welcome", 'a'), _hist_row(1, 'Zero', 'b'),
            _hist_row(2, "You're Welcome", 'c'), _hist_row(3, 'Into the Unknown', 'd')]
    server = [_svr('s1', "You're Welcome"), _svr('s2', 'Zero'), _svr('s3', 'Into the Unknown')]
    data = compare(rows, server, {'a': 's1', 'c': 's1'})

    assert data['success'] is True
    src_rows = [t for t in data['tracks'] if t['source_track']]
    assert [t['source_track']['position'] for t in src_rows] == [1, 2, 3, 4]
    assert [t['match_status'] for t in src_rows] == ['matched'] * 4
    assert data['order_status'] == {'matched': 4, 'in_order': True, 'out_of_order': False}


def test_history_sourced_compare_still_flags_real_drift(compare):
    rows = [_hist_row(0, "You're Welcome", 'a'), _hist_row(1, 'Zero', 'b'),
            _hist_row(2, "You're Welcome", 'c'), _hist_row(3, 'Into the Unknown', 'd')]
    # server has Zero after Into the Unknown: really out of order
    server = [_svr('s1', "You're Welcome"), _svr('s3', 'Into the Unknown'), _svr('s2', 'Zero')]
    data = compare(rows, server, {'a': 's1', 'c': 's1'})

    assert data['order_status']['out_of_order'] is True
