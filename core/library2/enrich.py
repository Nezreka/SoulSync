"""Resync a lib2 entity's provider-sourced fields from its legacy counterpart
row right after ``core.metadata`` enrichment workers (see ``web_server.py``'s
``_run_single_enrichment``) re-query a provider and write fresh data into it.

lib2 rows are a point-in-time mirror of the legacy library (see
``core.library2.importer``): enrichment only ever updates the LEGACY row, so
without this the refreshed data would be invisible in the lib2 UI until a
full re-import. Unlike the bulk importer's upsert (which never regresses a
richer existing value across incremental imports — see
``_ArtistResolver.upsert_legacy``), a user-triggered Enrich is an explicit
"pull fresh data now" action for ONE entity, so its provider-owned fields are
safe to overwrite outright — except we still guard against clobbering good
existing data with a legacy column that some OTHER, untouched provider left
NULL, hence ``COALESCE``. Identity fields (name/title) are intentionally left
alone; Enrich only refreshes descriptive metadata. User overrides
(``core.library2.metadata_overrides``) are layered on top at read time
regardless of the base row, so overwriting the base row here is always safe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


def _row_get(row: Any, col: str) -> Optional[Any]:
    return row[col] if col in row.keys() else None


def _normalize_genres(raw: Any) -> str:
    """Mirror legacy genre storage (JSON array OR comma string) → JSON array
    string. Duplicated from ``importer._normalize_genres`` (module-private,
    same tiny-helper-duplication precedent as ``_precache_max_workers`` in
    ``artwork.py``/``completeness.py``)."""
    if not raw:
        return "[]"
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return json.dumps([str(g).strip() for g in parsed if str(g).strip()])
    except (ValueError, TypeError):
        pass
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return json.dumps(parts)


def _provider_ids(legacy_row: Any, entity_type: str) -> Dict[str, Any]:
    """Every provider id the legacy row carries for this entity type.

    Same ``match_status.SERVICES`` mapping the importer and the match chips
    use, so a provider added there is mirrored here without a second edit.
    """
    from core.library2.match_status import SERVICES

    out: Dict[str, Any] = {}
    for service, _label, id_columns in SERVICES:
        column = id_columns.get(entity_type)
        if not column:
            continue
        value = _row_get(legacy_row, column)
        if value not in (None, ""):
            out[service] = str(value).strip()
    return out


def _merge_json_column(conn, table: str, entity_id: int, column: str,
                       incoming: Dict[str, Any]) -> None:
    """Merge ``incoming`` into a JSON object column, legacy winning per key.

    Deliberately different from the importer's ``_merge_external_ids``, which
    uses ``setdefault`` so a thinner re-import can never regress a richer row.
    Here the trigger fired *because* an enrichment worker just wrote that very
    value, so legacy is the fresher source by construction — keeping the old
    value would mean a corrected provider id could never reach lib2. Keys the
    legacy row has nothing for are left untouched either way.

    The exception is a service whose worker has migrated: it writes lib2 itself, so
    legacy is no longer the fresher source for it and the merge fills only what
    lib2 lacks. Per key, not per bucket — a Last.fm bucket the worker populated
    with a bio can still receive a listener count legacy has and lib2 does not.
    """
    if not incoming:
        return
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE id=?", (entity_id,)).fetchone()
    try:
        current = json.loads((_row_get(row, column) if row else None) or "{}")
        if not isinstance(current, dict):
            current = {}
    except (TypeError, ValueError):
        current = {}
    merged = dict(current)
    for key, value in incoming.items():
        backfill_only = _migrated(key)
        if isinstance(value, dict):
            bucket = dict(merged.get(key) or {}) if isinstance(merged.get(key), dict) else {}
            for sub_key, sub_value in value.items():
                if backfill_only and not _is_absent(bucket.get(sub_key)):
                    continue
                bucket[sub_key] = sub_value
            merged[key] = bucket
        elif not (backfill_only and not _is_absent(merged.get(key))):
            merged[key] = value
    if merged != current:
        conn.execute(
            f"UPDATE {table} SET {column}=? WHERE id=?",
            (json.dumps(merged, sort_keys=True, separators=(",", ":")), entity_id))


def _list(raw):
    """Legacy stores repeated values as a JSON array or a comma string."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else None
    except (TypeError, ValueError):
        parts = [p.strip() for p in str(raw).split(",") if p.strip()]
        return parts or None


