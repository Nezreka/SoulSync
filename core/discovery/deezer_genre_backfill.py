"""Give genres to deezer tracks already sitting in the discovery pool.

the scan only started saving deezer genres now (see core/metadata/deezer_genres),
so everything it stored before has artist_genres NULL. this fills them in a
batch of artists at a time, riding the discovery scan, so a big pool catches up
over a few scans instead of hammering deezer in one go.

an artist deezer has no genres for gets '[]' written, not NULL, so it's
counted as checked and never asked about again.
"""

from __future__ import annotations

import json
from typing import Any, Callable, List

from utils.logging_config import get_logger

logger = get_logger("discovery.deezer_genre_backfill")

ARTISTS_PER_RUN = 300


def backfill_deezer_discovery_genres(
    database: Any,
    get_artist_genres: Callable[[str], List[str]],
    max_artists: int = ARTISTS_PER_RUN,
) -> dict:
    """fill artist_genres for up to max_artists deezer artists in the pool.

    returns {'artists': checked, 'with_genres': n, 'rows': rows_updated}.
    """
    with database._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT deezer_artist_id FROM discovery_pool
            WHERE source = 'deezer' AND artist_genres IS NULL
              AND deezer_artist_id IS NOT NULL AND deezer_artist_id != ''
            LIMIT ?
            """,
            (int(max_artists),),
        )
        artist_ids = [str(row[0]) for row in cursor.fetchall()]

    checked = with_genres = rows = 0
    for artist_id in artist_ids:
        try:
            genres = get_artist_genres(artist_id) or []
        except Exception as e:
            # a failed lookup stays NULL so the next run tries again
            logger.debug("deezer genre lookup failed for %s: %s", artist_id, e)
            continue
        checked += 1
        if genres:
            with_genres += 1
        with database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE discovery_pool SET artist_genres = ?
                WHERE source = 'deezer' AND deezer_artist_id = ? AND artist_genres IS NULL
                """,
                (json.dumps(genres), artist_id),
            )
            rows += cursor.rowcount or 0
            conn.commit()

    if artist_ids:
        logger.info(
            "Deezer genre backfill: %d artists checked, %d with genres, %d pool tracks updated",
            checked, with_genres, rows,
        )
    return {'artists': checked, 'with_genres': with_genres, 'rows': rows}
