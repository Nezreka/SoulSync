from types import SimpleNamespace

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None


class _FakeConnection:
    def __init__(self, rows):
        self._cursor = _FakeCursor(rows)

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _make_context(rows):
    conn = _FakeConnection(rows)
    config_manager = SimpleNamespace(
        get=lambda key, default=None: default,
        set=lambda *args, **kwargs: None,
    )
    db = SimpleNamespace(_get_connection=lambda: conn)
    return SimpleNamespace(
        db=db,
        transfer_folder="/music",
        config_manager=config_manager,
        acoustid_client=object(),
        create_finding=None,
        report_progress=lambda **kwargs: None,
        update_progress=lambda *args, **kwargs: None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *args, **kwargs: False,
    )


def test_load_db_tracks_skips_null_ids_and_normalizes_track_ids():
    job = AcoustIDScannerJob()
    context = _make_context([
        # 11 columns: id, title, artist (COALESCE'd), file_path, track_number,
        # album_title, album_thumb, artist_thumb, track_artist (raw, may be ''),
        # album_artist, duration_ms (issue #587 — duration guard).
        (None, "Broken Track", "Artist", "/music/broken.flac", 1, "Album", None, None, "", "Artist", 180000),
        (42, "Good Track", "Artist", "/music/good.flac", 2, "Album", "album-thumb", "artist-thumb", "", "Artist", 240000),
    ])

    tracks = job._load_db_tracks(context)

    assert list(tracks.keys()) == ["42"]
    assert tracks["42"]["title"] == "Good Track"
    assert tracks["42"]["artist"] == "Artist"
    assert tracks["42"]["duration_ms"] == 240000


def test_scan_handles_mixed_track_id_types(monkeypatch):
    job = AcoustIDScannerJob()
    context = _make_context([
        # 11 columns: id, title, artist (COALESCE'd), file_path, track_number,
        # album_title, album_thumb, artist_thumb, track_artist (raw, may be ''),
        # album_artist, duration_ms.
        (None, "Broken Track", "Artist", "/music/broken.flac", 1, "Album", None, None, "", "Artist", 180000),
        (42, "Good Track", "Artist", "/music/good.flac", 2, "Album", "album-thumb", "artist-thumb", "", "Artist", 240000),
    ])

    monkeypatch.setattr(job, "_resolve_path", lambda file_path, _context: file_path)

    scanned_track_ids = []

    def fake_scan_file(fpath, track_id, expected, acoustid_client, context, result,
                       fp_threshold, title_threshold, artist_threshold):
        scanned_track_ids.append(track_id)

    monkeypatch.setattr(job, "_scan_file", fake_scan_file)

    result = job.scan(context)

    assert result.scanned == 1
    assert scanned_track_ids == ["42"]


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


def _make_finding_capturing_context(track_row, captured):
    """Context that captures any create_finding calls into the
    `captured` list. Tests assert against this list to verify whether
    the scanner created a finding (false positive) or correctly
    skipped (multi-value match resolved)."""
    conn = _FakeConnection([track_row])
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
                'artist': 'Different Band, Other Person & Random Featuring',
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
    """Build a context with a REAL SQLite DB so the scanner's
    multi-table JOIN runs against actual schema. SimpleNamespace
    fakes can't simulate the JOIN."""
    import sqlite3
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE artists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            thumb_url TEXT
        );
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            thumb_url TEXT
        );
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY,
            title TEXT,
            artist_id TEXT,
            album_id TEXT,
            file_path TEXT,
            track_number INTEGER,
            track_artist TEXT,
            duration INTEGER
        );
    """)
    conn.commit()
    conn.close()

    class _RealDB:
        def _get_connection(self):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

    return _RealDB()


def test_load_db_tracks_prefers_track_artist_for_compilation():
    """Reporter's exact case (Skowl) — compilation album where
    every track has a different artist credited via track_artist
    column, while artist_id points at the album-level curator."""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    db = _make_real_db_context(tmp)

    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO artists (id, name) VALUES ('andro', 'Andromedik')")
    cursor.execute(
        "INSERT INTO albums (id, title) VALUES ('hightea', 'High Tea Music: Vol 1')"
    )
    cursor.execute(
        "INSERT INTO tracks (id, title, artist_id, album_id, file_path, track_artist) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ('city-lights', 'City Lights', 'andro', 'hightea',
         '/music/citylights.mp3', 'Eclypse'),
    )
    cursor.execute(
        "INSERT INTO tracks (id, title, artist_id, album_id, file_path, track_artist) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ('invasion', 'Invasion', 'andro', 'hightea',
         '/music/invasion.mp3', None),  # NULL track_artist falls back
    )
    conn.commit()
    conn.close()

    job = AcoustIDScannerJob()
    context = SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda *a, **k: None),
    )
    tracks = job._load_db_tracks(context)

    # Track with track_artist populated → Eclypse (per-track), NOT
    # Andromedik (album-artist via artist_id).
    assert tracks['city-lights']['artist'] == 'Eclypse', (
        f"Compilation track must use track_artist; got {tracks['city-lights']['artist']!r}"
    )
    # Track with NULL track_artist → falls back to album artist
    # via COALESCE. Backward compat for legacy rows + single-artist
    # albums where track_artist isn't populated.
    assert tracks['invasion']['artist'] == 'Andromedik'


def test_load_db_tracks_falls_back_when_track_artist_empty_string():
    """Defensive: NULLIF treats empty string as NULL too. Some
    legacy rows might have stored '' instead of NULL."""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    db = _make_real_db_context(tmp)

    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO artists (id, name) VALUES ('a', 'Album Artist')")
    cursor.execute("INSERT INTO albums (id, title) VALUES ('alb', 'Album')")
    cursor.execute(
        "INSERT INTO tracks (id, title, artist_id, album_id, file_path, track_artist) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ('t1', 'T1', 'a', 'alb', '/music/t1.mp3', ''),  # empty string
    )
    conn.commit()
    conn.close()

    job = AcoustIDScannerJob()
    context = SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda *a, **k: None),
    )
    tracks = job._load_db_tracks(context)

    # Empty string in track_artist → NULLIF returns NULL → COALESCE
    # falls back to album artist
    assert tracks['t1']['artist'] == 'Album Artist'


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
                'artist': 'Different Band',
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
