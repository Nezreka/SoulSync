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
