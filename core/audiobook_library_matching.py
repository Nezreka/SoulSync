"""Conservative catalogue matching with explicit evidence and durable review decisions."""
from __future__ import annotations

import re
import time
import unicodedata
from difflib import SequenceMatcher

from core.audiobook_library_metadata import ASIN_RE

MATCH_VERSION = 1


def normalize(value):
    text = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.findall(r'\w+', ''.join(c for c in text if not unicodedata.combining(c))))


def title_key(value):
    return normalize(re.sub(r'\b(?:unabridged|abridged|audiobook)\b', '', str(value or ''), flags=re.I))


def similarity(left, right):
    left, right = normalize(left), normalize(right)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def people_score(local, names):
    if not local or not names:
        return 0.0
    # Sorting handles "surname, first name" without discarding any name tokens.
    key = ' '.join(sorted(normalize(local).split()))
    return max(similarity(key, ' '.join(sorted(normalize(name).split()))) for name in names)


def compact(book):
    data = book.to_dict() if hasattr(book, 'to_dict') else dict(book)
    return {key: data.get(key) for key in ('asin', 'title', 'subtitle', 'author_names',
            'narrator_names', 'runtime_minutes', 'language', 'format_type', 'cover_url', 'series', 'source')}


def score_candidate(local, book):
    book = compact(book)
    title = similarity(title_key(local.get('title')), title_key(book.get('title')))
    author = people_score(local.get('author'), book.get('author_names') or [])
    narrator = people_score(local.get('narrator'), book.get('narrator_names') or [])
    local_time = float(local.get('runtime_minutes') or 0)
    book_time = float(book.get('runtime_minutes') or 0)
    delta = abs(local_time - book_time) / book_time if local_time and book_time else None
    duration = max(0, 1 - delta * 3) if delta is not None else 0
    reasons, conflicts = [], []
    for label, score in (('Title', title), ('Author', author), ('Narrator', narrator)):
        reasons.append(f'{label}: ' + ('strong agreement' if score >= .94 else 'partial agreement' if score >= .70 else 'missing or different'))
    reasons.append(f'Runtime: {abs(local_time-book_time):.0f} minutes difference' if delta is not None else 'Runtime: not available for comparison')
    if re.findall(r'\d+', title_key(local.get('title'))) != re.findall(r'\d+', title_key(book.get('title'))):
        conflicts.append('Different volume or edition numbers')
    if local.get('author') and book.get('author_names') and author < .75:
        conflicts.append('Different author')
    if local.get('narrator') and book.get('narrator_names') and narrator < .80:
        conflicts.append('Different narrator or recording')
    if delta is not None and delta > .10:
        conflicts.append('Runtime differs by more than 10%')
    language = {'eng':'english', 'en':'english', 'en us':'english', 'en gb':'english', 'de':'german', 'deu':'german', 'ger':'german', 'fr':'french', 'fra':'french', 'fre':'french', 'es':'spanish', 'spa':'spanish'}
    a, b = normalize(local.get('language')), normalize(book.get('language'))
    if a and b and language.get(a,a) != language.get(b,b):
        conflicts.append('Different language')
    a, b = normalize(local.get('format_type')), normalize(book.get('format_type'))
    if a and b and a != b:
        conflicts.append('Abridged and unabridged editions differ')
    if local.get('metadata_conflicts'):
        conflicts.extend(local['metadata_conflicts'])
    score = round((title*.45 + author*.25 + narrator*.20 + duration*.10)*100, 1)
    strong = (title >= .96 and author >= .94 and narrator >= .94 and delta is not None
              and delta <= .03 and not conflicts and local.get('title_source') == 'metadata')
    return {'book': book, 'score': score, 'evidence': reasons, 'conflicts': conflicts,
            'automatic_eligible': strong, 'match_version': MATCH_VERSION}


def candidates_for(row, client, query=None):
    local = row.get('metadata_json') or row
    query = (query or f"{local.get('title', row.get('title', ''))} {local.get('author', row.get('author', ''))}").strip()
    if not query:
        return []
    books = client.search(query, limit=20, strict=True)
    distinct = {}
    for book in books:
        data = compact(book)
        if data.get('source') not in (None, 'audible') or not ASIN_RE.fullmatch(str(data.get('asin') or '')):
            continue
        distinct[data['asin']] = score_candidate(local, data)
    return sorted(distinct.values(), key=lambda c: c['score'], reverse=True)[:8]


def match_one(db, row, client):
    candidates = candidates_for(row, client)
    best = candidates[0] if candidates else None
    margin = best['score'] - candidates[1]['score'] if len(candidates) > 1 else 100
    automatic = bool(best and best['automatic_eligible'] and best['score'] >= 96 and margin >= 8)
    status = 'automatic' if automatic else 'suggested' if candidates else 'unmatched'
    evidence = list(best['evidence']) if best else ['No catalogue candidates found; will retry later']
    if best and margin < 8:
        evidence.append('Several editions are similarly plausible; review required')
    applied = db.apply_library_match(row['asin'], signature=row.get('scan_signature',''),
        revision=row.get('match_revision',0), status=status,
        catalog_asin=best['book']['asin'] if automatic else '',
        score=best['score'] if best else 0, evidence=evidence, candidates=candidates,
        book=best['book'] if automatic else {})
    return status if applied else 'unchanged'


def match_library(db, client=None, limit=25, progress=None):
    from core.audiobook_client import get_audiobook_client
    client = client or get_audiobook_client()
    counts = {'matched':0, 'review':0, 'match_errors':0, 'match_checked':0, 'match_pending':0}
    now = time.time()
    due = [r for r in db.get_library() if r.get('match_status') in ('unmatched','suggested','error')
           and now - float(r.get('match_checked_at') or 0) >= (3600 if r.get('match_status') == 'error' else 7*86400)]
    due.sort(key=lambda r: float(r.get('match_checked_at') or 0))
    batch = due[:max(0, min(int(limit),100))]
    counts['match_pending'] = max(0,len(due)-len(batch))
    for row in batch:
        try:
            status = match_one(db,row,client)
            counts['matched'] += int(status == 'automatic')
            counts['review'] += int(status == 'suggested')
        except Exception as exc:
            counts['match_errors'] += 1
            db.apply_library_match(row['asin'], signature=row.get('scan_signature',''),
                revision=row.get('match_revision',0), status='error', evidence=[str(exc)])
        counts['match_checked'] += 1
        if progress:
            progress(counts, row['title'])
    return counts
