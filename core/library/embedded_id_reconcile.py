"""Reconcile provider IDs embedded in audio files into the library DB.

Enrichment workers (Spotify / iTunes / MusicBrainz / Deezer / Tidal /
AudioDB / Genius / Last.fm) resolve each artist / album / track to a provider ID
via API calls, gating their work queues on ``{provider}_match_status IS
NULL``. But files that SoulSync (or MusicBrainz Picard) already tagged
carry those IDs in their metadata. Reading them back and gap-filling the
``{provider}_id`` + ``{provider}_match_status = 'matched'`` columns lets
the workers skip the API lookup entirely — large API savings on an
already-tagged library.

Split into a PURE planning layer and a thin DB apply layer:

- :func:`plan_reconcile` takes the tags read from ONE file (via
  ``core.library.file_tags.read_embedded_tags``) plus the current IDs of
  that file's track + its parent album + artist, and produces the list of
  :class:`Fill` operations to perform. It is gap-fill only: a provider id
  that already has a value is never planned for change; a DISAGREEING
  embedded id is reported as a conflict instead.

- :func:`apply_reconcile_plan` writes a plan, one guarded ``UPDATE`` per
  id column: ``WHERE id = ? AND ({id_col} IS NULL OR {id_col} = '')``.
  The guard makes the gap-fill ATOMIC — even if an enrichment worker
  matched the same entity between the plan's read and this write, the
  fill simply affects 0 rows instead of clobbering the worker's value.
  Columns are introspected first so a schema version missing a provider's
  columns is skipped, not errored.

Scope note: the MusicBrainz *recording* (track) ID is intentionally not
reconciled — on ID3 it lives in a ``UFID`` frame the shared reader
doesn't surface and the Vorbis ``musicbrainz_trackid`` convention is
format-ambiguous. MB *album* and *artist* IDs (which drive most worker
API calls) ARE reconciled, as are the clean per-provider track/album/
artist IDs of the other services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Each entry: (embedded-tag key from read_embedded_tags, entity, id column,
# match-status column). The id columns mirror web_server._SERVICE_ID_COLUMNS;
# they're spelled out here so this module stays importable without the Flask
# app. Single-column providers (deezer/tidal/audiodb/genius) reuse one id
# column across entity types — that's fine, fills are keyed by (entity, col).
_RECONCILE_FIELDS = (
    ('spotify_track_id',     'track',  'spotify_track_id',     'spotify_match_status'),
    ('spotify_album_id',     'album',  'spotify_album_id',     'spotify_match_status'),
    ('spotify_artist_id',    'artist', 'spotify_artist_id',    'spotify_match_status'),
    ('itunes_track_id',      'track',  'itunes_track_id',      'itunes_match_status'),
    ('itunes_album_id',      'album',  'itunes_album_id',      'itunes_match_status'),
    ('itunes_artist_id',     'artist', 'itunes_artist_id',     'itunes_match_status'),
    ('musicbrainz_albumid',  'album',  'musicbrainz_release_id', 'musicbrainz_match_status'),
    ('musicbrainz_artistid', 'artist', 'musicbrainz_id',       'musicbrainz_match_status'),
    ('deezer_track_id',      'track',  'deezer_id',            'deezer_match_status'),
    ('deezer_album_id',      'album',  'deezer_id',            'deezer_match_status'),
    ('deezer_artist_id',     'artist', 'deezer_id',            'deezer_match_status'),
    ('tidal_track_id',       'track',  'tidal_id',             'tidal_match_status'),
    ('tidal_album_id',       'album',  'tidal_id',             'tidal_match_status'),
    ('tidal_artist_id',      'artist', 'tidal_id',             'tidal_match_status'),
    ('audiodb_track_id',     'track',  'audiodb_id',           'audiodb_match_status'),
    ('audiodb_album_id',     'album',  'audiodb_id',           'audiodb_match_status'),
    ('audiodb_artist_id',    'artist', 'audiodb_id',           'audiodb_match_status'),
    ('genius_track_id',      'track',  'genius_id',            'genius_match_status'),
    # Last.fm embeds a single LASTFM_URL — sourced from get_track_info(), so it
    # is the TRACK's url. Map to tracks.lastfm_url only (artist/album last.fm
    # urls are different urls and aren't carried in the file).
    ('lastfm_url',           'track',  'lastfm_url',           'lastfm_match_status'),
)

_ENTITIES = ('track', 'album', 'artist')
_ENTITY_TABLE = {'track': 'tracks', 'album': 'albums', 'artist': 'artists'}


@dataclass(frozen=True)
class Fill:
    """One provider-id column to gap-fill on one entity."""
    entity: str          # 'track' | 'album' | 'artist'
    id_column: str       # e.g. 'spotify_artist_id'
    status_column: str   # e.g. 'spotify_match_status'
    value: str           # the embedded id to write


@dataclass
class ReconcilePlan:
    """The outcome of planning one file against its current DB rows.

    ``fills`` are the gap-fill operations to apply (empty id columns only).
    ``already_present`` counts embedded ids that matched a value already
    stored (no-op). ``conflicts`` lists embedded ids that DISAGREE with a
    stored value — never applied, surfaced for review.
    """

    fills: List[Fill] = field(default_factory=list)
    already_present: int = 0
    conflicts: List[Dict[str, str]] = field(default_factory=list)

    @property
    def filled(self) -> int:
        return len(self.fills)

    @property
    def has_updates(self) -> bool:
        return bool(self.fills)

    def fills_for(self, entity: str) -> List[Fill]:
        return [f for f in self.fills if f.entity == entity]


@dataclass
class ReconcileApplied:
    """Counts from actually writing a plan (based on real ``rowcount``)."""
    rows_updated: int = 0    # distinct entity rows touched
    ids_filled: int = 0      # id columns that actually landed (guard passed)


def _clean(value: Any) -> Optional[str]:
    """Normalise a tag/column value to a non-empty stripped string or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def plan_reconcile(
    embedded_tags: Optional[Dict[str, Any]],
    current_ids: Optional[Dict[str, Dict[str, Any]]],
) -> ReconcilePlan:
    """Plan which provider-ID columns to gap-fill from one file's tags.

    Args:
        embedded_tags: the ``tags`` dict from ``read_embedded_tags`` (flat
            ``friendly_key -> value``). ``None`` / empty yields an empty plan.
        current_ids: ``{'track': {...}, 'album': {...}, 'artist': {...}}``
            where each inner dict holds the entity's CURRENT column values
            (at minimum the id columns this module touches). Missing
            entities / keys are treated as empty (eligible to fill).

    Returns:
        A :class:`ReconcilePlan`. Gap-fill only — an id column with any
        existing value is never planned; a disagreeing embedded id is
        recorded in ``conflicts``.
    """
    plan = ReconcilePlan()
    tags = embedded_tags or {}
    current = current_ids or {}
    queued: Dict[tuple, str] = {}  # (entity, id_col) already queued this pass

    for embedded_key, entity, id_col, status_col in _RECONCILE_FIELDS:
        new_val = _clean(tags.get(embedded_key))
        if not new_val:
            continue

        row = current.get(entity) or {}
        existing = _clean(row.get(id_col))
        if existing is not None:
            if existing != new_val:
                plan.conflicts.append({
                    'entity': entity, 'column': id_col,
                    'existing': existing, 'embedded': new_val,
                })
            else:
                plan.already_present += 1
            continue

        key = (entity, id_col)
        if key in queued:
            # A single-column provider already queued this id col this pass.
            if queued[key] != new_val:
                plan.conflicts.append({
                    'entity': entity, 'column': id_col,
                    'existing': queued[key], 'embedded': new_val,
                })
            continue

        queued[key] = new_val
        plan.fills.append(Fill(entity, id_col, status_col, new_val))

    return plan


