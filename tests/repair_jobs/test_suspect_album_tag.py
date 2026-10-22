"""Unit tests for SuspectAlbumTagDetector (Issue #1335).

Covers:
- Job discovery & registration
- Category & Finding type metadata registration
- Detection signals:
  1. Lone track + missing cover art
  2. Compilation-like album title + individual artist
  3. Lone track + large full release (track_count >= 4)
- Normal albums (single release, has art) are not falsely flagged
- Finding details payload includes reidentify_query
- Scope estimation
"""

import pytest
from unittest.mock import MagicMock

from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext
from core.repair_jobs.suspect_album_tag import SuspectAlbumTagDetector, _is_various_artist, _COMPILATION_PATTERNS
from core.repair_worker import FINDING_TYPE_META, JOB_CATEGORIES, RepairWorker
from core.issues.activity import FIX_ACTIONS


class _FakeCursor:
    def __init__(self, rows, multi_rows, scalar=0):
        self._rows = rows
        self._multi_rows = multi_rows
        self._scalar = scalar
        self._sql = ""

    def execute(self, sql="", *_a, **_k):
        self._sql = sql if isinstance(sql, str) else ""
        return self

    def fetchall(self):
        # The real DB returns disjoint row sets for the two passes
        # (HAVING COUNT(t.id) = 1 vs HAVING COUNT(t.id) > 1); mirror that.
        if "COUNT(t.id) > 1" in self._sql:
            return self._multi_rows
        return self._rows

    def fetchone(self):
        if self._rows:
            return self._rows[0]
        return (self._scalar,)


class _FakeConn:
    def __init__(self, rows, multi_rows, scalar=0):
        self._rows = rows
        self._multi_rows = multi_rows
        self._scalar = scalar

    def cursor(self):
        return _FakeCursor(self._rows, self._multi_rows, self._scalar)

    def close(self):
        pass


class _FakeDB:
    def __init__(self, rows=None, multi_rows=None, scalar=0):
        self._rows = rows or []
        self._multi_rows = multi_rows or []
        self._scalar = scalar

    def _get_connection(self):
        return _FakeConn(self._rows, self._multi_rows, self._scalar)


def _row_dict(
    album_id=1,
    album_title="Some Album",
    thumb_url=None,
    full_track_count=1,
    artist_id=10,
    artist_name="Daughtry",
    track_id=100,
    track_title="It's Not Over",
    file_path="/music/Daughtry/Hitzone 43/01 - It's Not Over.flac",
):
    return {
        "album_id": album_id,
        "album_title": album_title,
        "thumb_url": thumb_url,
        "full_track_count": full_track_count,
        "artist_id": artist_id,
        "artist_name": artist_name,
        "track_id": track_id,
        "track_title": track_title,
        "file_path": file_path,
    }


def _context(rows, scalar=0, multi_rows=None):
    cfg = MagicMock()
    cfg.get.return_value = {}

    findings = []

    def create_finding(**kwargs):
        findings.append(kwargs)
        return True

    ctx = JobContext(
        db=_FakeDB(rows, multi_rows=multi_rows, scalar=scalar),
        transfer_folder="/music",
        config_manager=cfg,
        create_finding=create_finding,
    )
    return ctx, findings


def test_job_registration():
    jobs = get_all_jobs()
    assert "suspect_album_tag_detector" in jobs
    assert jobs["suspect_album_tag_detector"] is SuspectAlbumTagDetector
    assert SuspectAlbumTagDetector.job_id == "suspect_album_tag_detector"
    assert SuspectAlbumTagDetector.display_name == "Suspect Album Tags"


def test_metadata_and_categories_registration():
    assert "suspect_album_tag" in FINDING_TYPE_META
    assert FINDING_TYPE_META["suspect_album_tag"]["label"] == "Suspect Album Tags"
    assert FINDING_TYPE_META["suspect_album_tag"]["verb"] == "Re-identify"

    assert "suspect_album_tag_detector" in JOB_CATEGORIES
    assert JOB_CATEGORIES["suspect_album_tag_detector"] == "Tags & metadata"

    assert "suspect_album_tag" in FIX_ACTIONS
    assert FIX_ACTIONS["suspect_album_tag"] == ("reidentify", "Re-identify track")


def test_various_artist_detection():
    assert _is_various_artist("Various Artists")
    assert _is_various_artist("various")
    assert _is_various_artist("VA")
    assert _is_various_artist("v.a.")
    assert not _is_various_artist("Daughtry")
    assert not _is_various_artist("Taylor Swift")


