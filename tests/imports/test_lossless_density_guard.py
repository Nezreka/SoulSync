"""Preview clips padded to full length, from ANY source.

jadux reported a library full of files that play for ~30 seconds and then stop.
Not HiFi - he does not use it - and his Tidal subscription had lapsed, so Tidal
was handing back previews and SoulSync was filing them at 0.99 confidence.

His log settles the mechanism. Across 246 unique FLACs the correlation between
track LENGTH and file SIZE is -0.01: a 90-second track and a 450-second track
are both about 3MB. Every file holds the same ~30 seconds of real audio, padded
out to whatever the metadata claimed. 3MB of FLAC at a normal lossless bitrate
is almost exactly 30 seconds.

Why every existing check passed it:

* size leg      - 3MB is not "too small"
* parse leg     - a perfectly valid FLAC
* duration leg  - the container HONESTLY declares the full length; drift 0.0008s
* the decode leg - only fires when the header reports length 0, which was
                  HiFi's signature (total_samples=0). Tidal's header is
                  internally consistent, so it never ran.

That last point is the actual lesson: the guard was keyed to the fingerprint of
one source's lie instead of to the property we care about - does this file hold
the audio it claims. Size is the one thing a header cannot fake, so this check
needs no decoder at all.

Thresholds are measured, not guessed. Against a broken library and a healthy
one the clusters do not overlap: broken topped out at 28.7% of raw PCM, genuine
bottomed out at 37.2%. The 30% line sits in that gap, catching 234/246 of the
broken files with 0/30 false positives on the healthy set.
"""

from __future__ import annotations

import pytest

from core.imports.file_integrity import (
    LOSSLESS_MIN_DENSITY,
    is_fake_lossless_bitrate,
    is_lossless_audio,
    raw_pcm_bitrate,
)

CD = (44100, 16, 2)          # sample_rate, bits, channels
CD_RAW = 44100 * 16 * 2      # 1,411,200 bps


def _size_for(kbps, seconds):
    return int(kbps * 1000 * seconds / 8)


# ── the predicate ────────────────────────────────────────────────────────────
class TestDensity:
    def test_a_thirty_second_clip_padded_to_full_length_is_caught(self):
        """jadux's exact shape: ~3MB claiming a long runtime."""
        assert is_fake_lossless_bitrate(3_000_000, 300.0, *CD) is True

    def test_a_real_flac_passes(self):
        # a healthy CD-rip sits at 55-75% of raw
        assert is_fake_lossless_bitrate(_size_for(900, 240), 240.0, *CD) is False

    @pytest.mark.parametrize("kbps", [26, 60, 140, 163, 300, 405])
    def test_the_whole_broken_cluster_is_caught(self, kbps):
        """Real figures from the reported library, min/median/max."""
        assert is_fake_lossless_bitrate(_size_for(kbps, 200), 200.0, *CD) is True

    @pytest.mark.parametrize("kbps", [525, 549, 584, 621, 782, 816, 863])
    def test_the_whole_genuine_cluster_passes(self, kbps):
        """Real figures from a healthy library AND the genuine files in the
        broken one. The two clusters must not overlap."""
        assert is_fake_lossless_bitrate(_size_for(kbps, 200), 200.0, *CD) is False

    def test_the_threshold_sits_in_the_measured_gap(self):
        # broken topped out at 28.7%, genuine bottomed at 37.2%
        assert 0.287 < LOSSLESS_MIN_DENSITY < 0.372

    def test_unknowns_never_reject(self):
        """A missing dimension means we cannot judge, and quarantining on a
        shrug is how a guard becomes something people switch off."""
        assert is_fake_lossless_bitrate(0, 200.0, *CD) is False
        assert is_fake_lossless_bitrate(1000, 0, *CD) is False
        assert is_fake_lossless_bitrate(1000, 200.0, 0, 16, 2) is False
        assert is_fake_lossless_bitrate(1000, 200.0, 44100, 0, 2) is False
        assert is_fake_lossless_bitrate(1000, 200.0, 44100, 16, 0) is False
        assert is_fake_lossless_bitrate(None, None, None, None, None) is False
        assert is_fake_lossless_bitrate("x", "y", "z", "w", "v") is False

    def test_hi_res_scales_with_the_format(self):
        """24/96 raw is 3.3x CD, so the bar moves with it rather than being a
        fixed kbps number that would pass every hi-res preview."""
        hires = (96000, 24, 2)
        assert raw_pcm_bitrate(*hires) == 96000 * 24 * 2
        # 800kbps is healthy for CD but far too thin for 24/96
        assert is_fake_lossless_bitrate(_size_for(800, 200), 200.0, *hires) is True
        assert is_fake_lossless_bitrate(_size_for(2500, 200), 200.0, *hires) is False

    def test_mono_is_judged_against_mono_raw(self):
        mono = (44100, 16, 1)
        assert is_fake_lossless_bitrate(_size_for(450, 200), 200.0, *mono) is False
        assert is_fake_lossless_bitrate(_size_for(100, 200), 200.0, *mono) is True


