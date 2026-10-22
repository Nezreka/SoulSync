"""mcp endpoint: the protocol for both client eras, and who can call what.

the protocol tests use core.mcp.protocol on its own. the seam tests go
through the real app, its gates and the replayed /api/v1 calls, with keys
held in memory so nothing reaches the real config.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from api import auth
from core.mcp import protocol
from core.mcp.tools import API_TOOLS, build_request, check_args


class Headers(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class Echo:
    name = "echo"

    def listing(self):
        return {"name": "echo", "inputSchema": {"type": "object"}}


def _server(result=None):
    return protocol.Server(name="soulsync", version="9", instructions="hi", tools=lambda: [Echo()],
                           call=lambda tool, args: result or protocol.tool_result({"got": args}))


def _send(server, msg, **headers):
    return server.handle(json.dumps(msg).encode(), Headers(headers))


MODERN_META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
               "io.modelcontextprotocol/clientCapabilities": {}}


# ---- protocol: the 2025 era ---------------------------------------------------

def test_initialize_echoes_a_version_we_speak_and_offers_ours_otherwise():
    s = _server()
    status, body = _send(s, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "c", "version": "1"}}})
    assert status == 200
    assert body["result"]["protocolVersion"] == "2025-06-18"
    assert body["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert body["result"]["serverInfo"]["name"] == "soulsync"
    _, body = _send(s, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "1999-01-01"}})
    assert body["result"]["protocolVersion"] == "2025-11-25"


def test_notifications_and_stray_responses_get_202_and_no_body():
    s = _server()
    assert _send(s, {"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)
    assert _send(s, {"jsonrpc": "2.0", "id": 4, "result": {}}) == (202, None)


def test_legacy_list_and_call_have_no_modern_fields():
    s = _server()
    status, body = _send(s, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                         **{"MCP-Protocol-Version": "2025-11-25"})
    assert status == 200 and body["result"] == {"tools": [Echo().listing()]}
    _, body = _send(s, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"a": 1}}})
    assert body["result"]["structuredContent"] == {"got": {"a": 1}}
    assert "resultType" not in body["result"]


def test_legacy_errors_ride_a_200():
    status, body = _send(_server(), {"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    assert status == 200 and body["error"]["code"] == protocol.METHOD_NOT_FOUND


# ---- protocol: the 2026-07-28 era ---------------------------------------------

def test_modern_request_without_handshake_gets_result_type_and_server_info():
    status, body = _send(_server(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                     "params": {"_meta": MODERN_META}},
                         **{"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"})
    assert status == 200
    assert body["result"]["resultType"] == "complete"
    assert body["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "soulsync"
    # the 2.x python sdk refuses a modern list without these (found smoking it live)
    assert body["result"]["ttlMs"] > 0 and body["result"]["cacheScope"] == "private"


def test_discover():
    _, body = _send(_server(), {"jsonrpc": "2.0", "id": "d", "method": "server/discover",
                                "params": {"_meta": MODERN_META}}, **{"MCP-Protocol-Version": "2026-07-28"})
    assert "2026-07-28" in body["result"]["supportedVersions"]
    assert "tools" in body["result"]["capabilities"]
    assert body["result"]["ttlMs"] > 0 and body["result"]["cacheScope"] == "private"


def test_header_and_body_versions_must_agree():
    status, body = _send(_server(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                     "params": {"_meta": MODERN_META}}, **{"MCP-Protocol-Version": "2025-11-25"})
    assert status == 400 and body["error"]["code"] == protocol.HEADER_MISMATCH


def test_mcp_method_header_must_match_the_body():
    status, body = _send(_server(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                     "params": {"_meta": MODERN_META}},
                         **{"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call"})
    assert status == 400 and body["error"]["code"] == protocol.HEADER_MISMATCH


def test_unsupported_version_lists_what_we_do_speak():
    meta = dict(MODERN_META, **{"io.modelcontextprotocol/protocolVersion": "2099-01-01"})
    status, body = _send(_server(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                     "params": {"_meta": meta}}, **{"MCP-Protocol-Version": "2099-01-01"})
    assert status == 400 and body["error"]["code"] == protocol.UNSUPPORTED_VERSION
    assert body["error"]["data"]["requested"] == "2099-01-01"
    assert "2026-07-28" in body["error"]["data"]["supported"]


def test_modern_unknown_method_is_a_404():
    status, body = _send(_server(), {"jsonrpc": "2.0", "id": 1, "method": "prompts/list",
                                     "params": {"_meta": MODERN_META}}, **{"MCP-Protocol-Version": "2026-07-28"})
    assert status == 404 and body["error"]["code"] == protocol.METHOD_NOT_FOUND


@pytest.mark.parametrize("raw", [b"not json", b'[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]',
                                 b'{"jsonrpc": "1.0", "id": 1, "method": "ping"}'])
def test_garbage_and_batches_are_refused(raw):
    status, body = _server().handle(raw, Headers())
    assert status == 400 and body["error"]["code"] in (protocol.PARSE_ERROR, protocol.INVALID_REQUEST)


def test_unknown_tool_is_invalid_params():
    _, body = _send(_server(), {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}})
    assert body["error"]["code"] == protocol.INVALID_PARAMS


def test_huge_results_are_cut_and_lose_structured_content():
    big = protocol.tool_result({"x": "a" * (protocol.MAX_TEXT + 10)})
    assert len(big["content"][0]["text"]) < protocol.MAX_TEXT + 200
    assert "structuredContent" not in big


# ---- tool definitions ----------------------------------------------------------

def _tool(name):
    return next(t for t in API_TOOLS if t.name == name)


def test_path_args_leave_the_body_and_get_quoted():
    method, path, query, body = build_request(_tool("cancel_download"), {"download_id": "a b", "username": "u"})
    assert (method, path, query, body) == ("POST", "/api/v1/downloads/a%20b/cancel", {}, {"username": "u"})
    method, path, query, body = build_request(_tool("search_library_tracks"), {"title": "x", "artist": ""})
    assert (path, query, body) == ("/api/v1/library/tracks", {"title": "x"}, None)


@pytest.mark.parametrize("bad", ["../settings", "a/b", "..", "a\\b"])
def test_an_id_cannot_walk_to_another_route(bad):
    assert check_args(_tool("get_library_artist"), {"artist_id": bad}) == "artist_id is not a valid id"


def test_check_args_catches_the_obvious():
    assert check_args(_tool("request_track"), {}) == "query is required"
    assert check_args(_tool("recently_added"), {"type": "songs"}).startswith("type must be one of")
    assert check_args(_tool("recently_added"), {"limit": "5"}) == "limit must be a whole number"
    assert check_args(_tool("recently_added"), {"bogus": 1}) == "unknown argument bogus"
    assert check_args(_tool("recently_added"), {"type": "albums", "limit": 5}) is None


def test_nothing_that_can_wreck_a_library_is_a_tool():
    paths = {(t.method, t.path) for t in API_TOOLS}
    for method, prefix in [("PATCH", "/settings"), ("POST", "/api-keys"), ("DELETE", "/profiles"),
                           ("POST", "/downloads/cancel-all"), ("DELETE", "/downloads/failed-blocklist"),
                           ("DELETE", "/retag/groups")]:
        assert not any(m == method and p.startswith(prefix) for m, p in paths), prefix


# ---- through the real app ----------------------------------------------------------

web_server = pytest.importorskip('web_server')
import api.mcp as mcp_api  # noqa: E402


def _key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


ADMIN_RAW, READ_RAW = 'sk_mcp-admin', 'sk_mcp-read'
KEYS = [{'id': 'a', 'key_hash': _key(ADMIN_RAW), 'label': 'claude', 'scope': 'admin'},
        {'id': 'r', 'key_hash': _key(READ_RAW), 'label': 'peek', 'scope': 'read'}]
ADMIN = {'Authorization': f'Bearer {ADMIN_RAW}'}
READ = {'Authorization': f'Bearer {READ_RAW}'}


@pytest.fixture
def app_state(monkeypatch):
    state = {'mcp.enabled': True}
    real_get = web_server.config_manager.get

    def get(name, default=None):
        if name == 'api_keys':
            return KEYS
        if name in state:
            return state[name]
        return real_get(name, default)

    monkeypatch.setattr(web_server.config_manager, 'get', get)
    monkeypatch.setattr(web_server.config_manager, 'set', lambda *a, **k: None)
    monkeypatch.setattr(auth, '_should_persist_usage', lambda *a, **k: False)
    client = web_server.app.test_client()
    with client.session_transaction() as s:
        s.clear()
    return client, state


def _rpc(client, method, params=None, headers=ADMIN, rid=1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return client.post('/mcp', json=msg, headers={**headers, 'MCP-Protocol-Version': '2025-11-25'})


def _call(client, name, args=None, headers=ADMIN):
    resp = _rpc(client, "tools/call", {"name": name, "arguments": args or {}}, headers)
    return resp, resp.get_json()


def test_off_by_default_is_a_404(app_state):
    client, state = app_state
    state['mcp.enabled'] = False
    assert client.post('/mcp', json={}, headers=ADMIN).status_code == 404
    assert client.get('/mcp', headers=ADMIN).status_code == 404


def test_needs_a_key(app_state):
    client, _ = app_state
    resp = _rpc(client, "tools/list", headers={})
    assert resp.status_code == 401
    assert resp.headers.get('WWW-Authenticate', '').startswith('Bearer')


def test_get_is_405(app_state):
    client, _ = app_state
    assert client.get('/mcp', headers=ADMIN).status_code == 405


def test_admin_handshake_list_and_a_real_call(app_state):
    client, _ = app_state
    init = _rpc(client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}})
    assert init.status_code == 200 and init.get_json()["result"]["serverInfo"]["name"] == "soulsync"
    tools = _rpc(client, "tools/list").get_json()["result"]["tools"]
    assert len(tools) == len(mcp_api.TOOLS)
    resp, body = _call(client, "get_library_stats")
    assert resp.status_code == 200
    assert body["result"]["isError"] is False, body
    assert "data" in body["result"]["structuredContent"]


def test_read_key_gets_in_but_only_sees_read_tools(app_state):
    client, _ = app_state
    resp = _rpc(client, "tools/list", headers=READ)
    assert resp.status_code == 200, resp.data  # the scope gate let the POST through
    names = {t["name"] for t in resp.get_json()["result"]["tools"]}
    assert "get_library_stats" in names and "find_album" in names
    for hidden in ("request_track", "request_album", "remove_from_wishlist", "search_music",
                   "video_calendar"):  # the calendar GET writes, read keys can't call it on v1 either
        assert hidden not in names, hidden
    _, body = _call(client, "get_library_stats", headers=READ)
    assert body["result"]["isError"] is False, body


def test_read_key_cannot_call_a_hidden_tool(app_state):
    client, _ = app_state
    _, body = _call(client, "request_track", {"query": "a - b"}, headers=READ)
    assert body["error"]["code"] == protocol.INVALID_PARAMS


def test_even_if_a_write_tool_leaked_to_a_read_key_the_api_refuses_it(app_state, monkeypatch):
    """the visibility filter is the first wall, the replayed key is the second."""
    client, _ = app_state
    monkeypatch.setattr(mcp_api, 'visible_tools', lambda scope: list(mcp_api.TOOLS))
    _, body = _call(client, "request_track", {"query": "a - b"}, headers=READ)
    result = body["result"]
    assert result["isError"] is True
    assert "read-only" in result["content"][0]["text"] and "403" in result["content"][0]["text"]


def test_a_foreign_origin_is_refused_and_our_own_is_fine(app_state):
    client, _ = app_state
    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    evil = client.post('/mcp', json=msg, headers={**ADMIN, 'Origin': 'https://evil.example'})
    assert evil.status_code == 403
    own = client.post('/mcp', json=msg, headers={**ADMIN, 'Origin': 'http://localhost'})
    assert own.status_code == 200


def test_login_mode_does_not_lock_a_key_out(app_state):
    client, state = app_state
    state['security.require_login'] = True
    _, body = _call(client, "get_library_stats")
    assert body["result"]["isError"] is False, body


def test_api_errors_come_back_as_tool_errors_not_crashes(app_state):
    client, _ = app_state
    _, body = _call(client, "get_request_status", {"request_id": "does-not-exist"})
    assert body["result"]["isError"] is True
    assert "http 404" in body["result"]["content"][0]["text"]


def test_find_and_request_album_use_musicbrainz_and_the_shared_wishlist_add(app_state, monkeypatch):
    client, _ = app_state

    class FoundAlbum:
        id, name, artists, release_date, total_tracks, album_type = "rg-1", "OK Computer", ["Radiohead"], "1997", 12, "album"
        secondary_types, disambiguation = [], ""

    class MB:
        def search_albums(self, query, limit=10):
            assert query == "Radiohead - OK Computer"
            return [FoundAlbum()]

        def get_album(self, album_id, include_tracks=True):
            assert album_id == "rg-1"
            return {"id": "rg-1", "name": "OK Computer", "artists": [{"name": "Radiohead"}],
                    "tracks": [{"id": "t1", "name": "Airbag"}, {"id": "t2", "name": "Paranoid Android"}]}

    import core.metadata.registry as registry
    import core.wishlist.album_request as album_request
    monkeypatch.setattr(registry, 'get_musicbrainz_client', lambda: MB())
    seen = {}

    def fake_queue(album, artist, **kw):
        seen.update(album=album["name"], artist=artist["name"], **kw)
        return 2
    monkeypatch.setattr(album_request, 'queue_album_tracks', fake_queue)

    _, body = _call(client, "find_album", {"query": "Radiohead - OK Computer"})
    found = body["result"]["structuredContent"]["albums"][0]
    assert found["album_id"] == "rg-1" and found["tracks"] == 12

    _, body = _call(client, "request_album", {"album_id": found["album_id"]})
    out = body["result"]["structuredContent"]
    assert out["queued"] == 2 and out["tracks"] == 2
    assert seen["album"] == "OK Computer" and seen["artist"] == "Radiohead"
    assert seen["profile_id"] == 1 and seen["requested_via"] == "mcp"


def test_request_album_with_no_tracklist_says_so(app_state, monkeypatch):
    client, _ = app_state
    import core.metadata.registry as registry

    class MB:
        def get_album(self, album_id, include_tracks=True):
            return None
    monkeypatch.setattr(registry, 'get_musicbrainz_client', lambda: MB())
    _, body = _call(client, "request_album", {"album_id": "nope"})
    assert body["result"]["isError"] is True
    assert "MusicBrainz" in body["result"]["content"][0]["text"]


def test_v1_routes_a_tool_names_all_exist(app_state):
    """a tool pointing at a route that was renamed would fail only when an ai calls it."""
    from werkzeug.exceptions import MethodNotAllowed, NotFound
    adapter = web_server.app.url_map.bind("localhost")
    for tool in API_TOOLS:
        path = tool.sample_path().replace("/search/x", "/search/tracks")
        try:
            endpoint, _ = adapter.match(path, method=tool.method)
        except (NotFound, MethodNotAllowed):
            endpoint = None
        # the spa catch-all matches any path, so it has to be a v1 route
        assert endpoint and endpoint.startswith("api_v1."), f"{tool.name}: no {tool.method} {path}"
