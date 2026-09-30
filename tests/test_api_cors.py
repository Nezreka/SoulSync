"""CORS helpers for the JSON API (core/security/cors.py).

Pins the exact behavior the browser-extension fix depends on:
- OPTIONS preflights to /api/* are answered with CORS headers (so the
  web_server before_request hook can short-circuit before the auth gates).
- Non-OPTIONS requests and non-API paths are left alone.
- API responses get Access-Control-Allow-Origin.
"""

from __future__ import annotations

from core.security.cors import is_api_path, preflight_headers, response_headers


# ── is_api_path ──────────────────────────────────────────────────────────

def test_api_paths_recognized():
    assert is_api_path("/api/v1/playlists") is True
    assert is_api_path("/api/image-proxy") is True
    assert is_api_path("/api/server-activity/sessions") is True
    assert is_api_path("/api/") is True


def test_non_api_paths_rejected():
    assert is_api_path("/") is False
    assert is_api_path("/dashboard") is False
    assert is_api_path("/static/app.js") is False
    assert is_api_path("/socket.io/") is False
    assert is_api_path("") is False
    assert is_api_path(None) is False


# ── preflight_headers ────────────────────────────────────────────────────

def test_preflight_answered_for_api_options():
    headers = preflight_headers("/api/v1/playlists", "OPTIONS")
    assert headers is not None
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "Authorization" in headers["Access-Control-Allow-Headers"]
    assert "Content-Type" in headers["Access-Control-Allow-Headers"]
    assert "GET" in headers["Access-Control-Allow-Methods"]
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert "OPTIONS" in headers["Access-Control-Allow-Methods"]


def test_preflight_ignored_for_non_options():
    assert preflight_headers("/api/v1/playlists", "GET") is None
    assert preflight_headers("/api/v1/playlists", "POST") is None


def test_preflight_ignored_for_non_api_paths():
    assert preflight_headers("/", "OPTIONS") is None
    assert preflight_headers("/socket.io/", "OPTIONS") is None


def test_preflight_method_case_insensitive():
    assert preflight_headers("/api/v1/playlists", "options") is not None


# ── response_headers ─────────────────────────────────────────────────────

def test_api_responses_get_allow_origin():
    headers = response_headers("/api/v1/playlists")
    assert headers == {"Access-Control-Allow-Origin": "*"}


def test_non_api_responses_get_nothing():
    assert response_headers("/") == {}
    assert response_headers("/dashboard") == {}
    assert response_headers(None) == {}
