"""web_server.py keeps its OWN copy of `_STREAMING_SOURCE_NAMES`, separate
from core/downloads/monitor.py's and core/downloads/status.py's. When
torrent/usenet acquisition sources were added to the other two copies, this
one was left stale — `_infer_candidate_source` kept bucketing torrent/usenet
search candidates under 'soulseek' in the UI even though their retry-budget
bookkeeping (via monitor.py) correctly treated them as their own source.
"""

import web_server


def test_infer_candidate_source_recognizes_torrent_and_usenet():
    assert web_server._infer_candidate_source('torrent') == 'torrent'
    assert web_server._infer_candidate_source('usenet') == 'usenet'


def test_infer_candidate_source_recognizes_amazon():
    assert web_server._infer_candidate_source('amazon') == 'amazon'


def test_infer_candidate_source_still_buckets_soulseek_peers_as_soulseek():
    assert web_server._infer_candidate_source('some-peer-username') == 'soulseek'
    assert web_server._infer_candidate_source('') == 'soulseek'
