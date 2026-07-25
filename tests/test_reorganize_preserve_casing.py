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


# ── #1080: the user's own album year is kept, not the source's original ──────

def test_keep_user_year_prefers_user_when_preserving():
    from core.library_reorganize import _keep_user_year
    assert _keep_user_year("2020-05-01", "2023") == "2023"     # reissue year kept
    assert _keep_user_year("2020-05-01", "2020") == "2020-05-01"  # same → source
    assert _keep_user_year("2020-05-01", None) == "2020-05-01"    # no user year
    assert _keep_user_year("2020-05-01", "bogus") == "2020-05-01"  # not a 4-digit year


def test_keep_user_year_disabled_passthrough(monkeypatch):
    monkeypatch.setattr("core.library_reorganize._preserve_casing_enabled", lambda: False)
    from core.library_reorganize import _keep_user_year
    assert _keep_user_year("2020-05-01", "2023") == "2020-05-01"


def test_context_carries_user_year_into_release_date():
    ctx = _build_post_process_context(
        _album(), {"name": "Song", "track_number": 1, "disc_number": 1, "artists": [{"name": "A"}]},
        "A", "Alb", 1, local_year="2023")
    assert ctx["spotify_album"]["release_date"] == "2023"


# ── end-to-end preview: an already-organized file is left UNCHANGED (#1080) ──

def test_preview_leaves_already_organized_file_unchanged(monkeypatch, tmp_path):
    """The real complaint: run the actual preview against a file already
    organized with the user's casing + year, and confirm it reports NO change
    (not just that the path builder emits the right string). Mirrors QT3496's
    'The Violence (Sikdope Remix) [2019]' (casing) + 'Best Of Underoath [2014]'
    (casing + year) screenshots — the source returns lowercase 'remix'/'of'
    and year 2013/2020, which used to churn."""
    import core.library_reorganize as lr
    from core.imports.paths import build_final_path_for_track

    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: True)
    monkeypatch.setattr("core.imports._get_config_manager" if False else
                        "core.imports.paths._get_config_manager",
                        lambda: _TemplateCM())

    album_data = {"id": "AL1", "title": "The Violence (Sikdope Remix)",
                  "artist_name": "Asking Alexandria", "artist_id": "AR1",
                  "year": 2019, "spotify_album_id": "sp1"}
    tracks = [{"id": "T1", "title": "The Violence (Sikdope Remix)",
               "track_number": 1, "file_path": "X", "duration": 200}]
    api_album = {"id": "sp1", "name": "The Violence (Sikdope remix)",
                 "release_date": "2020-01-01", "album_type": "single",
                 "total_tracks": 1, "images": [{"url": ""}]}
    api_tracks = [{"name": "The Violence (Sikdope remix)", "track_number": 1,
                   "disc_number": 1, "artists": [{"name": "Asking Alexandria"}]}]
    monkeypatch.setattr(lr, "load_album_and_tracks",
                        lambda db, aid: (dict(album_data), [dict(t) for t in tracks]))
    monkeypatch.setattr(lr, "_resolve_source",
                        lambda ad, ps, strict_source=False, **kw: ("spotify", api_album, api_tracks))
    monkeypatch.setattr(lr, "_feat_in_title_enabled", lambda: False)

    organized = ("/xfer/A/Asking Alexandria/The Violence (Sikdope Remix) "
                 "[2019] [Single]/01 - The Violence (Sikdope Remix).flac")

    def _preview():
        return lr.preview_album_reorganize(
            album_id="AL1", db=None, transfer_dir="/xfer",
            resolve_file_path_fn=lambda p: organized,
            build_final_path_fn=build_final_path_for_track)["tracks"][0]

    assert _preview()["unchanged"] is True            # preserve on → no rename

    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: False)
    assert _preview()["unchanged"] is False           # off → would churn to the source


class _TemplateCM:
    """Minimal config-manager stub feeding QT3496's templates + transfer dir."""
    _vals = {
        "file_organization.templates": {
            "album_path": "$artistletter/$albumartist/$album [$year] [$albumtype]/$disc$track - $title",
            "single_path": "$artistletter/$albumartist/$album [$year] [Single]/$track - $title",
        },
        "soulseek.transfer_path": "/xfer",
    }

    def get(self, key, default=None):
        return self._vals.get(key, default)