@dataclass
class TrackReconcileResult:
    """Outcome of reconciling one track row against its file's tags."""
    applied: 'ReconcileApplied'
    conflicts: int = 0
    readable: bool = True   # False when the file's tags couldn't be read


def reconcile_track_row(
    cursor,
    track_row: Dict[str, Any],
    album_map: Dict[str, Dict[str, Any]],
    artist_map: Dict[str, Dict[str, Any]],
    embedded_tags: Optional[Dict[str, Any]],
) -> TrackReconcileResult:
    """Reconcile one track row + its parent album/artist against one file.

    Pure orchestration over :func:`plan_reconcile` / :func:`apply_reconcile_plan`,
    extracted so the per-track logic (id extraction, plan→apply chaining,
    keeping the in-memory parent maps fresh for sibling tracks) is testable
    without the Flask job. ``embedded_tags`` is the ``tags`` dict from
    ``read_embedded_tags`` (``None`` => unreadable file).

    ``album_map`` / ``artist_map`` map entity-id -> current column dict; this
    function UPDATES them in place with any fills it applies so a later track
    on the same album/artist sees the value and doesn't re-plan it. (DB safety
    is the guarded UPDATE in apply, never these maps.)
    """
    if not embedded_tags:
        return TrackReconcileResult(ReconcileApplied(), 0, readable=False)

    album_id = str(track_row['album_id']) if track_row.get('album_id') is not None else None
    artist_id = str(track_row['artist_id']) if track_row.get('artist_id') is not None else None

    plan = plan_reconcile(embedded_tags, {
        'track': track_row,
        'album': album_map.get(album_id, {}) if album_id else {},
        'artist': artist_map.get(artist_id, {}) if artist_id else {},
    })
    applied = apply_reconcile_plan(cursor, {
        'track': track_row.get('id'), 'album': album_id, 'artist': artist_id,
    }, plan)

    if album_id:
        for f in plan.fills_for('album'):
            album_map.setdefault(album_id, {})[f.id_column] = f.value
    if artist_id:
        for f in plan.fills_for('artist'):
            artist_map.setdefault(artist_id, {})[f.id_column] = f.value

    return TrackReconcileResult(applied, len(plan.conflicts), readable=True)


