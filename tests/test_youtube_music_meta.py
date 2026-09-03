"""Seam tests for the YouTube Music catalog path.

yt-dlp's flat playlist extraction is a video view: title, id, duration,
channel — no album, ever. music.youtube.com playlists have a real catalog
entry with both, so `parse_youtube_playlist` tries it first and falls back to
yt-dlp on any failure. These pin the field projection and — more importantly —
that every failure mode returns None rather than raising or returning a
half-populated playlist that would overwrite a good mirror.

Fixtures are trimmed copies of real ytmusicapi `get_playlist` responses.
"""

from __future__ import annotations

from types import SimpleNamespace
import sys

from core.youtube_music_meta import (
    _get_playlist_paginated,
    playlist_id_from_url,
    search_ytmusic_songs,
    ytmusic_playlist_to_payload,
    ytmusic_search_hit_to_track,
)

URL = "https://music.youtube.com/playlist?list=PLExamplePlaylistId00000000000000"


def _raw(*tracks):
    return {"id": "PL123", "title": "Example Playlist", "trackCount": len(tracks), "tracks": list(tracks)}


ATV_TRACK = {
    "videoId": "exampleVid1",
    "title": "Example Track",
    "artists": [{"name": "Example Artist", "id": "UCExampleChannelId000000"}],
    "album": {"name": "Example Track", "id": "MPREb_ExampleAlbumId"},
    "duration": "3:45",
    "duration_seconds": 225,
    "videoType": "MUSIC_VIDEO_TYPE_ATV",
    "isAvailable": True,
}


# ── URL parsing ───────────────────────────────────────────────────────────


def test_playlist_id_extracted_from_url():
    assert playlist_id_from_url(URL) == "PLExamplePlaylistId00000000000000"
    assert playlist_id_from_url("https://music.youtube.com/playlist?list=LM") == "LM"


def test_playlist_id_missing_or_bad_input():
    for bad in (None, "", "   ", 42, "https://music.youtube.com/", "not a url"):
        assert playlist_id_from_url(bad) == ""


# ── projection ────────────────────────────────────────────────────────────


def test_catalog_track_projects_all_fields():
    payload = ytmusic_playlist_to_payload(_raw(ATV_TRACK), URL)
    assert payload["name"] == "Example Playlist"
    assert payload["source"] == "youtube"
    assert payload["track_count"] == 1
    track = payload["tracks"][0]
    assert track["id"] == "exampleVid1"
    assert track["name"] == "Example Track"
    assert track["artists"] == ["Example Artist"]
    # The field yt-dlp can never supply — the reason this path exists.
    assert track["album"] == "Example Track"
    assert track["duration_ms"] == 225_000
    assert track["video_type"] == "MUSIC_VIDEO_TYPE_ATV"


def test_multiple_artists_keep_primary_first():
    # Downstream takes artists[0]; a feat. credit must not displace the primary.
    payload = ytmusic_playlist_to_payload(
        _raw({**ATV_TRACK, "artists": [{"name": "Primary Artist"}, {"name": "Featured Artist"}]}), URL)
    assert payload["tracks"][0]["artists"] == ["Primary Artist", "Featured Artist"]
    assert payload["tracks"][0]["raw_artist"] == "Primary Artist"


def test_duplicate_and_blank_artist_names_are_cleaned():
    payload = ytmusic_playlist_to_payload(
        _raw({**ATV_TRACK,
              "artists": [{"name": "Solo Artist"}, {"name": ""}, {"name": "Solo Artist"}, {"name": None}]}), URL)
    assert payload["tracks"][0]["artists"] == ["Solo Artist"]


def test_ugc_track_without_album_is_kept():
    # A user upload inside a YT Music playlist: channel as artist, no album.
    # That is exactly what the yt-dlp path would have produced, so it must be
    # kept rather than dropped — otherwise this path LOSES tracks.
    payload = ytmusic_playlist_to_payload(
        _raw({"videoId": "abc123", "title": "Some Fan Upload",
              "artists": [{"name": "Uploader Channel"}], "album": None,
              "duration_seconds": 190, "videoType": "MUSIC_VIDEO_TYPE_UGC"}), URL)
    track = payload["tracks"][0]
    assert track["artists"] == ["Uploader Channel"]
    assert track["album"] == ""
    assert track["video_type"] == "MUSIC_VIDEO_TYPE_UGC"


