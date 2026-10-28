import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace


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

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

from core.repair_worker import RepairWorker


def test_incomplete_album_auto_fill_skips_source_artist_mismatch(tmp_path):
    """Album Completeness must never fill a target album with another artist."""
    existing_path = tmp_path / "Gut" / "Light Years" / "01 - Wound Fuck.flac"
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(b"existing")

    candidate_path = tmp_path / "Jamiroquai" / "Light Years.flac"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"candidate")

    class _FakeDB:
        def __init__(self):
            self.search_calls = []
            self.wishlist = []

        def get_tracks_by_album(self, album_id):
            if album_id == "target-album":
                return [
                    SimpleNamespace(
                        id="target-existing-1",
                        album_id="target-album",
                        artist_id="gut",
                        title="Wound Fuck",
                        track_number=1,
                        file_path=str(existing_path),
                        bitrate=9999,
                    ),
                ]
            return []

        def search_tracks(self, title="", artist="", limit=50, server_source=None):
            self.search_calls.append((title, artist, limit, server_source))
            return [
                SimpleNamespace(
                    id="candidate-1",
                    album_id="single-album",
                    artist_id="jamiroquai",
                    artist_name="Jamiroquai",
                    title="Light Years",
                    track_number=1,
                    file_path=str(candidate_path),
                    bitrate=9999,
                ),
            ]

        def add_to_wishlist(self, *args, **kwargs):
            self.wishlist.append((args, kwargs))

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _FakeDB()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None

    result = worker._fix_incomplete_album(
        "album",
        "target-album",
        None,
        {
            "album_id": "target-album",
            "album_title": "Light Years",
            "artist": "Gut",
            "missing_tracks": [
                {
                    "name": "Light Years",
                    "track_number": 2,
                    "disc_number": 1,
                    "source": "spotify",
                    "source_track_id": "sp-light-years",
                    "artists": ["Jamiroquai"],
                },
            ],
        },
    )

    assert result["success"] is False
    assert result["fixed"] == 0
    assert result["skipped"] == 1
    assert result["details"][0]["reason"] == "source artist does not match target album artist"
    assert worker.db.search_calls == []
    assert worker.db.wishlist == []


def test_incomplete_album_auto_fill_rejects_wrong_artist_candidate(tmp_path):
    """Exact title is not enough when the candidate artist differs."""
    existing_path = tmp_path / "album" / "01 - Existing.flac"
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(b"existing")
    candidate_path = tmp_path / "wrong" / "02 - Light Years.flac"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"wrong")

    class _FakeDB:
        def __init__(self):
            self.wishlist = []

        def get_tracks_by_album(self, album_id):
            if album_id == "target-album":
                return [
                    SimpleNamespace(
                        id="target-existing-1",
                        album_id="target-album",
                        artist_id="jamiroquai",
                        title="Existing",
                        track_number=1,
                        file_path=str(existing_path),
                        bitrate=9999,
                    ),
                ]
            return []

        def search_tracks(self, title="", artist="", limit=50, server_source=None):
            return [
                SimpleNamespace(
                    id="candidate-1",
                    album_id="wrong-album",
                    artist_id="gut",
                    artist_name="Gut",
                    title="Light Years",
                    track_number=1,
                    file_path=str(candidate_path),
                    bitrate=9999,
                ),
            ]

        def add_to_wishlist(self, *args, **kwargs):
            self.wishlist.append((args, kwargs))

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _FakeDB()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None

    result = worker._fix_incomplete_album(
        "album",
        "target-album",
        None,
        {
            "album_id": "target-album",
            "album_title": "Light Years",
            "artist": "Jamiroquai",
            "spotify_album_id": "sp-album",
            "expected_tracks": 2,
            "missing_tracks": [
                {
                    "name": "Light Years",
                    "track_number": 2,
                    "disc_number": 1,
                    "source": "spotify",
                    "source_track_id": "sp-light-years",
                    "artists": ["Jamiroquai"],
                },
            ],
        },
    )

    assert result["success"] is True
    assert result["fixed"] == 0
    assert result["wishlisted"] == 1
    assert worker.db.wishlist


