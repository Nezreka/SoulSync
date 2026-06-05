"""Find and clear corrupted source-id assignments on the ``artists`` table.

Background
----------
The metadata enrichment workers (Deezer / AudioDB / Qobuz / Tidal) historically
"corrected" an artist's source id from an album/track match **without a name
check**. A track our library credits to one artist but which lives on another
artist's curated/compilation album (e.g. anyone featured on Kendrick Lamar's
"Black Panther" album) resolved to that album, whose primary artist is someone
else — and the worker stamped that wrong id onto our artist. The upshot: one
source id (Kendrick's Deezer ``525046``) ends up shared across several unrelated
artists.

That bug is now fixed in the workers (they name-check before correcting). This
module is the one-off repair for libraries that already got corrupted.

What counts as corruption
-------------------------
A *corrupt cluster* is one source id held by artists with **different names**.
Legitimate duplicates — the SAME artist indexed on two media servers, sharing
one id — have identical names and are left untouched.

The repair
----------
For every corrupt cluster, clear the source id AND its match-status column on
each member artist, so the (now name-checked) worker re-derives each artist's id
correctly on the next enrichment pass. Only the ``artists`` table is touched;
album/track rows keep their match status, so the album/track correction path
isn't re-run during re-enrichment.

``clear_corrupt_source_ids`` defaults to ``dry_run=True`` — it reports exactly
what it would change and writes nothing unless explicitly told to apply.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# source -> (id column, match-status column) on the ``artists`` table.
SOURCE_COLUMNS = {
    'deezer': ('deezer_id', 'deezer_match_status'),
    'spotify': ('spotify_artist_id', 'spotify_match_status'),
    'itunes': ('itunes_artist_id', 'itunes_match_status'),
    'musicbrainz': ('musicbrainz_id', 'musicbrainz_match_status'),
    'discogs': ('discogs_id', 'discogs_match_status'),
    'audiodb': ('audiodb_id', 'audiodb_match_status'),
    'qobuz': ('qobuz_id', 'qobuz_match_status'),
    'tidal': ('tidal_id', 'tidal_match_status'),
}


def _norm(name: str) -> str:
    """Loose name key — lowercased, whitespace-collapsed."""
    return ' '.join((name or '').lower().split())


def _artists_columns(conn) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(artists)")}


def find_corrupt_clusters(database: Any) -> list[dict]:
    """Return corrupt source-id clusters across every known source column.

    Each cluster is a dict: ``{source, id_column, status_column, source_id,
    members: [(artist_id, name), ...]}``. A cluster is corrupt when one id is
    held by artists with more than one distinct (normalized) name.
    """
    clusters: list[dict] = []
    with database._get_connection() as conn:
        existing = _artists_columns(conn)
        for source, (id_col, status_col) in SOURCE_COLUMNS.items():
            if id_col not in existing:
                continue
            rows = conn.execute(
                f"SELECT {id_col}, id, name FROM artists "
                f"WHERE {id_col} IS NOT NULL AND {id_col} != ''"
            ).fetchall()
            by_id: dict = {}
            for sid, aid, name in rows:
                by_id.setdefault(str(sid), []).append((aid, name))
            for sid, members in by_id.items():
                if len(members) > 1 and len({_norm(n) for _, n in members}) > 1:
                    clusters.append({
                        'source': source,
                        'id_column': id_col,
                        'status_column': status_col,
                        'source_id': sid,
                        'members': members,
                    })
    return clusters


def clear_corrupt_source_ids(database: Any, dry_run: bool = True) -> dict:
    """Clear source id + match status on every artist in a corrupt cluster.

    ``dry_run=True`` (default) writes nothing — the returned report shows
    exactly what would change so the operator can review first. Pass
    ``dry_run=False`` to apply.
    """
    clusters = find_corrupt_clusters(database)
    report = {
        'dry_run': dry_run,
        'cluster_count': len(clusters),
        'artist_count': sum(len(c['members']) for c in clusters),
        'by_source': {},
        'clusters': [],
    }
    for c in clusters:
        report['by_source'][c['source']] = (
            report['by_source'].get(c['source'], 0) + len(c['members'])
        )
        report['clusters'].append({
            'source': c['source'],
            'source_id': c['source_id'],
            'artists': sorted(n for _, n in c['members']),
        })

    if not dry_run and clusters:
        with database._get_connection() as conn:
            for c in clusters:
                ids = [aid for aid, _ in c['members']]
                placeholders = ','.join('?' for _ in ids)
                conn.execute(
                    f"UPDATE artists SET {c['id_column']} = NULL, "
                    f"{c['status_column']} = NULL WHERE id IN ({placeholders})",
                    ids,
                )
            conn.commit()
        logger.info(
            f"Cleared {report['artist_count']} corrupt source ids across "
            f"{report['cluster_count']} clusters — re-run enrichment to "
            f"re-derive them correctly"
        )

    return report
