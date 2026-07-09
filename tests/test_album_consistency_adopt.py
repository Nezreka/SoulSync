"""Album consistency must ADOPT the existing album, not impose on it (#1000).

User (abclive): completing an album re-tagged files and a filesystem/mutagen
quirk damaged them. Part of the fix: a completing / late-arriving track should
JOIN the album already on disk by copying the siblings' album-level tags onto
the NEW file — never reaching back to rewrite the existing tracks (and never
imposing a freshly-picked MusicBrainz release that could differ from the
siblings and split the album).

These pin: adopt when siblings exist (new file joins them, existing files
untouched), and fall back to the MusicBrainz path when there are none.
"""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import core.album_consistency as ac
from mutagen.flac import FLAC


def _make_flac(path: Path, tags: dict | None = None) -> None:
    """Create a real, minimal FLAC (so we exercise the true mutagen path)."""
    fLaC = b'fLaC'
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])  # last block, STREAMINFO, len 34
    path.write_bytes(fLaC + block_header + bytes(streaminfo))
    audio = FLAC(str(path))
    if tags:
        for k, v in tags.items():
            audio[k] = [v]
    audio.save()


def _album_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Some Artist" / "Some Album"
    d.mkdir(parents=True)
    return d


# --- adopt path -------------------------------------------------------------

def test_new_file_adopts_sibling_album_tags(tmp_path):
    d = _album_dir(tmp_path)
    # Existing track already in the library with the album's identity tags.
    _make_flac(d / "01 - Old.flac", {
        'MUSICBRAINZ_RELEASE_ID': 'EXISTING-REL',
        'MUSICBRAINZ_RELEASEGROUPID': 'EXISTING-RG',
        'ALBUM': 'Some Album',
        'ALBUMARTIST': 'Some Artist',
    })
    # New completing track with NO album identity yet.
    new = d / "02 - New.flac"
    _make_flac(new)

    file_infos = [{'path': str(new), 'track_number': 2, 'disc_number': 1, 'title': 'New'}]
    # mb_service present but MUST NOT be consulted on the adopt path.
    def _boom(*a, **k):
        raise AssertionError("MusicBrainz must not be consulted when adopting")
    res = ac.run_album_consistency(file_infos, "Some Album", "Some Artist",
                                   mb_service=SimpleNamespace(mb_client=SimpleNamespace(get_release=_boom)))

    assert res['success'] and res.get('adopted') is True
    assert res['release_mbid'] == 'EXISTING-REL'
    written = FLAC(str(new))
    assert written['MUSICBRAINZ_RELEASE_ID'] == ['EXISTING-REL']
    assert written['MUSICBRAINZ_RELEASEGROUPID'] == ['EXISTING-RG']
    assert written['ALBUM'] == ['Some Album']
    assert written['ALBUMARTIST'] == ['Some Artist']


def test_existing_sibling_is_never_written(tmp_path, monkeypatch):
    d = _album_dir(tmp_path)
    old = d / "01 - Old.flac"
    _make_flac(old, {'MUSICBRAINZ_RELEASE_ID': 'EXISTING-REL', 'ALBUM': 'Some Album'})
    new = d / "02 - New.flac"
    _make_flac(new)

    before = old.read_bytes()
    file_infos = [{'path': str(new), 'track_number': 2, 'disc_number': 1, 'title': 'New'}]
    ac.run_album_consistency(file_infos, "Some Album", "Some Artist", mb_service=SimpleNamespace())

    assert old.read_bytes() == before  # sibling byte-for-byte identical


def test_majority_wins_across_siblings(tmp_path):
    d = _album_dir(tmp_path)
    _make_flac(d / "01.flac", {'MUSICBRAINZ_RELEASE_ID': 'REL-MAJ', 'ALBUM': 'A'})
    _make_flac(d / "02.flac", {'MUSICBRAINZ_RELEASE_ID': 'REL-MAJ', 'ALBUM': 'A'})
    _make_flac(d / "03.flac", {'MUSICBRAINZ_RELEASE_ID': 'REL-MIN', 'ALBUM': 'A'})
    new = d / "04.flac"
    _make_flac(new)

    file_infos = [{'path': str(new), 'track_number': 4, 'disc_number': 1, 'title': 'x'}]
    res = ac.run_album_consistency(file_infos, "A", "Some Artist", mb_service=SimpleNamespace())
    assert res.get('adopted') is True
    assert FLAC(str(new))['MUSICBRAINZ_RELEASE_ID'] == ['REL-MAJ']


# --- fallback path ----------------------------------------------------------

def test_no_siblings_falls_back_to_mb(tmp_path, monkeypatch):
    # Brand-new album: every file is in file_infos, nothing to adopt -> MB path.
    d = _album_dir(tmp_path)
    t1, t2 = d / "01.flac", d / "02.flac"
    _make_flac(t1)
    _make_flac(t2)
    file_infos = [
        {'path': str(t1), 'track_number': 1, 'disc_number': 1, 'title': 'One'},
        {'path': str(t2), 'track_number': 2, 'disc_number': 1, 'title': 'Two'},
    ]

    monkeypatch.setattr(ac, "_resolve_album_release",
                        lambda *a, **k: {"id": "MB-REL", "title": "A", "release-group": {}})
    monkeypatch.setattr(ac, "_match_files_to_tracklist",
                        lambda fis, rel: {fi['path']: {"id": "trk-%s" % i} for i, fi in enumerate(fis)})

    res = ac.run_album_consistency(file_infos, "A", "Some Artist",
                                   mb_service=SimpleNamespace())
    assert res.get('adopted') is not True
    assert res['release_mbid'] == 'MB-REL'
    assert FLAC(str(t1))['MUSICBRAINZ_RELEASE_ID'] == ['MB-REL']


def test_untagged_siblings_do_not_trigger_adopt(tmp_path, monkeypatch):
    # Siblings exist but carry no album identity -> nothing to join -> MB path.
    d = _album_dir(tmp_path)
    _make_flac(d / "01 - Old.flac")  # no tags at all
    new = d / "02 - New.flac"
    _make_flac(new)
    file_infos = [{'path': str(new), 'track_number': 2, 'disc_number': 1, 'title': 'x'}]

    monkeypatch.setattr(ac, "_resolve_album_release",
                        lambda *a, **k: {"id": "MB-REL", "title": "A", "release-group": {}})
    monkeypatch.setattr(ac, "_match_files_to_tracklist",
                        lambda fis, rel: {fi['path']: {"id": "t"} for fi in fis})

    res = ac.run_album_consistency(file_infos, "A", "Some Artist", mb_service=SimpleNamespace())
    assert res.get('adopted') is not True
    assert res['release_mbid'] == 'MB-REL'
