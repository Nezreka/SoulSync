"""Regression tests for the backend review — imports area (C4, H4, M5-M8, L2).

C4: `_stable_soulsync_id` MD5-mod-1e9 collisions silently dropped the
    second track (IntegrityError swallowed by the outer except).
H4: pipeline wrapper logged "assuming success" for failed contexts.
M5: deluxe grouping depended on track processing order.
M6: embedded track numbers lost to filename guesses.
M7: same-titled album by a different artist auto-adopted.
M8: sanitize_filename truncated by chars, not bytes (ENAMETOOLONG).
L2: $albumtype ignored album_info/track_info total_tracks.
"""

from __future__ import annotations

import sqlite3
import types
from types import SimpleNamespace

import pytest

from core.imports import side_effects
import core.imports.pipeline as import_pipeline
import core.runtime_state as runtime_state


# ---------------------------------------------------------------------------
# C4 — stable track-ID collision
# ---------------------------------------------------------------------------


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


def _make_soulsync_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE artists (
            id TEXT PRIMARY KEY, name TEXT, genres TEXT, thumb_url TEXT,
            server_source TEXT, created_at TEXT, updated_at TEXT,
            spotify_artist_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE albums (
            id TEXT PRIMARY KEY, artist_id TEXT, title TEXT, year INTEGER,
            thumb_url TEXT, genres TEXT, track_count INTEGER, duration INTEGER,
            server_source TEXT, created_at TEXT, updated_at TEXT,
            spotify_album_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT,
            track_number INTEGER, duration INTEGER, file_path TEXT,
            bitrate INTEGER, file_size INTEGER, track_artist TEXT,
            musicbrainz_recording_id TEXT, isrc TEXT,
            quality_profile_id INTEGER, server_source TEXT,
            created_at TEXT, updated_at TEXT, spotify_track_id TEXT,
            deezer_id TEXT
        )
        """
    )
    return conn


def _track_context(final_path, track_id, track_name, track_number):
    return {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Test Artist"},
        "album": {
            "id": "sp-album",
            "name": "Some Album",
            "release_date": "2024-01-01",
            "total_tracks": 2,
        },
        "track_info": {
            "id": track_id,
            "name": track_name,
            "track_number": track_number,
            "duration_ms": 200000,
            "artists": [{"name": "Test Artist"}],
            "_source": "spotify",
        },
        "_final_processed_path": final_path,
    }


@pytest.fixture()
def soulsync_db(monkeypatch):
    conn = _make_soulsync_db()
    monkeypatch.setattr(side_effects, "get_database", lambda: _FakeDB(conn))
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)
    return conn


# Real collision re-verified 2026-09-28 against current code.
_COLLIDING_A = "/music/Test Artist/Some Album/22873 - Track 22873.flac"
_COLLIDING_B = "/music/Test Artist/Some Album/28946 - Track 28946.flac"


def test_stable_id_collision_known_pair(soulsync_db):
    assert side_effects._stable_soulsync_id(_COLLIDING_A) == side_effects._stable_soulsync_id(_COLLIDING_B)


def test_colliding_track_ids_both_persist(soulsync_db):
    """C4: two different files hashing to the same stable ID must both
    land in the library with distinct IDs — not one row + one swallowed
    IntegrityError."""
    artist_context = {"name": "Test Artist", "genres": []}

    side_effects.record_soulsync_library_entry(
        _track_context(_COLLIDING_A, "sp-track-a", "Track 22873", 1),
        artist_context,
        {"album_name": "Some Album", "track_number": 1},
    )
    side_effects.record_soulsync_library_entry(
        _track_context(_COLLIDING_B, "sp-track-b", "Track 28946", 2),
        artist_context,
        {"album_name": "Some Album", "track_number": 2},
    )

    rows = soulsync_db.execute("SELECT id, file_path FROM tracks ORDER BY file_path").fetchall()
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]
    assert {r["file_path"] for r in rows} == {_COLLIDING_A, _COLLIDING_B}


def test_colliding_track_keeps_source_id_on_reminted_id(soulsync_db):
    """C4: the post-insert source-ID update must target the REMINTED id,
    not the collided one — otherwise the second track's source id lands
    on the first track's row (or nowhere)."""
    artist_context = {"name": "Test Artist", "genres": []}

    side_effects.record_soulsync_library_entry(
        _track_context(_COLLIDING_A, "sp-track-a", "Track 22873", 1),
        artist_context,
        {"album_name": "Some Album", "track_number": 1},
    )
    side_effects.record_soulsync_library_entry(
        _track_context(_COLLIDING_B, "sp-track-b", "Track 28946", 2),
        artist_context,
        {"album_name": "Some Album", "track_number": 2},
    )

    row_a = soulsync_db.execute(
        "SELECT spotify_track_id FROM tracks WHERE file_path = ?", (_COLLIDING_A,)
    ).fetchone()
    row_b = soulsync_db.execute(
        "SELECT spotify_track_id FROM tracks WHERE file_path = ?", (_COLLIDING_B,)
    ).fetchone()
    assert row_a["spotify_track_id"] == "sp-track-a"
    assert row_b["spotify_track_id"] == "sp-track-b"


def test_reimport_same_path_does_not_duplicate(soulsync_db):
    """The file_path pre-check must keep working: re-importing the SAME
    file refreshes it, it must not mint a second row."""
    artist_context = {"name": "Test Artist", "genres": []}
    ctx = _track_context(_COLLIDING_A, "sp-track-a", "Track 22873", 1)
    album_info = {"album_name": "Some Album", "track_number": 1}

    side_effects.record_soulsync_library_entry(ctx, artist_context, album_info)
    side_effects.record_soulsync_library_entry(ctx, artist_context, album_info)

    rows = soulsync_db.execute("SELECT id FROM tracks").fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# M5 — order-dependent deluxe grouping
# ---------------------------------------------------------------------------


def test_album_group_upgrade_signals_previous_name(monkeypatch):
    """M5: when a deluxe track upgrades the group, the upgrade must be
    signaled so the caller can relocate tracks already filed under the
    standard folder."""
    import core.imports.album_naming as album_naming

    monkeypatch.setattr(album_naming, "_album_name_cache", {})
    monkeypatch.setattr(album_naming, "_album_editions", {})

    artist_context = {"name": "Test Artist"}

    std_info: dict = {"album_name": "Greatest Hits"}
    assert album_naming.resolve_album_group(artist_context, std_info) == "Greatest Hits"
    assert "_album_group_upgraded_from" not in std_info

    dlx_info: dict = {"album_name": "Greatest Hits (Deluxe Edition)"}
    assert (
        album_naming.resolve_album_group(artist_context, dlx_info)
        == "Greatest Hits (Deluxe Edition)"
    )
    assert dlx_info.get("_album_group_upgraded_from") == "Greatest Hits"


def test_deluxe_upgrade_merges_previously_filed_standard_folder(tmp_path, monkeypatch):
    """M5 end-to-end: standard track filed first, deluxe track upgrades
    the group — the merge must move the standard folder's files into the
    deluxe folder so the album is not split by processing order."""
    import core.imports.album_naming as album_naming
    import core.imports.pipeline as pipeline_mod

    monkeypatch.setattr(album_naming, "_album_name_cache", {})
    monkeypatch.setattr(album_naming, "_album_editions", {})

    artist_context = {"name": "Test Artist"}
    library = tmp_path / "music"

    def fake_build_final_path(context, artist_ctx, album_info, file_ext, create_dirs=True):
        folder = library / "Test Artist" / album_info["album_name"]
        if create_dirs:
            folder.mkdir(parents=True, exist_ok=True)
        return str(folder / f"track{file_ext}"), False

    monkeypatch.setattr(pipeline_mod, "build_final_path_for_track", fake_build_final_path)
    monkeypatch.setattr(pipeline_mod, "transfer_root_for_context", lambda ctx: str(library))

    # Track 1 (standard): group resolves to the standard name; file it.
    std_info: dict = {"album_name": "Greatest Hits"}
    assert album_naming.resolve_album_group(artist_context, std_info) == "Greatest Hits"
    std_path, _ = fake_build_final_path(None, artist_context, std_info, ".flac")
    import os

    os.makedirs(os.path.dirname(std_path), exist_ok=True)
    with open(std_path, "wb") as fh:
        fh.write(b"audio-one")

    # Track 2 (deluxe): upgrades the group.
    dlx_info: dict = {"album_name": "Greatest Hits (Deluxe Edition)"}
    assert (
        album_naming.resolve_album_group(artist_context, dlx_info)
        == "Greatest Hits (Deluxe Edition)"
    )
    upgraded_from = dlx_info.pop("_album_group_upgraded_from", None)
    assert upgraded_from == "Greatest Hits"

    # The pipeline's merge step (runs after the upgrade, before filing the
    # deluxe track) relocates the already-filed standard tracks.
    new_final_path, _ = fake_build_final_path(None, artist_context, dlx_info, ".flac")
    pipeline_mod._merge_upgraded_album_folder(
        {}, artist_context, dlx_info, upgraded_from, ".flac", new_final_path
    )

    dlx_folder = library / "Test Artist" / "Greatest Hits (Deluxe Edition)"
    assert (dlx_folder / "track.flac").exists()
    assert not (library / "Test Artist" / "Greatest Hits").exists()


def test_merge_skips_when_old_folder_missing(tmp_path, monkeypatch):
    """M5: no standard folder on disk (nothing filed yet) — the merge is
    a no-op, not an error."""
    import core.imports.pipeline as pipeline_mod

    library = tmp_path / "music"

    def fake_build_final_path(context, artist_ctx, album_info, file_ext, create_dirs=True):
        folder = library / "Test Artist" / album_info["album_name"]
        return str(folder / f"track{file_ext}"), False

    monkeypatch.setattr(pipeline_mod, "build_final_path_for_track", fake_build_final_path)
    monkeypatch.setattr(pipeline_mod, "transfer_root_for_context", lambda ctx: str(library))

    new_final_path, _ = fake_build_final_path(
        None, {"name": "Test Artist"}, {"album_name": "Greatest Hits (Deluxe Edition)"}, ".flac"
    )
    # Must not raise.
    pipeline_mod._merge_upgraded_album_folder(
        {}, {"name": "Test Artist"}, {"album_name": "Greatest Hits (Deluxe Edition)"},
        "Greatest Hits", ".flac", new_final_path,
    )


# ---------------------------------------------------------------------------
# M6 — track-number precedence: embedded before filename
# ---------------------------------------------------------------------------


def test_m6_embedded_beats_filename_guess():
    """M6: a wrong filename guess must not override the source-written
    embedded tag. /01 - Track One.flac/ with embedded 5 resolves to 5."""
    from core.imports.track_number import resolve_track_number

    assert (
        resolve_track_number({}, {}, "/music/01 - Track One.flac", embedded_track_number=5)
        == 5
    )


def test_m6_metadata_still_beats_embedded():
    """M6: provider metadata keeps top precedence over the embedded tag."""
    from core.imports.track_number import resolve_track_number

    assert (
        resolve_track_number(
            {"track_number": 7}, {}, "/music/01 - Track One.flac", embedded_track_number=5
        )
        == 7
    )


def test_m6_filename_still_used_when_no_embedded():
    """M6: with no embedded tag, the filename guess still fills the gap."""
    from core.imports.track_number import resolve_track_number

    assert (
        resolve_track_number({}, {}, "/music/03 - Track Three.flac", embedded_track_number=None)
        == 3
    )


# ---------------------------------------------------------------------------
# M7 — wrong-artist album search guard
# ---------------------------------------------------------------------------


def _m7_result(name: str, artist_name: str = "", total_tracks: int = 0):
    from types import SimpleNamespace

    artists = [{"name": artist_name}] if artist_name else []
    return SimpleNamespace(name=name, artists=artists, total_tracks=total_tracks)


def test_m7_wrong_artist_exact_title_rejected():
    """M7: an exact title match must not clear the threshold on a clearly
    wrong artist. 'Target Artist' vs 'Some Other Band' scores 0.143 on the
    real similarity engine — far below any legitimate variant."""
    from core.imports.album_matching import (
        ALBUM_SEARCH_THRESHOLD,
        score_album_search_result,
    )

    score = score_album_search_result(
        _m7_result("Greatest Hits", "Some Other Band", total_tracks=12),
        "Greatest Hits",
        "Target Artist",
        12,
    )
    assert score < ALBUM_SEARCH_THRESHOLD, f"wrong-artist result scored {score}"


def test_m7_legitimate_artist_variant_still_passes():
    """M7: real artist spelling variants (AC/DC vs ACDC = 0.889) must keep
    passing — the guard targets wrong artists, not punctuation."""
    from core.imports.album_matching import (
        ALBUM_SEARCH_THRESHOLD,
        score_album_search_result,
    )

    score = score_album_search_result(
        _m7_result("Greatest Hits", "ACDC", total_tracks=12),
        "Greatest Hits",
        "AC/DC",
        12,
    )
    assert score >= ALBUM_SEARCH_THRESHOLD, f"legit variant scored {score}"


def test_m7_album_only_search_unaffected():
    """M7: with no search artist, the guard does not apply — album-only
    searches keep working exactly as before."""
    from core.imports.album_matching import (
        ALBUM_SEARCH_THRESHOLD,
        score_album_search_result,
    )

    score = score_album_search_result(
        _m7_result("Greatest Hits", "Some Other Band", total_tracks=12),
        "Greatest Hits",
        None,
        12,
    )
    assert score >= ALBUM_SEARCH_THRESHOLD, f"album-only search scored {score}"


def test_m7_missing_result_artist_unaffected():
    """M7: when the result carries no artist, there is nothing to guard on —
    the title score still decides."""
    from core.imports.album_matching import (
        ALBUM_SEARCH_THRESHOLD,
        score_album_search_result,
    )

    score = score_album_search_result(
        _m7_result("Greatest Hits", "", total_tracks=12),
        "Greatest Hits",
        "Target Artist",
        12,
    )
    assert score >= ALBUM_SEARCH_THRESHOLD, f"missing-artist result scored {score}"


# ---------------------------------------------------------------------------
# M8 — UTF-8 byte truncation
# ---------------------------------------------------------------------------


def test_m8_cjk_truncated_by_bytes_not_chars():
    """M8: 200 CJK chars = 600 UTF-8 bytes. Must truncate to <=200 BYTES
    (not 200 chars) without splitting a code point."""
    from core.imports.paths import sanitize_filename

    out = sanitize_filename("日" * 200)
    assert len(out.encode("utf-8")) <= 200
    out.encode("utf-8").decode("utf-8")  # raises if a code point was split
    assert out == "日" * 66  # 66 * 3 = 198 bytes; 67th would be 201


def test_m8_ascii_behavior_unchanged():
    """M8: pure-ASCII truncation still yields exactly 200 chars."""
    from core.imports.paths import sanitize_filename

    assert sanitize_filename("a" * 250) == "a" * 200


def test_m8_mixed_multibyte_never_splits():
    """M8: 199 ASCII bytes + one 3-byte char = 202 bytes. The char must be
    dropped whole, not split — result stays valid UTF-8."""
    from core.imports.paths import sanitize_filename

    out = sanitize_filename("a" * 199 + "日")
    assert len(out.encode("utf-8")) <= 200
    assert out == "a" * 199


def test_m8_nonempty_fallback_preserved():
    """M8: the '_' fallback for empty input survives byte truncation."""
    from core.imports.paths import sanitize_filename

    assert sanitize_filename("   ") == "_"


# ---------------------------------------------------------------------------
# L2 — $albumtype total-track source chain
# ---------------------------------------------------------------------------


def _l2_config(tmp_path):
    class _Config:
        def __init__(self, values):
            self._values = values

        def get(self, key, default=None):
            return self._values.get(key, default)

    return _Config(
        {
            "soulseek.transfer_path": str(tmp_path / "Transfer"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$album [$albumtype]/$track - $title",
                "single_path": "$artist/$artist - $title",
            },
            "file_organization.collab_artist_mode": "first",
            "file_organization.disc_label": "Disc",
        }
    )


def test_l2_albumtype_reads_track_info_total_tracks(tmp_path, monkeypatch):
    """L2: $albumtype's track count must resolve album_context ->
    track_info -> album_info -> 0 (the chain import album building uses in
    context.py). Here album_context lacks total_tracks but track_info has 2,
    so a bare 'album' type must display as Single, not Album."""
    import core.imports.paths as import_paths

    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _l2_config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    context = {
        "artist": {"name": "Lenka"},
        "album": {
            "name": "The Show Single",
            "id": "album-1",
            "album_type": "album",  # bare 'album' — not a signal by itself
            "artists": [{"name": "Lenka"}],
            # NOTE: no total_tracks here — the L2 gap.
        },
        "track_info": {
            "name": "The Show",
            "id": "t1",
            "track_number": 1,
            "disc_number": 1,
            "total_tracks": 2,  # <-- the count the path builder must find
            "artists": [{"name": "Lenka"}],
        },
        "original_search_result": {
            "title": "The Show",
            "clean_title": "The Show",
            "clean_album": "The Show Single",
            "clean_artist": "Lenka",
            "artists": [{"name": "Lenka"}],
        },
        "source": "deezer",
        "is_album_download": False,
    }

    final_path, _ = import_paths.build_final_path_for_track(
        context,
        {"name": "Lenka"},
        {"is_album": True, "album_name": "The Show Single", "track_number": 1, "disc_number": 1},
        ".flac",
        create_dirs=False,
    )
    assert "[Single]" in final_path, f"$albumtype did not use track_info count: {final_path}"


def test_l2_albumtype_falls_back_to_album_info(tmp_path, monkeypatch):
    """L2: when neither album_context nor track_info carries the count,
    album_info's total_tracks is the next source before the 0 default."""
    import core.imports.paths as import_paths

    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _l2_config(tmp_path))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    context = {
        "artist": {"name": "Lenka"},
        "album": {"name": "The Show Single", "id": "album-1", "album_type": "album",
                  "artists": [{"name": "Lenka"}]},
        "track_info": {"name": "The Show", "id": "t1", "track_number": 1,
                       "disc_number": 1, "artists": [{"name": "Lenka"}]},
        "original_search_result": {
            "title": "The Show", "clean_title": "The Show",
            "clean_album": "The Show Single", "clean_artist": "Lenka",
            "artists": [{"name": "Lenka"}],
        },
        "source": "deezer",
        "is_album_download": False,
    }

    final_path, _ = import_paths.build_final_path_for_track(
        context,
        {"name": "Lenka"},
        {"is_album": True, "album_name": "The Show Single", "track_number": 1,
         "disc_number": 1, "total_tracks": 5},
        ".flac",
        create_dirs=False,
    )
    assert "[EP]" in final_path, f"$albumtype did not use album_info count: {final_path}"


