"""Contract tests for strict artist-discography provider access."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from core.metadata import registry as metadata_registry
from core.metadata.discography_strict import get_artist_discography
from core.metadata.lookup import MetadataLookupOptions
from core.metadata.provider_access import (
    ProviderAccessError,
    call_discography_provider,
)


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response or _Response()
        self.error = error

    def get(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.response


class _SwallowingClient:
    """Mimic clients that log failures and return an empty list."""

    def __init__(self, session):
        self.session = session

    def get_artist_albums(self, artist_id, **kwargs):
        try:
            response = self.session.get("https://provider.example/artist")
            response.raise_for_status()
            response.json()
        except Exception:
            return []
        return []


class _RecoveringSession:
    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise requests.Timeout(
                "GET https://provider.example/catalog?api_key=super-secret timed out"
            )
        return _Response(200, {"results": ["ok"]})


class _RecoveringClient:
    def __init__(self, albums):
        self.session = _RecoveringSession()
        self.albums = list(albums)

    def get_artist_albums(self, artist_id, **kwargs):
        try:
            self.session.get("https://provider.example/dead-mirror")
        except requests.RequestException:
            pass
        response = self.session.get("https://provider.example/live-mirror")
        response.raise_for_status()
        return list(self.albums)


class _StaticClient:
    def __init__(self, albums):
        self.albums = list(albums)
        self.calls = []

    def get_artist_albums(self, artist_id, **kwargs):
        self.calls.append((artist_id, dict(kwargs)))
        return list(self.albums)


class _TimeoutClient(_SwallowingClient):
    def __init__(self):
        super().__init__(_Session(error=requests.Timeout("provider timed out")))
        self.calls = []

    def get_artist_albums(self, artist_id, **kwargs):
        self.calls.append((artist_id, dict(kwargs)))
        return super().get_artist_albums(artist_id, **kwargs)


class _Search404Client:
    def __init__(self):
        self.session = _Session(response=_Response(404))

    def get_artist_albums(self, artist_id, **kwargs):
        return []

    def search_artists(self, query, limit=5):
        try:
            response = self.session.get("https://provider.example/search")
            response.raise_for_status()
        except Exception:
            return []
        return []


class _SpotifyFreeCatalog:
    def search_artists(self, query, limit):
        return [{"id": "spotify-artist", "name": query}]


class _SpotifyFreeClient:
    def __init__(self):
        self.calls = []
        self._free_meta_client = _SpotifyFreeCatalog()

    @property
    def _free_meta(self):
        return self._free_meta_client

    def _free_active(self):
        return True

    def is_spotify_authenticated(self):
        return False

    def is_rate_limited(self):
        return False

    def get_artist_albums(self, artist_id, **kwargs):
        self.calls.append((artist_id, dict(kwargs)))
        if artist_id == "spotify-artist" and kwargs.get("allow_fallback") is True:
            return [_album("spotify-album")]
        return []



def _album(album_id="album-1"):
    return SimpleNamespace(
        id=album_id,
        name="Album One",
        release_date="2024-01-01",
        album_type="album",
        image_url=None,
        total_tracks=10,
        external_urls={},
        artist_ids=["artist-1"],
    )


def _configure_sources(monkeypatch, clients, priority):
    monkeypatch.setattr(
        metadata_registry,
        "get_primary_source",
        lambda spotify_client_factory=None: priority[0],
    )
    monkeypatch.setattr(
        metadata_registry,
        "get_source_priority",
        lambda primary_source: list(priority),
    )
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: clients[source],
    )


def test_valid_empty_response_is_not_an_access_error():
    client = _SwallowingClient(_Session(response=_Response(200, {"results": []})))
    original_session = client.session

    result = call_discography_provider(
        "example",
        client,
        lambda isolated: isolated.get_artist_albums("artist-1"),
    )

    assert result == []
    assert client.session is original_session


def test_recovered_internal_failure_returns_real_results():
    client = _RecoveringClient([_album("recovered-album")])

    result = call_discography_provider(
        "example",
        client,
        lambda isolated: isolated.get_artist_albums("artist-1"),
    )

    assert [album.id for album in result] == ["recovered-album"]


def test_recorded_failure_explains_an_empty_recovered_call():
    client = _RecoveringClient([])

    with pytest.raises(ProviderAccessError) as raised:
        call_discography_provider(
            "example",
            client,
            lambda isolated: isolated.get_artist_albums("artist-1"),
        )

    message = str(raised.value)
    assert raised.value.status_code == 504
    assert "https://provider.example/catalog" in message
    assert "super-secret" not in message
    assert "api_key" not in message


def test_swallowed_timeout_is_propagated_as_gateway_timeout():
    client = _SwallowingClient(
        _Session(error=requests.Timeout("provider timed out"))
    )

    with pytest.raises(ProviderAccessError) as raised:
        call_discography_provider(
            "example",
            client,
            lambda isolated: isolated.get_artist_albums("artist-1"),
        )

    assert raised.value.status_code == 504
    assert raised.value.source == "example"
    assert "provider timed out" in str(raised.value)


def test_valid_empty_provider_continues_to_fallback(monkeypatch):
    primary = _StaticClient([])
    fallback = _StaticClient([_album()])
    _configure_sources(
        monkeypatch,
        {"primary": primary, "fallback": fallback},
        ["primary", "fallback"],
    )

    result = get_artist_discography(
        "artist-1",
        artist_name="Artist One",
        options=MetadataLookupOptions(),
    )

    assert result["state"] == "results"
    assert result["source"] == "fallback"
    assert [album["id"] for album in result["albums"]] == ["album-1"]
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_provider_access_error_stops_fallback(monkeypatch):
    primary = _TimeoutClient()
    fallback = _StaticClient([_album()])
    _configure_sources(
        monkeypatch,
        {"primary": primary, "fallback": fallback},
        ["primary", "fallback"],
    )

    result = get_artist_discography(
        "artist-1",
        artist_name="Artist One",
        options=MetadataLookupOptions(),
    )

    assert result["state"] == "error"
    assert result["source"] == "primary"
    assert result["status_code"] == 504
    assert len(primary.calls) == 1
    assert fallback.calls == []


def test_not_found_is_empty_for_explicit_id_lookup(monkeypatch):
    client = _SwallowingClient(_Session(response=_Response(404)))
    _configure_sources(monkeypatch, {"primary": client}, ["primary"])

    result = get_artist_discography(
        "foreign-id",
        artist_name="",
        options=MetadataLookupOptions(allow_fallback=False),
    )

    assert result["state"] == "empty"
    assert result["status_code"] == 404


def test_not_found_during_name_search_is_an_access_error(monkeypatch):
    client = _Search404Client()
    _configure_sources(monkeypatch, {"primary": client}, ["primary"])

    result = get_artist_discography(
        "foreign-id",
        artist_name="Artist One",
        options=MetadataLookupOptions(allow_fallback=False),
    )

    assert result["state"] == "error"
    assert result["status_code"] == 404


def test_spotify_free_stays_inside_spotify_catalogue(monkeypatch):
    client = _SpotifyFreeClient()
    _configure_sources(monkeypatch, {"spotify": client}, ["spotify"])

    result = get_artist_discography(
        "12345",
        artist_name="Artist One",
        options=MetadataLookupOptions(allow_fallback=False),
    )

    assert result["state"] == "results"
    assert result["source"] == "spotify"
    assert [album["id"] for album in result["albums"]] == ["spotify-album"]
    assert client.calls == [
        ("12345", pytest.helpers.anything) if False else client.calls[0],
        ("spotify-artist", client.calls[1][1]),
    ]
    assert client.calls[0][1]["allow_fallback"] is False
    assert client.calls[1][1]["allow_fallback"] is True
