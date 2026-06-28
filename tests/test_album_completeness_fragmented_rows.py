import sqlite3
import sys
import types
import uuid


class _DummyConfigManager:
    def get(self, key, default=None):
        return default

    def get_active_media_server(self):
        return "plex"


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.repair_jobs.album_completeness import AlbumCompletenessJob
import core.repair_jobs.album_completeness as album_completeness_module


class _SharedMemoryDB:
    def __init__(self):
        self.uri = (
            f"file:testdb_{uuid.uuid4().hex}"
            "?mode=memory&cache=shared"
        )
        self._keepalive = sqlite3.connect(self.uri, uri=True)
        self._keepalive.executescript(
            """
            CREATE TABLE artists (
                id TEXT PRIMARY KEY,
                name TEXT,
                thumb_url TEXT
            );

            CREATE TABLE albums (
                id TEXT PRIMARY KEY,
                artist_id TEXT,
                title TEXT,
                thumb_url TEXT,
                spotify_album_id TEXT,
                itunes_album_id TEXT,
                deezer_id TEXT,
                discogs_id TEXT,
                soul_id TEXT,
                musicbrainz_release_id TEXT,
                canonical_source TEXT,
                canonical_album_id TEXT,
                api_track_count INTEGER
            );

            CREATE TABLE tracks (
                id TEXT PRIMARY KEY,
                album_id TEXT,
                title TEXT,
                track_number INTEGER,
                disc_number INTEGER,
                duration INTEGER,
                musicbrainz_recording_id TEXT
            );
            """
        )
        self._keepalive.commit()

    def _get_connection(self):
        return sqlite3.connect(self.uri, uri=True)

    def insert_artist(self, artist_id, name):
        self._keepalive.execute(
            """
            INSERT INTO artists (id, name)
            VALUES (?, ?)
            """,
            (artist_id, name),
        )
        self._keepalive.commit()

    def insert_album(
        self,
        album_id,
        artist_id,
        title,
        *,
        spotify_id=None,
        musicbrainz_id=None,
        canonical_source=None,
        canonical_album_id=None,
        api_track_count=None,
    ):
        self._keepalive.execute(
            """
            INSERT INTO albums (
                id,
                artist_id,
                title,
                spotify_album_id,
                musicbrainz_release_id,
                canonical_source,
                canonical_album_id,
                api_track_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                album_id,
                artist_id,
                title,
                spotify_id,
                musicbrainz_id,
                canonical_source,
                canonical_album_id,
                api_track_count,
            ),
        )
        self._keepalive.commit()

    def insert_track(
        self,
        album_id,
        number,
        title,
        *,
        disc=1,
        duration_ms=180000,
        mbid=None,
    ):
        track_id = f"{album_id}-{disc}-{number}-{uuid.uuid4().hex}"
        self._keepalive.execute(
            """
            INSERT INTO tracks (
                id,
                album_id,
                title,
                track_number,
                disc_number,
                duration,
                musicbrainz_recording_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track_id,
                album_id,
                title,
                number,
                disc,
                duration_ms,
                mbid,
            ),
        )
        self._keepalive.commit()


def _context(db, findings):
    return types.SimpleNamespace(
        db=db,
        transfer_folder='',
        config_manager=_DummyConfigManager(),
        spotify_client=None,
        is_spotify_rate_limited=lambda: False,
        stop_event=None,
        create_finding=lambda **kwargs: (
            findings.append(kwargs) or True
        ),
        should_stop=None,
        is_paused=None,
        update_progress=None,
        report_progress=None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
    )


def _canonical_tracks(count, *, prefix="Canonical"):
    return {
        "items": [
            {
                "id": f"track-{number}",
                "name": f"{prefix} Track {number}",
                "track_number": number,
                "disc_number": 1,
                "duration_ms": 180000 + number,
                "artists": [],
            }
            for number in range(1, count + 1)
        ],
    }


def test_scan_groups_validated_fragmented_rows_into_one_finding(
    monkeypatch,
):
    db = _SharedMemoryDB()
    db.insert_artist("artist-1", "Artist")
    db.insert_album(
        "anchor",
        "artist-1",
        "Album",
        spotify_id="shared-release",
        canonical_source="deezer",
        canonical_album_id="canonical-release",
    )
    db.insert_album(
        "fragment",
        "artist-1",
        "ALBUM",
        spotify_id="shared-release",
        api_track_count=2,
    )

    db.insert_track("anchor", 1, "Canonical Track 1")
    db.insert_track("anchor", 2, "Canonical Track 2")
    db.insert_track("fragment", 3, "Canonical Track 3")
    db.insert_track("fragment", 4, "Canonical Track 4")

    calls = []

    def get_tracks(source, album_id):
        calls.append((source, album_id))
        assert source == "deezer"
        assert album_id == "canonical-release"
        return _canonical_tracks(5)

    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        get_tracks,
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_primary_source",
        lambda: "spotify",
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_source_priority",
        lambda primary: ["spotify", "deezer"],
    )

    findings = []
    result = AlbumCompletenessJob().scan(
        _context(db, findings)
    )

    assert result.scanned == 1
    assert result.findings_created == 1
    assert calls == [("deezer", "canonical-release")]

    details = findings[0]["details"]
    assert details["album_id"] == "anchor"
    assert details["expected_tracks"] == 5
    assert details["actual_tracks"] == 4
    assert details["raw_local_tracks"] == 4
    assert details["related_album_ids"] == [
        "anchor",
        "fragment",
    ]
    assert [
        track["track_number"]
        for track in details["missing_tracks"]
    ] == [5]


