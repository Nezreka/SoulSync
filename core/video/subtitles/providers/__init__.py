"""Subtitle provider registry + the hybrid-chain fetcher.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

from typing import Any, Callable

from ..scoring import DEFAULT_MIN_SCORE
from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery
from .opensubtitles import OpenSubtitlesProvider

__all__ = ["get_providers", "fetch_subtitle", "fetch_subtitle_detailed",
           "SubtitleProvider", "SubtitleQuery", "SubtitleCandidate"]


def get_providers() -> dict[str, SubtitleProvider]:
    """Every available subtitle provider, keyed by provider id.

    EXPLICIT registration: adding provider #2 is one line here. (Bazarr scans its
    providers directory at import time — with a ``# fixme: this is bad`` comment —
    we do it properly.) Fresh instances per call: providers cache resolved config
    on ``is_configured()``, so sharing instances across different settings would
    leak one caller's config into another's.
    """
    return {
        "opensubtitles": OpenSubtitlesProvider(),
    }


def _min_score(get_setting: Callable[[str, Any], Any]) -> float:
    """The download gate: candidates scoring below this are a miss. Tunable via
    the ``subtitle_min_score`` setting; falls back to DEFAULT_MIN_SCORE."""
    try:
        return float(get_setting("subtitle_min_score", DEFAULT_MIN_SCORE))
    except (TypeError, ValueError):
        return DEFAULT_MIN_SCORE


def _iter_configured(provider_order, get_setting):
    """(provider_id, provider) for each usable entry in the order. Best-effort."""
    if not isinstance(provider_order, (list, tuple)):
        return
    providers = get_providers()
    for pid in provider_order:
        if not isinstance(pid, str):
            continue  # unhashable / non-id garbage — skip, never raise
        provider = providers.get(pid)
        if provider is None:
            continue  # unknown provider id
        try:
            if not provider.is_configured(get_setting):
                continue
        except Exception:  # noqa: BLE001 - a broken config check reads as unconfigured
            continue
        yield pid, provider


def fetch_subtitle_detailed(query: SubtitleQuery, provider_order: list[str],
                            get_setting: Callable[[str, Any], Any],
                            on_download: Callable[[str], None] | None = None
                            ) -> tuple[str | None, str | None, SubtitleCandidate | None, float]:
    """(text, provider_id, candidate, score) for the best above-threshold candidate.

    Provider order is priority: each configured provider's candidates are scored
    hash-first, and candidates at/above the minimum score are tried in score
    order until one downloads — the first success wins. Below-threshold
    candidates are a miss, never downloaded. On a miss, ``score``/``candidate``
    describe the best below-threshold candidate seen (0.0/None when nothing was
    found at all), so history can distinguish the two. Best-effort BY CONTRACT —
    never raises.

    ``on_download`` is called with the provider id for EVERY successful
    provider.download() — a quota-burning call. Quota-tracking callers should
    bump there, not once per fetch, because several candidates can burn quota
    before one succeeds.
    """
    from ..scoring import score_candidate
    min_score = _min_score(get_setting)
    # No scoring signal (no filename, no hash): the threshold can't distinguish
    # anything, so the gate is skipped and provider order decides — the Phase 1
    # "first success wins" behavior. Production callers (import hook, worker)
    # always populate filename+moviehash, so the gate applies in practice.
    blind = not getattr(query, "filename", None) and not getattr(query, "moviehash", None)
    if blind:
        min_score = -1.0
    best_miss: tuple[float, str | None, Any] = (0.0, None, None)  # score, pid, candidate
    for pid, provider in _iter_configured(provider_order, get_setting):
        try:
            candidates = provider.search(query) or []
        except Exception:  # noqa: BLE001 - a dead provider never breaks the chain
            continue
        # Score order, best first; the provider's original order breaks ties
        # (sorted is stable, so enumerate-then-sort keeps it deterministic).
        ranked = sorted(
            ((score_candidate(c, query), i, c) for i, c in enumerate(candidates)),
            key=lambda t: (-t[0], t[1]))
        for score, _i, candidate in ranked:
            if score < min_score:
                if score > best_miss[0]:
                    best_miss = (score, pid, candidate)
                break  # sorted desc — the rest are worse
            try:
                text = provider.download(candidate)
            except Exception:  # noqa: BLE001
                continue  # try the next candidate
            if text:
                if callable(on_download):
                    try:
                        on_download(pid)
                    except Exception:  # noqa: BLE001 - accounting never breaks a fetch
                        pass
                return text, pid, candidate, score
    # Miss: report the best below-threshold score (0.0 when no candidate
    # existed anywhere) so history distinguishes "nothing found" from
    # "found but too weak".
    return None, None, best_miss[2], best_miss[0]


def fetch_subtitle(query: SubtitleQuery, provider_order: list[str],
                   get_setting: Callable[[str, Any], Any]) -> str | None:
    """Hybrid-chain fetch: try each configured provider in order, first success wins.

    ``provider_order`` is the parsed ``subtitle_provider_order`` setting (JSON list,
    default ``["opensubtitles"]``). Within a provider, candidates are scored
    hash-first and only the best at/above the minimum score
    (``subtitle_min_score`` setting) is downloaded — below threshold is a miss.

    Best-effort BY CONTRACT — never raises. Unknown ids, non-string ids (an
    unhashable pid would make ``dict.get`` raise TypeError), unconfigured
    providers, misses, and provider exceptions all fall through to the next
    provider; an empty (or missing) order returns None.
    """
    text, _pid, _cand, _score = fetch_subtitle_detailed(query, provider_order, get_setting)
    return text
