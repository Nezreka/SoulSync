"""MusicBrainz enrichment must not depend on the worker handle.

`_search_service` reached MusicBrainz through `mb_worker.mb_service`, so with
no worker object it raised "MusicBrainz worker not initialized". Two callers
then hid that completely: `enrich_native_entity_all_services` swallows a
per-provider exception at debug level, and `_get_enrichment_status` omits a
service whose worker is None — which drops MusicBrainz from the configured set
the enrich walk iterates, so it is never even attempted. "Enrich all" came back
reporting success with the MusicBrainz chip still pending.

MusicBrainz needs no credentials and no worker — only a client, and the
process already keeps a shared, rate-limited one (`get_musicbrainz_service`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import core.library.service_search as service_search


def _stub_service():
    svc = MagicMock()
    svc.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100,
         "disambiguation": "composer"},
    ]
    return svc


def test_artist_search_works_with_no_worker(monkeypatch):
    monkeypatch.setattr(service_search, "mb_worker", None)
    monkeypatch.setattr("core.musicbrainz_service.get_musicbrainz_service",
                        _stub_service)

    hits = service_search._search_service("musicbrainz", "artist", "Sawano Hiroyuki")

    assert [h["id"] for h in hits] == ["mbid-sawano"]


def test_a_present_worker_is_still_preferred(monkeypatch):
    """The worker owns the rate limiter it shares with its own loop — when it
    is there, keep using it rather than introducing a second client."""
    worker = MagicMock()
    worker.mb_service.mb_client.search_artist.return_value = [
        {"id": "from-worker", "name": "Sawano Hiroyuki", "score": 100},
    ]
    monkeypatch.setattr(service_search, "mb_worker", worker)
    monkeypatch.setattr("core.musicbrainz_service.get_musicbrainz_service",
                        _stub_service)

    hits = service_search._search_service("musicbrainz", "artist", "Sawano Hiroyuki")

    assert [h["id"] for h in hits] == ["from-worker"]


def test_it_still_raises_when_no_client_can_be_had(monkeypatch):
    def _boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(service_search, "mb_worker", None)
    monkeypatch.setattr("core.musicbrainz_service.get_musicbrainz_service", _boom)

    try:
        service_search._search_service("musicbrainz", "artist", "x")
    except ValueError as exc:
        assert "MusicBrainz" in str(exc)
    else:  # pragma: no cover - the call must not silently succeed
        raise AssertionError("expected a ValueError")
