"""5BILLION's Unraid report, round 2: Navidrome answers getArtists on an
EMPTY selected library with a hard API error ('Library not found or empty')
instead of an empty envelope — so the verified-empty logic never engaged and
the deep scan kept aborting instead of removing stale artists.

The fix is a two-key turn: that error is believed as 'empty' ONLY when the
selected folder provably exists (getMusicFolders) — a wrong folder id stays
a failure and can never wipe a library.
"""

from __future__ import annotations

from core.navidrome_client import NavidromeClient


def _client(*, folders, api_error=None, artists_response=None):
    c = NavidromeClient()
    c.base_url = "http://navidrome"
    c.username = "u"
    c.password = "p"
    c.music_folder_id = "2"
    c._connection_attempted = True

    def fake_request(endpoint, params=None):
        if endpoint == 'getArtists':
            if api_error is not None:
                c.last_api_error = api_error
                return None
            c.last_api_error = None
            return artists_response
        raise AssertionError("unexpected endpoint " + endpoint)

    c._make_request = fake_request
    c._fetch_music_folders = lambda: folders
    c.ensure_connection = lambda: True
    return c


def test_empty_library_error_with_real_folder_is_verified_empty():
    c = _client(folders=[{"id": 1, "name": "Music"}, {"id": 2, "name": "Empty"}],
                api_error="Library not found or empty")
    assert c.get_all_artists() == []
    assert c.last_fetch_failed is False        # verified empty → stale removal may run


def test_same_error_with_unknown_folder_stays_a_failure():
    c = _client(folders=[{"id": 1, "name": "Music"}],   # folder 2 doesn't exist
                api_error="Library not found or empty")
    assert c.get_all_artists() == []
    assert c.last_fetch_failed is True         # never wipe on a misconfig


def test_other_api_errors_stay_failures():
    c = _client(folders=[{"id": 2, "name": "Empty"}],
                api_error="Wrong username or password")
    assert c.get_all_artists() == []
    assert c.last_fetch_failed is True


def test_no_folder_selected_empty_error_is_verified_when_server_answers():
    """Round 3 (5BILLION): with NO folder selected the old guard could never
    verify-empty at all — the branch required a selected id, so an empty
    server erred forever. A server-wide 'empty' answer plus a LIVE
    getMusicFolders response is a verified empty server: there is no
    misconfigured folder id to protect against when none is selected."""
    c = _client(folders=[{"id": 2, "name": "x"}], api_error="Library not found or empty")
    c.music_folder_id = None
    assert c.get_all_artists() == []
    assert c.last_fetch_failed is False        # verified empty → stale removal may run


def test_no_folder_selected_stays_failure_when_folders_fetch_dead():
    """The second signal is required: if getMusicFolders does NOT answer with a
    list, the server isn't demonstrably alive — never verify on one signal."""
    c = _client(folders=None, api_error="Library not found or empty")
    c.music_folder_id = None
    assert c.get_all_artists() == []
    assert c.last_fetch_failed is True


def test_normal_empty_envelope_still_verified():
    c = _client(folders=[], artists_response={"status": "ok", "artists": {"index": []}})
    assert c.get_all_artists() == []
    assert c.last_fetch_failed is False


def test_worker_abort_is_now_observable():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "database_update_worker.py").read_text(
        encoding="utf-8", errors="replace")
    assert "Deep scan aborted: artists fetch UNVERIFIED" in src
    assert "last_api_error" in src


# ── round 4: the REMOVAL-detection fetchers had none of this ─────────────────
#
# Rounds 1-3 all hardened `get_all_artists`. Removal detection does not call it
# — it calls `get_all_artist_ids` / `get_all_album_ids`, which returned a bare
# empty set with no way to tell "empty library" from "API failed". So even after
# the deep scan learned to trust a verified empty, removal still refused to act
# on one. Both fetchers now honour the same contract, via the same helper.

def _ids_client(*, folders, artists_error=None, albums_pages=None,
                albums_error_at=None, folder_id="2"):
    """A client whose id endpoints can fail at a chosen pagination offset."""
    c = NavidromeClient()
    c.base_url = "http://navidrome"
    c.username = "u"
    c.password = "p"
    c.music_folder_id = folder_id
    c._connection_attempted = True

    def fake_request(endpoint, params=None):
        if endpoint == 'getArtists':
            if artists_error is not None:
                c.last_api_error = artists_error
                return None
            c.last_api_error = None
            return {'artists': {'index': []}}
        if endpoint == 'getAlbumList2':
            offset = (params or {}).get('offset', 0)
            if albums_error_at is not None and offset >= albums_error_at:
                c.last_api_error = "Library not found or empty"
                return None
            c.last_api_error = None
            return {'albumList2': {'album': (albums_pages or {}).get(offset, [])}}
        raise AssertionError("unexpected endpoint " + endpoint)

    c._make_request = fake_request
    c._fetch_music_folders = lambda: folders
    c.ensure_connection = lambda: True
    return c


_REAL_FOLDERS = [{"id": 1, "name": "Music"}, {"id": 2, "name": "Empty"}]


def test_artist_ids_report_a_verified_empty_library():
    c = _ids_client(folders=_REAL_FOLDERS, artists_error="Library not found or empty")
    assert c.get_all_artist_ids() == set()
    assert c.last_fetch_failed is False


def test_artist_ids_keep_a_bad_folder_id_as_a_failure():
    """The two-key turn applies here too: a stale folder id must never read as
    empty, because empty is what authorises deletion."""
    c = _ids_client(folders=[{"id": 9, "name": "Other"}],
                    artists_error="Library not found or empty")
    assert c.get_all_artist_ids() == set()
    assert c.last_fetch_failed is True


def test_artist_ids_clear_the_flag_on_a_SUCCESSFUL_fetch():
    """A success that leaves the flag set would tell the next reader the fetch
    failed. Harmless today only because a non-empty set passes the check on its
    own — exactly the kind of latent lie this bug is made of."""
    c = _ids_client(folders=_REAL_FOLDERS)
    c._make_request = lambda e, p=None: {'artists': {'index': [
        {'artist': [{'id': 'a1'}, {'id': 'a2'}]}]}}
    assert c.get_all_artist_ids() == {'a1', 'a2'}
    assert c.last_fetch_failed is False


def test_album_ids_report_a_verified_empty_library():
    c = _ids_client(folders=_REAL_FOLDERS, albums_error_at=0)
    assert c.get_all_album_ids() == set()
    assert c.last_fetch_failed is False


def test_a_PARTIAL_album_read_is_discarded_not_returned():
    """Page 1 lands, page 2 fails. Returning the 500 albums we did read would
    let removal detection treat every UNREAD album as deleted from the server.
    The partial is dropped and the fetch stays flagged as failed."""
    c = _ids_client(folders=_REAL_FOLDERS,
                    albums_pages={0: [{'id': f'b{i}'} for i in range(500)]},
                    albums_error_at=500)

    assert c.get_all_album_ids() == set(), "a truncated catalogue must not be returned"
    assert c.last_fetch_failed is True, "a partial read is a failure, not an empty library"


def test_a_complete_album_read_clears_the_flag():
    c = _ids_client(folders=_REAL_FOLDERS,
                    albums_pages={0: [{'id': 'b1'}, {'id': 'b2'}]})
    assert c.get_all_album_ids() == {'b1', 'b2'}
    assert c.last_fetch_failed is False
