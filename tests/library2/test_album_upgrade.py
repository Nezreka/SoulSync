"""Whole-album quality upgrades (asked for on Discord by hrk_hrk).

A quality profile with "Upgrade whole albums" on upgrades an album as one
release: the complete release or nothing, swapped in all at once, and never a
per-track download for that album. With the option off nothing changes.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from core.library2 import album_upgrade as au
from core.library2 import album_upgrade_sources as aus
from core.quality.model import AudioQuality
from tests.lib2_seed import row_conn

TRACKS = [("Intro", 200_000), ("Song Two", 180_000), ("Last One", 240_000)]
FLAC16 = [{"label": "FLAC 16-bit", "format": "flac", "bit_depth": 16, "min_sample_rate": 44100}]


class FakeDB:
    def __init__(self, path):
        self.database_path = path
        self.wishlist_adds = []

    def _get_connection(self):
        return row_conn(self.database_path)

    def add_to_wishlist(self, payload, **_kwargs):
        self.wishlist_adds.append(payload.get("id"))
        return True

    def remove_from_wishlist(self, *_args, **_kwargs):
        return True

    def is_blacklisted(self, _username, _filename):
        return False


@pytest.fixture
def library(tmp_path, monkeypatch):
    """One artist, one three-track album of MP3s on disk, a profile asking
    for FLAC with whole-album upgrades on, every track wanted."""
    from core.library2.schema import ensure_library_v2_schema
    from core.library2.wanted import recompute_wanted

    root = tmp_path / "music"
    album_dir = root / "Band" / "Record (2001)"
    album_dir.mkdir(parents=True)
    db_path = str(tmp_path / "lib2.db")
    conn = row_conn(db_path)
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    profile = cur.execute(
        "INSERT INTO quality_profiles(name, ranked_targets, upgrade_policy, upgrade_cutoff_index,"
        " upgrade_whole_albums) VALUES('Lossless', ?, 'until_cutoff', 0, 1)",
        (json.dumps(FLAC16),)).lastrowid
    artist = cur.execute(
        "INSERT INTO lib2_artists(name, sort_name, monitored, quality_profile_id,"
        " quality_profile_explicit) VALUES('Band', 'Band', 1, ?, 1)", (profile,)).lastrowid
    album = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, year, monitored)"
        " VALUES(?, 'Record', 'album', 2001, 1)", (artist,)).lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album, artist))
    track_ids, paths = [], []
    for number, (title, ms) in enumerate(TRACKS, 1):
        tid = cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, duration,"
            " monitored) VALUES(?,?,?,1,?,1)", (album, title, number, ms)).lastrowid
        cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)", (tid, artist))
        path = album_dir / f"{number:02d} - {title}.mp3"
        path.write_bytes(b"old mp3 " + title.encode())
        cur.execute(
            "INSERT INTO lib2_track_files(track_id, path, format, bitrate, sample_rate, size,"
            " is_primary) VALUES(?,?, 'mp3', 320, 44100, ?, 1)",
            (tid, str(path), path.stat().st_size))
        track_ids.append(tid)
        paths.append(str(path))
    conn.commit()
    from core.library2.monitor_rules import PROVENANCE_USER, record_rules
    record_rules(conn, "track", track_ids, True, PROVENANCE_USER, profile_id=1)
    recompute_wanted(conn, profile_id=1, track_ids=track_ids)
    conn.commit()
    conn.close()
    # never the configured library: everything stays in tmp_path
    monkeypatch.setattr(au, "library_root_for", lambda _pid, _paths: str(root))
    monkeypatch.setattr(au, "_library_roots", lambda _pid: [str(root)])
    db = FakeDB(db_path)
    return SimpleNamespace(db=db, root=str(root), album=album, profile=profile,
                           tracks=track_ids, paths=paths, album_dir=str(album_dir))


def _unit(lib):
    with lib.db._get_connection() as conn:
        return au.load_album_unit(conn, lib.album, profile_id=1)


def _files(durations=None, titles=None):
    titles = titles or [t for t, _ms in TRACKS]
    durations = durations or [ms / 1000 for _t, ms in TRACKS]
    return [{"path": f"/s/{i:02d} - {t}.flac", "relative_path": f"{i:02d} - {t}.flac",
             "title": t, "track_number": i, "disc_number": 1, "duration_seconds": d}
            for i, (t, d) in enumerate(zip(titles, durations), 1)]


def _rows(lib):
    with lib.db._get_connection() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT track_id, path, format FROM lib2_track_files ORDER BY track_id")]


def _disk(lib):
    return sorted(os.listdir(lib.album_dir))


# ── the candidate has to be the whole album ──────────────────────────────────

def test_a_release_with_every_track_is_complete(library):
    ok, reason, pairs = au.check_complete(_unit(library), _files())
    assert ok, reason
    assert sorted(pairs) == sorted(library.tracks)
    assert pairs[library.tracks[1]]["title"] == "Song Two"


def test_a_release_missing_a_track_is_not_complete(library):
    ok, reason, pairs = au.check_complete(_unit(library), _files()[:2])
    assert not ok and pairs == {}
    assert "2 tracks" in reason and "has 3" in reason


def test_a_release_with_a_bonus_track_is_another_edition(library):
    files = _files() + [{"path": "/s/04 - Bonus.flac", "relative_path": "04 - Bonus.flac",
                         "title": "Bonus", "track_number": 4, "disc_number": 1,
                         "duration_seconds": 100}]
    ok, reason, _ = au.check_complete(_unit(library), files)
    assert not ok and "4 tracks" in reason


def test_a_track_of_another_length_is_not_the_same_release(library):
    ok, reason, _ = au.check_complete(_unit(library), _files(durations=[200, 180, 300]))
    assert not ok
    assert "Last One" in reason and "5:00" in reason


def test_a_length_within_tolerance_still_matches(library):
    ok, reason, _ = au.check_complete(_unit(library), _files(durations=[202, 179, 243]))
    assert ok, reason


# ── the real files ───────────────────────────────────────────────────────────

def _good(_path, _ms):
    return SimpleNamespace(ok=True, reason="")


def _flac(_path):
    return AudioQuality(format="flac", bitrate=900, sample_rate=44100, bit_depth=16)


def test_files_that_reach_the_cutoff_pass(library):
    unit = _unit(library)
    _ok, _r, pairs = au.check_complete(unit, _files())
    ok, reason, qualities = au.validate_files(unit, pairs, probe=_flac, integrity=_good)
    assert ok, reason
    assert set(qualities) == set(library.tracks)


def test_a_file_below_the_cutoff_fails_the_album(library):
    unit = _unit(library)
    _ok, _r, pairs = au.check_complete(unit, _files())

    def probe(path):
        if "Song Two" in path:
            return AudioQuality(format="mp3", bitrate=320)
        return _flac(path)

    ok, reason, _ = au.validate_files(unit, pairs, probe=probe, integrity=_good)
    assert not ok
    assert "Song Two" in reason and "FLAC 16-bit" in reason


def test_a_file_that_does_not_decode_fails_the_album(library):
    unit = _unit(library)
    _ok, _r, pairs = au.check_complete(unit, _files())

    def integrity(path, _ms):
        return SimpleNamespace(ok="Last One" not in path, reason="damaged frames")

    ok, reason, _ = au.validate_files(unit, pairs, probe=_flac, integrity=integrity)
    assert not ok and "Last One" in reason and "decode" in reason


# ── the swap: all or nothing ─────────────────────────────────────────────────

def _staged(tmp_path, lib):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    out = {}
    for number, (tid, (title, _ms)) in enumerate(zip(lib.tracks, TRACKS), 1):
        path = staging / f"{number:02d} - {title}.flac"
        path.write_bytes(b"new flac " + title.encode() * 50)
        out[tid] = {"path": str(path)}
    return str(staging), out


def _plan(tmp_path, lib):
    unit = _unit(lib)
    staging, pairs = _staged(tmp_path, lib)
    items, reason = au.plan_swap(unit, pairs, {tid: _flac("") for tid in lib.tracks})
    assert items, reason
    return staging, items


def test_a_library_update_failure_puts_the_whole_album_back(library, tmp_path):
    staging, items = _plan(tmp_path, library)
    before_rows, before_disk = _rows(library), _disk(library)

    def broken_db(_items):
        raise RuntimeError("database is locked")

    ok, reason, _ = au.swap_album(items, journal_dir=staging, album_id=library.album,
                                  apply_db=broken_db, retire=lambda *_: None)

    assert not ok and "put back" in reason
    assert _disk(library) == before_disk
    assert _rows(library) == before_rows
    for path in library.paths:
        assert open(path, "rb").read().startswith(b"old mp3")
    assert not os.path.exists(os.path.join(staging, au.JOURNAL_NAME))


def test_a_copy_failure_halfway_leaves_the_album_untouched(library, tmp_path):
    staging, items = _plan(tmp_path, library)
    before = _disk(library)
    calls = []

    def copy(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("disk full")
        with open(src, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())

    ok, reason, _ = au.swap_album(items, journal_dir=staging, album_id=library.album,
                                  apply_db=lambda _i: pytest.fail("no db write"),
                                  retire=lambda *_: None, copy=copy)
    assert not ok and "disk full" in reason
    assert _disk(library) == before


def test_a_rename_failure_midway_restores_every_file(library, tmp_path, monkeypatch):
    staging, items = _plan(tmp_path, library)
    before = _disk(library)
    real_replace = os.replace
    seen = []

    def replace(src, dst):
        seen.append(src)
        if src.endswith(au.TEMP_SUFFIX) and len([s for s in seen if s.endswith(au.TEMP_SUFFIX)]) == 2:
            raise OSError("busy")
        return real_replace(src, dst)

    monkeypatch.setattr(au.os, "replace", replace)
    ok, _reason, _ = au.swap_album(items, journal_dir=staging, album_id=library.album,
                                   apply_db=lambda _i: pytest.fail("no db write"),
                                   retire=lambda *_: None)
    monkeypatch.setattr(au.os, "replace", real_replace)
    assert not ok
    assert _disk(library) == before
    for path in library.paths:
        assert open(path, "rb").read().startswith(b"old mp3")


def test_an_interrupted_swap_is_undone_at_the_next_start(library, tmp_path):
    from core.downloads.atomic_album_publish import STAGING_DIRNAME
    unit = _unit(library)
    staging = os.path.join(library.root, STAGING_DIRNAME, f"{au.STAGING_PREFIX}{library.album}-1")
    os.makedirs(staging)
    _dir, pairs = _staged(tmp_path, library)
    items, _ = au.plan_swap(unit, pairs, {})
    # the process died after the first file was swapped, before the library moved
    first = items[0]
    os.replace(first.old_local, first.backup)
    with open(first.new_local, "wb") as handle:
        handle.write(b"new")
    au._write_journal(os.path.join(staging, au.JOURNAL_NAME), library.album, "swapping", items)

    assert au.recover_interrupted_swaps([library.root], library.db) == 1
    assert _disk(library) == sorted(os.path.basename(p) for p in library.paths)
    assert not os.path.exists(staging)


# ── one album, end to end ────────────────────────────────────────────────────

def _fetcher(tmp_path_factory_dir, *, titles=None, flac=True):
    """A source offering one release that stages the given files."""
    def fetch_candidates(_ctx, _source):
        def fetch(staging):
            for number, title in enumerate(titles or [t for t, _ms in TRACKS], 1):
                ext = ".flac" if flac else ".mp3"
                with open(os.path.join(staging, f"{number:02d} - {title}{ext}"), "wb") as h:
                    h.write(b"new flac " + title.encode() * 50)
            return aus.Fetched()
        yield aus.Candidate("test release", fetch=fetch)
    return fetch_candidates


def _read(paths, root):
    durations = {t: ms / 1000 for t, ms in TRACKS}

    def tags(path):
        name = os.path.basename(path)
        number, title = name.split(" - ", 1)
        title = os.path.splitext(title)[0]
        return {"title": title, "track_number": int(number), "disc_number": 1}

    def length(path):
        title = os.path.splitext(os.path.basename(path).split(" - ", 1)[1])[0]
        return durations.get(title, 99)
    return au.inventory(paths, root, read_tags=tags, length=length)


def _run(lib, fetch, *, files=None, swap=au.swap_album):
    def validate(unit, pairs):
        return au.validate_files(unit, pairs, probe=_flac if files is None else files,
                                 integrity=_good)
    return au.upgrade_album(
        lib.db, lib.album, profile_id=1, sources=["test"], fetchers={"test": fetch},
        tagger=lambda *_a: None, swap=swap,
        validators={"inventory": _read, "files": validate})


def test_a_complete_release_replaces_the_album_in_one_go(library, tmp_path):
    outcome = _run(library, _fetcher(tmp_path))

    assert outcome.upgraded, outcome.reason
    assert _disk(library) == sorted(os.path.splitext(os.path.basename(p))[0] + ".flac"
                                    for p in library.paths)
    rows = _rows(library)
    assert {fmt for _t, _p, fmt in rows} == {"flac"}
    assert all(path.endswith(".flac") and os.path.isfile(path) for _t, path, _f in rows)
    deleted = os.path.join(library.root, "ss_deleted")
    kept = [n for _d, _s, names in os.walk(library.root) for n in names if n.endswith(".mp3")]
    assert len(kept) == 3, deleted  # the old files are in the deleted-files folder
    [attempt] = au.recent_attempts(library.db)
    assert attempt["outcome"] == au.OUTCOME_UPGRADED and attempt["source"] == "test"


def test_a_failed_validation_leaves_the_album_unchanged_and_says_why(library, tmp_path):
    before_rows, before_disk = _rows(library), _disk(library)

    def probe(path):
        return AudioQuality(format="mp3", bitrate=320) if "Song Two" in path else _flac(path)

    outcome = _run(library, _fetcher(tmp_path), files=probe,
                   swap=lambda *a, **k: pytest.fail("never swapped"))

    assert outcome.outcome == au.OUTCOME_NO_RELEASE
    assert "Song Two" in outcome.reason
    assert _rows(library) == before_rows and _disk(library) == before_disk
    from core.downloads.atomic_album_publish import STAGING_DIRNAME
    assert os.listdir(os.path.join(library.root, STAGING_DIRNAME)) == []
    [attempt] = au.recent_attempts(library.db)
    assert attempt["outcome"] == au.OUTCOME_NO_RELEASE and "Song Two" in attempt["reason"]


def test_no_complete_release_never_falls_back_to_single_tracks(library, tmp_path):
    outcome = _run(library, _fetcher(tmp_path, titles=["Intro", "Song Two"]))

    assert outcome.outcome == au.OUTCOME_NO_RELEASE
    assert library.db.wishlist_adds == []
    # and the wishlist would not take one of its tracks either
    from core.library2.wishlist_mirror import track_wishlist_payload
    with library.db._get_connection() as conn:
        payload = track_wishlist_payload(conn, library.tracks[0])
    assert payload["_should_queue"] is False
    assert payload["_source_info"]["quality_evaluation"] == "album_upgrade"


def test_a_failed_album_waits_before_it_is_looked_for_again(library, tmp_path):
    with library.db._get_connection() as conn:
        assert au.pending_album_ids(conn, profile_id=1) == [library.album]
    _run(library, _fetcher(tmp_path, titles=["Intro"]))
    with library.db._get_connection() as conn:
        assert au.pending_album_ids(conn, profile_id=1) == []
        assert au.pending_album_ids(conn, profile_id=1, retry_after_days=0) == [library.album]


def test_a_hand_tagged_track_keeps_the_whole_album_as_it_is(library, monkeypatch, tmp_path):
    import core.repair_jobs.base as base
    monkeypatch.setattr(base, "hand_tagged_path_keys", lambda _db: {"x"})
    monkeypatch.setattr(base, "is_hand_tagged_path",
                        lambda path, _keys: "Song Two" in str(path))
    outcome = _run(library, _fetcher(tmp_path))
    assert outcome.outcome == au.OUTCOME_SKIPPED and "hand tagged" in outcome.reason


# ── the option off: today's per-track upgrades ───────────────────────────────

def test_with_the_option_off_tracks_upgrade_one_by_one(library):
    from core.library2.wishlist_mirror import track_wishlist_payload, upgrade_candidate_track_ids
    with library.db._get_connection() as conn:
        conn.execute("UPDATE quality_profiles SET upgrade_whole_albums=0")
        conn.commit()
        payload = track_wishlist_payload(conn, library.tracks[0])
        per_track = upgrade_candidate_track_ids(conn, profile_id=1, definite_only=True,
                                                whole_albums=False)
        assert au.pending_album_ids(conn, profile_id=1) == []
    assert payload["_should_queue"] is True
    assert payload["_source_info"]["quality_evaluation"] == "upgrade_candidate"
    assert sorted(per_track) == sorted(library.tracks)


def test_with_the_option_on_the_per_track_pass_skips_the_album(library):
    from core.library2.wishlist_mirror import upgrade_candidate_track_ids
    with library.db._get_connection() as conn:
        assert upgrade_candidate_track_ids(conn, profile_id=1, definite_only=True,
                                           whole_albums=False) == []
        assert sorted(upgrade_candidate_track_ids(conn, profile_id=1, definite_only=True,
                                                  whole_albums=True)) == sorted(library.tracks)


def test_the_profile_round_trips_the_option(tmp_path):
    from database.music_database import MusicDatabase
    params = MusicDatabase._quality_profile_bundle_params({"upgrade_whole_albums": True})
    assert params["upgrade_whole_albums"] == 1
    # a save that never names it leaves the row's value alone
    assert MusicDatabase._quality_profile_bundle_params({})["upgrade_whole_albums"] is None


# ── the sources ──────────────────────────────────────────────────────────────

def test_enabled_sources_follow_the_download_settings():
    config = {"download_source.mode": "hybrid",
              "download_source.hybrid_order": ["hifi", "soulseek", "deezer_dl", "torrent", "qobuz"]}
    assert aus.enabled_sources(lambda k, d=None: config.get(k, d)) == [
        "soulseek", "deezer", "torrent", "qobuz"]
    assert aus.enabled_sources(lambda k, d=None: {"download_source.mode": "usenet"}.get(k, d)) == [
        "usenet"]


def test_a_soulseek_folder_without_every_track_is_turned_down_before_download(library, monkeypatch):
    def track(title, number):
        return SimpleNamespace(filename=f"Band/Record/{number:02d} - {title}.flac", title=title,
                               track_number=number, duration=None, username="peer",
                               audio_quality=AudioQuality(format="flac", bit_depth=16,
                                                          sample_rate=44100))
    short = SimpleNamespace(username="peer", album_path="Band/Record",
                            tracks=[track("Intro", 1), track("Song Two", 2)])

    class Client:
        downloads = []

        def is_configured(self):
            return True

        async def search(self, _query, timeout=30):
            return [], [short]

        def _rank_album_bundle_folders(self, albums, *_a, **_k):
            return albums

        def filter_results_by_quality_preference(self, tracks, profile_id=None):
            return tracks

        def download_album_to_staging(self, *_a, **_k):
            self.downloads.append(_a)
            return {"success": True}

    monkeypatch.setattr(aus, "_client", lambda _source: Client())
    ctx = aus.FetchContext(database=library.db, unit=_unit(library),
                           config_get=lambda *_a: None, emit=lambda _m: None,
                           check=au.check_complete)
    [candidate] = list(aus.soulseek_candidates(ctx))
    assert candidate.fetch is None and "2 tracks" in candidate.rejected
    assert Client.downloads == []


def test_scoped_search_upgrades_a_whole_album_profile_album_as_one(library, monkeypatch):
    flask = pytest.importorskip("flask")
    from tests.library2.test_scoped_search_endpoint import _await_job
    queued, dispatched = [], []
    monkeypatch.setattr(au, "queue_album_upgrades",
                        lambda _db, ids, profile_id=1, **_k: queued.extend(ids) or len(ids))
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app, get_database=lambda: library.db,
        config_get=lambda key, default=None: {"features.library_v2": True}.get(key, default),
        config_manager=None, profile_id_getter=lambda: 1,
        scoped_wishlist_search_dispatcher=lambda ids, _p: dispatched.append(ids) or {"success": True},
        scoped_track_search_dispatcher=lambda ids, _p: dispatched.append(ids) or {"success": True})
    client = app.test_client()

    response = client.post(f"/api/library/v2/albums/{library.album}/search")
    status = _await_job(client, response.get_json()["job_id"])

    assert status["error"] is None
    assert queued == [library.album]
    assert dispatched == []


def test_the_apply_upgrades_automation_logs_each_album(library, monkeypatch):
    from core.automation.handlers import apply_quality_upgrades as handler
    lines = []
    outcome = au.Outcome(library.album, au.OUTCOME_NO_RELEASE, name="Band - Record",
                         reason="no complete release passed (soulseek: it has 2 tracks)")
    monkeypatch.setattr(au, "run_album_upgrades", lambda *_a, **_k: [outcome])
    monkeypatch.setattr(handler, "until_cutoff_finding_ids", lambda *_a, **_k: [])
    deps = SimpleNamespace(get_database=lambda: library.db, logger=SimpleNamespace(
        warning=lambda *a: None, debug=lambda *a: None),
        update_progress=lambda _id, **kw: lines.append(kw.get("log_line")),
        bulk_fix_repair_findings=None)

    result = handler.auto_apply_quality_upgrades({"_profile_id": 1}, deps)

    assert result["albums_tried"] == 1 and result["albums_upgraded"] == 0
    assert any("Left Band - Record as it is" in (line or "") for line in lines)


def test_a_file_out_of_reach_keeps_the_whole_album_as_it_is(library, tmp_path):
    os.remove(library.paths[2])
    outcome = _run(library, _fetcher(tmp_path))
    assert outcome.outcome == au.OUTCOME_SKIPPED and "not reachable" in outcome.reason
    assert _rows(library)[0][2] == "mp3"
