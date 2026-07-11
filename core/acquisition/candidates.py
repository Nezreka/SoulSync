"""Durable server-side release candidates for acquisition requests."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from core.acquisition.capabilities import require_source_capabilities
from core.acquisition.requests import get_request


CANDIDATE_ID_PREFIX = "arc1-"
DEFAULT_TTL_SECONDS = 6 * 60 * 60
CONTENT_SCOPES = frozenset({"recording", "release_bundle"})

RELEASE_CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS release_candidates (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    source TEXT NOT NULL,
    protocol TEXT NOT NULL,
    content_scope TEXT NOT NULL,
    indexer TEXT,
    guid TEXT,
    title TEXT NOT NULL,
    size_bytes INTEGER,
    age_seconds INTEGER,
    grabs INTEGER,
    seeders INTEGER,
    server_ref TEXT NOT NULL,
    facts_json TEXT NOT NULL DEFAULT '{}',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(request_id, dedupe_key),
    FOREIGN KEY (request_id) REFERENCES acquisition_requests(id) ON DELETE CASCADE,
    CHECK(content_scope IN ('recording','release_bundle')),
    CHECK(size_bytes IS NULL OR size_bytes >= 0),
    CHECK(age_seconds IS NULL OR age_seconds >= 0),
    CHECK(grabs IS NULL OR grabs >= 0),
    CHECK(seeders IS NULL OR seeders >= 0)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_release_candidates_request "
    "ON release_candidates(request_id, expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_release_candidates_guid "
    "ON release_candidates(source, indexer, guid)",
    "CREATE INDEX IF NOT EXISTS idx_release_candidates_expiry "
    "ON release_candidates(expires_at)",
)

_COLUMNS = (
    "id", "request_id", "source", "protocol", "content_scope", "indexer",
    "guid", "title", "size_bytes", "age_seconds", "grabs", "seeders",
    "server_ref", "facts_json", "raw_payload_json", "dedupe_key",
    "expires_at", "created_at", "updated_at",
)

_SECRET_KEYS = re.compile(
    r"(^|_)(api_?key|token|secret|password|download_?url|url|magnet)(_|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CandidateFacts:
    artist: Optional[str] = None
    release_title: Optional[str] = None
    edition: Optional[str] = None
    year: Optional[int] = None
    format: Optional[str] = None
    bitrate: Optional[int] = None
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
    track_count: Optional[int] = None
    language: Optional[str] = None
    release_type: Optional[str] = None
    custom_formats: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> "CandidateFacts":
        value = dict(raw or {})

        def optional_text(name: str) -> Optional[str]:
            text = str(value.get(name) or "").strip()
            return text or None

        def optional_int(name: str) -> Optional[int]:
            item = value.get(name)
            if item in (None, ""):
                return None
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None

        formats = value.get("custom_formats") or []
        if isinstance(formats, str):
            formats = [formats]
        return cls(
            artist=optional_text("artist"),
            release_title=optional_text("release_title"),
            edition=optional_text("edition"),
            year=optional_int("year"),
            format=optional_text("format"),
            bitrate=optional_int("bitrate"),
            sample_rate=optional_int("sample_rate"),
            bit_depth=optional_int("bit_depth"),
            track_count=optional_int("track_count"),
            language=optional_text("language"),
            release_type=optional_text("release_type"),
            custom_formats=tuple(
                str(item).strip() for item in formats if str(item).strip()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artist": self.artist,
            "release_title": self.release_title,
            "edition": self.edition,
            "year": self.year,
            "format": self.format,
            "bitrate": self.bitrate,
            "sample_rate": self.sample_rate,
            "bit_depth": self.bit_depth,
            "track_count": self.track_count,
            "language": self.language,
            "release_type": self.release_type,
            "custom_formats": list(self.custom_formats),
        }


@dataclass(frozen=True)
class ReleaseCandidate:
    id: str
    request_id: str
    source: str
    protocol: str
    content_scope: str
    indexer: Optional[str]
    guid: Optional[str]
    title: str
    size_bytes: Optional[int]
    age_seconds: Optional[int]
    grabs: Optional[int]
    seeders: Optional[int]
    server_ref: str
    facts: CandidateFacts
    raw_payload: Dict[str, Any]
    dedupe_key: str
    expires_at: float
    created_at: str
    updated_at: str

    def to_public_dict(self) -> Dict[str, Any]:
        """Browser-safe representation: no URL/token/raw provider payload."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "source": self.source,
            "protocol": self.protocol,
            "content_scope": self.content_scope,
            "indexer": self.indexer,
            "guid": self.guid,
            "title": self.title,
            "size_bytes": self.size_bytes,
            "age_seconds": self.age_seconds,
            "grabs": self.grabs,
            "seeders": self.seeders,
            "facts": self.facts.to_dict(),
            "expires_at": self.expires_at,
        }


