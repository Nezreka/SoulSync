"""Regression tests for ``core/library/path_resolver.py``.

GitHub issue #476 (gabistek, Docker / Arch host): Album Completeness
Auto-Fill returned ``Could not determine album folder from existing
tracks`` for every album. Root cause: the repair worker's path
resolver only probed the transfer + download folders, not the
user-configured ``library.music_paths`` or Plex-reported library
locations. Files lived in the media-server library mount and got
silently treated as missing.

These tests pin the resolver's behavior across the four base-dir
sources (explicit transfer, explicit download, config-driven library
paths, Plex client locations), the suffix-walk algorithm, and the
defensive return-None paths.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.library import path_resolver
from core.library.path_resolver import resolve_library_file_path


# ---------------------------------------------------------------------------
# Defensive boundary cases
# ---------------------------------------------------------------------------


def test_returns_none_for_empty_path() -> None:
    assert resolve_library_file_path("") is None
    assert resolve_library_file_path(None) is None  # type: ignore[arg-type]


def test_returns_none_when_no_base_dirs_configured(tmp_path: Path) -> None:
    """No transfer, no download, no config, no Plex → can't resolve."""
    fake = tmp_path / "non_existent.flac"
    assert resolve_library_file_path(str(fake)) is None


def test_returns_raw_path_when_it_exists(tmp_path: Path) -> None:
    """Happy path — the raw stored path resolves directly."""
    real = tmp_path / "track.flac"
    real.write_bytes(b"audio")

    result = resolve_library_file_path(str(real))

    assert result == str(real)


# ---------------------------------------------------------------------------
# Transfer / download base dirs (legacy behavior preserved)
# ---------------------------------------------------------------------------


def test_finds_file_via_transfer_folder_suffix_walk(tmp_path: Path) -> None:
    """DB stored path is `/music/Artist/Album/track.flac` but the file
    actually lives in `<transfer>/Artist/Album/track.flac`."""
    transfer = tmp_path / "Transfer"
    (transfer / "Artist" / "Album").mkdir(parents=True)
    actual = transfer / "Artist" / "Album" / "track.flac"
    actual.write_bytes(b"audio")

    result = resolve_library_file_path(
        "/music/Artist/Album/track.flac",
        transfer_folder=str(transfer),
    )

    assert result == str(actual)


def test_finds_file_via_download_folder_when_transfer_misses(tmp_path: Path) -> None:
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    download = tmp_path / "Downloads"
    (download / "Artist" / "Album").mkdir(parents=True)
    actual = download / "Artist" / "Album" / "track.flac"
    actual.write_bytes(b"audio")

    result = resolve_library_file_path(
        "/music/Artist/Album/track.flac",
        transfer_folder=str(transfer),
        download_folder=str(download),
    )

    assert result == str(actual)


def test_handles_windows_backslash_paths(tmp_path: Path) -> None:
    """DB stored paths can be Windows-style with backslashes — the
    walker normalizes them to forward slashes before splitting."""
    transfer = tmp_path / "Transfer"
    (transfer / "Artist" / "Album").mkdir(parents=True)
    actual = transfer / "Artist" / "Album" / "track.flac"
    actual.write_bytes(b"audio")

    result = resolve_library_file_path(
        r"H:\Music\Artist\Album\track.flac",
        transfer_folder=str(transfer),
    )

    assert result == str(actual)


# ---------------------------------------------------------------------------
# Library music paths from config (the fix for #476)
# ---------------------------------------------------------------------------


def test_finds_file_via_library_music_paths(tmp_path: Path) -> None:
    """The Plex/Jellyfin library at <library>/Artist/Album/track.flac
    is found via the user's configured ``library.music_paths`` even
    when transfer + download don't have it."""
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    library = tmp_path / "Library"
    (library / "Artist" / "Album").mkdir(parents=True)
    actual = library / "Artist" / "Album" / "track.flac"
    actual.write_bytes(b"audio")

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: {
        "library.music_paths": [str(library)],
        "soulseek.transfer_path": "",
        "soulseek.download_path": "",
    }.get(key, default)

    result = resolve_library_file_path(
        "/data/music/Artist/Album/track.flac",
        transfer_folder=str(transfer),
        config_manager=cm,
    )

    assert result == str(actual)


