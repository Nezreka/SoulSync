"""#1609 (kevin2xk): Album Release Year Alignment said Weezer by Weezer should be
2008. weezer has eight albums called "Weezer", the title search answered with
the red album first and the job took it. buddy holly is on the blue album
(1994). the library's tracks now pick the release group when titles collide.

shapes mirror live musicbrainz answers (release / recording search, oct 2026).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.repair_jobs.album_release_year_repair import (
    release_group_holding_tracks,
    resolve_canonical_album_year,
)

RED, BLUE, WHITE = "9b8af98f-red", "c7b245c9-blue", "18fa1fe1-white"


def _release(rid, rg, date, rg_disamb=None):
    group = {"id": rg, "first-release-date": date}
    if rg_disamb:
        group["disambiguation"] = rg_disamb
    return {"id": rid, "title": "Weezer", "date": date, "release-group": group}


def _client():
    mb = MagicMock()
    mb.search_release.return_value = [
        _release("149f160f", RED, "2008-06-03"),
        _release("a22e488b", WHITE, "2016-04-01"),
        _release("039f5a32", RED, "2008-06-03"),
    ]
    mb.search_recording.side_effect = lambda title, artist, limit=25: {
        "Buddy Holly": [
            {"title": "Buddy Holly", "releases": [
                {"id": "blue-rel", "title": "Weezer", "release-group": {"id": BLUE}},
                {"id": "best-of", "title": "Greatest Hits", "release-group": {"id": "gh"}},
            ]},
            {"title": "Buddy Holly (live)", "releases": [
                {"id": "x", "title": "Weezer", "release-group": {"id": RED}},
            ]},
        ],
    }.get(title, [])
    mb.get_release_group.side_effect = lambda rg, includes=None: {
        BLUE: {"id": BLUE, "first-release-date": "1994-05-10", "disambiguation": "Blue Album"},
    }.get(rg)
    return mb


def test_the_tracks_pick_the_blue_album_not_the_first_title_hit():
    res = resolve_canonical_album_year(
        mb_client=_client(), album_title="Weezer", artist_name="Weezer",
        track_titles=["Buddy Holly"])
    assert res == ("1994", "1994-05-10", "blue-rel", BLUE)


def test_a_wrong_stored_release_is_checked_against_the_tracks():
    # the musicbrainz worker matched the album by title too, so the stored id
    # can be the red album. a disambiguated group gets verified
    mb = _client()
    mb.get_release.return_value = _release("149f160f", RED, "2008-06-03", rg_disamb="Red Album")
    res = resolve_canonical_album_year(
        mb_client=mb, album_title="Weezer", artist_name="Weezer",
        musicbrainz_release_id="149f160f", track_titles=["Buddy Holly"])
    assert res[0] == "1994"


def test_a_stored_release_the_tracks_agree_with_is_kept():
    mb = _client()
    mb.get_release.return_value = _release("blue-rel", BLUE, "1994-05-10", rg_disamb="Blue Album")
    res = resolve_canonical_album_year(
        mb_client=mb, album_title="Weezer", artist_name="Weezer",
        musicbrainz_release_id="blue-rel", track_titles=["Buddy Holly"])
    assert res == ("1994", "1994-05-10", "blue-rel", BLUE)
    mb.get_release_group.assert_not_called()


def test_no_guess_when_the_tracks_cant_tell_which_album():
    # the old code reported the red album's 2008 here
    res = resolve_canonical_album_year(
        mb_client=_client(), album_title="Weezer", artist_name="Weezer",
        track_titles=["Some Unknown Song"])
    assert res is None


def test_a_title_with_one_album_needs_no_track_lookup():
    mb = MagicMock()
    mb.search_release.return_value = [_release("r1", "rg-jazz", "1978-11-10"),
                                       _release("r2", "rg-jazz", "1978-11-10")]
    res = resolve_canonical_album_year(
        mb_client=mb, album_title="Weezer", artist_name="Weezer", track_titles=["Mustapha"])
    assert res[0] == "1978"
    mb.search_recording.assert_not_called()


def test_same_titled_albums_in_one_library_get_their_own_answers():
    mb = _client()
    memo = {}
    blue = resolve_canonical_album_year(
        mb_client=mb, album_title="Weezer", artist_name="Weezer",
        track_titles=["Buddy Holly"], memo=memo)
    other = resolve_canonical_album_year(
        mb_client=mb, album_title="Weezer", artist_name="Weezer",
        track_titles=["Pork and Beans"], memo=memo)
    assert blue[0] == "1994" and other is None   # not the blue answer reused


def test_release_group_holding_tracks_breaks_a_tie_with_the_next_track():
    mb = MagicMock()
    mb.search_recording.side_effect = lambda title, artist, limit=25: {
        "A": [{"title": "A", "releases": [
            {"id": "1", "title": "Weezer", "release-group": {"id": BLUE}},
            {"id": "2", "title": "Weezer", "release-group": {"id": RED}}]}],
        "B": [{"title": "B", "releases": [
            {"id": "3", "title": "Weezer", "release-group": {"id": BLUE}}]}],
    }[title]
    assert release_group_holding_tracks(mb, "Weezer", "Weezer", ["A", "B"]) == (BLUE, "1")


def test_release_group_holding_tracks_survives_errors():
    mb = MagicMock()
    mb.search_recording.side_effect = RuntimeError("503")
    assert release_group_holding_tracks(mb, "Weezer", "Weezer", ["A"]) is None
    assert release_group_holding_tracks(mb, "Weezer", "Weezer", None) is None