def ensure_release_candidates_schema(conn: Any) -> None:
    cursor = conn.cursor()
    cursor.execute(RELEASE_CANDIDATES_DDL)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if _SECRET_KEYS.search(str(key))
            else _redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str) and (
        "://" in value or value.strip().lower().startswith("magnet:")
    ):
        return "[redacted]"
    return value


def _optional_nonnegative_int(value: Any, name: str) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def candidate_dedupe_key(
    *, source: str, protocol: str, content_scope: str, indexer: Optional[str],
    guid: Optional[str], title: str, size_bytes: Optional[int], server_ref: str,
) -> str:
    if guid:
        identity = ("guid", _normalize(source), _normalize(indexer), _normalize(guid))
    else:
        identity = (
            "fallback", _normalize(source), _normalize(protocol), content_scope,
            _normalize(title), str(size_bytes or 0), server_ref,
        )
    return hashlib.sha256("\x1f".join(identity).encode("utf-8")).hexdigest()


def _row_mapping(cursor: Any, row: Any) -> Dict[str, Any]:
    if hasattr(row, "keys"):
        return dict(row)
    return {
        column[0]: value
        for column, value in zip(cursor.description, row, strict=True)
    }


def _decode_object(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _from_row(cursor: Any, row: Any) -> ReleaseCandidate:
    data = _row_mapping(cursor, row)
    return ReleaseCandidate(
        id=str(data["id"]),
        request_id=str(data["request_id"]),
        source=str(data["source"]),
        protocol=str(data["protocol"]),
        content_scope=str(data["content_scope"]),
        indexer=data["indexer"],
        guid=data["guid"],
        title=str(data["title"]),
        size_bytes=data["size_bytes"],
        age_seconds=data["age_seconds"],
        grabs=data["grabs"],
        seeders=data["seeders"],
        server_ref=str(data["server_ref"]),
        facts=CandidateFacts.from_mapping(_decode_object(data["facts_json"])),
        raw_payload=_decode_object(data["raw_payload_json"]),
        dedupe_key=str(data["dedupe_key"]),
        expires_at=float(data["expires_at"]),
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
    )


def get_candidate(conn: Any, candidate_id: str) -> Optional[ReleaseCandidate]:
    cursor = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM release_candidates WHERE id=?",
        (str(candidate_id),),
    )
    row = cursor.fetchone()
    return _from_row(cursor, row) if row is not None else None


def resolve_candidate(
    conn: Any,
    candidate_id: str,
    *,
    request_id: str,
    profile_id: int,
    now: Optional[float] = None,
) -> Optional[ReleaseCandidate]:
    candidate = get_candidate(conn, candidate_id)
    if candidate is None or candidate.request_id != str(request_id):
        return None
    request = get_request(conn, candidate.request_id)
    if request is None or request.profile_id != int(profile_id):
        return None
    if candidate.expires_at <= (time.time() if now is None else float(now)):
        return None
    return candidate