# ── the lossless gate: the part that protects healthy lossy files ────────────
class _Info:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Audio:
    """Stands in for a mutagen file object; the class NAME is what the gate
    reads, matching how mutagen identifies a format."""
    def __init__(self, name, **info):
        self.__class__ = type(name, (_Audio,), {})
        self.info = _Info(**info)


class TestLosslessGate:
    """A 128kbps MP3 is SUPPOSED to be 9% of raw PCM. Running the density check
    on lossy audio would quarantine every healthy file in the library - all five
    lossy files in the reported log would have been wrongly flagged."""

    def test_flac_is_lossless(self):
        assert is_lossless_audio(_Audio("FLAC", sample_rate=44100, bits_per_sample=16,
                                        channels=2, length=200.0)) is True

    def test_mp3_is_not(self):
        assert is_lossless_audio(_Audio("MP3", sample_rate=44100, channels=2,
                                        length=200.0, bitrate=128000)) is False

    def test_alac_in_an_mp4_is_lossless(self):
        assert is_lossless_audio(_Audio("MP4", codec="alac", sample_rate=44100,
                                        bits_per_sample=16, channels=2)) is True

    def test_aac_in_an_mp4_is_NOT_lossless_even_though_it_claims_16_bits(self):
        """The trap. A lossy AAC .m4a reports bits_per_sample=16, identical to
        ALAC in the same container, so bits_per_sample cannot be the gate and
        neither can the extension. Only the codec separates them. Found by
        encoding both and reading what mutagen actually said."""
        aac = _Audio("MP4", codec="mp4a.40.2", sample_rate=44100,
                     bits_per_sample=16, channels=2)
        assert getattr(aac.info, "bits_per_sample") == 16      # identical to ALAC
        assert is_lossless_audio(aac) is False

    def test_an_mp4_with_no_codec_string_is_not_assumed_lossless(self):
        assert is_lossless_audio(_Audio("MP4", sample_rate=44100, channels=2)) is False

    def test_wav_and_aiff_are_lossless(self):
        for fmt in ("WAVE", "AIFF"):
            assert is_lossless_audio(_Audio(fmt, sample_rate=44100,
                                            bits_per_sample=16, channels=2)) is True

    def test_a_file_with_no_info_block_is_not_lossless(self):
        class Bare:
            info = None
        assert is_lossless_audio(Bare()) is False
        assert is_lossless_audio(object()) is False


# ── end to end, through the shared check every source passes ─────────────────
import os
import shutil
import subprocess

_FFMPEG = shutil.which("ffmpeg") or next(
    (p for p in ("/mnt/c/Program Files/Jellyfin/Server/ffmpeg.exe",
                 "/mnt/c/Program Files/Navidrome/ffmpeg.exe") if os.path.exists(p)), None)

#: The guard shells out to a bare `ffmpeg`, so a Windows-only binary that this
#: file can drive with translated paths is NOT enough for it. Separated on
#: purpose: a reject test that silently skips is a test that checked nothing.
_GUARD_FFMPEG = shutil.which("ffmpeg")
_needs_guard_ffmpeg = pytest.mark.skipif(
    not _GUARD_FFMPEG,
    reason="the guard confirms with ffmpeg on PATH; without it the design "
           "deliberately fails open, which the fail-open test covers instead")


