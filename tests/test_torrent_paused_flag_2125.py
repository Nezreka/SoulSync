"""#2125: pausing a torrent on the clients tab looked like it did nothing.

a finished torrent you pause is still 'completed' to soulsync (qbittorrent
calls it pausedUP, 5.x stoppedUP; transmission and deluge report a stopped
100% torrent as completed too), and the tab only offered Resume for 'paused'.
so a paused seeding torrent read Complete and kept a Pause button that had
nothing left to pause. every adapter now says when the client has it stopped.
"""

from __future__ import annotations

import pytest

from core.torrent_clients.aria2 import Aria2Adapter
from tests.test_torrent_client_adapters import (_deluge_with_config, _qbit_with_config,
                                                _trans_with_config)


@pytest.mark.parametrize("qstate, state, paused", [
    ("stoppedUP", "completed", True),     # qbittorrent 5: a paused seeding torrent
    ("pausedUP", "completed", True),      # qbittorrent 4
    ("stoppedDL", "paused", True),
    ("pausedDL", "paused", True),
    ("uploading", "seeding", False),
    ("stalledUP", "seeding", False),
    ("downloading", "downloading", False),
])
def test_qbittorrent(qstate, state, paused):
    st = _qbit_with_config()._parse_status({"hash": "h", "name": "n", "state": qstate,
                                            "progress": 1.0})
    assert (st.state, st.paused) == (state, paused)


def test_transmission():
    a = _trans_with_config()
    done = a._parse_status({"hashString": "h", "status": 0, "percentDone": 1.0})
    assert (done.state, done.paused) == ("completed", True)
    assert a._parse_status({"hashString": "h", "status": 6, "percentDone": 1.0}).paused is False


def test_deluge():
    a = _deluge_with_config()
    done = a._parse_status({"hash": "h", "state": "Paused", "progress": 100})
    assert (done.state, done.paused) == ("completed", True)
    assert a._parse_status({"hash": "h", "state": "Seeding", "progress": 100}).paused is False


def test_aria2():
    a = Aria2Adapter.__new__(Aria2Adapter)
    st = a._parse_status({"gid": "g", "status": "paused", "totalLength": "10",
                          "completedLength": "10"})
    assert st.paused is True
    assert a._parse_status({"gid": "g", "status": "active", "totalLength": "10",
                            "completedLength": "10"}).paused is False
