"""Provider-qualified identity helpers for the native Library-v2 catalogue.

Library v2 stores the two most frequently indexed identifiers in dedicated
columns and every other provider identifier in ``external_ids``.  Callers must
not guess a provider from the value shape: numeric identifiers are used by
several catalogues and a fallback search may return a provider different from
the one that was attempted first.  This module is the single normalization
boundary used by maintenance tools and typed provider adapters.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


_NON_PROVIDER_KEYS = frozenset({"barcode", "isrc", "upc"})


def normalize_provider_name(value: Any) -> Optional[str]:
    """Return a safe lower-case provider namespace, or ``None``.

    Provider names are deliberately conservative because they are later used
    as JSON keys and provenance labels.  Hyphens and underscores are accepted;
    whitespace and punctuation are not silently rewritten.
    """

    text = str(value or "").strip().lower()
    if not text or not all(char.isalnum() or char in {"-", "_"} for char in text):
        return None
    return text


def parse_external_ids(raw: Any) -> Dict[str, str]:
    """Parse a provider-keyed mapping without inventing a namespace."""

    if isinstance(raw, Mapping):
        payload = raw
    else:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            payload = {}
    if not isinstance(payload, Mapping):
        return {}
    result: Dict[str, str] = {}
    for source, value in payload.items():
        provider = normalize_provider_name(source)
        identifier = str(value or "").strip()
        if provider and identifier:
            result[provider] = identifier
    return result


def source_ids_from_values(
    *,
    spotify_id: Any = None,
    musicbrainz_id: Any = None,
    external_ids: Any = None,
    isrc: Any = None,
    upc: Any = None,
) -> Dict[str, str]:
    """Return one namespace-correct identity mapping for an entity row.

    Dedicated columns are authoritative for their namespace.  The external
    JSON may repeat them, but it cannot replace a non-empty dedicated value.
    Provider-neutral identifiers are included under their explicit semantic
    keys so consumers can use them for edition validation without treating
    them as a catalogue provider.
    """

    result = parse_external_ids(external_ids)
    spotify = str(spotify_id or "").strip()
    musicbrainz = str(musicbrainz_id or "").strip()
    if spotify:
        result["spotify"] = spotify
    if musicbrainz:
        result["musicbrainz"] = musicbrainz
    recording_code = str(isrc or "").strip()
    barcode = str(upc or "").strip()
    if recording_code:
        result["isrc"] = recording_code
    if barcode:
        result["upc"] = barcode
    return result


def provider_only(source_ids: Mapping[str, Any]) -> Dict[str, str]:
    """Drop provider-neutral identity keys from a source-id mapping."""

    return {
        provider: str(value).strip()
        for provider, value in source_ids.items()
        if provider not in _NON_PROVIDER_KEYS and str(value or "").strip()
    }


def preferred_provider_identity(
    source_ids: Mapping[str, Any],
    source_order: Iterable[str] = (),
) -> Tuple[Optional[str], Optional[str]]:
    """Choose an explicitly stored provider identity without relabelling it."""

    values = provider_only(source_ids)
    order = [str(source).strip().lower() for source in source_order if source]
    order.extend(sorted(set(values) - set(order)))
    for provider in order:
        if values.get(provider):
            return provider, values[provider]
    return None, None


def merge_provider_id(
    raw: Any,
    provider: Any,
    provider_id: Any,
    *,
    overwrite: bool = False,
) -> str:
    """Merge one explicitly-qualified ID and return canonical JSON.

    A conflicting ID is preserved by default.  Silent replacement is unsafe:
    it commonly means two provider releases were incorrectly treated as the
    same local entity.
    """

    namespace = normalize_provider_name(provider)
    identifier = str(provider_id or "").strip()
    if not namespace or not identifier:
        raise ValueError("provider and provider_id are required")
    values = parse_external_ids(raw)
    if overwrite or not values.get(namespace):
        values[namespace] = identifier
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "merge_provider_id",
    "normalize_provider_name",
    "parse_external_ids",
    "preferred_provider_identity",
    "provider_only",
    "source_ids_from_values",
]
