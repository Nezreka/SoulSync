"""Reconcile provider IDs embedded in audio files into the library DB.

Enrichment workers (Spotify / iTunes / MusicBrainz / Deezer / Tidal /
AudioDB / Genius / Last.fm) resolve each artist / album / track to a provider ID
via API calls, gating their work queues on ``{provider}_match_status IS
NULL``. But files that SoulSync (or MusicBrainz Picard) already tagged
carry those IDs in their metadata. Reading them back and gap-filling the
``{provider}_id`` + ``{provider}_match_status = 'matched'`` columns lets
the workers skip the API lookup entirely — large API savings on an
already-tagged library.

Gap-fill only: lib2's ``claim_provider_id`` writes an id only while the
column is empty, in one statement, so an enrichment worker that matched the
entity meanwhile keeps its value; a DISAGREEING embedded id is counted as a
conflict. The artist a file's ``*_artist_id`` tag names is resolved through
``lib2_track_artists`` -- that tag describes who performed the TRACK, so on a
compilation or a featured track it is not the album's primary artist and must
not be written there.

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
# match-status column). Only the tag, the entity and the service (the status
# column's prefix) are read now that every write goes through lib2.
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
    ('jiosaavn_track_id',    'track',  'jiosaavn_id',          'jiosaavn_match_status'),
    ('jiosaavn_album_id',    'album',  'jiosaavn_id',          'jiosaavn_match_status'),
    ('jiosaavn_artist_id',   'artist', 'jiosaavn_id',          'jiosaavn_match_status'),
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


def _clean(value: Any) -> Optional[str]:
    """Normalise a tag/column value to a non-empty stripped string or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _single_value(value: Any) -> Optional[str]:
    """One embedded tag value, or None when the tag holds a LIST.

    ``read_embedded_tags`` flattens a multi-valued frame by joining it with
    ``", "``, which is exactly what a featured-artist file does to
    ``musicbrainz_artistid``: "A feat. B" carries both performers' ids. None of
    the fields this module fills can hold two ids, so a joined value is not a
    smaller version of the answer — it is a different kind of value, and
    storing it would write an id no provider will ever resolve.
    """
    text = _clean(value)
    if text is None or ", " in text:
        return None
    return text


@dataclass
class ReconcileTotals:
    """Accumulated counts over a reconcile run."""
    total: int = 0
    processed: int = 0
    ids_filled: int = 0
    entities_updated: int = 0
    conflicts: int = 0
    unreadable: int = 0


def reconcile_library(
    conn,
    read_tags,
    track_ids=None,
    page_size: int = 500,
    on_progress=None,
    should_stop=None,
    server_source=None,
) -> ReconcileTotals:
    """Gap-fill embedded provider IDs into the DB for a set of tracks.

    Shared orchestration used by both the manual backfill job and the
    auto-reconcile hook on library scans. Pages the track list (bounded
    memory), lazily loads only the parent album/artist rows actually
    referenced (cheap when scoped to a handful of new tracks), and commits
    per page so concurrent enrichment workers aren't starved of the write
    lock.

    Args:
        conn: open DB connection; this function commits per page.
        read_tags: callable ``(file_path) -> tags dict | None``. The caller
            injects path resolution + ``read_embedded_tags`` so this module
            stays free of Flask / docker-path concerns. ``None`` => unreadable.
        track_ids: iterable of track ids to reconcile, or ``None`` for every
            track that has a ``file_path``.
        page_size: rows materialised per page.
        on_progress: optional ``(totals, current_title) -> None`` after each
            track (for live UI).
        should_stop: optional ``() -> bool`` checked between tracks/pages to
            abort early.

    Returns:
        :class:`ReconcileTotals`.
    """
    from core.library2.provider_attempts import record_attempt
    from core.library2.worker_support import stored_provider_id
    from core.library2.provider_writes import claim_provider_id

    totals = ReconcileTotals()
    cur = conn.cursor()
    if track_ids is None:
        ids = [row[0] for row in cur.execute(
            "SELECT t.id FROM lib2_tracks t WHERE EXISTS (SELECT 1 FROM lib2_track_files f "
            "WHERE f.track_id=t.id AND COALESCE(f.file_state,'active')<>'deleted')")]
        by_server = False
    else:
        ids = [str(value) for value in track_ids if value is not None]
        by_server = bool(server_source)
    totals.total = len(ids)
    touched = set()
    for start in range(0, len(ids), page_size):
        if should_stop and should_stop():
            break
        page = ids[start:start + page_size]
        marks = ','.join('?' * len(page))
        where = (
            f"(EXISTS (SELECT 1 FROM lib2_media_server_mappings m "
            f"WHERE m.entity_type='track' AND m.entity_id=t.id "
            f"AND m.server_source=? AND m.server_id IN ({marks})) "
            f"OR (t.server_source=? AND t.server_id IN ({marks})))"
            if by_server else f"t.id IN ({marks})"
        )
        params = (
            [server_source, *page, server_source, *page]
            if by_server else page
        )
        rows = cur.execute(f"""
            SELECT t.id, t.album_id, t.title,
                   al.primary_artist_id AS album_artist_id,
                   -- The artist an `*_artist_id` tag names is the one who
                   -- PERFORMED this track, which on a compilation or a
                   -- featured track is not the album's primary artist.
                   (SELECT ta.artist_id FROM lib2_track_artists ta
                     WHERE ta.track_id=t.id
                     ORDER BY CASE WHEN ta.role='primary' THEN 0 ELSE 1 END,
                              ta.position, ta.artist_id LIMIT 1) AS credit_artist_id,
                   (SELECT f.path FROM lib2_track_files f WHERE f.track_id=t.id
                     AND COALESCE(f.file_state,'active')<>'deleted'
                     ORDER BY f.is_primary DESC, f.id LIMIT 1) AS file_path
              FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
             WHERE {where}
        """, params).fetchall()
        for row in rows:
            if should_stop and should_stop():
                break
            try:
                tags = read_tags(row['file_path'])
                if not tags:
                    totals.unreadable += 1
                    continue
                entity_ids = {
                    'track': row['id'],
                    'album': row['album_id'],
                    # Fall back to the album's artist only where the track has
                    # no credit of its own; a row with credits is answered by
                    # them, so the guest on someone else's record can no longer
                    # stamp their provider id onto the album artist.
                    'artist': row['credit_artist_id'] or row['album_artist_id'],
                }
                for tag, entity, _column, status_column in _RECONCILE_FIELDS:
                    value = _single_value(tags.get(tag))
                    if not value:
                        continue
                    service = status_column.removesuffix('_match_status')
                    entity_id = entity_ids[entity]
                    if entity_id is None:
                        continue
                    existing = stored_provider_id(conn, entity, entity_id, service)
                    if existing:
                        totals.conflicts += int(existing != value)
                        continue
                    # Guarded write, not a plain one: an enrichment worker may
                    # have settled this entity between the read above and now,
                    # and a gap-fill must never replace what it found.
                    if not claim_provider_id(conn, entity_type=entity,
                                             entity_id=entity_id, service=service,
                                             provider_id=value):
                        winner = stored_provider_id(conn, entity, entity_id, service)
                        totals.conflicts += int(bool(winner) and winner != value)
                        continue
                    record_attempt(conn, entity_type=entity, entity_id=entity_id,
                                   service=service, status='matched')
                    totals.ids_filled += 1
                    touched.add((entity, entity_id))
            except Exception:
                totals.unreadable += 1
            finally:
                totals.processed += 1
                if on_progress:
                    on_progress(totals, row['title'])
        conn.commit()
    totals.entities_updated = len(touched)
    return totals




