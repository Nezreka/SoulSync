"""#901: a manual match (Find & Add) on a file-import playlist track was silently
dropped and the track re-appeared as "extra". Root cause: file-import / iTunes-only
tracks arrive with an EMPTY source_track_id, and the whole manual-match system keys
on it — an empty key can't be persisted (no-op) or looked up. Fix: derive a stable
deterministic id from the track's identity so matches stick like they do for
Spotify/YouTube (which carry native ids).
"""

from __future__ import annotations

from core.playlists.source_refs import stable_source_track_id


def test_native_id_is_used_verbatim():
    assert stable_source_track_id({"source_track_id": "2fdfsGuqb6SBX5ocoBWHUd"}) == "2fdfsGuqb6SBX5ocoBWHUd"
    # explicit existing wins over the dict
    assert stable_source_track_id({"source_track_id": "x"}, existing="y") == "y"


def test_file_track_gets_a_deterministic_prefixed_id():
    t = {"track_name": "Slow Ride", "artist_name": "Foghat", "album_name": "Fool for the City"}
    a = stable_source_track_id(t)
    assert a.startswith("file:") and len(a) == len("file:") + 16
    # SAME song → SAME id across calls/re-imports (what the match lookup needs)
    assert stable_source_track_id(dict(t)) == a


def test_identity_is_case_and_field_insensitive_but_distinguishes_songs():
    base = {"track_name": "Slow Ride", "artist_name": "Foghat", "album_name": "Fool for the City"}
    same = {"name": "slow ride", "artist": "FOGHAT", "album": "fool for the city"}  # alt field names + case
    assert stable_source_track_id(base) == stable_source_track_id(same)
    # a different song gets a different id
    assert stable_source_track_id(base) != stable_source_track_id(
        {"track_name": "I Just Want to Make Love to You", "artist_name": "Foghat"})


def test_empty_id_when_no_title():
    assert stable_source_track_id({"artist_name": "Foghat"}) == ""
    assert stable_source_track_id({}) == ""


def test_never_collides_with_a_real_upstream_id():
    # the file: prefix keeps synthetic ids out of the spotify/youtube id space
    fid = stable_source_track_id({"track_name": "x", "artist_name": "y"})
    assert ":" in fid and not fid.replace("file:", "").startswith("file")
