from types import SimpleNamespace

import pytest

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob


@pytest.fixture(autouse=True)
def _no_live_alias_lookup(monkeypatch):
    """Keep the alias chain off the network.

    `_resolve_expected_artist_aliases` builds a MusicBrainzService and then
    queries MusicBrainz over HTTP. These tests were reaching it for real: they
    patched the name on the SCANNER module, which stopped importing it when the
    scan was routed through the shared verifier, and the non-raising form of
    monkeypatch turned that into a silent no-op. 35 seconds a piece, and a
    different answer when rate-limited. Tests that are about the alias bridge
    override this with the list they mean.
    """
    monkeypatch.setattr(
        "core.acoustid_verification._resolve_expected_artist_aliases",
        lambda name: [])


class _FakeCursor:
    def __init__(self, rows, lib_rows=None):
        self._rows = rows
        self._lib_rows = lib_rows or []
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchall(self):
        # The #934 history-match SELECT gets its own (id, file_path, title, source)
        # rows; the tracks scan query gets the track rows.
        if self.executed and 'FROM library_history' in self.executed[-1][0]:
            return self._lib_rows
        return self._rows

    def fetchone(self):
        return None


class _FakeConnection:
    def __init__(self, rows, lib_rows=None):
        self._cursor = _FakeCursor(rows, lib_rows)

    def cursor(self):
        return self._cursor

    def execute(self, query, params=None):
        return self._cursor.execute(query, params)

    def commit(self):
        pass

    def close(self):
        pass


def _make_context(subjects, tmp_path):
    """A real Library-v2 catalogue holding ``subjects``.

    ``subjects`` is a list of ``(track_id, title, artist, path, track_number,
    album_title, duration_ms)``; a ``None`` track id means "a row the catalogue
    could not identify", which lib2 expresses by simply not having it — there is
    no id-less track row to skip past any more.
    """
    import pathlib
    import sqlite3

    from core.library2.schema import ensure_library_v2_schema

    db_path = tmp_path / "scan.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name, image_url) "
                 "VALUES (7, 'Artist', 'Artist', 'artist-thumb')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url) "
                 "VALUES (1, 7, 'Album', 'album-thumb')")
    for track_id, title, artist, path, number, album_title, duration in subjects:
        if track_id is None:
            continue
        # A native subject resolves through `resolve_lib2_path`, not the legacy
        # path resolver, so the file has to actually be there.
        pathlib.Path(path).write_bytes(b"audio")
        conn.execute(
            "INSERT INTO lib2_tracks (id, album_id, title, track_number, duration) "
            "VALUES (?, 1, ?, ?, ?)", (track_id, title, number, duration))
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
            "VALUES (?, ?, 'flac', 1)", (track_id, path))
    conn.commit()
    conn.close()

    class _RealDB:
        def _get_connection(self):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

    return SimpleNamespace(
        db=_RealDB(),
        transfer_folder="/music",
        config_manager=_EnabledConfig(),
        acoustid_client=object(),
        create_finding=None,
        report_progress=lambda **kwargs: None,
        update_progress=lambda *args, **kwargs: None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *args, **kwargs: False,
    )


def test_load_db_tracks_keys_every_subject_by_its_native_id(tmp_path):
    job = AcoustIDScannerJob()
    context = _make_context([
        (42, "Good Track", "Artist", str(tmp_path / "good.flac"), 2, "Album", 240000),
    ], tmp_path)

    tracks = job._load_db_tracks(context)

    assert list(tracks.keys()) == ["lib2:42"]
    assert tracks["lib2:42"]["title"] == "Good Track"
    assert tracks["lib2:42"]["artist"] == "Artist"
    assert tracks["lib2:42"]["duration_ms"] == 240000   # #587 duration guard


