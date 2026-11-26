"""shared bits for the server activity tests: recorded plex / jellyfin payloads,
a fake plex server that answers raw xml, and a hermetic environment (temp play
log, no config, no network, no threads)."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import pytest

FIX = Path(__file__).resolve().parent / "fixtures" / "server_activity"


def plex_root() -> ET.Element:
    return ET.parse(FIX / "plex_sessions.xml").getroot()


def plex_session(rating_key: str) -> ET.Element:
    return next(el for el in plex_root() if el.get("ratingKey") == rating_key)


def plex_meta() -> Dict[str, ET.Element]:
    root = ET.parse(FIX / "plex_metadata.xml").getroot()
    return {mc[0].get("ratingKey"): mc[0] for mc in root}


def jf_sessions() -> List[Dict[str, Any]]:
    return json.loads((FIX / "jellyfin_sessions.json").read_text(encoding="utf-8"))


def jf_session(name: str) -> Dict[str, Any]:
    return copy.deepcopy(next(s for s in jf_sessions() if (s.get("NowPlayingItem") or {}).get("Name") == name))


def book_tracks(n: int = 32, current: int = 14, each_ms: int = 1_890_000) -> List[Dict[str, Any]]:
    return [{"key": str(800 + i) if i != current else "814", "title": f"Chapter {i}", "duration_ms": each_ms,
             "disc": 1, "index": i} for i in range(1, n + 1)]


class FakePlex:
    friendlyName = "Basement"
    version = "1.41.2"
    machineIdentifier = "machine-1"

    def __init__(self, root: Optional[ET.Element] = None, plex_pass: Optional[bool] = True,
                 resources: Optional[ET.Element] = None, fail: bool = False):
        self.root = root if root is not None else plex_root()
        self.meta = plex_meta()
        self.myPlexSubscription = plex_pass
        self.resources = resources
        self.fail = fail
        self.calls: List[tuple] = []
        self.history: List[ET.Element] = []
        self.accounts: List[Dict[str, str]] = []

    def query(self, path, params=None, **kw):
        self.calls.append((path, dict(params or {})))
        if self.fail:
            raise ConnectionError("plex down")
        if path == "/status/sessions":
            return self.root
        m = re.match(r"^/library/metadata/(\d+)$", path)
        if m:
            mc = ET.Element("MediaContainer")
            if m.group(1) in self.meta:
                mc.append(self.meta[m.group(1)])
            return mc
        m = re.match(r"^/library/metadata/(\d+)/children$", path)
        if m:
            mc = ET.Element("MediaContainer")
            for t in book_tracks():
                ET.SubElement(mc, "Track", ratingKey=t["key"], title=t["title"], duration=str(t["duration_ms"]),
                              index=str(t["index"]), parentIndex="1")
            return mc
        if path == "/statistics/resources":
            if self.resources is None:
                raise PermissionError("(401) unauthorized")
            return self.resources
        if path == "/status/sessions/terminate":
            return None
        if path == "/accounts":
            mc = ET.Element("MediaContainer")
            for a in self.accounts:
                ET.SubElement(mc, "Account", **a)
            return mc
        if path in ("/devices", "/library/sections"):
            return ET.Element("MediaContainer")
        if path == "/status/sessions/history/all":
            start = int((params or {}).get("X-Plex-Container-Start", 0))
            size = int((params or {}).get("X-Plex-Container-Size", 500))
            mc = ET.Element("MediaContainer")
            for el in self.history[start:start + size]:
                mc.append(el)
            return mc
        raise AssertionError(f"unexpected plex call {path}")


class FakeResponse:
    def __init__(self, status=200, body=None, ctype="application/json", content=None):
        self.status_code = status
        self._body = body
        self.headers = {"Content-Type": ctype}
        self.content = content if content is not None else (json.dumps(body).encode() if body is not None else b"")
        self.text = self.content.decode("utf-8", "replace") if isinstance(self.content, bytes) else str(self.content)

    def json(self):
        return self._body


@pytest.fixture()
def sa_env(monkeypatch, tmp_path):
    """a hermetic server activity: temp play log, no plex, no jellyfin, no
    config, no network, no threads. returns a namespace to configure."""
    import core.server_activity as sa
    from core.server_activity import geo, links, live, store
    from core.server_activity import plex as px
    from core.server_activity import settings as st
    from core.server_activity import stats

    db = tmp_path / "plays.db"

    def factory():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c

    store.use_connection_factory(factory)
    from core.server_activity import images
    images.use_cache_dir(str(tmp_path / "sa_images"))
    live.reset_for_tests()
    px.reset_caches()
    links.reset()
    geo.reset()
    stats.reset_for_tests()
    env = SimpleNamespace(plex=None, jf_cfg={"base_url": "", "api_key": ""},
                          plex_cfg={"base_url": "", "token": ""},
                          settings=dict(st.DEFAULTS, stop_presets=list(st.DEFAULT_STOP_PRESETS)),
                          geo_scheduled=[], db=db, factory=factory, requests=[], spawned=[])
    monkeypatch.setattr(sa, "_plex_config", lambda db=None: env.plex_cfg)
    monkeypatch.setattr(sa, "_jellyfin_config", lambda db=None: env.jf_cfg)
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: env.plex)
    monkeypatch.setattr(sa, "_mark_plex_unreachable", lambda srv: None)
    monkeypatch.setattr(st, "get_settings", lambda: env.settings)
    monkeypatch.setattr(geo, "_schedule", lambda ip: env.geo_scheduled.append(ip))
    monkeypatch.setattr(links, "_root_names", lambda: {"audiobook": "audiobooks", "podcast": "podcasts"})
    monkeypatch.setattr(links, "_book_index", lambda: {})
    monkeypatch.setattr(live, "_maybe_maintain", lambda now: None)
    # soulsync's own player: no live screens, no real player.db or profiles
    from core.player import presence
    from core.server_activity import soulsync
    presence._sessions.clear()
    presence._stops.clear()
    presence._notes.clear()
    soulsync.reset()
    monkeypatch.setattr(soulsync, "_server_name", lambda: "SoulSync")
    monkeypatch.setattr(soulsync, "_profiles", lambda: {})
    monkeypatch.setattr(soulsync, "_facts", lambda *a, **k: {})
    # background work is collected, not started: a test runs it when it wants
    monkeypatch.setattr(live, "_spawn", lambda fn: env.spawned.append(fn))
    monkeypatch.setattr(stats, "_spawn", lambda fn: env.spawned.append(fn))

    class _NoVideo:
        def items_by_server_ids(self, ids, server_source=None):
            return []

        def find_library_ref_by_title(self, kind, title, year=None):
            return None
    monkeypatch.setattr("api.video.get_video_db", lambda: _NoVideo())

    import requests

    def _no_net(*a, **k):
        raise AssertionError(f"unexpected network call {a}")
    monkeypatch.setattr(requests, "get", _no_net)
    monkeypatch.setattr(requests, "post", _no_net)

    def use_plex(plex=None):
        env.plex = plex or FakePlex()
        env.plex_cfg = {"base_url": "http://plex.test:32400", "token": "secret-plex-token"}
        return env.plex

    def use_jellyfin(sessions=None, extra=None):
        env.jf_cfg = {"base_url": "http://jf.test:8096", "api_key": "secret-jf-key"}
        data = jf_sessions() if sessions is None else sessions

        def fake_get(url, params=None, headers=None, timeout=None, **kw):
            env.requests.append(("GET", url, params))
            if url.endswith("/Sessions"):
                return FakeResponse(200, copy.deepcopy(data))
            if extra:
                return extra("GET", url, params)
            raise AssertionError(f"unexpected jellyfin GET {url}")

        def fake_post(url, json=None, headers=None, timeout=None, **kw):
            env.requests.append(("POST", url, json))
            if extra:
                return extra("POST", url, json)
            return FakeResponse(204, None)
        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr(requests, "post", fake_post)

    env.use_plex = use_plex
    env.use_jellyfin = use_jellyfin
    yield env
    presence._sessions.clear()
    presence._stops.clear()
    presence._notes.clear()
    store.use_connection_factory(None)
    images.use_cache_dir(None)
    live.reset_for_tests()
