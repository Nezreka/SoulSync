"""Regression coverage for real HTTP responses, pagination and Maloja history."""
import json
from unittest.mock import Mock

import pytest
import requests

from core.listenbrainz_client import ListenBrainzClient
from core.listening_import import listenbrainz as importer
from database.music_database import MusicDatabase


class Config:
    def __init__(self, url="https://api.listenbrainz.org"):
        self.values = {"listenbrainz.token": "private-token", "listenbrainz.username": "tester",
                       "listenbrainz.base_url": url}

    def get(self, key, default=None):
        return self.values.get(key, default)


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    return result


def listen(ts):
    return {"listened_at": ts, "track_metadata": {"track_name": f"Track {ts}", "artist_name": "Artist"}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ListenBrainzClient, "_validate_and_get_username", lambda self: True)
    return ListenBrainzClient(token="private-token", base_url="https://example.test")


@pytest.mark.parametrize("status,data", [(401, {}), (404, {}), (429, {}), (500, {}),
    (200, {"code": 200, "error": "Invalid Method"}), (200, {}), (200, {"payload": {}})])
def test_client_rejects_http_and_payload_failures(client, status, data):
    client.session.get = Mock(return_value=response(data, status))
    with pytest.raises((RuntimeError, ValueError)):
        client.get_user_listens("tester")


def test_client_reuses_url_and_token_and_encodes_username(client):
    client.session.get = Mock(return_value=response({"payload": {"listens": []}}))
    client.get_user_listens("a/b", max_ts=123, count=100)
    args, kwargs = client.session.get.call_args
    assert args[0] == "https://example.test/1/user/a%2Fb/listens"
    assert kwargs["headers"] == {"Authorization": "Token private-token"}
    assert kwargs["params"] == {"count": 100, "max_ts": 123}


@pytest.fixture
def worker(tmp_path, monkeypatch, client):
    monkeypatch.setattr(importer, "TRANSIENT_PAGE_RETRY_BASE_SECONDS", 0)
    monkeypatch.setattr(importer.time, "sleep", lambda _: None)
    monkeypatch.setattr(importer, "ListenBrainzClient", lambda **kwargs: client)
    client.get_user_listen_count = lambda _: None
    db = MusicDatabase(str(tmp_path / "history.db"))
    return importer.ListenBrainzListeningImportWorker(db, Config())


def seed_incremental(worker):
    state = {"username": "tester", "backfill_complete": True, "last_imported_ts": 1700000000,
             "status": "complete", "page": 10, "total_pages": 10}
    worker.db.set_metadata(importer.STATE_KEY, json.dumps(state))
    worker._state = state


def test_incremental_drains_multiple_pages_and_stops_at_overlap(worker, client):
    seed_incremental(worker)
    timestamps = list(range(1700300000, 1699800000, -1000))
    def get(url, **kwargs):
        maximum = kwargs["params"].get("max_ts")
        batch = [ts for ts in timestamps if maximum is None or ts < maximum][:100]
        return response({"payload": {"listens": [listen(ts) for ts in batch]}})
    client.session.get = Mock(side_effect=get)
    state = worker.run_once()
    expected = sum(ts > 1700000000 - 86400 for ts in timestamps)
    assert state["status"] == "complete"
    assert state["inserted"] == expected
    assert state["last_imported_ts"] == max(timestamps)
    assert client.session.get.call_count == 4
    assert len({call.kwargs["params"].get("max_ts") for call in client.session.get.call_args_list}) == 4


def test_failed_incremental_keeps_cursor_and_retry_recovers(worker, client):
    seed_incremental(worker)
    timestamps = list(range(1700300000, 1700100000, -1000))
    failing = True
    def get(url, **kwargs):
        maximum = kwargs["params"].get("max_ts")
        if failing and maximum is not None:
            return response({}, 503)
        batch = [ts for ts in timestamps if maximum is None or ts < maximum][:100]
        return response({"payload": {"listens": [listen(ts) for ts in batch]}})
    client.session.get = Mock(side_effect=get)
    state = worker.run_once()
    assert state["status"] == "error"
    assert state["last_imported_ts"] == 1700000000
    assert "503" in state["error"]
    failing = False
    state = worker.run_once()
    assert state["status"] == "complete"
    assert worker.db.get_listening_stats("all")["total_plays"] == len(timestamps)
    assert state["last_imported_ts"] == max(timestamps)