# The provider payload each entity type carries, as ``{source: {key: legacy
# column}}``. Values wrapped in ``_AsList`` come through ``_list``.
#
# These columns are folded into one JSON bucket rather than named in ``scalars``,
# so without this declaration neither the trigger-vs-resync guard nor the
# divergence metric can see them — which is how ``artists.lastfm_playcount`` went
# unmirrored, and how the entire album/track payload was being dropped
# (docs §50.4.4.3).
class _AsList(str):
    """Marks a legacy column whose value is a list, not a scalar."""


_ENRICHMENT_PAYLOAD: Dict[str, Dict[str, Dict[str, str]]] = {
    "artist": {
        "lastfm": {
            "bio": "lastfm_bio", "listeners": "lastfm_listeners",
            "playcount": "lastfm_playcount", "tags": _AsList("lastfm_tags"),
            "similar": _AsList("lastfm_similar"), "url": "lastfm_url",
        },
        "genius": {
            "description": "genius_description",
            "alt_names": _AsList("genius_alt_names"), "url": "genius_url",
        },
        "discogs": {
            "bio": "discogs_bio", "members": _AsList("discogs_members"),
            "urls": _AsList("discogs_urls"),
        },
    },
    "album": {
        "lastfm": {
            "listeners": "lastfm_listeners", "playcount": "lastfm_playcount",
            "tags": _AsList("lastfm_tags"), "wiki": "lastfm_wiki",
        },
        "discogs": {
            "genres": _AsList("discogs_genres"), "styles": _AsList("discogs_styles"),
            "label": "discogs_label", "catno": "discogs_catno",
            "country": "discogs_country", "rating": "discogs_rating",
            "rating_count": "discogs_rating_count",
        },
        "bandcamp": {
            "tags": _AsList("bandcamp_tags"), "label": "bandcamp_label",
        },
    },
    "track": {
        "lastfm": {
            "listeners": "lastfm_listeners", "playcount": "lastfm_playcount",
            "tags": _AsList("lastfm_tags"),
        },
        "genius": {"description": "genius_description", "url": "genius_url"},
        "bandcamp": {
            "tags": _AsList("bandcamp_tags"), "label": "bandcamp_label",
        },
    },
}


# Services whose producer already writes lib2 directly (docs §32.3.1 stage 2).
#
# They leave the mirror in the same change that moves their worker, and that is
# not tidiness — it is required. Promise 1 is that the mirror runs one way only,
# which is safe exactly while legacy is the sole writer of those fields. The
# moment a worker writes lib2, a stale legacy value would be pushed back over the
# fresh native one on the next drain, and the divergence metric would report the
# worker's own correct output as a defect.
#
# This set is therefore the stage-2 progress marker: it grows by one entry per
# migrated worker, and when it holds every service the mirror has no work left.
MIGRATED_SERVICES: frozenset = frozenset({
    "lastfm", "genius", "discogs", "bandcamp", "audiodb", "similar_artists",
    "amazon", "jiosaavn", "musicbrainz", "spotify", "itunes", "qobuz", "tidal",
    "deezer",
})


def _migrated(service: str) -> bool:
    return str(service).strip().lower() in MIGRATED_SERVICES


def _migrated_prefixes() -> tuple:
    return tuple(f"{service}_" for service in MIGRATED_SERVICES)


