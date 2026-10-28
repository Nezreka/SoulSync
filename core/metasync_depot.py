"""The MetaSync metadata depot — SoulSync's store for network answers.

Design: DESIGN-METADATA.md (MetaSync repo) §1, §2.4, §5, §8, §9, §13
step 8. The plain version: when SoulSync asks its MetaSync node about
an artist, album or track, the node merges what the network answered
into one record and hands SoulSync a COARSE PROJECTION of it (per
field: candidate values, a verification tier, a support BUCKET —
"none" / "1-2" / "3+", never an exact count, never an origin key).
That projection is what lives here, in the depot. Nothing that
arrives in the depot is written into library records on arrival; the
depot is a labelled candidate store, not a second library.

What the depot guarantees:

* **Per-field provenance.** Every candidate value is its own row,
  keyed by (subject, field, value) — the claim identity SoulSync is
  allowed to see. §8 keeps asserting node keys on the node, so the
  depot's identity omits the origin key on purpose; the origins
  behind a value are summarised by the node's tier + bucket.
* **Bounds like the node's cache (§6).** A field row dies 90 days
  after the depot last refreshed it from the node (the node's own
  aging runs from the origin's ``asserted_at``, capped at receipt;
  the projection carries no ``asserted_at``, so the depot's receipt
  time is the honest bound it has). A strong-verified tier lapses
  90 days after its ``verified_at`` (§1's depot timer): the value
  stays, the tier demotes, and §8 floor (a) stops passing until the
  node pushes a fresh verdict. Node-supplied timestamps are
  themselves clamped at receipt — a future ``verified_at`` can no
  more buy permanent freshness here than a future ``asserted_at``
  can on the node. Entries are capped; eviction drops the weakest,
  oldest candidates first.
* **Invalidation is a flag, never a rewrite.** The node pushes
  deaths (a revocation, an expiry, a superseding answer) to
  ``/api/v1/metasync/depot/*``; the depot flags the matching rows
  retracted and they stop promoting immediately. A value already
  promoted into a library record or a file's tags is NOT touched —
  corrections to written data are SoulSync's own workflows'
  business (§8). A reconcile pass re-reads every depot subject from
  the node and drops whatever the node's live view no longer holds:
  pushes are the fast path, the node's view is the truth.
* **Promotion happens only above §8's floor, at promotion time.**
  ``promotion_verdict()`` is the floor, and every promotion path in
  SoulSync (the tagging processor in core/metadata/source.py, and
  ``promote_field()`` for library records) goes through it. Peer
  agreement — the support bucket — never promotes anything by
  itself; counting is display-only, exactly as §9 says.
* **Promoted values are marked, forever (§2.4).** A promotion writes
  a row in ``metasync_network_marks`` keyed by (kind, soul_id,
  field, value). The MetaSync export emits those fields in each
  row's ``network_fields`` list, so the node's claim builder can
  emit the value with source ``network`` — zero support, never
  re-served as own-library data. Without the mark, one network
  answer promoted on three installs would re-enter as three
  independent assertions and launder itself into support. Fields
  fetched BY WAY OF a promoted ID (tagging steers a provider fetch
  with a depot-promoted ID and writes what comes back) inherit the
  mark with ``via='fetched_via_promoted_id'``.

The SoulSync-side identity approximation, stated plainly: the
projection deliberately hides origin keys, so the depot cannot tell
a ``network``-sourced claim from a lone claim whose asserters have
no standing — both project as a non-local value with support bucket
"none". The depot treats every such value as network-reflected:
promotable ONLY under floor leg (a), this node's own strong
verification. That fails closed, which is the direction §2.4 wants.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from utils.logging_config import get_logger

logger = get_logger("metasync_depot")

# -- tiers and buckets, the node's vocabulary (meta/merge.py) ---------------
TIER_STRONG = "strong-verified"
TIER_PLAUSIBLE = "plausible"
TIER_UNVERIFIED = "unverified"

BUCKET_NONE = "none"
BUCKET_SOME = "1-2"
BUCKET_FLOOR = "3+"

_KNOWN_TIERS = (TIER_STRONG, TIER_PLAUSIBLE, TIER_UNVERIFIED)
_KNOWN_BUCKETS = (BUCKET_NONE, BUCKET_SOME, BUCKET_FLOOR)


def _known_tier(value: Any) -> str:
    """A tier off the wire, whitelisted. Anything this build does
    not recognise — a newer node's vocabulary, a broken one — is
    stored as unverified: unknown must fail CLOSED, because a tier
    string the floor doesn't know buys nothing here, and treating
    it as anything stronger would be promotion on a guess."""
    return value if value in _KNOWN_TIERS else TIER_UNVERIFIED


def _known_bucket(value: Any) -> str:
    """A support bucket off the wire, whitelisted. An unrecognised
    bucket (``"0"``, ``"1+"`` from a newer or broken node) maps to
    ``"none"``: zero standing support is the only safe reading, and
    it routes the value through the network-reflection rule (floor
    leg (a) or nothing) instead of leaving it eligible for the
    confirmation and anchor legs on an unparseable claim."""
    return value if value in _KNOWN_BUCKETS else BUCKET_NONE

# -- bounds (§6 aging, §1's depot timer) ------------------------------------
FIELD_TTL_DAYS = 90          # unverified life from last node refresh
VERDICT_FRESH_DAYS = 90      # floor (a): verified_at must be inside this
SETTLEMENT_FRESH_DAYS = 90   # floor (c): settled_at inside this
MAX_ENTRIES = 10000          # depot size bound, weakest+oldest evict first

# Per-entry ingest bounds (§6's spirit, field-table edition): the entry
# cap above means nothing if one record can write unbounded field
# rows. Sized generously above anything the node's projection emits
# (its own cache is 2,000 batches; a merged record's candidate lists
# are short), so an honest node never trips them.
MAX_VALUES_PER_FIELD = 25        # candidate values for one field
MAX_LOCAL_VALUES_PER_FIELD = 10  # the user's own values per field
MAX_VARIANTS = 32                # album_track pressings per album
MAX_TRACKS_PER_VARIANT = 500     # slots in one pressing's tracklist

# Node-call circuit breaker: after a transport failure the tagging
# consult stops asking for a cooldown instead of re-taxing every file
# in a batch import for a node that is down. Depot-local answers are
# unaffected — the breaker gates only calls OUT to the node.
NODE_FAILURE_COOLDOWN_SECONDS = 300

# Reconcile's wall-clock budget: the subject limit bounds the count,
# this bounds the clock, so a UI-triggered run against a node that
# accepts connections and never answers cannot spin for an hour.
RECONCILE_TIME_BUDGET_SECONDS = 120

# -- ingest hygiene (§6 free-text caps; the depot treats even the node's
# output as untrusted input on its way to SoulSync's UI and tags) ------------
MAX_VALUE_CHARS = 4096
MAX_NAME_CHARS = 512
# `_clean` bounds: a hostile or broken record must not turn ingest
# into an unbounded blob or a RecursionError. Containers are
# truncated past these, and anything nested deeper is dropped.
MAX_CLEAN_DEPTH = 8
MAX_CLEAN_ITEMS = 1000
# apply_update_push bound: one push carries at most this many
# records; a bigger push is truncated, not honoured wholesale.
MAX_PUSH_RECORDS = 500
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

NODE_TIMEOUT_SECONDS = 10

# Claim-field vocabulary (the node's meta/claims.py ALL_FIELDS subset the
# depot can hold from a projection).
PROMOTABLE_FIELDS = {
    "artist": {"spotify_id", "deezer_id", "tidal_id", "itunes_id",
               "discogs_id", "artist_mbid", "genres"},
    "album": {"spotify_id", "deezer_id", "tidal_id", "itunes_id",
              "discogs_id", "release_group_mbid", "year", "genres",
              "label"},
    "track": {"spotify_id", "deezer_id", "tidal_id", "itunes_id",
              "discogs_id", "recording_mbid", "isrc", "year", "genres"},
}

# Every field name a projection may store rows under: the promotable
# vocabulary plus the structural fields the depot reads but never
# promotes (the album_track tracklists, read for settled variants,
# and the duration it holds without promoting). Anything else in a
# record is not stored — an unknown field name is either a newer node's
# vocabulary this build cannot judge, or junk; either way the depot
# has no floor for it.
_STRUCTURAL_FIELDS = {"album_track", "duration_ms"}
_FIELD_VOCABULARY = set().union(*PROMOTABLE_FIELDS.values()) | _STRUCTURAL_FIELDS

# claim field -> (library table, value column, provider match-status column
# or None). Fill-empty-only writes, mirroring the enrichment workers.
# Filling a provider ID also sets its ``<provider>_match_status`` to
# 'matched': the export only publishes IDs whose status is 'matched',
# and the network mark (network_fields) is what keeps a promoted ID
# honest on the way out — the two ship together, never one alone.
_LIBRARY_COLUMNS: Dict[Tuple[str, str], Tuple[str, str, Optional[str]]] = {
    ("artist", "spotify_id"): ("artists", "spotify_artist_id", "spotify_match_status"),
    ("artist", "deezer_id"): ("artists", "deezer_id", "deezer_match_status"),
    ("artist", "tidal_id"): ("artists", "tidal_id", "tidal_match_status"),
    ("artist", "itunes_id"): ("artists", "itunes_artist_id", "itunes_match_status"),
    ("artist", "discogs_id"): ("artists", "discogs_id", "discogs_match_status"),
    ("artist", "artist_mbid"): ("artists", "musicbrainz_id", "musicbrainz_match_status"),
    ("artist", "genres"): ("artists", "genres", None),
    ("album", "spotify_id"): ("albums", "spotify_album_id", "spotify_match_status"),
    ("album", "deezer_id"): ("albums", "deezer_id", "deezer_match_status"),
    ("album", "tidal_id"): ("albums", "tidal_id", "tidal_match_status"),
    ("album", "itunes_id"): ("albums", "itunes_album_id", "itunes_match_status"),
    ("album", "discogs_id"): ("albums", "discogs_id", "discogs_match_status"),
    ("album", "release_group_mbid"): ("albums", "musicbrainz_release_group_id", "musicbrainz_match_status"),
    ("album", "year"): ("albums", "year", None),
    ("album", "genres"): ("albums", "genres", None),
    ("album", "label"): ("albums", "label", None),
    ("track", "spotify_id"): ("tracks", "spotify_track_id", "spotify_match_status"),
    ("track", "deezer_id"): ("tracks", "deezer_id", "deezer_match_status"),
    ("track", "tidal_id"): ("tracks", "tidal_id", "tidal_match_status"),
    ("track", "itunes_id"): ("tracks", "itunes_track_id", "itunes_match_status"),
    ("track", "discogs_id"): ("tracks", "discogs_id", "discogs_match_status"),
    ("track", "recording_mbid"): ("tracks", "musicbrainz_recording_id", "musicbrainz_match_status"),
    ("track", "isrc"): ("tracks", "isrc", None),
    ("track", "year"): ("tracks", "year", None),
}

# The provider a disambiguation settlement key names for a field
# (the node's ladder keys candidates "<provider>:<id>", and every
# MBID field settles under "musicbrainz").
_SETTLEMENT_PROVIDERS = {
    "artist_mbid": "musicbrainz",
    "recording_mbid": "musicbrainz",
    "release_group_mbid": "musicbrainz",
}

# Duration is held in the depot but deliberately NOT promotable into
# library records in this step: the node's claim unit is duration_ms
# while SoulSync's tracks.duration has been written in mixed units by
# different writers, and a wrong-unit promotion is worse than none.
# It promotes into nothing until a units audit says otherwise.


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse a timestamp the depot holds. The node's projection
    carries EPOCH SECONDS (floats) for verified_at / settled_at;
    the depot's own columns are ISO strings. Both are accepted."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _coerce_ts(value: Any) -> Optional[str]:
    """Normalize a node timestamp (epoch float or ISO string) to the
    depot's ISO storage form."""
    parsed = _parse_iso(value)
    return _iso(parsed) if parsed else None