def test_compilation_regex_patterns():
    assert _COMPILATION_PATTERNS.search("Hitzone 43")
    assert _COMPILATION_PATTERNS.search("Now That's What I Call Music 80")
    assert _COMPILATION_PATTERNS.search("Now 45")
    assert _COMPILATION_PATTERNS.search("Now 114")
    assert _COMPILATION_PATTERNS.search("NOW 80")
    assert _COMPILATION_PATTERNS.search("Top 40 Summer Hits")
    assert _COMPILATION_PATTERNS.search("The Best Of 2000s")
    assert _COMPILATION_PATTERNS.search("Greatest Hits")
    assert not _COMPILATION_PATTERNS.search("Leave This Town")
    assert not _COMPILATION_PATTERNS.search("The Now Show")


def test_flags_lone_track_missing_cover_art():
    row = _row_dict(
        album_title="Normal Title",
        thumb_url=None,  # Missing art
        full_track_count=1,
        artist_name="Daughtry",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.scanned == 1
    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "suspect_album_tag"
    assert "no cover art" in f["details"]["reasons"]
    assert f["details"]["reidentify_query"] == "It's Not Over Daughtry"


def test_flags_compilation_pattern_with_named_artist():
    row = _row_dict(
        album_title="Hitzone 43",
        thumb_url="http://art.jpg",  # has art
        full_track_count=1,
        artist_name="Daughtry",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert any("looks like a compilation" in r for r in f["details"]["reasons"])


def test_flags_lone_track_in_various_artists_album_with_named_track_artist():
    row = _row_dict(
        album_title="Random Party Album",
        thumb_url="http://art.jpg",
        full_track_count=1,
        artist_name="Daughtry",
    )
    row["album_artist_name"] = "Various Artists"
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert any("lone track in Various Artists compilation" in r for r in f["details"]["reasons"])
    assert f["details"]["reidentify_query"] == "It's Not Over Daughtry"


def test_skips_compilation_if_artist_is_various():
    row = _row_dict(
        album_title="Hitzone 43",
        thumb_url="http://art.jpg",
        full_track_count=1,
        artist_name="Various Artists",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 0
    assert len(findings) == 0


def test_one_wished_track_of_a_big_album_alone_is_not_suspect():
    """The Wishlist case: one track of a 12-track album, cover present."""
    row = _row_dict(album_title="Ordinary Album", thumb_url="http://art.jpg",
                    full_track_count=12, artist_name="Coldplay", track_title="Yellow")
    ctx, findings = _context([row])
    assert SuspectAlbumTagDetector().scan(ctx).findings_created == 0


def test_lone_track_of_a_big_album_corroborates_another_signal():
    row = _row_dict(album_title="Ordinary Album", thumb_url=None,
                    full_track_count=12, artist_name="Coldplay", track_title="Yellow")
    ctx, findings = _context([row])
    SuspectAlbumTagDetector().scan(ctx)
    assert findings[0]["details"]["reasons"] == ["no cover art", "only 1 of 12 tracks locally owned"]
    assert findings[0]["details"]["reidentify_query"] == "Yellow Coldplay"


def test_singles_and_an_artists_own_greatest_hits_are_not_suspects():
    single = _row_dict(album_title="Yellow", thumb_url=None, artist_name="Coldplay")
    single["album_type"] = "single"
    hits = _row_dict(album_id=2, album_title="Greatest Hits", thumb_url="http://art.jpg",
                     full_track_count=17, artist_name="Queen", track_id=101)
    hits["album_artist_name"] = "Queen"
    ctx, findings = _context([single, hits])
    assert SuspectAlbumTagDetector().scan(ctx).findings_created == 0


def test_normal_single_with_art_not_flagged():
    row = _row_dict(
        album_title="Single Title",
        thumb_url="http://art.jpg",
        full_track_count=1,
        artist_name="Coldplay",
        track_title="Single Title",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.scanned == 1
    assert result.findings_created == 0
    assert result.skipped == 1
    assert len(findings) == 0


def test_scope_estimation():
    ctx, _ = _context([], scalar=5)
    scope = SuspectAlbumTagDetector().estimate_scope(ctx)
    assert scope == 5


def test_fix_finding_requires_reidentify_target():
    worker = MagicMock()
    # Test unbound method directly
    res = RepairWorker._fix_suspect_album_tag(
        worker,
        entity_type="track",
        entity_id="123",
        file_path="/path/track.mp3",
        details={"reidentify_query": "Test Track Artist"},
    )
    assert res["success"] is False
    assert "Re-identify button" in res["error"]


# ── Library v2: the query against a real catalogue ─────────────────────────

def _lib2_album(db, artist, title, *, owned_tracks, catalogue_tracks, image=None,
                track_count=None, credit=None):
    """An album with `catalogue_tracks` rows, the first `owned_tracks` of them
    with a live file. Returns the lib2 track ids."""
    with db._get_connection() as conn:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists (name, name_key) VALUES (?, ?)",
            (artist, f"{artist.lower()}-{title.lower()}")).lastrowid
        album_id = conn.execute(
            "INSERT INTO lib2_albums (primary_artist_id, title, image_url, track_count, origin) "
            "VALUES (?, ?, ?, ?, 'library')",
            (artist_id, title, image, track_count)).lastrowid
        credit_id = None
        if credit:
            credit_id = conn.execute(
                "INSERT INTO lib2_artists (name, name_key) VALUES (?, ?)",
                (credit, f"{credit.lower()}-credit")).lastrowid
        ids = []
        for n in range(catalogue_tracks):
            track_id = conn.execute(
                "INSERT INTO lib2_tracks (album_id, title, track_number) VALUES (?, ?, ?)",
                (album_id, f"{title} {n + 1}", n + 1)).lastrowid
            if credit_id:
                conn.execute("INSERT INTO lib2_track_artists (track_id, artist_id) VALUES (?, ?)",
                             (track_id, credit_id))
            if n < owned_tracks:
                conn.execute(
                    "INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
                    (track_id, f"/music/{artist}/{title}/{n + 1}.flac"))
            ids.append(track_id)
        conn.commit()
    return ids


def test_library_v2_lone_owned_track_is_flagged_with_a_native_subject(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / "m.db"))
    lone = _lib2_album(db, "Various Artists", "Hitzone 43", owned_tracks=1,
                       catalogue_tracks=20, image="http://art.jpg", track_count=20,
                       credit="Daughtry")
    _lib2_album(db, "Coldplay", "Parachutes", owned_tracks=10, catalogue_tracks=10,
                image="http://art.jpg", track_count=10)
    findings = []
    ctx = JobContext(db=db, transfer_folder="/music", config_manager=None,
                     create_finding=lambda **kw: findings.append(kw) or True)

    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.scanned == 1                       # only the one-track album
    assert len(findings) == 1
    f = findings[0]
    assert f["entity_id"] == f"lib2:{lone[0]}"
    assert f["file_path"] == "/music/Various Artists/Hitzone 43/1.flac"
    assert f["details"]["artist_name"] == "Daughtry"   # the track's own credit
    assert f["details"]["album_artist_name"] == "Various Artists"
    assert any("only 1 of 20" in r for r in f["details"]["reasons"])
    assert SuspectAlbumTagDetector().estimate_scope(ctx) == 1


def test_library_v2_wanted_tracks_without_files_are_not_owned(tmp_path):
    """a monitored album's missing tracks are catalogue rows with no file;
    they are not "your tracks" and must not hide a lone owned one"""
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / "m.db"))
    _lib2_album(db, "Nobody", "Nothing Owned", owned_tracks=0, catalogue_tracks=5)
    findings = []
    ctx = JobContext(db=db, transfer_folder="/music", config_manager=None,
                     create_finding=lambda **kw: findings.append(kw) or True)
    assert SuspectAlbumTagDetector().scan(ctx).scanned == 0
    assert findings == []


