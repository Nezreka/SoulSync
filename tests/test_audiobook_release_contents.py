"""Tests for core/audiobook_release_contents.py — what is inside a release.

Hermetic: no indexer is reached. Torrent payloads are built here with the same
bencode the decoder reads, and NZBs are literal XML.
"""

from unittest.mock import patch

import pytest

from core.audiobook_release_contents import (
    _parse_nzb,
    contents_for,
    is_audio,
    summarise,
)


def _bencode(value):
    """Just enough bencode to build a .torrent fixture."""
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, str):
        return _bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        out = b"d"
        for key in sorted(value):
            out += _bencode(key) + _bencode(value[key])
        return out + b"e"
    raise TypeError(type(value))


def _torrent(files):
    """A multi-file .torrent carrying (path, length) pairs."""
    return _bencode({
        "announce": "http://tracker.invalid/announce",
        "info": {
            "name": "Project Hail Mary",
            "piece length": 262144,
            "files": [{"path": [name], "length": size} for name, size in files],
        },
    })


def _single_torrent(name, size):
    return _bencode({
        "announce": "http://tracker.invalid/announce",
        "info": {"name": name, "length": size, "piece length": 262144},
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["a.mp3", "B.M4B", "x.flac", "y.opus"])
def test_audio_is_recognised(name):
    assert is_audio(name) is True


@pytest.mark.parametrize("name", ["cover.jpg", "notes.txt", "x.pdf", ""])
def test_non_audio_is_not(name):
    assert is_audio(name) is False


def test_a_summary_counts_audio_and_extras_apart():
    # They answer different questions: how many chapters, and how much of this
    # size is not the book.
    files = [
        {"name": "01.mp3", "size": 1000},
        {"name": "02.mp3", "size": 2000},
        {"name": "cover.jpg", "size": 50},
    ]
    out = summarise(files)
    assert out["audio_count"] == 2
    assert out["extra_count"] == 1
    assert out["audio_bytes"] == 3000
    assert out["total_bytes"] == 3050
    assert out["formats"] == ["mp3"]


def test_a_summary_reports_every_audio_format_present():
    files = [{"name": "01.m4b", "size": 1}, {"name": "02.mp3", "size": 1}]
    assert summarise(files)["formats"] == ["m4b", "mp3"]


def test_an_empty_summary_is_all_zeroes():
    assert summarise([]) == {"total": 0, "audio_count": 0, "extra_count": 0,
                             "audio_bytes": 0, "total_bytes": 0, "formats": []}


# ---------------------------------------------------------------------------
# Soulseek — free, no network
# ---------------------------------------------------------------------------

def test_a_soulseek_release_needs_no_network_at_all():
    release = {
        "protocol": "soulseek",
        "soulseek": {"username": "peer", "album_path": "x",
                     "files": [{"filename": "x\\\\Book\\\\01 - One.mp3", "size": 1000},
                               {"filename": "x\\\\Book\\\\02 - Two.mp3", "size": 2000}]},
    }
    with patch("requests.get") as net:
        out = contents_for(release)
    net.assert_not_called()
    assert [f["name"] for f in out["files"]] == ["01 - One.mp3", "02 - Two.mp3"]
    assert out["summary"]["audio_count"] == 2


def test_a_soulseek_release_with_no_files_says_so():
    out = contents_for({"protocol": "soulseek", "soulseek": {"files": []}})
    assert out["files"] == [] and out["note"]


# ---------------------------------------------------------------------------
# Torrent
# ---------------------------------------------------------------------------

def test_a_torrent_lists_its_files_and_sizes():
    payload = _torrent([("01 - Chapter.mp3", 5_000_000), ("cover.jpg", 40_000)])
    release = {"protocol": "torrent", "download_url": "https://indexer/a.torrent"}
    with patch("core.torrent_clients.base.fetch_torrent_payload",
               return_value=(payload, None)):
        out = contents_for(release)

    assert [f["name"] for f in out["files"]] == ["01 - Chapter.mp3", "cover.jpg"]
    assert out["summary"]["audio_count"] == 1
    assert out["summary"]["extra_count"] == 1
    assert out["summary"]["audio_bytes"] == 5_000_000
    assert out["note"] == ""


def test_a_single_file_torrent_is_one_file():
    payload = _single_torrent("Project Hail Mary.m4b", 900_000_000)
    with patch("core.torrent_clients.base.fetch_torrent_payload",
               return_value=(payload, None)):
        out = contents_for({"protocol": "torrent",
                            "download_url": "https://indexer/a.torrent"})
    assert len(out["files"]) == 1
    assert out["files"][0]["size"] == 900_000_000


def test_chapters_are_listed_in_natural_order():
    # Chapter 2 before chapter 10, the way it will play, not the way a plain
    # string sort would put it.
    payload = _torrent([("10 - Ten.mp3", 1), ("2 - Two.mp3", 1), ("1 - One.mp3", 1)])
    with patch("core.torrent_clients.base.fetch_torrent_payload",
               return_value=(payload, None)):
        out = contents_for({"protocol": "torrent",
                            "download_url": "https://indexer/a.torrent"})
    assert [f["name"] for f in out["files"]] == ["1 - One.mp3", "2 - Two.mp3", "10 - Ten.mp3"]


def test_a_magnet_only_release_explains_why_it_cannot_be_read():
    # The file list lives in the swarm; getting it means joining, which is a
    # download. Saying so beats showing an empty pack.
    out = contents_for({"protocol": "torrent", "download_url": "",
                        "magnet_uri": "magnet:?xt=urn:btih:abc"})
    assert out["files"] == []
    assert "magnet-only" in out["note"]


def test_a_redirect_to_a_magnet_is_reported_the_same_way():
    with patch("core.torrent_clients.base.fetch_torrent_payload",
               return_value=(None, "magnet:?xt=urn:btih:abc")):
        out = contents_for({"protocol": "torrent",
                            "download_url": "https://indexer/a.torrent"})
    assert "magnet-only" in out["note"]


def test_an_unreachable_indexer_is_reported_not_raised():
    with patch("core.torrent_clients.base.fetch_torrent_payload",
               side_effect=RuntimeError("timeout")):
        out = contents_for({"protocol": "torrent",
                            "download_url": "https://indexer/a.torrent"})
    assert out["files"] == [] and "Could not reach" in out["note"]


def test_an_undecodable_torrent_is_reported_not_raised():
    with patch("core.torrent_clients.base.fetch_torrent_payload",
               return_value=(b"this is not bencode", None)):
        out = contents_for({"protocol": "torrent",
                            "download_url": "https://indexer/a.torrent"})
    assert out["files"] == [] and out["note"]


# ---------------------------------------------------------------------------
# Usenet
# ---------------------------------------------------------------------------

_NZB = b"""<?xml version="1.0" encoding="iso-8859-1" ?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <file poster="x" subject="Project Hail Mary [01/03] - &quot;01 - One.mp3&quot; yEnc">
    <segments>
      <segment bytes="500000" number="1">a</segment>
      <segment bytes="500000" number="2">b</segment>
    </segments>
  </file>
  <file poster="x" subject="Project Hail Mary [02/03] - &quot;02 - Two.mp3&quot; yEnc">
    <segments><segment bytes="750000" number="1">c</segment></segments>
  </file>
  <file poster="x" subject="Project Hail Mary [03/03] - &quot;cover.jpg&quot; yEnc">
    <segments><segment bytes="40000" number="1">d</segment></segments>
  </file>
</nzb>
"""


def test_an_nzb_lists_its_files_with_summed_segment_sizes():
    files, note = _parse_nzb(_NZB)
    assert note == ""
    by_name = {f["name"]: f["size"] for f in files}
    assert by_name["01 - One.mp3"] == 1_000_000
    assert by_name["02 - Two.mp3"] == 750_000
    assert by_name["cover.jpg"] == 40_000


def test_an_nzb_release_reads_end_to_end():
    class _Resp:
        status_code = 200
        content = _NZB

    with patch("requests.get", return_value=_Resp()):
        out = contents_for({"protocol": "usenet",
                            "download_url": "https://indexer/a.nzb"})
    assert out["summary"]["audio_count"] == 2
    assert out["summary"]["extra_count"] == 1


def test_a_subject_with_no_quoted_filename_still_counts():
    # The quoting is a convention, not a rule. Dropping the file would
    # under-report the pack.
    raw = b'<nzb><file subject="some unquoted posting"><segments>' \
          b'<segment bytes="10">a</segment></segments></file></nzb>'
    files, note = _parse_nzb(raw)
    assert note == ""
    assert files[0]["name"] == "some unquoted posting"


def test_a_malformed_nzb_is_reported_not_raised():
    files, note = _parse_nzb(b"<nzb><file subject=")
    assert files == [] and note


def test_an_nzb_with_no_files_says_so():
    files, note = _parse_nzb(b"<nzb></nzb>")
    assert files == [] and note


def test_an_unreachable_nzb_is_reported_not_raised():
    with patch("requests.get", side_effect=RuntimeError("timeout")):
        out = contents_for({"protocol": "usenet",
                            "download_url": "https://indexer/a.nzb"})
    assert out["files"] == [] and "Could not reach" in out["note"]


def test_an_nzb_error_status_is_reported():
    class _Resp:
        status_code = 500
        content = b""

    with patch("requests.get", return_value=_Resp()):
        out = contents_for({"protocol": "usenet",
                            "download_url": "https://indexer/a.nzb"})
    assert out["files"] == [] and out["note"]


# ---------------------------------------------------------------------------
# Anything else
# ---------------------------------------------------------------------------

def test_an_unknown_protocol_is_reported_not_raised():
    out = contents_for({"protocol": "carrier pigeon"})
    assert out["files"] == [] and out["note"]


def test_a_release_with_no_link_is_reported():
    out = contents_for({"protocol": "usenet", "download_url": ""})
    assert out["files"] == [] and out["note"]


def test_a_preview_never_raises_whatever_it_is_handed():
    # A preview failing must never be the reason somebody cannot grab a book.
    for release in ({}, {"protocol": None}, {"protocol": "torrent"},
                    {"protocol": "soulseek", "soulseek": None}):
        assert isinstance(contents_for(release), dict)
