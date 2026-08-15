"""A title-only Soulseek query must be distinctive enough to be worth broadcasting (#1102).

Soulseek search is a broadcast: slskd sends the query to the network and every
peer holding a match opens a connection back. A query like "alex" matches on
thousands of peers at once, which exhausts the NAT connection-tracking table on
a consumer router — taking the user's WHOLE internet connection down, not just
SoulSync's. Sonoid reported exactly this, naming "SISTERS", "alex" and "luca";
slskd#1598 is the same root cause, closed there with "search more specifically",
which SoulSync's users cannot act on because SoulSync writes the queries.

The title-only fallback still exists — a distinctive title earns its broadcast.
Only the short ones, where the query returns noise the matcher discards anyway,
are withheld. Every artist-qualified query is untouched in both cases.
"""

from __future__ import annotations

import pytest

from core.matching_engine import MusicMatchingEngine


@pytest.fixture()
def engine():
    return MusicMatchingEngine()


# ── the pure predicate ───────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "alex",            # Sonoid's report, verbatim
    "luca",
    "SISTERS",
    "Kid A",           # two words but tiny — still noise on its own
    "Go",
    "1",
    "!",
    "   ",
    "",
])
def test_a_short_title_is_not_broadcast_alone(engine, title):
    assert engine._title_is_distinctive_enough_to_broadcast(title) is False


@pytest.mark.parametrize("title", [
    "Californication",           # one word, but long enough to be specific
    "Bohemian Rhapsody",
    "Everything In Its Right Place",
    "Smells Like Teen Spirit",
    "A Day In The Life",
    "Paranoid Android",
])
def test_a_distinctive_title_still_earns_its_broadcast(engine, title):
    assert engine._title_is_distinctive_enough_to_broadcast(title) is True


def test_whitespace_does_not_inflate_the_word_count(engine):
    """"  Go  " must not read as distinctive because of padding."""
    assert engine._title_is_distinctive_enough_to_broadcast("  Go  ") is False


def test_none_is_handled(engine):
    assert engine._title_is_distinctive_enough_to_broadcast(None) is False


# ── end to end through the query builder ─────────────────────────────────────

class _Track:
    """The attributes generate_download_queries actually reads."""

    def __init__(self, name, artists, album=None):
        self.name = name
        self.artists = artists
        self.album = album


def _queries(engine, name, artists, album=None):
    return engine.generate_download_queries(_Track(name, artists, album))


def test_a_short_title_produces_no_bare_query(engine):
    """The regression. Before this, a track called "alex" sent a bare "alex"
    to the whole network."""
    queries = _queries(engine, "alex", ["Boards of Canada"])

    assert queries, "the artist-qualified queries must still be generated"
    for q in queries:
        assert q.strip().lower() != "alex", (
            f"a bare title-only query for a 4-character title was still sent: {queries}")


def test_the_artist_qualified_queries_are_untouched(engine):
    """Only the unqualified broadcast is withheld — everything that narrows by
    artist still runs, so match rate for the normal path is unchanged."""
    queries = _queries(engine, "alex", ["Boards of Canada"])

    assert any("boards of canada" in q.lower() for q in queries), (
        f"the artist-qualified query disappeared too: {queries}")


def test_a_distinctive_title_still_gets_its_title_only_query(engine):
    queries = _queries(engine, "Californication", ["Red Hot Chili Peppers"])

    assert any(q.strip().lower() == "californication" for q in queries), (
        f"the title-only fallback was lost for a distinctive title: {queries}")


def test_a_track_with_no_artist_still_gets_a_query(engine):
    """The no-artist branch returns title-only as the ONLY query. Suppressing it
    would mean not searching at all, which is a worse outcome than a broad
    search — so that path is deliberately left alone."""
    queries = _queries(engine, "alex", [])

    assert queries, "a track with no artist must still produce something to search"
