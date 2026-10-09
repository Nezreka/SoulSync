"""Scan-level tests for SoulSync issue #1450 — edition preference (backend).

Covers the two issue scenarios end-to-end through
``scan_watchlist_artists``:

1. standard + deluxe released the same day, nothing owned → only the
   picked edition's tracks are wishlisted;
2. reissue of an owned album → one_standard wishlists nothing (never an
   extras-only folder), one_complete wishlists the full picked edition
   minus exact duplicates, "all" keeps today's extras-only behaviour.
"""

import sys
import types


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

if "core.matching_engine" not in sys.modules:
    matching_engine_mod = types.ModuleType("core.matching_engine")

    class _DummyMatchingEngine:
        def clean_title(self, title):
            return title

    matching_engine_mod.MusicMatchingEngine = _DummyMatchingEngine
    sys.modules["core.matching_engine"] = matching_engine_mod

import pytest  # noqa: E402

import core.library.track_identity as track_identity  # noqa: E402
import core.watchlist_scanner as watchlist_scanner_module  # noqa: E402
from core.watchlist_scanner import WatchlistScanner  # noqa: E402


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _PerAlbumMetadataService:
    """get_album returns a different payload per album id."""

    def __init__(self, payloads):
        self._payloads = dict(payloads)
        self.spotify = types.SimpleNamespace()
        self.itunes = types.SimpleNamespace()

    def get_album(self, album_id):
        return self._payloads[album_id]


def _album_payload(name, track_names, album_id, with_isrc=False):
    items = []
    for i, title in enumerate(track_names):
        item = {
            "id": f"{album_id}-t{i}",
            "name": title,
            "track_number": i + 1,
            "disc_number": 1,
            "artists": [{"name": "Artist One"}],
        }
        if with_isrc:
            item["isrc"] = f"ISRC-{i:03d}"
        items.append(item)
    return {"id": album_id, "name": name, "images": [], "tracks": {"items": items}}


def _build_artist():
    return types.SimpleNamespace(
        artist_name="Artist One",
        spotify_artist_id="sp-artist",
        itunes_artist_id=None,
        deezer_artist_id=None,
        discogs_artist_id=None,
        last_scan_timestamp=None,
        id=123,
        profile_id=11,
        include_albums=True,
        include_eps=True,
        include_singles=True,
        include_live=False,
        include_remixes=False,
        include_acoustic=False,
        include_compilations=False,
        include_instrumentals=False,
        lookback_days=7,
        image_url=None,
        auto_download=True,
        auto_download_pref=None,
    )


def _build_scanner(payloads):
    scanner = WatchlistScanner(
        metadata_service=_PerAlbumMetadataService(payloads)
    )
    scanner._database = types.SimpleNamespace()
    scanner._wishlist_service = types.SimpleNamespace()
    scanner._matching_engine = types.SimpleNamespace(clean_title=lambda t: t)
    return scanner


def _prime_scan(monkeypatch, scanner, albums, preference):
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ARTISTS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ALBUMS", 0)
    # Settings are read once per scan — drive them directly here.
    monkeypatch.setattr(
        watchlist_scanner_module, "_resolve_edition_preference",
        lambda explicit=None: preference,
    )
    monkeypatch.setattr(
        watchlist_scanner_module, "_resolve_prefer_explicit_edition",
        lambda: True,
    )
    monkeypatch.setattr(scanner, "_backfill_missing_ids", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "get_artist_image_url", lambda *a, **k: "")
    monkeypatch.setattr(
        scanner, "get_artist_discography_for_watchlist", lambda *a, **k: list(albums)
    )
    monkeypatch.setattr(scanner, "_should_include_track", lambda *a, **k: True)
    monkeypatch.setattr(scanner, "update_artist_scan_timestamp", lambda *a, **k: True)
    monkeypatch.setattr(scanner, "update_similar_artists", lambda *a, **k: True)
    monkeypatch.setattr(
        scanner, "_backfill_similar_artists_fallback_ids", lambda *a, **k: 0
    )


