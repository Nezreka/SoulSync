"""Reorganize title matcher: featured-artist credits must not block a match (#914).

iTunes appends "(feat. X)" to track titles while a user's file is often just the
bare title. Before the fix that extra credit dropped the substring ratio below the
match threshold, so a correctly-identified track was reported as "no matching track
in the iTunes tracklist". The credit is metadata, so it's stripped before scoring.
"""

from __future__ import annotations


# ── #1078: feat_in_title must not be STRIPPED by the reorganize ──────────────
# QT3496: files already correctly titled "Song (feat. X)" were flagged and
# "corrected" to "Song" — the clean title (which builds the filename) came
# straight from the API track name, and feat_in_title only ever re-added the
# credit to the TAG, never the filename, and only when the API carried >1
# artist. The helpers below carry the credit onto the clean title itself.

from core.library_reorganize import (   # noqa: E402
    _apply_feat_credit,
    _build_album_info,
    _build_post_process_context,
    _extract_feat_credit,
)


def test_extract_feat_credit_variants():
    assert _extract_feat_credit('The Chase (feat. Big Artist)') == '(feat. Big Artist)'
    assert _extract_feat_credit('Song [ft A]') == '[ft A]'
    assert _extract_feat_credit('Song ft. A & B') == 'ft. A & B'
    assert _extract_feat_credit('The Chase') == ''
    assert _extract_feat_credit('') == ''


def test_apply_feat_credit_from_api_artists():
    # API name lacks the credit but the track lists featured artists → rebuild it
    out = _apply_feat_credit('The Chase',
                             [{'name': 'Main Artist'}, {'name': 'Big Artist'}],
                             'The Chase (feat. Big Artist)')
    assert out == 'The Chase (feat. Big Artist)'


def test_apply_feat_credit_preserves_local_when_api_has_one_artist():
    # API only knows the primary → carry the user's own credit forward
    out = _apply_feat_credit('The Chase', [{'name': 'Main Artist'}],
                             'The Chase (feat. Obscure Guest)')
    assert out == 'The Chase (feat. Obscure Guest)'


def test_apply_feat_credit_never_double_credits():
    out = _apply_feat_credit('The Chase (feat. Big Artist)',
                             [{'name': 'Main Artist'}, {'name': 'Big Artist'}],
                             'The Chase (feat. Big Artist)')
    assert out == 'The Chase (feat. Big Artist)'


def test_apply_feat_credit_leaves_clean_titles_clean():
    out = _apply_feat_credit('Plain Song', [{'name': 'Main Artist'}], 'Plain Song')
    assert out == 'Plain Song'


def _album():
    return {'id': 'AL1', 'name': 'The Album', 'release_date': '2020-01-01',
            'total_tracks': 10, 'images': [{'url': ''}]}


def test_reorganize_keeps_feat_in_filename_when_setting_on(monkeypatch):
    """The reported bug end to end: with feat_in_title ON, the clean title the
    filename is built from must carry the credit, so an already-correct file
    isn't flagged for stripping."""
    monkeypatch.setattr('core.library_reorganize._feat_in_title_enabled', lambda: True)
    api_track = {'name': 'The Chase', 'track_number': 3, 'disc_number': 1,
                 'artists': [{'name': 'Main Artist'}, {'name': 'Big Artist'}]}
    ctx = _build_post_process_context(_album(), api_track, 'Main Artist',
                                      'The Album', 1,
                                      local_title='The Chase (feat. Big Artist)')
    assert ctx['original_search_result']['title'] == 'The Chase (feat. Big Artist)'
    assert ctx['original_search_result']['spotify_clean_title'] == 'The Chase (feat. Big Artist)'
    from core.imports.paths import build_final_path_for_track
    ai = _build_album_info(ctx)
    path, _ = build_final_path_for_track(ctx, ctx['spotify_artist'], ai, '.flac',
                                         create_dirs=False)
    import os
    assert os.path.basename(path) == '03 - The Chase (feat. Big Artist).flac'


def test_reorganize_leaves_title_alone_when_setting_off(monkeypatch):
    """feat_in_title OFF is unchanged behavior — featured artists belong in the
    ARTIST tag, so the title/filename stays the bare API name."""
    monkeypatch.setattr('core.library_reorganize._feat_in_title_enabled', lambda: False)
    api_track = {'name': 'The Chase', 'track_number': 3, 'disc_number': 1,
                 'artists': [{'name': 'Main Artist'}, {'name': 'Big Artist'}]}
    ctx = _build_post_process_context(_album(), api_track, 'Main Artist',
                                      'The Album', 1,
                                      local_title='The Chase (feat. Big Artist)')
    assert ctx['original_search_result']['title'] == 'The Chase'
