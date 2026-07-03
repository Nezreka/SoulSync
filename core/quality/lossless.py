"""Single source of truth for "is this audio lossless?" + the lossy-copy
overwrite invariant.

The "create a lossy copy of lossless tracks" feature lives in two places (the
import post-processing path and the Lossy Converter repair job). Both used to
hardcode ``.flac``, which is exactly how ALAC/WAV/DSD ended up being quality-
profile options but NOT lossy-copy sources (#941). The knowledge of which
formats are lossless now lives HERE, derived from the same format names the
quality model ranks, so adding a format lights it up in both sites at once and
they can never drift.

Two seams, both pure (no I/O) so they're unit-testable without real files:

* :func:`is_lossless_format` / :func:`is_lossless_audio_path` — eligibility.
  ``.m4a``/``.mp4`` are ambiguous (ALAC=lossless, AAC=lossy) and can only be told
  apart by codec, so the path check delegates them to an *injected* ``probe_codec``
  callable — the file I/O stays at the edge, the decision stays pure.
* :func:`lossy_output_would_overwrite_source` — the safety invariant: a lossy copy
  must NEVER be written over its own source (which becomes possible once ``.m4a``
  is eligible and the target codec is AAC → same ``.m4a`` path).
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from core.quality.source_map import AUDIO_EXTENSIONS, format_from_extension

# Canonical lossless format set. MUST stay in sync with the lossless tiers in
# core.quality.model.tier_score and the frontend RT_LOSSLESS_FORMATS — the
# consistency test in tests/quality/test_lossless.py pins the tier agreement.
LOSSLESS_FORMATS = frozenset({'flac', 'alac', 'wav', 'dsf'})

# Container extensions that may hold EITHER lossless (ALAC) or lossy (AAC) audio —
# only a codec probe can decide, so they're never lossless on extension alone.
_AMBIGUOUS_EXTS = frozenset({'m4a', 'mp4'})


def is_lossless_format(fmt: Any) -> bool:
    """True when a unified format name (as returned by ``format_from_extension``)
    is lossless. ``'aac'`` is False here — an ALAC-in-m4a file reports format
    ``'aac'`` by extension and must be resolved by codec, not by name."""
    return str(fmt or '').lower() in LOSSLESS_FORMATS


# Extensions worth checking as possibly-lossless (a caller can pre-filter SQL by
# these, then confirm each via is_lossless_audio_path). Derived from the format
# map so it can't drift: every extension whose format is lossless, plus the
# ambiguous ALAC containers. Leading dot included (e.g. '.flac', '.m4a').
LOSSLESS_CANDIDATE_EXTENSIONS = frozenset(
    e for e in AUDIO_EXTENSIONS
    if is_lossless_format(format_from_extension(e)) or e.lstrip('.') in _AMBIGUOUS_EXTS
)


def _ext(path: Any) -> str:
    return os.path.splitext(str(path or ''))[1].lower().lstrip('.')


def is_lossless_audio_path(
    path: Any,
    *,
    probe_codec: Optional[Callable[[str], Optional[str]]] = None,
) -> bool:
    """True when the file at ``path`` is lossless.

    Unambiguous extensions (flac/wav/aiff/dsf/dff/alac) are decided by extension.
    ``.m4a``/``.mp4`` are decided by ``probe_codec(path)`` (returns the codec, e.g.
    ``'alac'`` or ``'aac'``) — without a probe they're treated as NOT lossless, so
    a missing probe can never misclassify an AAC file as lossless.
    """
    ext = _ext(path)
    if is_lossless_format(format_from_extension(ext)):
        return True
    if ext in _AMBIGUOUS_EXTS and probe_codec is not None:
        try:
            codec = (probe_codec(str(path)) or '').lower()
        except Exception:
            return False
        return 'alac' in codec
    return False


def lossy_output_would_overwrite_source(source_path: Any, output_path: Any) -> bool:
    """True when the computed lossy-copy output path is the source file itself.

    Safety invariant: the converter must skip (never run ffmpeg with ``-y``) when
    this is True, or it would destroy the original. Happens when an ``.m4a`` ALAC
    source is converted with the AAC codec (output is also ``.m4a``)."""
    if not source_path or not output_path:
        return False
    a = os.path.normcase(os.path.normpath(str(source_path)))
    b = os.path.normcase(os.path.normpath(str(output_path)))
    return a == b


__all__ = [
    'LOSSLESS_FORMATS',
    'is_lossless_format',
    'is_lossless_audio_path',
    'lossy_output_would_overwrite_source',
]