# ---------------------------------------------------------------------------
# L3 — repeated artwork fetch across an album's tracks
# ---------------------------------------------------------------------------


def test_l3_art_bytes_fetched_once_per_album(monkeypatch):
    """L3: embed_album_art_metadata is called per track; the same cover URL
    must be fetched once per album, not once per track."""
    import core.metadata.artwork as artwork

    fetch_calls = []

    def fake_fetch(url):
        fetch_calls.append(url)
        return b"\xff\xd8" + b"\x00" * 5000, "image/jpeg"

    monkeypatch.setattr(artwork, "_fetch_art_bytes", fake_fetch)

    class Cfg:
        def get(self, key, default=None):
            return {
                "metadata_enhancement.min_art_size": 1000,
                "metadata_enhancement.album_art_order": [],
                "metadata_enhancement.prefer_caa_art": False,
            }.get(key, default)

    monkeypatch.setattr(artwork, "get_config_manager", lambda: Cfg())
    monkeypatch.setattr(artwork, "get_mutagen_symbols", lambda: {"x": 1})
    monkeypatch.setattr(
        artwork, "_write_embedded_art", lambda f, data, mime, sym: True
    )

    metadata = {
        "album": "L3 Test Album",
        "artist": "L3 Test Artist",
        "album_artist": "L3 Test Artist",
        "musicbrainz_release_id": "l3-release-mbid",
        "album_art_url": "https://example.com/l3-cover.jpg",
    }
    for i in range(3):
        artwork.embed_album_art_metadata(f"/tmp/l3-track{i:02d}.flac", metadata)

    assert len(fetch_calls) == 1, f"same cover fetched {len(fetch_calls)}x for 3 tracks"


