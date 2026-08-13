"""Keeps the UI's "Keep Best" ranking in step with the backend's deletion path.

`core/library/duplicate_keep.py` and the Tools page rank duplicate copies
independently, and its docstring records that they drifted once already — both
ranked bitrate-first, so a FLAC whose bitrate the scan never populated lost to a
282 kbps MP3 and the lossless copy got deleted.

This module owns the shared fixture: it asserts the fixture still matches what
the Python actually does, and the vitest side
(`webui/src/routes/tools/-tools.core.dupe-parity.test.ts`) asserts the TypeScript
agrees with the same file. Either side drifting fails a test.

Regenerate with:  .venv/bin/python tests/test_duplicate_keep_ui_parity.py
"""

from __future__ import annotations

import json
from pathlib import Path

from core.library.duplicate_keep import (
    duplicate_keep_sort_key,
    format_rank_for_path,
    pick_duplicate_to_keep,
)

FIXTURE = Path(__file__).resolve().parent.parent / "webui" / "src" / "routes" / "tools" / "duplicate-keep-parity.json"


# Each group is a duplicate set as the findings payload delivers it. Ids are
# stable so the expected keeper can be named without positional ambiguity.
GROUPS: list[dict] = [
    {
        "name": "lossless with no bitrate still beats a high-bitrate mp3",
        "tracks": [
            {"id": "mp3", "file_path": "/m/a/song.mp3", "bitrate": 320, "duration": 210, "track_number": 3},
            {"id": "flac", "file_path": "/m/a/song.flac", "bitrate": 0, "duration": 210, "track_number": 3},
        ],
    },
    {
        "name": "missing bitrate key entirely, not just zero",
        "tracks": [
            {"id": "mp3", "file_path": "/m/a/song.mp3", "bitrate": 282, "duration": 200, "track_number": 1},
            {"id": "flac", "file_path": "/m/a/song.flac", "duration": 200, "track_number": 1},
        ],
    },
    {
        "name": "same format falls through to bitrate",
        "tracks": [
            {"id": "low", "file_path": "/m/a/x.mp3", "bitrate": 128, "duration": 180, "track_number": 2},
            {"id": "high", "file_path": "/m/a/y.mp3", "bitrate": 320, "duration": 180, "track_number": 2},
        ],
    },
    {
        "name": "format and bitrate tied, duration breaks it",
        "tracks": [
            {"id": "short", "file_path": "/m/a/x.mp3", "bitrate": 320, "duration": 120, "track_number": 2},
            {"id": "long", "file_path": "/m/a/y.mp3", "bitrate": 320, "duration": 240, "track_number": 2},
        ],
    },
    {
        "name": "only the track number differs — a real number beats a placeholder",
        "tracks": [
            {"id": "placeholder", "file_path": "/m/a/x.mp3", "bitrate": 320, "duration": 200, "track_number": 1},
            {"id": "real", "file_path": "/m/a/y.mp3", "bitrate": 320, "duration": 200, "track_number": 7},
        ],
    },
    {
        "name": "total tie keeps the first",
        "tracks": [
            {"id": "first", "file_path": "/m/a/x.mp3", "bitrate": 320, "duration": 200, "track_number": 4},
            {"id": "second", "file_path": "/m/a/y.mp3", "bitrate": 320, "duration": 200, "track_number": 4},
        ],
    },
    {
        "name": "unknown extension ranks below every known one",
        "tracks": [
            {"id": "weird", "file_path": "/m/a/x.xyz", "bitrate": 9999, "duration": 999, "track_number": 99},
            {"id": "wma", "file_path": "/m/a/y.wma", "bitrate": 64, "duration": 10, "track_number": 1},
        ],
    },
    {
        "name": "no file path at all ranks as unknown",
        "tracks": [
            {"id": "nopath", "bitrate": 9999, "duration": 999, "track_number": 99},
            {"id": "aac", "file_path": "/m/a/y.aac", "bitrate": 64, "duration": 10, "track_number": 1},
        ],
    },
    {
        "name": "extension case is ignored",
        "tracks": [
            {"id": "shouty", "file_path": "/m/a/x.FLAC", "bitrate": 0, "duration": 100, "track_number": 1},
            {"id": "mp3", "file_path": "/m/a/y.mp3", "bitrate": 320, "duration": 100, "track_number": 1},
        ],
    },
    {
        "name": "dots in the directory don't confuse the extension",
        "tracks": [
            {"id": "dotted", "file_path": "/m/The B.B. Band/x.flac", "bitrate": 0, "duration": 100, "track_number": 1},
            {"id": "mp3", "file_path": "/m/plain/y.mp3", "bitrate": 320, "duration": 100, "track_number": 1},
        ],
    },
    {
        "name": "aiff and aif rank the same, so bitrate decides",
        "tracks": [
            {"id": "aif", "file_path": "/m/a/x.aif", "bitrate": 1000, "duration": 100, "track_number": 1},
            {"id": "aiff", "file_path": "/m/a/y.aiff", "bitrate": 1411, "duration": 100, "track_number": 1},
        ],
    },
    {
        "name": "ogg and opus rank the same too",
        "tracks": [
            {"id": "opus", "file_path": "/m/a/x.opus", "bitrate": 128, "duration": 100, "track_number": 1},
            {"id": "ogg", "file_path": "/m/a/y.ogg", "bitrate": 192, "duration": 100, "track_number": 1},
        ],
    },
    {
        "name": "three copies across three tiers",
        "tracks": [
            {"id": "m4a", "file_path": "/m/a/x.m4a", "bitrate": 256, "duration": 200, "track_number": 5},
            {"id": "wav", "file_path": "/m/a/y.wav", "bitrate": 0, "duration": 200, "track_number": 5},
            {"id": "ape", "file_path": "/m/a/z.ape", "bitrate": 900, "duration": 200, "track_number": 5},
        ],
    },
    {
        "name": "single track group",
        "tracks": [{"id": "only", "file_path": "/m/a/x.mp3", "bitrate": 128, "duration": 100, "track_number": 1}],
    },
]

