"""Tests for the pieces of `core.library_reorganize` the catalogue planner
(`core.library2.reorganize_plan`) shares: the post-process context the path
builder reads.

The orchestrator these tests used to sit beside (copy to staging, re-run
post-processing) is gone; reorganize only moves files now.
"""

import sys
import types


# --- module stubs (same shape used elsewhere in the test suite) -----------
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
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod


from core import library_reorganize  # noqa: E402


def test_reorganize_context_disables_folder_reuse():
    """Every reorganize
    pipeline context (preview, rename-only apply, full apply — all built by
    `_build_post_process_context`) carries the no-reuse flag."""
    context = library_reorganize._build_post_process_context(
        {'id': 'dz-1', 'name': 'Rocks'},
        {'id': 'a1', 'name': 'Back in the Saddle', 'track_number': 1},
        'Aerosmith', 'Rocks', 1,
    )
    assert context['_no_album_folder_reuse'] is True


def test_reorganize_context_defers_catalogue_registration_to_runner():
    context = library_reorganize._build_post_process_context(
        {'id': 'dz-1', 'name': 'Rocks'},
        {'id': 'a1', 'name': 'Back in the Saddle', 'track_number': 1},
        'Aerosmith', 'Rocks', 1,
    )
    assert context['_library_reorganize'] is True


def test_reorganize_context_is_a_local_import():
    """Reorganize handles the user's OWN library files — the integrity check's
    duration-agreement leg must not apply (#804 semantics). Without this flag,
    a file whose duration drifts from the re-resolved API tracklist (a
    different master / long version) got a copy QUARANTINED during reorganize
    (TheHomeGuy: 'Through Glass' 283s vs Discogs' 241s → quarantine + failed)."""
    context = library_reorganize._build_post_process_context(
        {'id': 'dz-1', 'name': 'Come What(ever) May'},
        {'id': 'a1', 'name': 'Through Glass', 'track_number': 8, 'duration_ms': 241000},
        'Stone Sour', 'Come What(ever) May', 1,
    )
    assert context['is_local_import'] is True

    # And the pipeline seam this flag drives: a local import never carries an
    # expected duration into the integrity check.
    from core.imports.file_integrity import expected_duration_for_check
    assert expected_duration_for_check(241000, True) is None
    assert expected_duration_for_check(241000, False) == 241000