def test_scan_walks_the_native_subjects(tmp_path, monkeypatch):
    job = AcoustIDScannerJob()
    context = _make_context([
        (42, "Good Track", "Artist", str(tmp_path / "good.flac"), 2, "Album", 240000),
    ], tmp_path)

    monkeypatch.setattr(job, "_resolve_path", lambda file_path, _context: file_path)

    scanned_track_ids = []

    def fake_scan_file(fpath, track_id, expected, acoustid_client, context, result,
                       fp_threshold, title_threshold, artist_threshold):
        scanned_track_ids.append(track_id)

    monkeypatch.setattr(job, "_scan_file", fake_scan_file)

    result = job.scan(context)

    assert result.scanned == 1
    assert scanned_track_ids == ["lib2:42"]


def test_scan_respects_active_manual_acoustid_override(tmp_path, monkeypatch):
    job = AcoustIDScannerJob()
    context = _make_context([
        (42, "Good Track", "Artist", str(tmp_path / "good.flac"), 2, "Album", 240000),
    ], tmp_path)
    monkeypatch.setattr(job, "_resolve_path", lambda file_path, _context: file_path)
    monkeypatch.setattr(
        "core.library2.manual_skips.check_is_skipped",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        job,
        "_scan_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual override must skip fingerprinting")
        ),
    )

    result = job.scan(context)

    assert result.scanned == 0
    assert result.skipped == 1


# ---------------------------------------------------------------------------
# Multi-value artist credit — Foxxify Discord report
# ---------------------------------------------------------------------------
#
# AcoustID returns the FULL artist credit while the library DB
# carries only the primary artist. Pre-fix raw SequenceMatcher
# scored 43% — below the 0.6 threshold — and the scanner created a
# Wrong Song finding even though the audio was correct. Post-fix the
# scanner routes through `artist_names_match` which splits the credit
# and finds the primary artist at 100%, suppressing the false flag.


def _make_finding_capturing_context(track_row, captured, lib_rows=None):
    """Context that captures any create_finding calls into the
    `captured` list. Tests assert against this list to verify whether
    the scanner created a finding (false positive) or correctly
    skipped (multi-value match resolved). ``lib_rows`` seeds the
    library_history match SELECT (#934)."""
    conn = _FakeConnection([track_row], lib_rows)
    config_manager = SimpleNamespace(
        get=lambda key, default=None: default,
        set=lambda *args, **kwargs: None,
    )
    db = SimpleNamespace(_get_connection=lambda: conn)

    def fake_create_finding(**kwargs):
        captured.append(kwargs)
        return True

    return SimpleNamespace(
        db=db,
        transfer_folder="/music",
        config_manager=config_manager,
        acoustid_client=object(),
        create_finding=fake_create_finding,
        report_progress=lambda **kwargs: None,
        update_progress=lambda *args, **kwargs: None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *args, **kwargs: False,
    )


def test_scanner_no_finding_when_primary_artist_in_acoustid_credit():
    """Reporter's exact case verbatim:

        Library DB:   title='Tea Parties With Dale Earnhardt' artist='Okayracer'
        AcoustID:     title='Tea Parties With Dale Earnhardt'
                      artist='Okayracer, aldrch & poptropicaslutz!'
        Pre-fix:      artist_sim=43% → Wrong Song finding
        Post-fix:     'Okayracer' found in credit → 100% → no finding
    """
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("69241726", "Tea Parties With Dale Earnhardt", "Okayracer",
                   "/music/track.opus", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{
                'title': 'Tea Parties With Dale Earnhardt',
                'artist': 'Okayracer, aldrch & poptropicaslutz!',
            }],
        },
    )

    result = JobResultStub()
    job._scan_file(
        '/music/track.opus',
        '69241726',
        {'title': 'Tea Parties With Dale Earnhardt', 'artist': 'Okayracer'},
        fake_acoustid,
        context,
        result,
        fp_threshold=0.85,
        title_threshold=0.85,
        artist_threshold=0.6,
    )

    assert captured_findings == [], (
        f"Expected no finding (primary artist in credit); got {captured_findings}"
    )


