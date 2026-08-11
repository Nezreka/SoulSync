"""Wing It stub ids must be reproducible.

The id identifies a stub that is written to
``mirrored_playlist_tracks.extra_data.matched_data.id`` and read back in a later
process. It used to be ``hash(f"{artist}_{track}") % 100000``; ``hash()`` on a str
is salted per interpreter (PEP 456), so the id changed on every restart. The
cross-process test below is the one that pins the actual regression — it passes
trivially inside a single process either way.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from core.discovery.wing_it import STUB_ID_PREFIX, is_stub_id, stub_track_id

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── determinism ───────────────────────────────────────────────────────────

def test_same_input_gives_same_id():
    assert stub_track_id("Yorushika", "Ghost in a Flower") == \
        stub_track_id("Yorushika", "Ghost in a Flower")


@pytest.mark.parametrize("artist,track,expected", [
    # Pinned so a future change of hashing scheme is a deliberate, visible act:
    # stub ids already persisted in user databases would stop resolving.
    ("Yorushika", "Ghost in a Flower", "wing_it_e6e3736d43fb"),
    ("LiSA", "紅蓮華 - Gurenge", "wing_it_bc442918ee92"),
    ("", "", "wing_it_b14a7b8059d9"),
])
def test_id_is_pinned_to_a_known_value(artist, track, expected):
    assert stub_track_id(artist, track) == expected


def test_id_survives_a_different_hash_seed():
    """The regression: two interpreters must agree.

    PYTHONHASHSEED has to be set before the process starts, so this shells out.
    On the old `hash()`-based id the two runs disagree and this fails."""
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, sys.argv[1])
        from core.discovery.wing_it import stub_track_id
        print(stub_track_id("Yorushika", "Ghost in a Flower"))
        """
    )
    ids = set()
    for seed in ("0", "1", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", script, str(REPO_ROOT)],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        )
        ids.add(out.stdout.strip())
    assert len(ids) == 1, f"stub id varies with PYTHONHASHSEED: {ids}"


# ── shape ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("artist,track", [
    ("Yorushika", "Ghost in a Flower"),
    ("", ""),
    (None, None),
    ("A" * 500, "B" * 500),
    ("Ado", "可愛くてごめん"),
    ("Sigur Rós", "Untitled #1 (Vaka)"),
])
def test_id_is_always_a_usable_stub_id(artist, track):
    tid = stub_track_id(artist, track)
    assert tid.startswith(STUB_ID_PREFIX)
    assert is_stub_id(tid)
    # No separator/whitespace surprises for callers that put it in a URL or SQL.
    assert tid.isascii() and tid.strip() == tid and " " not in tid


def test_different_tracks_get_different_ids():
    ids = {
        stub_track_id("Yorushika", "Ghost in a Flower"),
        stub_track_id("Yorushika", "Matasaburo"),
        stub_track_id("Ado", "Ghost in a Flower"),
        stub_track_id("", "Ghost in a Flower"),
        stub_track_id("Ghost in a Flower", "Yorushika"),
    }
    assert len(ids) == 5


def test_none_and_empty_are_the_same_track():
    # Both mean "the source gave us nothing", so they must not split into two ids.
    assert stub_track_id(None, None) == stub_track_id("", "")


# ── is_stub_id ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "wing_it_e6e3736d43fb",
    "wing_it_53014",          # the legacy hash()-based scheme still reads as a stub
    "wing_it_",
])
def test_is_stub_id_accepts_both_schemes(value):
    assert is_stub_id(value) is True


@pytest.mark.parametrize("value", [
    "3315608251",             # a real Deezer id
    "",
    None,
    "spotify:track:abc",
    "WING_IT_e6e3736d43fb",   # prefix is case-sensitive
    " wing_it_abc",
])
def test_is_stub_id_rejects_everything_else(value):
    assert is_stub_id(value) is False
