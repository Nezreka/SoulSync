"""Query-construction tests for core/musicbrainz_client.py.

Regression guard for #754: the user-facing "Search for Match" / Fix popup
runs non-strict (strict=False) MusicBrainz searches. The old non-strict path
built a bare "track artist" blob with NO field scoping, so the artist was just
a free fuzzy term — covers/karaoke whose TITLES contained the artist name
outranked the real recording (e.g. "Sweet Child O Mine" / "Guns N Roses"
returned only covers, never the Guns N' Roses original).

The fix keeps the track/album side loose (diacritic + bracket recall) but
field-scopes the artist (artist:(...)) so it actually constrains. These tests
pin the query STRING the client sends — no network — by capturing the params
passed to session.get.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.musicbrainz_client import MusicBrainzClient, _escape_lucene


@pytest.fixture
def client():
    c = MusicBrainzClient("SoulSync", "2")
    # Replace the HTTP session with a mock returning an empty result set, so
    # we can inspect the query string without touching the network.
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"recordings": [], "releases": []})
    c.session = MagicMock()
    c.session.get = MagicMock(return_value=resp)
    return c


def _query_of(client) -> str:
    """The 'query' param of the last session.get call."""
    _, kwargs = client.session.get.call_args
    return kwargs["params"]["query"]


# ── recording: non-strict must field-scope the artist ──────────────────────

def test_recording_nonstrict_scopes_artist(client):
    client.search_recording("Sweet Child O Mine", artist_name="Guns N Roses", strict=False)
    q = _query_of(client)
    assert "artist:(Guns N Roses)" in q          # artist is a CONSTRAINT, not a loose term
    assert q.startswith("Sweet Child O Mine")    # track side stays loose (no phrase quotes)
    assert '"' not in q                          # no phrase-quoting that kills bracket/diacritic recall


def test_recording_nonstrict_without_artist_is_bare_track(client):
    client.search_recording("Hyperballad", strict=False)
    assert _query_of(client) == "Hyperballad"    # no artist → no AND clause


def test_recording_nonstrict_whitespace_artist_is_bare_track(client):
    # A whitespace-only artist must not produce a malformed artist:(   ) group.
    client.search_recording("Hyperballad", artist_name="   ", strict=False)
    assert _query_of(client) == "Hyperballad"


def test_recording_nonstrict_strips_artist_padding(client):
    client.search_recording("Money", artist_name="  Pink Floyd  ", strict=False)
    assert _query_of(client) == "Money AND artist:(Pink Floyd)"


def test_recording_strict_unchanged(client):
    client.search_recording("Say You Will", artist_name="Foreigner", strict=True)
    q = _query_of(client)
    assert q == 'recording:"Say You Will" AND artist:"Foreigner"'  # strict path untouched


def test_recording_nonstrict_escapes_lucene_specials_in_artist(client):
    # Artist names with parens/?/! must NOT break the artist:(...) group.
    # Without escaping, "Sunn O)))" closes the group early and returns
    # unrelated artists; "Anthony Green (Saosin)" returns nothing.
    client.search_recording("Hunting Season", artist_name="Sunn O)))", strict=False)
    q = _query_of(client)
    assert "artist:(Sunn O\\)\\)\\))" in q        # every ) escaped, group stays balanced
    # The clause is well-formed: opening "artist:(" is closed by exactly one
    # unescaped ")", so paren depth returns to zero.
    depth = 0
    after = q.split("artist:(", 1)[1]
    i = 0
    while i < len(after):
        ch = after[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break          # this is the closing paren of artist:(
            depth -= 1
        i += 1
    assert depth == 0


def test_escape_lucene_helper():
    assert _escape_lucene("Sunn O)))") == "Sunn O\\)\\)\\)"
    assert _escape_lucene("Therapy?") == "Therapy\\?"
    assert _escape_lucene("AC/DC") == "AC\\/DC"
    assert _escape_lucene("Foreigner") == "Foreigner"   # plain text untouched


# ── release: same fix, same guarantees ─────────────────────────────────────

def test_release_nonstrict_scopes_artist(client):
    client.search_release("Nevermind", artist_name="Nirvana", strict=False)
    q = _query_of(client)
    assert "artist:(Nirvana)" in q
    assert q.startswith("Nevermind")
    assert '"' not in q


def test_release_strict_unchanged(client):
    client.search_release("Nevermind", artist_name="Nirvana", strict=True)
    assert _query_of(client) == 'release:"Nevermind" AND artist:"Nirvana"'
