"""Podcasts may not reach into the music acquisition pipeline, or into video.

There is already a behavioural isolation test (test_podcast_isolation.py) that
checks is_music_batch keeps podcast batches out of the music worker pool. This
one is the structural half: it reads the modules' real imports rather than
trusting that nobody added one.

The rules are NOT the audiobook rules, and the difference is the point.
Audiobooks were built with their own database and may not import ``database``
at all. Podcasts store the watchlist in music_library.db and write their
progress into the shared runtime state so episodes appear on the existing
Downloads page. Both of those are deliberate, and both are listed below as
allowed with the reason, because an isolation test that forbids what the design
actually does just gets edited until it passes.

What podcasts must never touch is the music side's ACQUISITION: its download
engine, its wishlist, its matching. That is what went wrong before podcasts were
pulled back out of the music worker pool in cb6f1aeeb, and it is what this holds.

The module list is derived from the filesystem on purpose. The audiobook guard
hardcodes its list, and three modules added later were never covered by it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _podcast_modules():
    """Every podcast module there is, found rather than listed."""
    found = sorted(_ROOT.glob("core/podcast*.py")) + sorted(_ROOT.glob("api/podcasts.py"))
    assert found, "no podcast modules found - has the layout changed?"
    return found


# Reaching any of these is how an isolated feature stops being isolated.
_FORBIDDEN = (
    "core.downloads",            # the music download engine and its lifecycle
    "core.download_engine",
    "core.download_orchestrator",
    "core.wishlist",             # the music wishlist
    "core.matching_engine",
    "core.spotify_client",
    "core.plex_client",
    "core.jellyfin_client",
    "core.navidrome_client",
    "core.video",                # the video side
    "core.audiobook_",           # the audiobook side
    "web_server",
)

# Deliberate, and each one earns its place:
#   core.runtime_state   - podcast progress is written into the SHARED download
#                          state under its own batch id, flagged is_music False,
#                          which is how an episode shows on the Downloads page
#                          without joining the music pipeline.
#   database.music_...   - the podcast watchlist is a table in music_library.db.
#                          audiobooks got their own file, podcasts did not, and
#                          moving them now would be a migration for no gain.
_ALLOWED_SHARED = ("core.runtime_state", "database.music_database")


def _imported_names(path: Path):
    """Every module name this file imports, including inside functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


@pytest.mark.parametrize("path", _podcast_modules(), ids=lambda p: p.name)
def test_a_podcast_module_never_imports_music_acquisition(path):
    for name in _imported_names(path):
        if name in _ALLOWED_SHARED:
            continue
        for banned in _FORBIDDEN:
            assert not (name == banned.rstrip(".") or name.startswith(banned)), (
                f"{path.relative_to(_ROOT)} imports {name}, which belongs to "
                f"another side of the app"
            )


def test_the_shared_state_podcasts_do_use_is_the_state_music_skips():
    """The allowance above is only safe because the music side honours the flag.

    is_music_batch is what keeps the music worker pool, the batch healer and the
    music wishlist failure processor away from a podcast batch. If that stops
    being true, writing into the shared state stops being safe and this test is
    the one that should say so.
    """
    from core.downloads.lifecycle import is_music_batch

    assert is_music_batch("podcasts", {}) is False
    assert is_music_batch("batch-x", {"is_music": False}) is False
    assert is_music_batch("batch-x", {"managed_externally": True}) is False
    # and a real music batch is still a music batch
    assert is_music_batch("batch-x", {"playlist_id": "p1"}) is True


def test_every_podcast_module_is_covered():
    """The list is a glob, so this is really a check that the glob still finds
    them - a module renamed out of the pattern would silently stop being held."""
    covered = {p.name for p in _podcast_modules()}
    assert "podcasts.py" in covered
    assert "podcast_client.py" in covered
    assert "podcast_automation.py" in covered
    assert "podcast_download_client.py" in covered
    assert "podcast_post_processor.py" in covered
    assert "podcast_ingest_guard.py" in covered
