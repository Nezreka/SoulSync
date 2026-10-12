"""Subtitle provider registry + the hybrid-chain fetcher.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

from typing import Any, Callable

from ..scoring import DEFAULT_MIN_SCORE
from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery
from .embedded import EmbeddedProvider
from .opensubtitles import OpenSubtitlesProvider
from .subdl import SubDLProvider
from .subsource import SubSourceProvider
from .whisper import WhisperProvider

__all__ = ["get_providers", "fetch_subtitle", "fetch_subtitle_detailed",
           "rank_candidates", "iter_configured_providers",
           "provider_is_quota_exempt",
           "SubtitleProvider", "SubtitleQuery", "SubtitleCandidate"]


def get_providers() -> dict[str, SubtitleProvider]:
    """Every available subtitle provider, keyed by provider id.

    EXPLICIT registration: adding provider #2 is one line here. (Bazarr scans its
    providers directory at import time — with a ``# fixme: this is bad`` comment —
    we do it properly.) Fresh instances per call: providers cache resolved config
    on ``is_configured()``, so sharing instances across different settings would
    leak one caller's config into another's.

    Whisper is ALWAYS last: local transcription is the final fallback, after
    every network provider has missed.
    """
    return {
        "opensubtitles": OpenSubtitlesProvider(),
        "subdl": SubDLProvider(),
        "subsource": SubSourceProvider(),
        "embedded": EmbeddedProvider(),
        "whisper": WhisperProvider(),
    }


def provider_is_quota_exempt(pid: Any) -> bool:
    """True when ``pid``'s downloads never burn network-provider quota
    (``burns_network_quota = False`` — local providers like Whisper).
    Unknown ids are NOT exempt. Never raises."""
    try:
        provider = get_providers().get(pid)
    except Exception:  # noqa: BLE001
        return False
    return not bool(getattr(provider, "burns_network_quota", True))


def _min_score(get_setting: Callable[[str, Any], Any]) -> float:
    """The download gate: candidates scoring below this are a miss. Tunable via
    the ``subtitle_min_score`` setting; falls back to DEFAULT_MIN_SCORE."""
    try:
        return float(get_setting("subtitle_min_score", DEFAULT_MIN_SCORE))
    except (TypeError, ValueError):
        return DEFAULT_MIN_SCORE


def _iter_configured(provider_order, get_setting, skip_background=False):
    """(provider_id, provider) for each usable entry in the order. Best-effort.

    Background-only providers (Whisper: ``background_download_only``) are
    pinned LAST and are appended even when the stored order predates them —
    enabling local transcription needs no chain editing, and it can never
    jump ahead of a network provider. ``skip_background=True`` leaves them
    out entirely (the import hook defers them to the worker; the eager
    ``rank_candidates`` never runs them).
    """
    if not isinstance(provider_order, (list, tuple)):
        return
    providers = get_providers()
    seen: set[str] = set()
    deferred: list[tuple[str, SubtitleProvider]] = []
    for pid in provider_order:
        if not isinstance(pid, str):
            continue  # unhashable / non-id garbage — skip, never raise
        if pid in seen:
            continue
        seen.add(pid)
        provider = providers.get(pid)
        if provider is None:
            continue  # unknown provider id
        try:
            if not provider.is_configured(get_setting):
                continue
        except Exception:  # noqa: BLE001 - a broken config check reads as unconfigured
            continue
        if getattr(provider, "background_download_only", False):
            if skip_background:
                continue
            deferred.append((pid, provider))  # pinned below, after network
            continue
        yield pid, provider
    if skip_background:
        return
    # Append configured background-only providers the stored order doesn't
    # name (fresh installs, or orders saved before Whisper existed).
    for pid, provider in providers.items():
        if pid in seen:
            continue
        if not getattr(provider, "background_download_only", False):
            continue
        try:
            if not provider.is_configured(get_setting):
                continue
        except Exception:  # noqa: BLE001 - a broken config check reads as unconfigured
            continue
        deferred.append((pid, provider))
    for pid, provider in deferred:
        yield pid, provider


def iter_configured_providers(provider_order, get_setting, skip_background=False):
    """Public alias of the configured-provider iterator (the manual-search API
    needs it too)."""
    return _iter_configured(provider_order, get_setting,
                             skip_background=skip_background)


def _rank_provider_candidates(provider, query, min_score):
    """One provider's search results, ranked: returns ``([(score, candidate)],``
    (score-desc, at/above the gate) and the best BELOW-gate ``(score,
    candidate)`` (``(0.0, None)`` when none). The provider's own result order
    breaks score ties (sorted is stable). Never raises — a dead provider reads
    as no candidates."""
    from ..scoring import score_candidate
    try:
        candidates = provider.search(query) or []
    except Exception:  # noqa: BLE001 - a dead provider never breaks the chain
        return [], (0.0, None)
    ranked = sorted(
        ((score_candidate(c, query), i, c) for i, c in enumerate(candidates)),
        key=lambda t: (-t[0], t[1]))
    ordered: list = []
    best_miss: tuple[float, Any] = (0.0, None)  # score, candidate
    for score, _i, candidate in ranked:
        if score < min_score:
            if score > best_miss[0]:
                best_miss = (score, candidate)
            break  # sorted desc — the rest are worse
        ordered.append((score, candidate))
    return ordered, best_miss


def _gate(get_setting, query) -> float:
    """The download gate for this query. No scoring signal (no filename, no
    hash): the threshold can't distinguish anything, so the gate is skipped
    and provider order decides — the Phase 1 "first success wins" behavior.
    Production callers (import hook, worker) always populate filename+moviehash,
    so the gate applies in practice."""
    min_score = _min_score(get_setting)
    blind = not getattr(query, "filename", None) and not getattr(query, "moviehash", None)
    if blind:
        min_score = -1.0
    return min_score


def rank_candidates(query: SubtitleQuery, provider_order: list[str],
                    get_setting: Callable[[str, Any], Any]) -> list:
    """[(score, provider_id, provider, candidate)], score desc across EVERY
    configured provider (provider order breaks ties), at/above the min-score
    gate (blind queries skip the gate).

    EAGER: searches every configured provider — this is for the Phase 3
    upgrade loop and the manual-search API, which need the true best candidate
    across providers. The fetch chain (``fetch_subtitle_detailed``) stays LAZY
    per provider and never consults a later provider once an earlier one
    succeeds.

    Background-only providers (Whisper) are NEVER ranked here: their
    ``download()`` is minutes of local CPU work that can't run in an upgrade
    pass or a request thread. (The upgrade loop must also never auto-replace
    a real subtitle with a generated transcript.)
    """
    min_score = _gate(get_setting, query)
    merged: list = []
    for order_idx, (pid, provider) in enumerate(
            _iter_configured(provider_order, get_setting,
                             skip_background=True)):
        ordered, _miss = _rank_provider_candidates(provider, query, min_score)
        for score, candidate in ordered:
            merged.append((score, order_idx, pid, provider, candidate))
    # Stable: full ties keep each provider's internal result order.
    merged.sort(key=lambda t: (-t[0], t[1]))
    return [(s, pid, prov, c) for s, _o, pid, prov, c in merged]


def fetch_subtitle_detailed(query: SubtitleQuery, provider_order: list[str],
                            get_setting: Callable[[str, Any], Any],
                            on_download: Callable[[str], None] | None = None,
                            skip_background: bool = False,
                            quota_check: Callable[[str], bool] | None = None,
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
    before one succeeds. (Whisper sets ``burns_network_quota = False`` — the
    bump callbacks skip it via ``provider_is_quota_exempt``.)

    ``skip_background=True`` leaves background-only providers (Whisper) out of
    the chain: the import hook uses it so a minutes-long transcription never
    stalls the download monitor — the wanted-loop worker runs those providers
    instead (its call uses the default False).

    ``quota_check`` is an optional ``(provider_id) -> bool``: when given, a
    provider whose check returns False is skipped entirely (none of its
    candidates are downloaded). Quota-exempt providers (local — embedded,
    Whisper) are never checked. Without it the chain trusts the caller to
    have gated quota beforehand, which is wrong once several network
    providers share one chain: an exhausted OpenSubtitles must not keep
    downloading just because SubDL still has room.
    """
    min_score = _gate(get_setting, query)
    best_miss: tuple[float, str | None, Any] = (0.0, None, None)  # score, pid, candidate
    for pid, provider in _iter_configured(provider_order, get_setting,
                                          skip_background=skip_background):
        # Quota gate BEFORE the search: an exhausted provider's candidates
        # can never be downloaded, so spending a search API call on it is
        # pure waste. Exempt (local) providers are never gated; a broken
        # check reads as "has quota" — accounting never blocks a fetch.
        if quota_check is not None and not provider_is_quota_exempt(pid):
            try:
                if not quota_check(pid):
                    continue
            except Exception:  # noqa: BLE001
                pass
        # LAZY per provider: a later provider is never searched once an
        # earlier one yields a download (its search() is an API call — don't
        # spend it when the answer is already in hand).
        ordered, (miss_score, miss_cand) = _rank_provider_candidates(
            provider, query, min_score)
        if miss_score > best_miss[0]:
            best_miss = (miss_score, pid, miss_cand)
        for score, candidate in ordered:
            # Per-provider quota: an exhausted provider yields no downloads,
            # even when a later provider in the chain still has room. Exempt
            # (local) providers are never gated. A broken check reads as
            # "has quota" — accounting never blocks a fetch.
            if quota_check is not None and not provider_is_quota_exempt(pid):
                try:
                    if not quota_check(pid):
                        break  # out of quota: next provider, not next candidate
                except Exception:  # noqa: BLE001
                    pass
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
