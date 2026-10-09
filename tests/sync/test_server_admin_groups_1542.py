"""#1542: server-admin playlist grouping."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.sync import server_playlist_access as spa


def _playlist(owner, title="Pl"):
    return SimpleNamespace(id=f"id-{owner}-{title}", title=title, owner=owner)


def _db():
    db = Mock()
    db.get_all_profiles.return_value = []
    return db


def _client(username, admin_map):
    """Navidrome client mock: admin_map maps owner -> is_admin (None = lookup fails)."""
    c = Mock()
    c.username = username
    def _is_admin(owner):
        v = admin_map.get(owner)
        if v is None:
            raise RuntimeError("lookup failed")
        return v
    c.is_server_admin = _is_admin
    return c


def _listed(playlists):
    return playlists


def test_admin_owner_goes_to_server_admin():
    client = _client("me", {"boss": True, "pleb": False})
    playlists = [_listed([_playlist("me"), _playlist("boss"), _playlist("pleb")])]
    with patch.object(spa, "list_playlists", return_value=playlists[0]):
        mine, admin_groups, others = spa.admin_split("navidrome", client, _db())
    assert [p.owner for p in mine] == ["me"]
    assert [g["owner"] for g in admin_groups] == ["boss"]
    assert [g["owner"] for g in others] == ["pleb"]


def test_lookup_failure_keeps_current_grouping():
    client = _client("me", {})  # every lookup raises
    playlists = [_playlist("me"), _playlist("boss")]
    with patch.object(spa, "list_playlists", return_value=playlists):
        mine, admin_groups, others = spa.admin_split("navidrome", client, _db())
    assert admin_groups == []
    assert [g["owner"] for g in others] == ["boss"]


def test_multiple_admins_grouped_separately():
    client = _client("me", {"a1": True, "a2": True})
    playlists = [_playlist("a1", "One"), _playlist("a2", "Two"), _playlist("a1", "Three")]
    with patch.object(spa, "list_playlists", return_value=playlists):
        _, admin_groups, others = spa.admin_split("navidrome", client, _db())
    assert sorted(g["owner"] for g in admin_groups) == ["a1", "a2"]
    assert others == []
    by_owner = {g["owner"]: g for g in admin_groups}
    assert len(by_owner["a1"]["playlists"]) == 2


def test_non_navidrome_server_admin_empty():
    client = Mock()
    with patch.object(spa, "list_playlists", return_value=[]):
        with patch.object(spa, "client_for_profile", return_value=Mock()):
            mine, admin_groups, others = spa.admin_split("plex", client, _db())
    assert admin_groups == []


def test_is_server_admin_caches():
    from core.navidrome_client import NavidromeClient
    c = NavidromeClient.__new__(NavidromeClient)
    c.ensure_connection = Mock(return_value=True)
    calls = []
    def _get_user(username):
        calls.append(username)
        return {"username": username, "adminRole": True}
    c.get_user = _get_user
    assert c.is_server_admin("Boss") is True
    assert c.is_server_admin("boss") is True  # case-insensitive cache hit
    assert len(calls) == 1


def test_is_server_admin_lookup_failure_returns_none():
    from core.navidrome_client import NavidromeClient
    c = NavidromeClient.__new__(NavidromeClient)
    c.ensure_connection = Mock(return_value=True)
    c.get_user = Mock(return_value=None)
    assert c.is_server_admin("ghost") is None