def _clamp_to_receipt(parsed: Optional[datetime],
                      now: datetime) -> Optional[datetime]:
    """Cap a node-supplied timestamp at the depot's receipt time.

    The node store caps an origin's ``asserted_at`` at the moment of
    receipt for exactly this reason: a future-dated timestamp must
    never buy freshness. Without the cap, a ``verified_at`` in the
    year 2100 makes the 90-day lapse land in 2100 too, floor leg
    (a)'s age test goes negative and passes, and a strong tier
    becomes effectively permanent. Clamped, a future date behaves
    exactly like "verified just now" — and lapses 90 days after
    RECEIPT, not after the fantasy date."""
    if parsed is not None and parsed > now:
        return now
    return parsed


def _clean(value: Any, _depth: int = 0) -> Any:
    """Strip control characters and cap length on network strings.

    Bounded in width AND depth: a record the depot stores passes
    through here, and an adversarial or corrupt one must not become
    an unbounded blob or blow the stack (a deeply nested push used
    to RecursionError inside the /records route and surface as a
    500 — the death of one bad record must cost nothing)."""
    if isinstance(value, str):
        return _CONTROL_CHARS.sub("", value)[:MAX_VALUE_CHARS]
    if _depth >= MAX_CLEAN_DEPTH:
        return None
    if isinstance(value, list):
        return [_clean(v, _depth + 1)
                for v in value[:MAX_CLEAN_ITEMS]]
    if isinstance(value, dict):
        return {str(k)[:MAX_NAME_CHARS]: _clean(v, _depth + 1)
                for k, v in list(value.items())[:MAX_CLEAN_ITEMS]}
    return value


def _value_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# The tagging consult reads the depot a dozen times per file; running
# the DDL on every call would tax the exact path the depot exists to
# make cheaper. Ensured paths are remembered per database file. The
# lock keeps two threads from running the DDL block concurrently on
# first use (the statements are IF NOT EXISTS, but concurrent DDL on
# one SQLite file can still surface as lock contention on the hot
# tagging path).
_SCHEMA_READY: set = set()
_SCHEMA_LOCK = threading.Lock()


def ensure_schema(db: Any) -> None:
    path = getattr(db, "database_path", None)
    if path is not None and path in _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if path is not None and path in _SCHEMA_READY:
            return
        _ensure_schema_ddl(db, path)


def _ensure_schema_ddl(db: Any, path: Any) -> None:
    with db._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metasync_depot_entries (
                subject TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name_json TEXT,
                record_json TEXT NOT NULL,
                disambiguation_json TEXT,
                fetched_at TEXT NOT NULL,
                invalidated INTEGER NOT NULL DEFAULT 0,
                invalidated_at TEXT,
                invalidation_reason TEXT,
                reconcile_rejects INTEGER NOT NULL DEFAULT 0
            )
        """)
        entry_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(metasync_depot_entries)").fetchall()}
        if "reconcile_rejects" not in entry_cols:
            conn.execute(
                "ALTER TABLE metasync_depot_entries "
                "ADD COLUMN reconcile_rejects INTEGER NOT NULL DEFAULT 0")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metasync_depot_fields (
                subject TEXT NOT NULL,
                field TEXT NOT NULL,
                value_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'unverified',
                support_bucket TEXT NOT NULL DEFAULT 'none',
                support_label TEXT,
                is_local INTEGER NOT NULL DEFAULT 0,
                verified_at TEXT,
                tier_lapses_at TEXT,
                expires_at TEXT NOT NULL,
                retracted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (subject, field, value_key)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metasync_depot_fields_subject
            ON metasync_depot_fields (subject, field)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metasync_network_marks (
                entity_kind TEXT NOT NULL,
                soul_id TEXT NOT NULL,
                field TEXT NOT NULL,
                value_key TEXT NOT NULL,
                via TEXT NOT NULL DEFAULT 'promoted',
                marked_at TEXT NOT NULL,
                PRIMARY KEY (entity_kind, soul_id, field, value_key)
            )
        """)
        # Marks are keyed BY VALUE, not one slot per field: a field
        # can legitimately hold two network-derived values in one
        # tagging run (the depot promotes ISRC A, the steered fetch
        # by that ID returns ISRC B), and a one-slot table let the
        # second mark overwrite the first — the value actually
        # written then exported as the user's own data. Older
        # tables from this branch's first cut keyed (kind, soul_id,
        # field); rebuild them in place, rows carried over.
        pk_cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(metasync_network_marks)").fetchall()
            if r[5]]
        if pk_cols and "value_key" not in pk_cols:
            conn.execute("""
                CREATE TABLE metasync_network_marks_v2 (
                    entity_kind TEXT NOT NULL,
                    soul_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    value_key TEXT NOT NULL,
                    via TEXT NOT NULL DEFAULT 'promoted',
                    marked_at TEXT NOT NULL,
                    PRIMARY KEY (entity_kind, soul_id, field, value_key)
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO metasync_network_marks_v2
                SELECT entity_kind, soul_id, field, value_key, via,
                       marked_at FROM metasync_network_marks
            """)
            conn.execute("DROP TABLE metasync_network_marks")
            conn.execute(
                "ALTER TABLE metasync_network_marks_v2 "
                "RENAME TO metasync_network_marks")
        conn.commit()
    if path is not None:
        _SCHEMA_READY.add(path)


