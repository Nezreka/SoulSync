"""Regression test for the per-source retry budget's source resolution.

The quarantine-retry engine buckets each download under a logical source so it
can budget retries per source and surface "source 'X' n/budget" to the UI.
Soulseek uses the peer name as the download's username, so unknown usernames
collapse to the 'soulseek' bucket. Torrent/usenet (release) downloads, however,
use their source name as the username and MUST keep their own identity —
otherwise every torrent/usenet retry mislabels itself as 'soulseek' in the UI.
"""

from core.downloads.monitor import _resolve_download_source


def test_streaming_sources_keep_their_name():
    for src in ('youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr', 'soundcloud', 'amazon'):
        assert _resolve_download_source(src) == src


def test_release_sources_keep_their_name():
    # Regression: torrent/usenet were bucketed as 'soulseek', so retry rows for
    # Prowlarr indexer downloads showed "source 'soulseek'" regardless of source.
    assert _resolve_download_source('torrent') == 'torrent'
    assert _resolve_download_source('usenet') == 'usenet'


def test_soulseek_peer_names_collapse_to_soulseek():
    assert _resolve_download_source('some_random_peer') == 'soulseek'
    assert _resolve_download_source('') == 'soulseek'
    assert _resolve_download_source(None) == 'soulseek'
