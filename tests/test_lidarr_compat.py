"""lidarr compatible api (#1799, Topaze77).

request apps that only talk to lidarr (singarr, musicseerr 1.x) point at
/lidarr on soulsync. these tests replay the exact call sequences those two
clients make, read from their source, against the real blueprint and service.
musicbrainz, the wishlist and the library check are stand-ins, so nothing
here reaches the network or a real database.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import uuid

import pytest
from flask import Flask

import api.lidarr_compat as lc
from core.lidarr_compat import resources
from core.lidarr_compat.commands import CommandRunner
from core.lidarr_compat.service import LidarrCompat, MusicBrainzSource
from core.lidarr_compat.store import LidarrStore, ensure_schema


_MBIDS = iter(range(1, 10_000))


def _mbid():
    # deterministic, not uuid4: STUDIO lands in a parametrize id, and pytest-xdist
    # refuses to run when its workers collect different test names
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"soulsync-lidarr-test-{next(_MBIDS)}"))


ARTIST = _mbid()
STUDIO = _mbid()      # plain studio album
LIVE = _mbid()        # album + live
SINGLE = _mbid()
EP = _mbid()
OTHER_ARTIST = _mbid()
OTHER_ALBUM = _mbid()

GROUPS = {
    STUDIO: {"mbid": STUDIO, "title": "Studio One", "disambiguation": "", "album_type": "Album",
             "secondary_types": [], "release_date": "2001-05-02", "artist_mbid": ARTIST,
             "artist_name": "The Band"},
    LIVE: {"mbid": LIVE, "title": "Live Somewhere", "disambiguation": "", "album_type": "Album",
           "secondary_types": ["Live"], "release_date": "2003", "artist_mbid": ARTIST,
           "artist_name": "The Band"},
    SINGLE: {"mbid": SINGLE, "title": "A Single", "disambiguation": "", "album_type": "Single",
             "secondary_types": [], "release_date": "2000-01", "artist_mbid": ARTIST,
             "artist_name": "The Band"},
    EP: {"mbid": EP, "title": "Late EP", "disambiguation": "", "album_type": "EP",
         "secondary_types": [], "release_date": "2010-02-02", "artist_mbid": ARTIST,
         "artist_name": "The Band"},
    OTHER_ALBUM: {"mbid": OTHER_ALBUM, "title": "Elsewhere", "disambiguation": "",
                  "album_type": "Album", "secondary_types": [], "release_date": "1999",
                  "artist_mbid": OTHER_ARTIST, "artist_name": "Someone Else"},
}


class FakeMB:
    def __init__(self):
        self.calls = []

    def artist(self, mbid):
        self.calls.append(("artist", mbid))
        names = {ARTIST: "The Band", OTHER_ARTIST: "Someone Else"}
        if mbid not in names:
            return None
        return {"mbid": mbid, "name": names[mbid], "disambiguation": "", "artist_type": "Group"}

    def search_artists(self, name, limit=10):
        self.calls.append(("search", name))
        return [self.artist(ARTIST)] if "band" in name.lower() else []

    def release_group(self, mbid):
        self.calls.append(("release_group", mbid))
        return dict(GROUPS[mbid]) if mbid in GROUPS else None

    def release_groups(self, artist_mbid):
        self.calls.append(("release_groups", artist_mbid))
        return [dict(g) for g in GROUPS.values() if g["artist_mbid"] == artist_mbid]

    def album(self, mbid):
        self.calls.append(("album", mbid))
        group = GROUPS[mbid]
        return {"id": mbid, "name": group["title"], "artists": [{"name": group["artist_name"], "id": ""}],
                "images": [], "album_type": group["album_type"].lower(),
                "release_date": group["release_date"], "total_tracks": 2,
                "tracks": [{"id": f"{mbid}-1", "name": "First", "artists": [{"name": group["artist_name"]}],
                            "duration_ms": 1000, "track_number": 1, "disc_number": 1},
                           {"id": f"{mbid}-2", "name": "Second", "artists": [{"name": group["artist_name"]}],
                            "duration_ms": 2000, "track_number": 2, "disc_number": 1}]}


class Harness:
    def __init__(self, tmp_path):
        path = str(tmp_path / "lidarr.db")
        conn = sqlite3.connect(path)
        ensure_schema(conn.cursor())
        conn.commit()
        conn.close()
        self.clock = [1000.0]
        self.mb = FakeMB()
        self.queued = []
        self.owned = {}           # title -> owned count
        self.ownership_calls = []
        self.store = LidarrStore(lambda: sqlite3.connect(path), now=lambda: self.clock[0])
        self.svc = LidarrCompat(
            self.store, self.mb, queue_album=self._queue, ownership=self._own,
            metadata_profile=lambda pid: resources.metadata_profiles()[pid - 1],
            root_folder=lambda: "/music/Transfer", commands=CommandRunner(inline=True),
            now=lambda: self.clock[0])

    def _queue(self, album, artist_row):
        self.queued.append((album["id"], artist_row.get("name"), [t["id"] for t in album["tracks"]]))
        return len(album["tracks"])

    def _own(self, title, artist_name, track_count):
        self.ownership_calls.append((title, artist_name, track_count))
        return self.owned.get(title, 0), track_count or 0


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


# ---- the flask side: config, keys, a real blueprint ---------------------------

ADMIN_RAW, READ_RAW = "sk_lidarr-admin", "sk_lidarr-read"


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class FakeConfig:
    def __init__(self, enabled=True, profile_id=1):
        self.d = {
            "lidarr_compat.enabled": enabled,
            "lidarr_compat.profile_id": profile_id,
            "api_keys": [{"id": "a", "key_hash": _hash(ADMIN_RAW), "label": "seerr", "scope": "admin"},
                         {"id": "r", "key_hash": _hash(READ_RAW), "label": "dash", "scope": "read"}],
            "soulseek.transfer_path": "/music/Transfer",
        }

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value

    def get_active_media_server(self):
        return "plex"


class FakeDB:
    def __init__(self, profiles=(1,)):
        self.profiles = set(profiles)
        self.completeness_calls = []
        self.answer = (None, 0.0, 0, 0, False, [])

    def get_profile(self, pid):
        return {"id": pid} if pid in self.profiles else None

    def check_album_exists_with_completeness(self, title, artist, expected_track_count=None):
        self.completeness_calls.append((title, artist, expected_track_count))
        return self.answer


@pytest.fixture
def client(h, monkeypatch):
    cfg = FakeConfig()
    db = FakeDB()
    monkeypatch.setattr(lc, "config_manager", cfg)
    monkeypatch.setattr(lc, "get_database", lambda: db)
    monkeypatch.setattr(lc, "_service", h.svc)
    # the usage stamp would write the key list back through key_store
    from api import auth
    monkeypatch.setattr(auth, "_should_persist_usage", lambda *a, **k: False)
    app = Flask(__name__)
    app.soulsync = {"config_manager": cfg}
    app.register_blueprint(lc.create_blueprint())
    c = app.test_client()
    c.cfg = cfg
    c.db = db
    return c


ADMIN = {"X-Api-Key": ADMIN_RAW}
BASE = "/lidarr/api/v1"


# ---- access ------------------------------------------------------------------

def test_off_by_default_answers_like_the_route_is_not_there(client):
    client.cfg.d["lidarr_compat.enabled"] = False
    resp = client.get(f"{BASE}/system/status", headers=ADMIN)
    assert resp.status_code == 404


def test_no_key_and_a_read_key_are_both_refused(client):
    assert client.get(f"{BASE}/system/status").status_code == 401
    assert client.get(f"{BASE}/system/status", headers={"X-Api-Key": "sk_wrong"}).status_code == 401
    # a read key can't add albums, so it can't use this at all
    assert client.get(f"{BASE}/system/status", headers={"X-Api-Key": READ_RAW}).status_code == 401


def test_admin_key_gets_a_version_singarr_accepts(client):
    resp = client.get(f"{BASE}/system/status", headers=ADMIN)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["version"] and body["appName"] == "Lidarr"


def test_profiles_and_root_folder_are_never_empty(client):
    # singarr refuses to add anything when any of these lists is empty
    for path in ("qualityprofile", "metadataprofile", "rootfolder"):
        rows = client.get(f"{BASE}/{path}", headers=ADMIN).get_json()
        assert rows and rows[0]["id"] == 1, path
    assert client.get(f"{BASE}/rootfolder", headers=ADMIN).get_json()[0]["path"] == "/music/Transfer"


# ---- singarr's addAlbumToLidarr, call for call --------------------------------

def test_singarr_album_request_for_a_new_artist_queues_that_album_only(client, h):
    term = {"term": f"lidarr:{STUDIO}"}
    album = client.get(f"{BASE}/album/lookup", query_string=term, headers=ADMIN).get_json()[0]
    assert album["foreignAlbumId"] == STUDIO
    assert album["artist"]["foreignArtistId"] == ARTIST
    assert "id" not in album  # not added yet

    assert client.get(f"{BASE}/artist", headers=ADMIN).get_json() == []
    artist = client.get(f"{BASE}/artist/lookup", query_string={"term": f"lidarr:{ARTIST}"},
                        headers=ADMIN).get_json()[0]
    created = client.post(f"{BASE}/artist", headers=ADMIN, json={
        **artist, "qualityProfileId": 1, "metadataProfileId": 1, "rootFolderPath": "/music/Transfer",
        "monitored": True, "addOptions": {"monitor": "none", "searchForMissingAlbums": False}})
    assert created.status_code == 201
    artist_id = created.get_json()["id"]
    assert client.put(f"{BASE}/artist/{artist_id}", headers=ADMIN,
                      json={**created.get_json(), "monitored": True}).status_code == 202

    # singarr polls this right after the add, the album has to be there already
    albums = client.get(f"{BASE}/album", query_string={"artistId": artist_id}, headers=ADMIN).get_json()
    wanted = next(a for a in albums if a["foreignAlbumId"] == STUDIO)
    assert not wanted["monitored"]
    assert client.put(f"{BASE}/album/monitor", headers=ADMIN,
                      json={"albumIds": [wanted["id"]], "monitored": True}).status_code == 202
    cmd = client.post(f"{BASE}/command", headers=ADMIN,
                      json={"name": "AlbumSearch", "albumIds": [wanted["id"]]})
    assert cmd.status_code == 201
    assert cmd.get_json()["status"] == "completed"

    # only the album asked for, not the rest of the discography
    assert [q[0] for q in h.queued] == [STUDIO]
    assert h.queued[0][2] == [f"{STUDIO}-1", f"{STUDIO}-2"]


def test_singarr_status_sync_reads_downloaded_once_the_library_has_it(client, h):
    artist_row, _ = h.svc.add_artist({"foreignArtistId": ARTIST, "addOptions": {"monitor": "none"}})
    row = h.store.album_by_mbid(STUDIO)
    h.svc.set_monitored([row["id"]], True)
    h.svc.search_albums([row["id"]])

    def status():
        albums = client.get(f"{BASE}/album", query_string={"artistId": artist_row["id"]},
                            headers=ADMIN).get_json()
        album = next(a for a in albums if a["foreignAlbumId"] == STUDIO)
        # singarr's own mapping
        if album["statistics"]["percentOfTracks"] == 100:
            return "downloaded"
        return "downloading" if album["grabbed"] else "found"

    assert status() == "downloading"
    h.owned["Studio One"] = 2
    h.clock[0] += 400  # past the status ttl
    assert status() == "downloaded"
    # the check asked about the tracklist we queued, not a guess
    assert h.ownership_calls[-1] == ("Studio One", "The Band", 2)


# ---- musicseerr 1.x add_album, call for call ----------------------------------

def test_musicseerr_album_request_creates_artist_unmonitored_then_monitors_the_album(client, h):
    lookup = client.get(f"{BASE}/album/lookup", query_string={"term": f"mbid:{LIVE}"},
                        headers=ADMIN).get_json()
    candidate = next(a for a in lookup if a["foreignAlbumId"] == LIVE)
    artist_mbid = candidate["artist"].get("mbId") or candidate["artist"]["foreignArtistId"]
    assert artist_mbid == ARTIST

    assert client.get(f"{BASE}/artist", query_string={"mbId": artist_mbid}, headers=ADMIN).get_json() == []
    root = client.get(f"{BASE}/rootfolder", headers=ADMIN).get_json()[0]
    remote = client.get(f"{BASE}/artist/lookup", query_string={"term": f"mbid:{artist_mbid}"},
                        headers=ADMIN).get_json()[0]
    created = client.post(f"{BASE}/artist", headers=ADMIN, json={
        "artistName": remote["artistName"], "mbId": artist_mbid, "foreignArtistId": artist_mbid,
        "qualityProfileId": root["defaultQualityProfileId"],
        "metadataProfileId": root["defaultMetadataProfileId"], "rootFolderPath": root["path"],
        "monitored": False, "monitorNewItems": "none",
        "addOptions": {"monitor": "none", "monitored": False, "searchForMissingAlbums": False}})
    artist_id = created.get_json()["id"]
    refresh = client.post(f"{BASE}/command", headers=ADMIN, json={"name": "RefreshArtist", "artistId": artist_id})
    cid = refresh.get_json()["id"]
    assert client.get(f"{BASE}/command/{cid}", headers=ADMIN).get_json()["status"] == "completed"
    # _wait_for_artist_commands_to_complete: nothing left queued or started
    assert all(c["status"] not in ("queued", "started")
               for c in client.get(f"{BASE}/command", headers=ADMIN).get_json())

    found = client.get(f"{BASE}/album", query_string={"foreignAlbumId": LIVE}, headers=ADMIN).get_json()
    assert len(found) == 1 and not found[0]["monitored"]
    album = client.get(f"{BASE}/album/{found[0]['id']}", headers=ADMIN).get_json()
    updated = client.put(f"{BASE}/album/{album['id']}", headers=ADMIN, json={**album, "monitored": True})
    assert updated.get_json()["monitored"] is True
    client.post(f"{BASE}/command", headers=ADMIN, json={"name": "AlbumSearch", "albumIds": [album["id"]]})

    # _unmonitor_auto_monitored_albums finds nothing else turned on
    others = client.get(f"{BASE}/album", query_string={"artistId": artist_id}, headers=ADMIN).get_json()
    assert [a["foreignAlbumId"] for a in others if a["monitored"]] == [LIVE]
    assert [q[0] for q in h.queued] == [LIVE]


def test_musicseerr_direct_album_post_for_an_album_we_already_have_is_not_refused(client, h):
    first = client.post(f"{BASE}/album", headers=ADMIN, json={
        "foreignAlbumId": OTHER_ALBUM, "artist": {"foreignArtistId": OTHER_ARTIST},
        "monitored": True, "addOptions": {"searchForNewAlbum": True}})
    assert first.status_code == 201
    again = client.post(f"{BASE}/album", headers=ADMIN, json={
        "foreignAlbumId": OTHER_ALBUM, "artist": {"foreignArtistId": OTHER_ARTIST}, "monitored": True})
    assert again.get_json()["id"] == first.get_json()["id"]
    assert len(h.store.albums(mbid=OTHER_ALBUM)) == 1
    assert len(h.store.artists()) == 1  # the album's artist came along, once
    assert [q[0] for q in h.queued] == [OTHER_ALBUM]


def test_queue_history_and_tracks_answer_in_lidarr_shape(client):
    queue = client.get(f"{BASE}/queue", query_string={"page": 1, "pageSize": 200}, headers=ADMIN).get_json()
    assert queue["records"] == [] and queue["totalRecords"] == 0
    history = client.get(f"{BASE}/history", query_string={"albumId": 3}, headers=ADMIN).get_json()
    assert history["records"] == []
    assert client.get(f"{BASE}/trackfile", query_string={"albumId": 3}, headers=ADMIN).get_json() == []


# ---- artist requests --------------------------------------------------------

def test_monitor_all_on_an_artist_means_studio_albums_not_every_single(h):
    h.svc.add_artist({"foreignArtistId": ARTIST, "metadataProfileId": 1,
                      "addOptions": {"monitor": "all", "searchForMissingAlbums": True}})
    monitored = {a["mbid"] for a in h.store.albums() if a["monitored"]}
    assert monitored == {STUDIO}
    assert [q[0] for q in h.queued] == [STUDIO]
    # every release group is still listed, so a later single request works
    assert {a["mbid"] for a in h.store.albums()} == {STUDIO, LIVE, SINGLE, EP}


def test_everything_profile_and_latest_option(h):
    row, _ = h.svc.add_artist({"foreignArtistId": ARTIST, "metadataProfileId": 2,
                               "addOptions": {"monitor": "latest"}})
    assert [a["mbid"] for a in h.store.albums(artist_id=row["id"]) if a["monitored"]] == [EP]
    assert h.queued == []  # no search asked for


def test_metadata_profile_put_only_changes_allowed_flags(client):
    prof = client.get(f"{BASE}/metadataprofile/1", headers=ADMIN).get_json()
    for item in prof["primaryAlbumTypes"]:
        if item["albumType"]["name"] == "EP":
            item["allowed"] = True
    prof["name"] = "renamed"
    saved = client.put(f"{BASE}/metadataprofile/1", headers=ADMIN, json=prof).get_json()
    assert saved["name"] == "Standard"
    again = client.get(f"{BASE}/metadataprofile/1", headers=ADMIN).get_json()
    allowed = {i["albumType"]["name"] for i in again["primaryAlbumTypes"] if i["allowed"]}
    assert allowed == {"Album", "EP"}


# ---- nothing destructive ------------------------------------------------------

def test_refresh_only_adds_and_keeps_ids(h):
    row, _ = h.svc.add_artist({"foreignArtistId": ARTIST, "addOptions": {"monitor": "none"}})
    before = {a["mbid"]: a["id"] for a in h.store.albums()}
    h.svc.set_monitored([before[STUDIO]], True)
    real = h.mb.release_groups
    h.mb.release_groups = lambda mbid: []  # musicbrainz down
    assert h.svc.refresh_artist(row["id"]) == 0
    h.mb.release_groups = real
    assert h.svc.refresh_artist(row["id"]) == 0
    after = {a["mbid"]: a["id"] for a in h.store.albums()}
    assert after == before
    assert h.store.album(before[STUDIO])["monitored"] is True


def test_delete_with_delete_files_only_forgets_the_artist(client, h):
    row, _ = h.svc.add_artist({"foreignArtistId": ARTIST, "addOptions": {"monitor": "none"}})
    resp = client.delete(f"{BASE}/artist/{row['id']}", query_string={"deleteFiles": "true"}, headers=ADMIN)
    assert resp.status_code == 200
    assert h.store.artists() == [] and h.store.albums() == []


def test_unknown_ids_are_404_not_500(client):
    assert client.get(f"{BASE}/artist/99", headers=ADMIN).status_code == 404
    assert client.get(f"{BASE}/album/99", headers=ADMIN).status_code == 404
    assert client.put(f"{BASE}/album/99", headers=ADMIN, json={"monitored": True}).status_code == 404
    bad = client.post(f"{BASE}/artist", headers=ADMIN, json={"foreignArtistId": "nope"})
    assert bad.status_code == 400


def test_unknown_command_still_completes(client):
    # a client waiting on a command we don't run must not wait forever
    cmd = client.post(f"{BASE}/command", headers=ADMIN, json={"name": "RescanFolders"}).get_json()
    assert cmd["status"] == "completed"


def test_background_runner_finishes_and_survives_a_failing_command():
    runner = CommandRunner()
    boom = runner.submit("AlbumSearch", {}, lambda: 1 / 0)
    ok = runner.submit("AlbumSearch", {}, lambda: "fine")
    import time
    deadline = time.time() + 5
    while time.time() < deadline and runner.get(ok["id"])["status"] != "completed":
        time.sleep(0.01)
    assert runner.get(boom["id"])["status"] == "failed"
    assert runner.get(ok["id"])["status"] == "completed"


# ---- the real seams: wishlist add and library check ---------------------------

class _FakeWishlistService:
    def __init__(self):
        self.calls = []

    def add_track_to_wishlist(self, **kwargs):
        self.calls.append(kwargs)
        return True


def test_queue_album_goes_through_the_album_modal_add_on_the_picked_profile(monkeypatch):
    import core.library_scope as scope
    import core.settings as settings
    import core.wishlist.routes as routes

    svc = _FakeWishlistService()
    monkeypatch.setattr(routes, "get_wishlist_service", lambda: svc)
    monkeypatch.setattr(scope, "library_scope_for_profile", lambda pid: "shared")
    real_get = settings.config_manager.get
    monkeypatch.setattr(settings.config_manager, "get",
                        lambda k, d=None: True if k == "wishlist.allow_duplicate_tracks" else real_get(k, d))
    monkeypatch.setattr(lc, "config_manager", FakeConfig(profile_id=5))
    monkeypatch.setattr(lc, "get_database", lambda: FakeDB(profiles=(1, 5)))

    album = FakeMB().album(STUDIO)
    queued = lc._queue_album(album, {"mbid": ARTIST, "name": "The Band"})

    assert queued == 2
    assert [c["profile_id"] for c in svc.calls] == [5, 5]
    first = svc.calls[0]
    assert first["source_type"] == "album"
    assert first["user_initiated"] is True
    assert first["track_data"]["album"]["name"] == "Studio One"
    assert first["track_data"]["album"]["id"] == STUDIO
    assert [c["track_data"]["track_number"] for c in svc.calls] == [1, 2]
    assert first["source_context"]["requested_via"] == "lidarr_api"


def test_a_deleted_request_profile_falls_back_to_admin(monkeypatch):
    monkeypatch.setattr(lc, "config_manager", FakeConfig(profile_id=7))
    monkeypatch.setattr(lc, "get_database", lambda: FakeDB(profiles=(1,)))
    assert lc.request_profile_id() == 1


def test_ownership_passes_the_tracklist_size_and_reads_owned_counts(monkeypatch):
    import core.library_scope as scope
    db = FakeDB()
    monkeypatch.setattr(scope, "library_scope_for_profile", lambda pid: "shared")
    monkeypatch.setattr(lc, "config_manager", FakeConfig())
    monkeypatch.setattr(lc, "get_database", lambda: db)

    assert lc._ownership("Studio One", "The Band", 12) == (0, 12)
    db.answer = (object(), 0.95, 7, 10, False, ["FLAC"])
    assert lc._ownership("Studio One", "The Band", 12) == (7, 12)
    assert lc._ownership("Studio One", "The Band", None) == (7, 10)
    assert db.completeness_calls[0] == ("Studio One", "The Band", 12)


def test_musicbrainz_source_flattens_release_groups():
    class Raw:
        def get_release_group(self, mbid, includes=None):
            assert includes == ["artist-credits"]
            return {"id": mbid, "title": "T", "primary-type": "Album", "secondary-types": ["Live"],
                    "first-release-date": "1999-02",
                    "artist-credit": [{"name": "A", "artist": {"id": ARTIST, "name": "A"}}]}

        def browse_artist_release_groups(self, mbid, release_types=None, limit=100, offset=0):
            return [{"id": f"rg-{offset + i}", "title": str(i)} for i in range(100 if offset == 0 else 3)]

    src = MusicBrainzSource(client=Raw(), search_client=object())
    group = src.release_group(STUDIO)
    assert group["artist_mbid"] == ARTIST and group["secondary_types"] == ["Live"]
    assert len(src.release_groups(ARTIST)) == 103  # walks past the first page


def test_store_schema_is_additive(tmp_path):
    path = os.path.join(tempfile.mkdtemp(dir=tmp_path), "x.db")
    conn = sqlite3.connect(path)
    ensure_schema(conn.cursor())
    ensure_schema(conn.cursor())  # runs on every boot
    conn.commit()
    conn.close()


# ---- the pure bits ------------------------------------------------------------

@pytest.mark.parametrize("term,expected", [
    (f"lidarr:{STUDIO}", {"mbid": STUDIO}),
    (f"mbid:{STUDIO.upper()}", {"mbid": STUDIO}),
    (STUDIO, {"mbid": STUDIO}),
    ("the band", {"name": "the band"}),
])
def test_parse_term(term, expected):
    assert resources.parse_term(term) == expected


def test_dates_and_stats():
    assert resources.iso_date("1997") == "1997-01-01T00:00:00Z"
    assert resources.iso_date("1997-5") == "1997-05-01T00:00:00Z"
    assert resources.iso_date("") is None
    assert resources.statistics(12, 10)["percentOfTracks"] == 100.0
    assert resources.statistics(1, 0)["percentOfTracks"] == 0.0


def test_album_looked_up_first_is_indexed_even_past_the_browse_cap(client, h):
    """radiohead has more release groups than the browse walks. singarr looks
    the album up, adds the artist, then wants the album in the artist's list."""
    h.mb.release_groups = lambda mbid: []  # the album sits past the cap
    client.get(f"{BASE}/album/lookup", query_string={"term": f"lidarr:{STUDIO}"}, headers=ADMIN)
    created = client.post(f"{BASE}/artist", headers=ADMIN, json={
        "foreignArtistId": ARTIST, "addOptions": {"monitor": "none"}}).get_json()
    albums = client.get(f"{BASE}/album", query_string={"artistId": created["id"]}, headers=ADMIN).get_json()
    assert [a["foreignAlbumId"] for a in albums] == [STUDIO]