def _win(path):
    """Return a path the selected ffmpeg can open.

    Native Windows Python already hands Windows paths to ffmpeg.exe. The wslpath
    conversion is only needed when this test is running from WSL but driving a
    Windows ffmpeg binary.
    """
    if os.name == "nt" or not (_FFMPEG or "").lower().endswith(".exe"):
        return str(path)
    out = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True)
    return out.stdout.strip() or str(path)


def _encode(dest, *args):
    subprocess.run([_FFMPEG, "-hide_banner", "-loglevel", "error", *args, _win(dest), "-y"],
                   check=True, timeout=120)
    return str(dest)


NOISE = "anoisesrc=d=%d:c=pink:r=44100:a=0.5"
SILENCE = "anullsrc=r=44100:cl=stereo:d=%d"


@pytest.mark.skipif(not _FFMPEG, reason="ffmpeg unavailable")
class TestEndToEnd:
    """Real encoded files through the real function. The bug was never in the
    arithmetic - it was in which files the arithmetic was allowed to see."""

    @pytest.fixture(scope="class")
    def files(self, tmp_path_factory):
        d = tmp_path_factory.mktemp("density")
        out = {}
        out["real"] = _encode(d / "real.flac", "-f", "lavfi", "-i", NOISE % 120,
                              "-ac", "2", "-sample_fmt", "s16")
        # 30s of audio then silence to 120s: the reported shape exactly
        out["fake"] = _encode(d / "fake.flac", "-f", "lavfi", "-i", NOISE % 30,
                              "-f", "lavfi", "-i", SILENCE % 90,
                              "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
                              "-ac", "2", "-sample_fmt", "s16", "-t", "120")
        out["mp3"] = _encode(d / "lossy.mp3", "-f", "lavfi", "-i", NOISE % 120,
                             "-ac", "2", "-b:a", "128k")
        out["aac"] = _encode(d / "lossy.m4a", "-f", "lavfi", "-i", NOISE % 120,
                             "-ac", "2", "-c:a", "aac", "-b:a", "160k")
        out["alac_fake"] = _encode(d / "fake_alac.m4a", "-f", "lavfi", "-i", NOISE % 30,
                                   "-f", "lavfi", "-i", SILENCE % 90,
                                   "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
                                   "-ac", "2", "-sample_fmt", "s16p", "-c:a", "alac",
                                   "-t", "120")
        return out

    @_needs_guard_ffmpeg
    def test_a_padded_preview_is_rejected(self, files):
        from core.imports.file_integrity import check_audio_integrity
        r = check_audio_integrity(files["fake"])
        assert r.ok is False
        assert "preview clip or truncated" in r.reason

    @_needs_guard_ffmpeg
    def test_it_needs_no_expected_duration(self, files):
        """The whole point. The old duration leg could only fire when the
        download had been matched to a metadata source; this protects an
        unmatched download too, and every source equally."""
        from core.imports.file_integrity import check_audio_integrity
        assert check_audio_integrity(files["fake"], None).ok is False

    @_needs_guard_ffmpeg
    def test_it_fires_even_when_the_header_is_perfectly_honest(self, files):
        """The reason the HiFi fix did not cover this. That guard decodes only
        when the header reports length 0; here the container correctly declares
        120s, so nothing before this check had anything to object to."""
        from mutagen import File
        from core.imports.file_integrity import check_audio_integrity
        assert File(files["fake"]).info.length == pytest.approx(120, abs=1)
        # the duration leg AGREES with the container - and still it is caught
        assert check_audio_integrity(files["fake"], 120_000).ok is False

    def test_a_real_flac_passes(self, files):
        from core.imports.file_integrity import check_audio_integrity
        r = check_audio_integrity(files["real"], 120_000)
        assert r.ok is True, r.reason
        assert r.checks["lossless_density"] > LOSSLESS_MIN_DENSITY

    @_needs_guard_ffmpeg
    def test_padded_alac_is_caught_too(self, files):
        from core.imports.file_integrity import check_audio_integrity
        assert check_audio_integrity(files["alac_fake"]).ok is False

    @pytest.mark.parametrize("key", ["mp3", "aac"])
    def test_lossy_files_are_untouched(self, files, key):
        """A 128kbps MP3 and a 160kbps AAC are both ~10% of raw PCM. If the
        gate ever breaks, every healthy lossy file in the library gets
        quarantined - all five in the reported log would have been."""
        from core.imports.file_integrity import check_audio_integrity
        r = check_audio_integrity(files[key], 120_000)
        assert r.ok is True, r.reason
        assert "lossless_density" not in r.checks