def test_scanner_still_flags_genuine_artist_mismatch():
    """Sanity: multi-value path doesn't suppress legitimate
    mismatches. If expected artist is NOT in the credit at all,
    finding still fires."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("99", "Some Track", "Foreigner",
                   "/music/track.flac", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{
                'title': 'Some Track',
                # Clearly-different multi-value credit (artist sim < 0.30). The
                # unified core gives 0.30-0.60 ("ambiguous") the benefit of the
                # doubt, so a genuine-mismatch assertion needs an artist that's
                # unambiguously different.
                'artist': 'Metallica, Slayer & Anthrax',
            }],
        },
    )

    result = JobResultStub()
    job._scan_file(
        '/music/track.flac',
        '99',
        {'title': 'Some Track', 'artist': 'Foreigner'},
        fake_acoustid,
        context,
        result,
        fp_threshold=0.85,
        title_threshold=0.85,
        artist_threshold=0.6,
    )

    assert len(captured_findings) == 1, (
        f"Expected a finding for genuine mismatch; got {len(captured_findings)}"
    )
    assert captured_findings[0]['finding_type'] == 'acoustid_mismatch'


class JobResultStub:
    """Minimal JobResult-like stub for the scanner integration tests
    above. The real JobResult tracks scanned/skipped/findings_created
    counters via attribute assignment — same shape works here."""
    findings_created = 0
    findings_skipped_dedup = 0
    errors = 0
    scanned = 0
    skipped = 0


# ---------------------------------------------------------------------------
# Compilation albums — Skowl Discord report
# ---------------------------------------------------------------------------
#
# Compilation albums (e.g. "High Tea Music: Vol 1") have different
# artists per track but `tracks.artist_id` points at the ALBUM artist
# (curator / label name applied to every row). The scanner used to
# compare AcoustID's per-track artist against the album artist →
# 12% sim → Wrong Song flag on every track. The `tracks.track_artist`
# column already holds the correct per-track artist for these cases
# (populated by every server-scan + auto-import path) — scanner just
# wasn't reading it. Post-fix `_load_db_tracks` prefers track_artist
# via `COALESCE(NULLIF(t.track_artist, ''), ar.name)`.


def _make_real_db_context(tmp_path):
    """A real Library-v2 catalogue, because that is what the scan reads.

    This used to hand-roll ``artists``/``albums``/``tracks``. The per-track
    artist credit that made these tests interesting still exists, one level
    down: lib2 records it in ``lib2_track_artists`` and ``active_file_subjects``
    prefers it over the album's primary artist — the same COALESCE, moved.
    """
    import sqlite3

    from core.library2.schema import ensure_library_v2_schema

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
    conn.close()

    class _RealDB:
        def _get_connection(self):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

    return _RealDB()


class _EnabledConfig:
    """``features.library_v2`` off means the subject walk returns nothing."""

    def get(self, key, default=None):
        return True if key == "features.library_v2" else default

    def set(self, *args, **kwargs):
        pass


def test_load_db_tracks_prefers_track_artist_for_compilation(tmp_path):
    """Reporter's exact case (Skowl) — a compilation where every track credits a
    different artist while the album belongs to the curator."""
    db = _make_real_db_context(tmp_path)

    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, 'Andromedik', 'Andromedik')")
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (2, 'Eclypse', 'Eclypse')")
    conn.execute(
        "INSERT INTO lib2_albums (id, primary_artist_id, title) "
        "VALUES (1, 1, 'High Tea Music: Vol 1')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (10, 1, 'City Lights')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (11, 1, 'Invasion')")
    conn.execute(
        "INSERT INTO lib2_track_artists (track_id, artist_id, role, position) "
        "VALUES (10, 2, 'primary', 0)")
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
        "VALUES (10, '/music/citylights.mp3', 'mp3', 1)")
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
        "VALUES (11, '/music/invasion.mp3', 'mp3', 1)")
    conn.commit()
    conn.close()

    job = AcoustIDScannerJob()
    context = SimpleNamespace(db=db, config_manager=_EnabledConfig())
    tracks = job._load_db_tracks(context)

    # Credited on the track → Eclypse, not the album's Andromedik.
    assert tracks['lib2:10']['artist'] == 'Eclypse'
    # No track credit → the album artist, same fallback as before.
    assert tracks['lib2:11']['artist'] == 'Andromedik'


def test_load_db_tracks_falls_back_when_a_track_has_no_credit_row(tmp_path):
    """The empty-string ``track_artist`` this used to guard cannot occur: a
    credit is a row in ``lib2_track_artists``, so it is either there or not."""
    db = _make_real_db_context(tmp_path)

    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, 'Album Artist', 'Album Artist')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Album')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (1, 1, 'T1')")
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
        "VALUES (1, '/music/t1.mp3', 'mp3', 1)")
    conn.commit()
    conn.close()

    job = AcoustIDScannerJob()
    context = SimpleNamespace(db=db, config_manager=_EnabledConfig())
    tracks = job._load_db_tracks(context)

    assert tracks['lib2:1']['artist'] == 'Album Artist'


# ---------------------------------------------------------------------------
# File-tag fallback for legacy compilation tracks — Skowl Discord follow-up
# ---------------------------------------------------------------------------
#
# Skowl reported that the AcoustID Scanner was STILL flagging his
# compilation tracks even after the COALESCE(track_artist, album_artist)
# fix shipped. Cause: his tracks were downloaded BEFORE the
# `tracks.track_artist` column existed, so for those rows
# `track_artist IS NULL` and COALESCE falls back to the ALBUM artist
# (the curator) — same wrong-comparison the prior fix was supposed to
# eliminate.
#
# The audio file's ARTIST tag is ground truth for what's on disk:
# Tidal/Spotify/Deezer all write the per-track artist into the file's
# tag at download time, regardless of the SoulSync DB schema. Reading
# it during the scan closes the gap without requiring a DB backfill
# of the legacy rows. These tests pin:
#   - File ARTIST tag trumps DB-resolved expected artist when present
#     (Skowl's exact case: file says 'Eclypse', DB says 'Andromedik',
#     AcoustID returns 'Eclypse' → no finding)
#   - Missing file tag falls through to DB value (preserves
#     pre-fix behavior for tracks without proper file tags)
#   - mutagen failure is swallowed → falls through to DB
#   - File tag matches DB → no behavioral change


def test_scanner_uses_file_tag_artist_over_db_for_legacy_compilation(monkeypatch):
    """Skowl's exact case verbatim:

        DB row:        artist_id → 'Andromedik' (album artist), track_artist=NULL
        File tag:      ARTIST='Eclypse' (Tidal-tagged correctly)
        AcoustID:      artist='Eclypse'
        Pre-fix:       expected='Andromedik' vs actual='Eclypse' → flag
        Post-fix:      file tag trumps DB → expected='Eclypse' → no flag
    """
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("city-lights", "City Lights", "Andromedik",
                   "/music/eclypse-city-lights.opus", 1,
                   "High Tea Music: Vol 1", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{
                'title': 'City Lights',
                'artist': 'Eclypse',
            }],
        },
    )

    # Patch read_file_tags to return Tidal's correct per-track artist.
    # The scanner imports lazily inside _scan_file so we patch the
    # source module's symbol.
    monkeypatch.setattr(
        'core.tag_writer.read_file_tags',
        lambda fpath: {'artist': 'Eclypse', 'title': 'City Lights'},
    )

    result = JobResultStub()
    job._scan_file(
        '/music/eclypse-city-lights.opus',
        'city-lights',
        {'title': 'City Lights', 'artist': 'Andromedik'},  # DB-resolved expected
        fake_acoustid,
        context,
        result,
        fp_threshold=0.85,
        title_threshold=0.85,
        artist_threshold=0.6,
    )

    assert captured_findings == [], (
        f"Expected no finding (file tag matches AcoustID); got {captured_findings}"
    )


def test_scanner_falls_back_to_db_when_file_tag_missing(monkeypatch):
    """Defensive: file has no ARTIST tag (rare but possible for
    non-standard formats / damaged files). MUST fall back to DB
    expected value. Otherwise the fix would BREAK the existing
    'flag genuine mismatches' contract for files without tags."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("99", "Some Track", "Foreigner",
                   "/music/track.flac", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{
                'title': 'Some Track',
                # Unambiguously different artist (sim < 0.30) so the unified
                # core flags it (0.30-0.60 would be treated as ambiguous).
                'artist': 'Metallica',
            }],
        },
    )

    # File has no ARTIST tag (read_file_tags returns None for the field)
    monkeypatch.setattr(
        'core.tag_writer.read_file_tags',
        lambda fpath: {'artist': None},
    )

    result = JobResultStub()
    job._scan_file(
        '/music/track.flac',
        '99',
        {'title': 'Some Track', 'artist': 'Foreigner'},
        fake_acoustid,
        context,
        result,
        fp_threshold=0.85,
        title_threshold=0.85,
        artist_threshold=0.6,
    )

    # Should still flag — file tag was missing, fell back to DB
    # ('Foreigner') vs AcoustID ('Different Band') mismatch
    assert len(captured_findings) == 1, (
        f"Expected finding (file tag missing → DB fallback → genuine mismatch); got {captured_findings}"
    )


