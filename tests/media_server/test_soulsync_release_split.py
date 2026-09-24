"""#1299: the standalone scan keeps same-named releases as separate albums.

the scan groups files by album-artist + album tag. two musicbrainz releases can
share both, so a release whose files carry its disambiguation (the album comment
tag) gets its own album, keyed by release id. everything else keeps the plain
key, and a release alone in the library never splits, so no existing album
changes id.
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path

import pytest
from mutagen.flac import FLAC

from core.soulsync_client import SoulSyncClient, _split_by_release, _stable_id

ARTIST = "кис-кис"
ALBUM = "юность в стиле панк"
ORIGINAL = "55c7242f-1b4b-485d-b5f2-d6a8feeee088"
BABY_PUNK = "3b98979b-6494-4a7c-8de6-2165902f8a87"


def _flac(path: Path, **tags) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo))
    audio = FLAC(str(path))
    for k, v in tags.items():
        audio[k] = [v]
    audio.save()


def _track(folder: Path, number: int, title: str, release=None, comment=None):
    tags = {"ALBUMARTIST": ARTIST, "ARTIST": ARTIST, "ALBUM": ALBUM,
            "TITLE": title, "TRACKNUMBER": str(number)}
    if release:
        tags["MUSICBRAINZ_ALBUMID"] = release
    if comment:
        tags["MUSICBRAINZ_ALBUMCOMMENT"] = comment
    _flac(folder / f"{number:02d} - {title}.flac", **tags)


@pytest.fixture
def client(tmp_path):
    c = SoulSyncClient.__new__(SoulSyncClient)
    c._transfer_path = str(tmp_path)
    c._progress_callback = None
    c._cache = None
    c._cache_time = 0
    c._cache_ttl = 300
    c._last_scan_time = None
    return c


def _albums(client, **kw):
    artists = client._scan_transfer(**kw)
    return {album.ratingKey: sorted(t.title for t in album.tracks())
            for artist in artists for album in artist.albums()}


def _plain_key():
    return _stable_id(f"{ARTIST}::{ALBUM}")


def test_the_two_releases_scan_as_two_albums(client, tmp_path):
    base = tmp_path / ARTIST
    _track(base / ALBUM, 1, "рэпер", ORIGINAL)
    _track(base / ALBUM, 3, "трахаюсь", ORIGINAL)
    _track(base / f"{ALBUM} (baby punk version)", 1, "рэпер", BABY_PUNK, "baby punk version")
    _track(base / f"{ALBUM} (baby punk version)", 3, "teen love", BABY_PUNK, "baby punk version")

    assert _albums(client) == {
        _plain_key(): ["рэпер", "трахаюсь"],
        _stable_id(f"{ARTIST}::{ALBUM}::{BABY_PUNK}"): ["teen love", "рэпер"],
    }


def test_a_lone_edition_keeps_its_plain_key(client, tmp_path):
    # picard writes the album comment too; one release on its own must not get
    # a new album id just for carrying it
    folder = tmp_path / ARTIST / f"{ALBUM} (baby punk version)"
    _track(folder, 1, "рэпер", BABY_PUNK, "baby punk version")
    _track(folder, 3, "teen love", BABY_PUNK, "baby punk version")
    assert _albums(client) == {_plain_key(): ["teen love", "рэпер"]}


def test_incremental_scan_of_only_the_new_release_waits_for_the_full_scan(client, tmp_path):
    # the known limit: seeing one release alone, the scan can't know to split
    base = tmp_path / ARTIST
    _track(base / ALBUM, 1, "рэпер", ORIGINAL)
    old = base / ALBUM / "01 - рэпер.flac"
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    _track(base / f"{ALBUM} (baby punk version)", 1, "рэпер", BABY_PUNK, "baby punk version")

    assert list(_albums(client, since_mtime=time.time() - 600)) == [_plain_key()]
    assert set(_albums(client)) == {_plain_key(), _stable_id(f"{ARTIST}::{ALBUM}::{BABY_PUNK}")}


def test_an_album_without_comments_keeps_its_key(client, tmp_path):
    # tagged with a release id but no disambiguation: exactly as before
    _track(tmp_path / ARTIST / ALBUM, 1, "рэпер", ORIGINAL)
    _track(tmp_path / ARTIST / ALBUM, 2, "тиндер", ORIGINAL)
    assert _albums(client) == {_plain_key(): ["рэпер", "тиндер"]}


def test_untagged_new_track_of_a_commented_release_stays_with_it(client, tmp_path):
    # one file tagged before the comment existed, same release id: joins its release
    _track(tmp_path / ARTIST / ALBUM, 1, "рэпер", ORIGINAL)
    folder = tmp_path / ARTIST / f"{ALBUM} (baby punk version)"
    _track(folder, 1, "рэпер", BABY_PUNK, "baby punk version")
    _track(folder, 10, "кирилл", BABY_PUNK)
    assert _albums(client) == {
        _plain_key(): ["рэпер"],
        _stable_id(f"{ARTIST}::{ALBUM}::{BABY_PUNK}"): ["кирилл", "рэпер"]}


def test_split_is_a_no_op_without_any_comment():
    entries = [("/a/1.flac", {"musicbrainz_albumid": ORIGINAL}),
               ("/b/1.flac", {"musicbrainz_albumid": BABY_PUNK}),
               ("/c/1.flac", {})]
    assert _split_by_release(entries) == [("", entries)]


def test_split_ignores_release_id_case():
    o = ("/o/1.flac", {"musicbrainz_albumid": ORIGINAL})
    a = ("/a/1.flac", {"musicbrainz_albumid": BABY_PUNK.upper(),
                       "musicbrainz_albumcomment": "baby punk version"})
    b = ("/a/2.flac", {"musicbrainz_albumid": BABY_PUNK})
    assert _split_by_release([o, a, b]) == [("", [o]), (f"::{BABY_PUNK}", [a, b])]
