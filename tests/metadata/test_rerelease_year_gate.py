"""Re-release year gate (#re-releases-showing-as-owned).

Ownership matching is name-based, so owning the ORIGINAL album lit up every
re-release of it (different-year cards for the same title) as owned/partial —
and switching the primary metadata source reshuffled which cards lit up.

The gate: on discography surfaces (strict_discography_match=True) a candidate
whose year and the card's year are BOTH known and differ by more than 1 year
can never match. Every fallback keeps the original behavior byte-for-byte:
either year missing/unparseable → no gate; non-strict callers (download
matching, repair) → no gate; same-year edition variants (standard vs deluxe)
→ untouched, including the intentional completeness clamp.
"""

from __future__ import annotations

import sys
import types

import pytest

# The same lightweight stubs the sibling completion tests use — keep the module
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
from core.metadata import completion as metadata_completion  # noqa: E402


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


ARTIST = "Original Artist"


def _match(db, title, candidates, *, year=None, strict=True, tracks=None):
    album, confidence = db.check_album_exists_with_editions(
        title, ARTIST, confidence_threshold=0.7,
        expected_track_count=tracks, candidate_albums=candidates,
        strict_discography_match=strict, expected_year=year)
    return album


class TestYearsConflict:
    def test_far_apart_years_conflict(self):
        assert MusicDatabase._release_years_conflict("2024", 2005) is True

    def test_within_tolerance_do_not_conflict(self):
        assert MusicDatabase._release_years_conflict("2006", 2005) is False
        assert MusicDatabase._release_years_conflict("2005", 2005) is False

    def test_missing_or_garbage_never_conflicts(self):
        assert MusicDatabase._release_years_conflict(None, 2005) is False
        assert MusicDatabase._release_years_conflict("2024", None) is False
        assert MusicDatabase._release_years_conflict("", 2005) is False
        assert MusicDatabase._release_years_conflict("abcd", 2005) is False
        assert MusicDatabase._release_years_conflict("0", 2005) is False
        assert MusicDatabase._release_years_conflict("2024-05-01", 2024) is False  # date string tolerated


class TestMatcherYearGate:
    def test_rerelease_card_does_not_match_owned_original(self, db):
        # THE reported bug: own the 2005 original; the 2024 re-release card
        # must not read as owned.
        owned = [_album("Album X", 2005)]
        assert _match(db, "Album X", owned, year="2024") is None

    def test_original_card_still_matches_owned_original(self, db):
        owned = [_album("Album X", 2005)]
        assert _match(db, "Album X", owned, year="2005") is not None

    def test_adjacent_year_tolerated(self, db):
        # deluxe drops the following year — still the same release family
        owned = [_album("Album X", 2005)]
        assert _match(db, "Album X", owned, year="2006") is not None

    def test_unknown_card_year_falls_back_to_old_behavior(self, db):
        owned = [_album("Album X", 2005)]
        assert _match(db, "Album X", owned, year=None) is not None

    def test_unknown_local_year_falls_back_to_old_behavior(self, db):
        owned = [_album("Album X", None)]
        assert _match(db, "Album X", owned, year="2024") is not None

    def test_non_strict_caller_with_year_now_gates(self, db):
        # ROUND 3 FLIP (5BILLION): this used to pin the gate as strict-only,
        # which left the download analysis year-blind — a 2023 remaster
        # edition-matched the 1998 original and every track showed FOUND.
        # The gate now fires for ANY caller that supplies expected_year;
        # callers that pass no year (all other non-strict callers) are
        # byte-identical to before (see test_no_year_still_edition_matches...).
        owned = [_album("Album X", 2005)]
        assert _match(db, "Album X", owned, year="2024", strict=False) is None

    def test_same_year_deluxe_still_matches_standard_card(self, db):
        # the intentional standard-vs-deluxe behavior is preserved: same year,
        # bigger owned edition, card asks for 12 → still a match
        owned = [_album("Album X (Deluxe Edition)", 2005, track_count=15)]
        assert _match(db, "Album X", owned, year="2005", tracks=12) is not None

    def test_gate_picks_the_right_edition_from_mixed_candidates(self, db):
        # both the original and a re-release exist locally; the 2024 card must
        # match the 2024 local copy, not the 2005 one
        owned = [_album("Album X", 2005, album_id=1),
                 _album("Album X", 2024, album_id=2)]
        got = _match(db, "Album X", owned, year="2024")
        assert got is not None and got.id == 2