# ---------------------------------------------------------------------------
# Signal 4 (#1567): multi-track poisoned batches.


def _multi_row(album_id=7, album_title="Music", album_artist="Nickelback", **kw):
    """A multi-track query row in the #1567 fingerprint shape: 11 unrelated
    tracks filed as one bogus album, duplicated track numbers."""
    row = {
        "album_id": album_id,
        "album_title": album_title,
        "album_type": "album",
        "full_track_count": 10,
        "album_artist_name": album_artist,
        "local_track_count": 11,
        "distinct_artists": 9,
        "artist_names": (
            "Finger Eleven | Foo Fighters | Godsmack | Nickelback | Default | "
            "Three Days Grace | Staind | Audioslave | Red Hot Chili Peppers"
        ),
        "numbered_tracks": 11,
        "distinct_numbers": 6,
        "album_artist_track_hits": 1,
    }
    row.update(kw)
    return row


def test_1567_multi_track_poisoned_batch_flagged():
    """#1567: 11 unrelated tracks, 9 distinct artists, duplicate track numbers
    (11 numbered tracks share 6 distinct numbers) — one album-level finding
    with a clear reason."""
    ctx, findings = _context([], multi_rows=[_multi_row()])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "suspect_album_tag"
    assert f["entity_type"] == "album"
    assert f["entity_id"] == "lib2:7"
    assert f["severity"] == "warning"
    assert any("duplicate track numbers" in r for r in f["details"]["reasons"])
    assert f["details"]["album_title"] == "Music"
    assert f["details"]["distinct_artists"] == 9
    assert "Music" in f["title"]


