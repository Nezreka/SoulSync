"""Subtitle provider registry + the hybrid-chain fetcher.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

from typing import Any, Callable

from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery
from .opensubtitles import OpenSubtitlesProvider

__all__ = ["get_providers", "fetch_subtitle",
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


def fetch_subtitle(query: SubtitleQuery, provider_order: list[str],
                   get_setting: Callable[[str, Any], Any]) -> str | None:
    """Hybrid-chain fetch: try each configured provider in order, first success wins.

    ``provider_order`` is the parsed ``subtitle_provider_order`` setting (JSON list,
    default ``["opensubtitles"]``). Within a provider, every candidate is tried in
    order before falling through to the next provider.

    Best-effort BY CONTRACT — never raises. Unknown ids, non-string ids (an
    unhashable pid would make ``dict.get`` raise TypeError), unconfigured
    providers, misses, and provider exceptions all fall through to the next
    provider; an empty (or missing) order returns None.
    """
    if not isinstance(provider_order, (list, tuple)):
        return None
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
            candidates = provider.search(query) or []
        except Exception:  # noqa: BLE001 - a dead provider never breaks the chain
            continue
        for candidate in candidates:
            try:
                text = provider.download(candidate)
            except Exception:  # noqa: BLE001
                continue
            if text:
                return text
    return None