def test_shared_id_without_track_match_stays_independent(
    monkeypatch,
):
    db = _SharedMemoryDB()
    db.insert_artist("artist-1", "Artist")
    db.insert_album(
        "anchor",
        "artist-1",
        "Album",
        spotify_id="shared-release",
        canonical_source="deezer",
        canonical_album_id="canonical-release",
    )
    db.insert_album(
        "unrelated",
        "artist-1",
        "Different Album",
        spotify_id="shared-release",
        api_track_count=1,
    )

    db.insert_track("anchor", 1, "Canonical Track 1")
    db.insert_track(
        "unrelated",
        99,
        "Completely Unrelated",
        duration_ms=900000,
    )

    calls = []

    def get_tracks(source, album_id):
        calls.append((source, album_id))
        return _canonical_tracks(3)

    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        get_tracks,
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_primary_source",
        lambda: "spotify",
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_source_priority",
        lambda primary: ["spotify", "deezer"],
    )

    findings = []
    result = AlbumCompletenessJob().scan(
        _context(db, findings)
    )

    assert result.scanned == 2
    assert result.findings_created == 1
    assert calls == [("deezer", "canonical-release")]
    assert findings[0]["entity_id"] == "anchor"
    assert findings[0]["details"]["related_album_ids"] == [
        "anchor",
    ]


def test_excluded_canonical_sibling_reports_only_its_missing_tracks(
    monkeypatch,
):
    """A second row pinned to the SAME canonical edition whose tracks fail the
    strict fragment match is still evaluated against that edition — but it must
    report only the tracks it does NOT own, not the whole tracklist. Regression
    for the excluded-sibling bug (it previously flagged every canonical track as
    missing, including the ones the row already had)."""
    db = _SharedMemoryDB()
    db.insert_artist("artist-1", "Artist")
    # Anchor: complete, titles match the canonical edition.
    db.insert_album(
        "anchor",
        "artist-1",
        "Album",
        canonical_source="deezer",
        canonical_album_id="canonical-release",
    )
    for number in range(1, 6):
        db.insert_track("anchor", number, f"Canonical Track {number}")

    # Sibling: same canonical pair, owns tracks 1-3 by NUMBER but with blank
    # titles → fails the strict fragment match → excluded from the anchor group.
    db.insert_album(
        "sibling",
        "artist-1",
        "Album",
        canonical_source="deezer",
        canonical_album_id="canonical-release",
    )
    for number in range(1, 4):
        db.insert_track("sibling", number, "")

    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: _canonical_tracks(5),
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_primary_source",
        lambda: "deezer",
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_source_priority",
        lambda primary: ["deezer"],
    )

    findings = []
    AlbumCompletenessJob().scan(_context(db, findings))

    sibling_finding = next(
        f for f in findings if f["entity_id"] == "sibling"
    )
    details = sibling_finding["details"]
    # Owns tracks 1-3 (by number) → "3 of 5", and only 2 missing (4 & 5),
    # NOT the full 5 — and the count stays internally consistent.
    assert details["actual_tracks"] == 3
    assert details["expected_tracks"] == 5
    missing_numbers = sorted(
        t["track_number"] for t in details["missing_tracks"]
    )
    assert missing_numbers == [4, 5]


def test_candidate_matching_two_canonical_groups_stays_independent():
    """A candidate whose shared ID resolves to MORE THAN ONE canonical group is
    ambiguous and must not be fused into either. Locks the `len(matches) == 1`
    rule the one-pass grouping relies on. (`_build_candidate_groups` is pure, so
    drive it directly with album dicts.)"""
    def _album(album_id, order, **extra):
        base = {
            'album_id': album_id, 'artist_id': 'artist-1',
            'album_title': album_id, 'actual_count': 1, '_scan_order': order,
            'spotify_album_id': '', 'itunes_album_id': '', 'deezer_album_id': '',
            'discogs_album_id': '', 'hydrabase_album_id': '',
            'musicbrainz_album_id': '', 'canonical_source': '',
            'canonical_album_id': '',
        }
        base.update(extra)
        return base

    # Two distinct canonical editions (same artist) that share spotify-X, plus a
    # candidate that also carries spotify-X → it resolves to BOTH groups.
    albums = [
        _album('anchor-a', 0, spotify_album_id='shared-X',
               canonical_source='deezer', canonical_album_id='canonical-a'),
        _album('anchor-b', 1, spotify_album_id='shared-X',
               canonical_source='deezer', canonical_album_id='canonical-b'),
        _album('candidate', 2, spotify_album_id='shared-X'),
    ]

    groups = AlbumCompletenessJob()._build_candidate_groups(albums)
    candidate_groups = [
        g for g in groups
        if any(m['album_id'] == 'candidate' for m in g['members'])
    ]
    # Candidate is its OWN singleton group, never fused into A or B.
    assert len(candidate_groups) == 1
    assert [m['album_id'] for m in candidate_groups[0]['members']] == [
        'candidate'
    ]