class _WiringDB:
    """Captures what completion passes to the matcher."""

    def __init__(self):
        self.kwargs = None

    def check_album_exists_with_completeness(self, **kwargs):
        self.kwargs = dict(kwargs)
        return None, 0.0, 0, 0, False, []


class TestCompletionWiring:
    def test_album_completion_passes_card_year(self):
        db = _WiringDB()
        metadata_completion.check_album_completion(
            db, {"id": "r1", "name": "Album X", "total_tracks": 12,
                 "year": "2024"}, ARTIST, source_chain=["spotify"])
        assert db.kwargs["expected_year"] == "2024"

    def test_album_completion_falls_back_to_release_date(self):
        db = _WiringDB()
        metadata_completion.check_album_completion(
            db, {"id": "r1", "name": "Album X", "total_tracks": 12,
                 "release_date": "2019-03-08"}, ARTIST, source_chain=["spotify"])
        assert db.kwargs["expected_year"] == "2019"

    def test_album_completion_passes_none_when_no_year(self):
        db = _WiringDB()
        metadata_completion.check_album_completion(
            db, {"id": "r1", "name": "Album X", "total_tracks": 12},
            ARTIST, source_chain=["spotify"])
        assert db.kwargs["expected_year"] is None

    def test_ep_completion_passes_card_year(self):
        db = _WiringDB()
        metadata_completion.check_single_completion(
            db, {"id": "r2", "name": "EP X", "total_tracks": 5,
                 "album_type": "ep", "year": "2021"}, ARTIST,
            source_chain=["spotify"])
        assert db.kwargs["expected_year"] == "2021"


# ── the caller must actually DELIVER the year to the gate (5BILLION round 2) ──
# The gate was correct; the library completion-stream rebuilt the album card
# into a stripped dict WITHOUT year, so expected_year=None and the gate never
# fired. These pin the year reaching the gate end to end.

class TestYearReachesTheGate:
    def test_check_album_completion_threads_the_card_year(self, db, monkeypatch):
        # owned original 2005; the card is the 2024 re-release of the same name
        db.check_album_exists_with_completeness  # ensure attr exists
        seen = {}
        real = db.check_album_exists_with_completeness

        def spy(*a, **kw):
            seen['expected_year'] = kw.get('expected_year')
            return real(*a, **kw)
        monkeypatch.setattr(db, 'check_album_exists_with_completeness', spy)

        metadata_completion.check_album_completion(
            db,
            {'id': 'x', 'name': 'The Album', 'total_tracks': 12, 'release_date': '2024-05-01'},
            ARTIST, source_chain=['itunes'], candidate_albums=[])
        # the card's year (from release_date) must arrive at the matcher
        assert seen['expected_year'] == '2024'

    def test_year_key_also_accepted(self, db, monkeypatch):
        seen = {}
        real = db.check_album_exists_with_completeness
        monkeypatch.setattr(db, 'check_album_exists_with_completeness',
                            lambda *a, **kw: seen.setdefault('y', kw.get('expected_year')) or real(*a, **kw))
        metadata_completion.check_album_completion(
            db, {'id': 'x', 'name': 'A', 'total_tracks': 1, 'year': 2019},
            ARTIST, source_chain=['itunes'], candidate_albums=[])
        assert seen['y'] == '2019'


