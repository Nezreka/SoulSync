"""Field-level user metadata overrides and effective read projection.

Provider/import metadata remains in the existing entity columns.  Admin user
corrections live separately in ``lib2_metadata_overrides`` and win only when a
read projection is built.  A later provider refresh can therefore correct its
own baseline without erasing user intent or mistaking a user value for
provider provenance (ADR-06).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


LIB2_METADATA_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS lib2_metadata_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, field_name),
    CHECK(entity_type IN (
        'artist','release_group','track','release_edition','recording'
    )),
    CHECK(entity_id > 0),
    CHECK(profile_id = 1)
)
"""


_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_lib2_metadata_overrides_entity "
    "ON lib2_metadata_overrides(entity_type, entity_id)",
)

_ENTITY_TABLES = {
    "artist": "lib2_artists",
    "release_group": "lib2_albums",
    "track": "lib2_tracks",
    "release_edition": "lib2_release_editions",
    "recording": "lib2_recordings",
}

# field -> validation kind. Operational state (monitoring, quality profiles,
# file paths, source ids) is intentionally absent: this store is metadata only.
_FIELD_SPECS = {
    "artist": {
        "name": "required_text",
        "sort_name": "text",
        "image_url": "text",
        "genres": "string_list",
        "summary": "long_text",
    },
    "release_group": {
        "title": "required_text",
        "album_type": "album_type",
        "secondary_types": "string_list",
        "release_date": "text",
        "year": "year",
        "image_url": "text",
        "genres": "string_list",
    },
    "track": {
        "title": "required_text",
        "track_number": "nonnegative_int",
        "disc_number": "nonnegative_int",
        "duration": "nonnegative_int",
    },
    "release_edition": {
        "title": "text",
        "disambiguation": "text",
        "country": "text",
        "label": "text",
        "barcode": "text",
        "status": "text",
        "media": "json_list",
        "disc_count": "nonnegative_int",
        "release_date": "text",
        "track_count": "nonnegative_int",
        "duration": "nonnegative_int",
    },
    "recording": {
        "title": "required_text",
        "duration": "nonnegative_int",
    },
}

_ALBUM_TYPES = frozenset({"album", "single", "ep", "compilation", "live"})