def test_l3_art_cache_does_not_cache_failures(monkeypatch):
    """L3: a failed fetch must not poison the cache — the next track retries
    the network instead of replaying the failure."""
    import core.metadata.artwork as artwork

    fetch_calls = []
    fail_first = {"n": 0}

    def fake_fetch(url):
        fetch_calls.append(url)
        fail_first["n"] += 1
        if fail_first["n"] == 1:
            return None, None
        return b"\xff\xd8" + b"\x00" * 5000, "image/jpeg"

    monkeypatch.setattr(artwork, "_fetch_art_bytes", fake_fetch)

    class Cfg:
        def get(self, key, default=None):
            return {
                "metadata_enhancement.min_art_size": 1000,
                "metadata_enhancement.album_art_order": [],
                "metadata_enhancement.prefer_caa_art": False,
            }.get(key, default)

    monkeypatch.setattr(artwork, "get_config_manager", lambda: Cfg())
    monkeypatch.setattr(artwork, "get_mutagen_symbols", lambda: {"x": 1})
    monkeypatch.setattr(
        artwork, "_write_embedded_art", lambda f, data, mime, sym: True
    )

    metadata = {
        "album": "L3 Failure Album",
        "artist": "L3 Test Artist",
        "album_artist": "L3 Test Artist",
        "musicbrainz_release_id": "l3-fail-mbid",
        "album_art_url": "https://example.com/l3-fail-cover.jpg",
    }
    artwork.embed_album_art_metadata("/tmp/l3-fail-01.flac", dict(metadata))
    artwork.embed_album_art_metadata("/tmp/l3-fail-02.flac", dict(metadata))

    # First track: release URL fails, fallback URL succeeds (2 fetches).
    # Second track: release URL retried (not a cached failure), then the
    # fallback URL is served from cache (1 fetch). Total 3, not 2.
    assert len(fetch_calls) == 3, f"expected retry-after-failure, got {len(fetch_calls)} fetches"


