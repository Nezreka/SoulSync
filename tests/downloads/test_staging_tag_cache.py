"""The staging scan must not re-read every tag for every download task.

``_get_staging_file_cache`` walks the staging tree before each download task
to check whether the track is already staged. Walking + stat-ing is cheap;
reading the tags is not (~60ms/file cold, i.e. minutes for a few thousand
files). The cache used to be keyed by ``batch_id``, but the wishlist
dispatches ONE BATCH PER TRACK — so the key never repeated, every track
re-read every tag in staging, and each download stalled for minutes before
its first search. That is longer than the 30-45s search timeout, which is how
tracks that were perfectly available ended up marked "Download failed".

The cache is now keyed per file by ``(path, mtime, size)``, so a scan only
pays for files that are new or actually changed.
"""

from __future__ import annotations

import os


def _write_audio(path, data=b'x' * 64):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(data)


def _caches(web_server):
    """Whichever staging cache the module has — so these tests exercise
    behaviour rather than passing/failing on a symbol rename."""
    return [c for c in (getattr(web_server, '_staging_tag_cache', None),
                        getattr(web_server, '_staging_cache', None))
            if c is not None]


def _cached_paths(web_server):
    """Every staging path currently retained, whatever the cache shape."""
    paths = set()
    for cache in _caches(web_server):
        for key, value in cache.items():
            if isinstance(value, list):      # old shape: batch_id -> [file dicts]
                paths.update(f.get('full_path', '') for f in value)
            else:                            # new shape: path -> (mtime, size, meta)
                paths.add(key)
    return {p for p in paths if p}


def _install_staging(monkeypatch, tmp_path, files):
    """Point web_server at a staging dir and count tag reads."""
    import web_server

    for name in files:
        _write_audio(str(tmp_path / name))

    monkeypatch.setattr(web_server, 'get_staging_path', lambda: str(tmp_path))
    for cache in _caches(web_server):
        cache.clear()

    calls = []

    def fake_read(full_path, rel_path):
        calls.append(full_path)
        return {
            'title': os.path.basename(full_path),
            'artist': 'A',
            'albumartist': 'A',
            'album': 'Alb',
            'track_number': 1,
            'disc_number': 1,
        }

    monkeypatch.setattr(web_server, '_read_staging_file_metadata', fake_read)
    return web_server, calls


def test_tags_are_not_reread_for_a_different_batch(monkeypatch, tmp_path):
    """The regression: a second batch_id must not re-read every tag."""
    web_server, calls = _install_staging(
        monkeypatch, tmp_path, ['a/one.flac', 'b/two.mp3', 'three.m4a'])

    first = web_server._get_staging_file_cache('batch-1')
    assert len(first) == 3
    assert len(calls) == 3, 'first scan must read each file once'

    # Pre-fix this re-read all three files, because the cache key was the
    # batch_id and the wishlist never reuses one.
    second = web_server._get_staging_file_cache('batch-2')
    assert len(second) == 3
    assert len(calls) == 3, 'second scan must reuse cached tags'

    assert {f['full_path'] for f in first} == {f['full_path'] for f in second}


def test_changed_mtime_reloads_that_file_only(monkeypatch, tmp_path):
    """A retag bumps mtime but can leave size identical — must not go stale."""
    web_server, calls = _install_staging(
        monkeypatch, tmp_path, ['one.flac', 'two.flac'])

    web_server._get_staging_file_cache('batch-1')
    assert len(calls) == 2

    # Same size, newer mtime — exactly what mutagen's .save() does to a FLAC
    # whose padding absorbs the tag change. A (path, size) key misses this.
    target = str(tmp_path / 'one.flac')
    st = os.stat(target)
    os.utime(target, (st.st_atime + 10, st.st_mtime + 10))
    assert os.stat(target).st_size == st.st_size

    calls.clear()
    web_server._get_staging_file_cache('batch-2')
    assert calls == [target], 'only the retagged file should be re-read'


def test_vanished_files_are_dropped_from_cache(monkeypatch, tmp_path):
    """The old dict grew per batch_id and was never pruned."""
    web_server, _calls = _install_staging(
        monkeypatch, tmp_path, ['one.flac', 'two.flac'])

    web_server._get_staging_file_cache('batch-1')
    assert _cached_paths(web_server) == {
        str(tmp_path / 'one.flac'), str(tmp_path / 'two.flac')}

    gone = str(tmp_path / 'two.flac')
    os.remove(gone)
    remaining = web_server._get_staging_file_cache('batch-2')

    assert len(remaining) == 1
    # Pre-fix the per-batch lists were never freed, so batch-1's entry for the
    # deleted file stayed resident (and grew again with every new batch_id).
    assert gone not in _cached_paths(web_server), 'stale entry must be pruned'


def test_removed_private_staging_root_is_pruned(monkeypatch, tmp_path):
    """An album-bundle batch's private staging dir can be cleaned up whole
    once the batch finishes. That must not orphan its cache entries forever —
    the early-return for a missing directory has to prune too, not just the
    walk path (CodeRabbit #18, review 844c9c72)."""
    import web_server

    root = tmp_path / 'private-staging'
    for name in ('one.flac', 'two.flac'):
        _write_audio(str(root / name))

    monkeypatch.setattr(web_server, '_get_album_bundle_staging_path',
                         lambda batch_id: str(root))
    monkeypatch.setattr(web_server, '_read_staging_file_metadata',
                         lambda full_path, rel_path: {
                             'title': os.path.basename(full_path), 'artist': 'A',
                             'albumartist': 'A', 'album': 'Alb',
                             'track_number': 1, 'disc_number': 1})
    for cache in _caches(web_server):
        cache.clear()

    found = web_server._get_staging_file_cache('bundle-batch')
    assert len(found) == 2
    assert _cached_paths(web_server) == {str(root / 'one.flac'), str(root / 'two.flac')}

    # The whole batch-private root disappears (batch finished, cleaned up) —
    # not just one file within it.
    import shutil
    shutil.rmtree(root)

    empty = web_server._get_staging_file_cache('bundle-batch')

    assert empty == []
    assert _cached_paths(web_server) == set(), \
        'cache entries under a removed staging root must be evicted, not orphaned'