# ---------------------------------------------------------------------------
# Ingest — one projected record from the node
# ---------------------------------------------------------------------------

def _field_rows_from_record(record: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    """Flatten a coarse projection record into depot field rows.

    The record is authoritative for its subject: rows it no longer
    carries are dropped by store_record (a superseding answer, a
    reconcile), which is how a field fed by several answers loses
    only the dead contribution.
    """
    rows: List[Dict[str, Any]] = []
    expires = _iso(now + timedelta(days=FIELD_TTL_DAYS))
    fields = record.get("fields") or {}
    if not isinstance(fields, dict):
        return rows
    for field, entry in fields.items():
        if not isinstance(entry, dict):
            continue
        field = str(field)
        if field not in _FIELD_VOCABULARY:
            continue  # a field this build has no floor for is not stored
        if "variants" in entry:  # album_track: one row per variant
            variants = entry.get("variants") or {}
            if isinstance(variants, dict):
                for name, variant in list(variants.items())[:MAX_VARIANTS]:
                    if not isinstance(variant, dict):
                        continue
                    tracks = variant.get("tracks") or []
                    if isinstance(tracks, list):
                        tracks = tracks[:MAX_TRACKS_PER_VARIANT]
                    else:
                        tracks = []
                    tiers = {_known_tier(t.get("tier")) for t in tracks
                             if isinstance(t, dict)}
                    tier = (TIER_STRONG if TIER_STRONG in tiers else
                            TIER_PLAUSIBLE if TIER_PLAUSIBLE in tiers else
                            TIER_UNVERIFIED)
                    buckets = {_known_bucket(t.get("support"))
                               for t in tracks if isinstance(t, dict)}
                    bucket = (BUCKET_FLOOR if BUCKET_FLOOR in buckets else
                              BUCKET_SOME if BUCKET_SOME in buckets else
                              BUCKET_NONE)
                    rows.append({
                        "field": "album_track",
                        "value": _clean({"variant": name,
                                         "fingerprint": variant.get("fingerprint"),
                                         "settled": bool(variant.get("settled")),
                                         "tracks": tracks}),
                        "tier": tier, "support_bucket": bucket,
                        "support_label": None, "is_local": False,
                        "verified_at": None,
                    })
            continue
        values = entry.get("values") or []
        if isinstance(values, list):
            for item in values[:MAX_VALUES_PER_FIELD]:
                if not isinstance(item, dict) or "value" not in item:
                    continue
                # verified_at is clamped at receipt before anything
                # downstream (lapse, floor leg (a)) can read it.
                verified = _clamp_to_receipt(
                    _parse_iso(item.get("verified_at")), now)
                rows.append({
                    "field": field,
                    "value": _clean(item.get("value")),
                    "tier": _known_tier(item.get("tier")),
                    "support_bucket": _known_bucket(item.get("support")),
                    "support_label": _clean(item.get("support_label")),
                    "is_local": bool(item.get("local")),
                    "verified_at": _iso(verified) if verified else None,
                })
        # The field's local values are the user's own data as the node
        # sees it: stored, flagged, never promotion candidates.
        local_values = entry.get("local_values")
        if isinstance(local_values, list):
            for value in local_values[:MAX_LOCAL_VALUES_PER_FIELD]:
                rows.append({
                    "field": field, "value": _clean(value),
                    "tier": TIER_UNVERIFIED, "support_bucket": BUCKET_NONE,
                    "support_label": None, "is_local": True,
                    "verified_at": None,
                })
    for row in rows:
        row["expires_at"] = expires
        verified = _parse_iso(row.get("verified_at"))
        row["tier_lapses_at"] = (
            _iso(verified + timedelta(days=VERDICT_FRESH_DAYS))
            if verified and row["tier"] == TIER_STRONG else None)
    return rows


def store_record(db: Any, record: Any, now: Optional[datetime] = None) -> bool:
    """Store one projected record (from /local/meta/query or
    /local/meta/record, or an update push). Returns False for shapes
    the depot cannot hold. A record with ``found`` false clears the
    subject's network candidates — the node no longer holds it."""
    if not isinstance(record, dict):
        return False
    subject = record.get("subject")
    kind = record.get("kind")
    if not isinstance(subject, str) or not subject.startswith("soul_"):
        return False
    if kind not in PROMOTABLE_FIELDS:
        return False
    now = now or _utcnow()
    ensure_schema(db)
    disambiguation = record.get("disambiguation")
    if isinstance(disambiguation, dict):
        # Stored artifacts are ingested cleaned like field values,
        # never raw: the block is compared against candidate keys in
        # leg (c) today and read by nobody else, but stored-raw is a
        # hole the day anything renders it.
        disambiguation = _clean(dict(disambiguation))
        # settled_at arrives as epoch seconds; store ISO like
        # everything else the depot holds, clamped at receipt so a
        # future date cannot keep leg (c) fresh forever.
        settled = _clamp_to_receipt(
            _parse_iso(disambiguation.get("settled_at")), now)
        if settled:
            disambiguation["settled_at"] = _iso(settled)
    # The full record is NOT stored: nothing reads record_json (the
    # field rows above are the depot), and a cleaned-but-whole copy
    # was an unbounded blob riding along on every entry. What stays
    # is a bounded provenance summary — enough to see what arrived,
    # never enough to become a second copy of the record.
    record_fields = record.get("fields")
    version = record.get("version")
    summary = {"found": bool(record.get("found")),
               "version": version if isinstance(version, (int, str))
               and not isinstance(version, bool) else None,
               "fields": sorted(str(f) for f in record_fields)[:100]
               if isinstance(record_fields, dict) else []}
    disambiguation_json = (
        json.dumps(disambiguation, ensure_ascii=False)[:MAX_VALUE_CHARS]
        if isinstance(disambiguation, dict) else None)
    with db._get_connection() as conn:
        conn.execute(
            """INSERT INTO metasync_depot_entries
               (subject, kind, name_json, record_json, disambiguation_json,
                fetched_at, invalidated, invalidated_at, invalidation_reason)
               VALUES (?, ?, ?, ?, ?, ?, 0, NULL, NULL)
               ON CONFLICT(subject) DO UPDATE SET
                 kind = excluded.kind, name_json = excluded.name_json,
                 record_json = excluded.record_json,
                 disambiguation_json = excluded.disambiguation_json,
                 fetched_at = excluded.fetched_at,
                 invalidated = 0, invalidated_at = NULL,
                 invalidation_reason = NULL, reconcile_rejects = 0
                 """,
            (subject, kind,
             json.dumps(_clean(record.get("name")), ensure_ascii=False),
             json.dumps(summary, ensure_ascii=False),
             disambiguation_json,
             _iso(now)))
        # Authoritative refresh: the node's current view replaces the
        # stored candidates wholesale. Retraction flags do NOT survive
        # a refresh — if the node still serves the value, its own view
        # already excludes whatever died on its side (§5 purge).
        conn.execute("DELETE FROM metasync_depot_fields WHERE subject = ?",
                     (subject,))
        if record.get("found"):
            for row in _field_rows_from_record(record, now):
                conn.execute(
                    """INSERT OR REPLACE INTO metasync_depot_fields
                       (subject, field, value_key, value_json, tier,
                        support_bucket, support_label, is_local,
                        verified_at, tier_lapses_at, expires_at,
                        retracted, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (subject, row["field"], _value_key(row["value"]),
                     json.dumps(row["value"], ensure_ascii=False),
                     row["tier"], row["support_bucket"],
                     row["support_label"], 1 if row["is_local"] else 0,
                     row["verified_at"], row["tier_lapses_at"],
                     row["expires_at"], _iso(now)))
        conn.commit()
    _prune_entries(db)
    return True


def _prune_entries(db: Any) -> None:
    """Bound the depot like the node's cache: past MAX_ENTRIES, the
    weakest (no strong field), oldest-refreshed entries go first."""
    with db._get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM metasync_depot_entries").fetchone()[0]
        excess = count - MAX_ENTRIES
        if excess <= 0:
            return
        victims = conn.execute(
            """SELECT e.subject FROM metasync_depot_entries e
               ORDER BY EXISTS (
                   SELECT 1 FROM metasync_depot_fields f
                   WHERE f.subject = e.subject AND f.tier = ?
                     AND f.retracted = 0) ASC,
                 e.fetched_at ASC LIMIT ?""",
            (TIER_STRONG, excess)).fetchall()
        for (subject,) in victims:
            conn.execute("DELETE FROM metasync_depot_fields WHERE subject = ?",
                         (subject,))
            conn.execute("DELETE FROM metasync_depot_entries WHERE subject = ?",
                         (subject,))
        conn.commit()


# ---------------------------------------------------------------------------
# Reads — the live candidate view promotion and display consume
# ---------------------------------------------------------------------------

def effective_tier(row: Dict[str, Any], now: datetime) -> str:
    """§1's depot timer: a strong verdict lapses 90 days after its
    verified_at unless the node pushed a fresher one."""
    if row.get("tier") == TIER_STRONG:
        lapses = _parse_iso(row.get("tier_lapses_at"))
        if lapses is not None and now >= lapses:
            return TIER_UNVERIFIED
    return str(row.get("tier") or TIER_UNVERIFIED)


def is_network_reflected(row: Dict[str, Any]) -> bool:
    """A non-local value with zero standing support. §2.4's mark
    cannot be seen through the projection, so these are treated as
    network reflections: floor leg (a) only."""
    return (not row.get("is_local")
            and row.get("support_bucket") == BUCKET_NONE)


def get_fields(db: Any, subject: str, field: Optional[str] = None,
               now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Live (not retracted, not expired, entry not invalidated)
    candidate rows for a subject, strongest first. Local rows are
    included, flagged — callers promoting must skip them."""
    now = now or _utcnow()
    ensure_schema(db)
    sql = """SELECT f.* FROM metasync_depot_fields f
             JOIN metasync_depot_entries e ON e.subject = f.subject
             WHERE f.subject = ? AND f.retracted = 0 AND e.invalidated = 0
               AND f.expires_at > ?"""
    params: List[Any] = [subject, _iso(now)]
    if field:
        sql += " AND f.field = ?"
        params.append(field)
    with db._get_connection() as conn:
        cursor = conn.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, r, strict=True)) for r in cursor.fetchall()]
    for row in rows:
        row["value"] = json.loads(row.pop("value_json"))
        row["is_local"] = bool(row["is_local"])
        row["effective_tier"] = effective_tier(row, now)
    rank = {TIER_STRONG: 0, TIER_PLAUSIBLE: 1, TIER_UNVERIFIED: 2}
    rows.sort(key=lambda r: (r["is_local"], rank.get(r["effective_tier"], 3),
                             r["field"]))
    return rows


