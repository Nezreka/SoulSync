"""Library Organize must not churn on cosmetic casing (#1078, QT3496).

The reorganize rebuilds each title/album from the metadata source verbatim,
so it adopted the SOURCE's casing — Spotify capitalizing prepositions, an
ALL-CAPS artist, iTunes vs Deezer conventions — and flagged already-organized
files for a rename that only changed letter-case. With
`library.reorganize_preserve_casing` on (default), a difference that is ONLY
case keeps the user's own casing, so both the filename and the title tag stay
put; genuine edits still adopt the source.
"""

from __future__ import annotations

import os

import pytest

from core.library_reorganize import (
    _build_album_info,
    _build_post_process_context,
    _keep_user_casing,
)
from core.imports.paths import build_final_path_for_track


@pytest.fixture(autouse=True)
def _casing_on(monkeypatch):
    monkeypatch.setattr("core.library_reorganize._preserve_casing_enabled", lambda: True)
    monkeypatch.setattr("core.library_reorganize._feat_in_title_enabled", lambda: False)


# ── the pure rule ────────────────────────────────────────────────────────────

def test_keep_user_casing_case_only_keeps_user():
    assert _keep_user_casing("The Chase", "the chase") == "the chase"
    assert _keep_user_casing("GREATEST HITS", "Greatest Hits") == "Greatest Hits"
    assert _keep_user_casing("A Song In The Key", "A Song in the Key") == "A Song in the Key"


def test_keep_user_casing_real_difference_adopts_source():
    # punctuation / words / additions are NOT case-only → source wins
    assert _keep_user_casing("Song (Remix)", "Song") == "Song (Remix)"
    assert _keep_user_casing("Dont Stop", "Don't Stop") == "Dont Stop"
    assert _keep_user_casing("The Chase", "") == "The Chase"
    assert _keep_user_casing("The Chase", None) == "The Chase"


def test_keep_user_casing_disabled_passthrough(monkeypatch):
    monkeypatch.setattr("core.library_reorganize._preserve_casing_enabled", lambda: False)
    assert _keep_user_casing("The Chase", "the chase") == "The Chase"


# ── end to end: title + album ────────────────────────────────────────────────

def _album(name="Greatest Hits"):
    return {"id": "AL1", "name": name, "release_date": "2020-01-01",
            "total_tracks": 10, "images": [{"url": ""}]}


def _ctx(api_title, local_title, api_album="Greatest Hits", db_album="Greatest Hits"):
    return _build_post_process_context(
        _album(api_album),
        {"name": api_title, "track_number": 1, "disc_number": 1, "artists": [{"name": "A"}]},
        "A", db_album, 1, local_title=local_title)


def test_title_casing_preserved_in_filename_and_tag():
    ctx = _ctx("The Chase", "the chase")
    # tag title keeps the user's case
    assert ctx["original_search_result"]["title"] == "the chase"
    assert ctx["original_search_result"]["spotify_clean_title"] == "the chase"
    # ...and so does the filename built from it
    ai = _build_album_info(ctx)
    path, _ = build_final_path_for_track(ctx, ctx["spotify_artist"], ai, ".flac", create_dirs=False)
    assert os.path.basename(path) == "01 - the chase.flac"


def test_album_folder_casing_preserved():
    ctx = _ctx("Song", "Song", api_album="GREATEST HITS", db_album="Greatest Hits")
    assert ctx["spotify_album"]["name"] == "Greatest Hits"


def test_real_title_edit_still_adopts_source():
    ctx = _ctx("Song (Remix)", "Song")
    assert ctx["original_search_result"]["title"] == "Song (Remix)"


def test_disabled_setting_canonicalizes_to_source(monkeypatch):
    monkeypatch.setattr("core.library_reorganize._preserve_casing_enabled", lambda: False)
    ctx = _ctx("The Chase", "the chase", api_album="GREATEST HITS", db_album="Greatest Hits")
    assert ctx["original_search_result"]["title"] == "The Chase"
    assert ctx["spotify_album"]["name"] == "GREATEST HITS"


def test_casing_preserve_composes_with_feat(monkeypatch):
    """Casing preserve runs AFTER feat: a bare source title vs a user's
    feat-tagged title is a real change (feat added), not case-only."""
    monkeypatch.setattr("core.library_reorganize._feat_in_title_enabled", lambda: True)
    ctx = _build_post_process_context(
        _album(), {"name": "The Chase", "track_number": 1, "disc_number": 1,
                   "artists": [{"name": "A"}, {"name": "Big Artist"}]},
        "A", "Greatest Hits", 1, local_title="the chase (feat. big artist)")
    # feat re-added from the API artists, then the WHOLE thing is a case-only
    # match to the user's title → user's casing kept
    assert ctx["original_search_result"]["title"] == "the chase (feat. big artist)"
