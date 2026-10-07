"""Regression: a metadata-enhancement failure must NOT wipe a clean/matched
import's tags (#804 — already-tagged files were blanked into Unknown Artist).

#1555: but the preserved tags must not keep the source file's FOREIGN
MusicBrainz ALBUM-identity ids either — one divergent release id splits the
album into duplicate entries on the media server while its siblings carry
SoulSync's pinned release id. The enhancement-failure preserve branch now
strips only the identity ids (strip_musicbrainz_identity_tags); everything
else stays exactly as #804 requires.
"""

from __future__ import annotations

import struct
from pathlib import Path

from core.imports.tag_policy import should_wipe_tags_on_enhancement_failure
from core.metadata.common import strip_musicbrainz_identity_tags


def test_clean_matched_import_is_never_wiped_on_failure():
    # The #804 case: matched import (clean metadata) → preserve existing tags.
    assert should_wipe_tags_on_enhancement_failure(has_clean_metadata=True) is False


def test_unmatched_download_still_strips_junk_on_failure():
    # Unchanged behavior for unmatched downloads (likely junk source tags).
    assert should_wipe_tags_on_enhancement_failure(has_clean_metadata=False) is True


def test_falsey_values_treated_as_unmatched():
    assert should_wipe_tags_on_enhancement_failure(None) is True
    assert should_wipe_tags_on_enhancement_failure(0) is True


# ---------------------------------------------------------------------------
# #1555: strip_musicbrainz_identity_tags — real FLAC files, no fixtures
# ---------------------------------------------------------------------------

_IDENTITY_TAGS = {
    "MUSICBRAINZ_ALBUMID": "foreign-release-mbid",
    "MUSICBRAINZ_RELEASEGROUPID": "foreign-release-group-mbid",
    "MUSICBRAINZ_ALBUMARTISTID": "foreign-album-artist-mbid",
}


def _make_minimal_flac(path: Path) -> None:
    """Create a real FLAC file mutagen can read/write tags on."""
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack(">H", 4096)  # min block
    streaminfo[2:4] = struct.pack(">H", 4096)  # max block
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])  # last block, STREAMINFO, len 34
    path.write_bytes(b"fLaC" + block_header + bytes(streaminfo))


def _make_1555_flac(path: Path):
    """FLAC carrying a foreign uploader's identity ids plus ordinary tags."""
    from mutagen.flac import FLAC

    _make_minimal_flac(path)
    audio = FLAC(str(path))
    for key, value in _IDENTITY_TAGS.items():
        audio[key] = [value]
    audio["MUSICBRAINZ_TRACKID"] = ["per-track-id-must-survive"]
    audio["TITLE"] = ["Go Green"]
    audio["ARTIST"] = ["Childish Major"]
    audio["ALBUM"] = ["1st Lady <3"]
    audio["CUSTOMTAG"] = ["keep me"]
    audio.save()
    return audio


def test_strip_removes_only_identity_tags_flac(tmp_path: Path) -> None:
    """The #1555 vector: a file with a foreign uploader's release id loses
    exactly the three album-identity ids; title/artist/album, the per-track
    MBID, and every other tag survive (#804 protection intact)."""
    from mutagen.flac import FLAC

    f = tmp_path / "track.flac"
    _make_1555_flac(f)

    assert strip_musicbrainz_identity_tags(str(f)) is True

    audio = FLAC(str(f))
    for key in _IDENTITY_TAGS:
        assert audio.get(key) is None, f"{key} should have been stripped"
    # #804: everything else preserved
    assert audio.get("TITLE") == ["Go Green"]
    assert audio.get("ARTIST") == ["Childish Major"]
    assert audio.get("ALBUM") == ["1st Lady <3"]
    assert audio.get("CUSTOMTAG") == ["keep me"]
    assert audio.get("MUSICBRAINZ_TRACKID") == ["per-track-id-must-survive"]


def test_strip_is_idempotent_and_noop_without_identity_tags(tmp_path: Path) -> None:
    """A second run (or a file that never had the ids) changes nothing and
    still returns True — the preserve branch must never corrupt a file."""
    from mutagen.flac import FLAC

    f = tmp_path / "clean.flac"
    _make_minimal_flac(f)
    audio = FLAC(str(f))
    audio["TITLE"] = ["Already Tagged"]
    audio.save()

    assert strip_musicbrainz_identity_tags(str(f)) is True
    assert strip_musicbrainz_identity_tags(str(f)) is True

    audio = FLAC(str(f))
    assert audio.get("TITLE") == ["Already Tagged"]


def test_strip_variant_spellings_flac(tmp_path: Path) -> None:
    """Foreign taggers don't always use the canonical Vorbis key: the
    no-underscore MUSICBRAINZ_RELEASEID variant is stripped too."""
    from mutagen.flac import FLAC

    f = tmp_path / "variant.flac"
    _make_minimal_flac(f)
    audio = FLAC(str(f))
    audio["MUSICBRAINZ_RELEASEID"] = ["foreign-variant-mbid"]
    audio["TITLE"] = ["Track"]
    audio.save()

    assert strip_musicbrainz_identity_tags(str(f)) is True
    audio = FLAC(str(f))
    assert audio.get("MUSICBRAINZ_RELEASEID") is None
    assert audio.get("TITLE") == ["Track"]


def _make_minimal_mp3(path: Path) -> None:
    """Real MP3 mutagen can parse: valid MPEG1 Layer III 128kbps/44.1kHz
    frames (417 bytes each — mutagen verifies the second frame syncs)."""
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    assert len(frame) == 417
    path.write_bytes(frame * 8)


