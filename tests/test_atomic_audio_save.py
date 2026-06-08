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


def test_corrupt_temp_rejected(tmp_path):
    # save-to-temp "succeeds" but produces a file with no audio → must NOT
    # replace the original; fall back instead.
    f = tmp_path / "song.flac"
    f.write_bytes(b"ORIGINAL")
    inplace = []

    class Audio:
        filename = str(f)
        tags = None

        def save(self, target=None, **k):
            if target is None:
                inplace.append(True)

    save_audio_file(Audio(), _symbols(0))   # File().info.length == 0 → invalid

    assert f.read_bytes() == b"ORIGINAL"
    assert not (tmp_path / "song.flac.sstmp").exists()
    assert inplace == [True]


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
    save_audio_file(audio, SimpleNamespace(ID3=type("ID3", (), {}), FLAC=FLAC,
                                           File=__import__("mutagen").File))

    reread = FLAC(str(f))
    assert reread.info.length == pytest.approx(orig_len, abs=0.05)  # audio intact
    assert reread["title"] == ["Atomic Test"]                      # tag written
    assert not (tmp_path / "real.flac.sstmp").exists()
