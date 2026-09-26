"""#1299: a file says which musicbrainz release it is, and soulsync writes that.

MUSICBRAINZ_ALBUMID is the release, MUSICBRAINZ_ALBUMCOMMENT its disambiguation
("baby punk version"). these round-trip the real mutagen writers and readers:
the tag writer, read_release_identity, and the standalone scanner's easy-mode
read, which all have to agree on the frame names picard uses.
"""

from __future__ import annotations

import struct
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TXXX
from mutagen.mp4 import MP4FreeForm, MP4Tags

from core.library.release_identity import identity_from_tags, read_release_identity

BABY_PUNK = "3b98979b-6494-4a7c-8de6-2165902f8a87"


def _make_flac(path: Path, tags: dict | None = None) -> str:
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo))
    audio = FLAC(str(path))
    for k, v in (tags or {}).items():
        audio[k] = [v]
    audio.save()
    return str(path)


def _make_mp3(path: Path, frames: dict | None = None) -> str:
    # ten silent-ish mpeg1 layer 3 frames, enough for mutagen to sync
    path.write_bytes((bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)) * 10)
    tags = ID3()
    for desc, value in (frames or {}).items():
        tags.add(TXXX(encoding=3, desc=desc, text=[value]))
    tags.save(str(path))
    return str(path)


# ── reading ──────────────────────────────────────────────────────────────────

def test_flac_identity(tmp_path):
    path = _make_flac(tmp_path / "a.flac", {
        "MUSICBRAINZ_ALBUMID": BABY_PUNK, "MUSICBRAINZ_ALBUMCOMMENT": "baby punk version"})
    assert read_release_identity(path) == (BABY_PUNK, "baby punk version")


def test_mp3_identity(tmp_path):
    path = _make_mp3(tmp_path / "a.mp3", {
        "MusicBrainz Album Id": BABY_PUNK, "MusicBrainz Album Comment": "baby punk version"})
    assert read_release_identity(path) == (BABY_PUNK, "baby punk version")


def test_mp4_identity_from_freeform_atoms():
    tags = MP4Tags()
    tags["----:com.apple.iTunes:MusicBrainz Album Id"] = [MP4FreeForm(BABY_PUNK.encode())]
    tags["----:com.apple.iTunes:MusicBrainz Album Comment"] = [MP4FreeForm(b"baby punk version")]
    assert identity_from_tags(tags) == (BABY_PUNK, "baby punk version")


def test_untagged_and_unreadable_files_are_unknown(tmp_path):
    assert read_release_identity(_make_flac(tmp_path / "plain.flac")) == ("", "")
    junk = tmp_path / "junk.flac"
    junk.write_bytes(b"not audio")
    assert read_release_identity(str(junk)) == ("", "")
    assert read_release_identity(str(tmp_path / "missing.flac")) == ("", "")


# ── writing ──────────────────────────────────────────────────────────────────

def test_release_tags_carry_the_disambiguation():
    from core.metadata.musicbrainz_tags import release_tags
    tags = release_tags({"id": BABY_PUNK, "disambiguation": " baby punk version "})
    assert tags["MUSICBRAINZ_ALBUMCOMMENT"] == "baby punk version"
    assert "MUSICBRAINZ_ALBUMCOMMENT" not in release_tags({"id": BABY_PUNK, "disambiguation": ""})
    assert "MUSICBRAINZ_ALBUMCOMMENT" not in release_tags({"id": BABY_PUNK})


def test_comment_tag_is_behind_its_own_setting():
    from core.metadata.source import SOURCE_TAG_CONFIG
    assert SOURCE_TAG_CONFIG["MUSICBRAINZ_ALBUMCOMMENT"] == "musicbrainz.tags.release_comment"


def _symbols():
    from core.metadata.common import get_mutagen_symbols
    return get_mutagen_symbols()


def test_written_tags_read_back_on_flac(tmp_path):
    from core.metadata.musicbrainz_tags import release_tags, write_tag
    path = _make_flac(tmp_path / "w.flac")
    audio = FLAC(path)
    for tag, value in release_tags({"id": BABY_PUNK, "disambiguation": "baby punk version"}).items():
        write_tag(audio, tag, value, _symbols())
    audio.save()
    assert read_release_identity(path) == (BABY_PUNK, "baby punk version")


def test_written_tags_read_back_on_mp3(tmp_path):
    from mutagen.mp3 import MP3
    from core.metadata.musicbrainz_tags import release_tags, write_tag
    path = _make_mp3(tmp_path / "w.mp3")
    audio = MP3(path)
    for tag, value in release_tags({"id": BABY_PUNK, "disambiguation": "baby punk version"}).items():
        write_tag(audio, tag, value, _symbols())
    audio.save()
    assert read_release_identity(path) == (BABY_PUNK, "baby punk version")


def test_written_tags_land_in_picards_mp4_atoms():
    from core.metadata.musicbrainz_tags import write_tag
    symbols = _symbols()

    class _MP4(symbols.MP4):
        # an mp4 as far as write_tag can tell, minus the file
        def __init__(self):
            self.tags = None
            self.written = {}

        def __setitem__(self, key, value):
            self.written[key] = value

    audio = _MP4()
    write_tag(audio, "MUSICBRAINZ_ALBUMCOMMENT", "baby punk version", symbols)
    assert audio.written == {"----:com.apple.iTunes:MusicBrainz Album Comment":
                             [MP4FreeForm(b"baby punk version")]}


# ── the standalone scanner reads the same tags ───────────────────────────────

def test_scanner_reads_release_id_and_comment(tmp_path):
    from core.soulsync_client import _read_tags
    flac = _make_flac(tmp_path / "s.flac", {
        "MUSICBRAINZ_ALBUMID": BABY_PUNK, "MUSICBRAINZ_ALBUMCOMMENT": "baby punk version"})
    mp3 = _make_mp3(tmp_path / "s.mp3", {
        "MusicBrainz Album Id": BABY_PUNK, "MusicBrainz Album Comment": "baby punk version"})
    for path in (flac, mp3):
        tags = _read_tags(path)
        assert tags["musicbrainz_albumid"] == BABY_PUNK
        assert tags["musicbrainz_albumcomment"] == "baby punk version"
