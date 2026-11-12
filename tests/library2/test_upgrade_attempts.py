"""#2060: the memory behind automatic upgrades, and what is worth downloading."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.library2 import upgrade_attempts as U
from core.quality.model import AudioQuality, QualityTarget

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
TARGETS = [QualityTarget.from_dict({"label": "FLAC", "format": "flac"}),
           QualityTarget.from_dict({"label": "MP3 320", "format": "mp3", "min_bitrate": 320})]


def _stamp(days_ago, count=1, file=7):
    at = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return json.dumps({"at": at, "count": count, "file": file})


def test_the_wait_doubles_and_stops_at_ninety_days():
    assert [U.wait_days(n, 7) for n in (1, 2, 3, 4, 5, 9)] == [7, 14, 28, 56, 90, 90]


def test_a_track_never_asked_for_is_due():
    assert U.is_due(None, 7, retry_days=7, now=NOW)
    assert U.is_due("not json", 7, retry_days=7, now=NOW)


def test_a_track_is_due_only_after_its_wait():
    assert not U.is_due(_stamp(6), 7, retry_days=7, now=NOW)
    assert U.is_due(_stamp(8), 7, retry_days=7, now=NOW)
    # the second miss waits twice as long
    assert not U.is_due(_stamp(8, count=2), 7, retry_days=7, now=NOW)
    assert U.is_due(_stamp(15, count=2), 7, retry_days=7, now=NOW)


def test_a_new_file_starts_over():
    # a better copy that is still below the profile is worth following up
    assert U.is_due(_stamp(1, count=4, file=7), 8, retry_days=7, now=NOW)
    assert json.loads(U.next_stamp(_stamp(1, count=4, file=7), 8, NOW))["count"] == 1
    assert json.loads(U.next_stamp(_stamp(1, count=4, file=7), 7, NOW))["count"] == 5
    assert json.loads(U.next_stamp(None, 7, NOW)) == {
        "at": "2026-11-12 12:00:00", "count": 1, "file": 7}


def test_only_a_library_upgrade_row_is_an_upgrade_wish():
    wish = {"source": "library_v2", "lib2_track_id": 42, "upgrade_check": True}
    assert U.is_upgrade_wish(wish) == 42
    assert U.is_upgrade_wish(json.dumps(wish)) == 42
    assert U.is_upgrade_wish({**wish, "upgrade_check": False}) is None   # a missing track
    assert U.is_upgrade_wish({**wish, "source": "playlist"}) is None
    assert U.is_upgrade_wish({"source": "library_v2", "upgrade_check": True}) is None
    assert U.is_upgrade_wish("nope") is None and U.is_upgrade_wish(None) is None


def _basis(fmt="mp3", bitrate=128, source="deezer_dl", days_old=1):
    from core.quality.model import rank_candidate
    quality = AudioQuality(format=fmt, bitrate=bitrate)
    return {"targets": TARGETS, "rank": rank_candidate(quality, TARGETS)[0],
            "score": quality.tier_score(), "source": source,
            "added": (NOW - timedelta(days=days_old)).replace(tzinfo=None)}


def _candidate(username, fmt=None, bitrate=None):
    quality = AudioQuality(format=fmt, bitrate=bitrate) if fmt else None
    return SimpleNamespace(username=username, audio_quality=quality)


STREAMING = {"deezer_dl", "tidal"}


def test_the_source_that_just_delivered_the_file_is_not_asked_again():
    refusal = U.candidate_refusal(_basis(), _candidate("deezer_dl", "mp3", 320),
                                  streaming_sources=STREAMING, now=NOW)
    assert refusal == "the same source just delivered the file this would replace"
    # another source, or a soulseek peer of the same name, may be tried
    assert U.candidate_refusal(_basis(), _candidate("tidal", "flac"),
                               streaming_sources=STREAMING, now=NOW) is None
    assert U.candidate_refusal(_basis(source="some_peer"), _candidate("some_peer", "flac"),
                               streaming_sources=STREAMING, now=NOW) is None
    # and after a month the same source gets one more try: accounts change
    assert U.candidate_refusal(_basis(days_old=31), _candidate("deezer_dl", "mp3", 320),
                               streaming_sources=STREAMING, now=NOW) is None


def test_a_copy_known_to_be_no_better_is_not_downloaded():
    no_better = "no better than the file you have"
    assert U.candidate_refusal(_basis(), _candidate("peer", "mp3", 128), now=NOW) == no_better
    assert U.candidate_refusal(_basis(), _candidate("peer", "mp3", 96), now=NOW) == no_better
    assert U.candidate_refusal(_basis("mp3", 320), _candidate("peer", "mp3", 320), now=NOW) == no_better
    # better within the profile, or still outside it but better than what is there
    assert U.candidate_refusal(_basis(), _candidate("peer", "mp3", 320), now=NOW) is None
    assert U.candidate_refusal(_basis(), _candidate("peer", "flac"), now=NOW) is None
    assert U.candidate_refusal(_basis(), _candidate("peer", "mp3", 256), now=NOW) is None


def test_a_copy_whose_quality_is_not_known_is_left_for_the_import_to_measure():
    assert U.candidate_refusal(_basis(), _candidate("peer"), now=NOW) is None
    assert U.candidate_refusal(_basis(), _candidate("peer", "unknown"), now=NOW) is None
    # slskd often lists an mp3 with no bitrate at all
    assert U.candidate_refusal(_basis(), _candidate("peer", "mp3", None), now=NOW) is None
    # and with nothing to compare against, nothing is refused
    assert U.candidate_refusal(None, _candidate("peer", "mp3", 64), now=NOW) is None
