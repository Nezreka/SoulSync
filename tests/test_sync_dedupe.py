"""Sync must push a ratingKey-deduped track list (#905).

The dedup was computed but the dispatch sent the raw matched list, so a track
matched by more than one source entry got pushed multiple times — and on
reconcile/replace that re-added it every sync (playlists doubling). This guards
the pure dedup helper the dispatch now uses.
"""

from __future__ import annotations

from services.sync_service import _dedupe_by_rating_key


class _T:
    def __init__(self, rk):
        self.ratingKey = rk


def test_removes_duplicate_rating_keys_preserving_order():
    a, b, c = _T('1'), _T('2'), _T('3')
    dup_b = _T('2')
    out = _dedupe_by_rating_key([a, b, c, dup_b, a])
    assert [t.ratingKey for t in out] == ['1', '2', '3']
    assert out[1] is b  # first-seen object kept, not the later duplicate


def test_no_duplicates_is_identity():
    items = [_T('1'), _T('2'), _T('3')]
    assert _dedupe_by_rating_key(items) == items


def test_drops_tracks_without_rating_key():
    class _NoKey:
        pass
    out = _dedupe_by_rating_key([_T('1'), _NoKey(), _T('2')])
    assert [t.ratingKey for t in out] == ['1', '2']


def test_empty():
    assert _dedupe_by_rating_key([]) == []
