"""v1 rate limiter: admin API keys are exempt, everyone else is capped (#1923).

The bug: the whole /api/v1 blueprint sat under one blanket 60/minute
per-IP cap. MetaSync's weekly report walks GET /library/albums
sequentially at limit=200, so any library past ~12,000 albums needs more
than 60 pages; on a fast host the 61st page lands inside the first
minute, the walk gets a 429 RATE_LIMITED, and the report job fails.

The fix exempts requests carrying a valid ADMIN-scope API key (the scope
SoulSync's own pairing mints for MetaSync) and leaves the cap exactly as
it was for everyone else. These tests pin both halves with the REAL
flask_limiter and the REAL blueprint — a stubbed limiter would make the
whole file fiction, which is why nothing here (or imported before here)
may replace it.
"""

from __future__ import annotations

import hashlib
import sys

import pytest


def _ensure_real_flask_limiter():
    """Pin the real flask_limiter before anything imports the api package.

    Several test modules stub flask_limiter in sys.modules when they are
    imported first (their stub's limit() is a no-op decorator). If such a
    stub beat us here, drop it — and any api module already built on top
    of it — so this file exercises the limiter that actually ships.
    Modules other files already imported keep their own references; only
    fresh imports see the real package.
    """
    mod = sys.modules.get("flask_limiter")
    if mod is not None and getattr(mod, "__file__", None) is None:
        for name in [n for n in list(sys.modules)
                     if n == "flask_limiter" or n.startswith("flask_limiter.")
                     or n == "api" or n.startswith("api.")]:
            del sys.modules[name]
    import flask_limiter  # noqa: F401 — importing pins the real module


_ensure_real_flask_limiter()

from flask import Flask  # noqa: E402

import api.library as library_mod  # noqa: E402
from api import create_api_blueprint, limiter  # noqa: E402

ADMIN_RAW = "sk_admin_rate_limit_test"
READ_RAW = "sk_read_rate_limit_test"
LEGACY_RAW = "sk_legacy_rate_limit_test"
BOGUS_RAW = "sk_not_a_real_key"

TOTAL_ALBUMS = 20_000  # at limit=200 that is 100 pages — well past the cap


def _record(raw, scope):
    return {
        "id": f"k-{scope}",
        "label": f"rate-limit-{scope}",
        "key_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "key_prefix": raw[:11],
        "scope": scope,
        "created_at": "2026-01-01T00:00:00+00:00",
        "last_used_at": None,
    }


class _Config:
    """Throwaway config manager: an admin key, a read key, and a legacy
    key whose record predates scopes (no "scope" field at all)."""

    def __init__(self):
        legacy = _record(LEGACY_RAW, "admin")
        del legacy["scope"]
        self._keys = [_record(ADMIN_RAW, "admin"), _record(READ_RAW, "read"),
                      legacy]

    def get(self, key, default=None):
        return self._keys if key == "api_keys" else default

    def set(self, *a, **kw):
        return None

    def save_config(self, *a, **kw):
        return None


class _FakeDB:
    """Just enough database for /library/albums: a one-row page and a
    total large enough that the pagination claims 100 pages."""

    def api_list_albums(self, search="", artist_id=None, year=None,
                        page=1, limit=200):
        return {
            "albums": [{"id": "al1", "artist_id": "ar1", "title": "One"}],
            "total": TOTAL_ALBUMS,
        }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(library_mod, "get_database", lambda: _FakeDB())

    app = Flask(__name__)
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)
    # the limiter is a module-level singleton shared with other test
    # files: start from a clean window and hand back a clean one, so no
    # counts leak in either direction. (reset() needs init_app first —
    # the storage is only created there.)
    limiter.reset()
    app.register_blueprint(create_api_blueprint(), url_prefix="/api/v1")
    app.soulsync = {"config_manager": _Config()}
    yield app.test_client()
    limiter.reset()


def _albums_page(client, page, headers=None):
    return client.get(f"/api/v1/library/albums?page={page}&limit=200",
                      headers=headers or {})


# ── the bug: an admin key's paginated walk must not be capped ─────────

def test_admin_key_walk_is_not_rate_limited(client):
    """75 sequential pages with the admin key (what SoulSync mints for
    MetaSync): every one is a 200, none is a 429."""
    headers = {"Authorization": f"Bearer {ADMIN_RAW}"}
    for page in range(1, 76):
        resp = _albums_page(client, page, headers)
        assert resp.status_code == 200, f"page {page} -> {resp.status_code}"
        if page == 1:
            # the walk really does have further to go than the old cap
            assert resp.get_json()["pagination"]["total_pages"] == 100


# ── the protection is intact for everyone else ────────────────────────

def test_no_key_requests_hit_the_cap(client):
    """Unauthenticated requests are still rejected one by one (401) until
    the cap, and the 61st is a 429 carrying the RATE_LIMITED code."""
    statuses = [_albums_page(client, 1).status_code for _ in range(61)]
    assert statuses[:60] == [401] * 60
    assert statuses[60] == 429

    body = _albums_page(client, 1).get_json()
    assert body["error"]["code"] == "RATE_LIMITED"


def test_read_scope_key_stays_capped(client):
    """A read-scope key may fetch albums (200s) but stays on the capped
    tier: the 61st request in the window is a 429. Only admin is exempt."""
    headers = {"X-API-Key": READ_RAW}
    for page in range(1, 61):
        resp = _albums_page(client, page, headers)
        assert resp.status_code == 200, f"page {page} -> {resp.status_code}"
    resp = _albums_page(client, 61, headers)
    assert resp.status_code == 429
    assert resp.get_json()["error"]["code"] == "RATE_LIMITED"


def test_legacy_key_without_scope_is_exempt(client):
    """A key record with no scope field is an older key and counts as
    admin everywhere in the auth system (key_scope's rule) — the limiter
    mirrors that rather than inventing a stricter rule of its own."""
    headers = {"Authorization": f"Bearer {LEGACY_RAW}"}
    for page in range(1, 76):
        resp = _albums_page(client, page, headers)
        assert resp.status_code == 200, f"page {page} -> {resp.status_code}"


def test_admin_traffic_does_not_consume_the_capped_quota(client):
    """Exempt requests are not counted at all: after a full admin walk,
    a keyless caller from the same IP still gets its whole 60 before
    the cap bites."""
    headers = {"Authorization": f"Bearer {ADMIN_RAW}"}
    for page in range(1, 76):
        assert _albums_page(client, page, headers).status_code == 200

    statuses = [_albums_page(client, 1).status_code for _ in range(61)]
    assert statuses[:60] == [401] * 60
    assert statuses[60] == 429


def test_invalid_key_stays_capped(client):
    """A key that matches nothing is just an unauthenticated caller with
    extra steps: 403s until the cap, then a 429."""
    headers = {"Authorization": f"Bearer {BOGUS_RAW}"}
    statuses = [_albums_page(client, 1, headers).status_code
                for _ in range(61)]
    assert statuses[:60] == [403] * 60
    assert statuses[60] == 429


# ── fail closed ───────────────────────────────────────────────────────

def test_exemption_check_fails_closed(client, monkeypatch):
    """If the auth check itself blows up, the request is NOT exempt —
    a broken auth path must never silently disable the limiter."""
    import api.auth as auth_mod
    from api import _rate_limit_exempt

    def _boom():
        raise RuntimeError("auth store unavailable")

    monkeypatch.setattr(auth_mod, "matched_api_key", _boom)
    with client.application.test_request_context(
            "/api/v1/library/albums",
            headers={"Authorization": f"Bearer {ADMIN_RAW}"}):
        assert _rate_limit_exempt() is False
