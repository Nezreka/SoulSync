"""#1422: a flac -> mp3 lossy copy is tagged like a native mp3 import, not with
ffmpeg's raw vorbis names. #1425: ARTISTS follows the primary source."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TXXX
from mutagen.mp4 import MP4, MP4Tags
from mutagen._vorbis import VCommentDict

from core.imports import file_ops
from core.metadata import source as ms
from core.metadata.common import get_mutagen_symbols
from core.metadata.lossy_tags import _vorbis_to_mp4, carry_tags_to_lossy_copy

COVER = b"\xff\xd8\xff\xe0" + b"cover" * 50


def _make_flac(path):
    """a minimal real flac (streaminfo + fake frames), tagged the way soulsync's
    import writer leaves it."""
    import struct
    si = bytearray(34)
    si[0:2] = struct.pack(">H", 4096)
    si[2:4] = struct.pack(">H", 4096)
    si[10] = 0x0A
    si[12] = 0x70
    path.write_bytes(b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(si) + bytes(range(256)) * 8)
    audio = FLAC(str(path))
    audio["title"] = ["Desgraça"]
    audio["artist"] = ["CPM 22"]
    audio["artists"] = ["CPM 22", "Guest"]
    audio["albumartist"] = ["CPM 22"]
    audio["album"] = ["Depois de um Longo Inverno"]
    audio["date"] = ["2006"]
    audio["genre"] = ["Rock", "Punk"]
    audio["tracknumber"] = ["1"]
    audio["tracktotal"] = ["13"]
    audio["totaltracks"] = ["13"]
    audio["discnumber"] = ["1"]
    audio["MUSICBRAINZ_ALBUMID"] = ["album-mbid"]
    audio["MUSICBRAINZ_TRACKID"] = ["recording-mbid"]
    audio["RELEASETYPE"] = ["album"]
    audio["ORIGINALDATE"] = ["2006-05-01"]
    audio["LABEL"] = ["Arsenal"]
    audio["lyrics"] = ["la la la"]
    audio["quality"] = ["FLAC 16bit/44.1kHz"]
    audio["SPOTIFY_TRACK_ID"] = ["sp1"]
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", COVER
    audio.add_picture(pic)
    audio.save()


def _make_ffmpeg_mp3(path):
    """silent mpeg frames carrying what ffmpeg -map_metadata 0 wrote (#1422)."""
    Path(path).write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 20)
    tags = ID3()
    for desc, text in (("MUSICBRAINZ_ALBUMID", "album-mbid"), ("USLT", "la la la"),
                       ("quality", "FLAC 16bit/44.1kHz"), ("tracktotal", "13")):
        tags.add(TXXX(encoding=3, desc=desc, text=[text]))
    tags.save(str(path), v2_version=3)


def _assert_native_mp3(path):
    tags = ID3(str(path))
    assert tags.version[:2] == (2, 4)
    assert tags["TIT2"].text == ["Desgraça"]
    assert tags["TPE1"].text == ["CPM 22"]
    assert tags["TPE2"].text == ["CPM 22"]
    assert tags["TCON"].text == ["Rock", "Punk"]
    assert tags["TRCK"].text == ["1/13"]
    assert tags["TPOS"].text == ["1"]
    assert tags["TXXX:MusicBrainz Album Id"].text == ["album-mbid"]
    assert tags["UFID:http://musicbrainz.org"].data == b"recording-mbid"
    assert tags["TXXX:MusicBrainz Album Type"].text == ["album"]
    assert str(tags["TDOR"].text[0]) == "2006-05-01"
    assert tags["TPUB"].text == ["Arsenal"]
    assert tags["TXXX:Artists"].text == ["CPM 22", "Guest"]
    assert tags["TXXX:SPOTIFY_TRACK_ID"].text == ["sp1"]
    assert tags.getall("USLT")[0].text == "la la la"
    assert tags["APIC:Cover"].data == COVER
    assert [f.text for f in tags.getall("TXXX") if f.desc.upper() == "QUALITY"] == [["MP3-320"]]
    descs = {f.desc for f in tags.getall("TXXX")}
    assert not descs & {"MUSICBRAINZ_ALBUMID", "MUSICBRAINZ_TRACKID", "USLT", "tracktotal",
                        "TRACKTOTAL", "TOTALTRACKS", "LYRICS"}


def test_flac_to_mp3_copy_gets_native_frames(tmp_path):
    src, dest = tmp_path / "01.flac", tmp_path / "01.mp3"
    _make_flac(src)
    _make_ffmpeg_mp3(dest)
    assert carry_tags_to_lossy_copy(str(src), str(dest), "MP3-320") is True
    _assert_native_mp3(dest)


def test_create_lossy_copy_retags_the_mp3(tmp_path, monkeypatch):
    """the import path's converter runs the retag on the real output file."""
    src = tmp_path / "01 - Desgraça.flac"
    _make_flac(src)
    cfg = {"lossy_copy.enabled": True, "lossy_copy.codec": "mp3",
           "lossy_copy.bitrate": "320", "lossy_copy.delete_original": True}
    fake_cfg = MagicMock()
    fake_cfg.get.side_effect = lambda key, default=None: cfg.get(key, default)
    monkeypatch.setattr(file_ops, "config_manager", fake_cfg)
    monkeypatch.setattr(file_ops.shutil, "which", lambda _: "/fake/ffmpeg")

    def fake_run(cmd, **_kw):
        _make_ffmpeg_mp3(cmd[-1])
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(file_ops.subprocess, "run", fake_run)
    out = file_ops.create_lossy_copy(str(src))
    assert out and out.endswith(".mp3")
    assert not src.exists()
    _assert_native_mp3(out)


def test_flac_tags_translate_to_mp4_atoms(tmp_path):
    src = tmp_path / "01.flac"
    _make_flac(src)
    fields = {}
    for k, v in FLAC(str(src)).tags:
        fields.setdefault(k.upper(), []).append(v)
    dest = MP4.__new__(MP4)
    dest.tags = MP4Tags()
    _vorbis_to_mp4(fields, dest, get_mutagen_symbols())
    assert dest["\xa9nam"] == ["Desgraça"]
    assert dest["trkn"] == [(1, 13)]
    assert dest["disk"] == [(1, 0)]
    assert dest["\xa9lyr"] == ["la la la"]
    assert bytes(dest["----:com.apple.iTunes:MusicBrainz Album Id"][0]) == b"album-mbid"
    assert "----:com.apple.iTunes:QUALITY" not in dest
    assert "----:com.apple.iTunes:MUSICBRAINZ_ALBUMID" not in dest


# ── #1425 ──

class _Cfg:
    def get(self, key, default=None):
        return default


def _write_artists(metadata):
    audio = FLAC.__new__(FLAC)
    audio.tags = VCommentDict()
    state = ms._blank_post_process_state()
    state["id_tags"]["ARTISTS"] = ["Mammoth WVH"]   # musicbrainz credit name
    ms._write_embedded_metadata(audio, metadata, state, _Cfg(), get_mutagen_symbols())
    return audio.tags["ARTISTS"]


def test_artists_follows_the_primary_source():
    assert _write_artists({"artist": "Mammoth", "_artists_list": ["Mammoth"]}) == ["Mammoth"]


def test_artists_keeps_musicbrainz_when_primary_has_no_list():
    assert _write_artists({"artist": "Mammoth"}) == ["Mammoth WVH"]