def register_candidate(
    conn: Any,
    *,
    request_id: str,
    source: str,
    protocol: str,
    content_scope: str,
    server_ref: str,
    title: str,
    indexer: Optional[str] = None,
    guid: Optional[str] = None,
    size_bytes: Optional[int] = None,
    age_seconds: Optional[int] = None,
    grabs: Optional[int] = None,
    seeders: Optional[int] = None,
    facts: Optional[Mapping[str, Any]] = None,
    raw_payload: Optional[Mapping[str, Any]] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> Tuple[ReleaseCandidate, bool]:
    """Persist a normalized candidate; returns ``(candidate, created)``."""
    ensure_release_candidates_schema(conn)
    request = get_request(conn, request_id)
    if request is None:
        raise ValueError("candidate request does not exist")
    if request.status not in {"searching", "candidates_ready"}:
        raise ValueError(f"cannot register candidate while request is {request.status}")
    source = str(source or "").strip().lower()
    capabilities = require_source_capabilities(source)
    content_scope = str(content_scope or "").strip().lower()
    if content_scope not in CONTENT_SCOPES:
        raise ValueError(f"invalid candidate content_scope: {content_scope!r}")
    if content_scope != capabilities.content_scope:
        raise ValueError(
            f"source {source} declares {capabilities.content_scope}, not {content_scope}")
    protocol = str(protocol or "").strip().lower()
    title = str(title or "").strip()
    server_ref = str(server_ref or "").strip()
    if not protocol or not title or not server_ref:
        raise ValueError("candidate protocol, title and server_ref are required")
    if "://" in server_ref or server_ref.lower().startswith("magnet:"):
        raise ValueError("candidate server_ref must be opaque, never a URL")
    ttl_seconds = int(ttl_seconds)
    if ttl_seconds <= 0 or ttl_seconds > 24 * 60 * 60:
        raise ValueError("candidate ttl_seconds must be between 1 and 86400")
    size_bytes = _optional_nonnegative_int(size_bytes, "size_bytes")
    age_seconds = _optional_nonnegative_int(age_seconds, "age_seconds")
    grabs = _optional_nonnegative_int(grabs, "grabs")
    seeders = _optional_nonnegative_int(seeders, "seeders")
    indexer = str(indexer).strip() if indexer not in (None, "") else None
    guid = str(guid).strip() if guid not in (None, "") else None
    typed_facts = CandidateFacts.from_mapping(facts)
    redacted_payload = _redact_payload(dict(raw_payload or {}))
    dedupe_key = candidate_dedupe_key(
        source=source,
        protocol=protocol,
        content_scope=content_scope,
        indexer=indexer,
        guid=guid,
        title=title,
        size_bytes=size_bytes,
        server_ref=server_ref,
    )
    timestamp = time.time() if now is None else float(now)
    expires_at = timestamp + ttl_seconds
    existing_cursor = conn.execute(
        f"""SELECT {', '.join(_COLUMNS)} FROM release_candidates
             WHERE request_id=? AND dedupe_key=?""",
        (request.id, dedupe_key),
    )
    existing_row = existing_cursor.fetchone()
    candidate_id = (
        _from_row(existing_cursor, existing_row).id
        if existing_row is not None
        else CANDIDATE_ID_PREFIX + secrets.token_urlsafe(18)
    )
    conn.execute(
        """INSERT INTO release_candidates(
               id, request_id, source, protocol, content_scope, indexer, guid,
               title, size_bytes, age_seconds, grabs, seeders, server_ref,
               facts_json, raw_payload_json, dedupe_key, expires_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(request_id, dedupe_key) DO UPDATE SET
               title=excluded.title,
               size_bytes=excluded.size_bytes,
               age_seconds=excluded.age_seconds,
               grabs=excluded.grabs,
               seeders=excluded.seeders,
               server_ref=excluded.server_ref,
               facts_json=excluded.facts_json,
               raw_payload_json=excluded.raw_payload_json,
               expires_at=excluded.expires_at,
               updated_at=CURRENT_TIMESTAMP""",
        (
            candidate_id, request.id, source, protocol, content_scope, indexer,
            guid, title, size_bytes, age_seconds, grabs, seeders, server_ref,
            _json(typed_facts.to_dict()), _json(redacted_payload), dedupe_key,
            expires_at,
        ),
    )
    candidate = get_candidate(conn, candidate_id)
    if candidate is None:  # pragma: no cover - guarded by successful upsert
        raise RuntimeError("candidate upsert did not produce a row")
    return candidate, existing_row is None


def list_request_candidates(
    conn: Any, request_id: str, *, include_expired: bool = False,
    now: Optional[float] = None,
) -> list[ReleaseCandidate]:
    args: list[Any] = [str(request_id)]
    expiry_sql = ""
    if not include_expired:
        expiry_sql = " AND expires_at>?"
        args.append(time.time() if now is None else float(now))
    cursor = conn.execute(
        f"""SELECT {', '.join(_COLUMNS)} FROM release_candidates
             WHERE request_id=?{expiry_sql} ORDER BY id""",
        args,
    )
    return [_from_row(cursor, row) for row in cursor.fetchall()]


def prune_expired_candidates(conn: Any, *, now: Optional[float] = None) -> int:
    result = conn.execute(
        "DELETE FROM release_candidates WHERE expires_at<=?",
        (time.time() if now is None else float(now),),
    )
    return int(result.rowcount)


__all__ = [
    "CANDIDATE_ID_PREFIX",
    "CONTENT_SCOPES",
    "DEFAULT_TTL_SECONDS",
    "RELEASE_CANDIDATES_DDL",
    "CandidateFacts",
    "ReleaseCandidate",
    "candidate_dedupe_key",
    "ensure_release_candidates_schema",
    "get_candidate",
    "list_request_candidates",
    "prune_expired_candidates",
    "register_candidate",
    "resolve_candidate",
]
