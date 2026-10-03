"""#1289 item 6 — playlist-ID read-half (follow ID silently).

Rename scenario: a mirrored playlist with a stored, still-valid server
playlist ID follows that ID even after the server playlist was renamed —
no name-match, no duplicate, no prompt.

Fallback scenario: with no stored ID (or a stale / wrong-server-type one),
the legacy case-insensitive name-match runs exactly as before, and a hit
records the discovered server ID so the next sync follows it by ID.
"""

from types import SimpleNamespace

from core.media_server.types import PlaylistInfo
from core.navidrome_client import NavidromeClient
from core.sync.mirrored_server_link import (
    mirrored_pk_from_sync_id,
    resolve_mirrored_server_playlist_id,
)


def _playlist_info(pid, title="My Mix"):
    return PlaylistInfo(
        id=pid,
        title=title,
        description=None,
        duration=0,
        leaf_count=0,
        tracks=[],
        owner="tester",
    )


class _FakeDB:
    """Minimal MusicDatabase double for the link read/write halves."""

    def __init__(self, mirror_row):
        self._row = mirror_row
        self.links = []  # (playlist_id, server_playlist_id, server_type)

    def get_mirrored_playlist(self, playlist_id, profile_id=None):
        return self._row

    def link_mirrored_playlist_to_server(self, playlist_id, server_playlist_id, server_type, *, profile_id=None):
        self.links.append((playlist_id, server_playlist_id, server_type))
        return True


class _FakeClient:
    """Duck-typed Navidrome client double."""

    def __init__(self, by_id=None, by_name=None):
        self._by_id = by_id or {}
        self._by_name = by_name or {}
        self.id_lookups = []
        self.name_lookups = []

    def get_playlist_by_id(self, playlist_id):
        self.id_lookups.append(playlist_id)
        return self._by_id.get(playlist_id)

    def get_playlists_by_name(self, name):
        self.name_lookups.append(name)
        return list(self._by_name.get(name, []))


def _resolve(db, client, **kw):
    return resolve_mirrored_server_playlist_id(
        db,
        client,
        server_type="navidrome",
        mirrored_playlist_id=7,
        playlist_name="My Mix",
        profile_id=1,
        **kw,
    )


# ---------------------------------------------------------------------------
# Resolver: rename scenario (stored ID followed silently)
# ---------------------------------------------------------------------------


def test_stored_id_followed_silently_after_server_rename():
    """The server playlist was renamed; the stored ID is still valid, so the
    sync follows it WITHOUT consulting the name at all."""
    db = _FakeDB({"id": 7, "server_playlist_id": "srv-123", "server_type": "navidrome"})
    client = _FakeClient(
        by_id={"srv-123": _playlist_info("srv-123", title="Renamed On Server")},
        by_name={"My Mix": []},  # old name matches nothing now
    )
    assert _resolve(db, client) == "srv-123"
    assert client.id_lookups == ["srv-123"]
    assert client.name_lookups == []  # name-match never consulted
    assert db.links == []  # already linked; nothing to record


# ---------------------------------------------------------------------------
# Resolver: fallback scenario (no stored ID -> name-match as before)
# ---------------------------------------------------------------------------


def test_no_stored_id_falls_back_to_name_match_and_records():
    """Legacy behaviour preserved: name-match finds the playlist, and the
    discovered server ID is recorded so the NEXT sync follows it by ID."""
    db = _FakeDB({"id": 7, "server_playlist_id": None, "server_type": None})
    client = _FakeClient(by_name={"My Mix": [_playlist_info("srv-9")]})
    assert _resolve(db, client) == "srv-9"
    assert client.name_lookups == ["My Mix"]
    assert db.links == [(7, "srv-9", "navidrome")]


def test_stale_stored_id_falls_back_to_name_match_and_relinks():
    """Server playlist deleted (stored ID invalid) but a same-named playlist
    exists: fall back to the name-match and overwrite the stale link."""
    db = _FakeDB({"id": 7, "server_playlist_id": "srv-dead", "server_type": "navidrome"})
    client = _FakeClient(
        by_id={},  # srv-dead gone
        by_name={"My Mix": [_playlist_info("srv-9")]},
    )
    assert _resolve(db, client) == "srv-9"
    assert db.links == [(7, "srv-9", "navidrome")]