def test_transient_http_error_is_retried(worker, client):
    client.session.get = Mock(side_effect=[response({}, 503), response({"payload": {"listens": [listen(1700000000)]}})])
    state = worker.run_once()
    assert state["status"] == "complete"
    assert state["inserted"] == 1
    assert client.session.get.call_count == 2


@pytest.mark.parametrize("data", [None, {"code": 200, "error": "Invalid Method"}])
def test_invalid_result_never_marks_backfill_complete(worker, client, data):
    client.get_user_listens = Mock(return_value=data)
    state = worker.run_once()
    assert state["status"] == "error"
    assert not state["backfill_complete"]
    assert not state.get("last_success_at")


def test_cancelled_incremental_preserves_cursor(worker, client):
    seed_incremental(worker)
    def get(url, **kwargs):
        worker.cancel()
        return response({"payload": {"listens": [listen(1700300000)]}})
    client.session.get = Mock(side_effect=get)
    state = worker.run_once()
    assert state["status"] == "cancelled"
    assert state["last_imported_ts"] == 1700000000
    assert state["backfill_complete"] is True


def maloja_row(ts):
    return {"time": ts, "track": {"title": f"Track {ts}", "artists": ["Artist"],
            "album": {"albumtitle": "Album"}, "length": 180}}


@pytest.mark.parametrize("alias", ["listenbrainz", "lbrnz"])
def test_maloja_native_pagination_and_resumed_bounds(client, alias):
    client.base_url = f"http://maloja.local/music/apis/{alias}/1"
    rows = [maloja_row(ts) for ts in range(1700001200, 1700000000, -1)]
    def get(url, **kwargs):
        assert url == "http://maloja.local/music/apis/mlj_1/scrobbles"
        assert kwargs["headers"] == {"Authorization": "Token private-token"}
        page = kwargs["params"]["page"]
        return response({"status": "ok", "list": rows[page * 1000:(page + 1) * 1000]})
    client.session.get = Mock(side_effect=get)
    collected = []
    maximum = 1700001151
    while True:
        batch = client.get_user_listens("tester", max_ts=maximum)["payload"]["listens"]
        collected.extend(batch)
        if len(batch) < 100:
            break
        maximum = min(item["listened_at"] for item in batch)
    assert len(collected) == 1150
    assert len({item["listened_at"] for item in collected}) == 1150
    assert client.session.get.call_count == 2
    event = importer.normalize_listenbrainz_listen(collected[0])
    assert event["album"] == "Album"
    assert event["duration_ms"] == 180000


def test_maloja_import_enters_shared_history(worker, client):
    client.base_url = "http://maloja.local/apis/listenbrainz/1"
    client.session.get = Mock(return_value=response({"status": "ok", "list": [maloja_row(1700000000)]}))
    state = worker.run_once()
    assert state["status"] == "complete"
    assert worker.db.get_top_artists("all", 10)[0]["name"] == "Artist"
    assert worker.db.get_listening_stats("all")["total_plays"] == 1


def test_server_ignoring_cursor_is_an_error(worker, client):
    seed_incremental(worker)
    batch = [listen(ts) for ts in range(1700300000, 1700299900, -1)]
    client.session.get = Mock(return_value=response({"payload": {"listens": batch}}))
    state = worker.run_once()
    assert state["status"] == "error"
    assert "cursor did not advance" in state["error"]
    assert state["last_imported_ts"] == 1700000000


def test_maloja_http_failure_does_not_consume_native_page(client):
    client.base_url = "http://maloja.local/apis/listenbrainz/1"
    client.session.get = Mock(side_effect=[response({}, 503),
        response({"status": "ok", "list": [maloja_row(1700000000)]})])
    with pytest.raises(RuntimeError):
        client.get_user_listens("tester")
    result = client.get_user_listens("tester")
    assert result["payload"]["listens"][0]["listened_at"] == 1700000000
    assert [call.kwargs["params"]["page"] for call in client.session.get.call_args_list] == [0, 0]
