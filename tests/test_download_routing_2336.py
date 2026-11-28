"""tests for multiple download client instances and per-library routing (#2336)."""

from __future__ import annotations

from flask import Flask

from api.download_clients import bp as download_clients_bp
from core import torrent_clients as TC
from core import usenet_clients as UC
from core.downloads import routing as R
from core.video import client_grab


class _MemConfig:
    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def _patch_cfg(monkeypatch, initial=None):
    cfg = _MemConfig(initial)
    monkeypatch.setattr(TC, "config_manager", cfg)
    monkeypatch.setattr(UC, "config_manager", cfg)
    monkeypatch.setattr(R, "config_manager", cfg)
    monkeypatch.setattr("core.torrent_clients.qbittorrent.config_manager", cfg)
    monkeypatch.setattr("core.torrent_clients.deluge.config_manager", cfg)
    monkeypatch.setattr("core.torrent_clients.transmission.config_manager", cfg)
    monkeypatch.setattr("core.usenet_clients.sabnzbd.config_manager", cfg)
    monkeypatch.setattr("core.usenet_clients.nzbget.config_manager", cfg)
    return cfg


def test_multiple_torrent_and_usenet_clients_and_per_library_routing(monkeypatch, tmp_path):
    _patch_cfg(
        monkeypatch,
        {
            "torrent_client.type": "qbittorrent",
            "torrent_client.url": "http://qbit-main:8080",
            "torrent_client.category": "soulsync",
            "torrent_client.save_path": "/downloads/default",
            "usenet_client.type": "sabnzbd",
            "usenet_client.url": "http://sab-main:8080",
            "usenet_client.api_key": "mainkey",
            "usenet_client.category": "soulsync",
        },
    )

    # add a second torrent client (Deluge for 4K) and a second usenet client (NZBGet)
    TC.upsert_client(
        {
            "id": "deluge-4k",
            "name": "Deluge 4K",
            "type": "deluge",
            "url": "http://deluge-4k:8112",
            "password": "secret",
            "category": "movies-4k",
            "save_path": "/remote/4k",
            "completed_path": str(tmp_path / "local_4k"),
        }
    )
    UC.upsert_client(
        {
            "id": "nzbget-tv",
            "name": "NZBGet TV",
            "type": "nzbget",
            "url": "http://nzbget:6789",
            "username": "nzb",
            "password": "pw",
            "category": "tv-hd",
        }
    )

    assert [c["id"] for c in TC.list_configured_clients()] == ["default", "deluge-4k"]
    assert [c["id"] for c in UC.list_configured_clients()] == ["default", "nzbget-tv"]
    # secrets are never leaked by default
    assert "password" not in TC.get_client_profile("deluge-4k")
    assert TC.get_client_profile("deluge-4k")["has_password"] is True

    # adapters instantiate with their own client type & URL
    a_default = TC.get_adapter_for_client("default")
    a_deluge = TC.get_adapter_for_client("deluge-4k")
    assert isinstance(a_default, TC.QBittorrentAdapter)
    assert isinstance(a_deluge, TC.DelugeAdapter)
    assert a_default._url == "http://qbit-main:8080"
    assert a_deluge._url == "http://deluge-4k:8112"

    u_default = UC.get_adapter_for_client("default")
    u_nzbget = UC.get_adapter_for_client("nzbget-tv")
    assert isinstance(u_default, UC.SABnzbdAdapter)
    assert isinstance(u_nzbget, UC.NZBGetAdapter)

    # configure independent routing for Movies 4K, Stand-Up Comedy, Audiobooks
    R.set_library_overrides(
        "video_library:4k",
        {
            "torrent_client_id": "deluge-4k",
            "prowlarr_indexer_ids": [10, 12],
            "download_source_priority": ["torrent", "usenet"],
        },
    )
    R.set_library_overrides(
        "audiobooks",
        {
            "torrent_client_id": "default",
            "torrent_category": "audiobooks",
            "torrent_save_path": "/downloads/audiobooks",
            "prowlarr_indexer_ids": [99],
        },
    )

    r_4k = R.resolve_library_routing("video_library:4k")
    assert r_4k.torrent_client_id == "deluge-4k"
    assert r_4k.torrent_category == "movies-4k"  # inherited from deluge-4k profile!
    assert r_4k.torrent_save_path == "/remote/4k"
    assert r_4k.prowlarr_indexer_ids == [10, 12]
    assert r_4k.download_source_priority == ["torrent", "usenet"]

    r_ab = R.resolve_library_routing("audiobooks")
    assert r_ab.torrent_client_id == "default"
    assert r_ab.torrent_category == "audiobooks"
    assert r_ab.torrent_save_path == "/downloads/audiobooks"
    assert r_ab.prowlarr_indexer_ids == [99]

    # unconfigured library falls back cleanly to global defaults
    r_music = R.resolve_library_routing("music")
    assert r_music.torrent_client_id == "default"
    assert r_music.torrent_category == "soulsync"
    assert r_music.prowlarr_indexer_ids is None

    # completed_path mapping resolves client's remote path to SoulSync's local path
    local_4k = tmp_path / "local_4k"
    local_4k.mkdir()
    movie_file = local_4k / "Dune.2021.2160p.mkv"
    movie_file.write_bytes(b"video")
    resolved = R.resolve_completed_path_for_client(
        "/remote/4k/Dune.2021.2160p.mkv",
        source="torrent",
        client_id="deluge-4k",
        library_key="video_library:4k",
    )
    assert resolved == str(movie_file)


