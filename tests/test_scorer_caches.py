"""the pool scorer's caches and reordering (PERF_REVIEW item 3).

_calculate_track_confidence runs once per candidate in a pool, so a 100-track
playlist by an artist with 1,500 library tracks is 150,000 calls. each one
used to re-normalize the constant search side, rebuild and re-escape ~20
version-keyword regexes twice, and score the artist before it knew whether
the title had cleared its floor. now: cached normalizers, cached similarity
pairs, precompiled keyword patterns, and artist scoring only after the title
gate. 155 ms → 18 ms per 300-candidate check on the bench library; a
differential run of 8,000 scores against the previous code found no
difference. these pin the pieces that keep it equivalent.
"""

import inspect

import core.matching_engine as me_mod
from core.matching_engine import MusicMatchingEngine
from core.text.title_match import _content_tokens, titles_plausibly_same
from database.music_database import DatabaseTrack, MusicDatabase


def _track(title, artist, track_artist=None, album=""):
    t = DatabaseTrack(id=1, album_id=1, artist_id=1, title=title, track_number=1, duration=0, file_path="", bitrate=0)
    t.artist_name = artist
    t.track_artist = track_artist
    t.album_title = album
    return t


def test_similarity_score_values_are_the_documented_ones():
    me = MusicMatchingEngine()
    assert me.similarity_score("song", "song") == 1.0
    assert me.similarity_score("song", "song (remix)") == 0.30           # different version
    assert me.similarity_score("song", "song (2011 remaster)") == 0.75   # remaster is lenient
    assert me.similarity_score("song (shazam remix)", "song (southstar remix)") == 0.30  # divergent versions
    assert me.similarity_score("song (live)", "song - live") > 0.8      # same version, different wrapping: no divergent penalty
    assert me.similarity_score("", "song") == 0.0


def test_similarity_score_is_stable_across_calls_and_engines():
    a = MusicMatchingEngine().similarity_score("dani california", "californication")
    b = MusicMatchingEngine().similarity_score("dani california", "californication")
    assert a == b == me_mod._similarity_score_cached_pair("dani california", "californication")


def test_version_keyword_patterns_match_the_keyword_list():
    kws = [kw for kw, _rx in me_mod._VERSION_KEYWORD_PATTERNS]
    assert kws == me_mod._DIFFERENT_VERSION_KEYWORDS
    for kw, rx in me_mod._VERSION_KEYWORD_PATTERNS:
        assert rx.search(f"title ({kw})")
        assert not rx.search(f"title ({kw}er)")   # word-bounded, as re.search(r'\b..\b') was
    # the method still reads the same list (no second, drifting copy)
    src = inspect.getsource(MusicMatchingEngine._similarity_score_uncached)
    assert "different_version_keywords = _DIFFERENT_VERSION_KEYWORDS" in src


def test_content_tokens_are_cached_and_immutable():
    a = _content_tokens("Under The Bridge")
    assert a == frozenset({"under", "bridge"})
    assert _content_tokens("Under The Bridge") is a
    assert titles_plausibly_same("under the bridge", "around the world", 0.62) is False


def test_track_confidence_title_gate_returns_before_artist_work():
    """below the 0.6 title floor the result is title-only, so the artist is
    never consulted: a scorer given a track whose artist_name would raise if
    normalized still answers."""
    db = MusicDatabase.__new__(MusicDatabase)

    class Boom(str):
        def lower(self):
            raise AssertionError("artist scored before the title gate")

    t = _track("completely different words here", Boom("Artist"))
    conf = db._calculate_track_confidence("zzyzx", "Artist", t)
    assert conf < 0.3


def test_track_confidence_known_outcomes():
    db = MusicDatabase.__new__(MusicDatabase)
    assert db._calculate_track_confidence("Human Behaviour", "Bjork", _track("Human Behaviour", "Björk")) == 1.0
    # per-track credit carries a feat. artist
    assert db._calculate_track_confidence("We Found Love", "Rihanna", _track("We Found Love", "Calvin Harris", "Calvin Harris feat. Rihanna")) >= 0.9
    # same title, different artist: below threshold
    assert db._calculate_track_confidence("Champagne Supernova", "Blur", _track("Champagne Supernova", "Oasis")) < 0.7
    # a remix is not the original
    assert db._calculate_track_confidence("Song", "A", _track("Song (Remix)", "A")) < 0.7
    # #808: a qualifier that restates the album is context, not a version
    assert db._calculate_track_confidence(
        "Champagne Supernova (OurVinyl Sessions)", "Oasis",
        _track("Champagne Supernova", "Oasis", album="Live at OurVinyl Sessions")) >= 0.7