# Shared scalar columns whose only legacy writer is one particular service.
#
# Prefix matching finds ``genius_lyrics``. It cannot find these: ``artists.style``
# carries no service in its name, yet AudioDB's worker is the only thing that ever
# wrote it (``mood``, ``label`` and ``banner_url`` likewise; ``albums.label`` is
# deliberately absent because five other workers write that one). Without the
# declaration these columns would keep overwriting their own worker's fresh native
# value from a stale legacy row.
_SCALAR_OWNERS: Dict[str, Dict[str, str]] = {
    "artist": {"style": "audiodb", "mood": "audiodb", "label": "audiodb",
               "banner_url": "audiodb", "aliases": "musicbrainz"},
    "album": {"style": "audiodb", "mood": "audiodb"},
    "track": {"style": "audiodb", "mood": "audiodb"},
}


# The promoted id columns, and which service owns each. ``isrc`` is deliberately
# absent: it is provider-neutral and several sources write it.
_ID_COLUMN_OWNERS: Dict[str, str] = {
    "spotify_id": "spotify",
    "musicbrainz_id": "musicbrainz",
}


def id_column_handed_over(column: str) -> bool:
    """Whether a migrated worker owns this promoted id column.

    The third place the handover has to reach, after the scalars and the JSON
    payload. lib2 stores Spotify and MusicBrainz ids twice — a promoted column the
    read paths join on, plus ``external_ids`` — and mirroring the column outright
    after its worker moved would push a stale legacy id over the fresh native one.
    """
    owner = _ID_COLUMN_OWNERS.get(str(column))
    return bool(owner and _migrated(owner))


def _scalar_owner(entity_type: str, field: tuple) -> Optional[str]:
    """Which service owns one scalar field, by declaration or by column prefix."""
    declared = (_SCALAR_OWNERS.get(entity_type) or {}).get(str(field[0]))
    if declared:
        return declared
    legacy_column = str(field[1])
    for service in MIGRATED_SERVICES:
        if legacy_column.startswith(f"{service}_"):
            return service
    return None


def handed_over(spec: "MirrorSpec", field: tuple) -> bool:
    """Whether a migrated worker owns this scalar, so the mirror only backfills it.

    Dropping the field instead would close the stale-overwrite hazard and open a
    quieter one: a legacy value the lib2 twin never received, on a row the ledger
    already calls ``matched`` so the worker will not re-fetch it, would simply be
    gone. Backfill settles both — an empty lib2 field is still filled, a field the
    worker has written is never overwritten.
    """
    owner = _scalar_owner(spec.entity_type, field)
    return bool(owner and _migrated(owner))


def active_scalars(spec: "MirrorSpec") -> tuple:
    """Scalar fields where legacy is still authoritative and overwrites lib2."""
    return tuple(field for field in spec.scalars if not handed_over(spec, field))


def handover_scalars(spec: "MirrorSpec") -> tuple:
    """Scalar fields a migrated worker owns — mirrored into an empty field only."""
    return tuple(field for field in spec.scalars if handed_over(spec, field))


def enrichment_columns(entity_type: str) -> Tuple[str, ...]:
    """Every legacy column the enrichment bucket reads for one entity type.

    Includes migrated services: their payload still crosses, backfill-only, so the
    trigger has to keep watching the columns and the audit has to keep seeing them.
    """
    return tuple(sorted({
        str(column)
        for _source, sources in (_ENRICHMENT_PAYLOAD.get(entity_type) or {}).items()
        for column in sources.values()
    }))