# ---------------------------------------------------------------------------
# Scenario 1: standard + deluxe released the same day, nothing owned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preference,expected_albums,expected_count",
    [
        ("one_standard", {"Infinito"}, 10),
        ("one_complete", {"Infinito (Deluxe)"}, 14),
        ("all", {"Infinito", "Infinito (Deluxe)"}, 24),
    ],
)
def test_new_release_standard_and_deluxe_same_day(
    monkeypatch, preference, expected_albums, expected_count
):
    """Only the picked edition's tracks reach the wishlist.

    The group is {Infinito (10), Infinito (Deluxe) (14)} — track counts
    are NOT on the provider album list, so the pick is deferred until
    each edition's track list is fetched in the loop.
    """
    std_tracks = [f"Song {i + 1:02d}" for i in range(10)]
    deluxe_tracks = std_tracks + [f"Bonus Track {i + 1}" for i in range(4)]
    albums = [
        types.SimpleNamespace(id="a-std", name="Infinito"),
        types.SimpleNamespace(id="a-dlx", name="Infinito (Deluxe)"),
    ]
    scanner = _build_scanner(
        {
            "a-std": _album_payload("Infinito", std_tracks, "a-std"),
            "a-dlx": _album_payload("Infinito (Deluxe)", deluxe_tracks, "a-dlx"),
        }
    )
    _prime_scan(monkeypatch, scanner, albums, preference)

    # Nothing owned: every track is missing.
    monkeypatch.setattr(
        scanner, "is_track_missing_from_library", lambda *a, **k: True
    )
    added = []
    monkeypatch.setattr(
        scanner,
        "add_track_to_wishlist",
        lambda track, album_data, artist, **k: added.append(
            (track.get("name"), album_data.get("name"))
        )
        or True,
    )

    results = scanner.scan_watchlist_artists([_build_artist()], scan_state={})

    assert results[0].success is True
    assert results[0].tracks_added_to_wishlist == expected_count
    assert {album for _, album in added} == expected_albums
    # No (track, album) pair is wishlisted twice. Note: for "all" the two
    # editions share 10 titles — that overlap is the issue itself (both
    # editions' rows land in the wishlist).
    assert len({(name, album) for name, album in added}) == expected_count


def test_provider_order_preserved_through_reduction(monkeypatch):
    """Deluxe listed first still reduces to the standard for one_standard."""
    std_tracks = [f"Song {i + 1:02d}" for i in range(10)]
    deluxe_tracks = std_tracks + [f"Bonus Track {i + 1}" for i in range(4)]
    albums = [
        types.SimpleNamespace(id="a-dlx", name="Infinito (Deluxe)"),
        types.SimpleNamespace(id="a-std", name="Infinito"),
    ]
    scanner = _build_scanner(
        {
            "a-std": _album_payload("Infinito", std_tracks, "a-std"),
            "a-dlx": _album_payload("Infinito (Deluxe)", deluxe_tracks, "a-dlx"),
        }
    )
    _prime_scan(monkeypatch, scanner, albums, "one_standard")
    monkeypatch.setattr(
        scanner, "is_track_missing_from_library", lambda *a, **k: True
    )
    added = []
    monkeypatch.setattr(
        scanner,
        "add_track_to_wishlist",
        lambda track, album_data, artist, **k: added.append(album_data.get("name"))
        or True,
    )

    scanner.scan_watchlist_artists([_build_artist()], scan_state={})

    assert set(added) == {"Infinito"}
    assert len(added) == 10


# ---------------------------------------------------------------------------
# Scenario 2: reissue of an owned album ("Aerosmith" 8 tracks owned;
# "Aerosmith (Legendary Expanded Edition)" 33 tracks appears in a scan)
# ---------------------------------------------------------------------------


