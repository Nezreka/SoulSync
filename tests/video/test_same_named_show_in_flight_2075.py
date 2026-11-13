"""#2075: a download in flight belongs to a show, not to a title. The Office
(2005) downloading made every episode of The Office (2001) read as already
downloading, and the grab for it was answered "already" and dropped."""

import json

from api.video.downloads import _active_episode_keys, _same_show


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def get_active_video_downloads(self):
        return self.rows


def _dl(season, episode, **cols):
    return {"kind": "show", "title": "The Office",
            "search_ctx": json.dumps({"season": season, "episode": episode}), **cols}


US = {"media_id": "2316", "media_source": "tmdb", "year": 2005}
UK = {"media_id": "2996", "media_source": "tmdb", "year": 2001}


def test_the_other_office_is_not_this_one():
    db = _DB([_dl(1, 1, **US), _dl(1, 2, **US)])
    assert _active_episode_keys(db, "The Office", UK["media_id"], "tmdb", 2001) == set()
    assert _active_episode_keys(db, "The Office", US["media_id"], "tmdb", 2005) == {(1, 1), (1, 2)}


def test_the_year_decides_when_the_ids_are_from_different_catalogues():
    # one side is a library row, the other a tmdb result: the ids cannot be compared
    library_us = _dl(1, 1, media_id="14", media_source="library", year=2005)
    assert not _same_show(library_us, "The Office", "2996", "tmdb", 2001)
    assert _same_show(library_us, "The Office", "2316", "tmdb", 2005)


def test_with_nothing_else_to_go_on_the_title_still_matches():
    # an older download row with no id and no year: the dedup it always had
    assert _same_show(_dl(1, 1), "The Office", "2996", "tmdb", 2001)
    assert _same_show(_dl(1, 1, year=2005), "The Office")
    assert not _same_show(_dl(1, 1), "Parks and Recreation")


def test_same_id_wins_over_a_wrong_year():
    assert _same_show(_dl(1, 1, media_id="2316", media_source="tmdb", year=2006),
                      "The Office", 2316, "TMDB", 2005)