# ── the zero-length header (HiFi's shape) ────────────────────────────────────
class TestZeroLengthHeader:
    """HiFi's previews hide the runtime instead of lying about it.

    Its HLS assembly leaves total_samples=0, so mutagen reports length 0 and the
    density check above has no runtime to measure against. That branch already
    decoded with ffmpeg to catch it - but the decode FAILS OPEN when ffmpeg is
    missing, so an install without ffmpeg was accepting the very clips the
    branch exists to stop. Found by running it on a machine without ffmpeg: a
    30s clip claiming 215s came back ok=True.

    The expected duration is a perfectly good reference, and the bytes still
    cannot lie, so the same arithmetic closes it with no decoder at all.
    """

    @staticmethod
    def _zero_len(sr=44100, bits=16, ch=2, name="FLAC"):
        info = type("I", (object,), {"length": 0.0, "sample_rate": sr,
                                     "bits_per_sample": bits, "channels": ch})
        return type(name, (object,), {"info": info()})()

    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_a_clip_with_a_hidden_runtime_is_caught_when_the_decode_cannot_run(self, tmp_path):
        from unittest import mock
        from core.imports.file_integrity import check_audio_integrity
        clip = _encode(tmp_path / "clip.flac", "-f", "lavfi", "-i", NOISE % 30,
                       "-ac", "2", "-sample_fmt", "s16")
        from core.imports import file_integrity as fi
        # probe returns 0.0 exactly as it does with no ffmpeg installed — the
        # state that used to ACCEPT a 30s clip claiming 215s
        with mock.patch("mutagen.File", return_value=self._zero_len()), \
             mock.patch.object(fi, "probe_decoded_duration", return_value=0.0):
            r = check_audio_integrity(clip, 215_000)      # claims 215s, holds 30s
        assert r.ok is False
        assert "header reports no length" in r.reason

    @_needs_guard_ffmpeg
    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_when_the_decode_CAN_run_it_is_the_one_that_answers(self, tmp_path):
        """The decode is authoritative and goes first. The density fallback only
        exists for the case where it cannot run - putting density first (which I
        did at first) would let a cheap suspicion override a real measurement."""
        from unittest import mock
        from core.imports.file_integrity import check_audio_integrity
        clip = _encode(tmp_path / "clip2.flac", "-f", "lavfi", "-i", NOISE % 30,
                       "-ac", "2", "-sample_fmt", "s16")
        with mock.patch("mutagen.File", return_value=self._zero_len()):
            r = check_audio_integrity(clip, 215_000)
        assert r.ok is False
        assert "Decoded audio is only" in r.reason

    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_a_genuine_streamed_flac_still_passes(self, tmp_path):
        """The false-positive risk this branch was always guarding. A real
        fragmented FLAC carries every frame and just cannot state its length -
        quarantining those is what the fail-open was protecting against."""
        from unittest import mock
        from core.imports.file_integrity import check_audio_integrity
        real = _encode(tmp_path / "real.flac", "-f", "lavfi", "-i", NOISE % 120,
                       "-ac", "2", "-sample_fmt", "s16")
        with mock.patch("mutagen.File", return_value=self._zero_len()):
            r = check_audio_integrity(real, 120_000)
        assert r.ok is True, r.reason

    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_lossy_with_a_zero_length_header_is_untouched(self, tmp_path):
        from unittest import mock
        from core.imports.file_integrity import check_audio_integrity
        mp3 = _encode(tmp_path / "l.mp3", "-f", "lavfi", "-i", NOISE % 120,
                      "-ac", "2", "-b:a", "128k")
        info = type("I", (object,), {"length": 0.0, "sample_rate": 44100, "channels": 2})
        with mock.patch("mutagen.File", return_value=type("MP3", (object,), {"info": info()})()):
            assert check_audio_integrity(mp3, 120_000).ok is True

    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_aac_with_a_zero_length_header_is_untouched(self, tmp_path):
        """The case that matters for the gate here. An MP3 stub has no
        bits_per_sample so the check skips it whether the gate exists or not -
        which made the earlier lossy test unable to fail, and a negative-check
        caught that. AAC DOES report 16 bits, exactly like ALAC, so it is the
        file that actually gets wrongly quarantined if the gate goes."""
        from unittest import mock
        from core.imports.file_integrity import check_audio_integrity
        aac = _encode(tmp_path / "l.m4a", "-f", "lavfi", "-i", NOISE % 120,
                      "-ac", "2", "-c:a", "aac", "-b:a", "160k")
        info = type("I", (object,), {"length": 0.0, "sample_rate": 44100,
                                     "bits_per_sample": 16, "channels": 2,
                                     "codec": "mp4a.40.2"})
        stub = type("MP4", (object,), {"info": info()})()
        # 160kbps AAC is ~11% of raw PCM — it WOULD trip the density rule
        assert check_audio_integrity(aac, 120_000).ok is True
        with mock.patch("mutagen.File", return_value=stub):
            r = check_audio_integrity(aac, 120_000)
        assert r.ok is True, r.reason

    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_no_expected_duration_means_no_judgement(self, tmp_path):
        """With the runtime hidden AND no reference, there is nothing to measure
        against. Accepting is the only honest answer."""
        from unittest import mock
        from core.imports.file_integrity import check_audio_integrity
        clip = _encode(tmp_path / "c.flac", "-f", "lavfi", "-i", NOISE % 30,
                       "-ac", "2", "-sample_fmt", "s16")
        with mock.patch("mutagen.File", return_value=self._zero_len()):
            assert check_audio_integrity(clip, None).ok is True


