"""#1134: POST /api/wishlist/process must not crash on its own runtime factory.

The route passed ``is_auto_processing_flag=…`` to
``_build_wishlist_route_runtime``, which has never accepted that keyword —
so the endpoint returned HTTP 500 (TypeError) on EVERY call, and manual
wishlist processing was unreachable. Nothing executed this route in tests,
which is how a keyword typo shipped. This calls the real route through the
real factory; only the handler behind it is stubbed so no background
processing actually starts.
"""

from __future__ import annotations


def test_process_route_builds_its_runtime_without_crashing(monkeypatch):
    import web_server

    seen = {}

    def fake_process_api(runtime, *, start_processing):
        seen['runtime'] = runtime
        return {"success": True, "message": "stubbed"}, 200

    from api import wishlist_routes
    monkeypatch.setattr(wishlist_routes, '_wishlist_process_api', fake_process_api)
    client = web_server.app.test_client()
    resp = client.post('/api/wishlist/process')

    # Pre-fix this was a 500 with "unexpected keyword argument
    # 'is_auto_processing_flag'" before the stub was ever reached.
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()['success'] is True
    # The runtime carries the ROBUST liveness check (the factory default),
    # not a raw staleness-prone flag — the 409 guard needs the real thing.
    runtime = seen['runtime']
    assert runtime.is_wishlist_actually_processing is web_server.is_wishlist_actually_processing