def _reissue_harness(monkeypatch, preference):
    base_tracks = [f"Track {i + 1:02d}" for i in range(8)]
    extra_tracks = [f"Extra Track {i + 1}" for i in range(25)]
    albums = [
        types.SimpleNamespace(
            id="a-exp", name="Aerosmith (Legendary Expanded Edition)"
        )
    ]
    scanner = _build_scanner(
        {
            "a-exp": _album_payload(
                "Aerosmith (Legendary Expanded Edition)",
                base_tracks + extra_tracks,
                "a-exp",
                with_isrc=True,
            )
        }
    )
    _prime_scan(monkeypatch, scanner, albums, preference)

    owned_isrcs = {f"ISRC-{i:03d}" for i in range(8)}
    owned_titles = set(base_tracks)

    def check_track_exists(title, artist, confidence_threshold=0.7,
                           server_source=None, album=None):
        if title in owned_titles:
            return (
                types.SimpleNamespace(
                    album_title="Aerosmith",
                    file_path="/music/aerosmith/track.flac",
                ),
                0.95,
            )
        return (None, 0.0)

    scanner._database = types.SimpleNamespace(
        check_track_exists=check_track_exists
    )

    # The 8 base tracks are exact external-ID duplicates of the library's
    # copies (same ISRCs); the 25 extras match nothing exactly.
    monkeypatch.setattr(
        track_identity,
        "extract_external_ids",
        lambda track, source_hint=None: {"isrc": track["isrc"]}
        if track.get("isrc")
        else {},
    )
    monkeypatch.setattr(
        track_identity,
        "find_library_track_by_external_id",
        lambda db, external_ids=None, server_source=None: (
            {"album_title": "Aerosmith"}
            if (external_ids or {}).get("isrc") in owned_isrcs
            else None
        ),
    )
    monkeypatch.setattr(
        track_identity,
        "find_provenance_by_external_id",
        lambda *a, **k: None,
    )

    added = []
    monkeypatch.setattr(
        scanner,
        "add_track_to_wishlist",
        lambda track, album_data, artist, **k: added.append(track.get("name"))
        or True,
    )
    return scanner, added, base_tracks, extra_tracks


def test_reissue_one_standard_wishlists_nothing(monkeypatch):
    """The library owns the base album → the whole group is skipped.

    Never an extras-only folder: one_standard wants the standard
    edition, which is already owned.
    """
    scanner, added, _base, _extras = _reissue_harness(monkeypatch, "one_standard")

    results = scanner.scan_watchlist_artists([_build_artist()], scan_state={})

    assert results[0].success is True
    assert results[0].new_tracks_found == 0
    assert results[0].tracks_added_to_wishlist == 0
    assert added == []


def test_reissue_one_complete_wishlists_full_edition_minus_exact_dupes(
    monkeypatch,
):
    """The picked edition queues COMPLETE: the fuzzy same-album skip is
    bypassed for non-exact matches, so the 25 extras are wishlisted;
    the 8 exact ISRC duplicates are still skipped."""
    scanner, added, base_tracks, extra_tracks = _reissue_harness(
        monkeypatch, "one_complete"
    )

    results = scanner.scan_watchlist_artists([_build_artist()], scan_state={})

    assert results[0].success is True
    assert results[0].tracks_added_to_wishlist == 25
    assert sorted(added) == sorted(extra_tracks)
    assert not (set(added) & set(base_tracks))


def test_reissue_all_keeps_todays_extras_only_behaviour(monkeypatch):
    """preference=all must be byte-for-byte today's behaviour: the 8
    owned tracks skip via the same-album gate, the 25 extras wishlist."""
    scanner, added, base_tracks, extra_tracks = _reissue_harness(
        monkeypatch, "all"
    )

    results = scanner.scan_watchlist_artists([_build_artist()], scan_state={})

    assert results[0].success is True
    assert results[0].tracks_added_to_wishlist == 25
    assert sorted(added) == sorted(extra_tracks)
    assert not (set(added) & set(base_tracks))


# ---------------------------------------------------------------------------
# #1450 review fixes — per-track ownership edge cases
# ---------------------------------------------------------------------------


def _owner_check_db():
    """Library claims the fuzzy title/artist/album match (a published row)."""
    def check_track_exists(title, artist, confidence_threshold=0.7,
                           server_source=None, album=None):
        if title == "Track 01":
            return (
                types.SimpleNamespace(
                    album_title="Aerosmith",
                    file_path="/music/aerosmith/track01.flac",
                ),
                0.95,
            )
        return (None, 0.0)

    return types.SimpleNamespace(check_track_exists=check_track_exists)


def _no_duplicate_config(monkeypatch, allow_duplicates):
    """Drive wishlist.allow_duplicate_tracks through the settings stub."""
    import core.settings as settings_stub

    class _Cfg:
        def get(self, key, default=None):
            if key == "wishlist.allow_duplicate_tracks":
                return allow_duplicates
            return default

        def get_active_media_server(self):
            return "plex"

    monkeypatch.setattr(settings_stub, "config_manager", _Cfg())


def _track_no_ids():
    return {"name": "Track 01", "artists": [{"name": "Artist One"}]}


