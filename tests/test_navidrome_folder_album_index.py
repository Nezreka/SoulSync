"""The music-folder album index in the Navidrome client.

`_get_folder_album_ids()` pages getAlbumList2 once per process and caches the
set; the scan then filters every artist's albums against it. Two ways that
went wrong in production:

1. `clear_cache()` left the index alone, so a process that added albums after
   startup had a permanently stale set — the incremental scan detected the new
   albums via getAlbumList2(type=newest), then filtered them straight back out
   because their IDs weren't in the index yet. Only a restart rebuilt it.
   The index is now reset by `clear_cache()` and rebuilt per scan.

2. A failed page mid-pagination cached the *partial* set as complete, so real
   albums were filtered out of every artist while the scan still looked fully
   trusted. A failed page now returns None (unverified) without caching, and
   the caller skips the folder filter for that scan.
"""

from __future__ import annotations

from core.navidrome_client import NavidromeClient


def _paged_client(pages, music_folder_id='folder1'):
    """pages: getAlbumList2 album-id list per offset page; None = failed page."""
    c = NavidromeClient()
    c.base_url = 'http://navidrome'
    c.music_folder_id = music_folder_id
    calls = {'n': 0}

    def fake_request(endpoint, params=None, **kw):
        if endpoint == 'getAlbumList2':
            idx = calls['n']
            calls['n'] += 1
            # repeat the last page on retry, like a real server would
            page = pages[min(idx, len(pages) - 1)]
            if page is None:
                return None
            return {'albumList2': {'album': [{'id': aid} for aid in page]}}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    c._make_request = fake_request
    return c


def test_clear_cache_resets_the_folder_album_index():
    c = _paged_client([['a1', 'a2']])
    assert c._get_folder_album_ids() == {'a1', 'a2'}
    # a new album lands on the server after the index was built
    c.clear_cache()
    assert c._folder_album_ids is None
    c._make_request = _paged_client([['a1', 'a2', 'a3']])._make_request
    assert c._get_folder_album_ids() == {'a1', 'a2', 'a3'}


def test_failed_page_returns_none_without_caching_a_partial_set():
    # a full first page (500) forces the loop onto page two, which fails
    c = _paged_client([[f'a{i}' for i in range(500)], None])
    assert c._get_folder_album_ids() is None
    assert c._folder_album_ids is None, "a partial index must never be cached as complete"
    # next call retries instead of serving the partial set
    assert c._get_folder_album_ids() is None


def test_no_music_folder_selected_returns_none_without_requesting():
    c = _paged_client([['a1']], music_folder_id=None)
    assert c._get_folder_album_ids() is None


def test_empty_folder_caches_the_empty_set():
    c = _paged_client([[]])
    assert c._get_folder_album_ids() == set()
