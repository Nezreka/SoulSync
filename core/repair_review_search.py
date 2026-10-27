"""Search names people see in an album review, without searching technical evidence."""
from __future__ import annotations

import json
import re
import unicodedata


def normalize(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.sub(r'[^\w]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).split())


def names(title, raw):
    try:
        detail = json.loads(raw or '{}')
    except (ValueError, TypeError):
        detail = {}
    values = [title]
    fields = ('album_title', 'db_album_title', 'album_name', 'album', 'artist_name',
              'artist', 'expected_artist', 'expected_title', 'title', 'current_title', 'local_title', 'slot_title', 'name')
    def collect(item):
        if isinstance(item, dict):
            values.extend(item.get(key) for key in fields if isinstance(item.get(key), str))
    collect(detail)
    for key in ('current_tags', 'expected_tags', 'release_track', 'selected_release', 'replacement_track'):
        collect(detail.get(key))
    for key in ('missing_tracks', 'tracks', 'assignments'):
        for item in detail.get(key) or []:
            collect(item)
    return normalize(' '.join(str(value) for value in values if value))


def rank(album, artist, query):
    album, artist, query = normalize(album), normalize(artist), normalize(query)
    return (0 if album == query else 1 if album.startswith(query) else
            2 if all(token in album for token in query.split()) else
            3 if all(token in artist for token in query.split()) else 4)


def matching_cases(conn, *, status, job_id, query):
    tokens = normalize(query).split()
    conn.create_function('review_search_names', 2, names, deterministic=True)
    # Combine names across an album before matching: album and track terms may
    # live on different findings. Filtering must retain its entire action plan.
    rows = conn.execute('''SELECT review_case_key,
        group_concat(review_search_names(title, details_json), ' ')
        FROM repair_findings WHERE status=? AND (?='' OR job_id=?)
        GROUP BY review_case_key''', (status, job_id, job_id))
    return [key for key, text in rows if all(token in (text or '') for token in tokens)]