def test_download_clients_api_crud_and_grab_routing(monkeypatch):
    _patch_cfg(
        monkeypatch,
        {
            "torrent_client.type": "qbittorrent",
            "torrent_client.url": "http://qbit:8080",
            "torrent_client.category": "soulsync",
        },
    )
    app = Flask(__name__)
    app.secret_key = "test-dc"
    app.register_blueprint(download_clients_bp)
    c = app.test_client()

    # non-admin / unauthenticated requests are rejected
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: False)
    assert c.get("/api/download-clients/instances").status_code == 403

    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: True)

    # upsert a named client via REST API
    res = c.post(
        "/api/download-clients/instances",
        json={
            "kind": "torrent",
            "id": "tx-seedbox",
            "name": "Transmission Seedbox",
            "type": "transmission",
            "url": "http://seedbox:9091",
            "category": "seedbox",
        },
    )
    assert res.status_code == 200
    assert any(x["id"] == "tx-seedbox" for x in res.get_json()["torrent"])

    # save per-library routing via REST API
    put = c.put(
        "/api/download-clients/routing/movies",
        json={"torrent_client_id": "tx-seedbox", "prowlarr_indexer_ids": [3, 5]},
    )
    assert put.status_code == 200
    eff = put.get_json()["effective"]
    assert eff["torrent_client_id"] == "tx-seedbox"
    assert eff["torrent_category"] == "seedbox"
    assert eff["prowlarr_indexer_ids"] == [3, 5]

    # grab_torrent routes to the requested client_id and returns client_id
    used = []

    class FakeAdapter:
        def is_configured(self):
            return True

        async def add_torrent(self, url, category=None, save_path=None):
            used.append((url, category, save_path))
            return "abc123"

    monkeypatch.setattr(TC, "get_adapter_for_client", lambda cid=None: FakeAdapter() if cid == "tx-seedbox" else None)
    grabbed = client_grab.grab("torrent", "magnet:?xt=urn:btih:" + "a" * 40, category="seedbox", client_id="tx-seedbox")
    assert grabbed["ok"] is True
    assert grabbed["client_id"] == "tx-seedbox"
    assert used[0][1] == "seedbox"

    # delete named client via REST API (default cannot be deleted)
    assert c.delete("/api/download-clients/instances/torrent/default").status_code == 404
    assert c.delete("/api/download-clients/instances/torrent/tx-seedbox").status_code == 200
