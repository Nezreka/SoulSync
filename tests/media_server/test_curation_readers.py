"""Per-server curation readers (Cremonies, phase 2).

Jellyfin and Navidrome differ in a way that shapes the whole feature:

* Jellyfin's ``UserData`` is per-user but an ADMIN key can read anyone's, so
  one credential covers the server.
* Subsonic scopes ``getStarred2`` and ratings to the AUTHENTICATED user with no
  admin impersonation, so reading another user's favourites needs their own
  password — which is exactly what Cremonies said. Playlists are the exception,
  since ``getPlaylists`` takes an admin-only ``username`` parameter.

No network: the request layer is stubbed. The recurring assertion is that a
failed read OMITS that user rather than recording them as empty, because empty
means "likes nothing" and would withdraw protection.
"""

from __future__ import annotations

import pytest

from core.jellyfin_client import JellyfinClient
from core.navidrome_client import NavidromeClient


# ── Navidrome ─────────────────────────────────────────────────────────────

@pytest.fixture
def nav(monkeypatch):
    client = NavidromeClient.__new__(NavidromeClient)
    client.base_url = "http://nav:4533"
    client.username = "admin"
    client.password = "adminpw"
    client.ensure_connection = lambda: True
    return client


def _nav_stub(responses, seen):
    def _make_request(endpoint, params=None, as_user=None):
        seen.append({"endpoint": endpoint, "params": params, "as_user": as_user})
        value = responses.get(endpoint)
        if isinstance(value, Exception):
            raise value
        return value
    return _make_request


def test_navidrome_reads_starred_for_the_configured_account(nav):
    seen = []
    nav._make_request = _nav_stub({
        "getStarred2": {"starred2": {"song": [
            {"path": "Artist/Album/01 Track.flac", "userRating": 4},
        ]}},
        "getPlaylists": {"playlists": {"playlist": []}},
    }, seen)

    signals = nav.get_curation_signals()
    assert list(signals) == ["admin"]
    row = signals["admin"][0]
    assert row["favorite"] is True and row["rating"] == 4


def test_navidrome_uses_each_users_own_credentials_for_starred(nav):
    """Subsonic has no admin path to someone else's stars, so the request must
    authenticate AS that user."""
    seen = []
    nav._make_request = _nav_stub({
        "getStarred2": {"starred2": {"song": [{"path": "A/B/c.flac"}]}},
        "getPlaylists": {"playlists": {"playlist": []}},
    }, seen)

    nav.get_curation_signals(users=[("alice", "alicepw"), ("bob", "bobpw")])
    starred_calls = [c for c in seen if c["endpoint"] == "getStarred2"]
    assert [c["as_user"] for c in starred_calls] == [("alice", "alicepw"), ("bob", "bobpw")]


def test_navidrome_reads_playlists_as_admin_on_the_users_behalf(nav):
    """Playlists DO have an admin-only username parameter, so they must not
    burn the user's own credentials."""
    seen = []
    nav._make_request = _nav_stub({
        "getStarred2": {"starred2": {}},
        "getPlaylists": {"playlists": {"playlist": [{"id": "p1"}]}},
        "getPlaylist": {"playlist": {"entry": [{"path": "A/B/c.flac"}]}},
    }, seen)

    signals = nav.get_curation_signals(users=[("alice", "alicepw")])
    playlist_call = next(c for c in seen if c["endpoint"] == "getPlaylists")
    assert playlist_call["as_user"] is None, "used alice's credentials for an admin call"
    assert playlist_call["params"] == {"username": "alice"}
    assert signals["alice"][0]["in_playlist"] is True


def test_navidrome_a_failed_user_is_omitted_not_emptied(nav):
    seen = []
    nav._make_request = _nav_stub({
        "getStarred2": RuntimeError("401"),
        "getPlaylists": RuntimeError("401"),
    }, seen)
    assert nav.get_curation_signals(users=[("alice", "pw")]) == {}


def test_navidrome_a_single_song_object_is_handled(nav):
    """Subsonic collapses a one-element list into a bare object."""
    seen = []
    nav._make_request = _nav_stub({
        "getStarred2": {"starred2": {"song": {"path": "A/B/c.flac"}}},
        "getPlaylists": {"playlists": {"playlist": []}},
    }, seen)
    assert len(nav.get_curation_signals()["admin"]) == 1


def test_navidrome_auth_override_does_not_mutate_the_client(nav):
    """The identity must be per-call. Setting it on the shared client is how
    _apply_profile_library leaks one user's identity into every other
    caller — this must not repeat that."""
    before = (nav.username, nav.password)
    params = nav._generate_auth_params("alice", "alicepw")
    assert params["u"] == "alice"
    assert (nav.username, nav.password) == before


def test_navidrome_auth_defaults_to_the_configured_account(nav):
    assert nav._generate_auth_params()["u"] == "admin"


# ── Jellyfin ──────────────────────────────────────────────────────────────

@pytest.fixture
def jf():
    client = JellyfinClient.__new__(JellyfinClient)
    client.base_url = "http://jf:8096"
    client.user_id = "admin-id"
    client.music_library_id = None
    client.ensure_connection = lambda: True
    return client


def test_jellyfin_reads_every_users_favourites_with_one_admin_key(jf):
    jf.get_available_users = lambda: [{"id": "u1", "name": "alice"},
                                      {"id": "u2", "name": "bob"}]
    seen = []

    def _make_request(endpoint, params=None):
        seen.append(endpoint)
        return {"Items": [{
            "Path": "/music/Artist/Album/01 Track.flac",
            "UserData": {"IsFavorite": True, "Rating": 5},
        }]}

    jf._make_request = _make_request
    signals = jf.get_curation_signals()
    assert sorted(signals) == ["alice", "bob"]
    assert signals["alice"][0]["favorite"] is True
    assert any("/Users/u1/Items" in e for e in seen)
    assert any("/Users/u2/Items" in e for e in seen)


def test_jellyfin_skips_items_that_are_neither_favourited_nor_rated(jf):
    jf.get_available_users = lambda: [{"id": "u1", "name": "alice"}]
    jf._make_request = lambda endpoint, params=None: {"Items": [
        {"Path": "/music/a.flac", "UserData": {"IsFavorite": False}},
        {"Path": "/music/b.flac", "UserData": {"IsFavorite": True}},
    ]}
    signals = jf.get_curation_signals()
    assert [r["path"] for r in signals["alice"]] == ["/music/b.flac"]


def test_jellyfin_a_failed_user_is_omitted_not_emptied(jf):
    jf.get_available_users = lambda: [{"id": "u1", "name": "alice"}]

    def _boom(endpoint, params=None):
        raise RuntimeError("500")

    jf._make_request = _boom
    assert jf.get_curation_signals() == {}


def test_jellyfin_no_users_is_not_an_error(jf):
    jf.get_available_users = lambda: []
    assert jf.get_curation_signals() == {}


def test_jellyfin_user_enumeration_failure_returns_nothing(jf):
    def _boom():
        raise RuntimeError("no admin rights")

    jf.get_available_users = _boom
    assert jf.get_curation_signals() == {}