# Extension ranking probed on its own, including the shapes that make the JS
# `split('.').pop()` and Python `os.path.splitext` disagree if either changes.
RANK_PATHS = [
    "/m/a/song.flac", "/m/a/song.FLAC", "/m/a/song.wav", "/m/a/song.aiff",
    "/m/a/song.aif", "/m/a/song.ape", "/m/a/song.m4a", "/m/a/song.ogg",
    "/m/a/song.opus", "/m/a/song.mp3", "/m/a/song.aac", "/m/a/song.wma",
    "/m/a/song.xyz", "/m/The B.B. Band/song.flac", "/m/a/noextension",
    "/m/a/trailingdot.", "", "song.mp3",
]


def _build() -> dict:
    return {
        "_comment": (
            "Generated from core/library/duplicate_keep.py by "
            "tests/test_duplicate_keep_ui_parity.py. Do not hand-edit — regenerate."
        ),
        "rank_by_path": {path: format_rank_for_path(path) for path in RANK_PATHS},
        "groups": [
            {
                "name": group["name"],
                "tracks": group["tracks"],
                "keys": [list(duplicate_keep_sort_key(track)) for track in group["tracks"]],
                "keeper_id": (pick_duplicate_to_keep(group["tracks"]) or {}).get("id"),
            }
            for group in GROUPS
        ],
    }


def test_fixture_matches_the_backend() -> None:
    """The committed fixture still describes what duplicate_keep.py does."""
    assert FIXTURE.exists(), f"missing fixture {FIXTURE}; regenerate with `python {__file__}`"
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert stored == _build(), (
        "duplicate-keep fixture is stale — the backend ranking changed. "
        f"Regenerate with `.venv/bin/python {Path(__file__).name}` and re-check the "
        "TypeScript in webui/src/routes/tools/-tools.core.ts."
    )


def test_format_tier_outranks_bitrate() -> None:
    """The regression the backend docstring records: lossless must win even with
    no bitrate recorded."""
    flac = {"file_path": "/m/a/song.flac", "bitrate": 0, "duration": 210, "track_number": 3}
    mp3 = {"file_path": "/m/a/song.mp3", "bitrate": 320, "duration": 210, "track_number": 3}
    assert pick_duplicate_to_keep([mp3, flac]) is flac
    assert pick_duplicate_to_keep([flac, mp3]) is flac


def test_empty_group_has_no_keeper() -> None:
    assert pick_duplicate_to_keep([]) is None


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(_build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
