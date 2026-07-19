"""#1047 P2 — stale Plex ratingKeys must not silently unmatch owned tracks.

Plex re-keys tracks on a metadata refresh/optimize, and SoulSync's db track
id IS that old ratingKey. The sync's fetchItem then 404s and the track —
which exists, matched at full confidence — silently became "missing":
wishlisted and dropped from the playlist. The manual-match path already had
a live-search self-heal; fuzzy matches now get the same rescue.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services.sync_service import rescue_stale_plex_track

_ROOT = Path(__file__).resolve().parent.parent.parent


def _live(rating_key, path):
    live = SimpleNamespace(ratingKey=rating_key)
    live.media = [SimpleNamespace(parts=[SimpleNamespace(file=path)])]
    return live


def _result(rating_key, path):
    return SimpleNamespace(_original_plex_track=_live(rating_key, path))


class _Client:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search_tracks(self, title, artist, limit=15):
        self.calls.append((title, artist))
        return self.results


def _db_track(title="Song", file_path="/music/A/album/03 - Song.flac", tid="123"):
    return SimpleNamespace(id=tid, title=title, file_path=file_path)


def test_rescues_by_exact_file_basename():
    # two versions live on plex; the one whose file matches the db track wins
    client = _Client([
        _result(901, "/music/A/other/Song (Live).flac"),
        _result(902, "/music/A/album/03 - Song.flac"),
    ])
    live = rescue_stale_plex_track(client, _db_track(), "Artist")
    assert live is not None and live.ratingKey == 902


def test_falls_back_to_top_result_without_a_path_match():
    client = _Client([_result(901, "/somewhere/else.flac")])
    live = rescue_stale_plex_track(client, _db_track(), "Artist")
    assert live is not None and live.ratingKey == 901


def test_no_results_returns_none():
    assert rescue_stale_plex_track(_Client([]), _db_track(), "Artist") is None


def test_search_error_returns_none_never_raises():
    class _Boom:
        def search_tracks(self, *a, **k):
            raise RuntimeError("plex down")
    assert rescue_stale_plex_track(_Boom(), _db_track(), "Artist") is None


def test_result_without_live_track_returns_none():
    client = _Client([SimpleNamespace(_original_plex_track=None)])
    assert rescue_stale_plex_track(client, _db_track(), "Artist") is None


def test_rescue_is_wired_into_both_fetch_failure_branches():
    src = (_ROOT / "services" / "sync_service.py").read_text(encoding="utf-8",
                                                             errors="replace")
    # once for the lacks-ratingKey branch, once for the fetchItem exception
    assert src.count("rescued = rescue_stale_plex_track(") == 2
    assert "[Stale-Key Rescue]" in src