def _enrichment_payload(entity_type: str, legacy_row: Any) -> Dict[str, Dict[str, Any]]:
    """Provider payload keyed by source — the ``bios`` Nezreka named, and the
    album/track equivalent.

    This lives in an ``enrichment`` JSON column rather than in table columns
    because a Last.fm wiki and a Discogs catalogue number are different data,
    not one field from two sources (same reasoning as
    ``importer._artist_enrichment_payload``). A provider that wrote nothing
    leaves no empty bucket behind.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for source, fields in (_ENRICHMENT_PAYLOAD.get(entity_type) or {}).items():
        cleaned = {}
        for key, column in fields.items():
            raw = _row_get(legacy_row, str(column))
            value = _list(raw) if isinstance(column, _AsList) else raw
            if value not in (None, "", []):
                cleaned[key] = value
        if cleaned:
            out[source] = cleaned
    return out


@dataclass(frozen=True)
class MirrorSpec:
    """Everything one entity type mirrors, declared once.

    The writer below and the divergence check in
    ``core.library2.integrity_reconciler`` both read this. Two hand-kept lists
    would drift the moment a field is added, and the audit is only worth
    anything while it covers exactly what the mirror copies (iss32-T01a).
    """

    entity_type: str
    lib2_table: str
    legacy_table: str
    link_column: str
    # (lib2 column, legacy column, transform applied to a truthy legacy value)
    scalars: Tuple[Tuple[str, str, Optional[Callable[[Any], Any]]], ...]
    # lib2 column ← first non-empty of these legacy columns
    id_columns: Tuple[Tuple[str, Tuple[str, ...]], ...]
    # JSON object column ← builder(legacy_row), merged key by key
    json_columns: Tuple[Tuple[str, Callable[[Any], Dict[str, Any]]], ...]


MIRROR_SPECS: Dict[str, MirrorSpec] = {
    "artist": MirrorSpec(
        entity_type="artist",
        lib2_table="lib2_artists",
        legacy_table="artists",
        link_column="legacy_artist_id",
        scalars=(
            ("image_url", "thumb_url", None),
            ("genres", "genres", _normalize_genres),
            ("summary", "summary", None),
            ("style", "style", None),
            ("mood", "mood", None),
            ("label", "label", None),
            ("aliases", "aliases", _normalize_genres),
            ("banner_url", "banner_url", None),
        ),
        id_columns=(
            ("spotify_id", ("spotify_artist_id",)),
            ("musicbrainz_id", ("musicbrainz_id",)),
        ),
        # iss32-E01: the review asks for "artwork, genres, bios, provider ids".
        # The scalars cover artwork and genres; these two are the half that was
        # missing entirely — a mirrored artist kept lib2's stale provider ids
        # and never received a Last.fm/Genius/Discogs bio at all.
        json_columns=(
            ("external_ids", lambda row: _provider_ids(row, "artist")),
            ("enrichment", lambda row: _enrichment_payload("artist", row)),
        ),
    ),
    "album": MirrorSpec(
        entity_type="album",
        lib2_table="lib2_albums",
        legacy_table="albums",
        link_column="legacy_album_id",
        scalars=(
            ("image_url", "thumb_url", None),
            ("genres", "genres", _normalize_genres),
            ("label", "label", None),
            ("explicit", "explicit", None),
            ("upc", "upc", None),
            ("style", "style", None),
            ("mood", "mood", None),
            ("release_date", "release_date", None),
        ),
        id_columns=(
            ("spotify_id", ("spotify_album_id",)),
            ("musicbrainz_id", ("musicbrainz_release_id",)),
        ),
        json_columns=(
            ("external_ids", lambda row: _provider_ids(row, "album")),
            ("enrichment", lambda row: _enrichment_payload("album", row)),
        ),
    ),
    "track": MirrorSpec(
        entity_type="track",
        lib2_table="lib2_tracks",
        legacy_table="tracks",
        link_column="legacy_track_id",
        scalars=(
            ("bpm", "bpm", None),
            ("explicit", "explicit", None),
            ("genius_lyrics", "genius_lyrics", None),
            ("copyright", "copyright", None),
            ("style", "style", None),
            ("mood", "mood", None),
            ("disc_number", "disc_number", None),
        ),
        id_columns=(
            ("spotify_id", ("spotify_track_id",)),
            ("musicbrainz_id", ("musicbrainz_recording_id",)),
            ("isrc", ("isrc",)),
        ),
        json_columns=(
            ("external_ids", lambda row: _provider_ids(row, "track")),
            ("enrichment", lambda row: _enrichment_payload("track", row)),
        ),
    ),
}


def _scalar_value(legacy_row: Any, field: tuple) -> Optional[Any]:
    """What the mirror would write for one scalar field, or None for "leave
    the lib2 value alone" — which is what the ``COALESCE`` below encodes."""
    _lib2_column, legacy_column, transform = field
    value = _row_get(legacy_row, legacy_column)
    if transform is None:
        return value
    return transform(value) if value else None


