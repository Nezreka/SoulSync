"""One copy of Spotify's web-player query hashes for the whole process.

spotapi (pulled in by SpotipyFree, the no-credentials Spotify source) needs
the persisted-query hashes from the web player's JavaScript before any
GraphQL call, and fetches them per client object: the JS pack plus ~75
chunks. SpotipyFree builds a new spotapi object for every call (album,
artist, track, playlist, search), so every call repeated that fetch, about
6-7 s. And spotapi joins the chunks with ``raw_hashes += chunk``: the text
holds non-ASCII characters, so CPython stores it at 4 bytes a character
(~27 MB), and 75 concatenations copy it over and over. The process's RSS
grew by ~1.4 GB per fetch and the allocator kept it (measured: 67 -> 1523 MB
for one ``Artist().get_artist``; 66 -> 169 MB with the chunks joined once).

``install()`` replaces ``BaseClient.get_sha256_hash`` with the same fetch,
joined once and shared by every client for ``TTL_SECONDS`` (Spotify ships a
new web player now and then; spotapi fetched afresh per object, so an hour
keeps the hashes about as current), and memoizes ``part_hash``, which split
the whole text for every query. It only patches a spotapi it recognizes and
leaves any other version alone.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("spotapi_hashes")

TTL_SECONDS = 3600
_WEB_PLAYER = "https://open.spotifycdn.com/cdn/build/web-player/{}"

_lock = threading.Lock()
# (monotonic time fetched, the hash text, part name -> hash)
_shared: Optional[Tuple[float, str, Dict[str, str]]] = None
_installed = False


def _fetch(base, module) -> str:
    """spotapi's own fetch, with the chunks joined once."""
    if base.js_pack is module._Undefined:
        base.get_session()
    if base.js_pack is module._Undefined:
        raise ValueError("Could not get playlist hashes")
    resp = base.client.get(str(base.js_pack))
    if resp.fail:
        raise module.BaseClientError("Could not get general hashes", error=resp.error.string)
    parts = [resp.response]
    str_mapping, hash_mapping = module.extract_mappings(str(resp.response))
    for name in module.combine_chunks(hash_mapping, str_mapping):
        resp = base.client.get(_WEB_PLAYER.format(name))
        if resp.fail:
            raise module.BaseClientError("Could not get general hashes", error=resp.error.string)
        parts.append(resp.response)
    return ''.join(str(p) for p in parts)


def _current(base, module) -> Tuple[str, Dict[str, str]]:
    """The shared hashes, fetched by one thread when missing or stale."""
    global _shared
    with _lock:
        if _shared is None or time.monotonic() - _shared[0] >= TTL_SECONDS:
            _shared = (time.monotonic(), _fetch(base, module), {})
        return _shared[1], _shared[2]


def install() -> bool:
    """Patch spotapi once. Returns whether the patch is in place."""
    global _installed
    if _installed:
        return True
    try:
        from spotapi import client as module
    except Exception:  # noqa: BLE001 - no spotapi, nothing to patch
        return False
    base_cls = getattr(module, 'BaseClient', None)
    known = all(hasattr(module, name) for name in
                ('extract_mappings', 'combine_chunks', '_Undefined', 'BaseClientError'))
    if base_cls is None or not known or not hasattr(base_cls, 'get_sha256_hash') \
            or not hasattr(base_cls, 'part_hash'):
        logger.info("spotapi version not recognized; its hash fetch is left as it is")
        return False

    def get_sha256_hash(self) -> None:
        self.raw_hashes, _parts = _current(self, module)

    def part_hash(self, name: str) -> str:
        text, parts = _current(self, module)
        self.raw_hashes = text
        if name not in parts:
            try:
                parts[name] = text.split(f'"{name}","query","')[1].split('"')[0]
            except IndexError:
                parts[name] = text.split(f'"{name}","mutation","')[1].split('"')[0]
        return parts[name]

    base_cls.get_sha256_hash = get_sha256_hash
    base_cls.part_hash = part_hash
    _installed = True
    return True


def reset_for_tests() -> None:
    global _shared
    with _lock:
        _shared = None