def test_library_music_paths_handles_multiple_dirs(tmp_path: Path) -> None:
    """Users can configure multiple music paths — first existing
    suffix-match wins."""
    lib_a = tmp_path / "LibA"
    lib_b = tmp_path / "LibB"
    (lib_a / "Artist").mkdir(parents=True)
    (lib_b / "Artist" / "Album").mkdir(parents=True)
    # Only LibB has the actual file
    actual = lib_b / "Artist" / "Album" / "track.flac"
    actual.write_bytes(b"audio")

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: {
        "library.music_paths": [str(lib_a), str(lib_b)],
    }.get(key, default)

    result = resolve_library_file_path(
        "/x/Artist/Album/track.flac",
        config_manager=cm,
    )

    assert result == str(actual)


def test_skips_non_string_entries_in_music_paths(tmp_path: Path) -> None:
    """Defensive: malformed config (None, int, dict in the list) must
    not crash the resolver."""
    library = tmp_path / "Library"
    (library / "Artist").mkdir(parents=True)
    actual = library / "Artist" / "track.flac"
    actual.write_bytes(b"audio")

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: {
        "library.music_paths": [None, 42, {"x": 1}, str(library), ""],
    }.get(key, default)

    result = resolve_library_file_path(
        "/x/Artist/track.flac",
        config_manager=cm,
    )

    assert result == str(actual)


def test_strips_whitespace_from_music_paths(tmp_path: Path) -> None:
    """Trailing whitespace on a config value (common copy-paste mistake)
    shouldn't break resolution."""
    library = tmp_path / "Library"
    (library / "Artist").mkdir(parents=True)
    actual = library / "Artist" / "track.flac"
    actual.write_bytes(b"audio")

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: {
        "library.music_paths": [f"  {library}  "],
    }.get(key, default)

    result = resolve_library_file_path(
        "/x/Artist/track.flac",
        config_manager=cm,
    )

    assert result == str(actual)


# ---------------------------------------------------------------------------
# Plex-reported library locations
# ---------------------------------------------------------------------------


def test_finds_file_via_plex_library_location(tmp_path: Path) -> None:
    """When SoulSync mounts the Plex library at a different path than
    Plex itself reports, the Plex-reported location is added to the
    search and the file is found."""
    plex_loc = tmp_path / "PlexLibrary"
    (plex_loc / "Artist" / "Album").mkdir(parents=True)
    actual = plex_loc / "Artist" / "Album" / "track.flac"
    actual.write_bytes(b"audio")

    plex_client = SimpleNamespace(
        server=SimpleNamespace(),  # truthy
        music_library=SimpleNamespace(locations=[str(plex_loc)]),
    )

    result = resolve_library_file_path(
        "/music/Artist/Album/track.flac",
        plex_client=plex_client,
    )

    assert result == str(actual)


def test_handles_plex_client_without_server(tmp_path: Path) -> None:
    """Plex client with no `server` attribute (uninitialized) shouldn't
    crash — just skip the Plex source."""
    plex_client = SimpleNamespace(server=None, music_library=None)

    # No other sources configured → returns None, no exception.
    assert resolve_library_file_path(
        "/x/track.flac",
        plex_client=plex_client,
    ) is None


def test_handles_plex_locations_attribute_missing(tmp_path: Path) -> None:
    """Plex music_library object without a `locations` attribute → skip."""
    plex_client = SimpleNamespace(
        server=SimpleNamespace(),
        music_library=SimpleNamespace(),  # no `locations`
    )

    assert resolve_library_file_path(
        "/x/track.flac",
        plex_client=plex_client,
    ) is None


# ---------------------------------------------------------------------------
# Source ordering & deduplication
# ---------------------------------------------------------------------------


def test_dedupe_avoids_duplicate_probes(tmp_path: Path) -> None:
    """If transfer == library_paths[0], the dir is only probed once.
    Resolver still finds the file."""
    shared = tmp_path / "Shared"
    (shared / "Artist").mkdir(parents=True)
    actual = shared / "Artist" / "track.flac"
    actual.write_bytes(b"audio")

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: {
        "soulseek.transfer_path": str(shared),
        "library.music_paths": [str(shared)],
    }.get(key, default)

    result = resolve_library_file_path(
        "/x/Artist/track.flac",
        transfer_folder=str(shared),
        config_manager=cm,
    )

    assert result == str(actual)


