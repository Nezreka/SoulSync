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


@pytest.mark.parametrize("protocol", ["", None, "carrier pigeon"])
def test_an_unsupported_protocol_is_refused(protocol):
    result = grab_release(_release(protocol=protocol))
    assert result["ok"] is False
    assert "protocol" in result["error"]


def test_a_soulseek_release_is_dispatched_to_the_peer():
    from unittest.mock import patch

    started = {"ok": True, "refs": ["a", "b"], "username": "peer",
               "folder": "Project Hail Mary", "error": ""}
    with patch("core.audiobook_soulseek.grab", return_value=started):
        result = grab_release(_release(protocol="soulseek"))

    assert result["ok"] is True
    assert result["client"] == "soulseek"
    assert result["files"] == 2
    # Every caller reads {ok, ref}. Returning anything else here meant a grab
    # reported success and registered no download at all.
    assert result["ref"] == "slsk:a"
    # The transfer ids ride in the one client_id column the row already has.
    from core.audiobook_soulseek import decode_refs
    assert decode_refs(result["client_ref"]) == {
        "username": "peer", "refs": ["a", "b"], "folder": "Project Hail Mary",
    }


def test_every_protocol_reports_its_handle_the_same_way():
    # A grab that answers in a different shape is a silent black hole: the
    # caller sees ok and stores nothing.
    from unittest.mock import patch

    from core.audiobook_grab import grab_release

    started = {"ok": True, "refs": ["a"], "username": "peer", "folder": "F", "error": ""}
    with patch("core.audiobook_soulseek.grab", return_value=started):
        assert grab_release(_release(protocol="soulseek"))["ref"]


def test_a_soulseek_folder_nobody_would_serve_is_refused():
    from unittest.mock import patch

    refused = {"ok": False, "error": "peer accepted none of the files.", "refs": []}
    with patch("core.audiobook_soulseek.grab", return_value=refused):
        result = grab_release(_release(protocol="soulseek"))
    assert result["ok"] is False and "none of the files" in result["error"]


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

def test_downloads_go_to_the_universal_download_folder(tmp_path):
    """No save path is passed, so the client uses its own — the shared one.

    This used to pass ``audiobooks.download_path``, which the settings page
    fills from the SAME input as the audiobook LIBRARY folder. Books therefore
    downloaded into the finished library and were then copied into a subfolder
    of it: every book on disk twice, half-finished ones sitting in the library.
    The video side has always passed nothing here; audiobooks now match.
    """
    with patch("core.settings.config_manager.get", return_value=str(tmp_path)):
        assert audiobook_download_path() is None


@pytest.mark.parametrize("protocol", ["torrent", "usenet"])
def test_a_grab_never_tells_the_client_where_to_put_a_book(protocol):
    # The end-to-end version of the above: whatever the config says, no
    # save_path reaches the download client, for either protocol.
    seen = {}

    async def _capture(*args, **kwargs):
        seen.update(kwargs)
        return "ref-1"

    adapter = _Adapter()
    with patch("core.torrent_clients.get_active_adapter", return_value=adapter), \
         patch("core.usenet_clients.get_active_adapter", return_value=adapter), \
         patch("core.torrent_clients.base.add_torrent_smart", new=_capture), \
         patch.object(_Adapter, "add_nzb", new=_capture), \
         patch("core.settings.config_manager.get", return_value="/some/library"), \
         patch("core.audiobook_grab.has_room", return_value=(True, None, 0.0)):
        result = grab_release(_release(protocol=protocol,
                                       download_url="http://x/a." + protocol))

    assert result["ok"] is True
    assert seen.get("save_path") is None


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


# ---------------------------------------------------------------------------
# Disk space
#
# Reuses the shared floor — the "Minimum free disk space (GB)" setting the music
# side refuses downloads on. Audiobooks are large enough to be the thing that
# fills the disk.
# ---------------------------------------------------------------------------

def test_a_grab_is_refused_when_the_disk_is_nearly_full():
    with patch("core.disk_guard.floor_gb", return_value=5.0), \
         patch("core.disk_guard.free_gb", return_value=1.2), \
         patch("core.settings.config_manager.get", return_value="/downloads"), \
         patch("core.audiobook_grab.grab_torrent") as torrent:
        result = grab_release(_release())
    assert result["ok"] is False
    assert "1.2 GB free" in result["error"]
    torrent.assert_not_called()


def test_a_grab_proceeds_when_there_is_room():
    with patch("core.disk_guard.floor_gb", return_value=5.0), \
         patch("core.disk_guard.free_gb", return_value=120.0), \
         patch("core.settings.config_manager.get", return_value="/downloads"), \
         patch("core.audiobook_grab.grab_torrent",
               return_value={"ok": True, "ref": "h"}) as torrent:
        result = grab_release(_release())
    assert result["ok"] is True
    torrent.assert_called_once()


def test_the_guard_is_off_when_the_floor_is_zero():
    with patch("core.disk_guard.floor_gb", return_value=0.0), \
         patch("core.disk_guard.free_gb", return_value=0.1), \
         patch("core.audiobook_grab.grab_torrent",
               return_value={"ok": True, "ref": "h"}) as torrent:
        assert grab_release(_release())["ok"] is True
    torrent.assert_called_once()


def test_an_unprobeable_disk_never_wedges_downloads():
    # The same call the music guard makes: unknown always passes.
    with patch("core.disk_guard.floor_gb", return_value=5.0), \
         patch("core.disk_guard.free_gb", return_value=None), \
         patch("core.settings.config_manager.get", return_value="/downloads"), \
         patch("core.audiobook_grab.grab_torrent",
               return_value={"ok": True, "ref": "h"}) as torrent:
        assert grab_release(_release())["ok"] is True
    torrent.assert_called_once()


def test_the_floor_comes_from_the_music_setting():
    # Not an audiobook-specific knob: it is literally the same disk.
    import inspect

    import core.audiobook_grab as module

    source = inspect.getsource(module)
    assert "core.disk_guard" in source
    assert "min_free_disk_gb" not in source


def test_the_disk_probe_is_the_music_one_not_a_copy_of_it():
    # Audiobooks download to the universal folder, so "is there room" is the
    # same question music asks. Measuring an audiobook-specific folder was
    # checking a volume nothing downloads to.
    from unittest.mock import patch as _patch

    from core.audiobook_grab import has_room

    with _patch("core.disk_guard.music_has_room", return_value=(False, 1.0, 5.0)) as probe:
        assert has_room() == (False, 1.0, 5.0)
    probe.assert_called_once()