def test_l3_art_cache_is_album_scoped(monkeypatch):
    """L3: two different albums with (hypothetically) the same art URL get
    separate cache entries — one album's bytes are never served to another."""
    import core.metadata.artwork as artwork

    fetch_calls = []

    def fake_fetch(url):
        fetch_calls.append(url)
        return b"\xff\xd8" + b"\x00" * 5000, "image/jpeg"

    monkeypatch.setattr(artwork, "_fetch_art_bytes", fake_fetch)

    class Cfg:
        def get(self, key, default=None):
            return {
                "metadata_enhancement.min_art_size": 1000,
                "metadata_enhancement.album_art_order": [],
                "metadata_enhancement.prefer_caa_art": False,
            }.get(key, default)

    monkeypatch.setattr(artwork, "get_config_manager", lambda: Cfg())
    monkeypatch.setattr(artwork, "get_mutagen_symbols", lambda: {"x": 1})
    monkeypatch.setattr(
        artwork, "_write_embedded_art", lambda f, data, mime, sym: True
    )

    def metadata_for(album):
        return {
            "album": album,
            "artist": "L3 Test Artist",
            "album_artist": "L3 Test Artist",
            "musicbrainz_release_id": f"l3-{album}-mbid",
            "album_art_url": "https://example.com/shared-cover.jpg",
        }

    artwork.embed_album_art_metadata("/tmp/l3-a.flac", metadata_for("Album One"))
    artwork.embed_album_art_metadata("/tmp/l3-b.flac", metadata_for("Album Two"))

    # Different release MBIDs -> different release URLs -> 2 fetches.
    assert len(fetch_calls) == 2


