"""Plex re-keys tracks on a metadata refresh, so a durable manual match's stored
ratingKey (and the SoulSync DB id, which IS that ratingKey) can both go stale at
once — every DB-side lookup lands on the same dead key and fetchItem 404s, so the
manually-matched track gets dropped on sync (wolf39us). The fix re-resolves the
match against LIVE Plex by the matched track's metadata, disambiguated by the
stored file path so the user's EXACT chosen track wins, and heals the stored id.
"""

from __future__ import annotations

from services.sync_service import reresolve_manual_match_live_plex


class _PlexTrack:
    def __init__(self, rating_key, file):
        self.ratingKey = rating_key
        self.title = f"track-{rating_key}"

        class _Part:
            def __init__(self, f): self.file = f
        class _Media:
            def __init__(self, f): self.parts = [_Part(f)]
        self.media = [_Media(file)]


class _TrackInfo:
    def __init__(self, plex_track):
        self._original_plex_track = plex_track


class _MediaClient:
    def __init__(self, results):
        self._results = results
        self.calls = []
    def search_tracks(self, title, artist, limit=15):
        self.calls.append((title, artist))
        return self._results


class _CacheDb:
    def __init__(self):
        self.healed = []
    def save_manual_library_match(self, profile_id, source, source_track_id, library_track_id, **meta):
        self.healed.append({"id": library_track_id, "source_track_id": source_track_id, **meta})
        return True


_MATCH = {
    "source": "spotify", "source_title": "It's the End of the World",
    "source_artist": "R.E.M.", "source_album": "Document",
    "library_file_path": "/music/REM/Document/05 - Its the End.flac",
    "library_track_id": 39161,   # stale
}


def test_picks_the_track_matching_the_stored_file_path():
    # Two live candidates (a different version + the real one); the stored file
    # path must select the user's exact track, with its CURRENT ratingKey.
    wrong = _TrackInfo(_PlexTrack(50001, "/music/REM/Live/05 - Its the End (Live).flac"))
    right = _TrackInfo(_PlexTrack(39167, "/music/REM/Document/05 - Its the End.flac"))
    mc = _MediaClient([wrong, right])
    db = _CacheDb()
    live = reresolve_manual_match_live_plex(
        db, mc, _MATCH, profile_id=1, source_track_id="sp1", server_source="plex")
    assert live is not None and live.ratingKey == 39167          # current key, not stale 39161
    assert mc.calls == [("It's the End of the World", "R.E.M.")]
    # healed the stored id to the fresh ratingKey
    assert db.healed and db.healed[0]["id"] == "39167"
    assert db.healed[0]["source_track_id"] == "sp1"


def test_basename_match_handles_server_vs_local_path():
    # stored path is a local path; the Plex part.file is a container path — same basename.
    m = dict(_MATCH, library_file_path="D:\\Music\\REM\\05 - Its the End.flac")
    right = _TrackInfo(_PlexTrack(39167, "/data/Music/REM/05 - Its the End.flac"))
    live = reresolve_manual_match_live_plex(
        _CacheDb(), _MediaClient([right]), m, profile_id=1, source_track_id="sp1", server_source="plex")
    assert live.ratingKey == 39167


def test_no_file_match_falls_back_to_top_result():
    only = _TrackInfo(_PlexTrack(40000, "/music/somewhere/else.flac"))
    live = reresolve_manual_match_live_plex(
        _CacheDb(), _MediaClient([only]), _MATCH, profile_id=1, source_track_id="sp1", server_source="plex")
    assert live.ratingKey == 40000          # never drop a manual match — best-effort top hit


def test_no_results_returns_none_and_does_not_heal():
    db = _CacheDb()
    assert reresolve_manual_match_live_plex(
        db, _MediaClient([]), _MATCH, profile_id=1, source_track_id="sp1", server_source="plex") is None
    assert db.healed == []


def test_missing_title_returns_none():
    m = dict(_MATCH, source_title="")
    assert reresolve_manual_match_live_plex(
        _CacheDb(), _MediaClient([_TrackInfo(_PlexTrack(1, "/x.flac"))]), m,
        profile_id=1, source_track_id="sp1", server_source="plex") is None


def test_never_raises_on_a_broken_media_client():
    class _Boom:
        def search_tracks(self, *a, **k):
            raise RuntimeError("plex down")
    # a transient Plex error must not bubble — the caller falls through, not crash.
    assert reresolve_manual_match_live_plex(
        _CacheDb(), _Boom(), _MATCH, profile_id=1, source_track_id="sp1", server_source="plex") is None


# an exact soundcloud upload (core/wishlist/soundcloud_exact) is only ever its
# own file: the live search runs by its names, so the top hit is the original
# it is a flip of. only the stored file may heal its match.
_EXACT = "soundcloud_exact_123"
_EXACT_MATCH = {
    "source": "soundcloud_exact", "source_title": "Song (DJ Flip)", "source_artist": "Artist A",
    "library_file_path": "/music/Artist A/Song (DJ Flip) [123].mp3",
    "library_track_id": 39161,   # stale
}


def test_exact_upload_never_takes_the_top_result():
    original = _TrackInfo(_PlexTrack(40000, "/music/Artist A/Album/03 - Song.flac"))
    db = _CacheDb()
    assert reresolve_manual_match_live_plex(
        db, _MediaClient([original]), _EXACT_MATCH, profile_id=1,
        source_track_id=_EXACT, server_source="plex") is None
    assert db.healed == []


def test_exact_upload_heals_onto_its_own_file():
    original = _TrackInfo(_PlexTrack(40000, "/music/Artist A/Album/03 - Song.flac"))
    upload = _TrackInfo(_PlexTrack(39170, "/data/Artist A/Song (DJ Flip) [123].mp3"))
    db = _CacheDb()
    live = reresolve_manual_match_live_plex(
        db, _MediaClient([original, upload]), _EXACT_MATCH, profile_id=1,
        source_track_id=_EXACT, server_source="plex")
    assert live is not None and live.ratingKey == 39170
    assert [(h["id"], h["source_track_id"]) for h in db.healed] == [("39170", _EXACT)]


def test_exact_upload_with_no_stored_file_never_heals():
    original = _TrackInfo(_PlexTrack(40000, "/music/Artist A/Album/03 - Song.flac"))
    db = _CacheDb()
    assert reresolve_manual_match_live_plex(
        db, _MediaClient([original]), dict(_EXACT_MATCH, library_file_path=""), profile_id=1,
        source_track_id=_EXACT, server_source="plex") is None
    assert db.healed == []


def test_catalogue_match_still_falls_back_to_the_top_result():
    original = _TrackInfo(_PlexTrack(40000, "/music/Artist A/Album/03 - Song.flac"))
    db = _CacheDb()
    live = reresolve_manual_match_live_plex(
        db, _MediaClient([original]), _EXACT_MATCH | {"source": "spotify"}, profile_id=1,
        source_track_id="sp1", server_source="plex")
    assert live.ratingKey == 40000
    assert [h["id"] for h in db.healed] == ["40000"]
