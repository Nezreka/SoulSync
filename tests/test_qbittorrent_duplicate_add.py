"""qBittorrent already holds it → adopt the torrent, don't report a failure.

Found in Boulder's live install. ``/api/v2/torrents/add`` answers the bare string
``Fails.`` for several distinct situations, and one of them is "I already have this
torrent". The adapter read every ``Fails.`` as a refusal and returned None, which made
the video wishlist drain treat an available release as a failed search — one row reached
133 fruitless "attempts" against a torrent that was sitting in the client at ``metaDL``
0% the entire time (hash ``d880c485…``, Project.Pay.Day.2021.1080p.WEB-DL).

The caller's intent — "this release should be in the client, give me its handle" — is
already satisfied when the client has it. So when the magnet names a hash we can see in
the pre-add snapshot, the existing hash is returned instead of a failure.

This adapter is SHARED with the music side, so the tests below also pin what must NOT
change: a genuine refusal still fails, and a hash we cannot see is never invented.
"""

from __future__ import annotations

from core.torrent_clients.qbittorrent import QBittorrentAdapter, _magnet_hash

_HASH = "d880c485f7655d4122619a9a8fe6a82f0044fe1e"
_MAGNET = "magnet:?xt=urn:btih:" + _HASH.upper() + "&dn=Project.Pay.Day.2021.1080p.WEB-DL"


# ── reading the hash out of a magnet ─────────────────────────────────────────

def test_a_v1_magnet_hash_is_read_and_lowercased():
    """qBittorrent reports hashes lowercase; the magnet may not be."""
    assert _magnet_hash(_MAGNET) == _HASH


def test_a_torrent_url_names_no_hash():
    assert _magnet_hash("https://indexer/download?id=42") is None


def test_junk_never_raises():
    for bad in (None, "", 12345, "magnet:?xt=urn:btih:tooshort", "magnet:?dn=no-hash"):
        assert _magnet_hash(bad) is None


def test_a_base32_magnet_is_not_guessed_at():
    """Only v1 40-hex hashes are recognised. Half-reading a base32 magnet and
    comparing it against qBittorrent's hex would silently never match — better
    to return None and let the normal add path run."""
    assert _magnet_hash("magnet:?xt=urn:btih:MFRGGZDFMZTWQ2LKNNWG23TPOA") is None


# ── the adoption path ────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text="Ok.", ok=True, status=200, payload=None):
        self.text, self.ok, self.status_code = text, ok, status
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


def _adapter(existing, add_body):
    """An adapter whose HTTP layer is replaced: /torrents/info reports the
    torrents already present, /torrents/add answers `add_body`."""
    a = QBittorrentAdapter()
    calls = []

    def _call(method, path, **kw):
        calls.append((method, path))
        if path.endswith("/torrents/info"):
            return _Resp(payload=[{"hash": h} for h in existing])
        if path.endswith("/torrents/add"):
            return _Resp(text=add_body)
        return _Resp()

    a._call = _call
    a._calls = calls
    return a


def test_a_duplicate_add_returns_the_hash_the_client_already_has():
    a = _adapter([_HASH], "Fails.")
    assert a._add_torrent_sync(_MAGNET, "soulsync", None) == _HASH


def test_a_genuine_refusal_still_fails():
    """The behaviour that must NOT be softened: 'Fails.' for a torrent the
    client does NOT have is a real refusal, and pretending otherwise would
    write a download row for something that will never arrive."""
    a = _adapter(["some-other-hash"], "Fails.")
    assert a._add_torrent_sync(_MAGNET, "soulsync", None) is None


def test_a_torrent_url_duplicate_cannot_be_adopted():
    """A .torrent URL names no hash, so there is nothing to compare — it must
    fail rather than adopt an unrelated torrent."""
    a = _adapter([_HASH], "Fails.")
    assert a._add_torrent_sync("https://indexer/dl.torrent", "soulsync", None) is None


def test_an_ok_response_that_adds_nothing_also_adopts_a_known_hash():
    """Some builds answer 'Ok.' to a duplicate and simply never create a row —
    the poll then finds no new hash. Same intent, same answer."""
    a = _adapter([_HASH], "Ok.")
    a._poll_for_new_hash = lambda before: None
    assert a._add_torrent_sync(_MAGNET, "soulsync", None) == _HASH


def test_a_normal_torrent_url_add_is_untouched():
    """URL/file adds still need qBittorrent's discovered hash."""
    a = _adapter([], "Ok.")
    a._poll_for_new_hash = lambda before: "brandnewhash"
    assert a._add_torrent_sync("https://indexer/dl.torrent", "soulsync", None) == "brandnewhash"


def test_a_magnet_returns_its_own_hash_even_if_another_torrent_appears():
    """qBittorrent gives /add no id, so the adapter used to diff the global
    torrent list and could adopt an unrelated concurrent add. A v1 magnet
    already names the id SoulSync must poll; use that instead of guessing."""
    a = _adapter([], "Ok.")
    a._poll_for_new_hash = lambda before: "some-other-new-hash"
    assert a._add_torrent_sync(_MAGNET, "soulsync", None) == _HASH


def test_a_failed_info_lookup_still_aborts_early():
    """Without a reliable before-snapshot the whole diff (and the adoption
    check) is meaningless, so the add must not proceed."""
    a = QBittorrentAdapter()
    a._call = lambda method, path, **kw: None
    assert a._add_torrent_sync(_MAGNET, "soulsync", None) is None