def test_track_with_no_artists_falls_back_to_unknown():
    payload = ytmusic_playlist_to_payload(
        _raw({**ATV_TRACK, "artists": []}), URL)
    assert payload["tracks"][0]["artists"] == ["Unknown Artist"]
    assert payload["tracks"][0]["raw_artist"] == ""


def test_missing_duration_is_zero_not_a_crash():
    for seconds in (None, "", "abc", {}):
        payload = ytmusic_playlist_to_payload(
            _raw({**ATV_TRACK, "duration_seconds": seconds}), URL)
        assert payload["tracks"][0]["duration_ms"] == 0


def test_placeholder_rows_are_dropped():
    # Deleted / region-blocked entries come back with neither title nor id.
    # mirror_playlist rejects an all-empty payload outright, so they must not
    # reach it.
    payload = ytmusic_playlist_to_payload(
        _raw(ATV_TRACK, {"videoId": None, "title": None, "artists": []}), URL)
    assert payload["track_count"] == 1


# ── fallback contract ─────────────────────────────────────────────────────
# Everything below must return None so parse_youtube_playlist uses yt-dlp.
# A partial payload here would overwrite a good mirror with a worse one.


def test_none_response_returns_none():
    assert ytmusic_playlist_to_payload(None, URL) is None
    assert ytmusic_playlist_to_payload("not a mapping", URL) is None


def test_playlist_with_no_tracks_returns_none():
    assert ytmusic_playlist_to_payload(_raw(), URL) is None


def test_playlist_with_only_placeholder_rows_returns_none():
    assert ytmusic_playlist_to_payload(
        _raw({"videoId": "", "title": ""}, {"videoId": None, "title": None}), URL) is None


def test_malformed_track_entries_are_skipped_not_fatal():
    payload = ytmusic_playlist_to_payload(_raw("junk", None, 42, ATV_TRACK), URL)
    assert payload["track_count"] == 1


def test_fetch_returns_none_without_a_playlist_id():
    # Guards before any import of ytmusicapi, so this holds whether or not the
    # optional dependency is installed.
    from core.youtube_music_meta import fetch_ytmusic_playlist
    assert fetch_ytmusic_playlist("https://music.youtube.com/") is None
    assert fetch_ytmusic_playlist("") is None


# ── "- Topic" channel names ───────────────────────────────────────────────
# An artist with no canonical catalog entry comes back as the raw auto-channel
# name. Left alone it reaches the mirror as "Example Band - Topic" and never matches
# the "Example Band" already in the library. Seen on 10 of 1995 real tracks.


def test_topic_suffix_stripped_from_catalog_artist():
    payload = ytmusic_playlist_to_payload(
        _raw({**ATV_TRACK, "artists": [{"name": "Example Band - Topic"}]}), URL)
    assert payload["tracks"][0]["artists"] == ["Example Band"]
    assert payload["tracks"][0]["raw_artist"] == "Example Band"


def test_topic_stripping_dedupes_against_the_canonical_name():
    # Same artist credited both ways must collapse to one name, not two.
    payload = ytmusic_playlist_to_payload(
        _raw({**ATV_TRACK, "artists": [{"name": "Second Artist"}, {"name": "Second Artist - Topic"}]}),
        URL)
    assert payload["tracks"][0]["artists"] == ["Second Artist"]


def test_artist_named_only_topic_is_not_emptied():
    payload = ytmusic_playlist_to_payload(
        _raw({**ATV_TRACK, "artists": [{"name": "- Topic"}]}), URL)
    assert payload["tracks"][0]["artists"] == ["- Topic"]


def test_missing_video_type_is_empty_not_none():
    # 160 of 1995 real tracks carry no videoType; it must not become "None".
    payload = ytmusic_playlist_to_payload(
        _raw({**ATV_TRACK, "videoType": None}), URL)
    assert payload["tracks"][0]["video_type"] == ""