def test_no_stored_id_and_no_name_match_returns_none():
    """Nothing linked, nothing name-matched: the caller runs its normal
    create path (which then links the new playlist)."""
    db = _FakeDB({"id": 7, "server_playlist_id": None, "server_type": None})
    client = _FakeClient(by_name={"My Mix": []})
    assert _resolve(db, client) is None
    assert db.links == []


def test_wrong_server_type_link_is_ignored():
    """A stored ID for Plex must never be used against Navidrome."""
    db = _FakeDB({"id": 7, "server_playlist_id": "plex-1", "server_type": "plex"})
    client = _FakeClient(
        by_id={"plex-1": _playlist_info("plex-1")},
        by_name={"My Mix": [_playlist_info("srv-9")]},
    )
    assert _resolve(db, client) == "srv-9"
    assert client.id_lookups == []  # never validated the Plex ID here
    assert db.links == [(7, "srv-9", "navidrome")]


def test_unknown_mirror_returns_none():
    db = _FakeDB(None)
    client = _FakeClient()
    assert _resolve(db, client) is None
    assert client.id_lookups == [] and client.name_lookups == []


def test_db_failure_degrades_to_none():
    class _BrokenDB:
        def get_mirrored_playlist(self, *a, **k):
            raise RuntimeError("db down")

    assert _resolve(_BrokenDB(), _FakeClient()) is None


# ---------------------------------------------------------------------------
# mirrored_pk_from_sync_id: strict prefixed forms only
# ---------------------------------------------------------------------------


def test_mirrored_pk_from_sync_id_strict():
    assert mirrored_pk_from_sync_id("auto_mirror_42") == 42
    assert mirrored_pk_from_sync_id("youtube_mirrored_7") == 7
    assert mirrored_pk_from_sync_id("mirrored_99") == 99
    # Bare numerics are NOT mirrors here (a Deezer source id must never
    # resolve a mirrored_playlists row by coincidence).
    assert mirrored_pk_from_sync_id("12345") is None
    assert mirrored_pk_from_sync_id("spotify:playlist:abc") is None
    assert mirrored_pk_from_sync_id("") is None
    assert mirrored_pk_from_sync_id(None) is None
    assert mirrored_pk_from_sync_id("auto_mirror_xyz") is None


# ---------------------------------------------------------------------------
# NavidromeClient: playlist_id follow-through on the write methods
# ---------------------------------------------------------------------------


def _raw_client(**stubs):
    c = NavidromeClient.__new__(NavidromeClient)
    c.ensure_connection = lambda: True
    for name, fn in stubs.items():
        setattr(c, name, fn)
    return c


def test_update_playlist_follows_stored_id_without_name_lookup():
    """Raw update_playlist with a valid playlist_id writes straight to that
    ID — get_playlists_by_name is never consulted (rename-safe)."""
    created = {}

    def fake_create(name, tracks, playlist_id=None):
        created["playlist_id"] = playlist_id
        return playlist_id or True

    def fail_if_name_lookup(name):
        raise AssertionError("name-match must not run when following an ID")

    c = _raw_client(
        get_playlist_by_id=lambda pid: _playlist_info(pid),
        get_playlists_by_name=fail_if_name_lookup,
        create_playlist=fake_create,
    )
    result = NavidromeClient.update_playlist.__wrapped__(c, "Old Name", [SimpleNamespace(ratingKey="s1")], playlist_id="srv-123")
    assert result == "srv-123"
    assert created["playlist_id"] == "srv-123"


def test_update_playlist_stale_id_falls_back_to_name_match():
    """A stored ID the server no longer has falls through to the legacy
    name-match path unchanged."""
    created = {}

    def fake_create(name, tracks, playlist_id=None):
        created["playlist_id"] = playlist_id
        return True

    c = _raw_client(
        get_playlist_by_id=lambda pid: None,  # deleted on server
        get_playlists_by_name=lambda name: [_playlist_info("srv-9")],
        create_playlist=fake_create,
    )
    result = NavidromeClient.update_playlist.__wrapped__(c, "My Mix", [SimpleNamespace(ratingKey="s1")], playlist_id="srv-dead")
    assert result is True
    assert created["playlist_id"] == "srv-9"