# ---------------------------------------------------------------------------
# L4 — repeated MusicBrainz artist lookups
# ---------------------------------------------------------------------------


def _l4_fake_service(calls):
    class FakeClient:
        def get_artist(self, mbid, includes=None):
            calls.append(("get_artist", mbid))
            return {"genres": [{"name": "Rock", "count": 10}]}

    class FakeService:
        def __init__(self):
            self.mb_client = FakeClient()

        def match_artist(self, name, owned_titles=None):
            calls.append(("match_artist", name))
            return {"mbid": "l4-artist-mbid", "name": name}

    return FakeService()


def _l4_isolate_caches(monkeypatch):
    import core.metadata.source as source
    from collections import OrderedDict

    monkeypatch.setattr(source, "mb_artist_cache", OrderedDict())
    monkeypatch.setattr(source, "mb_artist_detail_cache", OrderedDict())


def test_l4_artist_mbid_lookup_cached_across_tracks(monkeypatch):
    """L4: match_artist is called per track; the same artist must hit the
    network once, not once per track."""
    import core.metadata.source as source

    calls = []
    _l4_isolate_caches(monkeypatch)
    svc = _l4_fake_service(calls)

    r1 = source._cached_mb_artist(svc, "L4 Test Artist")
    r2 = source._cached_mb_artist(svc, "L4 Test Artist")
    assert r1["mbid"] == r2["mbid"] == "l4-artist-mbid"
    assert calls.count(("match_artist", "L4 Test Artist")) == 1, calls


