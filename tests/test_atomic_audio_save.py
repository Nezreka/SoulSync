"""Atomic tag saves: an interrupted/OOM-killed save must never destroy the
user's file (#819 — CubeComming's hi-res FLACs imported to an empty shell).

save_audio_file writes the modified tags into a temp COPY, verifies it's still
valid audio, then os.replace()s it in — the original is untouched until that
atomic swap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from core.metadata.common import save_audio_file


def _symbols(file_length):
    """Symbols whose File() reports the given decoded length (None → invalid)."""
    return SimpleNamespace(
        ID3=type("ID3", (), {}), FLAC=type("FLAC", (), {}),
        File=lambda p: (None if file_length is None
                        else SimpleNamespace(info=SimpleNamespace(length=file_length))),
    )


def test_atomic_replace_on_success(tmp_path):
    f = tmp_path / "song.flac"
    f.write_bytes(b"ORIGINAL-AUDIO")
    saved_to = []

    class Audio:
        filename = str(f)
        tags = None

        def save(self, target=None, **k):
            saved_to.append(target)
            # mimic mutagen writing modified tags into the temp copy
            with open(target, "ab") as h:
                h.write(b"+TAGS")

    save_audio_file(Audio(), _symbols(180.0))

    assert f.read_bytes() == b"ORIGINAL-AUDIO+TAGS"     # replaced with the tagged copy
    assert saved_to == [str(f) + ".sstmp"]              # wrote the temp, NOT in place
    assert not (tmp_path / "song.flac.sstmp").exists()  # temp cleaned up


def test_original_survives_save_failure(tmp_path):
    # The #819 scenario: the save blows up mid-write. The original must be intact.
    f = tmp_path / "song.flac"
    f.write_bytes(b"ORIGINAL")
    inplace = []

    class Audio:
        filename = str(f)
        tags = None

        def save(self, target=None, **k):
            if target is not None:
                raise OSError("simulated interrupted/OOM save")
            inplace.append(True)   # fallback in-place save (writes nothing here)

    save_audio_file(Audio(), _symbols(180.0))

    assert f.read_bytes() == b"ORIGINAL"               # never destroyed
    assert not (tmp_path / "song.flac.sstmp").exists()  # temp removed
    assert inplace == [True]                            # fell back to in-place


def test_corrupt_temp_aborts_without_inplace(tmp_path):
    # save-to-temp "succeeds" but produces a file with no audio (#1000). We must
    # NOT replace the original AND must NOT retry in place (the in-place write
    # would corrupt the real file the same way). Abort: original intact, False.
    f = tmp_path / "song.flac"
    f.write_bytes(b"ORIGINAL")
    inplace = []

    class Audio:
        filename = str(f)
        tags = None
        info = SimpleNamespace(length=180.0)   # original had real audio

        def save(self, target=None, **k):
            if target is None:
                inplace.append(True)

    ok = save_audio_file(Audio(), _symbols(0))   # temp reports length 0 → truncated

    assert ok is False                                  # signalled failure
    assert f.read_bytes() == b"ORIGINAL"                # original untouched
    assert not (tmp_path / "song.flac.sstmp").exists()  # temp removed
    assert inplace == []                                # NO in-place retry


def test_no_filename_plain_save():
    saves = []

    class Audio:
        filename = None
        tags = None

        def save(self, target=None, **k):
            saves.append(target)

    save_audio_file(Audio(), _symbols(180.0))
    assert saves == [None]   # nothing to be atomic about → plain in-place


# ── real mutagen round-trip (only if ffmpeg can make a FLAC) ──

def _make_flac(path):
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    r = subprocess.run(
        [ff, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(path)],
        capture_output=True)
    return r.returncode == 0 and os.path.getsize(path) > 0


def test_real_flac_atomic_save_preserves_audio(tmp_path):
    from mutagen.flac import FLAC
    f = tmp_path / "real.flac"
    if not _make_flac(f):
        pytest.skip("ffmpeg unavailable — cannot build a real FLAC")
    orig_len = FLAC(str(f)).info.length

    audio = FLAC(str(f))
    audio["title"] = "Atomic Test"
    ok = save_audio_file(audio, SimpleNamespace(ID3=type("ID3", (), {}), FLAC=FLAC,
                                                File=__import__("mutagen").File))

    assert ok is True
    reread = FLAC(str(f))
    assert reread.info.length == pytest.approx(orig_len, abs=0.05)  # audio intact
    assert reread["title"] == ["Atomic Test"]                      # tag written
    assert not (tmp_path / "real.flac.sstmp").exists()


# ── #1000: audio-frame integrity verification (no ffmpeg needed) ──

def _make_flac_with_frames(path, frames=None):
    """A minimal but real FLAC with synthetic audio-frame bytes, so we can
    exercise the frame byte-compare without ffmpeg. mutagen opens it and
    preserves the trailing frames across a tag save."""
    import struct
    from mutagen.flac import FLAC
    if frames is None:
        frames = bytes(range(256)) * 32
    si = bytearray(34)
    si[0:2] = struct.pack(">H", 4096)
    si[2:4] = struct.pack(">H", 4096)
    si[10] = 0x0A
    si[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])  # last block, STREAMINFO, len 34
    path.write_bytes(b"fLaC" + block_header + bytes(si) + frames)
    FLAC(str(path)).save()  # normalize; frames are retained as audio


def test_flac_audio_identical_detects_match_and_corruption(tmp_path):
    from mutagen.flac import FLAC
    import core.metadata.common as common
    a = tmp_path / "a.flac"
    b = tmp_path / "b.flac"
    _make_flac_with_frames(a)
    _make_flac_with_frames(b)
    # Different tags, same frames → provably identical (offsets differ, frames don't).
    fa = FLAC(str(a)); fa["title"] = ["A" * 200]; fa.save()
    fb = FLAC(str(b)); fb["artist"] = ["Someone"]; fb.save()
    assert common._flac_audio_identical(str(a), str(b)) is True
    # Flip a byte inside b's frames → detected as changed.
    off = common._flac_audio_offset(str(b))
    data = bytearray(b.read_bytes())
    data[off] ^= 0xFF
    b.write_bytes(bytes(data))
    assert common._flac_audio_identical(str(a), str(b)) is False


def test_flac_audio_offset_none_for_non_flac(tmp_path):
    import core.metadata.common as common
    p = tmp_path / "x.bin"
    p.write_bytes(b"NOTFLAC" + b"\x00" * 64)
    assert common._flac_audio_offset(str(p)) is None


def test_flac_frame_corruption_aborts(tmp_path, monkeypatch):
    # The write mangles the audio stream (simulating the filesystem/mutagen bug).
    # save_audio_file must detect it, abort, and leave the original intact.
    from mutagen.flac import FLAC
    import core.metadata.common as common
    f = tmp_path / "real.flac"
    _make_flac_with_frames(f)
    original = f.read_bytes()

    real_raw = common._raw_audio_save

    def corrupting_raw(audio_file, symbols, target=None):
        real_raw(audio_file, symbols, target=target)
        if target is not None:
            off = common._flac_audio_offset(target)
            data = bytearray(open(target, "rb").read())
            if off is not None and off < len(data):
                data[off] ^= 0xFF            # mangle a frame byte in the temp
                with open(target, "wb") as h:
                    h.write(bytes(data))

    monkeypatch.setattr(common, "_raw_audio_save", corrupting_raw)

    audio = FLAC(str(f))
    audio["title"] = "x"
    ok = save_audio_file(audio, SimpleNamespace(ID3=type("ID3", (), {}), FLAC=FLAC,
                                                File=__import__("mutagen").File))

    assert ok is False                         # aborted
    assert f.read_bytes() == original          # original byte-for-byte intact
    assert not (tmp_path / "real.flac.sstmp").exists()


def test_flac_clean_tag_write_replaces(tmp_path):
    # Sanity: a normal tag write on a FLAC with frames passes verification and
    # actually replaces the file (the happy path, no ffmpeg needed).
    from mutagen.flac import FLAC
    f = tmp_path / "real.flac"
    _make_flac_with_frames(f)
    audio = FLAC(str(f))
    audio["title"] = "Clean Write"
    ok = save_audio_file(audio, SimpleNamespace(ID3=type("ID3", (), {}), FLAC=FLAC,
                                                File=__import__("mutagen").File))
    assert ok is True
    assert FLAC(str(f))["title"] == ["Clean Write"]
    assert not (tmp_path / "real.flac.sstmp").exists()
