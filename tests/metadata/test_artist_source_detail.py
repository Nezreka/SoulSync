"""Tests for ``core.artist_source_detail.build_source_only_artist_detail``.

The function used to live inline inside ``web_server.py``; a prior version of
these tests AST-parsed the function body to assert on response keys because
``web_server.py`` couldn't be imported at test time. Now that the logic lives
in a side-effect-free core module with dependency-injected clients, the tests
just call it directly with mocks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import artist_source_detail
from core.artist_source_detail import build_source_only_artist_detail


# ---------------------------------------------------------------------------
# Fixtures — stubs for the metadata helpers the function calls
# ---------------------------------------------------------------------------

def _success_discography(**overrides):
    result = {
        "success": True,
        "albums": [{"id": "a1", "title": "Album One"}],
        "eps": [],
        "singles": [{"id": "s1", "title": "Single One"}],
    }
    result.update(overrides)
    return result


def _empty_discography():
    return {
        "success": False,
        "error": "No releases found for artist",
    }


@pytest.fixture
def _stub_metadata(monkeypatch):
    """Replace the metadata imports with controllable stubs.

    The function imports ``get_artist_image_url`` and
    ``get_artist_detail_discography`` from the concrete metadata modules, so we
    patch those modules directly.
    """
    from core.metadata import artist_image as metadata_artist_image
    from core.metadata import discography as metadata_discography

    state = {
        "image_url": None,
        "discography": _success_discography(),
        "last_options": None,
        "last_discog_call": None,
    }

    def fake_get_artist_image_url(artist_id, source_override=None):
        return state["image_url"]

    def fake_get_artist_detail_discography(artist_id, artist_name="", options=None):
        state["last_options"] = options
        state["last_discog_call"] = (artist_id, artist_name)
        return state["discography"]

    monkeypatch.setattr(metadata_artist_image, "get_artist_image_url", fake_get_artist_image_url)
    monkeypatch.setattr(metadata_discography, "get_artist_detail_discography", fake_get_artist_detail_discography)

    return state


# ---------------------------------------------------------------------------
# Group A — Success-path response shape + source-specific ID stamping
# ---------------------------------------------------------------------------

class TestResponseShape:
    def test_success_returns_expected_envelope(self, _stub_metadata):
        payload, status = build_source_only_artist_detail(
            "dz-123", "Artist One", "deezer",
        )
        assert status == 200
        assert payload["success"] is True
        assert payload["discography"] == _stub_metadata["discography"]
        assert payload["enrichment_coverage"] == {}
        assert payload["artist"]["id"] == "dz-123"
        assert payload["artist"]["name"] == "Artist One"
        assert payload["artist"]["server_source"] is None
        assert "genres" in payload["artist"]

    def test_empty_artist_name_falls_back_to_id(self, _stub_metadata):
        payload, status = build_source_only_artist_detail(
            "dz-123", "", "deezer",
        )
        assert status == 200
        assert payload["artist"]["name"] == "dz-123"

    def test_failure_returns_404(self, _stub_metadata):
        _stub_metadata["discography"] = _empty_discography()
        payload, status = build_source_only_artist_detail(
            "dz-missing", "Unknown Artist", "deezer",
        )
        assert status == 404
        assert payload["success"] is False
        assert payload["source"] == "deezer"
        assert "error" in payload

    @pytest.mark.parametrize("source,expected_field", [
        ("spotify", "spotify_artist_id"),
        ("itunes", "itunes_artist_id"),
        ("deezer", "deezer_id"),
        ("discogs", "discogs_id"),
        ("hydrabase", "soul_id"),
        ("musicbrainz", "musicbrainz_id"),
    ])
    def test_source_specific_id_field_is_stamped(self, _stub_metadata, source, expected_field):
        payload, _ = build_source_only_artist_detail("the-id", "Artist", source)
        assert payload["artist"][expected_field] == "the-id"


# ---------------------------------------------------------------------------
# Group B — Discography options contract (the bug that motivated the extract)
# ---------------------------------------------------------------------------

class TestDiscographyOptions:
    def test_dedup_variants_disabled(self, _stub_metadata):
        """Source-only view must show every release variant, matching the
        retired inline Artists page behaviour."""
        build_source_only_artist_detail("dz-1", "Artist", "deezer")
        opts = _stub_metadata["last_options"]
        assert opts is not None
        assert opts.dedup_variants is False

    def test_passes_source_override_and_artist_source_ids(self, _stub_metadata):
        build_source_only_artist_detail("sp-999", "Artist", "spotify")
        opts = _stub_metadata["last_options"]
        assert opts.source_override == "spotify"
        assert opts.artist_source_ids == {"spotify": "sp-999"}


# ---------------------------------------------------------------------------
# Group C — Per-source enrichment
# ---------------------------------------------------------------------------

class TestPerSourceEnrichment:
    def test_spotify_extracts_genres_followers_and_image_fallback(self, _stub_metadata):
        spotify = SimpleNamespace(
            get_artist=lambda aid, allow_fallback=False: {
                "genres": ["alt rock", "emo"],
                "followers": {"total": 12345},
                "images": [{"url": "https://sp/img.jpg"}],
            }
        )
        payload, _ = build_source_only_artist_detail(
            "sp-1", "Artist", "spotify", spotify_client=spotify,
        )
        assert payload["artist"]["genres"] == ["alt rock", "emo"]
        assert payload["artist"]["followers"] == 12345
        # image_url falls back to Spotify's image when metadata returned None
        assert payload["artist"]["image_url"] == "https://sp/img.jpg"

    def test_deezer_extracts_genres_and_followers(self, _stub_metadata):
        deezer = SimpleNamespace(
            get_artist_info=lambda aid: {
                "genres": ["pop"],
                "followers": {"total": 500},
            }
        )
        payload, _ = build_source_only_artist_detail(
            "dz-1", "Artist", "deezer", deezer_client=deezer,
        )
        assert payload["artist"]["genres"] == ["pop"]
        assert payload["artist"]["followers"] == 500

    def test_itunes_extracts_genres_only(self, _stub_metadata):
        itunes = SimpleNamespace(get_artist=lambda aid: {"genres": ["rock"]})
        payload, _ = build_source_only_artist_detail(
            "it-1", "Artist", "itunes", itunes_client=itunes,
        )
        assert payload["artist"]["genres"] == ["rock"]
        assert "followers" not in payload["artist"]

    def test_discogs_extracts_genres_only(self, _stub_metadata):
        discogs = SimpleNamespace(get_artist=lambda aid: {"genres": ["jazz"]})
        payload, _ = build_source_only_artist_detail(
            "dc-1", "Artist", "discogs", discogs_client=discogs,
        )
        assert payload["artist"]["genres"] == ["jazz"]

    def test_client_none_is_safe(self, _stub_metadata):
        """Missing client for the requested source is a no-op, not a crash."""
        payload, status = build_source_only_artist_detail(
            "dz-1", "Artist", "deezer", deezer_client=None,
        )
        assert status == 200
        assert payload["artist"]["genres"] == []

    def test_client_exception_does_not_propagate(self, _stub_metadata):
        """A failing source client should log and move on; the response still builds."""
        def _boom(_):
            raise RuntimeError("deezer down")

        deezer = SimpleNamespace(get_artist_info=_boom)
        payload, status = build_source_only_artist_detail(
            "dz-1", "Artist", "deezer", deezer_client=deezer,
        )
        assert status == 200
        assert payload["artist"]["genres"] == []


# ---------------------------------------------------------------------------
# Group D — Last.fm enrichment
# ---------------------------------------------------------------------------

class _FakeLastFM:
    def __init__(self, api_key=None):
        self.api_key = api_key

    def get_artist_info(self, name):
        return {
            "bio": {"content": "Long bio text", "summary": "Short summary"},
            "stats": {"listeners": "5000", "playcount": "99999"},
            "url": "https://last.fm/artist",
        }


class TestLastFmEnrichment:
    def _patch_lastfm(self, monkeypatch, cls=_FakeLastFM):
        import core.lastfm_client as lastfm_module
        monkeypatch.setattr(lastfm_module, "LastFMClient", cls)

    def test_lastfm_fields_populated_when_api_key_present(self, _stub_metadata, monkeypatch):
        self._patch_lastfm(monkeypatch)
        payload, _ = build_source_only_artist_detail(
            "dz-1", "Artist", "deezer", lastfm_api_key="LFM_KEY",
        )
        artist = payload["artist"]
        assert artist["lastfm_bio"] == "Long bio text"
        assert artist["lastfm_listeners"] == 5000
        assert artist["lastfm_playcount"] == 99999
        assert artist["lastfm_url"] == "https://last.fm/artist"

    def test_no_lastfm_when_api_key_missing(self, _stub_metadata, monkeypatch):
        self._patch_lastfm(monkeypatch)
        payload, _ = build_source_only_artist_detail(
            "dz-1", "Artist", "deezer", lastfm_api_key=None,
        )
        assert "lastfm_bio" not in payload["artist"]
        assert "lastfm_listeners" not in payload["artist"]

    def test_summary_used_when_bio_content_missing(self, _stub_metadata, monkeypatch):
        class _SummaryOnly(_FakeLastFM):
            def get_artist_info(self, name):
                return {
                    "bio": {"summary": "Just a summary"},
                    "stats": {},
                    "url": "",
                }
        self._patch_lastfm(monkeypatch, _SummaryOnly)
        payload, _ = build_source_only_artist_detail(
            "dz-1", "Artist", "deezer", lastfm_api_key="LFM_KEY",
        )
        assert payload["artist"]["lastfm_bio"] == "Just a summary"

    def test_lastfm_exception_does_not_propagate(self, _stub_metadata, monkeypatch):
        class _Broken(_FakeLastFM):
            def get_artist_info(self, name):
                raise RuntimeError("last.fm rate limited")
        self._patch_lastfm(monkeypatch, _Broken)
        payload, status = build_source_only_artist_detail(
            "dz-1", "Artist", "deezer", lastfm_api_key="LFM_KEY",
        )
        assert status == 200
        assert "lastfm_bio" not in payload["artist"]