def test_l4_artist_details_lookup_cached_across_tracks(monkeypatch):
    """L4: the genre get_artist call repeats per track; the same MBID must
    hit the network once."""
    import core.metadata.source as source

    calls = []
    _l4_isolate_caches(monkeypatch)
    svc = _l4_fake_service(calls)

    d1 = source._cached_mb_artist_details(svc, "l4-artist-mbid")
    d2 = source._cached_mb_artist_details(svc, "l4-artist-mbid")
    assert d1 == d2
    assert d1["genres"] == [{"name": "Rock", "count": 10}]
    assert calls.count(("get_artist", "l4-artist-mbid")) == 1, calls


def test_l4_artist_cache_key_normalized(monkeypatch):
    """L4: 'l4 artist' vs 'L4 Artist' vs '  L4 Artist ' are the same artist —
    one lookup, not three."""
    import core.metadata.source as source

    calls = []
    _l4_isolate_caches(monkeypatch)
    svc = _l4_fake_service(calls)

    source._cached_mb_artist(svc, "l4 artist")
    source._cached_mb_artist(svc, "L4 Artist")
    source._cached_mb_artist(svc, "  L4 Artist ")
    assert len([c for c in calls if c[0] == "match_artist"]) == 1, calls


# ---------------------------------------------------------------------------
# H4 — wrapper falsely marks completion
# ---------------------------------------------------------------------------