def test_strip_removes_only_identity_tags_mp3(tmp_path: Path) -> None:
    """Round-trip the ID3 branch on a real MP3, including a differently-cased
    TXXX description (foreign taggers don't use Picard's exact casing)."""
    from mutagen.id3 import ID3, TPE1, TIT2, TXXX

    f = tmp_path / "track.mp3"
    _make_minimal_mp3(f)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["Go Green"]))
    tags.add(TPE1(encoding=3, text=["Childish Major"]))
    tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=["foreign-mbid"]))
    tags.add(TXXX(encoding=3, desc="musicbrainz release group id", text=["foreign-rg"]))
    tags.add(TXXX(encoding=3, desc="MusicBrainz Album Artist Id", text=["foreign-aa"]))
    tags.add(TXXX(encoding=3, desc="My Custom Tag", text=["keep me"]))
    tags.save(str(f))

    assert strip_musicbrainz_identity_tags(str(f)) is True

    tags = ID3(str(f))
    assert tags.getall("TXXX:MusicBrainz Album Id") == []
    assert tags.getall("TXXX:musicbrainz release group id") == []
    assert tags.getall("TXXX:MusicBrainz Album Artist Id") == []
    assert [t.text[0] for t in tags.getall("TIT2")] == ["Go Green"]
    assert [t.text[0] for t in tags.getall("TPE1")] == ["Childish Major"]
    assert [t.text[0] for t in tags.getall("TXXX:My Custom Tag")] == ["keep me"]


def test_strip_apev2_identity_tags_case_insensitive() -> None:
    """WavPack/Musepack/Monkey's Audio use APEv2: keys are stripped
    case-insensitively (mutagen's `del` by a differently-cased key raises
    KeyError, so this pins the iterate-and-match behavior)."""
    from mutagen.apev2 import APEv2

    from core.metadata.common import _strip_apev2_identity_tags

    tags = APEv2()
    tags["MusicBrainz Album Id"] = "foreign-mbid"
    tags["MUSICBRAINZ_RELEASEGROUPID"] = "foreign-rg"
    tags["Title"] = "Go Green"

    assert _strip_apev2_identity_tags(tags) == 2
    assert "MusicBrainz Album Id" not in tags
    assert "MUSICBRAINZ_RELEASEGROUPID" not in tags
    assert tags["Title"] == "Go Green"


def test_strip_returns_false_nonfatal_on_missing_file(tmp_path: Path) -> None:
    """Callers in the failure branch must never blow up on an unreadable
    file — the whole point of the branch is that something already failed."""
    assert strip_musicbrainz_identity_tags(str(tmp_path / "nope.flac")) is False


def test_strip_save_abort_leaves_file_untouched(tmp_path: Path, monkeypatch) -> None:
    """If the atomic save aborts on an audio-integrity check, the function
    reports False (not a lying True) and the file is byte-identical."""
    from mutagen.flac import FLAC

    import core.metadata.common as common

    f = tmp_path / "track.flac"
    _make_1555_flac(f)
    before = f.read_bytes()

    monkeypatch.setattr(common, "save_audio_file", lambda audio, symbols: False)

    assert strip_musicbrainz_identity_tags(str(f)) is False
    assert f.read_bytes() == before
    # The in-memory tag deletion happened, but nothing was persisted.
    assert FLAC(str(f)).get("MUSICBRAINZ_ALBUMID") == ["foreign-release-mbid"]


def test_strip_mp3_foreign_tagger_description_variants(tmp_path: Path) -> None:
    """Mp3tag-style ID3 spellings (no spaces/underscores, trailing space)
    are stripped too — MP3 is the dominant Soulseek format, so this is the
    population the fix targets."""
    from mutagen.id3 import ID3, TXXX

    f = tmp_path / "foreign.mp3"
    _make_minimal_mp3(f)
    tags = ID3()
    tags.add(TXXX(encoding=3, desc="MUSICBRAINZ_RELEASEID", text=["foreign-mbid"]))
    tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id ", text=["trailing-space"]))
    tags.save(str(f))

    assert strip_musicbrainz_identity_tags(str(f)) is True

    tags = ID3(str(f))
    assert tags.getall("TXXX:MUSICBRAINZ_RELEASEID") == []
    assert tags.getall("TXXX:MusicBrainz Album Id ") == []


def test_strip_tag_keys_match_picard_standards() -> None:
    """The tag map keys strip_musicbrainz_identity_tags deletes must be the
    exact Picard-standard keys, or the foreign ids survive silently."""
    from core.metadata.source import ID3_TAG_MAP, MP4_TAG_MAP, VORBIS_TAG_MAP

    assert VORBIS_TAG_MAP["MUSICBRAINZ_RELEASE_ID"] == "MUSICBRAINZ_ALBUMID"
    assert VORBIS_TAG_MAP["MUSICBRAINZ_RELEASEGROUPID"] == "MUSICBRAINZ_RELEASEGROUPID"
    assert VORBIS_TAG_MAP["MUSICBRAINZ_ALBUMARTISTID"] == "MUSICBRAINZ_ALBUMARTISTID"
    assert ID3_TAG_MAP["MUSICBRAINZ_RELEASE_ID"] == ("TXXX", "MusicBrainz Album Id")
    assert ID3_TAG_MAP["MUSICBRAINZ_RELEASEGROUPID"] == ("TXXX", "MusicBrainz Release Group Id")
    assert ID3_TAG_MAP["MUSICBRAINZ_ALBUMARTISTID"] == ("TXXX", "MusicBrainz Album Artist Id")
    assert MP4_TAG_MAP["MUSICBRAINZ_RELEASE_ID"] == "MusicBrainz Album Id"
    assert MP4_TAG_MAP["MUSICBRAINZ_RELEASEGROUPID"] == "MusicBrainz Release Group Id"
    assert MP4_TAG_MAP["MUSICBRAINZ_ALBUMARTISTID"] == "MusicBrainz Album Artist Id"
