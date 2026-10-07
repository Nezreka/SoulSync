"""#1582: the deezer downloader never got the original "How Far I'll Go".

download searches give every source a plain query string, and deezer's
free-text search ranks the reprise, karaoke and key-shifted copies first and
leaves the original out. the song now rides along as a hint: deezer fetches
the track's own id when the task came from deezer, and runs a track:"title"
search kept to the track's own artist.
"""

from __future__ import annotations

import asyncio

import pytest

from core.deezer_download_client import DeezerDownloadClient
from core.downloads.track_hint import (
    current_track_hint,
    deezer_track_id,
    hint_from_track,
    track_hint_context,
)

ORIGINAL = {"id": 136340808, "title": "How Far I'll Go", "duration": 163,
            "artist": {"name": "Auli'i Cravalho"}, "album": {"title": "Moana (Deluxe)"}}
REPRISE = {"id": 1, "title": "How Far I'll Go (Reprise)", "duration": 60,
           "artist": {"name": "Auli'i Cravalho"}, "album": {"title": "Moana"}}
KARAOKE = {"id": 2, "title": "How Far I'll Go (Karaoke)", "duration": 163,
           "artist": {"name": "Karaoke Kings"}, "album": {"title": "Disney Karaoke"}}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _client(monkeypatch, *, scoped=(ORIGINAL, KARAOKE), plain=(REPRISE,)):
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._authenticated = True
    c._config = None
    c._quality = "flac"
    calls = []

    def api_get(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        if url.endswith("/search"):
            q = (params or {}).get("q", "")
            return _Resp({"data": list(scoped if q.startswith("track:") else plain)})
        if "/track/" in url:
            return _Resp(dict(ORIGINAL))
        return _Resp({})

    monkeypatch.setattr(c, "_api_get", api_get)
    return c, calls


def _ids(results):
    return [r.filename.split("||", 1)[0] for r in results]


# -- the hint --

def test_the_hint_uses_the_tracks_own_artist_not_the_album_artist():
    hint = hint_from_track({"name": "Friend Like Me",
                            "artists": [{"name": "Robin Williams"}],
                            "album": {"name": "Aladdin", "artists": [{"name": "Alan Menken"}]}})
    assert hint["title"] == "Friend Like Me" and hint["artist"] == "Robin Williams"


def test_a_deezer_id_is_only_trusted_from_deezer():
    assert deezer_track_id({"uri": "deezer:track:136340808"}) == "136340808"
    assert deezer_track_id({"external_urls": {"deezer": "https://www.deezer.com/track/77"}}) == "77"
    assert deezer_track_id({"_source": "deezer", "id": "55"}) == "55"
    assert deezer_track_id({"id": "1440833098"}) is None          # an itunes id is numeric too
    assert deezer_track_id({"uri": "spotify:track:abc"}) is None


# -- the deezer search --

def test_the_original_comes_first_and_other_artists_stay_out(monkeypatch):
    c, _ = _client(monkeypatch)
    with track_hint_context({"title": "How Far I'll Go", "artist": "Auli'i Cravalho", "deezer_id": None}):
        tracks, _ = c._search_sync("aulii cravalho how far ill go")
    assert _ids(tracks) == ["136340808", "1"]        # original, then the plain results


def test_the_tracks_own_id_is_fetched(monkeypatch):
    c, calls = _client(monkeypatch, scoped=())
    with track_hint_context({"title": "How Far I'll Go", "artist": "Auli'i Cravalho",
                             "deezer_id": "136340808"}):
        tracks, _ = c._search_sync("anything")
    assert _ids(tracks)[0] == "136340808"
    assert any(u.endswith("/track/136340808") for u, _ in calls)


def test_once_per_task_not_per_query(monkeypatch):
    c, calls = _client(monkeypatch)
    with track_hint_context({"title": "How Far I'll Go", "artist": "Auli'i Cravalho", "deezer_id": None}):
        c._search_sync("query one")
        tracks, _ = c._search_sync("query two")
    assert sum(1 for _, p in calls if str(p.get("q", "")).startswith("track:")) == 1
    assert _ids(tracks)[0] == "136340808"


def test_without_a_hint_nothing_changes(monkeypatch):
    c, calls = _client(monkeypatch)
    tracks, _ = c._search_sync("how far ill go")
    assert _ids(tracks) == ["1"] and len(calls) == 1


def test_no_artist_means_no_title_only_guess(monkeypatch):
    """dozens of songs share a title: without the artist the scoped search
    would only add noise"""
    c, calls = _client(monkeypatch)
    with track_hint_context({"title": "How Far I'll Go", "artist": "", "deezer_id": None}):
        c._search_sync("how far ill go")
    assert not any(str(p.get("q", "")).startswith("track:") for _, p in calls)


# -- the seam: orchestrator search -> source thread --

def test_the_orchestrator_hands_the_hint_to_the_source(monkeypatch):
    from core import download_orchestrator as do
    from core.async_utils import run_blocking

    seen = []

    class _Src:
        async def search(self, query, timeout=None, progress_callback=None):
            def _in_thread():
                seen.append(current_track_hint())
                return [], []
            return await run_blocking(_in_thread)

    orch = do.DownloadOrchestrator.__new__(do.DownloadOrchestrator)
    orch.mode = "deezer_dl"
    monkeypatch.setattr(orch, "_client", lambda name: _Src(), raising=False)
    orch.registry = type("_Reg", (), {"display_name": staticmethod(lambda name: name)})()
    hint = {"title": "How Far I'll Go", "artist": "Auli'i Cravalho", "deezer_id": None}
    asyncio.run(orch.search("q", track_hint=hint))
    assert seen == [hint]
    assert current_track_hint() is None      # reset after the call


@pytest.mark.parametrize("artists", [[{"name": "Auli'i Cravalho"}], ["Auli'i Cravalho"]])
def test_artist_shapes(artists):
    assert hint_from_track({"name": "x", "artists": artists})["artist"] == "Auli'i Cravalho"