@pytest.fixture()
def _isolate_runtime_state():
    snapshot = {
        "tasks": dict(runtime_state.download_tasks),
        "batches": dict(runtime_state.download_batches),
        "matched_ctx": dict(runtime_state.matched_downloads_context),
        "processed": set(runtime_state.processed_download_ids),
        "locks": dict(runtime_state.post_process_locks),
    }
    runtime_state.download_tasks.clear()
    runtime_state.download_batches.clear()
    runtime_state.matched_downloads_context.clear()
    runtime_state.processed_download_ids.clear()
    runtime_state.post_process_locks.clear()
    yield
    runtime_state.download_tasks.clear()
    runtime_state.download_tasks.update(snapshot["tasks"])
    runtime_state.download_batches.clear()
    runtime_state.download_batches.update(snapshot["batches"])
    runtime_state.matched_downloads_context.clear()
    runtime_state.matched_downloads_context.update(snapshot["matched_ctx"])
    runtime_state.processed_download_ids.clear()
    runtime_state.processed_download_ids.update(snapshot["processed"])
    runtime_state.post_process_locks.clear()
    runtime_state.post_process_locks.update(snapshot["locks"])


def _wrapper_runtime(completion_calls):
    return types.SimpleNamespace(
        automation_engine=None,
        on_download_completed=lambda batch, task, success: completion_calls.append(
            (batch, task, success)
        ),
        web_scan_manager=None,
        repair_worker=None,
    )


def _seed_wrapper_task(task_id="t1", batch_id="b1"):
    runtime_state.download_tasks[task_id] = {
        "task_id": task_id,
        "batch_id": batch_id,
        "status": "downloading",
        "track_info": {"name": "Some Track"},
    }


def _run_wrapper(context, monkeypatch, inner, completion_calls, task_id="t1", batch_id="b1"):
    runtime = _wrapper_runtime(completion_calls)
    monkeypatch.setattr(import_pipeline, "post_process_matched_download", inner)
    import_pipeline.post_process_matched_download_with_verification(
        "test::ctx", context, "/fake/source.flac", task_id, batch_id, runtime
    )


def test_wrapper_fails_on_context_failure_msg(_isolate_runtime_state, monkeypatch):
    """H4: the inner pipeline rejected the file (quality-upgrade guard)
    and left no destination — the wrapper must mark the task FAILED with
    the inner reason, never 'assume success'."""
    completion_calls = []
    _seed_wrapper_task()

    def fake_inner(context_key, context, file_path, runtime, metadata_runtime=None):
        context["_context_failure_msg"] = (
            "Incoming file is not a verified improvement under the quality profile"
        )

    _run_wrapper({"task_id": "t1", "batch_id": "b1"}, monkeypatch, fake_inner, completion_calls)

    task = runtime_state.download_tasks["t1"]
    assert task["status"] == "failed"
    assert "not a verified improvement" in task["error_message"]
    assert ("b1", "t1", True) not in completion_calls
    assert ("b1", "t1", False) in completion_calls