# ── song search projection ────────────────────────────────────────────────

SONG_HIT = {
    "videoId": "exampleVid1",
    "title": "Example Track",
    "artists": [{"name": "Example Artist", "id": "UCExampleChannelId000000"}],
    "album": {"name": "Example Album", "id": "MPREb_ExampleAlbumId"},
    "duration": "3:45",
    "duration_seconds": 225,
    "videoType": "MUSIC_VIDEO_TYPE_ATV",
    "thumbnails": [{"url": "https://i.ytimg.com/vi/exampleVid1/low.jpg"},
                   {"url": "https://i.ytimg.com/vi/exampleVid1/high.jpg"}],
}


def test_search_hit_projects_artist_album_duration_and_watch_url():
    track = ytmusic_search_hit_to_track(SONG_HIT)
    assert track["id"] == "exampleVid1"
    assert track["name"] == "Example Track"
    assert track["artists"] == ["Example Artist"]
    assert track["album"] == "Example Album"
    assert track["duration_ms"] == 225_000
    assert track["video_type"] == "MUSIC_VIDEO_TYPE_ATV"
    assert track["url"] == "https://www.youtube.com/watch?v=exampleVid1"
    assert track["thumbnail"] == "https://i.ytimg.com/vi/exampleVid1/high.jpg"


def test_search_hit_without_video_id_is_dropped():
    assert ytmusic_search_hit_to_track({**SONG_HIT, "videoId": ""}) is None
    assert ytmusic_search_hit_to_track({**SONG_HIT, "videoId": None}) is None
    assert ytmusic_search_hit_to_track("junk") is None


def test_search_hit_without_title_is_dropped():
    assert ytmusic_search_hit_to_track({**SONG_HIT, "title": ""}) is None
    assert ytmusic_search_hit_to_track({**SONG_HIT, "title": None}) is None


def test_search_hit_topic_suffix_stripped():
    track = ytmusic_search_hit_to_track(
        {**SONG_HIT, "artists": [{"name": "Example Band - Topic"}]})
    assert track["artists"] == ["Example Band"]


def test_search_hit_parses_duration_string_when_seconds_missing():
    track = ytmusic_search_hit_to_track(
        {**SONG_HIT, "duration_seconds": None, "duration": "3:45"})
    assert track["duration_ms"] == 225_000


def test_search_hit_parses_hour_duration_string():
    track = ytmusic_search_hit_to_track(
        {**SONG_HIT, "duration_seconds": None, "duration": "1:02:03"})
    assert track["duration_ms"] == 3_723_000


def test_search_ytmusic_songs_empty_query_returns_none():
    assert search_ytmusic_songs("") is None
    assert search_ytmusic_songs("   ") is None
    assert search_ytmusic_songs(None) is None  # type: ignore[arg-type]


def _install_fake_ytmusic(monkeypatch, search_impl):
    class _FakeYTMusic:
        def __init__(self, *_a, **_k):
            pass

        def search(self, query, filter="songs", limit=50):
            return search_impl(query, filter, limit)

    monkeypatch.setitem(sys.modules, "ytmusicapi", SimpleNamespace(YTMusic=_FakeYTMusic))


def test_search_ytmusic_songs_missing_library_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "ytmusicapi", None)
    assert search_ytmusic_songs("Artist - Title") is None


def test_search_ytmusic_songs_exception_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("inner-tube down")

    monkeypatch.setitem(sys.modules, "ytmusicapi", SimpleNamespace(YTMusic=_Boom))
    assert search_ytmusic_songs("Artist - Title") is None


def test_search_ytmusic_songs_empty_or_unusable_returns_none(monkeypatch):
    _install_fake_ytmusic(monkeypatch, lambda *_a: [])
    assert search_ytmusic_songs("Artist - Title") is None

    _install_fake_ytmusic(monkeypatch, lambda *_a: [{"title": "No Id"}])
    assert search_ytmusic_songs("Artist - Title") is None


