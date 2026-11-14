"""#2071: a box set filed under its first composer finds nothing in an
artist-scoped MusicBrainz search. match_release asks again by title alone and
takes only an answer the title pins down by itself."""

from unittest.mock import MagicMock

from core.musicbrainz_service import MusicBrainzService, _is_pre_title_alone_miss

BOX = "Nodame Cantabile Best 100 Collection Box"
VARIOUS = MusicBrainzService.VARIOUS_ARTISTS_MBID


def _service(by_title, scoped=()):
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc.saved = []

    def search(album, artist=None, **kwargs):
        return list(scoped) if artist else list(by_title)

    svc.mb_client.search_release.side_effect = search
    svc._check_cache = lambda *a, **k: None
    svc._save_to_cache = lambda *a, **k: svc.saved.append(a)
    return svc


def _release(title, mbid, group, artist_id="a-1", artist="Someone"):
    return {"id": mbid, "title": title, "score": 100, "release-group": {"id": group},
            "artist-credit": [{"name": artist, "artist": {"id": artist_id, "name": artist}}]}


def test_a_various_artists_box_is_found_by_its_title():
    svc = _service([_release(BOX, "mb-box", "rg-box", VARIOUS, "Various Artists"),
                    _release(BOX + " 2", "mb-other", "rg-other")])
    match = svc.match_release(BOX, "Pyotr Ilyich Tchaikovsky")
    assert match and match["mbid"] == "mb-box"
    assert match["confidence"] == MusicBrainzService.TITLE_ALONE_CONFIDENCE
    assert match["release_group_mbid"] == "rg-box"


def test_one_release_group_under_that_title_is_enough():
    svc = _service([_release(BOX, "mb-cd", "rg-box"), _release(BOX, "mb-digital", "rg-box")])
    assert svc.match_release(BOX, "Wrong Artist")["mbid"] == "mb-cd"


def test_two_different_albums_with_the_title_stay_unmatched():
    svc = _service([_release(BOX, "mb-1", "rg-1"), _release(BOX, "mb-2", "rg-2")])
    assert svc.match_release(BOX, "Wrong Artist") is None
    # the miss says the title-alone search ran, so it is not asked again
    assert svc.saved[-1][4] == {"title_alone": 1}


def test_a_short_title_never_stands_alone():
    svc = _service([_release("Greatest Hits", "mb-1", "rg-1", VARIOUS)])
    assert svc.match_release("Greatest Hits", "Wrong Artist") is None


def test_a_near_title_is_not_the_title():
    svc = _service([_release(BOX + " Vol. 2", "mb-1", "rg-1", VARIOUS)])
    assert svc.match_release(BOX, "Wrong Artist") is None


def test_an_artist_scoped_answer_is_never_second_guessed():
    scoped = [_release(BOX, "mb-scoped", "rg-s", "a-9", "Pyotr Ilyich Tchaikovsky")]
    svc = _service([_release(BOX, "mb-box", "rg-box", VARIOUS)], scoped=scoped)
    assert svc.match_release(BOX, "Pyotr Ilyich Tchaikovsky")["mbid"] == "mb-scoped"


def test_an_old_empty_miss_is_asked_once_more():
    old = {"musicbrainz_id": None, "confidence": 0, "metadata": None}
    assert _is_pre_title_alone_miss(old, "Artist")
    assert not _is_pre_title_alone_miss(old, None)
    assert not _is_pre_title_alone_miss({**old, "metadata": {"title_alone": 1}}, "Artist")
    assert not _is_pre_title_alone_miss({**old, "confidence": 55}, "Artist")
    assert not _is_pre_title_alone_miss({**old, "musicbrainz_id": "mb"}, "Artist")


# -- the album's own tracks pin down what the title cannot (Storm's follow-up:
# "AD:PIANO" by Diverse System, every track by somebody else)

