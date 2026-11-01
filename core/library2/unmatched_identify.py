"""What a file that imported without a match most likely is.

the review (core/library2/unmatched_review) shows what can be read off the
file's path. this listens to it: the file is fingerprinted with AcoustID, the
recording that comes back is looked up on the metadata source, and every
release it could be filed under is ranked by what backs it:

* the fingerprint and how sure AcoustID is of it
* the length: a release whose track is the file's length, give or take
* the folder: the album the rest of the file's folder is on
* the track number the file name gives

with no fingerprint (no AcoustID key, no chromaprint, a recording AcoustID
doesn't know) the same lookup runs on the name and the folder, and says so.
a recording the library already has is reported as a duplicate first: the
useful action there is removing the copy, not filing it twice.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

from .unmatched_review import unmatched_entry

logger = get_logger("library2.unmatched_identify")

# below this AcoustID's own score is noise
_MIN_FINGERPRINT = 0.5
# a candidate past this many seconds from the file is another version
_LENGTH_CLOSE_S = 3
_LENGTH_FAR_S = 10
_MAX_CANDIDATES = 6

# what the fingerprint said, kept with each result: a file AcoustID couldn't
# be asked about is asked again once it can be
RETRY_WHEN_AVAILABLE = ("unavailable", "no_backend")
RETRY_LATER = ("error",)

_BRACKETS = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")


def _norm(text: Any) -> str:
    from core.text.normalize import normalize_key
    return normalize_key(str(text or "")) or ""


def _title_key(text: Any) -> str:
    """the title without its "(feat. x)" or "[remastered]" """
    return _norm(_BRACKETS.sub("", str(text or ""))) or _norm(text)


def _similar(a: Any, b: Any) -> float:
    left, right = _title_key(a), _title_key(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _artist_similar(expected: Any, actual: Any) -> float:
    """a credit list ("Kanye West, Travis Scott") counts when any name fits"""
    if not expected or not actual:
        return 0.0
    names = [part for part in re.split(r",|&| feat\.? | x ", str(actual)) if part.strip()]
    return max([_similar(expected, actual)] + [_similar(expected, name) for name in names])


def _active_source() -> Optional[str]:
    from core.imports.rematch_search import available_sources
    sources = available_sources()
    active = [s for s in sources if s.get("active")] or sources
    return active[0]["source"] if active else None


def _default_lookup(path: str) -> Dict[str, Any]:
    from core.acoustid_client import AcoustIDClient
    return AcoustIDClient().lookup_with_status(path)


def _default_search(source: str, query: str, title: str, artist: str) -> List[Dict[str, Any]]:
    from core.imports.rematch_search import search_release_candidates
    return search_release_candidates(source, query, limit=10, title=title, artist=artist)


def _heard(lookup: Dict[str, Any]) -> List[Dict[str, Any]]:
    """the distinct recordings AcoustID is sure enough of, best first"""
    seen = set()
    out = []
    recordings = sorted(lookup.get("recordings") or [],
                        key=lambda r: float(r.get("score") or 0), reverse=True)
    for rec in recordings:
        score = float(rec.get("score") or 0)
        if score < _MIN_FINGERPRINT or not rec.get("title"):
            continue
        key = (_title_key(rec.get("title")), _norm(rec.get("artist")))
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": str(rec.get("title")), "artist": str(rec.get("artist") or ""),
                    "score": score, "duration": rec.get("duration")})
    return out[:2]


def _already_have(conn, title: str, artist: str, length_s: Optional[float]) -> Optional[Dict[str, Any]]:
    """the matched track this recording already is, if the library has it"""
    if not title or not artist:
        return None
    rows = conn.execute(
        """
        SELECT t.id AS track_id, t.title, t.duration, al.id AS album_id,
               al.title AS album_title, ar.name AS artist_name
          FROM lib2_artists ar
          JOIN lib2_albums al ON al.primary_artist_id = ar.id
          JOIN lib2_tracks t ON t.album_id = al.id
         WHERE LOWER(ar.name) = LOWER(?)
           AND EXISTS (SELECT 1 FROM lib2_track_files f
                        WHERE f.track_id = t.id
                          AND COALESCE(f.file_state, 'active') = 'active')
        """,
        (artist.strip(),),
    ).fetchall()
    want = _title_key(title)
    for row in rows:
        if _title_key(row["title"]) != want:
            continue
        duration = _seconds(row["duration"])
        # a live take or an edit has the title and not the length
        if length_s and duration and abs(duration - length_s) > _LENGTH_FAR_S:
            continue
        return {"track_id": row["track_id"], "title": row["title"], "album_id": row["album_id"],
                "album_title": row["album_title"], "artist_name": row["artist_name"]}
    return None


def _seconds(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number / 1000 if number > 10_000 else number


def _file_length(path: Optional[str]) -> Optional[float]:
    """the file's own length, for a row the catalogue holds none for"""
    if not path:
        return None
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        return _seconds(getattr(getattr(audio, "info", None), "length", None))
    except Exception:  # noqa: BLE001 - unreadable: the length just doesn't count
        return None


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _score(candidate: Dict[str, Any], *, heard: Optional[Dict[str, Any]],
           entry: Dict[str, Any], length_s: Optional[float]) -> Tuple[float, List[str]]:
    guess = entry["guess"]
    reasons: List[str] = []
    score = 0.0
    if heard:
        title_ok = _similar(candidate.get("track_title"), heard["title"]) >= 0.85
        artist_ok = _artist_similar(heard["artist"], candidate.get("artist_name")) >= 0.7
        if title_ok and artist_ok:
            score += 0.55 * heard["score"] + 0.05
            reasons.append(f"Fingerprint {round(heard['score'] * 100)}%")
        elif title_ok:
            score += 0.2 * heard["score"]
    else:
        if _similar(candidate.get("track_title"), guess["title"]) >= 0.85:
            score += 0.3
            reasons.append("Title from the file name")
        if guess.get("artist") and _artist_similar(guess["artist"], candidate.get("artist_name")) >= 0.7:
            score += 0.15
    if guess.get("album") and _title_key(candidate.get("album_name")) == _title_key(guess["album"]):
        score += 0.3
        reasons.append(f"Its folder is {guess['album']}")
    cand_s = _seconds(candidate.get("duration_ms"))
    if length_s and cand_s:
        gap = abs(cand_s - length_s)
        if gap <= _LENGTH_CLOSE_S:
            score += 0.12
            reasons.append("Same length")
        elif gap > _LENGTH_FAR_S:
            score -= 0.25
            reasons.append(f"Length differs ({_clock(length_s)} vs {_clock(cand_s)})")
    number = candidate.get("track_number")
    if guess.get("track_number") and number and int(number) == int(guess["track_number"]):
        score += 0.05
        reasons.append(f"Track {number}")
    return max(0.0, min(0.99, score)), reasons