def test_scanner_swallows_file_tag_read_exception(monkeypatch):
    """Defensive: mutagen errors mid-read shouldn't crash the scan
    — must log + fall back to DB value gracefully."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("99", "Track", "RealArtist",
                   "/music/corrupted.mp3", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{'title': 'Track', 'artist': 'RealArtist'}],
        },
    )

    def boom(fpath):
        raise RuntimeError("mutagen exploded on corrupted file")

    monkeypatch.setattr('core.tag_writer.read_file_tags', boom)

    result = JobResultStub()
    job._scan_file(
        '/music/corrupted.mp3',
        '99',
        {'title': 'Track', 'artist': 'RealArtist'},
        fake_acoustid,
        context,
        result,
        fp_threshold=0.85,
        title_threshold=0.85,
        artist_threshold=0.6,
    )

    # No finding — DB matches AcoustID after the fallback
    assert captured_findings == []


def test_scanner_trusts_curated_db_track_artist_over_stale_file_tag(monkeypatch):
    """The flip side of Skowl's case — user manually corrected
    `track_artist` in the DB via the enhanced library view but
    didn't re-tag the file. Pre-refactor 'file tag always wins'
    would flag this as a false positive (file says wrong, DB says
    right, AcoustID matches DB). Post-refactor: DB track_artist
    is the curated source of truth when populated → file tag is
    only consulted when DB is empty. No spurious flag.

    This is why `_load_db_tracks` surfaces `track_artist` as a
    separate field instead of just the COALESCE'd `artist`:
    `_scan_file` needs to distinguish 'DB has a curated value'
    from 'DB fell back to album artist'."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("99", "Track", "AlbumArtist",
                   "/music/track.flac", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{'title': 'Track', 'artist': 'Eclypse'}],
        },
    )

    # File has wrong tag (stale — user edited DB but didn't re-tag),
    # DB has correct value, AcoustID matches DB.
    monkeypatch.setattr(
        'core.tag_writer.read_file_tags',
        lambda fpath: {'artist': 'WrongStaleTag'},
    )

    result = JobResultStub()
    job._scan_file(
        '/music/track.flac', '99',
        # Simulates the post-refactor _load_db_tracks output:
        # track_artist populated (curated) takes priority over file tag.
        {'title': 'Track', 'artist': 'Eclypse',
         'track_artist': 'Eclypse', 'album_artist': 'AlbumArtist'},
        fake_acoustid, context, result,
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert captured_findings == [], (
        f"DB curated value must trump stale file tag; got {captured_findings}"
    )


def test_scanner_file_tag_matches_db_no_behavioral_change(monkeypatch):
    """Sanity: when file tag and DB agree, behavior is identical to
    the pre-fix path. No double-counting, no spurious findings."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("99", "Track", "RealArtist",
                   "/music/track.flac", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{'title': 'Track', 'artist': 'RealArtist'}],
        },
    )

    monkeypatch.setattr(
        'core.tag_writer.read_file_tags',
        lambda fpath: {'artist': 'RealArtist'},
    )

    result = JobResultStub()
    job._scan_file(
        '/music/track.flac', '99',
        {'title': 'Track', 'artist': 'RealArtist'},
        fake_acoustid, context, result,
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert captured_findings == []


# ---------------------------------------------------------------------------
# Issue #587 — multi-candidate scan + duration guard (Foxxify report)
# ---------------------------------------------------------------------------


def test_scanner_no_finding_when_lower_ranked_candidate_matches():
    """Foxxify case 2 — AcoustID returns multiple recordings per
    fingerprint; the top match is the wrong-credited recording but a
    lower-ranked candidate matches expected metadata exactly. Scanner
    should iterate ALL candidates and suppress the finding.

    Repro: file is "Nana" by Geoxor, AcoustID top match is "Nana" by
    Edward Vesala Trio (different recording sharing similar
    fingerprint), AcoustID's second candidate is the actual Geoxor
    track. Pre-fix scanner only saw [0] → flagged. Post-fix sees [1]
    → no flag."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("nana", "Nana", "Geoxor",
                   "/music/nana.opus", 6, "Stardust", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.97,
            'recordings': [
                # AcoustID's top match — wrong artist for our file
                {'title': 'Nana', 'artist': 'Edward Vesala Trio'},
                # Lower-ranked candidate — actually matches our expected
                {'title': 'Nana', 'artist': 'Geoxor'},
            ],
        },
    )

    result = JobResultStub()
    job._scan_file(
        '/music/nana.opus', 'nana',
        {'title': 'Nana', 'artist': 'Geoxor'},
        fake_acoustid, context, result,
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert captured_findings == [], (
        f"Expected no finding (lower-ranked candidate matches); got {captured_findings}"
    )


def test_scanner_still_flags_when_no_candidate_matches():
    """Confirm the multi-candidate check doesn't accidentally suppress
    legitimate mismatches — if NO candidate matches expected metadata,
    the finding still fires."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("99", "Expected Title", "Expected Artist",
                   "/music/track.flac", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [
                {'title': 'Wrong Track', 'artist': 'Wrong Artist A'},
                {'title': 'Different Wrong', 'artist': 'Wrong Artist B'},
            ],
        },
    )

    result = JobResultStub()
    job._scan_file(
        '/music/track.flac', '99',
        {'title': 'Expected Title', 'artist': 'Expected Artist'},
        fake_acoustid, context, result,
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert len(captured_findings) == 1


def test_scanner_skips_finding_on_strong_duration_mismatch():
    """Foxxify case 3 — 17-minute mashup edit fingerprints to a 5-minute
    late-70s Japanese hiphop track. Fingerprint matched a sample/intro
    section but the recordings are clearly different (drastic length
    difference). Scanner should skip the finding rather than recommend
    retag of a totally different track length."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("mashup", "Some Mashup Edit", "Mashup Artist",
                   "/music/mashup.opus", 1, "Mashups", None, None),
        captured=captured_findings,
    )

    # AcoustID matched a 5-minute Japanese hiphop track via fingerprint
    # hash collision. Expected file is 17 minutes — duration guard
    # should kick in.
    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.98,
            'recordings': [
                {'title': 'Different Song', 'artist': 'Different Artist',
                 'duration': 300},  # 5 min — way off from our 17 min file
            ],
        },
    )

    result = JobResultStub()
    # 17 minutes = 1020 sec = 1020000 ms
    job._scan_file(
        '/music/mashup.opus', 'mashup',
        {'title': 'Some Mashup Edit', 'artist': 'Mashup Artist', 'duration_ms': 1020000},
        fake_acoustid, context, result,
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert captured_findings == [], (
        f"Expected no finding (duration mismatch suggests collision); got {captured_findings}"
    )


def test_scanner_still_flags_when_duration_matches():
    """Confirm the duration guard only kicks in for STRONG mismatches —
    similar-length wrong song still gets flagged."""
    job = AcoustIDScannerJob()
    captured_findings = []
    context = _make_finding_capturing_context(
        track_row=("99", "Expected", "Artist",
                   "/music/track.flac", 1, "Album", None, None),
        captured=captured_findings,
    )

    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [
                {'title': 'Wrong Song', 'artist': 'Wrong Artist',
                 'duration': 180},  # 3 min, matches expected
            ],
        },
    )

    result = JobResultStub()
    # 3-minute file with 3-minute candidate — same length, but title +
    # artist clearly mismatch → finding should still fire
    job._scan_file(
        '/music/track.flac', '99',
        {'title': 'Expected', 'artist': 'Artist', 'duration_ms': 180000},
        fake_acoustid, context, result,
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert len(captured_findings) == 1


def test_scanner_does_not_flag_cross_script_when_alias_bridges(monkeypatch):
    """Anime-OST track: AcoustID returns the kanji artist with a <Vocal: ...>
    credit. With the MusicBrainz alias bridging 澤野弘之 ↔ Sawano Hiroyuki, the
    unified verification core recognises the match, so the library scan must NOT
    create a false 'Wrong download' finding (it did before, stripping all
    non-ASCII and never consulting aliases)."""
    monkeypatch.setattr(
        "core.acoustid_verification._resolve_expected_artist_aliases",
        lambda name: ["澤野弘之"])
    job = AcoustIDScannerJob()
    captured = []
    context = _make_finding_capturing_context(
        track_row=("7", "Call Your Name", "Sawano Hiroyuki",
                   "/music/cyn.flac", 15, "Attack on Titan OST", None, None),
        captured=captured,
    )
    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.97,
            'recordings': [{'title': 'call your name',
                            'artist': '澤野弘之 <Vocal: mpi & CASG>'}],
        },
    )
    result = JobResultStub()
    job._scan_file('/music/cyn.flac', '7',
                   {'title': 'Call Your Name', 'artist': 'Sawano Hiroyuki'},
                   fake_acoustid, context, result,
                   fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6)
    assert captured == [], f"cross-script track false-flagged: {captured}"


