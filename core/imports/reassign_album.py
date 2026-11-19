"""Reassign a whole album to a different artist/release.

TheHomeGuy: "Is there any way to re assign an album to a different artist? I
have one that is on the wrong artist... i have had this happen when a featured
artist is taken as the album artist."

WHY THIS IS NOT A DATABASE UPDATE. SoulSync's library is a MIRROR of the media
server, which reads tags off the files. Repointing ``albums.artist_id`` alone
survives exactly until the next full refresh (``clear_server_data`` drops every
row for the server and repopulates), and Jellyfin shows the wrong artist the
whole time regardless. The tags are the source of truth.

WHY IT IS NOT A BESPOKE RE-TAG + MOVE EITHER. That would be a second
implementation of what the import pipeline already does, and the two would
drift. The re-identify TRACK feature (#889) already settled this question: it
stages a COPY of the library file back into auto-import with a single-use DB
hint, and lets the pipeline re-file it. Tags, folder layout and database rows
then all come from the code that handles a fresh download.

So an album reassign is the same trick, N times: one hint per track, all
pointing at the release the user picked.

THE PART THAT IS GENUINELY NEW is deciding WHICH local file becomes WHICH
track on the target release. A track re-identify has one file and one chosen
track; an album has to line up two tracklists that may not agree on length,
numbering or titles. That is what this module is for, and it is deliberately
pure so the mapping can be shown to the user for confirmation BEFORE anything
touches the disk — an album is many files, and a silent wrong guess is many
misfiled tracks rather than one.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("imports.reassign_album")


def normalize_title(text: Any) -> str:
    """Casefold, strip diacritics and punctuation, collapse whitespace.

    Deliberately does NOT strip edition qualifiers (Remastered / Live /
    Acoustic): two recordings of one song under different qualifiers are
    different tracks, and folding them together is how a live version ends up
    filed as the studio one.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Apostrophes are DELETED, not turned into a space: taggers and metadata
    # sources disagree constantly about them, and "Don't Stop" must normalise
    # to the same thing as "Dont Stop". Turning them into spaces splits one
    # word into two and drops the similarity below the floor.
    without_quotes = re.sub(r"['\u2019\u02bc`]", "", stripped)
    # `[\W_]` and NOT `[^a-z0-9]`: the ASCII-only version reduced any
    # non-Latin title to an EMPTY string, so two identical Japanese, Korean,
    # Cyrillic or Greek titles scored 0.0 similarity and could never be
    # matched by title at all. `\w` is Unicode-aware in Python 3, so scripts
    # are kept and only punctuation and underscores are dropped.
    cleaned = re.sub(r"[\W_]+", " ", without_quotes.casefold(), flags=re.UNICODE)
    return " ".join(cleaned.split())


def title_similarity(a: Any, b: Any) -> float:
    """0..1 token overlap (Jaccard) between two titles."""
    left = set(normalize_title(a).split())
    right = set(normalize_title(b).split())
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# Above this, two differently-numbered tracks are confidently the same song.
# Set high on purpose: a wrong pairing here misfiles a track under a name it
# does not have, and leaving it UNMAPPED (visible, excluded) is a better
# failure than guessing.
TITLE_MATCH_FLOOR = 0.8

# a guest credit is not part of the song's name: "My Heart Is Open (feat. Gwen
# Stefani)" and "My Heart Is Open" are one song. version words (live, remix)
# stay, they are different recordings.
_FEAT_GROUP = re.compile(
    r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]", re.IGNORECASE)
_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)
# a title that names no song: what a tagger writes when it knows nothing
_PLACEHOLDER = re.compile(
    r"^(?:(?:track|trakc|piste|titel|audio track|untitled|unknown|\d+)\s*)+$")
_PLACEHOLDER_WORDS = {"unknown", "untitled"}
# how far two lengths may drift and still be one song at one position: a
# remaster or another edition's cut, not another song
_DURATION_SLACK_MS = 10_000
_DURATION_SLACK_SHARE = 0.05


def song_key(text: Any) -> str:
    """the title without its guest credit, normalised"""
    raw = _FEAT_TAIL.sub("", _FEAT_GROUP.sub(" ", str(text or "")))
    return normalize_title(raw)


def is_placeholder_title(text: Any) -> bool:
    """an empty title or one that only says "track 2" / "unknown" names no
    song, so it can't speak against a position"""
    key = song_key(text)
    if not key:
        return True
    return bool(_PLACEHOLDER.match(key)) or bool(_PLACEHOLDER_WORDS & set(key.split()))


