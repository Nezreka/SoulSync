"""an apostrophe goes to soulseek as a space.

measured against slskd on Nov 25 2026, searching for Journey:
    "journey dont stop believin"    0 files   (what was being sent)
    "journey don't stop believin"   610
    "journey don t stop believin"   1,104
soulseek matches whole words, and a file named "Don't Stop Believin'" holds
the words "don" and "t", not "dont".
"""

from types import SimpleNamespace as NS

import pytest

from core.matching_engine import MusicMatchingEngine

engine = MusicMatchingEngine()


def _queries(name, artist, album=""):
    return engine.generate_download_queries(
        NS(id="x", name=name, artists=[artist], album=album, duration_ms=240000, popularity=0))


@pytest.mark.parametrize("name,artist,word,never", [
    ("Don't Stop Believin'", "Journey", "don t stop believin", "dont"),
    ("Sweet Child O' Mine", "Guns N' Roses", "guns n roses", "o mine sweetchild"),
    ("Can\u2019t Help Falling in Love", "Elvis Presley", "can t help", "cant"),
    ("Bills, Bills, Bills", "Destiny's Child", "destiny s child", "destinys"),
])
def test_the_queries_carry_the_word_split_at_its_apostrophe(name, artist, word, never):
    queries = [q.lower() for q in _queries(name, artist)]
    assert queries, "no queries at all"
    assert any(word in q for q in queries), queries
    main = queries[:3]
    assert not any(never in q.split() for q in main), main


def test_the_track_itself_is_left_as_it_was():
    track = NS(id="x", name="Don't Stop Believin'", artists=["Journey"], album="Escape",
               duration_ms=240000, popularity=0)
    engine.generate_download_queries(track)
    assert track.name == "Don't Stop Believin'" and track.artists == ["Journey"]


def test_a_title_with_no_apostrophe_is_searched_as_before():
    queries = _queries("Paranoid", "Black Sabbath", "Paranoid")
    assert any(q.lower() == "black sabbath paranoid" for q in queries), queries