def test_update_playlist_without_id_keeps_legacy_behaviour():
    """No playlist_id kwarg at all: byte-for-byte the old name-match flow."""
    seen = {}

    def fake_create(name, tracks, playlist_id=None):
        seen["playlist_id"] = playlist_id
        return True

    c = _raw_client(
        get_playlists_by_name=lambda name: [_playlist_info("srv-9")],
        create_playlist=fake_create,
    )
    result = NavidromeClient.update_playlist.__wrapped__(c, "My Mix", [SimpleNamespace(ratingKey="s1")])
    assert result is True
    assert seen["playlist_id"] == "srv-9"


def test_reconcile_follows_stored_id():
    """reconcile_playlist with a valid playlist_id skips its name lookup."""

    def fail_if_name_lookup(name):
        raise AssertionError("name-match must not run when following an ID")

    c = _raw_client(
        get_playlist_by_id=lambda pid: _playlist_info(pid),
        get_playlists_by_name=fail_if_name_lookup,
        get_playlist_tracks=lambda pid: [],
    )
    captured = {}

    def fake_request(endpoint, params=None):
        captured["params"] = params
        return {"status": "ok"}

    c._make_request = fake_request
    result = NavidromeClient.reconcile_playlist.__wrapped__(c, "Old Name", [SimpleNamespace(ratingKey="s1")], playlist_id="srv-123")
    assert result is True
    assert captured["params"]["playlistId"] == "srv-123"


def test_update_playlist_follow_path_still_backs_up_when_enabled(monkeypatch):
    """The opt-in pre-sync backup also runs on the ID-follow path — against
    the playlist's current server-side title, so a renamed playlist is still
    backed up."""
    import core.settings

    class _Cfg:
        def get(self, key, default=None):
            return True if key == "playlist_sync.create_backup" else default

    monkeypatch.setattr(core.settings, "config_manager", _Cfg())
    copied = {}
    created = {}

    c = _raw_client(
        get_playlist_by_id=lambda pid: _playlist_info(pid, title="Renamed On Server"),
        get_playlists_by_name=lambda name: (_ for _ in ()).throw(AssertionError("name-match must not run when following an ID")),
        copy_playlist=lambda src, dst: copied.setdefault("args", (src, dst)) or True,
        create_playlist=lambda name, tracks, playlist_id=None: created.setdefault("playlist_id", playlist_id) or True,
    )
    result = NavidromeClient.update_playlist.__wrapped__(c, "My Mix", [SimpleNamespace(ratingKey="s1")], playlist_id="srv-123")
    # raw return carries create_playlist's new ID contract; the decorator
    # normalizes it to bool for external callers
    assert result == "srv-123"
    assert copied["args"] == ("Renamed On Server", "My Mix Backup")
    assert created["playlist_id"] == "srv-123"


def test_update_playlist_follow_path_skips_backup_when_disabled(monkeypatch):
    import core.settings

    class _Cfg:
        def get(self, key, default=None):
            return default

    monkeypatch.setattr(core.settings, "config_manager", _Cfg())

    def fail_if_backup(*a):
        raise AssertionError("backup must not run when disabled")

    c = _raw_client(
        get_playlist_by_id=lambda pid: _playlist_info(pid),
        copy_playlist=fail_if_backup,
        create_playlist=lambda name, tracks, playlist_id=None: True,
    )
    assert NavidromeClient.update_playlist.__wrapped__(c, "My Mix", [SimpleNamespace(ratingKey="s1")], playlist_id="srv-123") is True


def test_decorated_update_playlist_verifies_against_passed_id(monkeypatch):
    """Through the real write validator: a passed playlist_id is used for the
    pre/post verification instead of a name lookup, and the write lands on
    that ID (the renamed-on-server playlist is followed silently)."""
    import database.music_database as database

    monkeypatch.setattr(database, "get_database", lambda: SimpleNamespace(_get_connection=lambda: None))
    seen = []

    def request(endpoint, params=None, timeout=None):
        seen.append((endpoint, (params or {}).get("id") or (params or {}).get("playlistId")))
        if endpoint == "getPlaylist":
            # renamed on the server; holds exactly the desired track
            return {"playlist": {"id": "srv-123", "name": "Renamed", "entry": [{"id": "s1"}]}}
        if endpoint == "createPlaylist":
            return {"status": "ok"}
        raise AssertionError(endpoint)

    c = _raw_client()
    c._make_request = request
    c.get_playlists_by_name = lambda name: (_ for _ in ()).throw(AssertionError("name-match must not run when following an ID"))

    assert c.update_playlist("Old Name", [SimpleNamespace(ratingKey="s1")], playlist_id="srv-123") is True
    create_ids = [pid for endpoint, pid in seen if endpoint == "createPlaylist"]
    assert create_ids == ["srv-123"]
    assert not any(endpoint == "getPlaylists" for endpoint, _ in seen)


