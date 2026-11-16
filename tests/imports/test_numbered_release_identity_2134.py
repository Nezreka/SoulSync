"""#2134: owning or importing one group therapy episode was another one.

deezer's album search for 'Group Therapy 701' returns 703 and 702 before
701, and 638's search lists it last. every name scorer read 'Group Therapy
702' as 0.94 of '701', so:

- the import picked 702 when 701 wasn't in the results and then filed the
  folder's tracks into 702 by position (one folder, the other's metadata)
- the library said it owned 699 because it owned 696, and mapped 699's
  tracks onto 696's rows
"""

from types import SimpleNamespace

from core.imports.album_matching import (
    MATCH_THRESHOLD,
    score_album_search_result,
    score_file_against_track,
)
from core.imports.text_matching import album_similarity, title_similarity
from database.music_database import DatabaseAlbum, MusicDatabase

ARTIST = "Above & Beyond"


def _hit(name, total=23):
    return SimpleNamespace(name=name, artists=[ARTIST], total_tracks=total)


def test_shared_title_similarity_keeps_episode_numbers_apart():
    assert album_similarity("Group Therapy 699", "Group Therapy 696") <= 0.30
    assert title_similarity("Off My Mind (ABGT696) (Mixed)",
                            "Off My Mind (ABGT697) (Mixed)") <= 0.30
    assert title_similarity("Group Therapy (Messages Pt. 1) [ABGT696] (Mixed)",
                            "Group Therapy (Messages Pt. 4) [ABGT696] (Mixed)") <= 0.30
    assert album_similarity("Group Therapy 699 (DJ Mix)", "Group Therapy 699 (DJ Mix)") == 1.0


def test_import_never_identifies_a_folder_as_another_episode():
    """701's folder must not be imported as 702 when the search missed 701."""
    assert score_album_search_result(_hit("Group Therapy 702 (DJ Mix)"),
                                     "Group Therapy 701", ARTIST, 23) == 0.0
    assert score_album_search_result(_hit("Group Therapy 701 (DJ Mix)"),
                                     "Group Therapy 701", ARTIST, 23) > 0.4


def test_file_from_another_episode_does_not_fill_a_slot_by_position():
    """position used to carry 699's intro into 696's intro slot."""
    score = score_file_against_track(
        "/in/01 - Group Therapy Intro (ABGT699) (Mixed).flac",
        {"title": "Group Therapy Intro (ABGT699) (Mixed)", "artist": ARTIST,
         "album": "Group Therapy 699 (DJ Mix)", "track_number": 1, "disc_number": 1},
        {"name": "Group Therapy Intro (ABGT696) (Mixed)", "track_number": 1,
         "disc_number": 1, "artists": [{"name": ARTIST}]},
        target_album="Group Therapy 696 (DJ Mix)",
    )
    assert score < MATCH_THRESHOLD


def test_owning_160_is_not_owning_638():
    """a '(DJ Mix)' suffix happened to trip the version rule; the bare
    episode titles had nothing keeping them apart."""
    db = MusicDatabase.__new__(MusicDatabase)
    owned = DatabaseAlbum(id=1, artist_id=1, title="Group Therapy 160", track_count=23)
    assert db._calculate_album_confidence("Group Therapy 638", ARTIST, owned) < 0.7
    same = DatabaseAlbum(id=2, artist_id=1, title="Group Therapy 638", track_count=23)
    assert db._calculate_album_confidence("Group Therapy 638", ARTIST, same) >= 0.9
