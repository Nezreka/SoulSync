"""Tests for core/audiobook_grab.py.

Hermetic: the torrent and usenet adapters are always stubbed, so nothing here
reaches a real download client.

The point of this module is that audiobooks use the SHARED download clients
without touching anything the music side owns, so most of these are guards on
that boundary rather than on the happy path.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_grab import (
    audiobook_download_path,
    grab_release,
    grab_torrent,
    grab_usenet,
)


class _Adapter:
    def __init__(self, configured=True, ref="abc123", raises=None):
        self._configured = configured
        self.ref = ref
        self.raises = raises
        self.calls = []

    def is_configured(self):
        return self._configured

    async def add_nzb(self, url, category=None, save_path=None):
        self.calls.append({"url": url, "category": category, "save_path": save_path})
        if self.raises:
            raise self.raises
        return self.ref


def _release(**overrides):
    payload = {
        "protocol": "torrent",
        "download_url": "https://example.invalid/a.torrent",
        "magnet_uri": "magnet:?xt=urn:btih:abc",
        "title": "Project Hail Mary M4B",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Torrent
# ---------------------------------------------------------------------------

def test_torrent_grab_returns_the_tracking_ref():
    adapter = _Adapter()
    with patch("core.torrent_clients.get_active_adapter", return_value=adapter), \
         patch("core.torrent_clients.base.add_torrent_smart",
               new=_async_return("hash-1")) as _:
        result = grab_torrent("magnet:?xt=urn:btih:abc")
    assert result == {"ok": True, "ref": "hash-1"}


def test_torrent_grab_without_a_client_is_a_readable_error():
    with patch("core.torrent_clients.get_active_adapter", return_value=None):
        result = grab_torrent("magnet:?xt=urn:btih:abc")
    assert result["ok"] is False
    assert "Settings" in result["error"]


def test_torrent_grab_with_an_unconfigured_client():
    with patch("core.torrent_clients.get_active_adapter", return_value=_Adapter(configured=False)):
        assert grab_torrent("magnet:?x")["ok"] is False


def test_a_refusing_client_is_reported_not_raised():
    with patch("core.torrent_clients.get_active_adapter", return_value=_Adapter()), \
         patch("core.torrent_clients.base.add_torrent_smart", new=_async_return(None)):
        result = grab_torrent("magnet:?x")
    assert result["ok"] is False
    assert "didn't accept" in result["error"]


def test_a_throwing_client_is_reported_not_raised():
    with patch("core.torrent_clients.get_active_adapter", return_value=_Adapter()), \
         patch("core.torrent_clients.base.add_torrent_smart",
               new=_async_raise(RuntimeError("client on fire"))):
        result = grab_torrent("magnet:?x")
    assert result["ok"] is False
    assert "client on fire" in result["error"]


# ---------------------------------------------------------------------------
# Usenet
# ---------------------------------------------------------------------------

def test_usenet_grab_returns_the_tracking_ref():
    adapter = _Adapter(ref="nzo-9")
    with patch("core.usenet_clients.get_active_adapter", return_value=adapter):
        result = grab_usenet("https://example.invalid/a.nzb")
    assert result == {"ok": True, "ref": "nzo-9"}


def test_usenet_grab_without_a_client():
    with patch("core.usenet_clients.get_active_adapter", return_value=None):
        assert grab_usenet("https://example.invalid/a.nzb")["ok"] is False


def test_usenet_errors_are_reported():
    adapter = _Adapter(raises=RuntimeError("sab is down"))
    with patch("core.usenet_clients.get_active_adapter", return_value=adapter):
        result = grab_usenet("https://example.invalid/a.nzb")
    assert result["ok"] is False
    assert "sab is down" in result["error"]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def test_a_torrent_release_goes_to_the_torrent_client():
    with patch("core.audiobook_grab.grab_torrent", return_value={"ok": True, "ref": "h"}) as torrent, \
         patch("core.audiobook_grab.grab_usenet") as usenet:
        grab_release(_release(protocol="torrent"))
    torrent.assert_called_once()
    usenet.assert_not_called()


def test_a_usenet_release_goes_to_the_usenet_client():
    with patch("core.audiobook_grab.grab_usenet", return_value={"ok": True, "ref": "n"}) as usenet, \
         patch("core.audiobook_grab.grab_torrent") as torrent:
        grab_release(_release(protocol="usenet", magnet_uri=None))
    usenet.assert_called_once()
    torrent.assert_not_called()


def test_a_torrent_prefers_the_file_url_and_keeps_the_magnet_as_fallback():
    # The .torrent can be fetched server-side, but a URL this process cannot
    # reach would be a dead end where the magnet still works.
    with patch("core.audiobook_grab.grab_torrent", return_value={"ok": True, "ref": "h"}) as torrent:
        grab_release(_release())
    args, kwargs = torrent.call_args
    assert args[0] == "https://example.invalid/a.torrent"
    assert kwargs["fallback_magnet"] == "magnet:?xt=urn:btih:abc"


def test_a_magnet_only_torrent_still_grabs():
    with patch("core.audiobook_grab.grab_torrent", return_value={"ok": True, "ref": "h"}) as torrent:
        grab_release(_release(download_url=None))
    assert torrent.call_args[0][0].startswith("magnet:")


def test_a_release_with_no_links_is_refused():
    result = grab_release(_release(download_url=None, magnet_uri=None))
    assert result["ok"] is False


def test_a_usenet_release_with_no_nzb_is_refused():
    result = grab_release(_release(protocol="usenet", download_url=None))
    assert result["ok"] is False


@pytest.mark.parametrize("protocol", ["", None, "soulseek", "carrier pigeon"])
def test_an_unsupported_protocol_is_refused(protocol):
    result = grab_release(_release(protocol=protocol))
    assert result["ok"] is False
    assert "protocol" in result["error"]


def test_dispatch_accepts_the_dataclass_form():
    from core.audiobook_release_search import AudiobookRelease

    release = AudiobookRelease(
        source="prowlarr", protocol="usenet", title="Book", indexer="X",
        size_bytes=1, download_url="https://example.invalid/a.nzb",
    )
    with patch("core.audiobook_grab.grab_usenet", return_value={"ok": True, "ref": "n"}) as usenet:
        grab_release(release)
    usenet.assert_called_once()


# ---------------------------------------------------------------------------
# Isolation from the music side
# ---------------------------------------------------------------------------

def test_downloads_land_outside_the_music_tree(tmp_path):
    # A part-downloaded book under a music root is a folder of loose chapter
    # files, which is exactly what the library scanner imports as an album.
    target = tmp_path / "audiobooks"
    with patch("core.settings.config_manager.get", return_value=str(target)):
        assert audiobook_download_path() == str(target)
    assert target.exists()


def test_an_unset_download_path_lets_the_client_decide():
    with patch("core.settings.config_manager.get", return_value=""):
        assert audiobook_download_path() is None


def test_grabs_use_their_own_downloader_category():
    # Its own category so a finished book is never mistaken for a music release
    # by anything watching the music category.
    adapter = _Adapter()
    with patch("core.usenet_clients.get_active_adapter", return_value=adapter), \
         patch("core.settings.config_manager.get", return_value=""):
        grab_usenet("https://example.invalid/a.nzb")
    assert adapter.calls[0]["category"] == "audiobooks"


def test_the_module_never_touches_music_download_state():
    import ast
    import inspect

    import core.audiobook_grab as module

    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for name in imported:
        for forbidden in ("core.downloads", "core.runtime_state", "core.wishlist",
                          "core.download_engine", "database", "core.video"):
            assert not name.startswith(forbidden), f"audiobook_grab imports {name}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


def _async_raise(exc):
    async def _inner(*args, **kwargs):
        raise exc
    return _inner