def test_1567_multi_track_album_artist_mismatch_without_dup_numbers():
    """The OR branch: no duplicate numbers, but the album artist is not among
    the track artists — still suspect."""
    row = _multi_row(numbered_tracks=11, distinct_numbers=11,
                     album_artist_track_hits=0)
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert any("not among the" in r and "Nickelback" in r
               for r in f["details"]["reasons"])


def test_1567_multi_track_missing_album_artist_flagged():
    """3+ distinct track artists and no album artist at all — the album
    identity is unanchored."""
    row = _multi_row(album_artist="", album_artist_track_hits=0)
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    assert any("no album artist" in r for r in findings[0]["details"]["reasons"])


def test_1567_healthy_multi_artist_album_not_flagged():
    """A genuine multi-artist album: the album artist backs every track and
    no track numbers are duplicated — quiet."""
    row = _multi_row(
        album_title="Real Mix Tape",
        album_artist="DJ Mixmaster",
        local_track_count=10,
        distinct_artists=5,
        numbered_tracks=10,
        distinct_numbers=10,
        album_artist_track_hits=10,
    )
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 0
    assert findings == []


def test_1567_various_artists_multi_track_compilation_exempt():
    """A genuine Various Artists compilation with duplicated numbers (multi-
    disc) is exempt — the poisoned batch claimed one real band."""
    row = _multi_row(album_title="Hitzone 99", album_artist="Various Artists",
                     album_artist_track_hits=0)
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 0
    assert findings == []


def test_1567_two_artist_album_below_threshold_quiet():
    """A duet/collab album (2 distinct artists) is below the 3-artist gate."""
    row = _multi_row(distinct_artists=2, local_track_count=8,
                     numbered_tracks=8, distinct_numbers=8,
                     album_artist_track_hits=8)
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 0
    assert findings == []


def test_1567_multi_track_single_record_type_exempt():
    """Singles are never suspects, even in the multi-track pass."""
    row = _multi_row(album_type="single")
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 0
    assert findings == []


# ---------------------------------------------------------------------------
# Round 2 (reviewer 1): M3 — Signal 4 against a REAL SQLite database, so the
# per-(disc, track) duplicate counting and the curator-credit exemption run
# through the module's actual SQL, not the fake cursor.
# ---------------------------------------------------------------------------

import sqlite3 as _sqlite3


class _Keeper:
    def close(self):
        pass


def _lib2_sig4_db(albums):
    """Library v2 port of the real-SQL Signal 4 fixtures: ``albums`` is a list of
    ``(album_id, title, album_artist, spotify_id, track_count, tracks)`` with
    ``tracks`` as ``(credited_artist, track_artist, number, disc)``. Every track
    has a live primary file, so it counts as owned."""
    import tempfile

    from database.music_database import MusicDatabase

    db = MusicDatabase(tempfile.mkdtemp() + "/sig4.db")
    conn = db._get_connection()
    artists = {}

    def artist(name):
        if name not in artists:
            artists[name] = conn.execute(
                "INSERT INTO lib2_artists (name, name_key) VALUES (?, ?)",
                (name, name.lower())).lastrowid
        return artists[name]

    for album_id, title, album_artist, spotify_id, track_count, tracks in albums:
        conn.execute(
            "INSERT INTO lib2_albums (id, primary_artist_id, title, album_type, track_count, spotify_id, origin)"
            " VALUES (?, ?, ?, 'album', ?, ?, 'library')",
            (album_id, artist(album_artist), title, track_count, spotify_id))
        for n, (credited, track_artist, number, disc) in enumerate(tracks):
            track_id = conn.execute(
                "INSERT INTO lib2_tracks (album_id, title, track_number, disc_number, track_artist)"
                " VALUES (?, ?, ?, ?, ?)",
                (album_id, f"{title} {n}", number, disc, track_artist)).lastrowid
            conn.execute("INSERT INTO lib2_track_artists (track_id, artist_id) VALUES (?, ?)",
                         (track_id, artist(credited)))
            conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
                         (track_id, f"/music/{album_artist}/{title}/{track_id}.flac"))
    conn.commit()
    conn.close()
    return db, _Keeper()


