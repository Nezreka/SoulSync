"""Deezer-scoped reissue year-gate exemption (#1289).

Deezer's API `release_date` is the digital reissue date, not the original
release date:

- Yellowcard "One for the Kids": API says 2006-05-31, Deezer's site says 2001
- Yellowcard "Where We Stand":   API says 2004-03-30, Deezer's site says 1999

The re-release year gate assumed "year mismatch = different release" and
vetoed the library match whenever the years differed by more than 1, so
owned albums filed under their real years showed as Missing on the
discography page.

New rule (Deezer cards only): with exactly ONE exact normalized-title
candidate, the year no longer vetoes the match — it cannot be
disambiguating anything — UNLESS the candidate carries a stored Deezer id
that conflicts with the card's (hard proof of different releases, e.g.
owned standard vs deluxe card). Other metadata sources (spotify,
musicbrainz, ...) have trustworthy original release years, so their gate —
and any unknown/None source — behaves exactly as before. With >=2
same-title candidates the veto stays even for Deezer (that is the true
re-release ambiguity the gate was built for). The displayed card year is
intentionally unchanged by this fix — only the owned/missing decision.
"""

from __future__ import annotations

import sys
import types

import pytest

# Same lightweight stubs the sibling year-gate tests use — keep the module
# import from dragging in spotipy / live config.
if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = type("S", (), {"__init__": lambda self, *a, **k: None})
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = type("O", (), {"__init__": lambda self, *a, **k: None})
    oauth2.SpotifyClientCredentials = oauth2.SpotifyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

from database.music_database import MusicDatabase, DatabaseAlbum  # noqa: E402
from core.metadata.completion import check_album_completion  # noqa: E402


ARTIST = "Yellowcard"


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _album(title, year, track_count=12, album_id=1):
    a = DatabaseAlbum(id=album_id, artist_id=1, title=title, year=year,
                      track_count=track_count)
    # real candidates (search_albums / get_candidate_albums_for_artist) carry
    # the joined artist name; the matcher reads it
    a.artist_name = ARTIST
    return a


def _match(db, title, candidates, *, year, tracks=None, metadata_source=None):
    album, confidence = db.check_album_exists_with_editions(
        title, ARTIST, confidence_threshold=0.7,
        expected_track_count=tracks, candidate_albums=candidates,
        strict_discography_match=True, expected_year=year,
        metadata_source=metadata_source)
    return album


class TestDeezerReissueDates:
    def test_one_for_the_kids_single_candidate_accepted(self, db):
        # Deezer card says 2006 (reissue date); the owned album is filed
        # under 2001. Single exact-title candidate + deezer source -> year
        # must not veto.
        owned = [_album("One for the Kids", 2001)]
        got = _match(db, "One for the Kids", owned, year="2006",
                     metadata_source="deezer")
        assert got is not None and got.id == 1

    def test_where_we_stand_single_candidate_accepted(self, db):
        # Deezer card says 2004 (reissue date); the owned album is 1999.
        owned = [_album("Where We Stand", 1999)]
        got = _match(db, "Where We Stand", owned, year="2004",
                     metadata_source="deezer")
        assert got is not None and got.id == 1

    def test_year_agreement_still_matches(self, db):
        # Sanity: the pre-existing happy path is untouched.
        owned = [_album("One for the Kids", 2001)]
        got = _match(db, "One for the Kids", owned, year="2001",
                     metadata_source="deezer")
        assert got is not None and got.id == 1


class TestNonDeezerSourcesKeepTheGate:
    def test_spotify_card_still_vetoed(self, db):
        # Spotify's release years are trustworthy originals: the same
        # scenario must still veto.
        owned = [_album("One for the Kids", 2001)]
        got = _match(db, "One for the Kids", owned, year="2006",
                     metadata_source="spotify")
        assert got is None

    def test_unknown_source_still_vetoed(self, db):
        # None / unknown source -> historical behavior byte-for-byte.
        owned = [_album("One for the Kids", 2001)]
        assert _match(db, "One for the Kids", owned, year="2006",
                      metadata_source=None) is None
        assert _match(db, "One for the Kids", owned, year="2006",
                      metadata_source="musicbrainz") is None