def _with_tracks(svc, track_lists):
    svc.mb_client.get_release.side_effect = lambda mbid, includes=None: (
        {"id": mbid, "media": [{"tracks": [{"title": t} for t in track_lists[mbid]]}]}
        if track_lists.get(mbid) is not None else None)
    return svc


PIANO = ["Sunset Avenue", "Felys", "Rosin", "Luminous"]


def test_a_circles_compilation_is_found_through_its_tracks():
    svc = _with_tracks(_service([_release("AD:PIANO", "mb-ad", "rg-ad", "a-ds", "Diverse System")]),
                       {"mb-ad": PIANO + ["Twelve More"]})
    match = svc.match_release("AD:PIANO", "Feryquitous", track_titles=PIANO[:3])
    assert match and match["mbid"] == "mb-ad"
    # a title with query syntax in it is asked as a phrase, not as field AD
    assert svc.mb_client.search_release.call_args_list[-1].kwargs["strict"] is True


def test_a_short_title_with_other_peoples_tracks_stays_unmatched():
    svc = _with_tracks(_service([_release("Forever", "mb-f", "rg-f")]),
                       {"mb-f": ["Something", "Else", "Entirely"]})
    assert svc.match_release("Forever", "Wrong Artist", track_titles=PIANO) is None
    assert svc.saved[-1][4] == {"title_alone": 1, "track_list": 1}


def test_one_track_named_like_its_single_proves_nothing():
    svc = _with_tracks(_service([_release("Forever", "mb-f", "rg-f")]), {"mb-f": ["Forever"]})
    assert svc.match_release("Forever", "Wrong Artist", track_titles=["Forever"]) is None
    svc.mb_client.get_release.assert_not_called()


def test_the_tracks_tell_two_albums_of_one_title_apart():
    svc = _with_tracks(_service([_release(BOX, "mb-1", "rg-1"), _release(BOX, "mb-2", "rg-2")]),
                       {"mb-1": ["Nothing", "Like", "It"], "mb-2": PIANO})
    assert svc.match_release(BOX, "Wrong Artist", track_titles=PIANO)["mbid"] == "mb-2"


def test_two_albums_that_both_carry_the_tracks_stay_unmatched():
    svc = _with_tracks(_service([_release("Forever", "mb-1", "rg-1"), _release("Forever", "mb-2", "rg-2")]),
                       {"mb-1": PIANO, "mb-2": PIANO})
    assert svc.match_release("Forever", "Wrong Artist", track_titles=PIANO) is None


def test_most_of_the_tracks_is_enough_and_half_is_not():
    owned = PIANO + ["A Bonus Nobody Lists"]
    svc = _with_tracks(_service([_release("Forever", "mb-1", "rg-1")]), {"mb-1": PIANO})
    assert svc.match_release("Forever", "Wrong Artist", track_titles=owned)["mbid"] == "mb-1"
    svc = _with_tracks(_service([_release("Forever", "mb-1", "rg-1")]), {"mb-1": PIANO[:2]})
    assert svc.match_release("Forever", "Wrong Artist", track_titles=owned) is None


def test_a_track_list_that_could_not_be_read_is_not_written_down_as_a_miss():
    svc = _with_tracks(_service([_release("Forever", "mb-1", "rg-1")]), {})
    assert svc.match_release("Forever", "Wrong Artist", track_titles=PIANO) is None
    assert svc.saved == []


def test_a_miss_from_before_the_track_check_is_asked_again():
    old = {"musicbrainz_id": None, "confidence": 0, "metadata": {"title_alone": 1}}
    assert _is_pre_title_alone_miss(old, "Someone", PIANO)
    assert not _is_pre_title_alone_miss(old, "Someone", ["Forever"])
    assert not _is_pre_title_alone_miss(old, "Someone")
    checked = {"musicbrainz_id": None, "confidence": 0,
               "metadata": {"title_alone": 1, "track_list": 1}}
    assert not _is_pre_title_alone_miss(checked, "Someone", PIANO)
