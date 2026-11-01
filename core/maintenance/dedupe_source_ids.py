"""Find and clear corrupted source-id assignments in Library v2.

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

SOURCES = ('deezer', 'spotify', 'itunes', 'musicbrainz', 'discogs',
           'audiodb', 'qobuz', 'tidal')


def _norm(name: str) -> str:
    """Loose name key — lowercased, whitespace-collapsed."""
    return ' '.join((name or '').lower().split())


def find_corrupt_clusters(database: Any) -> list[dict]:
    """Return corrupt source-id clusters across every known source column.

    Each cluster is a dict: ``{source, id_column, status_column, source_id,
    members: [(artist_id, name), ...]}``. A cluster is corrupt when one id is
    held by artists with more than one distinct (normalized) name.
    """
    from core.library2.provider_ids import provider_id_sql

    clusters: list[dict] = []
    with database._get_connection() as conn:
        for source in SOURCES:
            id_col = provider_id_sql(source)
            rows = conn.execute(
                f"SELECT {id_col}, id, name FROM lib2_artists "
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
                source = c['source']
                if source in ('spotify', 'musicbrainz'):
                    conn.execute(
                        f"UPDATE lib2_artists SET {source}_id=NULL "
                        f"WHERE id IN ({placeholders})", ids)
                else:
                    conn.execute(
                        f"UPDATE lib2_artists SET external_ids=json_remove(external_ids, ?) "
                        f"WHERE id IN ({placeholders})", [f'$.{source}', *ids])
                conn.execute(
                    f"DELETE FROM lib2_provider_attempts WHERE entity_type='artist' "
                    f"AND service=? AND entity_id IN ({placeholders})", [source, *ids])
            conn.commit()
        logger.info(
            f"Cleared {report['artist_count']} corrupt source ids across "
            f"{report['cluster_count']} clusters — re-run enrichment to "
            f"re-derive them correctly"
        )

    return report


def repair_imported_state(database: Any) -> dict:
    """Apply one-time corruption repairs after legacy rows reached Library v2."""
    with database._get_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lib2_artists'"
        ).fetchone() is None:
            return {'skipped': 'no_catalogue'}
        conn.execute(
            "CREATE TABLE IF NOT EXISTS lib2_upgrade_repairs "
            "(name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        markers = {row[0] for row in conn.execute("SELECT name FROM lib2_upgrade_repairs")}
        conn.commit()
    report = clear_corrupt_source_ids(database, dry_run=False)
    with database._get_connection() as conn:
        if 'lib2_genius_search_fix' not in markers:
            for table in ('lib2_artists', 'lib2_tracks'):
                conn.execute(
                    f"UPDATE {table} SET external_ids=json_remove(external_ids,'$.genius'), "
                    "enrichment=json_remove(enrichment,'$.genius')"
                )
            conn.execute("DELETE FROM lib2_provider_attempts WHERE service='genius'")
            conn.execute("INSERT INTO lib2_upgrade_repairs(name) VALUES('lib2_genius_search_fix')")
            report['genius_reset'] = True
        if 'lib2_soulid_v2_migration' not in markers:
            report['soul_ids_cleared'] = conn.execute(
                "UPDATE lib2_artists SET soul_id=NULL WHERE soul_id IS NOT NULL"
            ).rowcount
            conn.execute("INSERT INTO lib2_upgrade_repairs(name) VALUES('lib2_soulid_v2_migration')")
        conn.commit()
    return report