def titles_conflict(a: Any, b: Any) -> bool:
    """True when two titles clearly name different songs (wolf39us: "Lost
    Stars" at the slot of "Maps"). a placeholder, a guest credit, a version
    tail ("X - 2011 Remaster" against "X") or a cut-short provider title
    ("This Summer" against "This Summer's Gonna Hurt...") is not a conflict."""
    if is_placeholder_title(a) or is_placeholder_title(b):
        return False
    left, right = song_key(a), song_key(b)
    if left == right:
        return False
    if left.startswith(right) or right.startswith(left):
        return False
    return title_similarity(left, right) < 0.5


def durations_disagree(a_ms: Any, b_ms: Any) -> bool:
    """both lengths known and further apart than one song's editions drift"""
    try:
        first, second = int(a_ms or 0), int(b_ms or 0)
    except (TypeError, ValueError):
        return False
    if first <= 0 or second <= 0:
        return False
    slack = max(_DURATION_SLACK_MS, round(max(first, second) * _DURATION_SLACK_SHARE))
    return abs(first - second) > slack


def _song_similarity(a: Any, b: Any) -> float:
    return max(title_similarity(a, b), title_similarity(song_key(a), song_key(b)))


@dataclass
class TrackPairing:
    """One local file lined up against one track on the target release."""
    local_id: Any
    local_title: str
    local_track_number: Optional[int]
    local_path: str
    target_id: Optional[str] = None
    # the file staged, so a replace retires only that copy (review 5 W2-WD-3)
    local_file_id: Optional[int] = None
    target_title: str = ""
    target_track_number: Optional[int] = None
    target_disc_number: Optional[int] = None
    # 'track_number' | 'title' | None — shown in the UI so the user can see
    # WHY a pairing was proposed, not just that it was.
    matched_by: Optional[str] = None
    local_duration: Optional[int] = None

    @property
    def mapped(self) -> bool:
        return self.target_id is not None


@dataclass
class ReassignPlan:
    pairings: List[TrackPairing] = field(default_factory=list)

    @property
    def mapped(self) -> List[TrackPairing]:
        return [p for p in self.pairings if p.mapped]

    @property
    def unmapped(self) -> List[TrackPairing]:
        return [p for p in self.pairings if not p.mapped]


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _target_title(target: Dict[str, Any]) -> str:
    return str(target.get("name") or target.get("title") or "")


def _target_duration(target: Dict[str, Any]) -> Optional[int]:
    # milliseconds only: a source's "duration" may be seconds
    return _int_or_none(target.get("duration_ms"))


