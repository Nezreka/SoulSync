"""`config/` holds user configuration. It must never hold application code.

TheHomeGuy, on 3.2.0: every Save failed with

    Failed to save settings: 'ConfigManager' object has no attribute 'batch'

`batch()` and its only caller shipped in the SAME commit, so the code was
self-consistent. What was not consistent was his install: `config/settings.py`
was older than the rest of it.

That was possible because `config/` is the directory users bind-mount to keep
their settings across upgrades (`- ./config:/app/config`). Everything else ran
from the image; that one module ran from the user's disk, where their copy
masked ours. Upgrade the app, keep the old ConfigManager.

The entrypoint had carried a workaround since March — copy a fresh settings.py
in on every boot — whose comment reads "Stale versions from older releases cause
startup crashes (missing methods)". A workaround that has to run correctly on
every user's machine forever is not a fix. The module now lives in `core/`,
outside every mount, and this test keeps it that way.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# Mounted in docker-compose.yml as `./config:/app/config`, so a user's copy of
# anything in here wins over the image's.
CONFIG_DIR = REPO / "config"


def test_the_config_directory_contains_no_python_modules():
    """A .py file here is a file the user's stale copy can override."""
    modules = sorted(p.name for p in CONFIG_DIR.glob("*.py"))
    assert modules == [], (
        f"{modules} is application code inside the user-mounted config/ "
        "directory. A user upgrading the app keeps their old copy of it, and it "
        "silently overrides ours — that is exactly how 'ConfigManager has no "
        "attribute batch' happened. Put it in core/ instead."
    )


def test_settings_lives_in_core_and_still_finds_config_json():
    """The move must not break path resolution.

    ConfigManager derives everything from `Path(__file__).parent.parent`, which
    is the repo root from `config/` AND from `core/` — that is why the move is
    safe. Pinned because a future move to a deeper package would silently point
    `config.json` and the database somewhere else."""
    from core.settings import ConfigManager

    assert (REPO / "core" / "settings.py").exists()
    assert not (REPO / "config" / "settings.py").exists()

    # A real instance, so this reads the value the app actually computes rather
    # than re-deriving it here. SOULSYNC_CONFIG_PATH is isolated by conftest, so
    # nothing touches the developer's real config.json.
    manager = ConfigManager()

    assert manager.base_dir == REPO, (
        f"base_dir resolved to {manager.base_dir}, not the repo root — every "
        "relative path (config.json, the database) hangs off this")

    # config_path and database_path themselves are redirected to tmp by the
    # conftest isolation, so they cannot be asserted here. base_dir is the thing
    # the move could actually have broken, and it is NOT overridden — so pin the
    # resolution the app performs against it (settings.py line 33 / line 53).
    assert manager.base_dir / "config/config.json" == REPO / "config" / "config.json"
    assert manager.base_dir / "database" / "music_library.db" == \
        REPO / "database" / "music_library.db", \
        "the database would follow the module and orphan every user's library"


def test_nothing_imports_the_old_module_path():
    """A single leftover `from config.settings import ...` would resurrect the
    bug for anyone whose mounted folder still holds the old file."""
    # git grep, not rglob: this repo lives on a Windows drive under WSL, where
    # reading a few thousand files from Python takes minutes. git does the same
    # scan natively in well under a second, and it only sees TRACKED files, so
    # .venv and node_modules are excluded for free.
    # BOTH spellings. The rewrite that performed this move searched for
    # "config.settings" and so silently skipped `from config import settings`,
    # which is the same import wearing a different hat — it survived in
    # test_watchlist_auto_download.py until a full run caught it.
    patterns = [
        # -F: a fixed string, not a regex. An unescaped "." matched the SPACE
        # in a docstring reading "based on config settings." — a false positive.
        ["-lF", "config.settings"],
        ["-lE", r"^\s*from config import\b"],
    ]

    this_file = pathlib.Path(__file__).name      # it quotes the old path to explain itself
    offenders = set()
    for flags in patterns:
        proc = subprocess.run(["git", "grep", *flags, "--", "*.py"],
                              cwd=REPO, capture_output=True, text=True)
        if proc.returncode not in (0, 1):        # 1 == no matches, which is the goal
            pytest.skip(f"git grep unavailable here: {proc.stderr.strip()}")
        offenders.update(line for line in proc.stdout.split("\n")
                         if line and pathlib.Path(line).name != this_file)

    assert offenders == set(), f"still referencing the old module path: {sorted(offenders)}"


def test_the_docker_image_no_longer_seeds_settings_into_the_mount():
    """Dockerfile + entrypoint must not put the module back."""
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (REPO / "entrypoint.sh").read_text(encoding="utf-8")

    assert "/defaults/settings.py" not in dockerfile, \
        "the image should no longer stash a settings.py to seed into the mount"
    assert "cp /defaults/settings.py" not in entrypoint, \
        "the boot-time copy was the workaround; the move replaces it"
    # …and it cleans up what older versions left in the user's folder.
    assert "rm -f /app/config/settings.py" in entrypoint
