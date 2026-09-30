"""the record labels the label explorer shelf is built from.

it used to be ``SELECT DISTINCT label FROM albums LIMIT 30``: whichever 30
labels sqlite happened to hit first. on boulder's library that was zalgo-text
labels and seven "$EBU & someone" variants, so the shelf showed albums from
labels nobody asked about. now it's the labels you actually play, and when
nothing has been played yet (a fresh install), the ones you own most of.
"""

from typing import List

DEFAULT_LIMIT = 30


def _clean(labels) -> List[str]:
    # grouped by exact spelling in sql, since the metadata cache matches labels
    # exactly; case variants fold here, the better-ranked spelling wins
    out, seen = [], set()
    for (label,) in labels:
        name = (label or '').strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def your_labels(conn, limit: int = DEFAULT_LIMIT) -> List[str]:
    """labels ranked by plays across their tracks, then by albums owned."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT TRIM(al.label) FROM tracks t JOIN albums al ON al.id = t.album_id
        WHERE t.play_count > 0 AND al.label IS NOT NULL AND TRIM(al.label) != ''
        GROUP BY TRIM(al.label)
        ORDER BY SUM(t.play_count) DESC, COUNT(DISTINCT al.id) DESC
        LIMIT ?
        """,
        (limit * 2,),
    )
    labels = _clean(cur.fetchall())[:limit]
    if len(labels) >= limit:
        return labels
    # not enough listening yet: fill from the labels you own the most of
    cur.execute(
        """
        SELECT TRIM(label) FROM albums
        WHERE label IS NOT NULL AND TRIM(label) != ''
        GROUP BY TRIM(label)
        ORDER BY COUNT(*) DESC
        LIMIT ?
        """,
        (limit * 2,),
    )
    have = {x.lower() for x in labels}
    for name in _clean(cur.fetchall()):
        if len(labels) >= limit:
            break
        if name.lower() not in have:
            labels.append(name)
            have.add(name.lower())
    return labels