def test_wrapper_fails_on_failure_msg_despite_existing_destination(
    _isolate_runtime_state, monkeypatch, tmp_path
):
    """H4 (critical): _final_processed_path is assigned BEFORE the
    quality-upgrade rejection, so a rejected import can carry BOTH a
    failure message AND a destination pointing at an existing library
    file. The wrapper must honor the failure — the existing destination
    must not flip it to Completed."""
    completion_calls = []
    _seed_wrapper_task()

    existing = tmp_path / "existing.flac"
    existing.write_bytes(b"audio")

    def fake_inner(context_key, context, file_path, runtime, metadata_runtime=None):
        context["_final_processed_path"] = str(existing)
        context["_context_failure_msg"] = (
            "Incoming file is not a verified improvement under the quality profile"
        )

    _run_wrapper({"task_id": "t1", "batch_id": "b1"}, monkeypatch, fake_inner, completion_calls)

    task = runtime_state.download_tasks["t1"]
    assert task["status"] == "failed"
    assert "not a verified improvement" in task["error_message"]
    assert ("b1", "t1", True) not in completion_calls
    assert ("b1", "t1", False) in completion_calls


def test_wrapper_fails_unrecognized_no_destination_outcome(_isolate_runtime_state, monkeypatch):
    """H4: the inner pipeline finished with no destination, no failure
    flag and no success flag — an unrecognized outcome. Marking that
    Completed is how missing files showed ✅. It must fail instead."""
    completion_calls = []
    _seed_wrapper_task()

    _run_wrapper(
        {"task_id": "t1", "batch_id": "b1"},
        monkeypatch,
        lambda *a, **k: None,
        completion_calls,
    )

    task = runtime_state.download_tasks["t1"]
    assert task["status"] == "failed"
    assert ("b1", "t1", True) not in completion_calls
    assert ("b1", "t1", False) in completion_calls


def test_wrapper_completes_pipeline_succeeded_without_path(_isolate_runtime_state, monkeypatch):
    """H4: a legitimate no-destination success (e.g. redundant source
    removal where the destination already exists) carries
    _pipeline_import_succeeded — it must still complete."""
    completion_calls = []
    _seed_wrapper_task()

    def fake_inner(context_key, context, file_path, runtime, metadata_runtime=None):
        context["_pipeline_import_succeeded"] = True

    _run_wrapper({"task_id": "t1", "batch_id": "b1"}, monkeypatch, fake_inner, completion_calls)

    assert runtime_state.download_tasks["t1"]["status"] == "completed"
    assert ("b1", "t1", True) in completion_calls


def test_missing_artist_leaves_failure_flag_and_fails_task(
    tmp_path, _isolate_runtime_state, monkeypatch
):
    """H4 (inner): the missing-artist early return must leave
    _context_failure_msg so the wrapper fails the task instead of
    logging 'cannot verify, assuming success'."""
    source = tmp_path / "track.flac"
    source.write_bytes(b"audio-data" * 64)

    # Bypass the pre-artist stages: this test is about the missing-artist
    # early return, not integrity/audio/acoustid behavior.
    monkeypatch.setattr(import_pipeline, "_should_skip_quarantine_check", lambda ctx, gate: True)
    monkeypatch.setattr(import_pipeline, "_resolve_context_quality_profile", lambda ctx: {})
    monkeypatch.setattr(import_pipeline, "get_audio_quality_string", lambda *a, **k: "")
    fake_acoustid = types.ModuleType("core.acoustid_verification")

    class _NoAcoustid:
        def quick_check_available(self):
            return False, "disabled"

    fake_acoustid.AcoustIDVerification = _NoAcoustid
    import sys

    monkeypatch.setitem(sys.modules, "core.acoustid_verification", fake_acoustid)

    completion_calls = []
    _seed_wrapper_task()
    context = {
        "source": "spotify",
        "track_info": {"name": "Some Track"},
        "task_id": "t1",
        "batch_id": "b1",
        # NOTE: no artist context at all.
    }
    runtime = _wrapper_runtime(completion_calls)
    import_pipeline.post_process_matched_download_with_verification(
        "test::ctx", context, str(source), "t1", "b1", runtime
    )

    assert context.get("_context_failure_msg") == "Missing artist context"
    assert runtime_state.download_tasks["t1"]["status"] == "failed"
    assert ("b1", "t1", True) not in completion_calls
