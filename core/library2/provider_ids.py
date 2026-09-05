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


def external_id_sql(column: str, service: str) -> str:
    """One service's id out of an ``external_ids`` column, as SQL.

    Both the lookup predicate and the expression index that serves it are built
    from here — SQLite only uses an expression index when the text matches, so
    two hand-written copies would silently degrade to a table scan. The path is
    a literal for the same reason (a bound path cannot match an index), which is
    why ``service`` must already be a normalized provider name; TRIM/CAST mirror
    the Python side's ``str(...).strip()`` so a JSON *number* still compares.
    """
    if normalize_provider_name(service) != service or not service.isalnum():
        raise ValueError(f"Unsafe provider name for SQL: {service!r}")
    return f"TRIM(CAST(json_extract({column},'$.{service}') AS TEXT))"


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


# --- Reading provider identity back out, in SQL ------------------------------
# Two dedicated columns, everything else in ``external_ids`` — that is lib2's
# storage shape, and it is nobody else's business. The JSON responses that carry
# these ids outward have used ``core.source_ids``' vocabulary for years, so the
# projection below hands the catalogue's row back under exactly those names and
# no consumer has to learn a second one.
ARTIST_IDS_SQL = """
    spotify_id AS spotify_artist_id,
    musicbrainz_id,
    json_extract(external_ids, '$.itunes') AS itunes_artist_id,
    json_extract(external_ids, '$.deezer') AS deezer_id,
    json_extract(external_ids, '$.discogs') AS discogs_id,
    json_extract(external_ids, '$.audiodb') AS audiodb_id,
    json_extract(external_ids, '$.tidal') AS tidal_id,
    json_extract(external_ids, '$.qobuz') AS qobuz_id,
    json_extract(external_ids, '$.amazon') AS amazon_id,
    json_extract(external_ids, '$.genius') AS genius_id,
    json_extract(external_ids, '$.lastfm') AS lastfm_url,
    json_extract(external_ids, '$.genius_url') AS genius_url
"""

# The providers that kept a column of their own. Hydrabase is in here because
# its key IS ``soul_id`` — the content id every SoulSync node computes alike —
# and that has a column precisely because it is not one provider's answer among
# many; ``external_ids`` is not a junk drawer for identifiers that aren't.
_DEDICATED_ID_COLUMNS = {
    "spotify": "spotify_id",
    "musicbrainz": "musicbrainz_id",
    "hydrabase": "soul_id",
}


def provider_id_sql(source: Any, *, alias: str = "") -> Optional[str]:
    """A SQL expression for one provider's id on a lib2 row, or ``None``.

    Callers that used to interpolate a legacy per-provider column name ask for
    this instead — it resolves to the column when the provider has one and to
    the ``external_ids`` slot when it doesn't.
    """

    provider = normalize_provider_name(source)
    if not provider:
        return None
    prefix = f"{alias}." if alias else ""
    column = _DEDICATED_ID_COLUMNS.get(provider)
    if column:
        return f"{prefix}{column}"
    return f"json_extract({prefix}external_ids, '$.{provider}')"


def external_provider_identity_sql(alias: str = "") -> str:
    """A SQL predicate: does this row carry a provider id in ``external_ids``?

    "Is this row independently identified?" is asked wherever deleting it is on
    the table, and the dedicated columns only cover Spotify and MusicBrainz. A
    check written against those two alone silently treats a Deezer-, Tidal- or
    Qobuz-identified row as legacy-owned scrap (MIG-04). ``isrc``/``upc``/
    ``barcode`` are product codes rather than provider identities and are
    excluded here; callers that count them do so with their own column.
    """
    prefix = f"{alias}." if alias else ""
    excluded = ", ".join(f"'{key}'" for key in sorted(_NON_PROVIDER_KEYS))
    return (
        f"EXISTS (SELECT 1 FROM json_each({prefix}external_ids)"
        f"         WHERE json_each.key NOT IN ({excluded})"
        f"           AND NULLIF(TRIM(CAST(json_each.value AS TEXT)), '') IS NOT NULL)"
    )


def any_provider_id_sql(alias: str = "") -> str:
    """A SQL predicate: does this lib2 row carry a given id, from any provider?

    Takes the searched id once per placeholder — every dedicated column plus
    the ``external_ids`` bucket. :data:`ANY_PROVIDER_ID_PARAMS` is how many.
    """

    prefix = f"{alias}." if alias else ""
    columns = " OR ".join(f"{prefix}{column} = ?"
                          for column in sorted(_DEDICATED_ID_COLUMNS.values()))
    return (
        f"({columns}"
        f" OR EXISTS (SELECT 1 FROM json_each({prefix}external_ids)"
        f"            WHERE json_each.value = ?))"
    )


#: Placeholders :func:`any_provider_id_sql` binds, so callers can say
#: ``(value,) * ANY_PROVIDER_ID_PARAMS`` and never miscount.
ANY_PROVIDER_ID_PARAMS = len(set(_DEDICATED_ID_COLUMNS.values())) + 1


__all__ = [
    "ANY_PROVIDER_ID_PARAMS",
    "ARTIST_IDS_SQL",
    "any_provider_id_sql",
    "external_provider_identity_sql",
    "merge_provider_id",
    "normalize_provider_name",
    "parse_external_ids",
    "preferred_provider_identity",
    "provider_only",
    "provider_id_sql",
    "source_ids_from_values",
]