def test_explicit_transfer_takes_priority_over_config(tmp_path: Path) -> None:
    """Explicit transfer kwarg is added before config-derived paths so
    the worker's already-cached transfer_folder always wins ties."""
    explicit = tmp_path / "Explicit"
    config_dir = tmp_path / "FromConfig"
    (explicit / "Artist").mkdir(parents=True)
    (config_dir / "Artist").mkdir(parents=True)
    actual_explicit = explicit / "Artist" / "track.flac"
    actual_config = config_dir / "Artist" / "track.flac"
    actual_explicit.write_bytes(b"explicit")
    actual_config.write_bytes(b"config")

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: {
        "soulseek.transfer_path": str(config_dir),
    }.get(key, default)

    result = resolve_library_file_path(
        "/x/Artist/track.flac",
        transfer_folder=str(explicit),
        config_manager=cm,
    )

    assert result == str(actual_explicit)


# ---------------------------------------------------------------------------
# Failure paths from external dependencies don't crash
# ---------------------------------------------------------------------------


def test_config_manager_get_raising_does_not_crash(tmp_path: Path) -> None:
    """A flaky config_manager.get raising shouldn't break resolution.
    The resolver swallows it and continues with the explicit dirs."""
    transfer = tmp_path / "Transfer"
    (transfer / "Artist").mkdir(parents=True)
    actual = transfer / "Artist" / "track.flac"
    actual.write_bytes(b"audio")

    cm = MagicMock()
    cm.get.side_effect = RuntimeError("config blew up")

    result = resolve_library_file_path(
        "/x/Artist/track.flac",
        transfer_folder=str(transfer),
        config_manager=cm,
    )

    assert result == str(actual)


def test_plex_client_attribute_access_raising_does_not_crash(tmp_path: Path) -> None:
    """A Plex client whose attribute access raises shouldn't break
    resolution — fallback to other sources."""
    transfer = tmp_path / "Transfer"
    (transfer / "Artist").mkdir(parents=True)
    actual = transfer / "Artist" / "track.flac"
    actual.write_bytes(b"audio")

    class _BrokenPlex:
        @property
        def server(self):
            raise RuntimeError("plex disconnected")

    result = resolve_library_file_path(
        "/x/Artist/track.flac",
        transfer_folder=str(transfer),
        plex_client=_BrokenPlex(),
    )

    assert result == str(actual)


def test_returns_none_when_no_suffix_matches(tmp_path: Path) -> None:
    """When the file genuinely doesn't exist anywhere, return None
    cleanly. Don't false-match an unrelated file."""
    transfer = tmp_path / "Transfer"
    (transfer / "Artist" / "Album").mkdir(parents=True)
    # Create a different file in the right tree
    (transfer / "Artist" / "Album" / "different.flac").write_bytes(b"x")

    result = resolve_library_file_path(
        "/x/Artist/Album/missing.flac",
        transfer_folder=str(transfer),
    )

    assert result is None


# ---------------------------------------------------------------------------
# docker_resolve_path internal helper
# ---------------------------------------------------------------------------


def test_docker_resolve_path_translates_windows_paths_inside_docker(monkeypatch) -> None:
    """Inside Docker, ``H:\\Music\\track.flac`` becomes
    ``/host/mnt/h/Music/track.flac`` so the bind-mounted host drive
    can be reached. Outside Docker, paths are returned unchanged."""
    real_exists = os.path.exists

    def _fake_exists(p):
        if p == "/.dockerenv":
            return True
        return real_exists(p)

    monkeypatch.setattr(os.path, "exists", _fake_exists)
    assert path_resolver._docker_resolve_path("H:\\Music\\track.flac") == "/host/mnt/h/Music/track.flac"

    # Non-Windows-style path passes through unchanged inside Docker too.
    assert path_resolver._docker_resolve_path("/data/music") == "/data/music"


def test_docker_resolve_path_pass_through_outside_docker(monkeypatch) -> None:
    """Outside Docker (no /.dockerenv), Windows paths are unchanged."""
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists",
                        lambda p: False if p == "/.dockerenv" else real_exists(p))
    assert path_resolver._docker_resolve_path("H:\\Music\\track.flac") == "H:\\Music\\track.flac"


# ---------------------------------------------------------------------------
# Diagnostic helper — issue #558 (gabistek, Navidrome on Docker)
# ---------------------------------------------------------------------------
#
# `resolve_library_file_path_with_diagnostic` returns
# `(resolved, ResolveAttempt)` so callers can render a useful error
# instead of a silent None. Pre-fix the Album Completeness "Auto-Fill"
# button surfaced "Could not determine album folder from existing
# tracks" with no diagnostic, leaving Navidrome users (whose Subsonic
# API doesn't expose library paths the way Plex's does) with no signal
# about what to configure.


from core.library.path_resolver import (  # noqa: E402
    ResolveAttempt,
    resolve_library_file_path_with_diagnostic,
)