def test_library_completion_stream_maps_the_year_through():
    """The library completion-stream endpoint rebuilds each card into a
    'mapped' dict — it must include year/release_date, or the gate is starved
    (the artist-detail path passes the card through untouched and was fine)."""
    import re
    from pathlib import Path
    ws = (Path(__file__).resolve().parents[2] / "web_server.py").read_text(
        encoding="utf-8", errors="replace")
    # isolate the mapped-dict literal in library_completion_stream
    block = ws.split("def library_completion_stream")[1].split("mapped = {")[1].split("}")[0]
    assert "'year'" in block, "library completion mapped dict dropped the year"
    assert "release_date" in block


# ── 5BILLION round 3: the gate reaches the DOWNLOAD ANALYSIS ─────────────────
# The gate used to fire only under strict_discography_match, so 'Begin
# analysis' on a re-release edition-matched the original album and every track
# showed FOUND. Now expected_year gates regardless of strict mode (callers
# that don't pass a year are byte-identical to before), and the per-track
# fallback refuses hits that landed on the original of the analyzed re-release.


class TestNonStrictYearGate:
    def test_year_conflict_rejects_without_strict_mode(self):
        db = MusicDatabase.__new__(MusicDatabase)
        original = _album("Chaosphere", 1998)
        album = _match(db, "Chaosphere (25th Anniversary Remastered 2023 Edition)",
                       [original], year="2023", strict=False)
        assert album is None                     # the analysis path is no longer year-blind

    def test_no_year_still_edition_matches_without_strict(self):
        db = MusicDatabase.__new__(MusicDatabase)
        original = _album("Chaosphere", 1998)
        album = _match(db, "Chaosphere (Deluxe Edition)", [original],
                       year=None, strict=False)
        assert album is not None                 # year-less callers unchanged


class TestFallbackRereleaseGuard:
    def _db_with_original(self, tmp_path):
        d = MusicDatabase(str(tmp_path / "m.db"))
        conn = d._get_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO artists (id, name) VALUES ('AR1','Meshuggah')")
        cur.execute("INSERT INTO albums (id, artist_id, title, year) VALUES ('AL1','AR1','Chaosphere',1998)")
        cur.execute("INSERT INTO albums (id, artist_id, title, year) VALUES ('AL2','AR1','Rare Trax',2001)")
        cur.execute("INSERT INTO tracks (id, album_id, artist_id, title) VALUES ('T1','AL1','AR1','Concatenation')")
        cur.execute("INSERT INTO tracks (id, album_id, artist_id, title) VALUES ('T2','AL2','AR1','Vanished')")
        conn.commit(); conn.close()
        return d

    def test_hit_on_original_of_rerelease_is_refused(self, tmp_path):
        from core.downloads.master import owned_hit_is_rerelease_original
        d = self._db_with_original(tmp_path)
        track = d.check_track_exists("Concatenation", "Meshuggah", confidence_threshold=0.7)[0]
        assert track is not None
        assert owned_hit_is_rerelease_original(
            d, "Chaosphere (25th Anniversary Remastered 2023 Edition)", "2023", track) is True

    def test_hit_on_unrelated_album_still_counts_as_owned(self, tmp_path):
        from core.downloads.master import owned_hit_is_rerelease_original
        d = self._db_with_original(tmp_path)
        track = d.check_track_exists("Vanished", "Meshuggah", confidence_threshold=0.7)[0]
        assert track is not None
        # 'Rare Trax' is not the same release family as the requested album
        assert owned_hit_is_rerelease_original(
            d, "Chaosphere (25th Anniversary Remastered 2023 Edition)", "2023", track) is False

    def test_missing_year_fails_open(self, tmp_path):
        from core.downloads.master import owned_hit_is_rerelease_original
        d = self._db_with_original(tmp_path)
        track = d.check_track_exists("Concatenation", "Meshuggah", confidence_threshold=0.7)[0]
        assert owned_hit_is_rerelease_original(
            d, "Chaosphere (25th Anniversary Remastered 2023 Edition)", None, track) is False
        assert owned_hit_is_rerelease_original(d, "", "2023", track) is False
        assert owned_hit_is_rerelease_original(d, "X", "2023", None) is False