def _force_imported_scan(monkeypatch):
    """Drive a scan over a force-imported file whose fingerprint clearly
    mismatches. Returns the captured findings."""
    monkeypatch.setattr(
        'core.tag_writer.read_file_tags',
        lambda fpath: {'artist': None, 'verification_status': 'force_imported'},
    )
    job = AcoustIDScannerJob()
    captured = []
    context = _make_finding_capturing_context(
        track_row=("42", "Wanted Song", "Real Artist",
                   "/music/ws.flac", 1, "Album", None, None),
        captured=captured,
    )
    fake_acoustid = SimpleNamespace(
        fingerprint_and_lookup=lambda fpath: {
            'best_score': 0.99,
            'recordings': [{'title': 'Wanted Song - Instrumental',
                            'artist': 'Real Artist'}],
        },
    )
    result = JobResultStub()
    job._scan_file('/music/ws.flac', '42',
                   {'title': 'Wanted Song', 'artist': 'Real Artist'},
                   fake_acoustid, context, result,
                   fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6)
    return captured


def test_force_imported_mismatch_is_reported_as_informational(monkeypatch):
    # The user opted into the fallback, so the scan must still TELL them the
    # file is e.g. an instrumental — but as 'info', clearly marked, not as a
    # red Wrong-download warning. Only human_verified short-circuits the scan.
    captured = _force_imported_scan(monkeypatch)
    assert len(captured) == 1
    assert captured[0]['severity'] == 'info'
    assert captured[0]['details'].get('force_imported') is True
    assert 'Force-imported' in captured[0]['title']