def map_album_tracks(
    local_tracks: Sequence[Dict[str, Any]],
    target_tracks: Sequence[Dict[str, Any]],
) -> List[TrackPairing]:
    """Line the album's local files up against the target release's tracklist.

    Three passes, most-trustworthy first:

    1. **Number and title together.** The number is unambiguous on both sides
       (a duplicated number, a multi-disc album flattened into one folder, is
       skipped), the two titles don't name different songs, the lengths agree,
       and no other track on the target carries the local file's exact title.
    2. **Title similarity**, for whatever pass 1 left over. Greedy on the best
       score above ``TITLE_MATCH_FLOOR``, the same number and then the
       closer length breaking ties, strictly one-to-one.
    3. **Number alone**, only for a file whose title names no song ("track
       02", "unknown"), so a mangled tag still lands on its slot.

    A title that clearly names a different song never maps by number
    (wolf39us: "1. Lost Stars" went to "1. Maps" while "15. Lost Stars" was on
    the same tracklist), and neither does a length that is another song's.

    Anything still unmatched comes back with ``target_id=None``. That is a
    RESULT, not a failure: the caller shows it and excludes it. An album is
    many files, so a silent wrong guess is many misfiled tracks, and "I could
    not place these three" is a far better answer than three wrong answers.
    """
    pairings: List[TrackPairing] = []
    for local in local_tracks or ():
        pairings.append(TrackPairing(
            local_id=local.get("id"),
            local_title=str(local.get("title") or ""),
            local_track_number=_int_or_none(local.get("track_number")),
            local_path=str(local.get("file_path") or ""),
            local_file_id=_int_or_none(local.get("file_id")),
            local_duration=_int_or_none(local.get("duration")),
        ))

    targets = list(target_tracks or ())
    claimed: set = set()

    by_number: Dict[int, List[int]] = {}
    for index, target in enumerate(targets):
        number = _int_or_none(target.get("track_number"))
        if number is not None:
            by_number.setdefault(number, []).append(index)
    local_number_counts: Dict[int, int] = {}
    for pairing in pairings:
        if pairing.local_track_number is not None:
            local_number_counts[pairing.local_track_number] = (
                local_number_counts.get(pairing.local_track_number, 0) + 1)

    def slot_of(pairing: TrackPairing) -> Optional[int]:
        number = pairing.local_track_number
        if number is None:
            return None
        candidates = by_number.get(number) or []
        if len(candidates) != 1 or local_number_counts.get(number, 0) != 1:
            # Ambiguous on one side or the other — leave it to the title pass
            # rather than pick one of two tracks that share a number.
            return None
        return None if candidates[0] in claimed else candidates[0]

    def fits_slot(pairing: TrackPairing, index: int) -> bool:
        target = targets[index]
        return (not titles_conflict(pairing.local_title, _target_title(target))
                and not durations_disagree(pairing.local_duration, _target_duration(target)))

    # Pass 1 — the number, when the title and length say the same.
    for pairing in pairings:
        if is_placeholder_title(pairing.local_title):
            continue
        index = slot_of(pairing)
        if index is None or not fits_slot(pairing, index):
            continue
        key = song_key(pairing.local_title)
        named_elsewhere = any(
            i != index and i not in claimed and song_key(_target_title(t)) == key
            for i, t in enumerate(targets))
        if named_elsewhere and song_key(_target_title(targets[index])) != key:
            continue
        _apply(pairing, targets[index], "track_number")
        if pairing.mapped:
            claimed.add(index)

    # Pass 2 — title similarity, best score first, one-to-one.
    scored = []
    for p_index, pairing in enumerate(pairings):
        if pairing.mapped:
            continue
        for t_index, target in enumerate(targets):
            if t_index in claimed:
                continue
            score = _song_similarity(pairing.local_title, _target_title(target))
            if score < TITLE_MATCH_FLOOR:
                continue
            same_number = (pairing.local_track_number is not None
                           and pairing.local_track_number == _int_or_none(target.get("track_number")))
            gap = None
            if pairing.local_duration and _target_duration(target):
                gap = abs(int(pairing.local_duration) - int(_target_duration(target)))
            scored.append((-score, not same_number, gap if gap is not None else 1 << 40,
                           p_index, t_index))
    scored.sort()
    for _score, _number, _gap, p_index, t_index in scored:
        pairing = pairings[p_index]
        if pairing.mapped or t_index in claimed:
            continue
        _apply(pairing, targets[t_index], "title")
        if pairing.mapped:
            claimed.add(t_index)

    # Pass 3 — the number alone, for a title that names no song.
    for pairing in pairings:
        if pairing.mapped or not is_placeholder_title(pairing.local_title):
            continue
        index = slot_of(pairing)
        if index is None or not fits_slot(pairing, index):
            continue
        _apply(pairing, targets[index], "track_number")
        if pairing.mapped:
            claimed.add(index)

    return pairings


def _apply(pairing: TrackPairing, target: Dict[str, Any], matched_by: str) -> None:
    pairing.target_id = str(target.get("id") or "") or None
    pairing.target_title = _target_title(target)
    pairing.target_track_number = _int_or_none(target.get("track_number"))
    pairing.target_disc_number = _int_or_none(target.get("disc_number")) or 1
    pairing.matched_by = matched_by if pairing.target_id else None
    if pairing.target_id is None:
        # A target row with no id cannot become a hint — the import needs it to
        # fetch the release. Treat it as unmatched rather than half-matched.
        pairing.target_title = ""
        pairing.matched_by = None


def build_reassign_plan(
    local_tracks: Sequence[Dict[str, Any]],
    target_tracks: Sequence[Dict[str, Any]],
) -> ReassignPlan:
    """The mapping, ready to show the user before anything is staged."""
    return ReassignPlan(pairings=map_album_tracks(local_tracks, target_tracks))


def hint_fields_for(
    pairing: TrackPairing,
    *,
    source: str,
    album_id: str,
    album_name: str,
    artist_id: Optional[str],
    artist_name: str,
    album_type: Optional[str] = None,
) -> Dict[str, Any]:
    """The hint payload for ONE paired file.

    Every track of the reassigned album carries the SAME release identity and
    its own track identity — which is exactly what makes the import pipeline
    file them together under the new artist.
    """
    return {
        "source": source,
        "track_id": pairing.target_id,
        "album_id": str(album_id),
        "artist_id": str(artist_id) if artist_id else None,
        "track_title": pairing.target_title or pairing.local_title,
        "album_name": album_name,
        "artist_name": artist_name,
        "album_type": album_type,
        "track_number": pairing.target_track_number or pairing.local_track_number,
        "disc_number": pairing.target_disc_number or 1,
        "isrc": None,
    }