class MetadataOverrideError(ValueError):
    """Validated user-facing override failure with an HTTP-ish status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class MetadataOverride:
    id: int
    entity_type: str
    entity_id: int
    field_name: str
    value: Any
    profile_id: int
    reason: Optional[str]
    created_at: str
    updated_at: str


def ensure_metadata_overrides_schema(cursor: Any) -> None:
    """Create the override store and entity-delete cleanup triggers."""
    cursor.execute(LIB2_METADATA_OVERRIDES_DDL)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)
    for entity_type, table in _ENTITY_TABLES.items():
        exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        trigger = f"trg_{table}_metadata_overrides_delete"
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        cursor.execute(f"""
            CREATE TRIGGER {trigger}
            AFTER DELETE ON {table}
            FOR EACH ROW
            BEGIN
                DELETE FROM lib2_metadata_overrides
                 WHERE entity_type='{entity_type}' AND entity_id=OLD.id;
            END
        """)


def _entity_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _ENTITY_TABLES:
        raise MetadataOverrideError(
            f"unsupported metadata override entity_type: {normalized!r}"
        )
    return normalized


def _field(entity_type: str, value: Any) -> tuple[str, str]:
    normalized = str(value or "").strip()
    spec = _FIELD_SPECS[entity_type].get(normalized)
    if spec is None:
        raise MetadataOverrideError(
            f"field {normalized!r} cannot be overridden for {entity_type}"
        )
    return normalized, spec


def _validated_value(spec: str, value: Any) -> Any:
    if value is None:
        if spec in {"required_text", "album_type"}:
            raise MetadataOverrideError("this metadata field cannot be null")
        return None
    if spec in {"text", "required_text", "long_text"}:
        if not isinstance(value, str):
            raise MetadataOverrideError("metadata override must be text")
        value = value.strip()
        if spec == "required_text" and not value:
            raise MetadataOverrideError("metadata override cannot be empty")
        limit = 10_000 if spec == "long_text" else 2_000
        if len(value) > limit:
            raise MetadataOverrideError(
                f"metadata override exceeds {limit} characters"
            )
        return value
    if spec == "album_type":
        normalized = str(value or "").strip().lower()
        if normalized not in _ALBUM_TYPES:
            raise MetadataOverrideError(
                "album_type must be one of " + "|".join(sorted(_ALBUM_TYPES))
            )
        return normalized
    if spec in {"year", "nonnegative_int"}:
        if isinstance(value, bool):
            raise MetadataOverrideError("metadata override must be an integer")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise MetadataOverrideError(
                "metadata override must be an integer"
            ) from exc
        if spec == "year" and not 0 <= normalized <= 9999:
            raise MetadataOverrideError("year must be between 0 and 9999")
        if spec == "nonnegative_int" and normalized < 0:
            raise MetadataOverrideError("metadata override cannot be negative")
        return normalized
    if spec == "string_list":
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise MetadataOverrideError("metadata override must be a list of strings")
        return [item.strip() for item in value if item.strip()]
    if spec == "json_list":
        if not isinstance(value, list):
            raise MetadataOverrideError("metadata override must be a JSON list")
        return value
    raise MetadataOverrideError("unsupported metadata override value")


def _row_to_override(row: Any) -> MetadataOverride:
    return MetadataOverride(
        id=int(row["id"]),
        entity_type=str(row["entity_type"]),
        entity_id=int(row["entity_id"]),
        field_name=str(row["field_name"]),
        value=json.loads(row["value_json"]),
        profile_id=int(row["profile_id"]),
        reason=row["reason"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def set_field_override(
    conn: Any,
    *,
    entity_type: str,
    entity_id: int,
    field_name: str,
    value: Any,
    profile_id: int = 1,
    reason: Optional[str] = None,
) -> MetadataOverride:
    """Upsert one admin override without committing the caller's transaction."""
    ensure_metadata_overrides_schema(conn.cursor())
    entity_type = _entity_type(entity_type)
    field_name, spec = _field(entity_type, field_name)
    entity_id = int(entity_id)
    if entity_id <= 0:
        raise MetadataOverrideError("entity_id must be positive")
    if int(profile_id) != 1:
        raise MetadataOverrideError("metadata overrides are admin-only", status=403)
    if conn.execute(
        f"SELECT 1 FROM {_ENTITY_TABLES[entity_type]} WHERE id=?", (entity_id,)
    ).fetchone() is None:
        raise MetadataOverrideError("metadata entity not found", status=404)
    value = _validated_value(spec, value)
    try:
        value_json = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MetadataOverrideError(
            "metadata override must be valid JSON"
        ) from exc
    reason = str(reason or "").strip()[:500] or None
    conn.execute(
        """INSERT INTO lib2_metadata_overrides(
               entity_type, entity_id, field_name, value_json, profile_id, reason)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(entity_type, entity_id, field_name) DO UPDATE SET
               value_json=excluded.value_json,
               profile_id=excluded.profile_id,
               reason=excluded.reason,
               updated_at=CURRENT_TIMESTAMP""",
        (entity_type, entity_id, field_name, value_json, 1, reason),
    )
    row = conn.execute(
        """SELECT * FROM lib2_metadata_overrides
            WHERE entity_type=? AND entity_id=? AND field_name=?""",
        (entity_type, entity_id, field_name),
    ).fetchone()
    return _row_to_override(row)


def clear_field_override(
    conn: Any,
    *,
    entity_type: str,
    entity_id: int,
    field_name: str,
    profile_id: int = 1,
) -> bool:
    """Remove one override so the current provider baseline becomes visible."""
    entity_type = _entity_type(entity_type)
    field_name, _spec = _field(entity_type, field_name)
    if int(profile_id) != 1:
        raise MetadataOverrideError("metadata overrides are admin-only", status=403)
    result = conn.execute(
        """DELETE FROM lib2_metadata_overrides
            WHERE entity_type=? AND entity_id=? AND field_name=?""",
        (entity_type, int(entity_id), field_name),
    )
    return bool(result.rowcount)


def get_field_overrides(
    conn: Any, *, entity_type: str, entity_id: int
) -> Dict[str, MetadataOverride]:
    entity_type = _entity_type(entity_type)
    rows = conn.execute(
        """SELECT * FROM lib2_metadata_overrides
            WHERE entity_type=? AND entity_id=? ORDER BY field_name""",
        (entity_type, int(entity_id)),
    ).fetchall()
    return {row["field_name"]: _row_to_override(row) for row in rows}


def project_metadata(
    conn: Any,
    *,
    entity_type: str,
    entity_id: int,
    provider_fields: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Overlay user values on provider fields and return (effective, overrides)."""
    effective = dict(provider_fields)
    overrides = get_field_overrides(
        conn, entity_type=entity_type, entity_id=entity_id
    )
    values = {field: override.value for field, override in overrides.items()}
    effective.update(values)
    return effective, values


__all__ = [
    "LIB2_METADATA_OVERRIDES_DDL",
    "MetadataOverride",
    "MetadataOverrideError",
    "clear_field_override",
    "ensure_metadata_overrides_schema",
    "get_field_overrides",
    "project_metadata",
    "set_field_override",
]
