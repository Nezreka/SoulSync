"""Library-v2 Interactive Search: what it searches for, and the pipeline's
verdict on each hit (core/library2/entity_search.py)."""

from __future__ import annotations

from core.download_plugins.types import TrackResult
from core.library2.entity_search import (
    entity_search_queries,
    entity_search_target,
    entity_track_hint,
    judge_results,
)
from core.matching_engine import MusicMatchingEngine
from tests.downloads.test_download_decisions import env  # noqa: F401 - the real-engine fixture

KERNKRAFT = {
    "track_id": 1726, "album_id": 9, "artist_name": "Topic", "album_name": "Kernkraft 400 (A Better Day)",
    "track_title": "Kernkraft 400 (A Better Day)", "track_artists": ["Topic", "A7S"],
    "duration_ms": 165_800, "quality_profile_id": None,
}


def _hit(artist, title, **over):
    base = dict(username="hifi", filename=f"hifi::{artist} - {title}", size=40_000_000,
                bitrate=9216, duration=165_800, quality="flac", artist=artist, title=title,
                bit_depth=24, sample_rate=96_000, free_upload_slots=1, upload_speed=0,
                queue_length=0)
    base.update(over)
    return TrackResult(**base)


def test_a_track_is_searched_like_an_automatic_download_searches_it():
    from core.downloads.pinned_batch import track_object
    from core.downloads.search_queries import automatic_search_queries

    engine = MusicMatchingEngine()
    ladder = automatic_search_queries(engine, track_object({
        "name": "Kernkraft 400 (A Better Day)", "artists": ["Topic", "A7S"],
        "album": "Kernkraft 400 (A Better Day)", "duration_ms": 165_800}))

    assert entity_search_queries(KERNKRAFT, engine) == ladder
    assert len(ladder) > 2 and "Kernkraft 400 (A Better Day)" in ladder
    release = {"album_id": 9, "artist_name": "Topic", "album_name": "Greatest Hits"}
    assert entity_search_queries(release, engine) == ["Topic Greatest Hits"]


def test_release_sources_get_the_song_and_the_window_names_everyone_on_it():
    # the hint adds the artist + album query on Usenet/torrent; no catalogue
    # context, whose hydration would write to the library during a search
    assert entity_track_hint(KERNKRAFT) == {
        "title": "Kernkraft 400 (A Better Day)", "artist": "Topic",
        "album": "Kernkraft 400 (A Better Day)", "deezer_id": None}
    assert entity_track_hint({"album_id": 9, "artist_name": "Topic"}) is None
    assert entity_search_target(KERNKRAFT) == {
        "title": "Kernkraft 400 (A Better Day)", "album": "Kernkraft 400 (A Better Day)",
        "artists": ["Topic", "A7S"]}


def test_hits_are_judged_against_every_credited_artist(env):  # noqa: F811
    right = _hit("Topic, A7S", "Kernkraft 400 (A Better Day)")
    cover = _hit("Robbe, Kuokka, ropsu", "Kernkraft 400 (A Better Day)")
    sped_up = _hit("Akoj, X-Tech, X-Hall", "Kernkraft 400 (A Better Day) (Sped Up)", duration=120_000)
    lead_only = _hit("Topic", "Kernkraft 400 (A Better Day) (feat. A7S)")
    lead_plain = _hit("Topic", "Kernkraft 400 (A Better Day)")
    release = _hit("Topic x A7S", "Kernkraft 400 A Better Day", username="usenet",
                   filename="token-1", quality="mp3", bitrate=320, bit_depth=None, sample_rate=None,
                   duration=0, _source_metadata={
                       "protocol": "usenet",
                       "release_title": "Topic x A7S - Kernkraft 400 A Better Day-602445905942-SINGLE-WEB-2022-ZzZz"})
    rows = [right, cover, sped_up, lead_only, lead_plain, release]

    both = judge_results(None, KERNKRAFT, rows, [], "Topic Kernkraft 400")
    assert both[id(lead_plain)]["accepted"] is True
    assert both[id(cover)]["code"] == "artist_mismatch"

    # A library that credits only the second artist still finds the song
    # wherever that artist is named: the credit list, a collaboration, the title.
    second_only = {**KERNKRAFT, "track_artists": ["A7S"]}
    verdicts = judge_results(None, second_only, rows, [], "A7S Kernkraft 400")

    assert verdicts[id(right)]["accepted"] is True
    assert verdicts[id(lead_only)]["accepted"] is True
    assert verdicts[id(release)]["accepted"] is True
    assert verdicts[id(lead_plain)]["accepted"] is False
    assert verdicts[id(cover)]["accepted"] is False
    assert verdicts[id(sped_up)]["accepted"] is False