def test_one_complete_duplicates_off_fuzzy_owned_no_extids_not_wishlisted(
    monkeypatch,
):
    """#1450 BLOCKING: with duplicates off, one_complete must not re-wishlist
    a fuzzy-owned track whose library row simply lacks external IDs
    (local imports / rows predating ID backfill).

    The exact-ID-only branch would miss it, and add_to_wishlist_detailed
    dedupes by provider track ID only (standard/deluxe IDs differ) —
    so the file would be re-downloaded, violating the explicit
    no-duplicates setting.
    """
    scanner = _build_scanner({})
    scanner._database = _owner_check_db()
    _no_duplicate_config(monkeypatch, False)
    monkeypatch.setattr(track_identity, "extract_external_ids", lambda *a, **k: {})

    missing = scanner.is_track_missing_from_library(
        _track_no_ids(), album_name="Aerosmith", edition_preference="one_complete"
    )
    assert missing is False


def test_one_complete_duplicates_on_no_extids_still_wishlisted(monkeypatch):
    """Control: with duplicates on, the narrow exact-ID branch still runs —
    a fuzzy match without external IDs does NOT count as owned and the
    track is wishlisted (the picked edition queues complete)."""
    scanner = _build_scanner({})
    scanner._database = _owner_check_db()
    _no_duplicate_config(monkeypatch, True)
    monkeypatch.setattr(track_identity, "extract_external_ids", lambda *a, **k: {})
    monkeypatch.setattr(
        track_identity, "find_provenance_by_external_id", lambda *a, **k: None
    )

    missing = scanner.is_track_missing_from_library(
        _track_no_ids(), album_name="Aerosmith", edition_preference="one_complete"
    )
    assert missing is True


def test_one_complete_db_error_fails_closed(monkeypatch):
    """#1450: a DB failure inside the one_complete probe must treat the track
    as present for this scan (H11 rule), not wishlisted/re-downloaded."""
    import sqlite3

    scanner = _build_scanner({})
    scanner._database = _owner_check_db()
    _no_duplicate_config(monkeypatch, True)

    def _raise_locked(track, source_hint=None):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(track_identity, "extract_external_ids", _raise_locked)

    missing = scanner.is_track_missing_from_library(
        {"name": "Track 01", "artists": [{"name": "Artist One"}],
         "isrc": "ISRC-001"},
        album_name="Aerosmith",
        edition_preference="one_complete",
    )
    assert missing is False


def _extid_scanner(monkeypatch, file_path):
    """one_complete probe where the track carries an ISRC matching a library
    row; only the row's file_path varies."""
    scanner = _build_scanner({})
    scanner._database = _owner_check_db()
    _no_duplicate_config(monkeypatch, True)
    monkeypatch.setattr(
        track_identity,
        "extract_external_ids",
        lambda track, source_hint=None: {"isrc": "ISRC-001"},
    )
    monkeypatch.setattr(
        track_identity,
        "find_library_track_by_external_id",
        lambda db, external_ids=None, server_source=None: {
            "album_title": "Aerosmith",
            "file_path": file_path,
        },
    )
    monkeypatch.setattr(
        track_identity, "find_provenance_by_external_id", lambda *a, **k: None
    )
    return scanner


def test_one_complete_staged_exact_match_kept_wishlisted(monkeypatch):
    """#1450: an exact external-ID match whose row is still in atomic-publish
    staging is NOT ownership — the file is quarantined and may never
    publish, so the track stays wishlisted."""
    scanner = _extid_scanner(
        monkeypatch,
        "/transfers/.soulsync_atomic_staging/batch-9/track01.flac",
    )
    missing = scanner.is_track_missing_from_library(
        {"name": "Track 01", "artists": [{"name": "Artist One"}],
         "isrc": "ISRC-001"},
        album_name="Aerosmith",
        edition_preference="one_complete",
    )
    assert missing is True


def test_one_complete_published_exact_match_still_skipped(monkeypatch):
    """Control: a published exact external-ID match is still skipped."""
    scanner = _extid_scanner(monkeypatch, "/music/aerosmith/track01.flac")
    missing = scanner.is_track_missing_from_library(
        {"name": "Track 01", "artists": [{"name": "Artist One"}],
         "isrc": "ISRC-001"},
        album_name="Aerosmith",
        edition_preference="one_complete",
    )
    assert missing is False