def _existing_columns(cursor, table: str) -> set:
    """Return the set of column names on ``table`` (migration-safe guard)."""
    cursor.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cursor.fetchall()}


def apply_reconcile_plan(cursor, entity_ids: Dict[str, Any], plan: ReconcilePlan) -> ReconcileApplied:
    """Apply a :class:`ReconcilePlan` to the DB via ``cursor``.

    Each fill is a single guarded ``UPDATE``:

        UPDATE {table} SET {id}=?, {status}='matched', {attempted}=now
        WHERE id=? AND ({id} IS NULL OR {id}='')

    The ``id IS NULL OR id=''`` guard makes the gap-fill atomic: if the
    column became non-empty between the plan's read and now (an enrichment
    worker matched it concurrently), the UPDATE affects 0 rows and the
    worker's value is preserved. Only columns that exist on the table are
    written (introspected + cached per call), so a schema missing a
    provider's columns is silently skipped.

    Args:
        cursor: an open DB cursor (caller owns the transaction/commit).
        entity_ids: ``{'track': id, 'album': id, 'artist': id}``. An entity
            with no id is skipped.

    Returns:
        A :class:`ReconcileApplied` with counts derived from real rowcounts.
    """
    result = ReconcileApplied()
    touched: set = set()
    col_cache: Dict[str, set] = {}

    for fill in plan.fills:
        ent_id = entity_ids.get(fill.entity)
        if ent_id is None or ent_id == '':
            continue
        table = _ENTITY_TABLE[fill.entity]
        if table not in col_cache:
            col_cache[table] = _existing_columns(cursor, table)
        cols = col_cache[table]
        if fill.id_column not in cols:
            continue

        assignments = [f"{fill.id_column} = ?"]
        values: List[Any] = [fill.value]
        if fill.status_column in cols:
            assignments.append(f"{fill.status_column} = ?")
            values.append('matched')
            attempted = fill.status_column.replace('_match_status', '_last_attempted')
            if attempted in cols:
                assignments.append(f"{attempted} = CURRENT_TIMESTAMP")

        cursor.execute(
            f"UPDATE {table} SET {', '.join(assignments)} "
            f"WHERE id = ? AND ({fill.id_column} IS NULL OR {fill.id_column} = '')",
            values + [str(ent_id)],
        )
        if cursor.rowcount:
            result.ids_filled += 1
            touched.add((fill.entity, str(ent_id)))

    result.rows_updated = len(touched)
    return result
