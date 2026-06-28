"""Pure orphan-detection rules for the review-queue cleanup (#934 follow-up)."""

from core.downloads.orphan_history import find_orphan_history_ids


def _rows(*specs):
    # specs: (id, file_path, exists?)
    return [{'id': i, 'file_path': p, '_exists': e} for i, p, e in specs]


def _resolve(row):
    # stand-in for _resolve_history_audio_path: returns a path or None
    return '/on/disk' if row.get('_exists') else None


def test_only_missing_files_are_orphans():
    rows = _rows((1, '/a.flac', True), (2, '/b.flac', False), (3, '/c.flac', True))
    out = find_orphan_history_ids(rows, _resolve)
    assert out['orphan_ids'] == [2]
    assert out['checked'] == 3
    assert out['suspicious'] is False


def test_rows_without_path_are_skipped():
    rows = _rows((1, '', False), (2, None, False), (3, '/c.flac', False))
    out = find_orphan_history_ids(rows, _resolve)
    assert out['checked'] == 1
    assert out['orphan_ids'] == [3]


def test_all_missing_with_enough_rows_is_suspicious():
    rows = _rows(*[(i, f'/x{i}.flac', False) for i in range(6)])
    out = find_orphan_history_ids(rows, _resolve)
    assert out['suspicious'] is True          # mount-down signature -> caller refuses
    assert len(out['orphan_ids']) == 6


def test_all_missing_but_few_rows_is_not_suspicious():
    rows = _rows((1, '/x.flac', False), (2, '/y.flac', False))
    out = find_orphan_history_ids(rows, _resolve)
    assert out['suspicious'] is False         # too few to suspect an outage
    assert out['orphan_ids'] == [1, 2]


def test_some_present_is_never_suspicious():
    rows = _rows(*[(i, f'/x{i}.flac', False) for i in range(8)], (99, '/real.flac', True))
    out = find_orphan_history_ids(rows, _resolve)
    assert out['suspicious'] is False         # at least one file exists -> library is up
    assert 99 not in out['orphan_ids']
