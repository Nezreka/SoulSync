"""Pin the diagnostic error string from
``RepairWorker._build_unresolvable_album_folder_error``.

GitHub issue #558 (gabistek, Navidrome on Docker / Arch host): the
Album Completeness Auto-Fill button surfaced a flat "Could not
determine album folder from existing tracks" error with no diagnostic.
Reporter is on Navidrome, which (unlike Plex) has no API that exposes
filesystem library paths — so the resolver returns None whenever the
DB-recorded path doesn't already exist as-is in SoulSync's container
view AND the user hasn't manually configured Settings → Library →
Music Paths.

The fix replaces the flat string with a multi-part diagnostic naming
the active media server, showing one sample DB path, listing the base
directories the resolver actually probed, and pointing the user at the
config that would unblock them. These tests pin each part so future
copy edits don't accidentally drop the actionable hint or the sample
path.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace


# ── Stub modules that the import of core.repair_worker pulls in ──
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

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod


from core.library.path_resolver import ResolveAttempt
from core.repair_worker import RepairWorker


def _make_worker(active_server="plex"):
    """Bare RepairWorker with a config_manager that reports the given
    active media server. We never run the full job — just exercise the
    diagnostic builder."""
    worker = RepairWorker(database=SimpleNamespace())
    cfg = SimpleNamespace()
    cfg.get_active_media_server = lambda: active_server
    cfg.get = lambda key, default=None: default
    worker._config_manager = cfg
    return worker


def test_error_names_active_media_server():
    """User needs to know which server's path conventions are at play
    so they can set the right mount in Settings."""
    worker = _make_worker(active_server="navidrome")
    msg = worker._build_unresolvable_album_folder_error(
        ResolveAttempt(base_dirs_tried=["/app/Transfer"]),
        "/music/Artist/Album/track.flac",
    )
    assert "navidrome" in msg.lower(), (
        f"Active server name must appear in error; got: {msg}"
    )


def test_error_includes_sample_db_path():
    """One concrete path lets the user see what their media server
    is reporting — usually enough to reverse-engineer the right mount."""
    worker = _make_worker()
    sample = "/music/Kendrick Lamar/Mr. Morale/01 - United in Grief.flac"
    msg = worker._build_unresolvable_album_folder_error(
        ResolveAttempt(base_dirs_tried=["/app/Transfer"]),
        sample,
    )
    assert sample in msg, (
        f"Sample DB path must appear verbatim in error; got: {msg}"
    )


def test_error_lists_base_dirs_tried():
    """User needs to know what the resolver probed — otherwise they
    can't tell whether to add a new mount or whether the existing one
    just doesn't match the recorded path."""
    worker = _make_worker()
    attempt = ResolveAttempt(
        base_dirs_tried=["/app/Transfer", "/downloads", "/library"],
    )
    msg = worker._build_unresolvable_album_folder_error(attempt, "/music/x.flac")
    for base in attempt.base_dirs_tried:
        assert base in msg, f"Probed base dir {base!r} missing from error: {msg}"


def test_error_calls_out_no_base_dirs_when_empty():
    """When the resolver had nothing to probe, that's a different
    failure mode than "tried 3 dirs and failed" — the user needs
    different action. Pin that the message distinguishes them."""
    worker = _make_worker()
    msg = worker._build_unresolvable_album_folder_error(
        ResolveAttempt(base_dirs_tried=[]),
        "/music/x.flac",
    )
    assert "no base director" in msg.lower(), (
        f"Empty-base-dirs case must surface 'no base directories'; got: {msg}"
    )


def test_error_always_includes_settings_hint():
    """The actionable fix line must always appear regardless of which
    failure mode fired. This is the part the user needs to act on."""
    worker = _make_worker()
    for attempt in (
        ResolveAttempt(base_dirs_tried=[]),
        ResolveAttempt(base_dirs_tried=["/app/Transfer"]),
        None,
    ):
        msg = worker._build_unresolvable_album_folder_error(attempt, "/music/x.flac")
        assert "Settings" in msg, f"Settings hint missing for attempt={attempt}; got: {msg}"
        assert "Music Paths" in msg, f"Music Paths hint missing for attempt={attempt}; got: {msg}"


def test_error_handles_none_attempt_defensively():
    """If for some reason no ResolveAttempt is collected (e.g. zero
    existing tracks loop never ran), the helper must not crash. It
    can omit the probe-detail line but must still emit the actionable
    Settings hint."""
    worker = _make_worker()
    msg = worker._build_unresolvable_album_folder_error(None, "/music/x.flac")
    assert "Settings" in msg, f"None attempt must still emit Settings hint; got: {msg}"
    assert "/music/x.flac" in msg


def test_error_handles_missing_sample_path():
    """If we couldn't sample a DB path (e.g. all entries had None
    file_path), the path line is omitted but the rest of the message
    still renders."""
    worker = _make_worker()
    msg = worker._build_unresolvable_album_folder_error(
        ResolveAttempt(base_dirs_tried=["/app/Transfer"]),
        None,
    )
    assert "Settings" in msg
    # No sample-path line means no "Example DB-recorded path" prefix
    assert "Example DB-recorded path:" not in msg


def test_error_handles_missing_config_manager():
    """RepairWorker may be constructed without a config_manager; the
    builder shouldn't crash and should fall back to 'unknown' for the
    server name rather than blowing up."""
    worker = RepairWorker(database=SimpleNamespace())
    worker._config_manager = None
    msg = worker._build_unresolvable_album_folder_error(
        ResolveAttempt(base_dirs_tried=[]), "/music/x.flac",
    )
    assert "unknown" in msg.lower()
