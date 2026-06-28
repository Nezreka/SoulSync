"""The canonical "is this lossless?" seam + the lossy-copy overwrite invariant
(#941). All pure — no files, no ffmpeg — so the decision and the safety guard are
unit-testable in isolation. Both the import path (create_lossy_copy) and the Lossy
Converter repair job route through these, so the same rules drive both."""

from core.quality.lossless import (
    LOSSLESS_FORMATS,
    LOSSLESS_CANDIDATE_EXTENSIONS,
    is_lossless_format,
    is_lossless_audio_path,
    lossy_output_would_overwrite_source,
)
from core.quality.model import AudioQuality
from core.repair_jobs.lossy_converter import _lossless_ext_where


# ── is_lossless_format ──

def test_lossless_formats_recognized():
    for fmt in ('flac', 'alac', 'wav', 'dsf', 'FLAC', 'Dsf'):
        assert is_lossless_format(fmt) is True


def test_lossy_formats_not_lossless():
    for fmt in ('mp3', 'aac', 'ogg', 'opus', 'wma', 'unknown', '', None):
        assert is_lossless_format(fmt) is False


# ── is_lossless_audio_path (the ambiguity is the whole point) ──

def test_unambiguous_extensions_decided_by_extension():
    for path in ('/m/a.flac', '/m/a.wav', '/m/a.wave', '/m/a.aiff', '/m/a.aif',
                 '/m/a.dsf', '/m/a.dff', '/m/a.alac', '/m/A.FLAC'):
        assert is_lossless_audio_path(path) is True


def test_lossy_extensions_are_not_lossless():
    for path in ('/m/a.mp3', '/m/a.ogg', '/m/a.opus', '/m/a.wma', '/m/a.aac'):
        assert is_lossless_audio_path(path) is False


def test_m4a_without_probe_is_not_lossless():
    # The safe default: with no codec probe, an .m4a can't be proven lossless, so
    # an AAC file is never misclassified as lossless and converted/deleted.
    assert is_lossless_audio_path('/m/a.m4a') is False
    assert is_lossless_audio_path('/m/a.mp4') is False


def test_m4a_alac_is_lossless_via_probe():
    assert is_lossless_audio_path('/m/a.m4a', probe_codec=lambda _p: 'alac') is True
    assert is_lossless_audio_path('/m/a.mp4', probe_codec=lambda _p: 'ALAC') is True


def test_m4a_aac_is_not_lossless_via_probe():
    assert is_lossless_audio_path('/m/a.m4a', probe_codec=lambda _p: 'mp4a.40.2') is False


def test_probe_exception_is_not_lossless():
    def _boom(_p):
        raise RuntimeError("probe failed")
    assert is_lossless_audio_path('/m/a.m4a', probe_codec=_boom) is False


# ── LOSSLESS_CANDIDATE_EXTENSIONS (the SQL pre-filter set) ──

def test_candidate_extensions_cover_lossless_plus_ambiguous():
    for e in ('.flac', '.wav', '.aiff', '.dsf', '.dff', '.alac', '.m4a', '.mp4'):
        assert e in LOSSLESS_CANDIDATE_EXTENSIONS
    # raw lossy extensions must NOT be candidates
    for e in ('.mp3', '.aac', '.ogg', '.opus', '.wma'):
        assert e not in LOSSLESS_CANDIDATE_EXTENSIONS


def test_sql_where_clause_matches_candidates_only():
    where = _lossless_ext_where('t.file_path')
    assert "LIKE '%.flac'" in where and "LIKE '%.dsf'" in where and "LIKE '%.m4a'" in where
    assert "LIKE '%.mp3'" not in where and "LIKE '%.aac'" not in where


# ── lossy_output_would_overwrite_source (the safety invariant) ──

def test_overwrite_detected_when_paths_equal():
    assert lossy_output_would_overwrite_source('/m/Album/01.m4a', '/m/Album/01.m4a') is True


def test_overwrite_detected_after_normalization():
    assert lossy_output_would_overwrite_source('/m/Album/../Album/01.m4a', '/m/Album/01.m4a') is True


def test_no_overwrite_for_different_extension():
    assert lossy_output_would_overwrite_source('/m/Album/01.flac', '/m/Album/01.mp3') is False
    assert lossy_output_would_overwrite_source('/m/Album/01.m4a', '/m/Album/01.mp3') is False


def test_overwrite_guard_handles_empty():
    assert lossy_output_would_overwrite_source('', '/x.mp3') is False
    assert lossy_output_would_overwrite_source('/x.flac', '') is False


# ── anti-drift: the seam must agree with the quality tier model ──

def test_lossless_set_consistent_with_tier_model():
    """If a format is in LOSSLESS_FORMATS it must out-rank every lossy format in
    tier_score — guards against the two lists drifting apart (the whole reason
    this seam exists)."""
    lossy = ('mp3', 'aac', 'ogg', 'opus', 'wma')
    worst_lossless = min(AudioQuality(f, bitrate=11290, sample_rate=44100, bit_depth=16).tier_score()
                         for f in LOSSLESS_FORMATS)
    best_lossy = max(AudioQuality(f, bitrate=320).tier_score() for f in lossy)
    assert worst_lossless > best_lossy


# ── regression: the exact bug class this guards (overwrite the original) ──

def test_regression_m4a_alac_to_aac_would_overwrite_and_is_blocked():
    """An .m4a ALAC source converted with the AAC codec lands on the SAME .m4a
    path. The guard must catch it so ffmpeg -y never destroys the original."""
    src = '/library/Sade/Diamond Life/01. Smooth Operator.m4a'   # ALAC
    out = src                                                     # AAC target → .m4a
    assert is_lossless_audio_path(src, probe_codec=lambda _p: 'alac') is True
    assert lossy_output_would_overwrite_source(src, out) is True   # → callers skip