def _real_sig4_db():
    """three multi-track albums: 100 a genuine 2-disc comp (numbers repeat
    across discs only, real provider id), 200 the #1567 fingerprint (dup
    numbers within disc 1, placeholder id), 300 a curator-credited comp with
    clean numbering and a real provider id."""
    dj = [(a, None, n, disc) for disc, names in ((1, ("Foo Fighters", "Godsmack", "Staind")),
                                                  (2, ("Default", "Three Days Grace", "Audioslave")))
          for n, a in enumerate(names, start=1)]
    poison_artists = ["Nickelback", "Foo Fighters", "Godsmack", "Staind", "Default",
                      "Three Days Grace", "Audioslave", "Finger Eleven",
                      "Red Hot Chili Peppers", "Foo Fighters", "Godsmack"]
    poison = [(a, None, n, 1) for a, n in zip(poison_artists, [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5])]
    curated = [(a, None, n, 1) for n, a in enumerate(
        ("Alpha Band", "Beta Band", "Gamma Band", "Alpha Band", "Beta Band"), start=1)]
    return _lib2_sig4_db([
        (100, "DJ Mix 2026", "DJ Mixmaster", "realDJmix123", 6, dj),
        (200, "Music", "Nickelback", "wishlist_album", 10, poison),
        (300, "Curator's Choice", "Curator Dan", "realCurator456", 5, curated),
    ])


def _sig4_context(db):
    from unittest.mock import MagicMock
    from core.repair_jobs.base import JobContext
    findings = []

    def create_finding(**kwargs):
        findings.append(kwargs)
        return True

    ctx = JobContext(
        db=db,
        transfer_folder="/music",
        config_manager=MagicMock(),
        create_finding=create_finding,
    )
    return ctx, findings


def test_1567_m3_multidisc_comp_stays_quiet_but_poison_fires():
    """M3 end-to-end on real SQL: the 2-disc DJ comp (numbers repeat across
    discs only) and the curator-credited comp (real provider id, clean
    numbering) stay quiet; the #1567 fingerprint (dup numbers within one
    disc, placeholder provider id) fires exactly one finding."""
    db, keeper = _real_sig4_db()
    try:
        ctx, findings = _sig4_context(db)
        result = SuspectAlbumTagDetector().scan(ctx)
    finally:
        keeper.close()

    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f["entity_id"] == "lib2:200"
    assert f["details"]["album_title"] == "Music"
    assert any("duplicate track numbers" in r for r in f["details"]["reasons"])


def test_1567_m3_curator_credit_with_placeholder_id_still_fires():
    """The curator exemption needs a REAL provider id: the same curator
    shape with a placeholder id keeps the artist-mismatch reason."""
    row = _multi_row(
        album_title="Curator's Choice",
        album_artist="Curator Dan",
        local_track_count=5,
        distinct_artists=3,
        numbered_tracks=5,
        distinct_numbers=5,  # clean numbering — only the OR branch can fire
        album_artist_track_hits=0,
        spotify_album_id="wishlist_album",
        itunes_album_id=None,
    )
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)
    assert result.findings_created == 1
    assert any("not among the" in r for r in findings[0]["details"]["reasons"])


def test_1567_m3_curator_credit_with_real_id_exempt():
    """M3 decision: curator/DJ-credited comp with a REAL shared provider
    album id and clean numbering is not flagged on artist mismatch alone."""
    row = _multi_row(
        album_title="Curator's Choice",
        album_artist="Curator Dan",
        local_track_count=5,
        distinct_artists=3,
        numbered_tracks=5,
        distinct_numbers=5,
        album_artist_track_hits=0,
        spotify_album_id="4aW4aW4aW4aW4aW4aW4aW",
        itunes_album_id=None,
    )
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)
    assert result.findings_created == 0
    assert findings == []