def test_perform_album_fill_copy_branch_generates_track_id(tmp_path, monkeypatch):
    src_path = tmp_path / "source.flac"
    src_path.write_bytes(b"fake-audio")
    album_folder = tmp_path / "album"
    db_path = tmp_path / "tracks.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE tracks (
                id TEXT PRIMARY KEY,
                album_id TEXT NOT NULL,
                artist_id TEXT NOT NULL,
                title TEXT NOT NULL,
                track_number INTEGER,
                duration INTEGER,
                file_path TEXT,
                bitrate INTEGER,
                server_source TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            ("target-track-1", "target-album", "target-artist", "Existing Track", 1, 180000, str(src_path), 320, "navidrome"),
        )
        conn.commit()

    class _FakeDB:
        def _get_connection(self):
            return sqlite3.connect(db_path)

        def get_tracks_by_album(self, album_id):
            return [
                SimpleNamespace(
                    id="source-track-1",
                    album_id="source-album",
                    artist_id="source-artist",
                    duration=180000,
                    file_path=str(src_path),
                    bitrate=320,
                    server_source="soulsync",
                ),
                SimpleNamespace(
                    id="source-track-2",
                    album_id="source-album",
                    artist_id="source-artist",
                    duration=181000,
                    file_path=str(src_path),
                    bitrate=320,
                    server_source="soulsync",
                ),
            ]

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _FakeDB()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    worker._enhance_file_metadata = None
    worker._enhance_placed_track = lambda *args, **kwargs: None

    monkeypatch.setattr(
        "core.repair_worker.uuid.uuid4",
        lambda: SimpleNamespace(hex="deadbeefcafebabe"),
    )

    result = worker._perform_album_fill(
        candidate=SimpleNamespace(
            id="source-track-1",
            album_id="source-album",
            artist_id="source-artist",
            duration=180000,
            file_path=str(src_path),
            bitrate=320,
            server_source="soulsync",
        ),
        album_id="target-album",
        album_title="Target Album",
        artist_name="Target Artist",
        track_name="New Track",
        track_number=2,
        disc_number=1,
        album_folder=str(album_folder),
        filename_pattern="{num:02d} - {title}",
        download_folder=None,
    )

    assert result["success"] is True
    assert result["action"] == "copied"

    with sqlite3.connect(db_path) as verify_conn:
        row = verify_conn.execute(
            "SELECT id, title, file_path, server_source FROM tracks WHERE title = ?",
            ("New Track",),
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[0].startswith("album_fill_source-track-1_deadbeef")
        assert row[1] == "New Track"
        assert Path(row[2]).exists()
        assert row[3] == "navidrome"
        assert verify_conn.execute("SELECT COUNT(*) FROM tracks WHERE id IS NULL").fetchone()[0] == 0


def _soundtrack_worker(tmp_path, album_artist, monkeypatch, tracklist_artists):
    """a target album with one owned track, a deezer id, and a fake deezer
    tracklist credited to ``tracklist_artists``."""
    import core.repair_worker as rw

    existing = tmp_path / album_artist / "Album" / "01 - Owned.flac"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    fetched = []

    def _tracks(source, album_id):
        fetched.append((source, album_id))
        return {"items": [{"track_number": i + 1, "artists": [{"name": a}]}
                          for i, a in enumerate(tracklist_artists)]}

    monkeypatch.setattr(rw, "get_album_tracks_for_source", _tracks)
    monkeypatch.setattr(rw, "get_source_priority", lambda primary: ["deezer"])

    class _FakeDB:
        def __init__(self):
            self.wishlist = []

        def get_tracks_by_album(self, album_id):
            return [SimpleNamespace(id="owned-1", album_id=album_id, artist_id="a",
                                    title="Owned", track_number=1,
                                    file_path=str(existing), bitrate=9999)]

        def search_tracks(self, title="", artist="", limit=50, server_source=None):
            return []

        def add_to_wishlist(self, *args, **kwargs):
            self.wishlist.append((args, kwargs))

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _FakeDB()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    return worker, fetched


def _guest_finding(album_artist, guest):
    return {
        "album_id": "target-album", "album_title": "How to Train Your Dragon 2",
        "artist": album_artist, "primary_source": "deezer", "deezer_album_id": "7845235",
        "missing_tracks": [
            {"name": "Into a Fantasy", "track_number": 20, "disc_number": 1,
             "source": "deezer", "source_track_id": "78917815", "artists": [guest]},
            {"name": "Heat and Havoc", "track_number": 21, "disc_number": 1,
             "source": "deezer", "source_track_id": "403093682", "artists": [guest]},
        ],
    }


def test_soundtrack_guest_track_is_not_skipped(tmp_path, monkeypatch):
    # storm's report: Alexander Rybak's song on John Powell's How to Train Your
    # Dragon 2 was skipped for not being John Powell. the album's own tracklist
    # is his, so the guest track belongs on it. wishlisting it takes an
    # explicit fix of this one album finding — that is the click storm made.
    worker, fetched = _soundtrack_worker(
        tmp_path, "John Powell", monkeypatch,
        ["John Powell"] * 19 + ["Alexander Rybak", "Alexander Rybak"])
    finding = _guest_finding("John Powell", "Alexander Rybak")
    finding["_explicit_single_fix"] = True
    result = worker._fix_incomplete_album("album", "target-album", None, finding)
    assert result["skipped"] == 0
    assert result["wishlisted"] == 2
    assert fetched == [("deezer", "7845235")]          # one tracklist fetch for the album


def test_soundtrack_guest_track_is_not_wishlisted_without_an_explicit_fix(tmp_path, monkeypatch):
    # seadogs' report: a bulk Fix All over compilation/soundtrack findings
    # wishlisted every other artist on those albums, and the wishlist then
    # auto-downloaded them. the guest track still belongs on the album —
    # it just may not be wishlisted unless this album was fixed on its own.
    # a direct handler call carries no explicit stamp (the safe default).
    worker, fetched = _soundtrack_worker(
        tmp_path, "John Powell", monkeypatch,
        ["John Powell"] * 19 + ["Alexander Rybak", "Alexander Rybak"])
    result = worker._fix_incomplete_album(
        "album", "target-album", None, _guest_finding("John Powell", "Alexander Rybak"))
    assert result["wishlisted"] == 0
    assert result["skipped"] == 2
    assert result["other_artist_skipped"] == 2
    assert worker.db.wishlist == []
    assert all(
        d["reason"] == "other artist track not wishlisted without an explicit fix of this album"
        for d in result["details"])
    assert "other artists were not added to the wishlist" in result["error"]


def test_guest_track_is_still_filled_from_the_library_without_an_explicit_fix(tmp_path, monkeypatch):
    # the gate is on wishlisting, not on filling: a guest track whose file
    # is already in the library is still copied onto its album in a bulk
    # run — that moves the user's own music, it queues no download.
    worker, fetched = _soundtrack_worker(
        tmp_path, "John Powell", monkeypatch,
        ["John Powell"] * 19 + ["Alexander Rybak", "Alexander Rybak"])
    candidate_path = tmp_path / "Rybak" / "Into a Fantasy.flac"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"candidate")
    worker.db.search_tracks = lambda title="", artist="", limit=50, server_source=None: [
        SimpleNamespace(
            id="candidate-1", album_id="other-album", artist_id="rybak",
            artist_name="Alexander Rybak", title="Into a Fantasy",
            track_number=1, file_path=str(candidate_path), bitrate=9999,
        ),
    ] if title == "Into a Fantasy" else []
    worker._perform_album_fill = lambda *args, **kwargs: {
        "success": True, "action": "copied", "message": "filled from library"}
    result = worker._fix_incomplete_album(
        "album", "target-album", None, _guest_finding("John Powell", "Alexander Rybak"))
    assert result["fixed"] == 1
    assert result["wishlisted"] == 0
    assert result["other_artist_skipped"] == 1
    assert worker.db.wishlist == []


