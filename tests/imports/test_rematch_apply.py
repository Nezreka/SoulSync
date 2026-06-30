"""#889 Phase 4/5: apply a re-identify — stage the file (copy, not move) + build
the hint. Locks down: the original is never touched, the staged name is unique +
keeps the extension, the hint carries the chosen release, and replace_track_id is
set ONLY when 'replace' is ticked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.imports.rematch_apply import (
    build_reidentify_hint,
    stage_file_for_reidentify,
    staged_destination,
)

_FIELDS = {
    "source": "spotify", "track_id": "trk_1", "album_id": "alb_album1",
    "artist_id": "art_1", "track_title": "Song", "album_name": "Album1",
    "artist_name": "Artist", "album_type": "album", "track_number": 5,
    "disc_number": 1, "isrc": "US1234567890",
}


def test_staged_destination_keeps_ext_and_is_traceable():
    dest = staged_destination("/staging", "/lib/EP1/05 - Song.flac", 42)
    assert dest.endswith(".flac")
    assert "[reid-42]" in dest          # traceable to the track + unique per track
    # loose file in staging root → single candidate (os.path.join, not POSIX-only '/')
    assert dest == os.path.join("/staging", "05 - Song [reid-42].flac")


def test_stage_copies_not_moves(tmp_path: Path):
    src = tmp_path / "lib" / "EP1" / "05 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio-bytes")
    staging = tmp_path / "Staging"

    out = stage_file_for_reidentify(str(src), str(staging), 42,
                                    signature_fn=lambda p: "sig123")
    staged = Path(out["staged_path"])
    assert staged.is_file() and staged.read_bytes() == b"audio-bytes"
    assert src.is_file()                 # ORIGINAL untouched (copy, never move)
    assert out["content_hash"] == "sig123"


def test_stage_missing_source_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        stage_file_for_reidentify(str(tmp_path / "gone.flac"), str(tmp_path / "S"), 1)


def test_build_hint_sets_replace_when_ticked():
    h = build_reidentify_hint(42, _FIELDS, "/staging/x.flac", "sig", replace=True)
    assert h.replace_track_id == 42
    assert h.album_id == "alb_album1" and h.source == "spotify"
    assert h.track_number == 5 and h.isrc == "US1234567890"
    assert h.exempt_dedup is True
    assert h.staged_path == "/staging/x.flac" and h.content_hash == "sig"


def test_build_hint_no_replace_when_unticked():
    h = build_reidentify_hint(42, _FIELDS, "/staging/x.flac", "sig", replace=False)
    assert h.replace_track_id is None    # keep original → no deletion
    assert h.exempt_dedup is True        # still bypasses dedup-skip (explicit action)


def test_build_hint_handles_non_numeric_track_id():
    # Jellyfin-style GUID track ids must still round-trip as replace target.
    h = build_reidentify_hint("abc-guid", _FIELDS, "/s/x.flac", None, replace=True)
    assert h.replace_track_id == "abc-guid"
