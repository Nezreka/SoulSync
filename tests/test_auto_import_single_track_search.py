"""Single-file identify must see the original song, not only Deezer's reprise.

`_search_single_track` used to run one plain "artist title" search capped at 5
results. For "How Far I'll Go" Deezer's top 5 were the Reprise and four karaoke
copies, so the real track (136340808) was never scored. It now goes through
`search_song`, which also runs the exact-title filter.
"""

from __future__ import annotations

import types

import pytest

from core.auto_import_worker import AutoImportWorker
from core.deezer_client import DeezerClient


def _track(track_id, name, artist):
    return types.SimpleNamespace(
        id=str(track_id), name=name, artists=[{"name": artist, "id": "1"}],
        album={"name": "Moana", "id": "9"}, image_url="", track_number=1,
        release_date="2016-11-18", total_tracks=1,
    )


REPRISE = _track(136340812, "How Far I'll Go (Reprise)", "Auli'i Cravalho")
KARAOKE = _track(3890762711, "How Far I'll Go (Karaoke Version)", "karaoke SESH")
ORIGINAL = _track(136340808, "How Far I'll Go", "Auli'i Cravalho")


@pytest.fixture()
def deezer(monkeypatch):
    client = DeezerClient.__new__(DeezerClient)
    calls = []

    def search_tracks(query="", limit=20, *, track=None, artist=None, album=None):
        calls.append({"query": query, "track": track, "artist": artist})
        if track:  # the exact-title filter finds the original
            return [ORIGINAL, REPRISE]
        return [REPRISE, KARAOKE]  # plain search: no original

    monkeypatch.setattr(client, "search_tracks", search_tracks)
    import core.metadata_service as ms

    monkeypatch.setattr(ms, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(ms, "get_client_for_source", lambda _s: client)
    client._calls = calls
    return client


def test_identify_picks_the_original_not_the_reprise(deezer):
    worker = AutoImportWorker.__new__(AutoImportWorker)
    out = worker._search_single_track("Auli'i Cravalho", "How Far I'll Go", "")

    assert out is not None
    assert out["track_id"] == "136340808"
    assert out["track_name"] == "How Far I'll Go"
    assert any(c["track"] == "How Far I'll Go" for c in deezer._calls)