def test_various_artists_compilation_is_not_wishlisted_without_an_explicit_fix(tmp_path, monkeypatch):
    # seadogs' headline case: an anjunabeats-style compilation credited to
    # Various Artists. the old gate blanket-passed every track on such an
    # album, so a bulk fix wishlisted (and then downloaded) all of them.
    worker, fetched = _soundtrack_worker(
        tmp_path, "Various Artists", monkeypatch, ["Various Artists"])
    finding = {
        "album_id": "target-album", "album_title": "Anjunabeats Volume 6",
        "artist": "Various Artists", "primary_source": "deezer",
        "deezer_album_id": "7845235",
        "missing_tracks": [
            {"name": "Made of Love", "track_number": 2, "disc_number": 1,
             "source": "deezer", "source_track_id": "t-yotto", "artists": ["YOTTO"]},
            {"name": "Mozart", "track_number": 3, "disc_number": 1,
             "source": "deezer", "source_track_id": "t-arty", "artists": ["ARTY"]},
        ],
    }
    result = worker._fix_incomplete_album("album", "target-album", None, finding)
    assert result["wishlisted"] == 0
    assert result["other_artist_skipped"] == 2
    assert worker.db.wishlist == []

    finding["_explicit_single_fix"] = True
    result = worker._fix_incomplete_album("album", "target-album", None, finding)
    assert result["wishlisted"] == 2
    assert len(worker.db.wishlist) == 2