# ── the guard I did not have, and the bug it would have caught ───────────────
class TestQuietMusicSurvives:
    """Thin is ALSO what quiet music looks like.

    The first cut of this check rejected on density alone and reported "0/30
    false positives" against a healthy library. That number was worthless: the
    control set was pop and rock, which never compresses that hard, so it only
    ever tested where the check works. Encoding actual quiet material broke it
    immediately - ambient at 6.5%, a sparse tone at 7.0%, very soft material at
    24.7%, every one a complete file, every one quarantined.

    That is why density only chooses what to DECODE. These are the cases that
    keep it honest, and they are the reason the check can be on by default: it
    cannot destroy someone's ambient album.
    """

    @pytest.fixture(scope="class")
    def quiet(self, tmp_path_factory):
        if not _FFMPEG:
            pytest.skip("ffmpeg needed to build the fixtures")
        d = tmp_path_factory.mktemp("quiet")
        return {
            "ambient": _encode(d / "ambient.flac", "-f", "lavfi", "-i",
                               "sine=f=220:d=120:r=44100", "-af", "volume=0.05",
                               "-ac", "2", "-sample_fmt", "s16"),
            "tone": _encode(d / "tone.flac", "-f", "lavfi", "-i",
                            "sine=f=440:d=120:r=44100", "-ac", "2", "-sample_fmt", "s16"),
            "soft": _encode(d / "soft.flac", "-f", "lavfi", "-i",
                            "anoisesrc=d=120:c=pink:r=44100:a=0.02",
                            "-ac", "2", "-sample_fmt", "s16"),
        }

    @pytest.mark.parametrize("key", ["ambient", "tone", "soft"])
    def test_it_is_flagged_as_thin(self, quiet, key):
        """The trigger fires - that part is correct and expected."""
        from core.imports.file_integrity import check_audio_integrity
        r = check_audio_integrity(quiet[key], 120_000)
        assert r.checks["lossless_density"] < LOSSLESS_MIN_DENSITY

    @_needs_guard_ffmpeg
    @pytest.mark.parametrize("key", ["ambient", "tone", "soft"])
    def test_but_it_is_NOT_quarantined(self, quiet, key):
        """...and the decode clears it, because the audio is all there."""
        from core.imports.file_integrity import check_audio_integrity
        r = check_audio_integrity(quiet[key], 120_000)
        assert r.ok is True, f"quarantined complete audio: {r.reason}"