class TestResolveAttemptShape:
    def test_returns_tuple_of_path_and_attempt(self, tmp_path: Path) -> None:
        real = tmp_path / "track.flac"
        real.write_bytes(b"a")
        result = resolve_library_file_path_with_diagnostic(str(real))
        assert isinstance(result, tuple)
        assert len(result) == 2
        path, attempt = result
        assert path == str(real)
        assert isinstance(attempt, ResolveAttempt)

    def test_raw_path_existed_true_when_short_circuit(self, tmp_path: Path) -> None:
        """Happy path → resolver short-circuits at the first
        `os.path.exists` check; `base_dirs_tried` stays empty."""
        real = tmp_path / "track.flac"
        real.write_bytes(b"a")
        _, attempt = resolve_library_file_path_with_diagnostic(str(real))
        assert attempt.raw_path_existed is True
        assert attempt.base_dirs_tried == []

    def test_raw_path_existed_false_when_walking(self, tmp_path: Path) -> None:
        """When the raw path doesn't exist but the suffix-walk finds it,
        the attempt should report `raw_path_existed=False` and list the
        base dir that succeeded among `base_dirs_tried`."""
        # Create the file under a real base dir at a different parent
        base = tmp_path / "library"
        target = base / "Artist" / "Album" / "track.flac"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"a")

        # DB stores it as if scanned at /music/Artist/Album/track.flac
        db_path = "/music/Artist/Album/track.flac"
        path, attempt = resolve_library_file_path_with_diagnostic(
            db_path, transfer_folder=str(base),
        )
        assert path == str(target), f"suffix-walk should have found the file under {base}"
        assert attempt.raw_path_existed is False
        assert str(base) in attempt.base_dirs_tried


class TestDiagnosticForFailedResolves:
    def test_no_base_dirs_returns_none_with_empty_attempt(self) -> None:
        """No transfer/download/config/plex → resolver can't probe.
        Diagnostic must report empty `base_dirs_tried` so the caller can
        render a "no probe sources configured" hint."""
        path, attempt = resolve_library_file_path_with_diagnostic(
            "/music/Artist/Album/track.flac",
        )
        assert path is None
        assert attempt.raw_path_existed is False
        assert attempt.base_dirs_tried == []
        assert attempt.had_config_manager is False
        assert attempt.had_plex_client is False

    def test_base_dirs_listed_even_when_walk_fails(self, tmp_path: Path) -> None:
        """When base dirs exist but the suffix-walk doesn't find the
        file, `base_dirs_tried` must still report what was probed.
        Lets the caller surface "we tried X, Y, Z" in the error."""
        base = tmp_path / "transfer"
        base.mkdir()
        # Don't create the target file — the walk will fail
        path, attempt = resolve_library_file_path_with_diagnostic(
            "/music/Artist/Album/missing.flac",
            transfer_folder=str(base),
        )
        assert path is None
        assert str(base) in attempt.base_dirs_tried

    def test_had_flags_track_caller_inputs(self, tmp_path: Path) -> None:
        """`had_config_manager` / `had_plex_client` reflect what the
        caller passed in — useful for distinguishing 'caller didn't
        wire up the optional input' from 'optional input was wired up
        but produced no usable base dirs'."""
        config = MagicMock()
        config.get.return_value = ""  # no config-driven paths
        plex = SimpleNamespace(server=None, music_library=None)

        path, attempt = resolve_library_file_path_with_diagnostic(
            "/music/Artist/track.flac",
            config_manager=config,
            plex_client=plex,
        )
        assert path is None
        assert attempt.had_config_manager is True
        assert attempt.had_plex_client is True


class TestBackwardsCompat:
    def test_existing_resolve_function_delegates_to_diagnostic(self, tmp_path: Path) -> None:
        """The non-diagnostic `resolve_library_file_path` is now a thin
        wrapper that drops the attempt. Pin that the legacy signature
        still returns the same path values across all the cases the
        old function covered, so existing callers don't see drift."""
        real = tmp_path / "track.flac"
        real.write_bytes(b"a")

        # Happy path
        assert resolve_library_file_path(str(real)) == str(real)

        # Suffix-walk path
        base = tmp_path / "lib"
        target = base / "Artist" / "Album" / "track.flac"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"a")
        result = resolve_library_file_path(
            "/music/Artist/Album/track.flac",
            transfer_folder=str(base),
        )
        assert result == str(target)

        # Failure path
        assert resolve_library_file_path(
            "/music/Artist/missing.flac",
            transfer_folder=str(base),
        ) is None