def test_get_playlist_by_id():
    c = _raw_client()
    c._make_request = lambda endpoint, params=None: {
        "playlist": {"id": "p1", "name": "Renamed", "songCount": 3, "comment": "c", "owner": "tester", "duration": 10}
    }
    info = c.get_playlist_by_id("p1")
    assert isinstance(info, PlaylistInfo)
    assert info.id == "p1"
    assert info.title == "Renamed"

    c2 = _raw_client()
    c2._make_request = lambda endpoint, params=None: {"status": "ok"}
    assert c2.get_playlist_by_id("gone") is None
    assert c2.get_playlist_by_id("") is None


def test_create_playlist_returns_server_id():
    """create_playlist returns the new server playlist ID (raw, unwrapped):
    the passed playlist_id for overwrites, the resolved ID for fresh
    creates, False on failure."""
    # overwrite -> echo the given ID
    c = _raw_client()
    c._make_request = lambda endpoint, params=None: {"status": "ok"}
    assert NavidromeClient.create_playlist.__wrapped__(c, "P", [SimpleNamespace(ratingKey="s1")], playlist_id="pl") == "pl"

    # fresh create -> resolved via name lookup
    c2 = _raw_client(get_playlists_by_name=lambda name: [_playlist_info("srv-new")])
    c2._make_request = lambda endpoint, params=None: {"status": "ok"}
    assert NavidromeClient.create_playlist.__wrapped__(c2, "P", [SimpleNamespace(ratingKey="s1")]) == "srv-new"

    # failure -> falsy (existing callers only test truthiness)
    c3 = _raw_client()
    c3._make_request = lambda endpoint, params=None: None
    assert NavidromeClient.create_playlist.__wrapped__(c3, "P", [SimpleNamespace(ratingKey="s1")]) is False


# ---------------------------------------------------------------------------
# resolve_sync_server_playlist_id: the sync-service entry point (gates)
# ---------------------------------------------------------------------------


def _sync_resolve(**kw):
    from core.sync.mirrored_server_link import resolve_sync_server_playlist_id

    args = dict(playlist_id="auto_mirror_7", playlist_name="My Mix", server_type="navidrome", media_client=object(), profile_id=1)
    args.update(kw)
    return resolve_sync_server_playlist_id(**args)


class _NoTouchDB:
    """DB double that explodes if the code under test touches it."""

    def __getattr__(self, name):
        raise AssertionError(f"db.{name} must not be touched")


def test_sync_entry_point_is_navidrome_only():
    # Non-Navidrome server types never consult the link (Plex/Jellyfin keep
    # their existing signatures, which have no playlist_id kwarg) — the DB
    # double is never touched.
    assert _sync_resolve(server_type="plex", db=_NoTouchDB()) is None
    assert _sync_resolve(server_type="jellyfin", db=_NoTouchDB()) is None
    assert _sync_resolve(server_type="soulsync", db=_NoTouchDB()) is None
    assert _sync_resolve(media_client=None, db=_NoTouchDB()) is None


def test_sync_entry_point_ignores_non_mirror_ids():
    """A numeric Deezer-style source id must not resolve a mirror row, so the
    DB double is never touched."""
    assert _sync_resolve(playlist_id="12345", db=_NoTouchDB()) is None
    assert _sync_resolve(playlist_id="spotify:playlist:abc", db=_NoTouchDB()) is None


def test_sync_entry_point_uses_stored_id():
    db = _FakeDB({"id": 7, "server_playlist_id": "srv-123", "server_type": "navidrome"})
    client = _FakeClient(by_id={"srv-123": _playlist_info("srv-123", title="Renamed")})
    assert _sync_resolve(db=db, media_client=client) == "srv-123"
    assert db.links == []  # already linked; nothing to record
