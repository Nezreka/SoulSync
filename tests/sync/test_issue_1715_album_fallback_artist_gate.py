"""Tests for SoulSync issue #1715 — playlist sync put the wrong song on
the playlist.

Report shape: a Spotify playlist track matched a PRE-EXISTING library
track with exactly the same title on an album with exactly the same
name — a completely different song by a completely different artist,
with no credit linking it to the searched artist. Playlist sync
counted it matched, wrote the wrong song to the server playlist, and
never wishlisted/downloaded the real track.

Root cause: the album-aware fallback in ``check_track_exists`` scored
rows by title similarity ALONE (``best_confidence = title_sim``). Every
other path goes through ``_calculate_track_confidence``, which weighs
the artist and collapses the score when the artist doesn't match; the
fallback bypassed it entirely, so title+album was the whole evidence.

Contract pinned here:
  * same title + same album name, different artist, no per-track credit
    -> NO match (the wrong-song case)
  * same album, row credited (``track_artist``) to the searched artist
    -> match (the compilation/guest shape the fallback exists for, #825)
  * album filed under the searched artist themselves -> match
  * Various Artists album whose row is credited to a DIFFERENT artist
    -> NO match (the credit is positive conflicting evidence)
  * Various Artists album with NO per-track credit at all -> title+album
    still matches (no artist evidence exists to weigh; that shape is
    pinned by tests/test_track_artist_matching.py)
  * a credited row outranks a no-evidence row across same-named albums,
    even when the no-evidence row's pure title score is higher
  * a joint album artist ('Simon & Garfunkel') evidences each partner
  * artist evidence is exact-key only: an edit-distance-1 namesake
    ('John William' vs 'John Williams') does NOT match
  * bracketed placeholder spellings ('[Unknown]', '[No Artist]') count
    as placeholders in either the album artist or the credit field
"""

from __future__ import annotations

from database.music_database import MusicDatabase


def _make_db(tmp_path, artists, albums, tracks):
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    c = conn.cursor()
    for r in artists:
        c.execute("INSERT INTO artists (id, name, server_source) VALUES (?,?,?)", r)
    for r in albums:
        c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?,?,?,?)", r)
    for r in tracks:
        c.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_artist, server_source)"
            " VALUES (?,?,?,?,?,?)", r)
    conn.commit()
    conn.close()
    db.ensure_norm_backfilled()
    return db


def _check(db, title, artist, **kwargs):
    kwargs.setdefault('confidence_threshold', 0.7)
    kwargs.setdefault('server_source', 'navidrome')
    kwargs.setdefault('candidate_tracks', [])
    return db.check_track_exists(title, artist, **kwargs)