def _remove_staged(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as cleanup_exc:
        logger.warning("Could not remove orphaned staged copy %s: %s", path, cleanup_exc)


def apply_album_reassign(
    plan: ReassignPlan,
    *,
    source: str,
    album_id: str,
    album_name: str,
    artist_id: Optional[str],
    artist_name: str,
    album_type: Optional[str],
    staging_dir: str,
    cursor: Any,
    replace: bool = True,
) -> Dict[str, Any]:
    """Stage every mapped file and write its hint. Returns a per-file summary.

    Ordering is deliberate (review 5 R5-D-3): copy to a hidden ``.part`` name
    first, then write and COMMIT the hint, then rename the copy into place.
    The copy never runs inside an open write transaction (a 12-track FLAC
    album held every other writer off for the whole copy), and auto-import
    never sees a staged file whose hint it can't read yet. A hint whose file
    could not be put in place is deleted again.

    If the hint cannot be written the staged copy is REMOVED again. Leaving it
    is not harmless: auto-import would pick it up as an ordinary file and match
    it however it likes, so the user ends up with the original AND a duplicate
    filed somewhere they did not ask for — N times over for an album.

    Partial success is a real outcome and is reported as one. An album is many
    files, and a single unreadable track must not abandon the other eleven —
    the caller shows what was staged and what was not.
    """
    from core.imports.rematch_apply import build_reidentify_hint, stage_file_for_reidentify
    from core.imports.rematch_hints import create_hint

    staged: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    conn = getattr(cursor, 'connection', None)

    def _commit():
        if conn is not None:
            conn.commit()

    def _rollback():
        if conn is not None:
            conn.rollback()

    for pairing in plan.mapped:
        try:
            staging = stage_file_for_reidentify(
                pairing.local_path, staging_dir, pairing.local_id, hidden=True)
        except FileNotFoundError:
            failed.append({'title': pairing.local_title,
                           'error': 'file is no longer on disk'})
            continue
        except Exception as exc:                        # noqa: BLE001 - one file must not sink the album
            failed.append({'title': pairing.local_title, 'error': str(exc)})
            continue

        try:
            hint = build_reidentify_hint(
                pairing.local_id,
                hint_fields_for(
                    pairing, source=source, album_id=album_id, album_name=album_name,
                    artist_id=artist_id, artist_name=artist_name, album_type=album_type,
                ),
                staging['staged_path'],
                staging.get('content_hash'),
                replace=replace,
                replace_file_id=pairing.local_file_id,
            )
            hint_id = create_hint(cursor, hint)
            _commit()
        except Exception as exc:                        # noqa: BLE001
            # Take the staged copy back out, or auto-import will treat it as a
            # new file and duplicate the track.
            try:
                _rollback()
            except Exception as rollback_exc:           # noqa: BLE001
                logger.debug("reassign hint rollback failed: %s", rollback_exc)
            _remove_staged(staging.get('temp_path') or staging['staged_path'])
            failed.append({'title': pairing.local_title, 'error': f'could not record hint: {exc}'})
            continue

        temp = staging.get('temp_path')
        if temp:
            try:
                os.replace(temp, staging['staged_path'])
            except OSError as exc:
                # a hint with no file never gets consumed: take it back out
                try:
                    cursor.execute("DELETE FROM rematch_hints WHERE id = ?", (hint_id,))
                    _commit()
                except Exception as hint_exc:           # noqa: BLE001
                    logger.warning("Could not remove hint %s for unstaged file: %s",
                                   hint_id, hint_exc)
                _remove_staged(temp)
                failed.append({'title': pairing.local_title, 'error': f'could not stage file: {exc}'})
                continue

        staged.append({'title': pairing.local_title,
                       'target_title': pairing.target_title,
                       'staged_path': staging['staged_path']})

    return {
        'staged': staged,
        'failed': failed,
        'skipped': [{'title': p.local_title, 'reason': 'no matching track on the target release'}
                    for p in plan.unmapped],
    }


__all__ = [
    "TITLE_MATCH_FLOOR",
    "durations_disagree",
    "is_placeholder_title",
    "song_key",
    "titles_conflict",
    "apply_album_reassign",
    "ReassignPlan",
    "TrackPairing",
    "build_reassign_plan",
    "hint_fields_for",
    "map_album_tracks",
    "normalize_title",
    "title_similarity",
]