def test_human_verified_files_are_never_scanned(monkeypatch):
    monkeypatch.setattr('core.tag_writer.read_file_tags',
                        lambda fpath: {'artist': None, 'verification_status': 'human_verified'})
    job = AcoustIDScannerJob()
    captured = []
    context = _make_finding_capturing_context(
        track_row=("7", "T", "A", "/music/t.flac", 1, "Al", None, None),
        captured=captured)
    fake = SimpleNamespace(fingerprint_and_lookup=lambda f: {
        'best_score': 0.99,
        'recordings': [{'title': 'Totally Different', 'artist': 'Metallica'}]})
    job._scan_file('/music/t.flac', '7', {'title': 'T', 'artist': 'A'},
                   fake, context, JobResultStub(),
                   fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6)
    assert captured == []


# ---------------------------------------------------------------------------
# Scan-outcome persistence — the scan feeds the same review pipeline as
# import-time verification (tag + tracks row + library_history rows).
# ---------------------------------------------------------------------------


def _run_persistence_scan(tmp_path, monkeypatch, *, file_status, aid_artist,
                          expected_artist):
    """Drive one ``_scan_file`` and return (file rows, tag writes, findings).

    The verdict now lands on ``lib2_track_files.verification_status``. It used to
    be written to ``tracks`` AND healed into the file's ``library_history`` row
    (#934); the native path writes neither, which is why these assertions moved
    rather than being deleted — see docs §50.4.4.11 for the history-row gap that
    is still open.
    """
    import sqlite3

    from core.library2.schema import ensure_library_v2_schema

    monkeypatch.setattr(
        'core.tag_writer.read_file_tags',
        lambda fpath: {'artist': None, 'verification_status': file_status})
    tag_writes = []
    monkeypatch.setattr(
        'core.tag_writer.write_verification_status',
        lambda fpath, status: tag_writes.append((fpath, status)) or True)

    db_path = tmp_path / "verify.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, ?, ?)",
                 (expected_artist, expected_artist))
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Album')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (9, 1, 'Call Your Name')")
    conn.execute(
        "INSERT INTO lib2_track_files (id, track_id, path, format, is_primary) "
        "VALUES (4, 9, '/music/cyn.flac', 'flac', 1)")
    conn.commit()
    conn.close()

    class _RealDB:
        def _get_connection(self):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

    captured = []
    context = SimpleNamespace(
        db=_RealDB(),
        transfer_folder="/music",
        config_manager=_EnabledConfig(),
        acoustid_client=object(),
        create_finding=lambda **kwargs: captured.append(kwargs) or True,
        report_change=None,
        report_progress=lambda **kwargs: None,
        update_progress=lambda *args, **kwargs: None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *args, **kwargs: False,
    )

    job = AcoustIDScannerJob()
    fake = SimpleNamespace(fingerprint_and_lookup=lambda f: {
        'best_score': 0.97,
        'recordings': [{'title': 'Call Your Name', 'artist': aid_artist}]})
    job._scan_file('/music/cyn.flac', 'lib2:9',
                   {'title': 'Call Your Name', 'artist': expected_artist,
                    'lib2_file_id': 4},
                   fake, context, JobResultStub(),
                   fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6)

    conn = _RealDB()._get_connection()
    try:
        status = conn.execute(
            "SELECT verification_status FROM lib2_track_files WHERE id=4").fetchone()[0]
    finally:
        conn.close()
    return status, tag_writes, captured


