"""Library-v2 Interactive Search: what it searches for, and the pipeline's
verdict on each hit (core/library2/entity_search.py)."""

from __future__ import annotations

from core.download_plugins.types import TrackResult
from core.library2.entity_search import entity_search_queries, judge_results, merge_results
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


def test_a_track_is_searched_by_itself_and_by_its_release():
    assert entity_search_queries(KERNKRAFT) == ["Topic Kernkraft 400 (A Better Day)"]
    on_album = {**KERNKRAFT, "album_name": "Greatest Hits"}
    assert entity_search_queries(on_album) == ["Topic Kernkraft 400 (A Better Day)", "Topic Greatest Hits"]
    release = {"album_id": 9, "artist_name": "Topic", "album_name": "Greatest Hits"}
    assert entity_search_queries(release, str.upper) == ["Topic GREATEST HITS"]


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


def test_merged_searches_keep_one_row_and_prefer_an_accepted_sighting():
    rejected = {"username": "u", "filename": "a.flac", "quality_score": 0.9,
                "decision": {"accepted": False, "code": "artist_mismatch"}}
    accepted = {"username": "u", "filename": "a.flac", "quality_score": 0.9,
                "decision": {"accepted": True, "code": "accepted"}}
    other = {"username": "v", "filename": "b.mp3", "quality_score": 0.5}

    merged = merge_results([[rejected, other], [accepted]])

    assert merged == [accepted, other]