class _NonClosingConn(sqlite3.Connection):
    def close(self):  # noqa: A003 - matching sqlite3's API on purpose
        pass


def _finding_backed_worker(tmp_path, monkeypatch):
    """A worker whose db is a real repair_findings table plus the album
    fakes, so a fix travels the path a user travels: fix_finding reading
    the stored finding, stamping it, and dispatching to the handler."""
    import json

    import core.repair_worker as rw

    existing = tmp_path / "John Powell" / "Album" / "01 - Owned.flac"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    monkeypatch.setattr(
        rw, "get_album_tracks_for_source",
        lambda source, album_id: {"items": [
            {"track_number": i + 1, "artists": [{"name": a}]}
            for i, a in enumerate(["John Powell"] * 19 + ["Alexander Rybak"] * 2)]})
    monkeypatch.setattr(rw, "get_source_priority", lambda primary: ["deezer"])

    conn = sqlite3.connect(":memory:", factory=_NonClosingConn)
    conn.execute("""
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL, finding_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'pending',
            entity_type TEXT, entity_id TEXT, file_path TEXT,
            title TEXT NOT NULL, description TEXT,
            details_json TEXT DEFAULT '{}', user_action TEXT,
            resolved_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_error TEXT
        )""")
    conn.execute("CREATE TABLE albums (id TEXT PRIMARY KEY, owner_profile_id INTEGER)")
    conn.commit()

    class _Db:
        def __init__(self):
            self.wishlist = []

        def _get_connection(self):
            return conn

        def get_tracks_by_album(self, album_id):
            return [SimpleNamespace(id="owned-1", album_id=album_id, artist_id="a",
                                    title="Owned", track_number=1,
                                    file_path=str(existing), bitrate=9999)]

        def search_tracks(self, title="", artist="", limit=50, server_source=None):
            return []

        def add_to_wishlist(self, *args, **kwargs):
            self.wishlist.append((args, kwargs))

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _Db()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None

    cur = conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, entity_type, entity_id, title, details_json) "
        "VALUES ('album_completeness', 'incomplete_album', 'album', 'target-album', 'incomplete', ?)",
        (json.dumps(_guest_finding("John Powell", "Alexander Rybak")),))
    conn.commit()
    return worker, conn, cur.lastrowid


def _finding_status(conn, fid):
    return conn.execute(
        "SELECT status, last_error FROM repair_findings WHERE id = ?", (fid,)).fetchone()


def test_guest_tracks_end_to_end_through_fix_finding(tmp_path, monkeypatch):
    # the path a user travels, both ways: fixing this one finding
    # wishlists the guest tracks and resolves it; a bulk fix of the same
    # finding wishlists nothing, counts it skipped (not failed), and
    # leaves it pending with the reason on the row.
    worker, conn, fid = _finding_backed_worker(tmp_path, monkeypatch)

    result = worker.fix_finding(fid, explicit=True)
    assert result["success"] is True
    assert result["wishlisted"] == 2
    assert len(worker.db.wishlist) == 2
    assert _finding_status(conn, fid)[0] == "resolved"

    conn.execute("UPDATE repair_findings SET status = 'pending' WHERE id = ?", (fid,))
    conn.commit()
    worker.db.wishlist.clear()

    out = worker.bulk_fix_findings()
    assert out["fixed"] == 0 and out["failed"] == 0
    assert out["skipped"] == 1 and out["other_artist_skipped"] == 2
    assert worker.db.wishlist == []
    status, last_error = _finding_status(conn, fid)
    assert status == "pending"
    assert "other artists were not added to the wishlist" in last_error


