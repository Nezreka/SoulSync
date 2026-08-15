"""Reading a .torrent's file list (#1149).

"Prefer a release with verified FLAC files over a title-based guess." We
already fetch the .torrent before handing it to the client (#1139), so the
real list is in memory at the one moment we can still decline to enqueue it.

The contract that matters most: this never raises, and None ("could not read")
is distinct from [] ("read it, declares nothing"). A decoder bug must degrade
to a title guess, not to an outage where nothing downloads.
"""

from __future__ import annotations

from core.quality.torrent_contents import MAX_TORRENT_BYTES, torrent_file_names


def _bencode(value) -> bytes:
    if isinstance(value, int):
        return b'i' + str(value).encode() + b'e'
    if isinstance(value, bytes):
        return str(len(value)).encode() + b':' + value
    if isinstance(value, str):
        return _bencode(value.encode())
    if isinstance(value, list):
        return b'l' + b''.join(_bencode(v) for v in value) + b'e'
    if isinstance(value, dict):
        out = b'd'
        for k in sorted(value):
            out += _bencode(k) + _bencode(value[k])
        return out + b'e'
    raise TypeError(type(value))


def _multi(*names) -> bytes:
    return _bencode({
        b'announce': b'http://tracker.example/announce',
        b'info': {
            b'name': b'Artist - Album',
            b'piece length': 262144,
            b'files': [{b'length': 1000, b'path': n.split('/')} for n in names],
        },
    })


def test_a_multi_file_torrent_lists_every_file():
    payload = _multi('01 - One.flac', '02 - Two.flac', 'cover.jpg')

    assert torrent_file_names(payload) == ['01 - One.flac', '02 - Two.flac', 'cover.jpg']


def test_nested_paths_are_joined():
    payload = _multi('CD1/01 - One.flac', 'CD2/01 - Two.flac')

    assert torrent_file_names(payload) == ['CD1/01 - One.flac', 'CD2/01 - Two.flac']


def test_a_single_file_torrent_reports_its_one_name():
    payload = _bencode({b'info': {b'name': b'Artist - Album.flac', b'length': 1000}})

    assert torrent_file_names(payload) == ['Artist - Album.flac']


def test_unreadable_input_is_None_not_an_exception():
    # None means "no evidence, use the title". It must never mean "reject".
    for junk in (None, b'', b'not bencode at all', b'd', b'i12', 'a string', 42):
        assert torrent_file_names(junk) is None


def test_a_torrent_with_no_info_dict_is_None():
    assert torrent_file_names(_bencode({b'announce': b'http://x/'})) is None


def test_an_info_dict_declaring_nothing_is_empty_not_None():
    """[] is a real answer: it read fine and the release declares no files.
    That is itself a reason to distrust the release, and the caller can tell
    it apart from a parse failure."""
    assert torrent_file_names(_bencode({b'info': {b'piece length': 262144}})) == []


def test_an_oversized_payload_is_refused_rather_than_parsed():
    assert torrent_file_names(b'd' + b'x' * (MAX_TORRENT_BYTES + 1)) is None


def test_non_utf8_names_survive_as_replacement_characters():
    """A mojibake filename must not take the whole parse down."""
    payload = _bencode({
        b'info': {
            b'name': b'Album',
            b'files': [{b'length': 1, b'path': [b'\xff\xfe bad.flac']}],
        },
    })

    names = torrent_file_names(payload)

    assert names is not None and len(names) == 1
    assert names[0].endswith('.flac')


def test_it_feeds_the_format_policy():
    """The two halves working together is the actual feature."""
    from core.quality.release_format import evaluate_release

    payload = _multi('01.mp3', '02.mp3')
    ok, reason = evaluate_release({'flac'}, 'Artist - Album [FLAC]',
                                  file_names=torrent_file_names(payload))

    assert ok is False
    assert 'file list' in reason


# ── the grab-time gate (#1149) ───────────────────────────────────────────────

import asyncio          # noqa: E402
from unittest.mock import AsyncMock, patch          # noqa: E402

import pytest          # noqa: E402

from core.torrent_clients.base import ReleaseRejected, add_torrent_smart          # noqa: E402


class _Adapter:
    def __init__(self):
        self.added_file = None
        self.added_url = None

    async def add_torrent_file(self, payload, category=None, save_path=None):
        self.added_file = payload
        return 'tid-file'

    async def add_torrent(self, url, category=None, save_path=None):
        self.added_url = url
        return 'tid-url'


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _payload(*names):
    return _multi(*names)


def test_a_verified_release_is_handed_to_the_client():
    adapter = _Adapter()
    with patch('core.torrent_clients.base._fetch_torrent_payload_async',
               new=AsyncMock(return_value=(_payload('01.flac'), None))):
        tid = _run(add_torrent_smart(adapter, 'http://indexer/x.torrent',
                                     verify_files=lambda names: (True, 'ok')))

    assert tid == 'tid-file'
    assert adapter.added_file is not None


def test_a_rejected_release_never_reaches_the_client():
    """The whole point: refuse BEFORE the client has it, so it never occupies
    a queue slot or needs cleaning up."""
    adapter = _Adapter()
    with patch('core.torrent_clients.base._fetch_torrent_payload_async',
               new=AsyncMock(return_value=(_payload('01.mp3'), None))):
        with pytest.raises(ReleaseRejected) as caught:
            _run(add_torrent_smart(adapter, 'http://indexer/x.torrent',
                                   verify_files=lambda names: (False, 'file list says mp3')))

    assert 'mp3' in caught.value.reason
    assert adapter.added_file is None
    assert adapter.added_url is None


def test_an_unparseable_payload_does_not_block_the_download():
    """A decoder failure is not evidence. It must degrade to the title check
    the caller already ran, not to a blocked queue."""
    adapter = _Adapter()
    called = []
    with patch('core.torrent_clients.base._fetch_torrent_payload_async',
               new=AsyncMock(return_value=(b'not bencode', None))):
        tid = _run(add_torrent_smart(adapter, 'http://indexer/x.torrent',
                                     verify_files=lambda names: called.append(names) or (False, 'no')))

    assert tid == 'tid-file'
    assert called == []


def test_a_magnet_skips_verification_rather_than_failing_it():
    """A magnet carries no file list. No evidence means proceed, not reject."""
    adapter = _Adapter()
    with patch('core.torrent_clients.base._fetch_torrent_payload_async',
               new=AsyncMock(return_value=(None, 'magnet:?xt=urn:btih:abc'))):
        tid = _run(add_torrent_smart(adapter, 'http://indexer/x.torrent',
                                     verify_files=lambda names: (False, 'never called')))

    assert tid == 'tid-url'


def test_no_verifier_means_the_old_behaviour_exactly():
    adapter = _Adapter()
    with patch('core.torrent_clients.base._fetch_torrent_payload_async',
               new=AsyncMock(return_value=(_payload('01.mp3'), None))):
        tid = _run(add_torrent_smart(adapter, 'http://indexer/x.torrent'))

    assert tid == 'tid-file'
