"""a video's own chapter marks reach the player with its tracks"""

from core.player import tracks as K


def _ch(start, end, title=None):
    c = {"start_time": str(start), "end_time": str(end)}
    if title is not None:
        c["tags"] = {"TITLE": title}
    return c


def test_chapters_come_out_in_order_with_names():
    data = {"chapters": [_ch(90.5, 1200, "Episode"), _ch(0, 90.5, "Intro"), _ch(1200, 1290.0)]}
    assert K.parse_chapters(data) == [
        {"start": 0.0, "end": 90.5, "title": "Intro"},
        {"start": 90.5, "end": 1200.0, "title": "Episode"},
        {"start": 1200.0, "end": 1290.0, "title": "Chapter 3"},
    ]


def test_one_chapter_or_none_is_no_chapters():
    assert K.parse_chapters({}) == []
    assert K.parse_chapters({"chapters": [_ch(0, 3000, "Movie")]}) == []


def test_junk_chapters_are_left_out():
    data = {"chapters": [_ch(0, 60, "A"), {"start_time": "x", "end_time": "5"}, _ch(-4, 2), _ch(60, 60),
                         _ch(60, 120, "B"), {"end_time": "9"}]}
    assert [c["title"] for c in K.parse_chapters(data)] == ["A", "B"]


def test_chapters_ride_with_the_tracks_and_reach_a_client(monkeypatch):
    monkeypatch.setattr(K, "probe", lambda *_a: {"audio": [], "subtitles": [],
                                                 "chapters": [{"start": 0.0, "end": 60.0, "title": "Intro"},
                                                              {"start": 60.0, "end": 90.0, "title": "Show"}]})
    got = K.public(K.all_tracks("ffprobe", "x.mkv", ("k",), None))
    assert [c["title"] for c in got["chapters"]] == ["Intro", "Show"]
    # a probe kept from before chapters were read still answers
    monkeypatch.setattr(K, "probe", lambda *_a: {"audio": [], "subtitles": []})
    assert K.public(K.all_tracks("ffprobe", "x.mkv", ("k",), None))["chapters"] == []