def _id_value(legacy_row: Any, legacy_columns: Tuple[str, ...]) -> Optional[str]:
    for column in legacy_columns:
        value = _row_get(legacy_row, column)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _apply_mirror(conn, spec: MirrorSpec, entity_id: int, legacy_row: Any) -> bool:
    # Two assignment forms, and the argument order matters: legacy wins where it is
    # still the only writer (COALESCE(?, col)), lib2 wins where the worker has
    # moved and legacy may only fill a gap (COALESCE(col, ?)).
    fields = (*active_scalars(spec), *handover_scalars(spec))
    assignments = ", ".join(
        f"{field[0]}=COALESCE(?, {field[0]})" for field in active_scalars(spec))
    # NULLIF twice over: a JSON-array column defaults to '[]', never NULL, so
    # treating only NULL as empty would mean the backfill never fires for `aliases`
    # or `genres` and the legacy value stays unreachable. Same emptiness rule as
    # `_is_absent` and `provider_writes._is_empty`. The trailing column reference is
    # what keeps an empty-on-both-sides NOT NULL column legal — without it the
    # expression collapses to NULL.
    handover = ", ".join(
        f"{field[0]}=COALESCE(NULLIF(NULLIF({field[0]},''),'[]'), ?, {field[0]})"
        for field in handover_scalars(spec))
    conn.execute(
        f"UPDATE {spec.lib2_table} SET "
        + ", ".join(part for part in (assignments, handover) if part)
        + ", updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (*(_scalar_value(legacy_row, field) for field in fields), entity_id),
    )
    for column, builder in spec.json_columns:
        _merge_json_column(conn, spec.lib2_table, entity_id, column,
                           builder(legacy_row))
    _sync_dedicated_id_columns(conn, spec.lib2_table, entity_id, legacy_row,
                               dict(spec.id_columns))
    return True


def resync_artist_from_legacy(conn, lib2_artist_id: int, legacy_row: Any) -> bool:
    return _apply_mirror(conn, MIRROR_SPECS["artist"], lib2_artist_id, legacy_row)


def _sync_dedicated_id_columns(conn, table: str, entity_id: int, legacy_row: Any,
                               mapping: Dict[str, tuple]) -> None:
    """Keep the promoted id columns in step with ``external_ids``.

    ``lib2_*`` stores Spotify/MusicBrainz twice: once as a first-class column
    (indexed, joined on) and once inside ``external_ids``. Mirroring only the
    JSON would leave the columns the read paths actually use behind.
    """
    for column, legacy_columns in mapping.items():
        value = _id_value(legacy_row, tuple(legacy_columns))
        if value is None:
            continue
        if id_column_handed_over(column):
            # Backfill only: its worker writes this column now.
            conn.execute(
                f"UPDATE {table} SET {column}=? WHERE id=? "
                f"AND COALESCE({column},'')=''", (value, entity_id))
            continue
        conn.execute(
            f"UPDATE {table} SET {column}=? WHERE id=? AND COALESCE({column},'')<>?",
            (value, entity_id, value))


def resync_album_from_legacy(conn, lib2_album_id: int, legacy_row: Any) -> bool:
    return _apply_mirror(conn, MIRROR_SPECS["album"], lib2_album_id, legacy_row)


def resync_track_from_legacy(conn, lib2_track_id: int, legacy_row: Any) -> bool:
    return _apply_mirror(conn, MIRROR_SPECS["track"], lib2_track_id, legacy_row)


def resync_entity_from_legacy(conn, entity_type: str, lib2_id: int, legacy_id: Any) -> bool:
    """Re-read the legacy row and overwrite the lib2 row's provider fields.

    Returns False (no-op) if the legacy row is gone or ``entity_type`` is
    unrecognized — the caller's enrichment result is unaffected either way.
    """
    spec = MIRROR_SPECS.get(entity_type)
    if spec is None:
        return False
    row = conn.execute(
        f"SELECT * FROM {spec.legacy_table} WHERE id=?", (legacy_id,)).fetchone()
    if row is None:
        return False
    return _apply_mirror(conn, spec, lib2_id, row)