def test_search_ytmusic_songs_sorts_atv_first(monkeypatch):
    _install_fake_ytmusic(monkeypatch, lambda *_a: [
        {**SONG_HIT, "videoId": "omv", "title": "Official Video",
         "videoType": "MUSIC_VIDEO_TYPE_OMV"},
        {**SONG_HIT, "videoId": "atv", "title": "Official Audio",
         "videoType": "MUSIC_VIDEO_TYPE_ATV"},
        {**SONG_HIT, "videoId": "ugc", "title": "Cover",
         "videoType": "MUSIC_VIDEO_TYPE_UGC"},
    ])
    hits = search_ytmusic_songs("Example Artist - Example Track")
    assert [h["id"] for h in hits] == ["atv", "omv", "ugc"]


# TEMPORARY, delete alongside the function once
# sigma67/ytmusicapi#953 ships and get_playlist grows its own validate_responses.


class _CountingClient:
    """A fake ytmusicapi client whose get_playlist() replays one canned
    response per call, in order, and records how many times it was called."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get_playlist(self, playlist_id, limit=None):
        self.calls += 1
        # Repeat the last response if asked for more calls than scripted —
        # keeps a test's intent ("it should have stopped by now") legible
        # instead of an IndexError burying the real assertion.
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]


def test_paginated_no_retry_when_first_attempt_is_already_complete():
    client = _CountingClient([{"tracks": [1, 2, 3], "trackCount": 3}])
    result = _get_playlist_paginated(client, "PL1")
    assert result["tracks"] == [1, 2, 3]
    assert client.calls == 1


def test_paginated_does_not_retry_a_normal_small_gap():
    # 143/144 (99.3%) — a real, reproducible shape from production: one
    # dead/region-blocked video that legitimately produces no row. Must be
    # accepted on the first attempt, not chased with a full refetch.
    client = _CountingClient([{"tracks": list(range(143)), "trackCount": 144}])
    result = _get_playlist_paginated(client, "PL1")
    assert len(result["tracks"]) == 143
    assert client.calls == 1


def test_paginated_retries_a_severely_truncated_result():
    # 2000/3132 (63.9%) — the real bug shape: catastrophically short, not
    # "a couple of dead entries". Recovers to the normal ceiling on retry.
    client = _CountingClient([
        {"tracks": list(range(2000)), "trackCount": 3132},
        {"tracks": list(range(3032)), "trackCount": 3132},
    ])
    result = _get_playlist_paginated(client, "PL1")
    assert len(result["tracks"]) == 3032
    assert client.calls == 2


def test_paginated_gives_up_after_max_attempts_and_keeps_the_best_seen():
    # Never crosses the completeness threshold — bounded, not infinite.
    client = _CountingClient([
        {"tracks": [1], "trackCount": 100},
        {"tracks": [1, 2], "trackCount": 100},   # the best of the two
    ])
    result = _get_playlist_paginated(client, "PL1")
    assert result["tracks"] == [1, 2]
    assert client.calls == 2


def test_paginated_does_not_retry_without_a_trackcount_to_validate_against():
    client = _CountingClient([{"tracks": [1], "trackCount": None}])
    result = _get_playlist_paginated(client, "PL1")
    assert result["tracks"] == [1]
    assert client.calls == 1


def test_paginated_first_attempt_failure_propagates():
    # Unchanged from before this wrapper existed: fetch_ytmusic_playlist's
    # own try/except catches this and falls back to yt-dlp.
    class _Boom:
        def get_playlist(self, playlist_id, limit=None):
            raise RuntimeError("network blip")

    try:
        _get_playlist_paginated(_Boom(), "PL1")
        assert False, "expected the exception to propagate"
    except RuntimeError:
        pass


def test_paginated_later_attempt_failure_keeps_the_partial_result():
    class _FailsOnSecondCall:
        def __init__(self):
            self.calls = 0

        def get_playlist(self, playlist_id, limit=None):
            self.calls += 1
            if self.calls == 1:
                return {"tracks": [1, 2], "trackCount": 3}
            raise RuntimeError("network blip")

    client = _FailsOnSecondCall()
    result = _get_playlist_paginated(client, "PL1")
    assert result["tracks"] == [1, 2]
    assert client.calls == 2
