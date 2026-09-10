"""slskd 0.26 marks good downloads as failed (#1229).

An enqueue that answers with no id made ``download()`` return the FILENAME as
the download id. ``get_download_status`` then asked for
``transfers/downloads/<filename>``, slskd answered 400/405, the monitor read
that as a failure, and a perfectly valid transfer was cancelled seconds after
starting.

slskd groups its listing username -> directories -> files, and every file
carries its real id. That listing is the only place a filename can be turned
back into a transfer, so a filename-keyed id resolves through it.
"""

import asyncio

import pytest

from core.download_plugins.types import DownloadStatus
from core.soulseek_client import SoulseekClient


def _client():
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = "http://slskd.invalid"
    return client


def _status(ident, filename, username="peer", state="InProgress"):
    return DownloadStatus(id=ident, filename=filename, username=username,
                          state=state, progress=10.0, size=100, transferred=10, speed=1)


# ---------------------------------------------------------------------------
# Telling an id from a filename
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "7f3a2b10-4c5d-11ee-be56-0242ac120002",
    "abc123",
])
def test_a_transfer_id_is_recognised(value):
    assert SoulseekClient._looks_like_transfer_id(value) is True


@pytest.mark.parametrize("value", [
    "@@abc\\Music\\Artist\\track.flac",
    "Music/Artist/track.mp3",
    "track.flac",
    "",
])
def test_a_filename_is_not_mistaken_for_an_id(value):
    # A filename always carries a separator or an extension dot; a UUID has
    # neither. Getting this backwards is what put a filename in a URL path.
    assert SoulseekClient._looks_like_transfer_id(value) is False


# ---------------------------------------------------------------------------
# Resolving through the grouped listing
# ---------------------------------------------------------------------------

def test_a_filename_keyed_id_never_reaches_the_endpoint(monkeypatch):
    """The actual bug: slskd answers 400/405 for a filename in the path."""
    client = _client()
    asked = []

    async def _request(method, endpoint, **kwargs):
        asked.append(endpoint)
        return None

    async def _all():
        return [_status("uuid-1", "@@abc\\Music\\track.flac")]

    monkeypatch.setattr(client, "_make_request", _request)
    monkeypatch.setattr(client, "get_all_downloads", _all)

    found = asyncio.run(client.get_download_status("@@abc\\Music\\track.flac"))

    assert found is not None and found.id == "uuid-1"
    assert asked == [], f"a filename was sent as a path segment: {asked}"


def test_a_real_transfer_id_still_uses_the_direct_endpoint(monkeypatch):
    # Backwards compatible: nothing changes for slskd versions that DO return
    # an id, and a single cheap request stays a single cheap request.
    client = _client()
    asked = []

    async def _request(method, endpoint, **kwargs):
        asked.append(endpoint)
        return {"id": "uuid-1", "filename": "x.flac", "username": "peer",
                "state": "InProgress", "percentComplete": 50.0, "size": 100,
                "bytesTransferred": 50, "averageSpeed": 1}

    monkeypatch.setattr(client, "_make_request", _request)
    found = asyncio.run(client.get_download_status("uuid-1"))

    assert found.id == "uuid-1"
    assert asked == ["transfers/downloads/uuid-1"]


def test_the_match_is_on_the_leaf_name_not_the_whole_path(monkeypatch):
    # slskd reports the peer's full share path; the enqueue used the same
    # string, but a mismatch in separators must not lose the transfer.
    client = _client()

    async def _all():
        return [_status("uuid-1", "@@server/Music/Artist/track.flac")]

    monkeypatch.setattr(client, "get_all_downloads", _all)
    found = asyncio.run(client.get_download_status("@@abc\\Music\\Artist\\track.flac"))

    assert found is not None and found.id == "uuid-1"


def test_a_filename_that_is_not_in_the_listing_reports_nothing(monkeypatch):
    # None means "cannot say", which the monitor treats as keep-waiting rather
    # than as a failure.
    client = _client()

    async def _all():
        return []

    monkeypatch.setattr(client, "get_all_downloads", _all)
    assert asyncio.run(client.get_download_status("missing.flac")) is None


def test_the_right_peers_transfer_is_picked(monkeypatch):
    # Two peers can serve a file of the same name.
    client = _client()

    async def _all():
        return [_status("uuid-a", "Music/track.flac", username="alice"),
                _status("uuid-b", "Music/track.flac", username="bob")]

    monkeypatch.setattr(client, "get_all_downloads", _all)
    found = asyncio.run(
        client._status_from_listing(filename="Music/track.flac", username="bob"))

    assert found.id == "uuid-b"


def test_an_unreachable_slskd_reports_nothing_rather_than_failing(monkeypatch):
    client = _client()

    async def _all():
        return []

    monkeypatch.setattr(client, "get_all_downloads", _all)
    assert asyncio.run(client.get_download_status("track.flac")) is None


def test_no_base_url_reports_nothing():
    client = SoulseekClient.__new__(SoulseekClient)
    client.base_url = ""
    assert asyncio.run(client.get_download_status("anything")) is None