def test_other_artist_helper_edges():
    """The strict gate's fail-closed edges, pinned at the helper."""
    from core.repair_worker import _album_fill_track_is_other_artist as is_other

    # the album artist's own track, incl. one member of a duo credit
    assert is_other("John Powell", ["John Powell"]) is False
    assert is_other("John Lennon & Yoko Ono", ["Yoko Ono"]) is False
    assert is_other("John Lennon & Yoko Ono", ["John Lennon"]) is False
    assert is_other("Hans Zimmer", ["Hans Zimmer & Lisa Gerrard"]) is False
    # another artist entirely
    assert is_other("John Powell", ["Alexander Rybak"]) is True
    assert is_other("Above & Beyond", ["YOTTO"]) is True
    # a split member matches exactly, never on a near-name: the fuzzy
    # matcher scores "Beyond" vs "Beyonder" above its threshold, so
    # this only holds because member matching is exact.
    assert is_other("Above & Beyond", ["Beyond"]) is False
    assert is_other("Above & Beyond", ["Beyonder"]) is True
    # a feat credit on the album artist's own track is still theirs, even
    # when the band name carries the "&" the credit splitter breaks on
    assert is_other("Above & Beyond", ["Above & Beyond feat. Zoë Johnston"]) is False
    assert is_other("Daft Punk", ["Daft Punk (feat. Pharrell Williams)"]) is False
    assert is_other("Daft Punk", ["Pharrell Williams (feat. Daft Punk)"]) is True
    # no usable album artist: placeholders matching proves nothing
    assert is_other("", ["YOTTO"]) is True
    assert is_other(None, ["YOTTO"]) is True
    assert is_other("Unknown Artist", ["Unknown Artist"]) is True
    assert is_other("Unknown Artist", ["YOTTO"]) is True
    assert is_other("Unknown Artist", []) is True
    assert is_other("", []) is True
    # no track credit: thin metadata fails open on a normal album,
    # closed on a compilation where the payload would use the blanket
    assert is_other("John Powell", []) is False
    assert is_other("Various Artists", []) is True
    assert is_other("Soundtrack", []) is True
    # a track credited to the blanket label itself is the album's own
    assert is_other("Various Artists", ["Various Artists"]) is False
    assert is_other("Various", ["Various Artists"]) is False


def test_duo_album_solo_tracks_are_wishlisted_in_bulk(tmp_path, monkeypatch):
    # a duo album's members' solo-credited tracks are the album artist's
    # tracks, not other artists': the matcher used to split only the
    # track side of a combined credit, so these could never wishlist.
    worker, fetched = _soundtrack_worker(
        tmp_path, "John Lennon & Yoko Ono", monkeypatch,
        ["John Lennon", "Yoko Ono"])
    finding = {
        "album_id": "target-album", "album_title": "Double Fantasy",
        "artist": "John Lennon & Yoko Ono", "primary_source": "deezer",
        "deezer_album_id": "7845235",
        "missing_tracks": [
            {"name": "Watching the Wheels", "track_number": 20, "disc_number": 1,
             "source": "deezer", "source_track_id": "t-jl", "artists": ["John Lennon"]},
            {"name": "Kiss Kiss Kiss", "track_number": 21, "disc_number": 1,
             "source": "deezer", "source_track_id": "t-yo", "artists": ["Yoko Ono"]},
        ],
    }
    result = worker._fix_incomplete_album("album", "target-album", None, finding)
    assert result["wishlisted"] == 2
    assert worker.db.wishlist


def test_wrong_release_is_still_skipped_with_a_tracklist(tmp_path, monkeypatch):
    # gut's light years matched to jamiroquai's: the tracklist never credits gut
    worker, fetched = _soundtrack_worker(
        tmp_path, "Gut", monkeypatch, ["Jamiroquai", "Jamiroquai"])
    result = worker._fix_incomplete_album(
        "album", "target-album", None, _guest_finding("Gut", "Jamiroquai"))
    assert result["skipped"] == 2 and result["wishlisted"] == 0
    assert worker.db.wishlist == []
