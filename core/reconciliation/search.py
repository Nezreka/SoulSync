"""Search names people see in an album review, without searching technical evidence."""
from __future__ import annotations

import json
import re
import unicodedata


def normalize(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.sub(r'[^\w]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).split())


def matches(text, tokens):
    words = normalize(text).split()
    return all(any(word.startswith(token) for word in words) for token in tokens)


def names(title, raw):
    try:
        detail = json.loads(raw or '{}')
    except (ValueError, TypeError):
        detail = {}
    values = [title]
    fields = ('album_title', 'library_album_title', 'current_album_title', 'db_album_title', 'album_name', 'album', 'artist_name',
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
            2 if matches(album, query.split()) else
            3 if matches(artist, query.split()) else 4)


def prepare(conn, *, status, job_id):
    """Cache only searchable names; evidence updates invalidate their own entry.

    Triggers use ordinary SQL so writers need no connection-specific functions.
    Build outside the write transaction and compare the source again at insertion
    so a concurrent scan cannot publish stale names after its invalidation.
    """
    conn.execute('''CREATE TABLE IF NOT EXISTS repair_review_search_documents (
        finding_id INTEGER PRIMARY KEY, words TEXT NOT NULL)''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS repair_review_search_changed
        AFTER UPDATE OF title,details_json ON repair_findings
        WHEN OLD.title IS NOT NEW.title OR OLD.details_json IS NOT NEW.details_json
        BEGIN DELETE FROM repair_review_search_documents WHERE finding_id=NEW.id; END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS repair_review_search_deleted
        AFTER DELETE ON repair_findings
        BEGIN DELETE FROM repair_review_search_documents WHERE finding_id=OLD.id; END''')
    job_filter = 'AND f.job_id=?' if job_id else ''
    params = (status, job_id) if job_id else (status,)
    rows = conn.execute(f'''SELECT f.id,f.title,f.details_json FROM repair_findings f
        LEFT JOIN repair_review_search_documents d ON d.finding_id=f.id
        WHERE f.status=? {job_filter} AND d.finding_id IS NULL''', params).fetchall()
    documents = [(' '.join(sorted(set(names(title, raw).split()))), fid, title, raw)
                 for fid, title, raw in rows]
    if documents:
        conn.executemany('''INSERT OR IGNORE INTO repair_review_search_documents
            SELECT id,? FROM repair_findings WHERE id=? AND title IS ? AND details_json IS ?''',
            documents)
        conn.commit()


def matching_cases(conn, *, status, job_id, query):
    tokens = normalize(query).split()
    conn.create_function('review_search_names', 2, names, deterministic=True)
    # Combine names across an album before matching: album and track terms may
    # live on different findings. Filtering must retain its entire action plan.
    job_filter = 'AND f.job_id=?' if job_id else ''
    params = (status, job_id) if job_id else (status,)
    rows = conn.execute(f'''SELECT f.review_case_key, group_concat(COALESCE(d.words, review_search_names(f.title,f.details_json)), ' ')
        FROM repair_findings f LEFT JOIN repair_review_search_documents d ON d.finding_id=f.id
        WHERE f.status=? {job_filter} GROUP BY f.review_case_key''', params)
    return [key for key, text in rows
            if all(any(word.startswith(token) for word in text.split()) for token in tokens)]
