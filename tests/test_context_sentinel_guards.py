"""A download-context placeholder must never be stored as an artist id (#1284).

'from_sync_modal' and friends are context stand-ins the download path invents when
it cannot name an artist. One reached a watchlist row in August; the scanner keyed
25 similar artists by it, twice, and those rows seeded the discovery pool. The
recommendation fix stops them counting — these guards stop them being written.
"""

import pytest

from core.context_sentinels import CONTEXT_SENTINEL_IDS, is_context_sentinel
from database.music_database import MusicDatabase


@pytest.mark.parametrize("value", sorted(CONTEXT_SENTINEL_IDS) + ["", "   ", None, "FROM_SYNC_MODAL", " from_sync_modal "])
def test_placeholders_are_recognised(value):
    assert is_context_sentinel(value)


@pytest.mark.parametrize("value", ["4gzpq5DPGxSnKTe4SA8HAU", "465031", "Kylie Minogue", "0", "auto_importer"])
def test_real_ids_and_names_are_not_placeholders(value):
    # the artist map keys a cached row by NAME when the artist matched no
    # provider, so a name has to survive this check.
    assert not is_context_sentinel(value)


def test_watchlist_refuses_a_placeholder_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_artist_to_watchlist("from_sync_modal", "Britney Spears", profile_id=1) is False

    with db._get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM watchlist_artists").fetchone()[0]
    assert rows == 0


def test_watchlist_still_accepts_a_real_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_artist_to_watchlist("4gzpq5DPGxSnKTe4SA8HAU", "Kylie Minogue",
                                      profile_id=1, source="spotify") is True

    with db._get_connection() as conn:
        row = conn.execute("SELECT artist_name, spotify_artist_id FROM watchlist_artists").fetchone()
    assert tuple(row) == ("Kylie Minogue", "4gzpq5DPGxSnKTe4SA8HAU")


def test_similar_artists_refuse_a_placeholder_source(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_or_update_similar_artist(
        source_artist_id="from_sync_modal", similar_artist_name="Shakira",
        similar_artist_deezer_id="dz-1", profile_id=1) is False

    with db._get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM similar_artists").fetchone()[0]
    assert rows == 0


def test_similar_artists_still_accept_a_name_keyed_source(tmp_path):
    # the artist map's own fallback: no provider matched, so the cached edge is
    # keyed by the artist's name. that is not a placeholder.
    db = MusicDatabase(str(tmp_path / "music.db"))

    assert db.add_or_update_similar_artist(
        source_artist_id="Biking With Francis", similar_artist_name="Bren Joy",
        similar_artist_spotify_id="sp-1", profile_id=1) is True

    with db._get_connection() as conn:
        row = conn.execute("SELECT source_artist_id, similar_artist_name FROM similar_artists").fetchone()
    assert tuple(row) == ("Biking With Francis", "Bren Joy")