def get_disambiguation(db: Any, subject: str) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT disambiguation_json FROM metasync_depot_entries "
            "WHERE subject = ? AND invalidated = 0", (subject,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        parsed = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def depot_status(db: Any) -> Dict[str, Any]:
    ensure_schema(db)
    with db._get_connection() as conn:
        entries = conn.execute(
            "SELECT COUNT(*) FROM metasync_depot_entries").fetchone()[0]
        invalidated = conn.execute(
            "SELECT COUNT(*) FROM metasync_depot_entries "
            "WHERE invalidated = 1").fetchone()[0]
        fields = conn.execute(
            "SELECT COUNT(*) FROM metasync_depot_fields "
            "WHERE retracted = 0 AND is_local = 0").fetchone()[0]
        strong = conn.execute(
            "SELECT COUNT(*) FROM metasync_depot_fields "
            "WHERE retracted = 0 AND is_local = 0 AND tier = ?",
            (TIER_STRONG,)).fetchone()[0]
        marks = conn.execute(
            "SELECT COUNT(*) FROM metasync_network_marks").fetchone()[0]
    return {"entries": entries, "invalidated_entries": invalidated,
            "candidate_fields": fields, "strong_verified_fields": strong,
            "network_marks": marks}


# ---------------------------------------------------------------------------
# §8's promotion floor — the one gate every promotion goes through
# ---------------------------------------------------------------------------

def _norm_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _as_int(value: Any) -> Optional[int]:
    """An int from a node- or provider-supplied slot part, or None.
    Slot parts arrive off the wire (a variant's disc, a provider
    response's position): a non-numeric one is a malformed answer,
    and the floor's answer to a malformed answer is False — never
    an exception escaping into the tagging pipeline."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def confirmation_key_check(response: Any, local_keys: Any, *,
                           fetched_id: Any = None,
                           fetched_for: Any = None) -> bool:
    """Floor leg (b)'s independent key check, over a provider response
    fetched by the candidate's own ID.

    Passes only when the response and the user's local (non-network)
    data agree on a KEY: an ISRC, an MBID, or album + position
    against a local album/track ID. Name and duration in the
    response are never even read here — agreement on them is
    exactly the weak test §8 refuses (a remaster, a re-recording
    and clean/explicit twin share both). Local keys that are
    themselves network-marked never vouch: an earlier promotion
    cannot confirm a later one (§8).

    ``fetched_id`` / ``fetched_for`` tie the check to ONE candidate:
    when both are given, the response must be the one fetched BY
    that candidate's own ID — a response fetched for candidate X
    confirms nothing about candidate Y, however well its keys
    agree. (A caller looping candidates over one shared response
    would otherwise pass whichever candidate it tried first,
    including every genre in a list, on a fetch that was never
    about them.) Callers that hold a bare key-check response
    without fetch provenance omit both.
    """
    if not isinstance(response, dict) or not isinstance(local_keys, dict):
        return False
    if fetched_id is not None or fetched_for is not None:
        if str(fetched_id or "") != str(fetched_for or ""):
            return False

    def usable(key: str) -> Optional[str]:
        entry = local_keys.get(key)
        if isinstance(entry, dict):
            if entry.get("network"):
                return None
            entry = entry.get("value")
        return entry if isinstance(entry, str) and entry else None

    resp_isrc = response.get("isrc")
    local_isrc = usable("isrc")
    if resp_isrc and local_isrc and _norm_key(resp_isrc) == _norm_key(local_isrc):
        return True
    for resp_key, local_key in (("recording_mbid", "mbid"),
                                ("mbid", "mbid"),
                                ("release_mbid", "release_mbid")):
        resp_val = response.get(resp_key)
        local_val = usable(local_key)
        if resp_val and local_val and str(resp_val).lower() == str(local_val).lower():
            return True
    # Album + position: the response names the album it sits on and
    # its slot; the local side names one of the user's own albums (by
    # a provider album ID from their own data) and the file's slot.
    # Position parts are compared defensively: a response whose slot
    # is missing or non-numeric (the shape a misread client key
    # produces) simply does not pass — it must never raise.
    local_album = usable("album_id")
    local_pos = local_keys.get("position")
    resp_album = response.get("album_id")
    resp_pos = response.get("position")
    if (local_album and resp_album and str(resp_album) == str(local_album)
            and isinstance(local_pos, (list, tuple))
            and isinstance(resp_pos, (list, tuple))
            and len(local_pos) == 2 and len(resp_pos) == 2):
        local_slot = [_as_int(x) for x in local_pos]
        resp_slot = [_as_int(x) for x in resp_pos]
        if None not in local_slot and local_slot == resp_slot:
            return True
    return False


def _settled_variant_anchor(db: Any, album_subject: Optional[str],
                            track_subject: str, disc: Any,
                            position: Any) -> bool:
    """Is the track at the file's disc+position in a SETTLED
    album_track variant whose own slot for it is strong-verified?

    This proves WHICH track sits at the slot — and nothing about
    whether the provider ID attached to that track is right.
    promotion_verdict therefore does NOT consult it (concerns
    §4.1): with the node's projection carrying no per-slot
    provider IDs, slot plus an exactly-one-candidate rule let a
    lone answering node's wrong ID promote on niche items. The
    check stays for the day the projection carries the per-slot
    provider ID recorded at verification time: then leg (c) can
    require the candidate to BE the ID the verified tracklist
    carries at that slot, which is what §8 actually names.
    Slot parts are read defensively: a non-numeric disc/position
    off the wire is no anchor, never an exception."""
    file_disc = _as_int(disc)
    file_pos = _as_int(position)
    if not album_subject or file_disc is None or file_pos is None:
        return False
    for row in get_fields(db, album_subject, "album_track"):
        variant = row["value"]
        if not isinstance(variant, dict) or not variant.get("settled"):
            continue
        if not variant.get("fingerprint"):
            continue
        for slot in variant.get("tracks") or []:
            if (isinstance(slot, dict)
                    and slot.get("track") == track_subject
                    and _as_int(slot.get("disc")) == file_disc
                    and _as_int(slot.get("position")) == file_pos
                    and slot.get("tier") == TIER_STRONG):
                return True
    return False


# The provider-ID fields: any field whose value IS a provider
# identifier (the provider IDs proper plus the MusicBrainz ID
# fields). Leg (c)'s tracklist slot anchor is OFF for exactly
# these (concerns §4.1): the settled slot proves which track sits
# there, not that the ID a node attached to it is right, and on a
# niche item a single answering node is automatically the single
# survivor. These fields promote via leg (a), leg (b), or leg
# (c)'s disambiguation settlement — never via the slot.
_PROVIDER_ID_FIELDS = {
    "spotify_id", "deezer_id", "tidal_id", "itunes_id", "discogs_id",
    "artist_mbid", "release_group_mbid", "recording_mbid",
}


def promotion_verdict(db: Any, subject: str, field: str, value: Any, *,
                      now: Optional[datetime] = None,
                      confirmation_response: Any = None,
                      confirmation_fetched_id: Any = None,
                      local_keys: Any = None,
                      album_subject: Optional[str] = None,
                      disc: Any = None, position: Any = None) -> Tuple[bool, Optional[str]]:
    """May this depot value be promoted into the library right now?

    Returns (allowed, leg) where leg is "a", "b" or "c". The floor
    (DESIGN §8), checked at promotion time against live depot rows:

    (a) the value's tier is strong-verified with a verified_at
        inside 90 days — a tier the node earned with its own check.
        The only leg a network-reflected value (zero standing
        support, non-local) may ever use.
    (b) the workflow spent a local provider confirmation fetch on
        the candidate (by its own ID — ``confirmation_fetched_id``
        must BE this candidate) and the response passes
        confirmation_key_check() against the user's non-network
        local keys.
    (c) the value is the SINGLE surviving candidate for its field,
        picked by a verified anchor: the node's disambiguation
        settlement for the entity (settled via a fetched link or a
        shared strong MBID, promotion-flagged, fresh). The
        tracklist slot anchor is NOT such an anchor for provider-ID
        fields: a settled, strong-verified slot proves which track
        sits there, not that the ID attached to it is right, and
        on a niche item one answering node is automatically the
        single survivor (concerns §4.1) — so this leg never
        promotes a provider ID on slot evidence. It comes back
        when the projection carries the per-slot provider ID
        recorded at verification time and the candidate can be
        checked against it. A settlement reached by counting
        unverified links is display-only in the projection
        (promotion=False) and never satisfies this leg; a lone
        claim with zero support and no anchor is not a survivor.

    Name + duration agreement alone never promotes — there is no
    leg for it, on purpose.
    """
    now = now or _utcnow()
    rows = [r for r in get_fields(db, subject, field, now=now)
            if _value_key(r["value"]) == _value_key(value)]
    if not rows:
        return (False, None)
    row = rows[0]
    if row["is_local"]:
        return (False, None)  # the user's own data needs no promotion

    # (a) strong-verified, fresh.
    if row["effective_tier"] == TIER_STRONG:
        verified = _parse_iso(row.get("verified_at"))
        if verified and now - verified <= timedelta(days=VERDICT_FRESH_DAYS):
            return (True, "a")

    if is_network_reflected(row):
        return (False, None)  # §2.4: leg (a) or nothing

    # (b) local confirmation fetch, independent key check. The
    # response must be the one fetched BY this candidate's own ID:
    # without that tie, any candidate could ride on a fetch spent
    # for a different one.
    if confirmation_response is not None and local_keys:
        if confirmation_key_check(confirmation_response, local_keys,
                                  fetched_id=confirmation_fetched_id,
                                  fetched_for=value):
            return (True, "b")

    # (c) single survivor + verified anchor.
    candidates = [r for r in get_fields(db, subject, field, now=now)
                  if not r["is_local"]]
    if len(candidates) == 1:
        block = get_disambiguation(db, subject)
        if block and block.get("promotion") and block.get("settled"):
            settled = str(block["settled"])
            provider = _SETTLEMENT_PROVIDERS.get(field) or (
                field[:-3] if field.endswith("_id") else field)
            if settled in (f"{provider}:{value}", str(value)):
                settled_at = _parse_iso(block.get("settled_at"))
                if settled_at and now - settled_at <= timedelta(
                        days=SETTLEMENT_FRESH_DAYS):
                    return (True, "c")
        # No slot-anchor path (concerns §4.1): the settled slot
        # proves which track sits at the file's disc+position, not
        # that a provider ID attached to it is right, so it never
        # promotes a provider-ID field — and it never spoke for
        # non-ID fields. _settled_variant_anchor stays for the
        # cross-repo fix that records the per-slot provider ID at
        # verification time and checks the candidate against it.
    return (False, None)


def subject_kind(db: Any, subject: str) -> Optional[str]:
    ensure_schema(db)
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT kind FROM metasync_depot_entries WHERE subject = ?",
            (subject,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Marks — §2.4's provenance bit, SoulSync-side storage
# ---------------------------------------------------------------------------

def record_mark(db: Any, kind: str, soul_id: str, field: str, value: Any,
                via: str = "promoted", conn: Any = None) -> None:
    """Mark one library value as network-derived. Keyed by value as
    well as field: if the field's value is later replaced by the
    user's own matching, the mark stops applying (the export checks
    the row's current value against it) instead of mislabelling
    fresh data.

    One row PER VALUE, never one slot per field: a single tagging
    run can put two network-derived values on one field (the depot
    promotes ISRC A; the fetch steered by the promoted Deezer ID
    returns ISRC B and B inherits a mark). With one slot, B's mark
    overwrote A's while A was the value written to the tags — A
    then exported as the user's own data, the laundering the mark
    exists to prevent. Re-marking the SAME value just refreshes
    its row.

    With ``conn`` given, the mark is written on the caller's
    connection inside the caller's transaction — promote_field's
    fill uses this so the fill and the mark commit together or not
    at all (the module's "the two ship together" contract). That
    path does NOT re-run ensure_schema: the caller ensured it
    before opening its transaction, and reaching for the DDL on a
    second connection mid-transaction is a self-inflicted
    "database is locked"."""
    if kind not in PROMOTABLE_FIELDS or not soul_id:
        return
    sql = """INSERT INTO metasync_network_marks
             (entity_kind, soul_id, field, value_key, via, marked_at)
             VALUES (?, ?, ?, ?, ?, ?)
             ON CONFLICT(entity_kind, soul_id, field, value_key)
             DO UPDATE SET via = excluded.via,
               marked_at = excluded.marked_at"""
    params = (kind, soul_id, field, _value_key(_clean(value)), via,
              _iso(_utcnow()))
    if conn is not None:
        conn.execute(sql, params)
        return
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(sql, params)
        conn.commit()


def marks_for(db: Any, kind: str, soul_id: str) -> Dict[str, List[str]]:
    """{field: [value_key, ...]} — every value marked for one
    entity. A field can hold several marked values (a promoted one
    plus one fetched by way of it); all of them are the entity's
    network-derived values for that field."""
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT field, value_key FROM metasync_network_marks "
            "WHERE entity_kind = ? AND soul_id = ?", (kind, soul_id)).fetchall()
    out: Dict[str, List[str]] = {}
    for field, value_key in rows:
        out.setdefault(field, []).append(value_key)
    return out


# Fields whose library values are IDs: the mark comparison for
# these is normalized (case/trim/punctuation-insensitive), so a
# later writer's cosmetic re-casing of the same ID does not drop
# the network bit while the promoted value is still what's stored.
_ID_FIELDS = {"spotify_id", "deezer_id", "tidal_id", "itunes_id",
              "discogs_id", "artist_mbid", "release_group_mbid",
              "recording_mbid", "isrc"}


def _table_columns(conn: Any, table: str) -> set:
    """The columns a library table actually carries. The static
    _LIBRARY_COLUMNS map can name a column the table does not have
    (tracks has no discogs_id — Discogs enrichment is artist/album
    level), and SQL naming a missing column raises; callers check
    membership here first and fail closed. Table names come only
    from the static map, never from input."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def network_fields_for_export(db: Any, kind: str, row: Dict[str, Any]) -> List[str]:
    """The §5.1 G1 export bit: claim fields of THIS library row
    whose CURRENT value is a marked (network-derived) one. A mark
    whose value no longer matches what the row holds contributes
    nothing — the bit describes the value, not the field's history.

    The current value comes from the export row itself whenever the
    row carries it (the export walk reads the mapped column for
    every row it serves, under its library name): soul_ids are NOT
    unique — a track's is artist+title by design, shared between a
    single and the album version — so resolving "the current value"
    by soul_id can fetch a DIFFERENT row's value, and the promoted
    row's bit silently drops. When the row lacks the value (a
    hand-built row), the fallback lookup is by the row's own ``id``
    — still this row, never an arbitrary same-soul_id sibling.

    Marks are per value, so a scalar field is network-marked while
    ANY of its marked values matches the row's current value.
    List-valued fields (genres) match per value too, not whole-list:
    tagging stores ONE merged list (depot genres plus whatever
    local enrichment added), while marks hold the subsets each path
    promoted or fetched. The field is network-marked while any
    marked genre value is still present in the current list; when
    the last marked genre goes, the bit goes with it."""
    soul_id = row.get("soul_id")
    if not soul_id:
        return []
    marks = marks_for(db, kind, soul_id)
    if not marks:
        return []
    out: List[str] = []
    with db._get_connection() as conn:
        for field, value_keys in marks.items():
            target = _LIBRARY_COLUMNS.get((kind, field))
            if target is None:
                continue
            table, column, _status = target
            if column in row:
                current = row.get(column)
            elif row.get("id") is not None:
                if column not in _table_columns(conn, table):
                    # The map names a column this table does not
                    # carry (tracks / discogs_id): there is no
                    # stored value to match the mark against, and
                    # asking SQLite for the column raises — an
                    # export 500. Fail closed: no bit.
                    continue
                found = conn.execute(
                    f"SELECT {column} FROM {table} WHERE id = ?",
                    (row["id"],)).fetchone()
                if found is None:
                    continue
                current = found[0]
            else:
                # No identity to resolve THIS row with: fail closed
                # (no bit) rather than guess from a sibling row.
                continue
            if field == "genres":
                if isinstance(current, str):
                    try:
                        current = json.loads(current)
                    except ValueError:
                        current = []
                current = {str(g).strip().lower()
                           for g in (current or [])}
                hit = False
                for value_key in value_keys:
                    try:
                        marked = json.loads(value_key)
                    except ValueError:
                        continue
                    marked_values = (marked if isinstance(marked, list)
                                     else [marked])
                    if any(str(g).strip().lower() in current
                           for g in marked_values):
                        hit = True
                        break
                if hit:
                    out.append(field)
                continue
            if current is None:
                continue
            for value_key in value_keys:
                try:
                    marked = json.loads(value_key)
                except ValueError:
                    continue
                # str() on both sides: a provider ID may sit in
                # the library as an int and in the mark as a string
                # (or the reverse) depending on which writer filled
                # it. ID-shaped values compare normalized, so a
                # cosmetic re-case or trim by a later writer keeps
                # the bit; other scalars (year, label) compare
                # exactly.
                if field in _ID_FIELDS:
                    if _norm_key(marked) and \
                            _norm_key(marked) == _norm_key(current):
                        out.append(field)
                        break
                elif str(marked) == str(current):
                    out.append(field)
                    break
    return sorted(out)


# ---------------------------------------------------------------------------
# Promotion into a library record — fill empty fields only (§2.1)
# ---------------------------------------------------------------------------

def promote_field(db: Any, kind: str, soul_id: str, field: str, *,
                  entity_id: Any = None,
                  now: Optional[datetime] = None,
                  confirmation_response: Any = None,
                  confirmation_fetched_id: Any = None,
                  local_keys: Any = None,
                  album_subject: Optional[str] = None,
                  disc: Any = None, position: Any = None) -> Dict[str, Any]:
    """Promote one depot candidate into its library record.

    The library row is found by soul_id (or entity_id when given).
    Only an EMPTY column is filled — promotion never overwrites the
    user's data (§2.1) — and a successful fill commits together
    with its §2.4 mark, in one transaction on one connection:
    neither can land without the other. Returns a status dict; a
    refusal is a status, not an exception.

    A confirmation response is spent on ONE candidate: pass
    ``confirmation_fetched_id`` (the ID the response was fetched
    by) and leg (b) can only ever confirm that candidate. For
    genres — where one fetch is never about each value — leg (b)
    is not offered at all; each genre clears (a) or (c) on its own.
    """
    now = now or _utcnow()
    target = _LIBRARY_COLUMNS.get((kind, field))
    if target is None:
        return {"status": "unsupported_field", "field": field}
    table, column, status_column = target
    candidates = [r for r in get_fields(db, soul_id, field, now=now)
                  if not r["is_local"]]
    if not candidates:
        return {"status": "no_candidate", "field": field}
    if field == "genres":
        # Genre claims are per-genre values; the library column is
        # the whole list. Each genre must clear the floor on its own
        # (§8: disputed genres are never promoted on agreement
        # alone), and the column is filled with exactly the genres
        # that cleared it. The confirmation response is deliberately
        # NOT passed: it was fetched by one ID, and letting every
        # genre ride on it would promote values the fetch never
        # spoke about.
        passing: List[str] = []
        leg = None
        for row in candidates:
            ok, via = promotion_verdict(
                db, soul_id, field, row["value"], now=now,
                local_keys=local_keys, album_subject=album_subject,
                disc=disc, position=position)
            if ok:
                passing.append(str(row["value"]))
                leg = leg or via
        if not passing:
            return {"status": "below_floor", "field": field}
        return _fill_library_field(
            db, kind, soul_id, field, table, column, status_column,
            passing, entity_id=entity_id, leg=leg)
    # The strongest candidate first (get_fields ordering); a set-valued
    # field promotes only the candidate the floor passes for — try
    # each in order, floor per candidate (§8's disambiguation is
    # inside the floor: leg (c) fails when >1 candidate survives).
    chosen = None
    leg = None
    for row in candidates:
        ok, via = promotion_verdict(
            db, soul_id, field, row["value"], now=now,
            confirmation_response=confirmation_response,
            confirmation_fetched_id=confirmation_fetched_id,
            local_keys=local_keys, album_subject=album_subject,
            disc=disc, position=position)
        if ok:
            chosen, leg = row, via
            break
    if chosen is None:
        return {"status": "below_floor", "field": field}
    return _fill_library_field(
        db, kind, soul_id, field, table, column, status_column,
        chosen["value"], entity_id=entity_id, leg=leg)


def _fill_library_field(db: Any, kind: str, soul_id: str, field: str,
                        table: str, column: str,
                        status_column: Optional[str], value: Any, *,
                        entity_id: Any = None,
                        leg: Optional[str] = None) -> Dict[str, Any]:
    """The fill-empty-only write + mark, shared by the scalar and
    genres paths. Table/column names come only from the static
    _LIBRARY_COLUMNS map.

    The write is a compare-and-swap: the UPDATE only lands while
    the column is still empty, so two promotions racing the same
    field cannot both win — the loser's UPDATE matches no row, it
    reports already_filled, and it writes NO mark (a mark naming a
    value the library does not hold would mislabel the winner's).
    The fill and the mark are written on this one connection and
    committed together: a crash between them is impossible, and if
    the mark write itself fails the fill rolls back with it."""
    ensure_schema(db)
    with db._get_connection() as conn:
        columns = _table_columns(conn, table)
        if column not in columns or \
                (status_column and status_column not in columns):
            # The static map names a column this table does not
            # carry (tracks / discogs_id): the promotion has
            # nowhere to write, so it refuses — it never raises
            # against a missing column, and it writes no mark.
            return {"status": "no_library_column", "field": field}
        if entity_id is None:
            row = conn.execute(
                f"SELECT id, {column} FROM {table} WHERE soul_id = ?",
                (soul_id,)).fetchone()
        else:
            row = conn.execute(
                f"SELECT id, {column} FROM {table} WHERE id = ?",
                (entity_id,)).fetchone()
        if row is None:
            return {"status": "no_library_row", "field": field}
        row_id, current = row[0], row[1]
        if not _is_empty(current, field):
            return {"status": "already_filled", "field": field}
        stored = (json.dumps(value, ensure_ascii=False)
                  if field == "genres" else value)
        sets = f"{column} = ?, updated_at = CURRENT_TIMESTAMP"
        params: List[Any] = [stored]
        if status_column:
            sets += f", {status_column} = 'matched'"
        guard = (f"({column} IS NULL OR TRIM({column}) = '' "
                 f"OR {column} = '[]')" if field == "genres"
                 else f"({column} IS NULL OR {column} = 0 "
                      f"OR TRIM({column}) = '')")
        cur = conn.execute(
            f"UPDATE {table} SET {sets} WHERE id = ? AND {guard}",
            (*params, row_id))
        if cur.rowcount == 0:
            conn.rollback()
            return {"status": "already_filled", "field": field}
        record_mark(db, kind, soul_id, field, value,
                    via="promoted", conn=conn)
        conn.commit()
    return {"status": "promoted", "field": field, "leg": leg,
            "value": value}


def _is_empty(current: Any, field: str) -> bool:
    if current is None:
        return True
    if field == "genres":
        if isinstance(current, str):
            try:
                parsed = json.loads(current)
            except ValueError:
                return not current.strip()
            return not parsed
        return not current
    if isinstance(current, str):
        return current.strip() in ("", "0")
    return current == 0


# ---------------------------------------------------------------------------
# Invalidation pushes (node -> SoulSync) and reconcile (SoulSync -> node)
# ---------------------------------------------------------------------------

def apply_invalidation(db: Any, payload: Any,
                       now: Optional[datetime] = None) -> Dict[str, Any]:
    """Apply one invalidation push body. Two shapes:

    {"kind": "subjects", "subjects": [...], "reason": "..."}
    {"kind": "fields", "items": [{"subject", "field", "value"?}],
     "reason": "..."}

    Flag-only: rows are marked retracted / entries invalidated, and
    promotion from them stops at once. Library records and file tags
    are never touched here (§8) — a promoted value that came from a
    retracted field keeps living in the library; the mark table
    keeps saying where it came from.
    """
    now = now or _utcnow()
    if not isinstance(payload, dict):
        return {"status": "bad_payload"}
    ensure_schema(db)
    reason = str(payload.get("reason") or "node_push")[:200]
    kind = payload.get("kind")
    with db._get_connection() as conn:
        if kind == "subjects":
            subjects = payload.get("subjects") or []
            count = 0
            for subject in subjects:
                if not isinstance(subject, str):
                    continue
                cur = conn.execute(
                    """UPDATE metasync_depot_entries
                       SET invalidated = 1, invalidated_at = ?,
                           invalidation_reason = ?
                       WHERE subject = ?""",
                    (_iso(now), reason, subject))
                count += cur.rowcount
                conn.execute(
                    "UPDATE metasync_depot_fields SET retracted = 1 "
                    "WHERE subject = ?", (subject,))
            conn.commit()
            return {"status": "ok", "invalidated_entries": count}
        if kind == "fields":
            items = payload.get("items") or []
            count = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                subject, field = item.get("subject"), item.get("field")
                if not isinstance(subject, str) or not isinstance(field, str):
                    continue
                if "value" in item:
                    cur = conn.execute(
                        """UPDATE metasync_depot_fields SET retracted = 1
                           WHERE subject = ? AND field = ? AND value_key = ?""",
                        (subject, field, _value_key(_clean(item["value"]))))
                else:
                    cur = conn.execute(
                        """UPDATE metasync_depot_fields SET retracted = 1
                           WHERE subject = ? AND field = ?""",
                        (subject, field))
                count += cur.rowcount
            conn.commit()
            return {"status": "ok", "retracted_fields": count}
    return {"status": "bad_payload"}


def apply_update_push(db: Any, payload: Any,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """Apply an update push: {"records": [projection, ...]} — each is
    stored exactly as if SoulSync had just fetched it (a re-merge
    result after an invalidation, a fresh verdict, a superseding
    answer)."""
    if not isinstance(payload, dict):
        return {"status": "bad_payload"}
    records = payload.get("records") or []
    if not isinstance(records, list):
        return {"status": "bad_payload"}
    stored = 0
    for record in records[:MAX_PUSH_RECORDS]:
        if store_record(db, record, now=now):
            stored += 1
    return {"status": "ok", "stored": stored}


# ---------------------------------------------------------------------------
# Node client — SoulSync -> node (the pairing that already exists for SIN)
# ---------------------------------------------------------------------------

class NodeUnreachable(RuntimeError):
    """The MetaSync node did not answer; the depot works without it."""


def _node_config(config_get: Callable[..., Any]) -> Tuple[str, str]:
    node_url = str(config_get("metasync.node_url", "") or "").strip().rstrip("/")
    local_key = str(config_get("metasync.local_api_key", "") or "").strip()
    return node_url, local_key


# -- node-call circuit breaker ------------------------------------------------
# Process-local by design: the tax this avoids is per-process work
# (the tagging pipeline re-asking a dead node once per file). Keyed
# by node URL so a reconfigured node is a fresh circuit.
_node_failure_until: Dict[str, float] = {}
_node_failure_lock = threading.Lock()


def _node_circuit_open(node_url: str) -> bool:
    with _node_failure_lock:
        until = _node_failure_until.get(node_url, 0.0)
    return time.monotonic() < until


def _note_node_failure(node_url: str) -> None:
    with _node_failure_lock:
        _node_failure_until[node_url] = (
            time.monotonic() + NODE_FAILURE_COOLDOWN_SECONDS)


def _note_node_success(node_url: str) -> None:
    with _node_failure_lock:
        _node_failure_until.pop(node_url, None)


# -- "node doesn't hold it" memo ----------------------------------------------
# A found:false answer stores an entry with no fields, so the
# tagging consult's have-anything check stays false and every file
# in a batch for the same missing subject re-asks the node. The
# memo remembers recent misses per (node, kind, subject) for a
# short TTL — long enough to cover a batch, short enough that a
# subject the network learns about mid-hour is only delayed, never
# hidden. Process-local, like the circuit breaker.
MISS_MEMO_TTL_SECONDS = 300
_node_miss_until: Dict[Tuple[str, str, str], float] = {}
_node_miss_lock = threading.Lock()


def _miss_memoized(node_url: str, kind: str, subject: str) -> bool:
    with _node_miss_lock:
        until = _node_miss_until.get((node_url, kind, subject), 0.0)
    return time.monotonic() < until


def _note_node_miss(node_url: str, kind: str, subject: str) -> None:
    with _node_miss_lock:
        _node_miss_until[(node_url, kind, subject)] = (
            time.monotonic() + MISS_MEMO_TTL_SECONDS)


def _note_node_hit(node_url: str, kind: str, subject: str) -> None:
    with _node_miss_lock:
        _node_miss_until.pop((node_url, kind, subject), None)


def query_node(config_get: Callable[..., Any], kind: str,
               subjects: List[str]) -> List[Dict[str, Any]]:
    """One exact ask through the node (POST /local/meta/query).
    Returns the projected records (found or not). Raises
    NodeUnreachable on transport failure or a refusal — and, while
    the circuit breaker is in its cooldown after a transport
    failure, without making the call at all."""
    node_url, local_key = _node_config(config_get)
    if not node_url or not local_key:
        raise NodeUnreachable("MetaSync is not connected")
    if _node_circuit_open(node_url):
        raise NodeUnreachable(
            f"MetaSync at {node_url} recently failed; in cooldown")
    try:
        resp = requests.post(
            f"{node_url}/local/meta/query",
            json={"kind": kind, "mode": "exact", "subjects": subjects},
            headers={"Authorization": f"Bearer {local_key}",
                     "Accept": "application/json"},
            timeout=NODE_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        _note_node_failure(node_url)
        raise NodeUnreachable(f"MetaSync is unreachable at {node_url}: {e}") from e
    if resp.status_code != 200:
        raise NodeUnreachable(
            f"MetaSync query failed with HTTP {resp.status_code}")
    payload = resp.json()
    if not payload.get("ok"):
        raise NodeUnreachable(
            f"MetaSync query refused: {payload.get('error') or 'unknown error'}")
    _note_node_success(node_url)
    records = payload.get("records")
    return records if isinstance(records, list) else []


def fetch_node_record(config_get: Callable[..., Any],
                      subject: str) -> Optional[Dict[str, Any]]:
    """One subject's current merged view from the node (GET
    /local/meta/record/<subject>) — no relay traffic on the node's
    side. None when the node knows nothing about the subject. The
    subject is URL-quoted: subjects are validated only by prefix at
    ingest, so a planted one containing ``?``, ``#`` or ``/`` must
    not be able to reshape the request."""
    node_url, local_key = _node_config(config_get)
    if not node_url or not local_key:
        raise NodeUnreachable("MetaSync is not connected")
    if _node_circuit_open(node_url):
        raise NodeUnreachable(
            f"MetaSync at {node_url} recently failed; in cooldown")
    try:
        resp = requests.get(
            f"{node_url}/local/meta/record/{quote(subject, safe='')}",
            headers={"Authorization": f"Bearer {local_key}",
                     "Accept": "application/json"},
            timeout=NODE_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        _note_node_failure(node_url)
        raise NodeUnreachable(f"MetaSync is unreachable at {node_url}: {e}") from e
    if resp.status_code == 404:
        _note_node_success(node_url)
        return None
    if resp.status_code != 200:
        raise NodeUnreachable(
            f"MetaSync record fetch failed with HTTP {resp.status_code}")
    _note_node_success(node_url)
    payload = resp.json()
    if not isinstance(payload, dict):
        return None
    # the node wraps the projection: {"ok": true, "record": {...}}
    # (node app.py local_meta_record). a bare record is accepted too.
    if "record" in payload:
        record = payload.get("record")
        return record if isinstance(record, dict) else None
    return payload


def collect_subjects(db: Any, config_get: Callable[..., Any],
                     subjects_by_kind: Dict[str, List[str]],
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ask the node about the given subjects and deposit every
    record it returns. This is the everyday workhorse (§1):
    SoulSync already knows the IDs of what it is tagging, so the
    question is exact, and each answer lands here ready for the
    next run. Subjects the node recently answered found:false for
    are skipped (the miss memo): a batch of files for one missing
    subject costs one ask, not one per file."""
    node_url, _key = _node_config(config_get)
    stored = 0
    for kind, subjects in subjects_by_kind.items():
        ask = [s for s in subjects
               if not _miss_memoized(node_url, kind, s)]
        if not ask:
            continue
        returned = set()
        for record in query_node(config_get, kind, ask):
            if store_record(db, record, now=now):
                stored += 1
            if isinstance(record, dict) and record.get("subject"):
                returned.add(record["subject"])
                if record.get("found"):
                    _note_node_hit(node_url, kind, record["subject"])
                else:
                    _note_node_miss(node_url, kind, record["subject"])
        for subject in ask:
            if subject not in returned:
                # No record at all is also a miss — the node holds
                # nothing under that subject right now.
                _note_node_miss(node_url, kind, subject)
    return {"status": "ok", "stored": stored}


def reconcile(db: Any, config_get: Callable[..., Any], *,
              limit: int = 500,
              time_budget_seconds: Optional[float] = None,
              now: Optional[datetime] = None) -> Dict[str, Any]:
    """Reconnect reconcile (§5): SoulSync presents the claim
    identities its depot holds — at SoulSync's granularity that is
    its depot subjects with their field values — and the node's
    current merged view answers which are still live. Dead subjects
    are invalidated; live ones are re-stored from the node's view,
    which drops fields whose claims died and refreshes the rest.

    Runs when SoulSync (re)connects to the node: the daily MetaSync
    automation calls it after a successful charts refresh, and it is
    exposed on POST /api/metasync/depot/reconcile.

    Two bounds keep the pass honest. Invalidated entries are NOT
    in the rotation: an entry the depot has already flagged dead
    was confirmed dead (by push or by an earlier pass), re-storing
    is the only thing that may resurrect it, and letting dead
    entries fill the window would starve live subjects of coverage
    once enough of them pile up at the front of the fetched_at
    order. And the pass runs against a wall-clock budget
    (``time_budget_seconds``, default RECONCILE_TIME_BUDGET_SECONDS)
    as well as the subject limit: subjects past the budget are
    simply not checked this run — the result says so — and rotate
    to the front next run because refreshed entries move to the
    back of the fetched_at order.

    A subject whose answer has a shape store_record rejects is
    rotated the same way (fetched_at bumped, rejects counted in
    the result): left in place it would head the window forever
    and starve every subject behind it. Its stored candidates are
    untouched — an unreadable shape is not evidence anything died.
    """
    now = now or _utcnow()
    if time_budget_seconds is None:
        time_budget_seconds = RECONCILE_TIME_BUDGET_SECONDS
    ensure_schema(db)
    with db._get_connection() as conn:
        subjects = [r[0] for r in conn.execute(
            "SELECT subject FROM metasync_depot_entries "
            "WHERE invalidated = 0 "
            "ORDER BY fetched_at ASC LIMIT ?", (limit,)).fetchall()]
    deadline = time.monotonic() + time_budget_seconds
    refreshed = 0
    dropped = 0
    rejected = 0
    checked = 0
    for subject in subjects:
        if checked and time.monotonic() >= deadline:
            break
        record = fetch_node_record(config_get, subject)
        checked += 1
        if record is None:
            apply_invalidation(
                db, {"kind": "subjects", "subjects": [subject],
                     "reason": "reconcile: node no longer holds it"},
                now=now)
            dropped += 1
        elif store_record(db, record, now=now):
            refreshed += 1
        else:
            # The node answered with a shape the depot cannot
            # hold. Leaving the entry untouched would pin it at
            # the front of the fetched_at window forever — the
            # same subjects re-fetched (and re-rejected) every
            # run, starving everything behind them. Bump
            # fetched_at so it rotates to the back like a checked
            # entry, and count consecutive rejects; the entry's
            # stored candidates are NOT touched (a shape this
            # build can't read is not evidence the claims died).
            rejected += 1
            with db._get_connection() as conn:
                conn.execute(
                    "UPDATE metasync_depot_entries SET fetched_at = ?, "
                    "reconcile_rejects = reconcile_rejects + 1 "
                    "WHERE subject = ?", (_iso(now), subject))
                conn.commit()
    return {"status": "ok", "checked": checked,
            "refreshed": refreshed, "dropped": dropped,
            "rejected": rejected,
            "budget_exhausted": checked < len(subjects)}