def test_scan_pass_backfills_verified_status(tmp_path, monkeypatch):
    """A clean fingerprint PASS stamps the tag and the file row."""
    status, tag_writes, captured = _run_persistence_scan(
        tmp_path, monkeypatch, file_status=None,
        aid_artist='Sawano Hiroyuki', expected_artist='Sawano Hiroyuki')

    assert captured == []
    assert tag_writes == [('/music/cyn.flac', 'verified')]
    assert status == 'verified'


def test_scan_skip_marks_untagged_file_unverified(tmp_path, monkeypatch):
    """Title matches but the artist is ambiguous (cover? collab?) → SKIP, and an
    untagged file is recorded 'unverified' so it surfaces for review."""
    status, tag_writes, captured = _run_persistence_scan(
        tmp_path, monkeypatch, file_status=None,
        aid_artist='Mantilla', expected_artist='Metallica')

    assert captured == []
    assert tag_writes == [('/music/cyn.flac', 'unverified')]
    assert status == 'unverified'


def test_scan_skip_does_not_downgrade_verified(tmp_path, monkeypatch):
    """A SKIP must not undo an import-time 'verified' — that check ran with
    richer candidate metadata. The status is refreshed, the tag untouched."""
    status, tag_writes, captured = _run_persistence_scan(
        tmp_path, monkeypatch, file_status='verified',
        aid_artist='Mantilla', expected_artist='Metallica')

    assert captured == []
    assert tag_writes == []
    assert status == 'verified'