def test_1567_m3_dup_numbers_fire_despite_real_id():
    """The duplicate-numbers half of Signal 4 is NOT exempted by a real
    provider id — number collisions within a disc stay suspicious."""
    row = _multi_row(
        album_artist="Curator Dan",
        album_artist_track_hits=0,
        spotify_album_id="4aW4aW4aW4aW4aW4aW4aW",
    )
    ctx, findings = _context([], multi_rows=[row])
    result = SuspectAlbumTagDetector().scan(ctx)
    assert result.findings_created == 1
    assert any("duplicate track numbers" in r for r in findings[0]["details"]["reasons"])


# ---------------------------------------------------------------------------
# Round 3 (reviewer 2): MINOR 1 — Signal 4's duplicate count must not collapse
# NULL disc numbers into disc 1. A legit multi-disc rip that never set
# disc_number must stay quiet even when track numbers repeat; the #1567
# poison fingerprint still fires via the album-artist half on NULL discs.
# ---------------------------------------------------------------------------


def _real_sig4_nulldisc_db():
    """disc_number NULL everywhere: 400 a legit two-"disc" box set (1-12
    twice, album artist among the track artists) must stay quiet; 500 the
    #1567 shape whose album artist is a stranger fires via the artist half."""
    box = [(a, None, n, None) for _rep in range(2)
           for n, a in enumerate(("Alpha Band", "Beta Band", "Gamma Band", "Delta Band",
                                  "Epsilon Band", "Zeta Band") * 2, start=1)]
    poison_artists = ["Nickelback", "Foo Fighters", "Godsmack", "Staind", "Default",
                      "Three Days Grace", "Audioslave", "Finger Eleven",
                      "Red Hot Chili Peppers", "Foo Fighters", "Godsmack"]
    poison = [(a, None, n, None) for a, n in zip(poison_artists, [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5])]
    return _lib2_sig4_db([
        (400, "Null Disc Box Set", "Alpha Band", "wishlist_album", 24, box),
        (500, "Music (Null Disc)", "Stranger Sam", "wishlist_album", 11, poison),
    ])


def test_1567_r2_null_disc_numbers_do_not_trip_dup_check():
    """MINOR 1 end-to-end on real SQL: the NULL-disc box set (repeated track
    numbers, no provable disc) stays quiet; the #1567 shape on NULL discs
    still fires via the album-artist half."""
    db, keeper = _real_sig4_nulldisc_db()
    try:
        ctx, findings = _sig4_context(db)
        result = SuspectAlbumTagDetector().scan(ctx)
    finally:
        keeper.close()

    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f["entity_id"] == "lib2:500"
    reasons = f["details"]["reasons"]
    assert any("not among the" in r for r in reasons)
    assert not any("duplicate track numbers" in r for r in reasons)


# ---------------------------------------------------------------------------
# R4F2: Signal 4 against the REAL production DB shape — bundle-flow imports
# stamp every track with the BATCH artist_id, so the query must count
# distinct artists via tracks.track_artist, not the joined artists row.
# ---------------------------------------------------------------------------


def _real_sig4_batch_artist_db():
    """the true #1567 production shape: every track credited to the BATCH
    artist (Nickelback), the per-track truth only in track_artist."""
    real_artists = [
        "Nickelback", "Finger Eleven", "Foo Fighters", "Godsmack", "Staind",
        "Default", "Three Days Grace", "Audioslave", "Red Hot Chili Peppers",
        "Foo Fighters", "Godsmack",
    ]
    numbers = [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5]
    return _lib2_sig4_db([
        (700, "Music", "Nickelback", "wishlist_album", 10,
         [("Nickelback", artist, num, 1) for artist, num in zip(real_artists, numbers)]),
    ])


def test_1567_r4_signal4_counts_track_artist_not_batch_artist_id():
    """R4F2 end-to-end on real SQL: all 11 tracks share the batch artist_id
    (the production bundle-flow shape). The finding must fire because the
    per-track artists differ — the old query counted via the joined artist
    row and saw a single artist."""
    db, keeper = _real_sig4_batch_artist_db()
    try:
        ctx, findings = _sig4_context(db)
        result = SuspectAlbumTagDetector().scan(ctx)
    finally:
        keeper.close()

    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f["entity_id"] == "lib2:700"
    assert f["details"]["album_title"] == "Music"
    # the finding opens the album on its artist page, so it carries the id
    assert f["details"]["artist_id"] == 2
    assert f["details"]["distinct_artists"] == 9
    assert any("duplicate track numbers" in r for r in f["details"]["reasons"])