def ensure_unmatched_match_schema(cursor) -> None:
    """the identifications, one per unmatched file, so a restart keeps them"""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lib2_unmatched_matches (
            file_id INTEGER PRIMARY KEY,
            file_key TEXT NOT NULL,
            status TEXT NOT NULL,
            fingerprint_status TEXT,
            best_confidence REAL,
            result_json TEXT NOT NULL,
            identified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")


def _stored_rows(conn, file_ids: List[int]) -> Dict[int, Any]:
    if not file_ids:
        return {}
    marks = ",".join("?" for _ in file_ids)
    try:
        rows = conn.execute(
            f"SELECT file_id, file_key, status, fingerprint_status, best_confidence, "
            f"result_json, identified_at FROM lib2_unmatched_matches WHERE file_id IN ({marks})",
            [int(i) for i in file_ids]).fetchall()
    except sqlite3.OperationalError:
        # a read never creates the table: nothing identified yet
        return {}
    return {int(r["file_id"]): r for r in rows}


def stored_results(conn, entries: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """the stored identification of each entry whose file hasn't changed since"""
    rows = _stored_rows(conn, [int(e["file_id"]) for e in entries])
    out = {}
    for entry in entries:
        row = rows.get(int(entry["file_id"]))
        if row is not None and row["file_key"] == entry["file_key"]:
            out[int(entry["file_id"])] = json.loads(row["result_json"])
    return out


def stale_entries(conn, entries: List[Dict[str, Any]], *, acoustid_ready: bool,
                  retry_after_s: float = 3600.0) -> List[Dict[str, Any]]:
    """the entries that need identifying: never done, changed since, or done
    without a fingerprint that can be had now"""
    rows = _stored_rows(conn, [int(e["file_id"]) for e in entries])
    out = []
    for entry in entries:
        row = rows.get(int(entry["file_id"]))
        if row is None or row["file_key"] != entry["file_key"]:
            out.append(entry)
        elif acoustid_ready and row["fingerprint_status"] in RETRY_WHEN_AVAILABLE:
            out.append(entry)
        elif row["fingerprint_status"] in RETRY_LATER and _age_s(row["identified_at"]) > retry_after_s:
            out.append(entry)
    return out


def _age_s(stamp: Any) -> float:
    from datetime import datetime, timezone
    try:
        then = datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


def _store(conn, entry: Dict[str, Any], result: Dict[str, Any], fingerprint_status: str) -> None:
    ensure_unmatched_match_schema(conn.cursor())
    best = result["candidates"][0]["confidence"] if result["candidates"] else None
    conn.execute(
        "INSERT OR REPLACE INTO lib2_unmatched_matches(file_id, file_key, status, "
        "fingerprint_status, best_confidence, result_json, identified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (int(entry["file_id"]), entry["file_key"], result["status"], fingerprint_status,
         best, json.dumps(result)))
    conn.commit()


def strong_matches_ready(conn, *, at: float = 0.85) -> int:
    """how many unmatched files have a strong match waiting to be accepted.
    one count for the library banner: no per-file work"""
    from .unmatched_review import UNKNOWN_ARTISTS_CTE
    query = UNKNOWN_ARTISTS_CTE + """
        SELECT COUNT(*)
          FROM unknown u
          CROSS JOIN lib2_albums al ON al.primary_artist_id = u.id
          JOIN lib2_tracks t ON t.album_id = al.id
          JOIN lib2_track_files f ON f.track_id = t.id
          JOIN lib2_unmatched_matches m ON m.file_id = f.id
         WHERE COALESCE(f.file_state, 'active') = 'active'
           AND m.file_key = f.path || '|' || COALESCE(f.size, '') || '|' || COALESCE(f.content_hash, '')
           AND m.status = 'matched' AND m.best_confidence >= ?
           {waiting}
    """
    waiting = ("AND NOT EXISTS (SELECT 1 FROM rematch_hints h WHERE h.status = 'pending' "
               "AND h.replace_track_id = t.id)")
    for clause in (waiting, ""):
        try:
            return int(conn.execute(query.format(waiting=clause), (at,)).fetchone()[0])
        except sqlite3.OperationalError:
            continue
    return 0


def identify_file(
    conn,
    file_id: int,
    *,
    lookup: Optional[Callable[[str], Dict[str, Any]]] = None,
    search: Optional[Callable[[str, str, str, str], List[Dict[str, Any]]]] = None,
    source: Optional[str] = None,
    resolve: Optional[Callable[[str], Optional[str]]] = None,
    refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """The ranked releases this unmatched file could be, or None when the file
    isn't one of the unmatched (matched since, or not a file at all). The
    answer is stored; a stored one for the unchanged file is returned as is
    unless ``refresh``."""
    entry = unmatched_entry(conn, int(file_id))
    if entry is None:
        return None
    if not refresh:
        stored = stored_results(conn, [entry]).get(int(entry["file_id"]))
        if stored is not None:
            return stored
    if resolve is None:
        from core.library2.paths import resolve_lib2_path
        resolve = resolve_lib2_path
    path = entry["file_path"] if os.path.isfile(entry["file_path"]) else resolve(entry["file_path"])
    result, fingerprint_status = _identify(conn, entry, path, lookup=lookup or _default_lookup,
                                           search=search or _default_search, source=source)
    # a missing file may come back; nothing about it is worth keeping
    if result["status"] != "missing":
        _store(conn, entry, result, fingerprint_status)
    return result


def _identify(conn, entry: Dict[str, Any], path: Optional[str], *,
              lookup, search, source: Optional[str]) -> Tuple[Dict[str, Any], str]:
    guess = entry["guess"]
    # an unmatched row usually has no length: the file has one
    length_s = _seconds(entry.get("duration")) or _file_length(path)
    result: Dict[str, Any] = {"status": "no_match", "method": "name", "heard": None,
                              "candidates": [], "already_have": None, "message": ""}
    if not path:
        result.update(status="missing", message="The file isn’t on disk any more.")
        return result, "missing"

    heard: List[Dict[str, Any]] = []
    fingerprint_note = ""
    try:
        outcome = lookup(path) or {}
    except Exception as exc:  # noqa: BLE001 - the name lookup still runs
        logger.warning("unmatched fingerprint failed for %s: %s", path, exc)
        outcome = {"status": "error", "error": str(exc)}
    status = outcome.get("status")
    if status == "ok":
        heard = _heard(outcome)
        if not heard:
            fingerprint_note = "AcoustID wasn’t sure what this recording is."
    elif status == "no_match":
        fingerprint_note = "AcoustID doesn’t know this recording."
    elif status in ("unavailable", "no_backend"):
        fingerprint_note = "Fingerprinting is off (no AcoustID key or chromaprint)."
    elif status in ("unsupported", "fingerprint_error"):
        fingerprint_note = "This file couldn’t be fingerprinted."
    else:
        fingerprint_note = "AcoustID couldn’t be reached."

    if heard:
        result["method"] = "fingerprint"
        result["heard"] = {k: heard[0][k] for k in ("title", "artist", "score")}
        if not length_s:
            length_s = _seconds(heard[0].get("duration"))
        result["already_have"] = _already_have(conn, heard[0]["title"], heard[0]["artist"], length_s)
    elif entry.get("duplicate_of"):
        dup = entry["duplicate_of"]
        result["already_have"] = {k: dup[k] for k in
                                  ("track_id", "title", "album_id", "album_title", "artist_name")}

    try:
        src = source or _active_source()
    except Exception:  # noqa: BLE001
        src = None
    candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if src:
        searches: List[Tuple[Optional[Dict[str, Any]], str, str, str]] = []
        for rec in heard:
            searches.append((rec, f"{rec['title']} {rec['artist']}".strip(), rec["title"], rec["artist"]))
        if not heard and guess.get("title"):
            searches.append((None, guess["query"], guess["title"] if guess.get("artist") else "",
                             guess.get("artist") or ""))
        for rec, query, title, artist in searches:
            try:
                rows = search(src, query, title, artist) or []
            except Exception as exc:  # noqa: BLE001 - one failed search leaves the others
                logger.debug("unmatched search %r failed: %s", query, exc)
                rows = []
            for row in rows:
                confidence, reasons = _score(row, heard=rec, entry=entry, length_s=length_s)
                ident = (str(row.get("source")), str(row.get("track_id")))
                if ident in candidates and candidates[ident]["confidence"] >= confidence:
                    continue
                candidates[ident] = {
                    "source": row.get("source") or src,
                    "track_id": str(row.get("track_id")),
                    "track_title": row.get("track_title") or "",
                    "artist_name": row.get("artist_name") or "",
                    "album_name": row.get("album_name") or "",
                    "album_type": row.get("album_type") or "",
                    "year": row.get("year"),
                    "total_tracks": row.get("total_tracks"),
                    "image_url": row.get("image_url") or "",
                    "isrc": row.get("isrc"),
                    "confidence": round(confidence, 3),
                    "reasons": reasons,
                }
    ranked = sorted(candidates.values(), key=lambda c: c["confidence"], reverse=True)
    # a search lists strangers too ("Sugar On My Tongue" for "Tongue"): only
    # what something backs is offered
    ranked = [c for c in ranked if c["confidence"] >= 0.3]
    result["candidates"] = ranked[:_MAX_CANDIDATES]

    if result["already_have"]:
        result["status"] = "duplicate"
    elif result["candidates"]:
        result["status"] = "matched"
    else:
        result["status"] = "no_match"
        if not src:
            result["message"] = "No metadata source is set up to look it up on."
        elif heard:
            h = heard[0]
            result["message"] = (f"It sounds like “{h['title']}” by {h['artist']}, "
                                 "but no release of it was found. Search to pick one.")
        else:
            result["message"] = (f"{fingerprint_note} Its name alone wasn’t enough. "
                                 "Play it, then search.").strip()
    if heard or result["status"] != "no_match":
        result["message"] = result["message"] or fingerprint_note
    return result, str(status or "error")
