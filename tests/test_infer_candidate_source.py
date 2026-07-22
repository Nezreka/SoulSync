"""web_server.py, core/downloads/monitor.py and core/downloads/status.py used
to each hardcode their own copy of `_STREAMING_SOURCE_NAMES`. When
torrent/usenet acquisition sources were added to the other two copies, this
one was left stale — `_infer_candidate_source` kept bucketing torrent/usenet
search candidates under 'soulseek' in the UI even though their retry-budget
bookkeeping (via monitor.py) correctly treated them as their own source. All
three now import the single canonical set from
`core.downloads.source_policy.STREAMING_SOURCE_NAMES`, so this can't recur.
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