class TestAlbumFallbackArtistGate:
    def test_same_title_same_album_different_artist_is_no_match(self, tmp_path):
        """The #1715 shape: Taylor Swift's 'Blue Sky' from her 'Greatest
        Hits' must NOT match Metallica's 'Blue Sky' on Metallica's
        'Greatest Hits' just because title and album name coincide.
        Swift owns nothing in this library."""
        db = _make_db(
            tmp_path,
            artists=[('a-met', 'Metallica', 'navidrome')],
            albums=[('al-gh', 'a-met', 'Greatest Hits', 'navidrome')],
            tracks=[('t-met', 'al-gh', 'a-met', 'Blue Sky', None, 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Greatest Hits')
        assert track is None
        assert conf < 0.7

    def test_compilation_credit_still_matches(self, tmp_path):
        """The fallback's intended shape (#825): the searched artist's
        song filed on a Various Artists compilation under a per-track
        credit still matches via the album fallback."""
        db = _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', 'Blue Sky', 'Taylor Swift', 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Party Mix')
        assert track is not None
        assert track.id == 't-va'
        assert conf >= 0.7

    def test_compilation_credit_to_another_artist_is_no_match(self, tmp_path):
        """A Various Artists album is not a blank cheque: when the row
        DOES carry a per-track credit and it names a different artist,
        that is positive conflicting evidence — no match."""
        db = _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', 'Blue Sky', 'Metallica', 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Party Mix')
        assert track is None
        assert conf < 0.7

    def test_similar_named_distinct_artist_is_no_match(self, tmp_path):
        """Fuzzy artist similarity is not evidence in the fallback: the
        weighted scorer alone would carry Taylor Smith's track over the
        threshold for a Taylor Swift search (artist sim 0.83); the
        fallback demands an exact variation key or >= 0.9."""
        db = _make_db(
            tmp_path,
            artists=[('a-tsm', 'Taylor Smith', 'navidrome')],
            albums=[('al-gh', 'a-tsm', 'Greatest Hits', 'navidrome')],
            tracks=[('t-tsm', 'al-gh', 'a-tsm', 'Blue Sky', None, 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Greatest Hits')
        assert track is None
        assert conf < 0.7

    def test_later_same_named_album_holds_the_exact_match(self, tmp_path):
        """All same-named album candidates are scored: the exact credited
        match on the second 'Greatest Hits' must win over the
        similar-named artist's own album, whichever order search
        returns them in."""
        db = _make_db(
            tmp_path,
            artists=[('a-tsm', 'Taylor Smith', 'navidrome'),
                     ('a-met', 'Metallica', 'navidrome')],
            albums=[('al-s', 'a-tsm', 'Greatest Hits', 'navidrome'),
                    ('al-m', 'a-met', 'Greatest Hits', 'navidrome')],
            tracks=[('t-smith', 'al-s', 'a-tsm', 'Blue Sky', None, 'navidrome'),
                    ('t-swift', 'al-m', 'a-met', 'Blue Sky', 'Taylor Swift', 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Greatest Hits')
        assert track is not None
        assert track.id == 't-swift'
        assert conf >= 0.7

    def test_unknown_artist_filing_matches_on_exact_title(self, tmp_path):
        """'Unknown Artist' is a placeholder, not a conflicting artist:
        a track filed under it with no credit carries no artist evidence,
        so an exact title on the hinted album still matches."""
        db = _make_db(
            tmp_path,
            artists=[('a-un', 'Unknown Artist', 'navidrome')],
            albums=[('al-u', 'a-un', 'Oldies', 'navidrome')],
            tracks=[('t-u', 'al-u', 'a-un', 'Believe', None, 'navidrome')],
        )
        track, conf = _check(db, 'Believe', 'Cher', album='Oldies')
        assert track is not None
        assert track.id == 't-u'
        assert conf >= 0.7

    def test_credited_row_outranks_no_evidence_row(self, tmp_path):
        """Ranking across branches: an exact-title VA row with NO artist
        evidence (pure title score 1.0) must not beat a row credited to
        the searched artist whose title is merely very good ('Blue
        Skies', weighted 0.889) — the credited row is the one whose
        artist agrees."""
        db = _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome'),
                     ('a-met', 'Metallica', 'navidrome')],
            albums=[('al-va', 'a-va', 'Greatest Hits', 'navidrome'),
                    ('al-met', 'a-met', 'Greatest Hits', 'navidrome')],
            tracks=[('t-va', 'al-va', 'a-va', 'Blue Sky', None, 'navidrome'),
                    ('t-cred', 'al-met', 'a-met', 'Blue Skies', 'Taylor Swift', 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Greatest Hits')
        assert track is not None
        assert track.id == 't-cred'

    def test_joint_album_artist_evidences_each_partner(self, tmp_path):
        """A joint filing ('Simon & Garfunkel', 'Tony Bennett & Lady
        Gaga') evidences EACH partner — searching either one must find
        the album's tracks through the fallback."""
        db = _make_db(
            tmp_path,
            artists=[('a-sg', 'Simon & Garfunkel', 'navidrome')],
            albums=[('al-s', 'a-sg', 'Greatest Hits', 'navidrome')],
            tracks=[('t-sg', 'al-s', 'a-sg', 'Mrs. Robinson', None, 'navidrome')],
        )
        for artist in ('Garfunkel', 'Simon'):
            track, conf = _check(db, 'Mrs. Robinson', artist, album='Greatest Hits')
            assert track is not None, f"{artist} must key-match the joint filing"
            assert track.id == 't-sg'

    def test_edit_distance_one_artist_is_no_match(self, tmp_path):
        """Exact-key evidence only: 'John William' filed, 'John Williams'
        searched (similarity 0.98) is a DIFFERENT artist — no match.
        Fuzzy similarity is not identity."""
        db = _make_db(
            tmp_path,
            artists=[('a-jw', 'John William', 'navidrome')],
            albums=[('al-j', 'a-jw', 'Greatest Hits', 'navidrome')],
            tracks=[('t-jw', 'al-j', 'a-jw', 'Blue Sky', None, 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'John Williams', album='Greatest Hits')
        assert track is None
        assert conf < 0.7

    def test_bracketed_placeholder_spellings(self, tmp_path):
        """MusicBrainz/Picard placeholder spellings ('[Unknown]',
        '[No Artist]', ...) are placeholders in either field, not
        conflicting artist evidence."""
        db = _make_db(
            tmp_path,
            artists=[('a-u', '[Unknown]', 'navidrome')],
            albums=[('al-u', 'a-u', 'Oldies', 'navidrome')],
            tracks=[('t-u', 'al-u', 'a-u', 'Believe', None, 'navidrome')],
        )
        track, conf = _check(db, 'Believe', 'Cher', album='Oldies')
        assert track is not None
        assert track.id == 't-u'

        # The same placeholder spelling as a per-track CREDIT on a VA
        # album must not defeat the no-evidence shape either.
        sub = tmp_path / "credit"
        sub.mkdir()
        db2 = _make_db(
            sub,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-m', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-m', 'al-m', 'a-va', 'Believe', '[No Artist]', 'navidrome')],
        )
        track, conf = _check(db2, 'Believe', 'Cher', album='Party Mix')
        assert track is not None
        assert track.id == 't-m'

    def test_batched_pool_and_exhaustive_callers_get_the_gate(self, tmp_path):
        """The gate lives in the shared fallback, so the sync service's
        batched-pool convention and discography's exhaustive convention
        are protected too — not just the legacy path."""
        db = _make_db(
            tmp_path,
            artists=[('a-met', 'Metallica', 'navidrome'),
                     ('a-dra', 'Drake', 'navidrome')],
            albums=[('al-gh', 'a-met', 'Greatest Hits', 'navidrome'),
                    ('al-d', 'a-dra', 'For All The Dogs', 'navidrome')],
            tracks=[('t-met', 'al-gh', 'a-met', 'Blue Sky', None, 'navidrome'),
                    ('t-dra', 'al-d', 'a-dra', 'First Person Shooter', None, 'navidrome')],
        )
        pool = db.get_artist_tracks_indexed('Drake', server_source='navidrome')
        assert pool
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Greatest Hits',
                             candidate_tracks=pool)
        assert track is None
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Greatest Hits',
                             candidate_tracks=[], candidate_tracks_exhaustive=True)
        assert track is None


    def test_album_artist_themselves_still_matches(self, tmp_path):
        """Metallica's own track on their own 'Greatest Hits' still
        matches Metallica — the album artist is artist evidence."""
        db = _make_db(
            tmp_path,
            artists=[('a-met', 'Metallica', 'navidrome')],
            albums=[('al-gh', 'a-met', 'Greatest Hits', 'navidrome')],
            tracks=[('t-met', 'al-gh', 'a-met', 'Blue Sky', None, 'navidrome')],
        )
        track, conf = _check(db, 'Blue Sky', 'Metallica', album='Greatest Hits')
        assert track is not None
        assert track.id == 't-met'
        assert conf >= 0.7


class TestNoEvidenceTitleBar:
    """The no-artist-evidence shape (placeholder album artist, no real
    credit) keeps title+album matching, but only for a guarded,
    near-identical title — a raw char ratio waved different songs
    through ('Believe' vs 'Believer' at 0.933)."""

    def _va_db(self, tmp_path, title, credit=None):
        return _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', title, credit, 'navidrome')],
        )

    def test_near_title_on_va_album_is_no_match(self, tmp_path):
        db = self._va_db(tmp_path, 'Believer')
        track, conf = _check(db, 'Believe', 'Cher', album='Party Mix')
        assert track is None
        assert conf < 0.7

    def test_longer_title_on_va_album_is_no_match(self, tmp_path):
        db = self._va_db(tmp_path, 'Believe In Me')
        track, conf = _check(db, 'Believe', 'Cher', album='Party Mix')
        assert track is None
        assert conf < 0.7

    def test_exact_title_on_va_album_still_matches(self, tmp_path):
        db = self._va_db(tmp_path, 'Believe')
        track, conf = _check(db, 'Believe', 'Cher', album='Party Mix')
        assert track is not None
        assert track.id == 't-va'
        assert conf >= 0.95

    def test_placeholder_credit_does_not_defeat_va_shape(self, tmp_path):
        """A credit that is itself the placeholder ('Various Artists',
        or blank) is not artist evidence — the VA shape still applies."""
        for i, credit in enumerate(('Various Artists', ' ')):
            sub = tmp_path / f"c{i}"
            sub.mkdir()
            db = self._va_db(sub, 'Hooked on a Feeling', credit=credit)
            track, conf = _check(db, 'Hooked on a Feeling', 'Blue Swede', album='Party Mix')
            assert track is not None, f"credit {credit!r} must count as no credit"
            assert track.id == 't-va'

    def test_caller_threshold_above_floor_is_honored(self, tmp_path):
        """The fallback's return gate is max(0.7, caller threshold): a
        0.8 caller must not be handed a sub-0.8 fallback match."""
        db = self._va_db(tmp_path, 'Blue Skies')
        track, conf = _check(db, 'Blue Sky', 'Taylor Swift', album='Party Mix',
                             confidence_threshold=0.8)
        assert track is None