class TestFailsOpenWithoutFfmpeg:
    """No decoder means no way to tell a preview from a quiet recording.

    Quarantining someone's ambient album because a tool is missing is worse
    than missing a fake, so the confirmation fails open. This pins that, because
    the alternative is a silent policy change the day ffmpeg goes missing.
    """

    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_a_thin_file_is_accepted_when_confirmation_is_impossible(self, tmp_path):
        from unittest import mock
        from core.imports import file_integrity as fi
        fake = _encode(tmp_path / "f.flac", "-f", "lavfi", "-i", NOISE % 30,
                       "-f", "lavfi", "-i", SILENCE % 90,
                       "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
                       "-ac", "2", "-sample_fmt", "s16", "-t", "120")
        with mock.patch.object(fi, "_confirm_broken_audio", return_value=None):
            r = fi.check_audio_integrity(fake, 120_000)
        assert r.ok is True
        assert r.checks["lossless_density_suspicious"] is True   # recorded, not acted on

    @pytest.mark.skipif(not _FFMPEG, reason="ffmpeg needed to build the fixtures")
    def test_a_confirmation_that_raises_never_quarantines(self, tmp_path):
        from unittest import mock
        from core.imports import file_integrity as fi
        fake = _encode(tmp_path / "f2.flac", "-f", "lavfi", "-i", NOISE % 30,
                       "-f", "lavfi", "-i", SILENCE % 90,
                       "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
                       "-ac", "2", "-sample_fmt", "s16", "-t", "120")
        with mock.patch("core.imports.silence.detect_broken_audio",
                        side_effect=OSError("ffmpeg exploded")):
            assert fi.check_audio_integrity(fake, 120_000).ok is True


class TestTheGuardsOnTheGuard:
    """Two properties that survived a deliberate break with every test green.

    Both are the same shape of hole: a value I fixed by hand and then never
    pinned, so the next edit silently undoes it.
    """

    def test_every_lossless_type_name_is_a_REAL_mutagen_class(self):
        """The list is matched against type(audio).__name__, so a typo does not
        raise - it just silently stops recognising that format. "Monkeys" was
        wrong for exactly this reason and nothing caught it."""
        import importlib
        from core.imports.file_integrity import _LOSSLESS_TYPES
        modules = ("mutagen.flac", "mutagen.wave", "mutagen.aiff", "mutagen.wavpack",
                   "mutagen.trueaudio", "mutagen.monkeysaudio")
        known = set()
        for m in modules:
            known |= {n for n in dir(importlib.import_module(m)) if n[0].isupper()}
        for name in _LOSSLESS_TYPES:
            assert name in known, f"{name!r} is not a real mutagen class"

    def test_monkeys_audio_specifically(self):
        from mutagen.monkeysaudio import MonkeysAudio
        from core.imports.file_integrity import _LOSSLESS_TYPES
        assert MonkeysAudio.__name__ in _LOSSLESS_TYPES

    def test_confirmation_fails_OPEN_when_the_decoder_is_missing(self):
        """The property that lets this run by default. If confirmation ever
        returns a reason it did not actually measure, the check goes back to
        quarantining complete audio - which is the bug this whole redesign
        exists to remove."""
        from unittest import mock
        from core.imports import file_integrity as fi
        with mock.patch.dict("sys.modules", {"core.imports.silence": None}):
            assert fi._confirm_broken_audio("/nonexistent.flac") is None

    def test_confirmation_fails_OPEN_when_the_decoder_raises(self):
        from unittest import mock
        from core.imports import file_integrity as fi
        with mock.patch("core.imports.silence.detect_broken_audio",
                        side_effect=RuntimeError("boom")):
            assert fi._confirm_broken_audio("/nonexistent.flac") is None

    def test_confirmation_returns_what_the_decoder_actually_said(self):
        """...and nothing else. It must not invent, soften or upgrade a verdict."""
        from unittest import mock
        from core.imports import file_integrity as fi
        with mock.patch("core.imports.silence.detect_broken_audio",
                        return_value="Audio is mostly silent: 75% silence"):
            assert fi._confirm_broken_audio("/x.flac") == "Audio is mostly silent: 75% silence"
        with mock.patch("core.imports.silence.detect_broken_audio", return_value=None):
            assert fi._confirm_broken_audio("/x.flac") is None