def test_unambiguous_candidate_joins_its_single_group():
    """The complement: a candidate that resolves to exactly one canonical group
    joins it (the one-pass path produces the same membership as before)."""
    def _album(album_id, order, **extra):
        base = {
            'album_id': album_id, 'artist_id': 'artist-1',
            'album_title': album_id, 'actual_count': 1, '_scan_order': order,
            'spotify_album_id': '', 'itunes_album_id': '', 'deezer_album_id': '',
            'discogs_album_id': '', 'hydrabase_album_id': '',
            'musicbrainz_album_id': '', 'canonical_source': '',
            'canonical_album_id': '',
        }
        base.update(extra)
        return base

    albums = [
        _album('anchor', 0, spotify_album_id='shared-X',
               canonical_source='deezer', canonical_album_id='canonical-a'),
        _album('candidate', 1, spotify_album_id='shared-X'),
    ]
    groups = AlbumCompletenessJob()._build_candidate_groups(albums)
    assert len(groups) == 1
    assert sorted(m['album_id'] for m in groups[0]['members']) == [
        'anchor', 'candidate'
    ]


def test_fragment_grouping_never_crosses_artist_boundary(
    monkeypatch,
):
    db = _SharedMemoryDB()
    db.insert_artist("artist-1", "Artist One")
    db.insert_artist("artist-2", "Artist Two")
    db.insert_album(
        "anchor",
        "artist-1",
        "Album",
        spotify_id="shared-release",
        canonical_source="deezer",
        canonical_album_id="canonical-release",
    )
    db.insert_album(
        "other-artist",
        "artist-2",
        "Album",
        spotify_id="shared-release",
        api_track_count=1,
    )

    db.insert_track("anchor", 1, "Canonical Track 1")
    db.insert_track(
        "other-artist",
        2,
        "Canonical Track 2",
    )

    calls = []

    def get_tracks(source, album_id):
        calls.append((source, album_id))
        return _canonical_tracks(3)

    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        get_tracks,
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_primary_source",
        lambda: "spotify",
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_source_priority",
        lambda primary: ["spotify", "deezer"],
    )

    findings = []
    result = AlbumCompletenessJob().scan(
        _context(db, findings)
    )

    assert result.scanned == 2
    assert result.findings_created == 1
    assert calls == [("deezer", "canonical-release")]
    assert findings[0]["details"]["related_album_ids"] == [
        "anchor",
    ]


def test_musicbrainz_recording_id_validates_fragment(
    monkeypatch,
):
    db = _SharedMemoryDB()
    db.insert_artist("artist-1", "Artist")
    db.insert_album(
        "anchor",
        "artist-1",
        "Album",
        spotify_id="shared-release",
        musicbrainz_id="mb-release",
        canonical_source="musicbrainz",
        canonical_album_id="mb-release",
    )
    db.insert_album(
        "fragment",
        "artist-1",
        "Album Fragment",
        spotify_id="shared-release",
        api_track_count=1,
    )

    db.insert_track(
        "anchor",
        1,
        "Canonical Track 1",
        mbid="track-1",
    )
    db.insert_track(
        "fragment",
        99,
        "Wrong title and position",
        duration_ms=999999,
        mbid="track-2",
    )

    calls = []

    def get_tracks(source, album_id):
        calls.append((source, album_id))
        return _canonical_tracks(3)

    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        get_tracks,
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_primary_source",
        lambda: "spotify",
    )
    monkeypatch.setattr(
        album_completeness_module,
        "get_source_priority",
        lambda primary: ["spotify", "musicbrainz"],
    )

    findings = []
    result = AlbumCompletenessJob().scan(
        _context(db, findings)
    )

    assert result.scanned == 1
    assert result.findings_created == 1
    assert calls == [("musicbrainz", "mb-release")]

    details = findings[0]["details"]
    assert details["actual_tracks"] == 2
    assert details["related_album_ids"] == [
        "anchor",
        "fragment",
    ]
    assert [
        track["track_number"]
        for track in details["missing_tracks"]
    ] == [3]