class TestTrueAmbiguityKeepsTheVeto:
    def test_two_same_title_candidates_year_veto_still_fires(self, db):
        # Genuinely two same-titled local releases: the year gate must still
        # disambiguate — the 2006 card matches the 2006 copy, not 2001.
        # (No metadata_source: historical path.)
        owned = [_album("One for the Kids", 2001, album_id=1),
                 _album("One for the Kids", 2006, album_id=2)]
        got = _match(db, "One for the Kids", owned, year="2006")
        assert got is not None and got.id == 2

    def test_two_same_title_candidates_deezer_still_vetoes(self, db):
        # The Deezer exemption does NOT apply when there is genuine
        # ambiguity: two same-titled candidates -> the year still decides.
        owned = [_album("One for the Kids", 2001, album_id=1),
                 _album("One for the Kids", 2006, album_id=2)]
        got = _match(db, "One for the Kids", owned, year="2006",
                     metadata_source="deezer")
        assert got is not None and got.id == 2

    def test_two_same_title_candidates_card_matches_older(self, db):
        owned = [_album("One for the Kids", 2001, album_id=1),
                 _album("One for the Kids", 2006, album_id=2)]
        got = _match(db, "One for the Kids", owned, year="2001")
        assert got is not None and got.id == 1

    def test_edition_variant_does_not_count_as_exact_title(self, db):
        # "One for the Kids (Deluxe Edition)" is a different normalized
        # title, so it does not create ambiguity: the plain-titled album is
        # still the single exact-title candidate and the year is skipped
        # for a deezer card.
        owned = [_album("One for the Kids", 2001, album_id=1),
                 _album("One for the Kids (Deluxe Edition)", 2006, album_id=2)]
        got = _match(db, "One for the Kids", owned, year="2006",
                     metadata_source="deezer")
        assert got is not None and got.id == 1


class TestNoFalsePositives:
    def test_different_title_still_rejected(self, db):
        owned = [_album("Totally Different Album", 2006)]
        assert _match(db, "One for the Kids", owned, year="2006") is None

    def test_case_insensitive_exact_title(self, db):
        # Normalization is case/diacritics only: differing case still counts
        # as the single exact-title candidate (deezer card).
        owned = [_album("one for the kids", 2001)]
        got = _match(db, "One for the Kids", owned, year="2006",
                     metadata_source="deezer")
        assert got is not None and got.id == 1

    def test_no_year_behaves_as_before(self, db):
        # Callers that pass no year never engaged the gate; unchanged.
        owned = [_album("One for the Kids", 2001)]
        got = _match(db, "One for the Kids", owned, year=None)
        assert got is not None and got.id == 1


class TestMetadataSourceThreading:
    def test_typeerror_fallback_survives_new_kwarg(self):
        """check_album_completion's TypeError fallback must keep working now
        that the primary call passes metadata_source: a db with the legacy
        signature raises TypeError on the primary call, and the retry
        (without the new kwarg) proceeds cleanly to a normal result."""

        class _LegacyDB:
            """Mimics the pre-change db method: no metadata_source kwarg."""

            def __init__(self):
                self.attempts = []

            def check_album_exists_with_completeness(self, title, artist, **kw):
                self.attempts.append(set(kw))
                if "metadata_source" in kw:
                    raise TypeError(
                        "check_album_exists_with_completeness() got an "
                        "unexpected keyword argument 'metadata_source'")
                return (None, 0.0, 0, 0, False, [])

        legacy = _LegacyDB()
        card = {"id": "DZ-1", "name": "One for the Kids", "total_tracks": 12,
                "album_type": "album", "year": 2006}
        r = check_album_completion(legacy, card, "Yellowcard",
                                   source_override="deezer",
                                   candidate_albums=None)
        # Clean fallback: a normal 'missing' verdict, not an 'error' status,
        # and the retry dropped the unknown kwarg.
        assert r["status"] == "missing"
        assert len(legacy.attempts) == 2
        assert "metadata_source" in legacy.attempts[0]
        assert "metadata_source" not in legacy.attempts[1]