def _same_value(expected: Any, actual: Any) -> bool:
    """Compare a mirrored value with what the lib2 row carries.

    Deliberately tolerant about representation, not about content: a REAL
    ``120.0`` read back through a TEXT column, or an int that survived a JSON
    round trip, is the same value. Anything stricter would report the whole
    library; anything looser would hide the disagreement being measured.
    """
    if actual is None:
        return expected is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return float(expected) == float(actual)
        except (TypeError, ValueError):
            return False
    return str(expected).strip() == str(actual).strip()


def _is_absent(value: Any) -> bool:
    """Whether a value carries nothing a mirror could meaningfully hand over.

    An empty legacy column, an empty JSON array and NULL are the same absence.
    Reporting ``'' vs NULL`` as a disagreement would put rows into the metric
    that nobody can act on and that no drain would ever clear, which costs it
    the property that makes it worth having: a non-zero value means a bug.
    """
    if value is None:
        return True
    return str(value).strip() in ("", "[]", "{}")


def _json_divergence(column: str, incoming: Dict[str, Any],
                     raw: Any) -> Dict[str, Dict[str, Any]]:
    try:
        current = json.loads(raw or "{}")
        if not isinstance(current, dict):
            current = {}
    except (TypeError, ValueError):
        current = {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in incoming.items():
        # A migrated service's key is only mirrored into a gap, so a difference
        # where lib2 already has a value is the intended outcome, not a defect.
        backfill_only = _migrated(key)
        if isinstance(value, dict):
            bucket = current.get(key)
            bucket = bucket if isinstance(bucket, dict) else {}
            for sub_key, sub_value in value.items():
                if backfill_only and not _is_absent(bucket.get(sub_key)):
                    continue
                if not _same_value(sub_value, bucket.get(sub_key)):
                    out[f"{column}.{key}.{sub_key}"] = {
                        "legacy": sub_value, "lib2": bucket.get(sub_key)}
        elif backfill_only and not _is_absent(current.get(key)):
            continue
        elif not _same_value(value, current.get(key)):
            out[f"{column}.{key}"] = {"legacy": value, "lib2": current.get(key)}
    return out


def mirror_divergence(spec: MirrorSpec, legacy_row: Any,
                      lib2_row: Any) -> Dict[str, Dict[str, Any]]:
    """Fields where the lib2 row does not carry what the mirror would write.

    Only fields the mirror actually copies are compared, and only where the
    legacy side has something to give: the mirror is ``COALESCE``-based, so a
    legacy NULL means "leave lib2 alone" by contract, not "lib2 is wrong".
    An empty result is the expected state (docs §32.3.1, promise 2).
    """
    out: Dict[str, Dict[str, Any]] = {}
    for field in (*active_scalars(spec), *handover_scalars(spec)):
        expected = _scalar_value(legacy_row, field)
        if _is_absent(expected):
            continue
        actual = _row_get(lib2_row, field[0])
        if handed_over(spec, field) and not _is_absent(actual):
            # Backfill-only: the mirror will not touch this, so reporting it would
            # put a row into the metric no sweep can ever clear.
            continue
        if not _same_value(expected, actual):
            out[field[0]] = {"legacy": expected, "lib2": actual}
    for column, legacy_columns in spec.id_columns:
        expected = _id_value(legacy_row, legacy_columns)
        if expected is None:
            continue
        actual = _row_get(lib2_row, column)
        if id_column_handed_over(column) and not _is_absent(actual):
            # Backfill-only, so a difference here is the intended outcome.
            continue
        if str(actual or "").strip() != expected:
            out[column] = {"legacy": expected, "lib2": actual}
    for column, builder in spec.json_columns:
        out.update(_json_divergence(
            column, builder(legacy_row), _row_get(lib2_row, column)))
    return out


@dataclass(frozen=True)
class MirrorObservation:
    """How one mirrored lib2 row stands against its legacy source row."""

    entity_type: str
    lib2_id: Any
    legacy_id: Any
    status: str  # "in_step" | "divergent" | "dangling" | "pending"
    fields: Dict[str, Dict[str, Any]]


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


def legacy_key(value: Any) -> Optional[str]:
    """One comparable form for a legacy row id.

    ``artists.id`` is a ``TEXT PRIMARY KEY`` holding a media-server ratingKey,
    while ``lib2_artists.legacy_artist_id`` is ``INTEGER``. SQLite matches the
    two through column affinity, so the SQL is right while a Python dict keyed
    on the raw value is not — measured against a real library, that difference
    turned every mirrored row into a false "legacy row missing" and left the
    divergence figure measuring nothing at all.
    """
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def iter_mirror_divergences(conn, *, batch_size: int = 500,
                            after: Optional[Dict[str, Any]] = None):
    """Yield one :class:`MirrorObservation` per mirrored lib2 row.

    Rows without a legacy link are natively created and have nothing to
    compare against; they are skipped rather than yielded. The walk is
    resumable through ``after`` (``{entity_type: last lib2 id}``) and bounded
    by whatever the caller takes from the generator, so a sweep can stay
    inside a time budget without a second implementation of the join.
    """
    cursors = dict(after or {})
    has_queue = _table_exists(conn, "lib2_legacy_dirty")
    for entity_type, spec in MIRROR_SPECS.items():
        if not (_table_exists(conn, spec.lib2_table)
                and _table_exists(conn, spec.legacy_table)):
            continue
        pending = set()
        if has_queue:
            pending = {
                legacy_key(row[0]) for row in conn.execute(
                    "SELECT legacy_id FROM lib2_legacy_dirty WHERE entity_type=?",
                    (entity_type,))
            }
        position = cursors.get(entity_type, -1)
        while True:
            # Keyset paging, not OFFSET: this walks a table that grows to one
            # row per track, and OFFSET re-scans everything it skipped.
            rows = [dict(row) for row in conn.execute(
                f"SELECT * FROM {spec.lib2_table} "
                f"WHERE {spec.link_column} IS NOT NULL AND id>? ORDER BY id LIMIT ?",
                (position, int(batch_size)))]
            if not rows:
                break
            position = rows[-1]["id"]
            wanted = {
                row[spec.link_column] for row in rows
                if legacy_key(row[spec.link_column]) not in pending
            }
            legacy_rows: Dict[Optional[str], Any] = {}
            if wanted:
                holes = ",".join("?" for _ in wanted)
                legacy_rows = {
                    legacy_key(row["id"]): row for row in conn.execute(
                        f"SELECT * FROM {spec.legacy_table} WHERE id IN ({holes})",
                        tuple(wanted))
                }
            for row in rows:
                legacy_id = row[spec.link_column]
                key = legacy_key(legacy_id)
                if key in pending:
                    # Queued by the trigger and not yet drained: work in
                    # flight, not a defect.
                    yield MirrorObservation(
                        entity_type, row["id"], legacy_id, "pending", {})
                    continue
                legacy_row = legacy_rows.get(key)
                if legacy_row is None:
                    yield MirrorObservation(
                        entity_type, row["id"], legacy_id, "dangling", {})
                    continue
                fields = mirror_divergence(spec, legacy_row, row)
                yield MirrorObservation(
                    entity_type, row["id"], legacy_id,
                    "divergent" if fields else "in_step", fields)


def mirror_check_ran(conn) -> bool:
    """Whether any entity type still has both of its tables to compare."""
    return any(
        _table_exists(conn, spec.lib2_table) and _table_exists(conn, spec.legacy_table)
        for spec in MIRROR_SPECS.values()
    )


__all__ = [
    "MIGRATED_SERVICES",
    "active_scalars",
    "handed_over",
    "handover_scalars",
    "id_column_handed_over",
    "MIRROR_SPECS",
    "MirrorObservation",
    "MirrorSpec",
    "iter_mirror_divergences",
    "legacy_key",
    "mirror_check_ran",
    "mirror_divergence",
    "resync_artist_from_legacy",
    "resync_album_from_legacy",
    "resync_track_from_legacy",
    "resync_entity_from_legacy",
]
